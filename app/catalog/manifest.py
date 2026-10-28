"""Launch-manifest validation and loading (catalog content plan §2-3).

The manifest is the source of truth for products. `validate()` collects every problem
before anything touches the database; `load()` upserts by slug/SKU and is idempotent.
Products missing from the manifest are archived, never deleted, so existing carts and
orders keep their references.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.catalog.models import (
    Category,
    Collection,
    OptionAxis,
    OptionValue,
    Product,
    ProductImage,
    ProductStatus,
    ProductType,
    SaleUnit,
    Variant,
    combination_key,
)
from app.catalog.naming import COLOR_NAMES
from app.extensions import db

SCHEMA_VERSION = 1
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMAGE_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png"}
MAX_CATEGORY_DEPTH = 2  # D-003

PRODUCT_KEYS = {
    "slug", "name", "type", "status", "featured", "published", "legacy_wp_id",
    "summary", "description", "categories", "collections", "images", "options", "variants",
    "colors", "autoname", "sale_unit", "piece_length_yards", "piece_length_max_yards",
    "width_inches",
}  # fmt: skip
VARIANT_KEYS = {
    "sku", "options", "price_cents", "compare_at_cents", "stock", "track_inventory",
    "weight_grams", "image", "active",
}  # fmt: skip


class ManifestError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"{len(errors)} manifest error(s)")
        self.errors = errors


@dataclass
class LoadReport:
    categories: int = 0
    collections: int = 0
    products: int = 0
    variants: int = 0
    images: int = 0
    archived: list[str] = field(default_factory=list)


def read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ManifestError(["manifest must be a mapping"])
    return data


def validate(data: dict[str, Any], assets_dir: Path) -> list[str]:
    errors: list[str] = []
    err = errors.append

    if data.get("schema_version") != SCHEMA_VERSION:
        err(f"schema_version must be {SCHEMA_VERSION}")

    # Categories (max two levels).
    category_slugs: set[Any] = set()

    def walk(nodes: Any, depth: int, where: str) -> None:
        if not isinstance(nodes, list):
            err(f"{where}: must be a list")
            return
        for i, node in enumerate(nodes):
            loc = f"{where}[{i}]"
            if not isinstance(node, dict):
                err(f"{loc}: must be a mapping")
                continue
            slug = node.get("slug")
            _check_slug(slug, loc, err)
            if slug in category_slugs:
                err(f"{loc}: duplicate category slug {slug!r}")
            category_slugs.add(slug)
            if not _nonempty(node.get("name")):
                err(f"{loc}: name is required")
            _check_asset(node.get("image"), f"{loc}.image", assets_dir, err, required=False)
            children = node.get("children", [])
            if children and depth >= MAX_CATEGORY_DEPTH:
                err(f"{loc}: categories may only be {MAX_CATEGORY_DEPTH} levels deep (D-003)")
            elif children:
                walk(children, depth + 1, f"{loc}.children")

    walk(data.get("categories", []), 1, "categories")

    collection_slugs: set[Any] = set()
    for i, col in enumerate(data.get("collections", []) or []):
        loc = f"collections[{i}]"
        if not isinstance(col, dict):
            err(f"{loc}: must be a mapping")
            continue
        _check_slug(col.get("slug"), loc, err)
        if col.get("slug") in collection_slugs:
            err(f"{loc}: duplicate collection slug {col.get('slug')!r}")
        collection_slugs.add(col.get("slug"))
        if not _nonempty(col.get("name")):
            err(f"{loc}: name is required")
        _check_asset(col.get("image"), f"{loc}.image", assets_dir, err, required=False)

    # Products.
    product_slugs: set[Any] = set()
    skus: set[Any] = set()
    products = data.get("products")
    if not isinstance(products, list) or not products:
        err("products: at least one product is required")
        products = []

    for i, p in enumerate(products):
        loc = f"products[{i}]"
        if not isinstance(p, dict):
            err(f"{loc}: must be a mapping")
            continue
        loc = f"products[{i}] ({p.get('slug', '?')})"
        for key in set(p) - PRODUCT_KEYS:
            err(f"{loc}: unknown field {key!r}")
        _check_slug(p.get("slug"), loc, err)
        if p.get("slug") in product_slugs:
            err(f"{loc}: duplicate product slug")
        product_slugs.add(p.get("slug"))
        if not _nonempty(p.get("name")):
            err(f"{loc}: name is required")
        _check_enum(p.get("type"), ProductType, f"{loc}.type", err)
        _check_enum(p.get("status", "draft"), ProductStatus, f"{loc}.status", err)
        _check_enum(p.get("sale_unit", "fixed_bundle"), SaleUnit, f"{loc}.sale_unit", err)
        length = p.get("piece_length_yards")
        if length is not None and (
            isinstance(length, bool)
            or not isinstance(length, int | float)
            or not 0 < length <= 1000
            or round(length, 2) != length
        ):
            err(f"{loc}.piece_length_yards: a positive number of yards, at most 2 decimals")
        longest = p.get("piece_length_max_yards")
        if longest is not None and (
            isinstance(longest, bool)
            or not isinstance(longest, int | float)
            or not isinstance(length, int | float)
            or not length < longest <= 1000
            or round(longest, 2) != longest
        ):
            err(
                f"{loc}.piece_length_max_yards: needs piece_length_yards and must be longer than it"
            )
        width = p.get("width_inches")
        if width is not None and (
            isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 200
        ):
            err(f"{loc}.width_inches: a whole number of inches (1-200)")
        colors = p.get("colors") or []
        if not isinstance(colors, list) or any(c not in COLOR_NAMES for c in colors):
            err(f"{loc}.colors: must be a list of {sorted(COLOR_NAMES)}")
        if len(p.get("summary") or "") > 300:
            err(f"{loc}.summary: 300 characters maximum")
        if p.get("published") is not None and not isinstance(p.get("published"), date):
            err(f"{loc}.published: must be a date (YYYY-MM-DD)")

        cats = p.get("categories") or []
        if not cats:
            err(f"{loc}: at least one category is required")
        for c in cats:
            if c not in category_slugs:
                err(f"{loc}: unknown category {c!r}")
        for c in p.get("collections") or []:
            if c not in collection_slugs:
                err(f"{loc}: unknown collection {c!r}")

        image_files: set[Any] = set()
        images = p.get("images") or []
        if p.get("status") == "active" and not images:
            err(f"{loc}: active products need at least one image")
        for j, img in enumerate(images):
            iloc = f"{loc}.images[{j}]"
            if not isinstance(img, dict):
                err(f"{iloc}: must be a mapping")
                continue
            _check_asset(img.get("file"), f"{iloc}.file", assets_dir, err, required=True)
            if not _nonempty(img.get("alt")):
                err(f"{iloc}: alt text is required")
            image_files.add(img.get("file"))

        axes: dict[Any, set[str]] = {}
        for j, axis in enumerate(p.get("options") or []):
            aloc = f"{loc}.options[{j}]"
            name = axis.get("name") if isinstance(axis, dict) else None
            values = axis.get("values") if isinstance(axis, dict) else None
            if not _nonempty(name):
                err(f"{aloc}: name is required")
                continue
            if name in axes:
                err(f"{aloc}: duplicate option {name!r}")
            if not isinstance(values, list) or not values or not all(map(_nonempty, values)):
                err(f"{aloc}: values must be a non-empty list of labels")
                values = []
            if len({str(v).casefold() for v in values}) != len(values):
                err(f"{aloc}: duplicate values")
            axes[name] = {str(v) for v in values}

        variants = p.get("variants") or []
        if not variants:
            err(f"{loc}: at least one variant is required")
        if not axes and len(variants) > 1:
            err(f"{loc}: products without options must have exactly one variant")
        combos: set[str] = set()
        for j, v in enumerate(variants):
            vloc = f"{loc}.variants[{j}]"
            if not isinstance(v, dict):
                err(f"{vloc}: must be a mapping")
                continue
            for key in set(v) - VARIANT_KEYS:
                err(f"{vloc}: unknown field {key!r}")
            sku = v.get("sku")
            if not isinstance(sku, str) or not SKU_RE.match(sku):
                err(f"{vloc}: sku must be 1-64 letters, digits, '.', '_' or '-'")
            elif sku in skus:
                err(f"{vloc}: duplicate sku {sku!r}")
            skus.add(sku)
            _check_cents(v.get("price_cents"), f"{vloc}.price_cents", err, required=True)
            compare = v.get("compare_at_cents")
            if compare is not None:
                _check_cents(compare, f"{vloc}.compare_at_cents", err, required=True)
                price = v.get("price_cents")
                if isinstance(compare, int) and isinstance(price, int) and compare <= price:
                    err(f"{vloc}: compare_at_cents must be above price_cents")
            stock = v.get("stock", 0)
            if not isinstance(stock, int) or isinstance(stock, bool) or stock < 0:
                err(f"{vloc}.stock: must be a whole number >= 0")
            weight = v.get("weight_grams")
            if weight is not None and (not isinstance(weight, int) or weight < 0):
                err(f"{vloc}.weight_grams: must be a whole number >= 0")
            if v.get("image") is not None and v.get("image") not in image_files:
                err(f"{vloc}.image: must be one of this product's images")

            chosen = v.get("options") or {}
            if not isinstance(chosen, dict):
                err(f"{vloc}.options: must be a mapping")
                continue
            if set(chosen) != set(axes):
                err(f"{vloc}: must choose exactly one value for each option {sorted(axes)}")
                continue
            for axis_name, value in chosen.items():
                if str(value) not in axes[axis_name]:
                    err(f"{vloc}: {value!r} is not a value of {axis_name!r}")
            key = combination_key({k: str(val) for k, val in chosen.items()})
            if key in combos:
                err(f"{vloc}: duplicate option combination {chosen}")
            combos.add(key)

    return errors


def load(data: dict[str, Any], assets_dir: Path, media_root: Path) -> LoadReport:
    """Validate, copy media and upsert everything in one transaction."""
    errors = validate(data, assets_dir)
    if errors:
        raise ManifestError(errors)

    report = LoadReport()
    try:
        cats = _load_categories(data.get("categories", []), assets_dir, media_root, report)
        cols = _load_collections(data.get("collections", []) or [], assets_dir, media_root, report)
        seen: set[str] = set()
        for position, p in enumerate(data["products"]):
            _load_product(p, position, cats, cols, assets_dir, media_root, report)
            seen.add(p["slug"])
        for product in db.session.scalars(db.select(Product).where(Product.slug.not_in(seen))):
            if product.status != ProductStatus.ARCHIVED:
                product.status = ProductStatus.ARCHIVED
                report.archived.append(product.slug)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return report


def _load_categories(
    nodes: list[dict[str, Any]], assets: Path, media: Path, report: LoadReport
) -> dict[str, Category]:
    by_slug = {c.slug: c for c in db.session.scalars(db.select(Category))}

    def walk(items: list[dict[str, Any]], parent: Category | None) -> None:
        for position, node in enumerate(items):
            cat = by_slug.get(node["slug"]) or Category(slug=node["slug"])
            cat.name = node["name"].strip()
            cat.description = (node.get("description") or "").strip()
            cat.image = _copy_asset(node.get("image"), assets, media)
            cat.position = position
            cat.parent = parent
            db.session.add(cat)
            by_slug[cat.slug] = cat
            report.categories += 1
            walk(node.get("children") or [], cat)

    walk(nodes, None)
    db.session.flush()
    return by_slug


def _load_collections(
    items: list[dict[str, Any]], assets: Path, media: Path, report: LoadReport
) -> dict[str, Collection]:
    by_slug = {c.slug: c for c in db.session.scalars(db.select(Collection))}
    for position, item in enumerate(items):
        col = by_slug.get(item["slug"]) or Collection(slug=item["slug"])
        col.name = item["name"].strip()
        col.description = (item.get("description") or "").strip()
        col.image = _copy_asset(item.get("image"), assets, media)
        col.position = position
        col.starts_at = _as_datetime(item.get("starts"))
        col.ends_at = _as_datetime(item.get("ends"))
        db.session.add(col)
        by_slug[col.slug] = col
        report.collections += 1
    db.session.flush()
    return by_slug


def _load_product(
    p: dict[str, Any],
    position: int,
    cats: dict[str, Category],
    cols: dict[str, Collection],
    assets: Path,
    media: Path,
    report: LoadReport,
) -> None:
    product = db.session.scalar(db.select(Product).where(Product.slug == p["slug"]))
    if product is None:
        product = Product(slug=p["slug"])
        db.session.add(product)
    product.name = p["name"].strip()
    product.product_type = ProductType(p["type"])
    product.status = ProductStatus(p.get("status", "draft"))
    product.featured = bool(p.get("featured", False))
    product.sale_unit = SaleUnit(p.get("sale_unit", "fixed_bundle")).value
    length = p.get("piece_length_yards")
    product.piece_length_yards = None if length is None else Decimal(str(length))
    longest = p.get("piece_length_max_yards")
    product.piece_length_max_yards = None if longest is None else Decimal(str(longest))
    product.width_inches = p.get("width_inches")
    product.colors = ",".join(p.get("colors") or [])
    product.summary = (p.get("summary") or "").strip()
    product.description = (p.get("description") or "").strip()
    product.legacy_wp_id = p.get("legacy_wp_id")
    product.published_at = _as_datetime(p.get("published")) or product.published_at
    if product.status == ProductStatus.ACTIVE and product.published_at is None:
        product.published_at = datetime.now(UTC)
    product.categories = [cats[s] for s in p["categories"]]
    product.collections = [cols[s] for s in p.get("collections") or []]

    # Images: keep rows whose path is still listed, add new ones, drop the rest.
    existing = {img.path: img for img in product.images}
    images: list[ProductImage] = []
    for i, entry in enumerate(p.get("images") or []):
        rel = _copy_asset(entry["file"], assets, media)
        img = existing.pop(rel, None) or ProductImage(path=rel)
        img.alt = entry["alt"].strip()
        img.position = i
        images.append(img)
    product.images = images
    report.images += len(images)

    # Option axes and values (upsert by name/label).
    axes_by_name = {a.name: a for a in product.option_axes}
    axes: list[OptionAxis] = []
    for i, axis_data in enumerate(p.get("options") or []):
        axis = axes_by_name.get(axis_data["name"]) or OptionAxis(name=axis_data["name"])
        axis.position = i
        values_by_label = {v.label: v for v in axis.values}
        axis.values = [
            _with_position(values_by_label.get(str(label)) or OptionValue(label=str(label)), j)
            for j, label in enumerate(axis_data["values"])
        ]
        axes.append(axis)
    product.option_axes = axes
    db.session.flush()

    with db.session.no_autoflush:
        _load_variants(product, p, axes, images)
    report.products += 1
    report.variants += len(p["variants"])
    db.session.flush()


def _load_variants(
    product: Product, p: dict[str, Any], axes: list[OptionAxis], images: list[ProductImage]
) -> None:
    value_lookup = {(a.name, v.label): v for a in axes for v in a.values}
    image_lookup = {img.path: img for img in images}
    variants_by_sku = {v.sku: v for v in db.session.scalars(db.select(Variant))}
    keep: list[Variant] = []
    for i, vd in enumerate(p["variants"]):
        variant = variants_by_sku.get(vd["sku"]) or Variant(sku=vd["sku"])
        chosen = {k: str(val) for k, val in (vd.get("options") or {}).items()}
        variant.product = product
        variant.combination_key = combination_key(chosen)
        variant.option_values = [value_lookup[(k, val)] for k, val in chosen.items()]
        variant.price_cents = vd["price_cents"]
        variant.compare_at_cents = vd.get("compare_at_cents")
        variant.stock_quantity = vd.get("stock", 0)
        variant.track_inventory = bool(vd.get("track_inventory", True))
        variant.weight_grams = vd.get("weight_grams")
        variant.active = bool(vd.get("active", True))
        variant.position = i
        image_path = _media_rel(vd["image"]) if vd.get("image") else None
        variant.image = image_lookup.get(image_path) if image_path else None
        keep.append(variant)
    # Variants no longer listed are deactivated (carts may still reference them).
    for old in product.variants:
        if old not in keep:
            old.active = False
            old.combination_key = f"retired:{old.sku}"
            keep.append(old)
    product.variants = keep


def _with_position(value: OptionValue, position: int) -> OptionValue:
    value.position = position
    return value


def _media_rel(asset: str) -> str:
    return str(PurePosixPath(asset))


def _copy_asset(asset: str | None, assets: Path, media: Path) -> str | None:
    if not asset:
        return None
    rel = _media_rel(asset)
    src = assets / rel
    dest = media / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dest)
    return rel


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise ValueError(f"not a date: {value!r}")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str | int) and str(value).strip() != ""


def _check_slug(slug: Any, loc: str, err: Any) -> None:
    if not isinstance(slug, str) or not SLUG_RE.match(slug) or len(slug) > 120:
        err(f"{loc}: slug must be lowercase letters, digits and single hyphens")


def _check_enum(value: Any, enum_cls: Any, loc: str, err: Any) -> None:
    allowed = [m.value for m in enum_cls]
    if value not in allowed:
        err(f"{loc}: must be one of {allowed}")


def _check_cents(value: Any, loc: str, err: Any, required: bool) -> None:
    if value is None and not required:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        err(f"{loc}: must be a whole number of cents >= 0 (e.g. 899 for $8.99)")


def _check_asset(asset: Any, loc: str, assets_dir: Path, err: Any, required: bool) -> None:
    if asset is None:
        if required:
            err(f"{loc}: file is required")
        return
    if not isinstance(asset, str):
        err(f"{loc}: must be a path")
        return
    rel = PurePosixPath(asset)
    if rel.is_absolute() or ".." in rel.parts or "\\" in asset or ":" in asset:
        err(f"{loc}: unsafe path {asset!r}")
        return
    if rel.suffix.lower() not in IMAGE_SUFFIXES:
        err(f"{loc}: images must be {sorted(IMAGE_SUFFIXES)}")
        return
    if not (assets_dir / rel).is_file():
        err(f"{loc}: file not found: {asset}")

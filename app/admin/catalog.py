"""Validated catalog mutations used by the private administration UI."""

from __future__ import annotations

import re
import secrets
import warnings
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app import cache
from app.admin import service
from app.admin.models import AdminUser
from app.catalog.models import Category, Product, ProductImage, ProductStatus, ProductType, Variant
from app.extensions import db

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_PIXELS = 36_000_000
MAX_DIMENSION = 6_000


class CatalogError(ValueError):
    pass


def update_product(admin: AdminUser, product: Product, form: dict[str, str]) -> None:
    name = form.get("name", "").strip()
    summary = form.get("summary", "").strip()
    description = form.get("description", "").strip()
    if not 1 <= len(name) <= 200:
        raise CatalogError("Product name must be between 1 and 200 characters.")
    if len(summary) > 300:
        raise CatalogError("Summary must be 300 characters or fewer.")
    try:
        status = ProductStatus(form.get("status", ""))
        product_type = ProductType(form.get("product_type", ""))
    except ValueError:
        raise CatalogError("Choose a valid product type and status.") from None
    before = {
        "name": product.name,
        "status": product.status.value,
        "featured": product.featured,
    }
    product.name = name
    product.summary = summary
    product.description = description[:20_000]
    product.status = status
    product.product_type = product_type
    product.featured = form.get("featured") == "1"
    category_ids = _ids(form.get("category_ids", ""))
    categories = db.session.scalars(select(Category).where(Category.id.in_(category_ids))).all()
    if not categories:
        raise CatalogError("Choose at least one category.")
    product.categories = list(categories)
    service.audit(
        admin,
        "catalog.product.updated",
        "product",
        str(product.id),
        {
            "before": before,
            "after": {"name": name, "status": status.value, "featured": product.featured},
        },
    )
    db.session.commit()
    cache.bump_catalog_version()


def update_variant(admin: AdminUser, variant: Variant, form: dict[str, str]) -> None:
    sku = form.get("sku", "").strip()
    if not 1 <= len(sku) <= 64:
        raise CatalogError("SKU must be between 1 and 64 characters.")
    duplicate = db.session.scalar(
        select(Variant.id).where(Variant.sku == sku, Variant.id != variant.id)
    )
    if duplicate is not None:
        raise CatalogError("That SKU is already in use.")
    price = _money(form.get("price", ""), "Price")
    compare = _money(form.get("compare_at", ""), "Compare-at price", blank=True)
    stock = _integer(form.get("stock", ""), "Stock", minimum=0, maximum=1_000_000)
    weight = _integer(
        form.get("weight_grams", ""), "Weight", minimum=1, maximum=1_000_000, blank=True
    )
    assert price is not None and stock is not None  # required fields, narrowed for type checking
    if compare is not None and compare <= price:
        raise CatalogError("Compare-at price must be higher than the selling price.")
    before = {
        "sku": variant.sku,
        "price_cents": variant.price_cents,
        "stock": variant.stock_quantity,
    }
    variant.sku = sku
    variant.price_cents = price
    variant.compare_at_cents = compare
    variant.stock_quantity = stock or 0
    variant.weight_grams = weight
    variant.track_inventory = form.get("track_inventory") == "1"
    variant.active = form.get("active") == "1"
    service.audit(
        admin,
        "catalog.variant.updated",
        "variant",
        str(variant.id),
        {"before": before, "after": {"sku": sku, "price_cents": price, "stock": stock}},
    )
    db.session.commit()
    cache.bump_catalog_version()


def update_category(admin: AdminUser, category: Category, form: dict[str, str]) -> None:
    name = form.get("name", "").strip()
    if not 1 <= len(name) <= 120:
        raise CatalogError("Category name must be between 1 and 120 characters.")
    position = _integer(form.get("position", ""), "Position", minimum=0, maximum=10_000)
    before = {"name": category.name, "position": category.position}
    category.name = name
    category.description = form.get("description", "").strip()[:5000]
    category.position = position or 0
    service.audit(
        admin,
        "catalog.category.updated",
        "category",
        str(category.id),
        {"before": before, "after": {"name": name, "position": position}},
    )
    db.session.commit()
    cache.bump_catalog_version()


def upload_image(
    admin: AdminUser, product: Product, upload: FileStorage | None, alt: str
) -> ProductImage:
    alt = alt.strip()
    if not 3 <= len(alt) <= 255:
        raise CatalogError("Alternative text must be between 3 and 255 characters.")
    if upload is None or not upload.filename:
        raise CatalogError("Choose an image to upload.")
    try:
        upload.stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload.stream)
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise CatalogError("Upload a JPEG, PNG, or WebP image.")
            if (
                image.width > MAX_DIMENSION
                or image.height > MAX_DIMENSION
                or image.width * image.height > MAX_PIXELS
            ):
                raise CatalogError("Image dimensions are too large.")
            image.load()
    except CatalogError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
    ):
        raise CatalogError("The uploaded file is not a safe, readable image.") from None

    processed = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    processed.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    relative = Path("products") / product.slug / "uploads" / f"{secrets.token_hex(12)}.webp"
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    destination = (root / relative).resolve()
    if root not in destination.parents:
        raise CatalogError("Unsafe image destination.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    processed.save(destination, "WEBP", quality=88, method=6)
    record = ProductImage(
        product=product, path=relative.as_posix(), alt=alt, position=len(product.images)
    )
    db.session.add(record)
    service.audit(
        admin, "catalog.image.uploaded", "product", str(product.id), {"path": record.path}
    )
    try:
        db.session.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    cache.bump_catalog_version()
    return record


def delete_image(admin: AdminUser, image: ProductImage) -> None:
    if db.session.scalar(select(Variant.id).where(Variant.image_id == image.id)) is not None:
        raise CatalogError("This image is assigned to a variant and cannot be removed.")
    path = image.path
    product_id = image.product_id
    db.session.delete(image)
    service.audit(admin, "catalog.image.deleted", "product", str(product_id), {"path": path})
    db.session.commit()
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    target = (root / path).resolve()
    if root in target.parents:
        target.unlink(missing_ok=True)
    cache.bump_catalog_version()


def _ids(raw: str) -> list[int]:
    try:
        return sorted({int(value) for value in raw.split(",") if value})
    except ValueError:
        raise CatalogError("Invalid category selection.") from None


def _integer(
    raw: str, label: str, *, minimum: int, maximum: int, blank: bool = False
) -> int | None:
    if blank and not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        raise CatalogError(f"{label} must be a whole number.") from None
    if not minimum <= value <= maximum:
        raise CatalogError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _money(raw: str, label: str, *, blank: bool = False) -> int | None:
    if blank and not raw.strip():
        return None
    try:
        text = raw.strip().replace("$", "").replace(",", "")
        whole, dot, fraction = text.partition(".")
        if not whole.isdigit() or (dot and (not fraction.isdigit() or len(fraction) > 2)):
            raise ValueError
        cents = int(whole) * 100 + int((fraction + "00")[:2])
    except ValueError:
        raise CatalogError(f"{label} must be a valid dollar amount.") from None
    if not 0 <= cents <= 100_000_000:
        raise CatalogError(f"{label} is outside the allowed range.")
    return cents

"""Validated catalog mutations used by the private administration UI."""

from __future__ import annotations

import re

from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app import cache
from app.admin import service
from app.admin.models import AdminUser
from app.catalog import images as image_service
from app.catalog import units
from app.catalog.models import (
    Category,
    Product,
    ProductImage,
    ProductStatus,
    ProductType,
    SaleUnit,
    Variant,
)
from app.extensions import db

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
        sale_unit = SaleUnit(form.get("sale_unit", product.sale_unit))
    except ValueError:
        raise CatalogError("Choose a valid product type and status.") from None
    try:
        piece_length = units.parse_piece_length(form.get("piece_length_yards"))
        width = units.parse_width(form.get("width_inches"))
    except ValueError as exc:
        raise CatalogError(str(exc)) from None
    before = {
        "name": product.name,
        "status": product.status.value,
        "featured": product.featured,
        "sale_unit": product.sale_unit,
        "piece_length_yards": str(product.piece_length_yards or ""),
        "width_inches": product.width_inches,
    }
    product.name = name
    product.summary = summary
    product.description = description[:20_000]
    product.status = status
    product.product_type = product_type
    product.featured = form.get("featured") == "1"
    product.sale_unit = sale_unit.value
    product.piece_length_yards = piece_length
    product.width_inches = width
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
            "after": {
                "name": name,
                "status": status.value,
                "featured": product.featured,
                "sale_unit": product.sale_unit,
                "piece_length_yards": str(piece_length or ""),
                "width_inches": width,
            },
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
    try:
        record, destination = image_service.prepare_upload(product, upload, alt)
    except image_service.ImageError as exc:
        raise CatalogError(str(exc)) from None
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
    try:
        image_service.ensure_deletable(image)
    except image_service.ImageError as exc:
        raise CatalogError(str(exc)) from None
    path = image.path
    product_id = image.product_id
    db.session.delete(image)
    service.audit(admin, "catalog.image.deleted", "product", str(product_id), {"path": path})
    db.session.commit()
    target = image_service.disk_path(path)
    if target:
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

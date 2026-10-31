"""Studio groups (categories and collections) and new products (owner, 2026-10-30).

Every Studio account may edit groups freely (no owner review: groups only arrange existing
products) and create products. A new product always starts hidden; it reaches the shop only
through "Put back live", which reviewed staff still send to the owner.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select

from app import cache
from app.catalog import units
from app.catalog.models import (
    Category,
    Collection,
    OptionAxis,
    OptionValue,
    Product,
    ProductStatus,
    ProductType,
    SaleUnit,
    Variant,
    combination_key,
    product_categories,
)
from app.extensions import db
from app.studio.models import StudioUser
from app.studio.service import StudioError, _local_datetime, audit

MAX_CATEGORY_DEPTH = 3  # same rule as the catalog import (Fabrics → Wax Prints → Grand Super)
MAX_OPTIONS = 30
TYPE_LABELS = {
    ProductType.FABRIC.value: "Fabric",
    ProductType.CLOTHING.value: "Clothing",
    ProductType.HOME_ACCESSORIES.value: "Home & accessories",
}


def slugify(name: str, model: type[Any], limit: int = 80) -> str:
    """A URL name from `name`, made unique for `model` with -2, -3, ..."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:limit].strip("-") or "item"
    slug, number = base, 2
    while db.session.scalar(select(model.id).where(model.slug == slug)) is not None:
        suffix = f"-{number}"
        slug = base[: limit - len(suffix)].rstrip("-") + suffix
        number += 1
    return slug


def _position(raw: str) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    if not text.isdigit() or int(text) > 10_000:
        raise StudioError("Order must be a whole number from 0 to 10000.")
    return int(text)


def _name_and_description(form: dict[str, str], label: str) -> tuple[str, str]:
    name = form.get("name", "").strip()
    description = form.get("description", "").strip()
    if not 1 <= len(name) <= 120:
        raise StudioError(f"{label} name must be between 1 and 120 characters.")
    if len(description) > 5000:
        raise StudioError("Description must be 5,000 characters or fewer.")
    return name, description


# ---------- categories ----------


def category_tree() -> list[tuple[Category, int]]:
    """Every category with its depth (0 = top level), in shop order."""
    tops = db.session.scalars(
        select(Category).where(Category.parent_id.is_(None)).order_by(Category.position)
    ).all()
    out: list[tuple[Category, int]] = []

    def walk(category: Category, depth: int) -> None:
        out.append((category, depth))
        for child in category.children:
            walk(child, depth + 1)

    for top in tops:
        walk(top, 0)
    return out


def product_counts() -> dict[int, int]:
    rows = db.session.execute(
        select(product_categories.c.category_id, func.count()).group_by(
            product_categories.c.category_id
        )
    ).all()
    return {category_id: count for category_id, count in rows}


def _height(category: Category) -> int:
    """Levels from this category down to its deepest child (1 = no children)."""
    return 1 + max((_height(child) for child in category.children), default=0)


def save_category(user: StudioUser, category: Category | None, form: dict[str, str]) -> Category:
    name, description = _name_and_description(form, "Category")
    position = _position(form.get("position", ""))
    parent: Category | None = None
    parent_raw = form.get("parent_id", "").strip()
    if parent_raw:
        if not parent_raw.isdigit():
            raise StudioError("Choose a valid parent category.")
        parent = db.session.get(Category, int(parent_raw))
        if parent is None:
            raise StudioError("Choose a valid parent category.")
    if category is not None and parent is not None and parent.id in category.subtree_ids():
        raise StudioError("A category cannot go inside itself or one of its own subcategories.")
    depth = (len(parent.ancestors) + 1) if parent else 0
    if depth + (_height(category) if category else 1) > MAX_CATEGORY_DEPTH:
        raise StudioError(
            f"Categories can be at most {MAX_CATEGORY_DEPTH} levels deep "
            "(like Fabrics → Wax Prints → Grand Super Wax)."
        )
    creating = category is None
    if category is None:
        category = Category(slug=slugify(name, Category))
        db.session.add(category)
    before = (
        {}
        if creating
        else {"name": category.name, "parent_id": category.parent_id, "position": category.position}
    )
    category.name = name
    category.description = description
    category.position = position
    category.parent = parent
    db.session.flush()
    audit(
        user,
        "studio.category.created" if creating else "studio.category.updated",
        "category",
        str(category.id),
        {
            "before": before,
            "after": {"name": name, "parent_id": category.parent_id, "position": position},
        },
    )
    db.session.commit()
    cache.bump_catalog_version()
    return category


def delete_category(user: StudioUser, category: Category) -> None:
    if category.children:
        raise StudioError("Move or delete its subcategories first.")
    if product_counts().get(category.id):
        raise StudioError("Move its products to another category first.")
    audit(user, "studio.category.deleted", "category", str(category.id), {"name": category.name})
    db.session.delete(category)
    db.session.commit()
    cache.bump_catalog_version()


# ---------- collections ----------


def _optional_datetime(raw: str, label: str) -> Any:
    return _local_datetime(raw.strip(), label) if raw and raw.strip() else None


def save_collection(
    user: StudioUser, collection: Collection | None, form: dict[str, str], product_ids: list[str]
) -> Collection:
    name, description = _name_and_description(form, "Collection")
    position = _position(form.get("position", ""))
    try:
        starts = _optional_datetime(form.get("starts_at", ""), "start")
        ends = _optional_datetime(form.get("ends_at", ""), "end")
    except StudioError:
        raise StudioError("Enter valid start and end dates, or leave them blank.") from None
    if starts and ends and ends <= starts:
        raise StudioError("The end date must be after the start date.")
    try:
        wanted = {int(value) for value in product_ids}
    except ValueError:
        raise StudioError("Choose valid products.") from None
    products = (
        db.session.scalars(select(Product).where(Product.id.in_(wanted))).all() if wanted else []
    )
    if len(products) != len(wanted):
        raise StudioError("One of the chosen products no longer exists. Reload and try again.")
    creating = collection is None
    if collection is None:
        collection = Collection(slug=slugify(name, Collection))
        db.session.add(collection)
    collection.name = name
    collection.description = description
    collection.position = position
    collection.starts_at = starts
    collection.ends_at = ends
    db.session.flush()
    members = set(collection_product_ids(collection))
    for product in products:
        if product.id not in members:
            product.collections.append(collection)
    for product in db.session.scalars(select(Product).where(Product.id.in_(members - wanted))):
        product.collections.remove(collection)
    audit(
        user,
        "studio.collection.created" if creating else "studio.collection.updated",
        "collection",
        str(collection.id),
        {"name": name, "products": sorted(wanted)},
    )
    db.session.commit()
    cache.bump_catalog_version()
    return collection


def collection_product_ids(collection: Collection) -> list[int]:
    return list(
        db.session.scalars(
            select(Product.id).where(Product.collections.any(Collection.id == collection.id))
        )
    )


def delete_collection(user: StudioUser, collection: Collection) -> None:
    """Products stay; only the grouping goes."""
    audit(
        user,
        "studio.collection.deleted",
        "collection",
        str(collection.id),
        {"name": collection.name},
    )
    db.session.delete(collection)
    db.session.commit()
    cache.bump_catalog_version()


# ---------- new products ----------


def _cents(raw: str) -> int:
    text = (raw or "").strip().replace("$", "").replace(",", "")
    whole, dot, fraction = text.partition(".")
    if not whole.isdigit() or (dot and (not fraction.isdigit() or len(fraction) > 2)):
        raise StudioError("Price must be a dollar amount, like 24.99.")
    cents = int(whole) * 100 + int((fraction + "00")[:2])
    if not 1 <= cents <= 100_000_000:
        raise StudioError("Price must be more than $0.")
    return cents


def _stock(raw: str) -> int:
    text = (raw or "").strip() or "0"
    if not text.isdigit() or int(text) > 100_000:
        raise StudioError("Stock must be a whole number from 0 to 100000.")
    return int(text)


def create_product(user: StudioUser, form: dict[str, str], category_ids: list[str]) -> Product:
    """A hidden product with one option per listed choice (or a single option)."""
    name = form.get("name", "").strip()
    summary = form.get("summary", "").strip()
    description = form.get("description", "").strip()
    if not 1 <= len(name) <= 200:
        raise StudioError("Product name must be between 1 and 200 characters.")
    if len(summary) > 300:
        raise StudioError("Summary must be 300 characters or fewer.")
    if len(description) > 20_000:
        raise StudioError("Description must be 20,000 characters or fewer.")
    try:
        product_type = ProductType(form.get("product_type", ""))
    except ValueError:
        raise StudioError("Choose what kind of product this is.") from None
    try:
        sale_unit = SaleUnit(form.get("sale_unit", "") or SaleUnit.FIXED_BUNDLE.value)
    except ValueError:
        raise StudioError("Choose bundle, yard, or metre for the selling unit.") from None
    try:
        piece_length, piece_max = units.parse_piece_range(form.get("piece_length_yards", ""))
        width = units.parse_width(form.get("width_inches", ""))
    except ValueError as exc:
        raise StudioError(str(exc)) from None
    try:
        wanted = {int(value) for value in category_ids}
    except ValueError:
        raise StudioError("Choose valid categories.") from None
    categories = db.session.scalars(select(Category).where(Category.id.in_(wanted))).all()
    if not categories or len(categories) != len(wanted):
        raise StudioError("Choose at least one category.")
    price = _cents(form.get("price", ""))
    stock = _stock(form.get("stock", ""))
    option_name = form.get("option_name", "").strip()
    choices: list[str] = []
    for raw in form.get("option_values", "").split(","):
        label = raw.strip()[:80]
        if label and label.casefold() not in {c.casefold() for c in choices}:
            choices.append(label)
    if choices and not 1 <= len(option_name) <= 60:
        raise StudioError("Name the choice (for example Colour or Size), or leave both blank.")
    if option_name and not choices:
        raise StudioError("List the choices separated by commas, or leave both blank.")
    if len(choices) > MAX_OPTIONS:
        raise StudioError(f"Up to {MAX_OPTIONS} choices per product.")

    product = Product(
        slug=slugify(name, Product, 120),
        name=name,
        product_type=product_type,
        status=ProductStatus.DRAFT,
        summary=summary,
        description=description,
        sale_unit=sale_unit.value,
        piece_length_yards=piece_length,
        piece_length_max_yards=piece_max,
        width_inches=width,
        categories=list(categories),
    )
    db.session.add(product)
    db.session.flush()
    if choices:
        axis = OptionAxis(product=product, name=option_name, position=0)
        for position, label in enumerate(choices):
            value = OptionValue(axis=axis, label=label, position=position)
            db.session.add(
                Variant(
                    product=product,
                    sku=f"GG-{product.id}-{position + 1}",
                    combination_key=combination_key({option_name: label}),
                    price_cents=price,
                    stock_quantity=stock,
                    position=position,
                    option_values=[value],
                )
            )
    else:
        db.session.add(
            Variant(
                product=product, sku=f"GG-{product.id}", price_cents=price, stock_quantity=stock
            )
        )
    db.session.flush()
    audit(
        user,
        "studio.product.created",
        "product",
        str(product.id),
        {"name": name, "price_cents": price, "options": choices},
    )
    db.session.commit()
    cache.bump_catalog_version()
    return product

"""Read-side catalog queries for the storefront. Only active products are ever returned."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import selectinload

from app.catalog.models import (
    Category,
    Collection,
    Product,
    ProductStatus,
    Variant,
    product_categories,
    product_collections,
    utcnow,
)
from app.extensions import db

PAGE_SIZE = 16  # feature matrix R-007
NEW_ARRIVAL_DAYS = 90

SORTS: dict[str, str] = {
    "default": "Default sorting",
    "newest": "Sort by latest",
    "price-asc": "Sort by price: low to high",
    "price-desc": "Sort by price: high to low",
    "name": "Sort by name",
}

# Collections computed from product data rather than hand-picked (D-004).
VIRTUAL_COLLECTIONS = {
    "new-arrivals": ("New Arrivals", "Our newest prints and pieces."),
    "sale": ("Sale", "Prints and pieces at reduced prices."),
}


@dataclass
class Page:
    items: list[Product]
    total: int
    page: int
    pages: int

    @property
    def first_index(self) -> int:
        return (self.page - 1) * PAGE_SIZE + 1 if self.total else 0

    @property
    def last_index(self) -> int:
        return (self.page - 1) * PAGE_SIZE + len(self.items)


def _active_variant() -> Select[int]:
    return select(Variant.id).where(Variant.product_id == Product.id, Variant.active.is_(True))


def _in_stock_clause():  # type: ignore[no-untyped-def]
    return exists(
        _active_variant().where(or_(Variant.track_inventory.is_(False), Variant.stock_quantity > 0))
    )


def _on_sale_clause():  # type: ignore[no-untyped-def]
    return exists(
        _active_variant().where(
            Variant.compare_at_cents.is_not(None), Variant.compare_at_cents > Variant.price_cents
        )
    )


def _min_price():  # type: ignore[no-untyped-def]
    return (
        select(func.min(Variant.price_cents))
        .where(Variant.product_id == Product.id, Variant.active.is_(True))
        .scalar_subquery()
    )


def base_query() -> Select[Product]:
    return (
        select(Product)
        .where(Product.status == ProductStatus.ACTIVE, exists(_active_variant()))
        .options(
            selectinload(Product.images),
            selectinload(Product.variants),
            selectinload(Product.categories),
        )
    )


def listing(
    *,
    categories: list[Category] | None = None,
    collection: str | None = None,
    query: str = "",
    in_stock: bool = False,
    sort: str = "default",
    page: int = 1,
) -> Page:
    stmt = base_query()
    if categories is not None:
        ids = [c.id for c in categories]
        stmt = stmt.where(
            exists(
                select(product_categories.c.product_id).where(
                    product_categories.c.product_id == Product.id,
                    product_categories.c.category_id.in_(ids),
                )
            )
        )
    if collection == "sale":
        stmt = stmt.where(_on_sale_clause())
    elif collection == "new-arrivals":
        stmt = stmt.where(Product.published_at >= utcnow() - timedelta(days=NEW_ARRIVAL_DAYS))
    elif collection:
        stmt = stmt.where(
            exists(
                select(product_collections.c.product_id)
                .join(Collection, Collection.id == product_collections.c.collection_id)
                .where(
                    product_collections.c.product_id == Product.id, Collection.slug == collection
                )
            )
        )
    if query:
        like = f"%{query.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                Product.summary.ilike(like),
                exists(_active_variant().where(Variant.sku.ilike(like))),
            )
        )
    if in_stock:
        stmt = stmt.where(_in_stock_clause())

    order = {
        "newest": [Product.published_at.desc().nulls_last()],
        "price-asc": [_min_price().asc()],
        "price-desc": [_min_price().desc()],
        "name": [Product.name.asc()],
    }.get(sort, [Product.featured.desc(), Product.published_at.desc().nulls_last()])
    stmt = stmt.order_by(*order, Product.id)  # id keeps pagination stable (C-010)

    total = db.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    pages = max(1, -(-(total or 0) // PAGE_SIZE))
    page = min(max(1, page), pages)
    items = list(db.session.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)))
    return Page(items, total or 0, page, pages)


def newest(limit: int) -> list[Product]:
    stmt = base_query().order_by(Product.published_at.desc().nulls_last(), Product.id.desc())
    return list(db.session.scalars(stmt.limit(limit)))


def featured(limit: int) -> list[Product]:
    stmt = base_query().where(Product.featured.is_(True)).order_by(Product.published_at.desc())
    items = list(db.session.scalars(stmt.limit(limit)))
    if len(items) < limit:  # top up with in-stock products so the row is never half-empty
        more = base_query().where(_in_stock_clause(), Product.id.not_in([p.id for p in items]))
        items += list(db.session.scalars(more.order_by(Product.name).limit(limit - len(items))))
    return items


def product_by_slug(slug: str) -> Product | None:
    stmt = (
        select(Product)
        .where(Product.slug == slug, Product.status == ProductStatus.ACTIVE)
        .options(
            selectinload(Product.images),
            selectinload(Product.option_axes),
            selectinload(Product.variants).selectinload(Variant.option_values),
            selectinload(Product.categories),
        )
    )
    return db.session.scalar(stmt)


def related(product: Product, limit: int = 8) -> list[Product]:
    """Same category first, in stock only, never the product itself (C-008)."""
    cat_ids = [c.id for c in product.categories]
    stmt = (
        base_query()
        .where(Product.id != product.id, _in_stock_clause())
        .order_by(
            exists(
                select(product_categories.c.product_id).where(
                    and_(
                        product_categories.c.product_id == Product.id,
                        product_categories.c.category_id.in_(cat_ids),
                    )
                )
            ).desc(),
            Product.published_at.desc().nulls_last(),
            Product.id,
        )
    )
    return list(db.session.scalars(stmt.limit(limit)))


def category_by_slug(slug: str) -> Category | None:
    return db.session.scalar(
        select(Category).where(Category.slug == slug).options(selectinload(Category.children))
    )


def top_categories() -> list[Category]:
    stmt = (
        select(Category)
        .where(Category.parent_id.is_(None))
        .order_by(Category.position)
        .options(selectinload(Category.children))
    )
    return list(db.session.scalars(stmt))


def categories_by_slug(slugs: list[str]) -> list[Category]:
    found = {
        c.slug: c for c in db.session.scalars(select(Category).where(Category.slug.in_(slugs)))
    }
    return [found[s] for s in slugs if s in found]


def collection_by_slug(slug: str) -> Collection | None:
    col = db.session.scalar(select(Collection).where(Collection.slug == slug))
    return col if col and col.is_live() else None


def category_counts() -> dict[int, int]:
    """Active product count per category (children roll up into parents)."""
    rows = db.session.execute(
        select(product_categories.c.category_id, func.count())
        .join(Product, Product.id == product_categories.c.product_id)
        .where(Product.status == ProductStatus.ACTIVE)
        .group_by(product_categories.c.category_id)
    ).all()
    counts = {cid: n for cid, n in rows}
    for cat in db.session.scalars(select(Category).where(Category.parent_id.is_not(None))):
        if cat.parent_id is not None:
            counts[cat.parent_id] = counts.get(cat.parent_id, 0) + counts.get(cat.id, 0)
    return counts

"""Catalog schema.

Separate tables for categories, collections, option axes and variants; no generic
taxonomy layer (catalog decisions T-010). Every product has at least one variant: a
simple product has a single variant with an empty combination key, so carts and
orders always reference a variant.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProductType(enum.StrEnum):
    FABRIC = "fabric"
    CLOTHING = "clothing"
    HOME_ACCESSORIES = "home_accessories"


class ProductStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SaleUnit(enum.StrEnum):
    FIXED_BUNDLE = "fixed_bundle"
    YARD = "yard"
    METRE = "metre"


product_categories = Table(
    "product_categories",
    db.metadata,
    Column("product_id", ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)

product_collections = Table(
    "product_collections",
    db.metadata,
    Column("product_id", ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
    Column("collection_id", ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
)

variant_option_values = Table(
    "variant_option_values",
    db.metadata,
    Column("variant_id", ForeignKey("variants.id", ondelete="CASCADE"), primary_key=True),
    Column("option_value_id", ForeignKey("option_values.id", ondelete="CASCADE"), primary_key=True),
)


class Category(db.Model):  # type: ignore[name-defined]
    """What the product is. At most two public levels (D-003); enforced by the manifest loader."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    image: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))

    parent: Mapped[Category | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Category]] = relationship(
        back_populates="parent", order_by="Category.position"
    )

    def __repr__(self) -> str:
        return f"<Category {self.slug}>"


class Collection(db.Model):  # type: ignore[name-defined]
    """Why or when to shop (D-004): flat, hand-picked, optionally dated."""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    image: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at"),
    )

    def is_live(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        starts, ends = _aware(self.starts_at), _aware(self.ends_at)
        return (starts is None or starts <= now) and (ends is None or now < ends)


class Product(db.Model):  # type: ignore[name-defined]
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "sale_unit IN ('fixed_bundle', 'yard', 'metre')",
            name="product_sale_unit_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    product_type: Mapped[ProductType] = mapped_column(
        Enum(ProductType, name="product_type", values_callable=lambda e: [m.value for m in e])
    )
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, name="product_status", values_callable=lambda e: [m.value for m in e]),
        default=ProductStatus.DRAFT,
        index=True,
    )
    summary: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    featured: Mapped[bool] = mapped_column(default=False)
    sale_unit: Mapped[str] = mapped_column(
        String(20), default=SaleUnit.FIXED_BUNDLE.value, server_default="fixed_bundle"
    )
    # Comma-separated names from naming.PALETTE (controlled vocabulary, future color filter).
    colors: Mapped[str] = mapped_column(String(200), default="", server_default="")
    legacy_wp_id: Mapped[int | None] = mapped_column(Integer)  # editorial traceability only
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    categories: Mapped[list[Category]] = relationship(secondary=product_categories)
    collections: Mapped[list[Collection]] = relationship(secondary=product_collections)
    images: Mapped[list[ProductImage]] = relationship(
        back_populates="product",
        order_by="ProductImage.position",
        cascade="all, delete-orphan",
    )
    option_axes: Mapped[list[OptionAxis]] = relationship(
        back_populates="product", order_by="OptionAxis.position", cascade="all, delete-orphan"
    )
    variants: Mapped[list[Variant]] = relationship(
        back_populates="product", order_by="Variant.position", cascade="all, delete-orphan"
    )

    @property
    def active_variants(self) -> list[Variant]:
        return [v for v in self.variants if v.active]

    @property
    def has_options(self) -> bool:
        return bool(self.option_axes)

    @property
    def price_cents(self) -> int:
        """Lowest active variant price (what a product card shows)."""
        prices = [v.price_cents for v in self.active_variants]
        return min(prices) if prices else 0

    @property
    def compare_at_cents(self) -> int | None:
        cheapest = min(self.active_variants, key=lambda v: v.price_cents, default=None)
        return cheapest.compare_at_cents if cheapest else None

    @property
    def on_sale(self) -> bool:
        return any(v.on_sale for v in self.active_variants)

    @property
    def in_stock(self) -> bool:
        return any(v.available_quantity > 0 for v in self.active_variants)

    @property
    def sale_unit_label(self) -> str:
        return {
            SaleUnit.FIXED_BUNDLE.value: "bundle",
            SaleUnit.YARD.value: "yard",
            SaleUnit.METRE.value: "metre",
        }.get(self.sale_unit, "bundle")

    @property
    def color_list(self) -> list[str]:
        return [c for c in self.colors.split(",") if c]

    @property
    def primary_category(self) -> Category | None:
        return self.categories[0] if self.categories else None

    @property
    def cover(self) -> ProductImage | None:
        return self.images[0] if self.images else None

    def __repr__(self) -> str:
        return f"<Product {self.slug}>"


class ProductImage(db.Model):  # type: ignore[name-defined]
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(255))  # relative to the media root
    alt: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="images")


class OptionAxis(db.Model):  # type: ignore[name-defined]
    """A choice that creates separately stocked/priced variants (T-002), e.g. Colorway."""

    __tablename__ = "option_axes"
    __table_args__ = (UniqueConstraint("product_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="option_axes")
    values: Mapped[list[OptionValue]] = relationship(
        back_populates="axis", order_by="OptionValue.position", cascade="all, delete-orphan"
    )


class OptionValue(db.Model):  # type: ignore[name-defined]
    __tablename__ = "option_values"
    __table_args__ = (UniqueConstraint("axis_id", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    axis_id: Mapped[int] = mapped_column(ForeignKey("option_axes.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0)

    axis: Mapped[OptionAxis] = relationship(back_populates="values")


class Variant(db.Model):  # type: ignore[name-defined]
    """One sellable combination with its own SKU, price, stock and photo."""

    __tablename__ = "variants"
    __table_args__ = (
        # Required invariant: no two variants of a product share an option combination.
        UniqueConstraint("product_id", "combination_key"),
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint(
            "compare_at_cents IS NULL OR compare_at_cents > price_cents", name="compare_above_price"
        ),
        CheckConstraint("stock_quantity >= 0", name="stock_non_negative"),
        CheckConstraint("weight_grams IS NULL OR weight_grams >= 0", name="weight_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    combination_key: Mapped[str] = mapped_column(String(255), default="")
    price_cents: Mapped[int] = mapped_column(Integer)
    compare_at_cents: Mapped[int | None] = mapped_column(Integer)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    track_inventory: Mapped[bool] = mapped_column(default=True)
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    image_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_images.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="variants")
    option_values: Mapped[list[OptionValue]] = relationship(secondary=variant_option_values)
    image: Mapped[ProductImage | None] = relationship()

    @property
    def on_sale(self) -> bool:
        return self.compare_at_cents is not None and self.compare_at_cents > self.price_cents

    @property
    def available_quantity(self) -> int:
        """Units that can be sold now. Untracked inventory is capped for safety."""
        return self.stock_quantity if self.track_inventory else 999

    @property
    def label(self) -> str:
        return " / ".join(v.label for v in self.option_values) or self.product.name


def combination_key(options: dict[str, str]) -> str:
    """Normalised, order-independent key for an {axis: value} choice, e.g. 'colorway=blue'."""
    return "|".join(
        f"{axis.strip().casefold()}={value.strip().casefold()}"
        for axis, value in sorted(options.items(), key=lambda kv: kv[0].casefold())
    )


def _aware(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

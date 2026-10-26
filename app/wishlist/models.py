"""Anonymous wishlist (W-001). Like the cart, the browser holds a random token and the
database stores only its SHA-256 hash plus product IDs. No personal data is kept."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import Product, utcnow
from app.extensions import db


class Wishlist(db.Model):  # type: ignore[name-defined]
    __tablename__ = "wishlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Touched on every change; lists idle longer than RETENTION_DAYS are pruned.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True
    )

    items: Mapped[list[WishlistItem]] = relationship(
        back_populates="wishlist",
        order_by="WishlistItem.created_at.desc()",
        cascade="all, delete-orphan",
    )


class WishlistItem(db.Model):  # type: ignore[name-defined]
    __tablename__ = "wishlist_items"
    __table_args__ = (UniqueConstraint("wishlist_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wishlist_id: Mapped[int] = mapped_column(
        ForeignKey("wishlists.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    wishlist: Mapped[Wishlist] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()

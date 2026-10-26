"""Server-side cart. The browser only holds an opaque random token; the database stores
its SHA-256 hash, so a leaked database cannot be replayed as cart cookies (O-001)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import Variant, utcnow
from app.extensions import db


class Cart(db.Model):  # type: ignore[name-defined]
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True
    )

    lines: Mapped[list[CartLine]] = relationship(
        back_populates="cart", order_by="CartLine.id", cascade="all, delete-orphan"
    )


class CartLine(db.Model):  # type: ignore[name-defined]
    __tablename__ = "cart_lines"
    __table_args__ = (
        UniqueConstraint("cart_id", "variant_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer)

    cart: Mapped[Cart] = relationship(back_populates="lines")
    variant: Mapped[Variant] = relationship()

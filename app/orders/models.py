"""Orders, immutable line snapshots, coupons, and temporary stock holds."""

from __future__ import annotations

import enum
from datetime import datetime
from urllib.parse import quote

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.cart.models import Cart
from app.catalog.models import Variant, utcnow
from app.extensions import db


class OrderStatus(enum.StrEnum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentProvider(enum.StrEnum):
    STRIPE = "stripe"
    PAYPAL = "paypal"


class PaymentStatus(enum.StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class DiscountType(enum.StrEnum):
    FIXED = "fixed"
    PERCENT = "percent"


class Coupon(db.Model):  # type: ignore[name-defined]
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType, name="discount_type", values_callable=lambda e: [m.value for m in e])
    )
    amount: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    minimum_subtotal_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("amount > 0", name="coupon_amount_positive"),
        CheckConstraint("uses >= 0", name="coupon_uses_non_negative"),
    )


# Carriers the owner can choose when marking an order shipped: code -> (name, tracking URL prefix).
TRACKING_CARRIERS: dict[str, tuple[str, str]] = {
    "usps": ("USPS", "https://tools.usps.com/go/TrackConfirmAction?tLabels="),
    "ups": ("UPS", "https://www.ups.com/track?tracknum="),
    "fedex": ("FedEx", "https://www.fedex.com/fedextrack/?trknbr="),
    "dhl": ("DHL", "https://www.dhl.com/us-en/home/tracking.html?tracking-id="),
}


class Order(db.Model):  # type: ignore[name-defined]
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(24), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cart_id: Mapped[int | None] = mapped_column(ForeignKey("carts.id", ondelete="SET NULL"))
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", values_callable=lambda e: [m.value for m in e]),
        default=OrderStatus.PENDING_PAYMENT,
        index=True,
    )
    payment_provider: Mapped[PaymentProvider] = mapped_column(
        Enum(
            PaymentProvider, name="payment_provider", values_callable=lambda e: [m.value for m in e]
        )
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", values_callable=lambda e: [m.value for m in e]),
        default=PaymentStatus.PENDING,
    )
    provider_reference: Mapped[str | None] = mapped_column(String(160), unique=True)
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(30))
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    address1: Mapped[str] = mapped_column(String(160))
    address2: Mapped[str] = mapped_column(String(160), default="")
    city: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(100))
    postcode: Mapped[str] = mapped_column(String(24))
    country: Mapped[str] = mapped_column(String(2))
    subtotal_cents: Mapped[int] = mapped_column(Integer)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    shipping_cents: Mapped[int] = mapped_column(Integer)
    # Which rate was charged (Shippo rate id is kept to buy the matching label later).
    shipping_carrier: Mapped[str | None] = mapped_column(String(40))
    shipping_service: Mapped[str | None] = mapped_column(String(80))
    shipping_rate_ref: Mapped[str | None] = mapped_column(String(80))
    # Filled when the owner marks the order shipped (labels bought outside the app).
    tracking_carrier: Mapped[str | None] = mapped_column(String(20))
    tracking_number: Mapped[str | None] = mapped_column(String(40))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Estimated parcel weight when the order was placed (fabric at 16 oz/sq yd + packaging),
    # shown in order admin with its USPS price step.
    parcel_weight_grams: Mapped[int | None] = mapped_column(Integer)
    tax_cents: Mapped[int] = mapped_column(Integer)
    # Stripe Tax calculation (at checkout) and transaction (after payment) for reporting.
    tax_calculation_ref: Mapped[str | None] = mapped_column(String(80))
    tax_transaction_ref: Mapped[str | None] = mapped_column(String(80))
    total_cents: Mapped[int] = mapped_column(Integer)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Provider refund id and state (pending/completed/failed); the order only becomes Refunded
    # once the provider confirms it (`flask orders sync-refunds` follows up on pending ones).
    refund_reference: Mapped[str | None] = mapped_column(String(80))
    refund_status: Mapped[str | None] = mapped_column(String(20), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    hold_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order", order_by="OrderLine.id", cascade="all, delete-orphan"
    )
    holds: Mapped[list[StockHold]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    cart: Mapped[Cart | None] = relationship()
    notes: Mapped[list[OrderNote]] = relationship(
        back_populates="order", order_by="OrderNote.created_at", cascade="all, delete-orphan"
    )

    @property
    def tracking_url(self) -> str | None:
        carrier = TRACKING_CARRIERS.get(self.tracking_carrier or "")
        if not carrier or not self.tracking_number:
            return None
        return carrier[1] + quote(self.tracking_number)


class OrderLine(db.Model):  # type: ignore[name-defined]
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="SET NULL"))
    product_name: Mapped[str] = mapped_column(String(200))
    variant_label: Mapped[str] = mapped_column(String(300), default="")
    sku: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    line_total_cents: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="lines")
    variant: Mapped[Variant | None] = relationship()


class StockHold(db.Model):  # type: ignore[name-defined]
    __tablename__ = "stock_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="holds")
    variant: Mapped[Variant] = relationship()

    __table_args__ = (CheckConstraint("quantity > 0", name="stock_hold_quantity_positive"),)


class PaymentEvent(db.Model):  # type: ignore[name-defined]
    """A provider event receipt; the unique key makes webhook handling replay-safe."""

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[PaymentProvider] = mapped_column(
        Enum(
            PaymentProvider,
            name="payment_provider",
            values_callable=lambda e: [m.value for m in e],
            create_constraint=False,
        )
    )
    event_id: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(120))
    payload_hash: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order | None] = relationship()

    __table_args__ = (UniqueConstraint("provider", "event_id"),)


class OrderNote(db.Model):  # type: ignore[name-defined]
    __tablename__ = "order_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="notes")


class OrderAlertRecipient(db.Model):  # type: ignore[name-defined]
    """Who hears about new paid orders (owner, 2026-10-30): an email address, or an ntfy topic
    that phones subscribe to in the free ntfy app (no sign-up). Managed in /admin/alerts."""

    __tablename__ = "order_alert_recipients"
    __table_args__ = (
        UniqueConstraint("kind", "value"),
        CheckConstraint("kind IN ('email', 'ntfy')", name="order_alert_kind_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    value: Mapped[str] = mapped_column(String(254))
    label: Mapped[str] = mapped_column(String(80), default="")
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

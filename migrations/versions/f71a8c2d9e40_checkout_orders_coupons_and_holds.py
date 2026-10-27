"""checkout orders coupons and holds

Revision ID: f71a8c2d9e40
Revises: d31c9ef5a7b2
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "f71a8c2d9e40"
down_revision: str | None = "d31c9ef5a7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    discount_type = postgresql.ENUM("fixed", "percent", name="discount_type", create_type=False)
    order_status = postgresql.ENUM(
        "pending_payment",
        "paid",
        "processing",
        "shipped",
        "completed",
        "cancelled",
        "refunded",
        name="order_status", create_type=False)
    payment_provider = postgresql.ENUM("stripe", "paypal", name="payment_provider", create_type=False)
    payment_status = postgresql.ENUM("pending", "paid", "failed", "refunded", name="payment_status", create_type=False)
    for enum in (discount_type, order_status, payment_provider, payment_status):
        enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("discount_type", discount_type, nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column("minimum_subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="coupon_amount_positive"),
        sa.CheckConstraint("uses >= 0", name="coupon_uses_non_negative"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(24), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("cart_id", sa.Integer(), sa.ForeignKey("carts.id", ondelete="SET NULL")),
        sa.Column("status", order_status, nullable=False),
        sa.Column("payment_provider", payment_provider, nullable=False),
        sa.Column("payment_status", payment_status, nullable=False),
        sa.Column("provider_reference", sa.String(160), unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone", sa.String(30)),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column("address1", sa.String(160), nullable=False),
        sa.Column("address2", sa.String(160), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("postcode", sa.String(24), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column("shipping_cents", sa.Integer(), nullable=False),
        sa.Column("tax_cents", sa.Integer(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("refunded_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("coupon_code", sa.String(40)),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_hold_expires_at", "orders", ["hold_expires_at"])
    op.create_index("ix_orders_created_at", "orders", ["created_at"])
    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("variants.id", ondelete="SET NULL")),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("variant_label", sa.String(300), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
    )
    op.create_index("ix_order_lines_order_id", "order_lines", ["order_id"])
    op.create_table(
        "stock_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "variant_id",
            sa.Integer(),
            sa.ForeignKey("variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="stock_hold_quantity_positive"),
    )
    op.create_index("ix_stock_holds_order_id", "stock_holds", ["order_id"])
    op.create_index("ix_stock_holds_variant_id", "stock_holds", ["variant_id"])
    op.create_index("ix_stock_holds_expires_at", "stock_holds", ["expires_at"])
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", payment_provider, nullable=False),
        sa.Column("event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "event_id"),
    )
    op.create_index("ix_payment_events_order_id", "payment_events", ["order_id"])
    op.create_table(
        "order_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="SET NULL")),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_order_notes_order_id", "order_notes", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_notes")
    op.drop_table("payment_events")
    op.drop_table("stock_holds")
    op.drop_table("order_lines")
    op.drop_table("orders")
    op.drop_table("coupons")
    for name in ("payment_status", "payment_provider", "order_status", "discount_type"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)

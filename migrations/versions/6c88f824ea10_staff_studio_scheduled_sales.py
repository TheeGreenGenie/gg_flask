"""staff studio scheduled sales

Revision ID: 6c88f824ea10
Revises: 1f3b9a72c6d4
Create Date: 2026-10-27 05:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "6c88f824ea10"
down_revision = "1f3b9a72c6d4"
branch_labels = None
depends_on = None


def upgrade():
    discount_kind = sa.Enum("percent", "fixed", "set_price", name="studio_sale_discount_kind")
    sale_status = sa.Enum(
        "draft", "scheduled", "active", "ended", "cancelled", name="studio_sale_status"
    )
    op.create_table(
        "studio_sales",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("discount_kind", discount_kind, nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sale_status, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["studio_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_studio_sales_status", "studio_sales", ["status"])
    op.create_index("ix_studio_sales_starts_at", "studio_sales", ["starts_at"])
    op.create_index("ix_studio_sales_ends_at", "studio_sales", ["ends_at"])
    op.create_table(
        "studio_sale_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("original_price_cents", sa.Integer(), nullable=False),
        sa.Column("sale_price_cents", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict_reason", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["sale_id"], ["studio_sales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["variants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sale_id", "variant_id"),
    )
    op.create_index("ix_studio_sale_items_sale_id", "studio_sale_items", ["sale_id"])
    op.create_index("ix_studio_sale_items_variant_id", "studio_sale_items", ["variant_id"])


def downgrade():
    op.drop_table("studio_sale_items")
    op.drop_table("studio_sales")
    if op.get_bind().dialect.name == "postgresql":
        sa.Enum(name="studio_sale_status").drop(op.get_bind(), checkfirst=True)
        sa.Enum(name="studio_sale_discount_kind").drop(op.get_bind(), checkfirst=True)

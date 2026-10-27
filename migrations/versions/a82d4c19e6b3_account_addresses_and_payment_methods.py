"""account addresses and stored provider payment references

Revision ID: a82d4c19e6b3
Revises: f71a8c2d9e40
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa

revision = "a82d4c19e6b3"
down_revision = "f71a8c2d9e40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "addresses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("first_name", sa.String(80), nullable=False),
        sa.Column("last_name", sa.String(80), nullable=False),
        sa.Column("address1", sa.String(160), nullable=False),
        sa.Column("address2", sa.String(160), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("region", sa.String(100), nullable=False),
        sa.Column("postcode", sa.String(24), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("phone", sa.String(30)),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_addresses_user_id", "addresses", ["user_id"])
    op.create_table(
        "stored_payment_methods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_customer_ref", sa.String(160), nullable=False),
        sa.Column("provider_method_ref", sa.String(160), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("brand", sa.String(40), nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("expiry_month", sa.Integer()),
        sa.Column("expiry_year", sa.Integer()),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider", "provider_method_ref"),
    )
    op.create_index("ix_stored_payment_methods_user_id", "stored_payment_methods", ["user_id"])


def downgrade() -> None:
    op.drop_table("stored_payment_methods")
    op.drop_table("addresses")

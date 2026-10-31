"""order alert recipients (emails and ntfy topics told about new paid orders)

Revision ID: 3b7e2f1a9c55
Revises: 8e4b1c7d2a90
Create Date: 2026-10-30 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "3b7e2f1a9c55"
down_revision = "8e4b1c7d2a90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_alert_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=254), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('email', 'ntfy')", name="order_alert_kind_valid"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "value"),
    )


def downgrade() -> None:
    op.drop_table("order_alert_recipients")

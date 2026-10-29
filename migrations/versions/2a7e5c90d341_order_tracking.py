"""order tracking number, carrier and shipped time

Revision ID: 2a7e5c90d341
Revises: 1c4d87f4b613
Create Date: 2026-10-28 16:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "2a7e5c90d341"
down_revision = "1c4d87f4b613"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("tracking_carrier", sa.String(length=20), nullable=True))
    op.add_column("orders", sa.Column("tracking_number", sa.String(length=40), nullable=True))
    op.add_column("orders", sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "shipped_at")
    op.drop_column("orders", "tracking_number")
    op.drop_column("orders", "tracking_carrier")

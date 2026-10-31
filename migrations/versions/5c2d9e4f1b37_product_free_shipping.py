"""per-product free shipping (the launch test item)

Revision ID: 5c2d9e4f1b37
Revises: 3b7e2f1a9c55
Create Date: 2026-10-31 01:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "5c2d9e4f1b37"
down_revision = "3b7e2f1a9c55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("free_shipping", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("products", "free_shipping")

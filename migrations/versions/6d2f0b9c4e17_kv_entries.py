"""key/value table standing in for Redis on hosts without it (rate limits, caches)

Revision ID: 6d2f0b9c4e17
Revises: 3b8f1e62a905
Create Date: 2026-10-29 02:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "6d2f0b9c4e17"
down_revision = "3b8f1e62a905"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kv_entries",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("kv_entries")

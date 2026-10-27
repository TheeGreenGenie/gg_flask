"""Add conflict-aware inventory history undo marker.

Revision ID: e2a71bd54c90
Revises: d9e44b6ac130
"""

from alembic import op
import sqlalchemy as sa

revision = "e2a71bd54c90"
down_revision = "d9e44b6ac130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("studio_inventory_history") as batch_op:
        batch_op.add_column(sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("studio_inventory_history") as batch_op:
        batch_op.drop_column("undone_at")

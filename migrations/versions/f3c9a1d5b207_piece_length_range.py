"""piece length range (e.g. 4-5 yards)

Revision ID: f3c9a1d5b207
Revises: e5b7d2a4c118
Create Date: 2026-10-28 15:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f3c9a1d5b207"
down_revision = "e5b7d2a4c118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: a SQLite batch rebuild would drop the unique flag on existing indexes.
    op.add_column("products", sa.Column("piece_length_max_yards", sa.Numeric(6, 2), nullable=True))
    if op.get_bind().dialect.name != "sqlite":  # SQLite cannot add a CHECK to an existing table
        op.create_check_constraint(
            "piece_length_range_valid",
            "products",
            "piece_length_max_yards IS NULL OR piece_length_max_yards > piece_length_yards",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("piece_length_range_valid", "products", type_="check")
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("piece_length_max_yards")

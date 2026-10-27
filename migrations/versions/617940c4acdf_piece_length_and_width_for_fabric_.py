"""Piece length and width for fabric bundles.

Owner, 2026-10-27: the per-yard prints are precut 1-yard pieces; every product can record the
length of one piece and the fabric width, which also drive the shipping-weight estimate.

Revision ID: 617940c4acdf
Revises: f4b93c10ad72
Create Date: 2026-10-27 15:31:03.445438

"""

import sqlalchemy as sa
from alembic import op

revision = "617940c4acdf"
down_revision = "f4b93c10ad72"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("piece_length_yards", sa.Numeric(precision=6, scale=2), nullable=True)
        )
        batch_op.add_column(sa.Column("width_inches", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "piece_length_positive", "piece_length_yards IS NULL OR piece_length_yards > 0"
        )
        batch_op.create_check_constraint(
            "width_inches_positive", "width_inches IS NULL OR width_inches > 0"
        )


def downgrade():
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_constraint("width_inches_positive", type_="check")
        batch_op.drop_constraint("piece_length_positive", type_="check")
        batch_op.drop_column("width_inches")
        batch_op.drop_column("piece_length_yards")

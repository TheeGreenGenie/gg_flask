"""Parcel weight on orders.

Estimated parcel weight when the order was placed (fabric at 16 oz per square yard plus
packaging), shown in order admin with its USPS price step (owner, 2026-10-27).

Revision ID: b09691a03912
Revises: 617940c4acdf
Create Date: 2026-10-27

"""

import sqlalchemy as sa
from alembic import op

revision = "b09691a03912"
down_revision = "617940c4acdf"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parcel_weight_grams", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("parcel_weight_grams")

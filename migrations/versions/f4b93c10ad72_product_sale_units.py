"""Add a selectable selling unit to products.

Revision ID: f4b93c10ad72
Revises: e2a71bd54c90
"""

from alembic import op
import sqlalchemy as sa

revision = "f4b93c10ad72"
down_revision = "e2a71bd54c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sale_unit",
                sa.String(length=20),
                nullable=False,
                server_default="fixed_bundle",
            )
        )
        batch_op.create_check_constraint(
            "product_sale_unit_valid",
            "sale_unit IN ('fixed_bundle', 'yard', 'metre')",
        )


def downgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint("product_sale_unit_valid", type_="check")
        batch_op.drop_column("sale_unit")

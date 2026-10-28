"""link a Studio account to the /admin owner account of the same person

Revision ID: 3b8f1e62a905
Revises: 1254ba17dc34
Create Date: 2026-10-28 22:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "3b8f1e62a905"
down_revision = "1254ba17dc34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: a SQLite batch rebuild would drop the unique flag on existing indexes.
    op.add_column("studio_users", sa.Column("admin_user_id", sa.Integer(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":  # SQLite cannot add a constraint to a live table
        op.create_foreign_key(
            "fk_studio_users_admin_user_id",
            "studio_users",
            "admin_users",
            ["admin_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_studio_users_admin_user_id", "studio_users", type_="foreignkey")
    with op.batch_alter_table("studio_users") as batch_op:
        batch_op.drop_column("admin_user_id")

"""studio authenticator-app sign-in codes

Revision ID: e5b7d2a4c118
Revises: c35a8e42f914
Create Date: 2026-10-28 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e5b7d2a4c118"
down_revision = "c35a8e42f914"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: a SQLite batch rebuild would drop the unique flag on existing indexes.
    op.add_column("studio_users", sa.Column("totp_secret", sa.String(length=64), nullable=True))
    op.add_column("studio_users", sa.Column("totp_last_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("studio_users") as batch_op:
        batch_op.drop_column("totp_last_step")
        batch_op.drop_column("totp_secret")

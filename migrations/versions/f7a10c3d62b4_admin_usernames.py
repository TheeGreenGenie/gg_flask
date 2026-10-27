"""Use usernames for private administrator sign-in.

Revision ID: f7a10c3d62b4
Revises: e5b7c2a19f40
Create Date: 2026-10-27
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "f7a10c3d62b4"
down_revision = "e5b7c2a19f40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(length=64), nullable=True))

    table = sa.table(
        "admin_users",
        sa.column("id", sa.Integer()),
        sa.column("email", sa.String()),
        sa.column("username", sa.String()),
    )
    connection = op.get_bind()
    used: set[str] = set()
    for row in connection.execute(sa.select(table.c.id, table.c.email).order_by(table.c.id)):
        local_part = str(row.email or "admin").split("@", 1)[0].lower()
        base = re.sub(r"[^a-z0-9._-]+", "-", local_part).strip("._-")
        if not base or not base[0].isalpha():
            base = f"admin-{row.id}"
        base = base[:64]
        candidate = base if len(base) >= 3 else f"admin-{row.id}"
        if candidate in used:
            suffix = f"-{row.id}"
            candidate = base[: 64 - len(suffix)] + suffix
        used.add(candidate)
        connection.execute(table.update().where(table.c.id == row.id).values(username=candidate))

    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(length=64), nullable=False)
        batch_op.create_index("ix_admin_users_username", ["username"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.drop_index("ix_admin_users_username")
        batch_op.drop_column("username")

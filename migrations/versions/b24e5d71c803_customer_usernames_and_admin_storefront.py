"""customer usernames and linked admin storefront identities

Revision ID: b24e5d71c803
Revises: a13f0c92e471
Create Date: 2026-10-27 23:20:00
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa


revision = "b24e5d71c803"
down_revision = "a13f0c92e471"
branch_labels = None
depends_on = None


def _candidate(email: str, user_id: int) -> str:
    local = email.partition("@")[0].lower()
    value = re.sub(r"[^a-z0-9._-]", "-", local).strip("._-")
    if not value or not value[0].isalpha():
        value = f"user{user_id}"
    value = value[:64]
    return value if len(value) >= 3 else f"user{user_id}"


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))
    bind = op.get_bind()
    reserved = {
        row.username for row in bind.execute(sa.text("SELECT username FROM admin_users")).mappings()
    }
    used = set(reserved)
    users = bind.execute(sa.text("SELECT id, email FROM users ORDER BY id")).mappings()
    for row in users:
        base = _candidate(str(row.email), int(row.id))
        username = base
        suffix = 2
        while username in used:
            tail = f"-{suffix}"
            username = base[: 64 - len(tail)] + tail
            suffix += 1
        bind.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :user_id"),
            {"username": username, "user_id": row.id},
        )
        used.add(username)
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", existing_type=sa.String(length=64), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    with op.batch_alter_table("admin_users") as batch:
        batch.add_column(sa.Column("customer_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_admin_users_customer_user_id_users",
            "users",
            ["customer_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_admin_users_customer_user_id", ["customer_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("admin_users") as batch:
        batch.drop_constraint("uq_admin_users_customer_user_id", type_="unique")
        batch.drop_constraint("fk_admin_users_customer_user_id_users", type_="foreignkey")
        batch.drop_column("customer_user_id")
    op.drop_index("ix_users_username", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("username")

"""admin security and audit

Revision ID: d31c9ef5a7b2
Revises: 8a916f0a8726
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "d31c9ef5a7b2"
down_revision: str | None = "8a916f0a8726"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    role = postgresql.ENUM("owner", "catalog", "support", name="admin_role", create_type=False)
    role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("totp_secret", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "admin_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_recovery_codes_admin_id", "admin_recovery_codes", ["admin_id"])
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="SET NULL")),
        sa.Column("actor_email", sa.String(254), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(80)),
        sa.Column("entity_id", sa.String(120)),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_audit_admin_id", "admin_audit", ["admin_id"])
    op.create_index("ix_admin_audit_action", "admin_audit", ["action"])
    op.create_index("ix_admin_audit_created_at", "admin_audit", ["created_at"])


def downgrade() -> None:
    op.drop_table("admin_audit")
    op.drop_table("admin_recovery_codes")
    op.drop_table("admin_users")
    sa.Enum(name="admin_role").drop(op.get_bind(), checkfirst=True)

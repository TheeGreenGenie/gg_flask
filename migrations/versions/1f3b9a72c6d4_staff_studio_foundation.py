"""staff studio authentication, sessions, audit and change history

Revision ID: 1f3b9a72c6d4
Revises: 45ddbba49b58
Create Date: 2026-10-27 04:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "1f3b9a72c6d4"
down_revision = "45ddbba49b58"
branch_labels = None
depends_on = None


def upgrade():
    approval_status = sa.Enum(
        "pending", "approved", "denied", "consumed", "expired", name="studio_approval_status"
    )
    op.create_table(
        "studio_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_studio_users_username", "studio_users", ["username"], unique=True)
    op.create_table(
        "studio_login_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("approval_token_hash", sa.String(length=64), nullable=False),
        sa.Column("match_code", sa.String(length=2), nullable=False),
        sa.Column("status", approval_status, nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["studio_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_token_hash"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_studio_login_requests_request_id", "studio_login_requests", ["request_id"])
    op.create_index("ix_studio_login_requests_status", "studio_login_requests", ["status"])
    op.create_index("ix_studio_login_requests_expires_at", "studio_login_requests", ["expires_at"])
    op.create_table(
        "studio_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["studio_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_studio_sessions_token_hash", "studio_sessions", ["token_hash"])
    op.create_index("ix_studio_sessions_expires_at", "studio_sessions", ["expires_at"])
    op.create_table(
        "studio_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["studio_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_studio_audit_user_id", "studio_audit", ["user_id"])
    op.create_index("ix_studio_audit_action", "studio_audit", ["action"])
    op.create_index("ix_studio_audit_created_at", "studio_audit", ["created_at"])
    _create_history("studio_inventory_history", "before_quantity", "after_quantity")
    _create_history("studio_price_history", "before_cents", "after_cents", with_undo=True)


def _create_history(table, before, after, with_undo=False):
    columns = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(before, sa.Integer(), nullable=False),
        sa.Column(after, sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=False),
    ]
    if with_undo:
        columns.append(sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True))
    columns.extend(
        [
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["variant_id"], ["variants.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["user_id"], ["studio_users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        ]
    )
    op.create_table(table, *columns)
    op.create_index(f"ix_{table}_variant_id", table, ["variant_id"])
    op.create_index(f"ix_{table}_created_at", table, ["created_at"])


def downgrade():
    for table in (
        "studio_price_history",
        "studio_inventory_history",
        "studio_audit",
        "studio_sessions",
        "studio_login_requests",
        "studio_users",
    ):
        op.drop_table(table)
    # PostgreSQL enum types outlive their table; SQLite ignores this branch.
    if op.get_bind().dialect.name == "postgresql":
        sa.Enum(name="studio_approval_status").drop(op.get_bind(), checkfirst=True)

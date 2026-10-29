"""admin sign-in by owner approval (ntfy) instead of an authenticator

Revision ID: 8e4b1c7d2a90
Revises: 6d2f0b9c4e17
Create Date: 2026-10-29 21:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "8e4b1c7d2a90"
down_revision = "6d2f0b9c4e17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column("uses_owner_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "admin_login_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("approval_token_hash", sa.String(length=64), nullable=False),
        sa.Column("match_code", sa.String(length=2), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["admin_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_token_hash"),
    )
    op.create_index(
        "ix_admin_login_requests_request_id", "admin_login_requests", ["request_id"], unique=True
    )
    op.create_index("ix_admin_login_requests_status", "admin_login_requests", ["status"])
    op.create_index("ix_admin_login_requests_expires_at", "admin_login_requests", ["expires_at"])


def downgrade() -> None:
    op.drop_table("admin_login_requests")
    op.drop_column("admin_users", "uses_owner_approval")

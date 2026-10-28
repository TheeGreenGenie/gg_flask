"""customer email verification

Revision ID: c35a8e42f914
Revises: b24e5d71c803
Create Date: 2026-10-27 23:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c35a8e42f914"
down_revision = "b24e5d71c803"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Accounts that existed before this feature were already actively used; do not lock them out.
    op.execute("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP")
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email_opt_in", sa.Boolean(), nullable=False),
        sa.Column("sms_opt_in", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_email_verifications_user_id", "email_verifications", ["user_id"]
    )
    op.create_index(
        "ix_email_verifications_expires_at", "email_verifications", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("email_verifications")
    op.drop_column("users", "email_verified_at")

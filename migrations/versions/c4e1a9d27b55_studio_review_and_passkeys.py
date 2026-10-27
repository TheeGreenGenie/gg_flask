"""Studio change review, scoped roles and owner-confirmed passkeys.

Owner, 2026-10-27: branding/maintenance/sales staff changes wait for the owner's emailed
approval; a co-owner may approve her own sign-ins with a phone passkey the owner confirmed.

Revision ID: c4e1a9d27b55
Revises: b09691a03912
Create Date: 2026-10-27

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e1a9d27b55"
down_revision = "b09691a03912"
branch_labels = None
depends_on = None

CHANGE_STATUS = ("pending", "applied", "rejected", "conflict", "failed")
ENROLLMENT_STATUS = ("pending", "approved", "denied", "registered", "expired")


def _enum(name: str, values: tuple[str, ...]) -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        enum = postgresql.ENUM(*values, name=name, create_type=False)
        enum.create(op.get_bind(), checkfirst=True)
        return enum
    return sa.Enum(*values, name=name)


def upgrade():
    with op.batch_alter_table("studio_users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("may_self_approve", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "studio_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("studio_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.String(80), nullable=False),
        sa.Column("view_args", sa.Text(), nullable=False),
        sa.Column("form_data", sa.Text(), nullable=False),
        sa.Column("upload", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("status", _enum("studio_change_status", CHANGE_STATUS), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(254), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_studio_changes_user_id", "studio_changes", ["user_id"])
    op.create_index("ix_studio_changes_status", "studio_changes", ["status"])
    op.create_index("ix_studio_changes_created_at", "studio_changes", ["created_at"])

    op.create_table(
        "studio_passkeys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("studio_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("credential_id", sa.String(512), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_studio_passkeys_user_id", "studio_passkeys", ["user_id"])

    op.create_table(
        "studio_key_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("studio_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "status", _enum("studio_key_enrollment_status", ENROLLMENT_STATUS), nullable=False
        ),
        sa.Column("device", sa.String(300), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_studio_key_enrollments_user_id", "studio_key_enrollments", ["user_id"])
    op.create_index("ix_studio_key_enrollments_status", "studio_key_enrollments", ["status"])


def downgrade():
    op.drop_table("studio_key_enrollments")
    op.drop_table("studio_passkeys")
    op.drop_table("studio_changes")
    if op.get_bind().dialect.name == "postgresql":
        postgresql.ENUM(name="studio_key_enrollment_status").drop(op.get_bind(), checkfirst=True)
        postgresql.ENUM(name="studio_change_status").drop(op.get_bind(), checkfirst=True)
    with op.batch_alter_table("studio_users", schema=None) as batch_op:
        batch_op.drop_column("may_self_approve")
        batch_op.drop_column("requires_review")

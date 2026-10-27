"""Studio text-message sign-in step.

Owner, 2026-10-27: the web developer account (tgg) needs the phone key AND a texted code on
every sign-in.

Revision ID: e5b7c2a19f40
Revises: d8a2f6c31e07
Create Date: 2026-10-27

"""

import sqlalchemy as sa
from alembic import op

revision = "e5b7c2a19f40"
down_revision = "d8a2f6c31e07"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("studio_users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("requires_sms", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("sms_phone", sa.String(20), nullable=True))
    with op.batch_alter_table("studio_login_requests", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sms_code_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("sms_sent_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("sms_sends", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("sms_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("sms_verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("studio_login_requests", schema=None) as batch_op:
        for column in (
            "sms_verified_at",
            "sms_attempts",
            "sms_sends",
            "sms_sent_at",
            "sms_code_hash",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("studio_users", schema=None) as batch_op:
        batch_op.drop_column("sms_phone")
        batch_op.drop_column("requires_sms")

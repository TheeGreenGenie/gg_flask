"""add product comments

Revision ID: a13f0c92e471
Revises: f7a10c3d62b4
Create Date: 2026-10-27 22:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "a13f0c92e471"
down_revision = "f7a10c3d62b4"
branch_labels = None
depends_on = None


def _replace_post_kind(values: tuple[str, ...], downgrade: bool = False) -> None:
    bind = op.get_bind()
    if downgrade:
        op.execute("UPDATE product_posts SET kind = 'question' WHERE kind = 'comment'")
    temporary = sa.Enum(*values, name="product_post_kind_replacement")
    temporary.create(bind, checkfirst=False)
    op.execute(
        "ALTER TABLE product_posts ALTER COLUMN kind TYPE "
        "product_post_kind_replacement USING kind::text::product_post_kind_replacement"
    )
    sa.Enum(name="product_post_kind").drop(bind, checkfirst=False)
    op.execute("ALTER TYPE product_post_kind_replacement RENAME TO product_post_kind")


def upgrade() -> None:
    _replace_post_kind(("review", "comment", "question"))


def downgrade() -> None:
    # Preserve old comments as questions when returning to a version without comment support.
    _replace_post_kind(("review", "question"), downgrade=True)

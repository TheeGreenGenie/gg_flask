"""reviews, questions, replies and moderation records

Revision ID: d9e44b6ac130
Revises: b5c2a71d004e
Create Date: 2026-10-27 06:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e44b6ac130"
down_revision = "b5c2a71d004e"
branch_labels = None
depends_on = None


def upgrade():
    kind = sa.Enum("review", "question", name="product_post_kind")
    status = sa.Enum("visible", "collapsed", "removed", "hidden", name="product_post_status")
    op.create_table(
        "product_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", kind, nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=False),
        sa.Column("moderation_reason", sa.String(length=120), nullable=True),
        sa.Column("staff_relevance_override", sa.Boolean(), nullable=True),
        sa.Column("verified_buyer", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)", name="post_rating_range"
        ),
        sa.CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 100", name="relevance_range"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("product_id", "user_id", "status", "created_at"):
        op.create_index(f"ix_product_posts_{column}", "product_posts", [column])
    op.create_table(
        "product_post_replies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("studio_user_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["product_posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["studio_user_id"], ["studio_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_post_replies_post_id", "product_post_replies", ["post_id"])
    op.create_table(
        "product_post_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=400), nullable=False),
        sa.Column("alt", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["product_posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )
    op.create_index("ix_product_post_photos_post_id", "product_post_photos", ["post_id"])
    op.create_table(
        "moderation_removals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=120), nullable=False),
        sa.Column("original_title", sa.String(length=160), nullable=False),
        sa.Column("original_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["product_posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id"),
    )
    for column in ("user_id", "created_at", "expires_at"):
        op.create_index(f"ix_moderation_removals_{column}", "moderation_removals", [column])
    op.create_table(
        "moderation_words",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("word", sa.String(length=100), nullable=False),
        sa.Column("is_topic", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["studio_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_moderation_words_language", "moderation_words", ["language"])


def downgrade():
    for table in (
        "moderation_words",
        "moderation_removals",
        "product_post_photos",
        "product_post_replies",
        "product_posts",
    ):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        sa.Enum(name="product_post_status").drop(op.get_bind(), checkfirst=True)
        sa.Enum(name="product_post_kind").drop(op.get_bind(), checkfirst=True)

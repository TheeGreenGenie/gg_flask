"""database-backed journal and original post migration

Revision ID: b5c2a71d004e
Revises: 6c88f824ea10
Create Date: 2026-10-27 05:45:00
"""

import json
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "b5c2a71d004e"
down_revision = "6c88f824ea10"
branch_labels = None
depends_on = None


def upgrade():
    blog_status = sa.Enum("draft", "scheduled", "published", name="blog_status")
    posts = op.create_table(
        "blog_posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("excerpt", sa.String(length=400), nullable=False),
        sa.Column("cover_path", sa.String(length=400), nullable=False),
        sa.Column("cover_alt", sa.String(length=255), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("status", blog_status, nullable=False),
        sa.Column("seo_title", sa.String(length=220), nullable=False),
        sa.Column("seo_description", sa.String(length=400), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["studio_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_blog_posts_slug", "blog_posts", ["slug"], unique=True)
    op.create_index("ix_blog_posts_status", "blog_posts", ["status"])
    op.create_index("ix_blog_posts_publish_at", "blog_posts", ["publish_at"])
    op.create_index("ix_blog_posts_published_at", "blog_posts", ["published_at"])
    op.create_table(
        "blog_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["blog_posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["studio_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_blog_revisions_post_id", "blog_revisions", ["post_id"])
    op.create_index("ix_blog_revisions_created_at", "blog_revisions", ["created_at"])

    # Keep this data migration tied to the retained legacy content source so clean
    # installations preserve the two original URLs and exact article content.
    from app.store.content import POSTS

    rows = []
    for item in POSTS:
        published = datetime.fromisoformat(str(item["date"])).replace(tzinfo=UTC)
        rows.append(
            {
                "slug": item["slug"],
                "title": item["title"],
                "category": item["category"],
                "excerpt": item["excerpt"],
                "cover_path": "static:" + str(item["image"]),
                "cover_alt": item["alt"],
                "body_json": json.dumps(_blocks(item["body"]), ensure_ascii=False),
                "status": "published",
                "seo_title": item["title"],
                "seo_description": item["excerpt"],
                "publish_at": published,
                "published_at": published,
                "created_by_id": None,
                "created_at": published,
                "updated_at": published,
            }
        )
    op.bulk_insert(posts, rows)


def _blocks(body):
    output = []
    for block in body:
        kind = block[0]
        if kind == "ul":
            output.append({"type": "ul", "items": list(block[1])})
        elif kind == "img":
            output.append(
                {
                    "type": "img",
                    "source": "static",
                    "path": block[1],
                    "alt": block[2],
                    "caption": block[3],
                }
            )
        else:
            output.append({"type": kind, "text": block[1]})
    return output


def downgrade():
    op.drop_table("blog_revisions")
    op.drop_table("blog_posts")
    if op.get_bind().dialect.name == "postgresql":
        sa.Enum(name="blog_status").drop(op.get_bind(), checkfirst=True)

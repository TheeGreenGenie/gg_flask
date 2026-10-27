"""Database-backed journal posts and immutable editorial revisions."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import utcnow
from app.extensions import db


class BlogStatus(enum.StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"


class BlogPost(db.Model):  # type: ignore[name-defined]
    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(220))
    category: Mapped[str] = mapped_column(String(100), default="Journal")
    excerpt: Mapped[str] = mapped_column(String(400))
    cover_path: Mapped[str] = mapped_column(String(400))
    cover_alt: Mapped[str] = mapped_column(String(255))
    body_json: Mapped[str] = mapped_column(Text)
    status: Mapped[BlogStatus] = mapped_column(
        Enum(
            BlogStatus, name="blog_status", values_callable=lambda values: [v.value for v in values]
        ),
        default=BlogStatus.DRAFT,
        index=True,
    )
    seo_title: Mapped[str] = mapped_column(String(220), default="")
    seo_description: Mapped[str] = mapped_column(String(400), default="")
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("studio_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    revisions: Mapped[list[BlogRevision]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )


class BlogRevision(db.Model):  # type: ignore[name-defined]
    __tablename__ = "blog_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("blog_posts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("studio_users.id", ondelete="SET NULL"))
    snapshot_json: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    post: Mapped[BlogPost] = relationship(back_populates="revisions")

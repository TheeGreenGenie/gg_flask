"""Customer reviews, product questions, replies, and moderation records."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import Product, utcnow
from app.extensions import db


class PostKind(enum.StrEnum):
    REVIEW = "review"
    QUESTION = "question"


class PostStatus(enum.StrEnum):
    VISIBLE = "visible"
    COLLAPSED = "collapsed"
    REMOVED = "removed"
    HIDDEN = "hidden"


class ProductPost(db.Model):  # type: ignore[name-defined]
    __tablename__ = "product_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[PostKind] = mapped_column(
        Enum(
            PostKind,
            name="product_post_kind",
            values_callable=lambda values: [v.value for v in values],
        )
    )
    status: Mapped[PostStatus] = mapped_column(
        Enum(
            PostStatus,
            name="product_post_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(12), default="und")
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    moderation_reason: Mapped[str | None] = mapped_column(String(120))
    staff_relevance_override: Mapped[bool | None] = mapped_column()
    verified_buyer: Mapped[bool] = mapped_column(default=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    product: Mapped[Product] = relationship()
    replies: Mapped[list[ProductPostReply]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="ProductPostReply.created_at"
    )
    photos: Mapped[list[ProductPostPhoto]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )
    removal: Mapped[ModerationRemoval | None] = relationship(
        back_populates="post", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)", name="post_rating_range"
        ),
        CheckConstraint("relevance_score >= 0 AND relevance_score <= 100", name="relevance_range"),
    )


class ProductPostReply(db.Model):  # type: ignore[name-defined]
    __tablename__ = "product_post_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("product_posts.id", ondelete="CASCADE"), index=True
    )
    studio_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("studio_users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped[ProductPost] = relationship(back_populates="replies")


class ProductPostPhoto(db.Model):  # type: ignore[name-defined]
    __tablename__ = "product_post_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("product_posts.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(400), unique=True)
    alt: Mapped[str] = mapped_column(String(255), default="Customer review photo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped[ProductPost] = relationship(back_populates="photos")


class ModerationRemoval(db.Model):  # type: ignore[name-defined]
    __tablename__ = "moderation_removals"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("product_posts.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str] = mapped_column(String(120))
    original_title: Mapped[str] = mapped_column(String(160), default="")
    original_body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    post: Mapped[ProductPost] = relationship(back_populates="removal")


class ModerationWord(db.Model):  # type: ignore[name-defined]
    __tablename__ = "moderation_words"

    id: Mapped[int] = mapped_column(primary_key=True)
    language: Mapped[str] = mapped_column(String(12), index=True)
    word: Mapped[str] = mapped_column(String(100))
    is_topic: Mapped[bool] = mapped_column(default=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("studio_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

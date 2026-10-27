"""Media library: one row per stored file.

The bytes live on the server's disk under MEDIA_ROOT (served at /media/...); PostgreSQL holds
the record of every file — where it is, its SHA-256 fingerprint, type, size, dimensions and,
for files brought over from WordPress, where they came from and what they were attached to.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog.models import utcnow
from app.extensions import db


class MediaAsset(db.Model):  # type: ignore[name-defined]
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(400), unique=True)  # relative to MEDIA_ROOT
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)  # image | video | svg | model
    mime: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    original_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    alt: Mapped[str] = mapped_column(String(300), default="")
    caption: Mapped[str] = mapped_column(Text, default="")

    # Provenance (WordPress import). Kept for editorial traceability only.
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_path: Mapped[str] = mapped_column(String(400))
    wp_attachment_id: Mapped[int | None] = mapped_column(Integer, index=True)
    wp_parent_type: Mapped[str | None] = mapped_column(String(40), index=True)
    wp_parent_id: Mapped[int | None] = mapped_column(Integer)
    wp_parent_slug: Mapped[str | None] = mapped_column(String(200))
    wp_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self) -> str:
        return f"<MediaAsset {self.path}>"

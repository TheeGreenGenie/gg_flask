"""Journal querying, safe block parsing, revisions, and scheduled publication."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from flask import current_app
from sqlalchemy import select

from app.content.models import BlogPost, BlogRevision, BlogStatus
from app.extensions import db
from app.store.content import POSTS as LEGACY_POSTS

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"^\[([^\]]{1,160})\]\((https?://[^\s)]+)\)$")
# A preview card row inside an article (owner, 2026-10-30): "[product: slug]",
# "[category: slug]" or "[collection: slug]" on a line of its own.
EMBED_RE = re.compile(r"^\[(product|category|collection):\s*([a-z0-9]+(?:-[a-z0-9]+)*)\]$")
EMBED_LIMIT = 8  # products shown for a category or collection
IMAGE_RE = re.compile(r'^!\[([^\]]{3,255})\]\((static:|media:)([^\s)]+)\)(?:\s+"([^"]{0,255})")?$')


class BlogError(ValueError):
    pass


@dataclass(frozen=True)
class BlogView:
    slug: str
    title: str
    category: str
    excerpt: str
    image: str
    alt: str
    body: tuple[Any, ...]
    date: str
    date_label: str
    image_is_media: bool = False


def parse_body(raw: str) -> list[dict[str, object]]:
    """Parse a tiny, escaped block language; raw HTML is always ordinary text."""
    blocks: list[dict[str, object]] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append({"type": "p", "text": " ".join(paragraph)})
            paragraph.clear()

    def flush_items() -> None:
        if items:
            blocks.append({"type": "ul", "items": list(items)})
            items.clear()

    for source_line in raw.replace("\r\n", "\n").split("\n"):
        line = source_line.strip()
        if not line:
            flush_paragraph()
            flush_items()
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_items()
            text = line[3:].strip()
            if text:
                blocks.append({"type": "h2", "text": text[:300]})
            continue
        if line.startswith("- "):
            flush_paragraph()
            items.append(line[2:].strip()[:1000])
            continue
        flush_items()
        embed = EMBED_RE.fullmatch(line)
        if embed:
            flush_paragraph()
            kind, slug = embed.group(1), embed.group(2)
            if _embed_target(kind, slug) is None:
                raise BlogError(f"There is no {kind} with the web name “{slug}”.")
            blocks.append({"type": "embed", "kind": kind, "slug": slug})
            continue
        link = LINK_RE.fullmatch(line)
        image = IMAGE_RE.fullmatch(line)
        if link:
            flush_paragraph()
            blocks.append({"type": "link", "text": link.group(1), "url": link.group(2)})
        elif image:
            flush_paragraph()
            blocks.append(
                {
                    "type": "img",
                    "alt": image.group(1),
                    "source": image.group(2)[:-1],
                    "path": image.group(3),
                    "caption": image.group(4) or "",
                }
            )
        else:
            paragraph.append(line[:5000])
    flush_paragraph()
    flush_items()
    if not blocks:
        raise BlogError("Add at least one paragraph to the article.")
    if len(json.dumps(blocks)) > 100_000:
        raise BlogError("The article is too long.")
    return blocks


def body_to_editor(blocks: list[dict[str, object]]) -> str:
    output: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "h2":
            output.append("## " + str(block.get("text", "")))
        elif kind == "ul":
            raw_items = block.get("items", [])
            if isinstance(raw_items, list):
                output.extend("- " + str(item) for item in raw_items)
        elif kind == "link":
            output.append(f"[{block.get('text', '')}]({block.get('url', '')})")
        elif kind == "embed":
            output.append(f"[{block.get('kind', '')}: {block.get('slug', '')}]")
        elif kind == "img":
            caption = str(block.get("caption", ""))
            suffix = f' "{caption}"' if caption else ""
            alt = block.get("alt", "")
            source = block.get("source", "media")
            path = block.get("path", "")
            output.append(f"![{alt}]({source}:{path}){suffix}")
        else:
            output.append(str(block.get("text", "")))
        output.append("")
    return "\n".join(output).strip()


def public_posts() -> list[BlogView]:
    try:
        records = db.session.scalars(
            select(BlogPost)
            .where(BlogPost.status == BlogStatus.PUBLISHED)
            .order_by(BlogPost.published_at.desc(), BlogPost.id.desc())
        ).all()
    except Exception:
        db.session.rollback()
        records = []
    return [_view(record) for record in records] or [
        _legacy_view(cast(Mapping[str, Any], item)) for item in LEGACY_POSTS
    ]


def public_post(slug: str) -> tuple[BlogView | None, BlogView | None, BlogView | None]:
    ordered = list(reversed(public_posts()))
    index = next((i for i, post in enumerate(ordered) if post.slug == slug), None)
    if index is None:
        return None, None, None
    return (
        ordered[index],
        ordered[index - 1] if index > 0 else None,
        ordered[index + 1] if index + 1 < len(ordered) else None,
    )


def validate_cover(path_raw: str) -> str:
    path = path_raw.strip().replace("\\", "/")
    if path.startswith("static:"):
        relative = path[7:]
        root = Path(current_app.static_folder or "").resolve()
    elif path.startswith("media:"):
        relative = path[6:]
        root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    else:
        raise BlogError("Cover path must begin with static: or media:.")
    target = (root / relative).resolve()
    if root not in target.parents or not target.is_file():
        raise BlogError("Choose an existing cover image from the static or media library.")
    return path


def sync_publication(now: datetime | None = None) -> tuple[int, int]:
    from app.catalog.models import utcnow

    now = now or utcnow()
    published = db.session.scalars(
        select(BlogPost).where(BlogPost.status == BlogStatus.SCHEDULED, BlogPost.publish_at <= now)
    ).all()
    for post in published:
        post.status = BlogStatus.PUBLISHED
        post.published_at = post.publish_at or now
    db.session.commit()
    return len(published), 0


def view_for_preview(post: BlogPost) -> BlogView:
    """The public rendering of any article, published or not (Studio preview only)."""
    return _view(post)


def _view(post: BlogPost) -> BlogView:
    blocks = json.loads(post.body_json)
    date = (post.published_at or post.publish_at or post.created_at).date().isoformat()
    return BlogView(
        slug=post.slug,
        title=post.title,
        category=post.category,
        excerpt=post.excerpt,
        image=post.cover_path.split(":", 1)[1],
        alt=post.cover_alt,
        body=_template_blocks(blocks),
        date=date,
        date_label=datetime.fromisoformat(date).strftime("%B %d, %Y").replace(" 0", " "),
        image_is_media=post.cover_path.startswith("media:"),
    )


def _legacy_view(item: Mapping[str, Any]) -> BlogView:
    return BlogView(
        slug=str(item["slug"]),
        title=str(item["title"]),
        category=str(item["category"]),
        excerpt=str(item["excerpt"]),
        image=str(item["image"]),
        alt=str(item["alt"]),
        body=tuple(item["body"]),
        date=str(item["date"]),
        date_label=str(item["date_label"]),
    )


def _template_blocks(blocks: list[dict[str, object]]) -> tuple:
    output = []
    for block in blocks:
        kind = block.get("type")
        if kind == "ul":
            output.append(("ul", block.get("items", []), "", ""))
        elif kind == "img":
            prefix = "media:" if block.get("source") == "media" else "static:"
            output.append(
                (
                    "img",
                    prefix + str(block.get("path", "")),
                    str(block.get("alt", "")),
                    str(block.get("caption", "")),
                )
            )
        elif kind == "link":
            output.append(("link", str(block.get("text", "")), str(block.get("url", "")), ""))
        elif kind == "embed":
            output.append(("embed", str(block.get("kind", "")), str(block.get("slug", "")), ""))
        else:
            output.append((str(kind), str(block.get("text", "")), "", ""))
    return tuple(output)


def _snapshot(post: BlogPost) -> str:
    return json.dumps(
        {
            "title": post.title,
            "category": post.category,
            "excerpt": post.excerpt,
            "cover_path": post.cover_path,
            "cover_alt": post.cover_alt,
            "body_json": post.body_json,
            "seo_title": post.seo_title,
            "seo_description": post.seo_description,
            "status": post.status.value,
            "publish_at": post.publish_at.isoformat() if post.publish_at else None,
            "published_at": post.published_at.isoformat() if post.published_at else None,
        },
        sort_keys=True,
    )


def record_revision(post: BlogPost, user_id: int, reason: str) -> None:
    if post.id:
        db.session.add(
            BlogRevision(
                post_id=post.id,
                user_id=user_id,
                snapshot_json=_snapshot(post),
                reason=reason[:120],
            )
        )


def safe_external_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _embed_target(kind: str, slug: str) -> Any:
    from app.catalog import queries
    from app.catalog.models import Category, Collection, Product

    if kind == "collection" and slug in queries.VIRTUAL_COLLECTIONS:
        return queries.VIRTUAL_COLLECTIONS[slug]
    model: Any = {"product": Product, "category": Category, "collection": Collection}[kind]
    return db.session.scalar(select(model).where(model.slug == slug))


@dataclass(frozen=True)
class Embed:
    title: str
    url: str
    products: list[Any]


def embed(kind: str, slug: str) -> Embed | None:
    """Live products for a preview row, read when the article is shown (so prices, photos and
    stock are always current). Hidden products never appear; an empty row is skipped."""
    from flask import url_for

    from app.catalog import queries

    if kind == "product":
        product = queries.product_by_slug(slug)
        if product is None:
            return None
        return Embed("", url_for("store.product", slug=slug), [product])
    if kind == "category":
        category = queries.category_by_slug(slug)
        if category is None:
            return None
        items = queries.listing(categories=category.subtree()).items[:EMBED_LIMIT]
        return Embed(category.name, url_for("store.category", slug=slug), items) if items else None
    if kind == "collection":
        if slug in queries.VIRTUAL_COLLECTIONS:  # New Arrivals, Sale
            title = queries.VIRTUAL_COLLECTIONS[slug][0]
        else:
            collection = queries.collection_by_slug(slug)  # None unless live
            if collection is None:
                return None
            title = collection.name
        items = queries.listing(collection=slug).items[:EMBED_LIMIT]
        url = url_for("store.collection", slug=slug)
        return Embed(title, url, items) if items else None
    return None

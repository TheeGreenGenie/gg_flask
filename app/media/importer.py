"""Bring files from the old WordPress site into the media library.

Safety rules:
- Private/operational folders (order logs, invoices, contact-form uploads, transient files,
  plugin logs, generated CSS/fonts) are never read.
- Only real media is accepted, identified by file content (magic bytes), not extension:
  JPEG, PNG, GIF, WebP, MP4, SVG and GLB. Images must decode and stay under a pixel limit.
  SVGs containing scripts, event handlers or javascript: URLs are rejected.
- WordPress's auto-generated size copies (name-300x200.jpg) are skipped when the original is
  present; the new site makes its own sizes.
- Re-running is safe: files are identified by SHA-256, so nothing is copied or recorded twice.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image
from sqlalchemy import select

from app.extensions import db
from app.media.models import MediaAsset

PRIVATE_DIRS = frozenset(
    {
        "wc-logs",
        "cartflows-logs",
        "woocommerce_transient_files",
        "woocommerce_uploads",
        "wpcf7_uploads",
        "print-invoices-packing-slip-labels-for-woocommerce",
        "mailchimp-for-wp",
        "elementor",  # generated CSS
        "fonts",  # cached third-party fonts; the new site self-hosts its own
    }
)
SIZE_COPY_RE = re.compile(r"^(?P<stem>.+)-\d+x\d+(?P<ext>\.[A-Za-z0-9]+)$")
SCALED_RE = re.compile(r"^(?P<stem>.+)-(?:scaled|rotated)(?P<ext>\.[A-Za-z0-9]+)$")
MAX_PIXELS = 60_000_000
logging.getLogger("PIL").setLevel(logging.INFO)  # its DEBUG output floods the import log
MAX_BYTES = 300 * 1024 * 1024
SVG_DANGER = re.compile(rb"<script|\son[a-z]+\s*=|javascript:|<foreignObject", re.I)
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class ImportReport:
    imported: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=dict)
    bytes_copied: int = 0

    def skip(self, reason: str, path: str) -> None:
        self.skipped[reason] += 1
        self.examples.setdefault(reason, [])
        if len(self.examples[reason]) < 3:
            self.examples[reason].append(path)


@dataclass(frozen=True)
class WPMeta:
    attachment_id: int
    title: str
    caption: str
    alt: str
    parent_type: str | None
    parent_id: int | None
    parent_slug: str | None
    uploaded_at: datetime | None


def sniff(head: bytes) -> tuple[str, str] | None:
    """(kind, mime) from the first bytes of a file, or None if it is not allowed media."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if head[4:8] == b"ftyp":
        return "video", "video/mp4"
    if head[:4] == b"glTF":
        return "model", "model/gltf-binary"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith(b"<svg") or (text.startswith(b"<?xml") and b"<svg" in head.lower()):
        return "svg", "image/svg+xml"
    return None


def wordpress_metadata(mysql_url: str) -> dict[str, WPMeta]:
    """Attachment metadata keyed by uploads-relative path (read-only query)."""
    from urllib.parse import unquote, urlsplit

    import pymysql  # dev/tools dependency, only needed for the import

    url = urlsplit(mysql_url)
    conn = pymysql.connect(
        host=url.hostname or "localhost",
        port=url.port or 3306,
        user=unquote(url.username or "root"),
        password=unquote(url.password or ""),
        database=url.path.lstrip("/"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    prefix = "wp_"
    sql = f"""
        SELECT a.ID, a.post_title, a.post_excerpt, a.post_date_gmt,
               f.meta_value AS file, alt.meta_value AS alt,
               par.ID AS parent_id, par.post_type AS parent_type, par.post_name AS parent_slug
        FROM {prefix}posts a
        JOIN {prefix}postmeta f ON f.post_id = a.ID AND f.meta_key = '_wp_attached_file'
        LEFT JOIN {prefix}postmeta alt
               ON alt.post_id = a.ID AND alt.meta_key = '_wp_attachment_image_alt'
        LEFT JOIN {prefix}posts par ON par.ID = a.post_parent
        WHERE a.post_type = 'attachment'
    """  # noqa: S608 - fixed table prefix, no user input
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()
    out: dict[str, WPMeta] = {}
    for r in rows:
        uploaded = r["post_date_gmt"]
        meta = WPMeta(
            attachment_id=int(r["ID"]),
            title=(r["post_title"] or "")[:255],
            caption=r["post_excerpt"] or "",
            alt=(r["alt"] or "")[:300],
            parent_type=r["parent_type"],
            parent_id=r["parent_id"],
            parent_slug=r["parent_slug"],
            uploaded_at=uploaded.replace(tzinfo=UTC)
            if isinstance(uploaded, datetime) and uploaded.year > 1970
            else None,
        )
        rel = str(r["file"])
        out[rel] = meta
        # WordPress records the "-scaled" copy; let the untouched original match too.
        m = SCALED_RE.match(PurePosixPath(rel).name)
        if m:
            out[str(PurePosixPath(rel).with_name(m["stem"] + m["ext"]))] = meta
    return out


def candidate_files(root: Path, report: ImportReport) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in PRIVATE_DIRS:
            report.skip("private or generated folder", rel.as_posix())
            continue
        if (
            path.suffix.lower() in {".php", ".htaccess", ".html", ".log", ".csv", ".css"}
            or path.name == ".htaccess"
        ):
            report.skip("not media", rel.as_posix())
            continue
        size_copy = SIZE_COPY_RE.match(path.name)
        if size_copy and (path.parent / (size_copy["stem"] + size_copy["ext"])).exists():
            report.skip("WordPress size copy", rel.as_posix())
            continue
        if size_copy and any(
            (path.parent / f"{size_copy['stem']}-{tag}{size_copy['ext']}").exists()
            for tag in ("scaled", "rotated")
        ):
            report.skip("WordPress size copy", rel.as_posix())
            continue
        scaled = SCALED_RE.match(path.name)
        if scaled and (path.parent / (scaled["stem"] + scaled["ext"])).exists():
            report.skip("WordPress scaled copy (original kept)", rel.as_posix())
            continue
        files.append(path)
    return files


def import_tree(
    root: Path,
    media_root: Path,
    *,
    source: str,
    dest_prefix: str,
    wp_meta: dict[str, WPMeta] | None = None,
    dry_run: bool = False,
    report: ImportReport | None = None,
) -> ImportReport:
    report = report or ImportReport()
    known = set(db.session.scalars(select(MediaAsset.sha256)))
    taken = set(db.session.scalars(select(MediaAsset.path)))
    batch = 0
    for path in candidate_files(root, report):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size == 0 or size > MAX_BYTES:
            report.skip("empty or too large", rel)
            continue
        with path.open("rb") as fh:
            head = fh.read(512)
        sniffed = sniff(head)
        if sniffed is None:
            report.skip("unrecognised file type", rel)
            continue
        kind, mime = sniffed
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in known:
            report.skip("duplicate (already imported)", rel)
            continue
        width = height = None
        if kind == "image":
            try:
                with Image.open(path) as im:
                    width, height = im.size
                    if width * height > MAX_PIXELS:
                        report.skip("image too large (pixels)", rel)
                        continue
                    im.verify()
            except Exception:  # noqa: BLE001 - any decode failure means "not a usable image"
                report.skip("corrupt image", rel)
                continue
        elif kind == "svg" and SVG_DANGER.search(data):
            report.skip("unsafe SVG (scripts or handlers)", rel)
            continue

        dest_rel = _unique(f"{dest_prefix}/{_safe_rel(rel)}", taken)
        meta = (wp_meta or {}).get(rel)
        if not dry_run:
            dest = media_root / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            db.session.add(
                MediaAsset(
                    path=dest_rel,
                    sha256=digest,
                    kind=kind,
                    mime=mime,
                    size_bytes=size,
                    width=width,
                    height=height,
                    original_name=path.name[:255],
                    title=meta.title if meta else "",
                    alt=meta.alt if meta else "",
                    caption=meta.caption if meta else "",
                    source=source,
                    source_path=rel[:400],
                    wp_attachment_id=meta.attachment_id if meta else None,
                    wp_parent_type=meta.parent_type if meta else None,
                    wp_parent_id=meta.parent_id if meta else None,
                    wp_parent_slug=meta.parent_slug if meta else None,
                    wp_uploaded_at=meta.uploaded_at if meta else None,
                )
            )
            batch += 1
            if batch >= 100:
                db.session.commit()
                batch = 0
        known.add(digest)
        taken.add(dest_rel)
        report.imported[kind] += 1
        report.bytes_copied += size
    if not dry_run:
        db.session.commit()
    return report


def verify(media_root: Path) -> dict[str, list[str]]:
    """Check every recorded file exists on disk with the recorded fingerprint."""
    problems: dict[str, list[str]] = {"missing": [], "changed": []}
    for asset in db.session.scalars(select(MediaAsset).order_by(MediaAsset.id)):
        file = media_root / asset.path
        if not file.is_file():
            problems["missing"].append(asset.path)
        elif hashlib.sha256(file.read_bytes()).hexdigest() != asset.sha256:
            problems["changed"].append(asset.path)
    return problems


def _safe_rel(rel: str) -> str:
    *dirs, name = PurePosixPath(rel).parts
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    clean = [SAFE_NAME.sub("-", d).strip("-.") or "dir" for d in dirs]
    stem = SAFE_NAME.sub("-", stem).strip("-.") or "file"
    ext = SAFE_NAME.sub("", ext).lower()
    return "/".join([*clean, f"{stem}.{ext}" if ext else stem])


def _unique(candidate: str, taken: set[str]) -> str:
    if candidate not in taken:
        return candidate
    p = PurePosixPath(candidate)
    n = 2
    while (alt := str(p.with_name(f"{p.stem}-{n}{p.suffix}"))) in taken:
        n += 1
    return alt


def summary(report: ImportReport) -> dict[str, Any]:
    return {
        "imported": dict(report.imported),
        "skipped": dict(report.skipped),
        "megabytes": round(report.bytes_copied / 1_048_576, 1),
    }

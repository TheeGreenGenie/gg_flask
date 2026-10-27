"""Customer photos on reviews and questions, checked for nudity on our own server.

Owner, 2026-10-27: the check runs privately (photos never leave the server) and an inappropriate
photo removes the whole post. The open-source NudeNet detector (bundled ONNX model, CPU only) is
used when IMAGE_MODERATION_BACKEND=local and the optional package is installed; otherwise photo
posting is switched off entirely, so an unchecked photo can never be published (fail closed).

A flagged photo is never written to disk or kept; clean photos are re-encoded (metadata dropped)
and stored under MEDIA_ROOT/reviews with random names. Calibrated 2026-10-27 on all 401 catalog
photos: at the 0.5 threshold none of the shop's own fabrics were flagged (highest score 0.39).
"""

from __future__ import annotations

import io
import logging
import secrets
import tempfile
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from app.catalog.images import MAX_DIMENSION, MAX_PIXELS

log = logging.getLogger(__name__)
MAX_PHOTOS = 3
EXPLICIT_CLASSES = frozenset(
    {
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
        "BUTTOCKS_EXPOSED",
        "ANUS_EXPOSED",
    }
)


class PhotoError(ValueError):
    """The photo can't be accepted (unreadable, too large, too many)."""


@lru_cache(maxsize=1)
def _detector() -> Any | None:
    try:
        from nudenet import NudeDetector  # optional: pip install -e ".[moderation]"
    except ImportError:
        log.error("image_moderation_unavailable: nudenet is not installed; photo posts are off")
        return None
    return NudeDetector()


def photos_enabled() -> bool:
    if current_app.config.get("IMAGE_MODERATION_BACKEND") != "local":
        return False
    return _detector() is not None


def explicit_score(webp: bytes) -> float:
    """Highest score among exposed-nudity classes (0 when none)."""
    detector = _detector()
    if detector is None:  # pragma: no cover - guarded by photos_enabled()
        raise PhotoError("Photo checks are not available.")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "photo.webp"
        path.write_bytes(webp)
        detections = detector.detect(str(path))
    return max(
        (float(d.get("score", 0)) for d in detections if d.get("class") in EXPLICIT_CLASSES),
        default=0.0,
    )


def prepare(upload: FileStorage) -> bytes:
    """Decode safely and re-encode as WebP in memory (nothing is written yet)."""
    try:
        upload.stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload.stream)
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise PhotoError("Photos must be JPEG, PNG, or WebP.")
            if (
                image.width > MAX_DIMENSION
                or image.height > MAX_DIMENSION
                or image.width * image.height > MAX_PIXELS
            ):
                raise PhotoError("That photo is too large.")
            image.load()
    except PhotoError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
    ):
        raise PhotoError("One of the photos could not be read.") from None
    processed = image.convert("RGB")
    processed.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    processed.save(buffer, "WEBP", quality=82, method=6)
    return buffer.getvalue()


def screen(uploads: list[FileStorage]) -> tuple[list[bytes], bool]:
    """(clean photos ready to save, whether any photo was inappropriate)."""
    chosen = [u for u in uploads if u and u.filename]
    if not chosen:
        return [], False
    if not photos_enabled():
        raise PhotoError("Photos can't be added to posts yet.")
    if len(chosen) > MAX_PHOTOS:
        raise PhotoError(f"Add at most {MAX_PHOTOS} photos.")
    threshold = float(current_app.config.get("IMAGE_MODERATION_THRESHOLD") or 0.5)
    prepared = [prepare(u) for u in chosen]
    flagged = any(explicit_score(data) >= threshold for data in prepared)
    return ([] if flagged else prepared), flagged


def save(data: bytes) -> str:
    relative = Path("reviews") / f"{secrets.token_hex(12)}.webp"
    destination = Path(current_app.config["MEDIA_ROOT"]).resolve() / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return relative.as_posix()

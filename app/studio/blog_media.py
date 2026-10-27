"""Photos uploaded from the Studio blog editor (cover and in-article images).

Same safety rules as product photos (app/catalog/images.py): decoded with Pillow under pixel and
dimension limits, metadata dropped, re-encoded as WebP under a random name in the media library.
Returns the `media:` reference the blog editor and renderer already understand.
"""

from __future__ import annotations

import secrets
import warnings
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from app.catalog.images import MAX_DIMENSION, MAX_PIXELS


class BlogImageError(ValueError):
    pass


def save_image(upload: FileStorage) -> str:
    try:
        upload.stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload.stream)
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise BlogImageError("Upload a JPEG, PNG, or WebP photo.")
            if (
                image.width > MAX_DIMENSION
                or image.height > MAX_DIMENSION
                or image.width * image.height > MAX_PIXELS
            ):
                raise BlogImageError("That photo's dimensions are too large.")
            image.load()
    except BlogImageError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
    ):
        raise BlogImageError("The uploaded file is not a safe, readable photo.") from None
    processed = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    processed.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    relative = Path("blog") / f"{secrets.token_hex(12)}.webp"
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    processed.save(destination, "WEBP", quality=84, method=6)
    return f"media:{relative.as_posix()}"

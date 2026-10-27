"""Shared safe product-image decoding, storage, and deletion primitives."""

from __future__ import annotations

import secrets
import warnings
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app.catalog.models import Product, ProductImage, Variant
from app.extensions import db

MAX_PIXELS = 36_000_000
MAX_DIMENSION = 6_000


class ImageError(ValueError):
    pass


def prepare_upload(
    product: Product, upload: FileStorage | None, alt_raw: str
) -> tuple[ProductImage, Path]:
    """Validate and write a randomized WebP; caller owns transaction and cleanup."""
    alt = alt_raw.strip()
    if not 3 <= len(alt) <= 255:
        raise ImageError("Alternative text must be between 3 and 255 characters.")
    if upload is None or not upload.filename:
        raise ImageError("Choose an image to upload.")
    try:
        upload.stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload.stream)
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ImageError("Upload a JPEG, PNG, or WebP image.")
            if (
                image.width > MAX_DIMENSION
                or image.height > MAX_DIMENSION
                or image.width * image.height > MAX_PIXELS
            ):
                raise ImageError("Image dimensions are too large.")
            image.load()
    except ImageError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
    ):
        raise ImageError("The uploaded file is not a safe, readable image.") from None
    processed = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    processed.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    relative = Path("products") / product.slug / "uploads" / f"{secrets.token_hex(12)}.webp"
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    destination = (root / relative).resolve()
    if root not in destination.parents:
        raise ImageError("Unsafe image destination.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    processed.save(destination, "WEBP", quality=88, method=6)
    record = ProductImage(
        product=product, path=relative.as_posix(), alt=alt, position=len(product.images)
    )
    db.session.add(record)
    return record, destination


def ensure_deletable(image: ProductImage) -> None:
    if db.session.scalar(select(Variant.id).where(Variant.image_id == image.id)) is not None:
        raise ImageError("This image is assigned to a product option and cannot be removed.")


def disk_path(relative_path: str) -> Path | None:
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    target = (root / relative_path).resolve()
    return target if root in target.parents else None

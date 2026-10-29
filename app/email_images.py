"""Product photos for emails, as JPEG.

Product photos are stored as WebP, which Outlook and some Gmail versions do not display. Emails
link here instead: the first request converts the photo to a small JPEG, cached next to the media
(MEDIA_ROOT/_email/...), and later requests serve the cached copy. Only image files inside
MEDIA_ROOT can be read.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Flask, abort, current_app, send_file
from PIL import Image, ImageOps
from werkzeug.wrappers import Response

bp = Blueprint("email_images", __name__)

SOURCE_TYPES = {".webp", ".png", ".jpg", ".jpeg"}
MAX_SIDE = 480  # shown at 80-120 px in emails; 480 stays sharp on high-density phone screens
YEAR = 365 * 86400


@bp.get("/email-images/<path:path>")
def image(path: str) -> Response:
    root = Path(current_app.config["MEDIA_ROOT"]).resolve()
    source = (root / path).resolve()
    if (
        root not in source.parents
        or source.suffix.lower() not in SOURCE_TYPES
        or "_email" in source.relative_to(root).parts
        or not source.is_file()
    ):
        abort(404)
    cached = (root / "_email" / source.relative_to(root)).with_suffix(".jpg")
    if not cached.exists() or cached.stat().st_mtime < source.stat().st_mtime:
        cached.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as original:
            picture = ImageOps.exif_transpose(original).convert("RGB")
            picture.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            partial = cached.with_suffix(".part")
            picture.save(partial, "JPEG", quality=82, optimize=True, progressive=True)
            partial.replace(cached)  # never serve a half-written file
    response = send_file(cached, mimetype="image/jpeg", max_age=YEAR)
    response.cache_control.public = True
    return response


def init_email_images(app: Flask) -> None:
    app.register_blueprint(bp)

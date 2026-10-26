"""Site-level routes that are not part of the storefront."""

from __future__ import annotations

from flask import Blueprint, current_app, send_from_directory
from werkzeug.wrappers import Response

bp = Blueprint("main", __name__)


@bp.get("/favicon.ico")
def favicon() -> Response:
    """Browsers request /favicon.ico directly regardless of <link rel=icon>."""
    return send_from_directory(current_app.static_folder or "static", "favicon.ico", max_age=86400)

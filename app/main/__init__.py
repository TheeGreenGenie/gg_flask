"""Storefront placeholder until the design system lands in Phase 2."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, send_from_directory
from werkzeug.wrappers import Response

bp = Blueprint("main", __name__)


@bp.get("/")
def home() -> str:
    return render_template("home.html")


@bp.get("/favicon.ico")
def favicon() -> Response:
    """Browsers request /favicon.ico directly regardless of <link rel=icon>."""
    return send_from_directory(current_app.static_folder or "static", "favicon.ico", max_age=86400)

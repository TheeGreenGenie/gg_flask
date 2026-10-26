"""Storefront placeholder until the design system lands in Phase 2."""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("main", __name__)


@bp.get("/")
def home() -> str:
    return render_template("home.html")

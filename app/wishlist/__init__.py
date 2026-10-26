"""Wishlist pages, actions and the JSON endpoint used by Find Fabrics."""

from __future__ import annotations

from typing import Any

import click
from flask import (
    Blueprint,
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from app.wishlist import service

bp = Blueprint("wishlist", __name__)


@bp.app_context_processor
def wishlist_globals() -> dict[str, Any]:
    saved = service.slugs(service.current())
    return {"wishlist_slugs": saved, "wishlist_count": len(saved)}


@bp.after_app_request
def _wishlist_cookie(response: Response) -> Response:
    return service.set_cookie(response)


def _wants_json() -> bool:
    return request.accept_mimetypes.best == "application/json"


def _reply(ok: bool, message: str, saved: bool | None = None) -> Response | tuple[Response, int]:
    if _wants_json():
        body: dict[str, Any] = {
            "ok": ok,
            "message": message,
            "count": len(service.slugs(service.current())),
        }
        if saved is not None:
            body["saved"] = saved
        return jsonify(body), (200 if ok else 400)
    flash(message, "success" if ok else "error")
    target = request.referrer or ""
    # Only bounce back to our own pages (no open redirect).
    if not target.startswith(request.host_url):
        target = url_for("wishlist.page")
    return redirect(target)


@bp.get("/wishlist/")
def page() -> str:
    return render_template(
        "wishlist/page.html",
        products=service.products(service.current()),
        retention_days=service.RETENTION_DAYS,
    )


@bp.post("/wishlist/toggle")
def toggle() -> Response | tuple[Response, int]:
    slug = request.form.get("product", "")
    try:
        if request.form.get("action") == "remove":
            service.remove(slug)
            return _reply(True, "Removed from your wishlist.", saved=False)
        added = service.add(slug)
        return _reply(
            True, "Saved to your wishlist." if added else "Already in your wishlist.", saved=True
        )
    except service.WishlistError as exc:
        return _reply(False, str(exc))


@bp.post("/wishlist/clear")
def clear() -> Response | tuple[Response, int]:
    try:
        service.clear()
    except service.WishlistError as exc:
        return _reply(False, str(exc))
    return _reply(True, "Your wishlist is empty.")


@bp.get("/api/v1/wishlist")
def api_list() -> Response:
    from app.store import card_json

    items = [card_json(p) for p in service.products(service.current())]
    resp = jsonify(items=items, count=len(items))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def init_wishlist(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.cli.command("wishlist-prune")
    @click.option("--days", default=service.RETENTION_DAYS, show_default=True)
    def prune_cmd(days: int) -> None:
        """Delete wishlists nobody has changed for DAYS days (run daily)."""
        click.echo(f"Removed {service.prune(days)} idle wishlist(s).")

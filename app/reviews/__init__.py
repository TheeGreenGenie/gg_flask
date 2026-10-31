"""Storefront review, comment, and question routes and maintenance commands."""

from __future__ import annotations

import click
from flask import Blueprint, Flask, abort, flash, redirect, request, url_for
from werkzeug.wrappers import Response

from app.accounts import service as account_service
from app.catalog import queries
from app.reviews import service

bp = Blueprint("reviews", __name__, url_prefix="/products")


@bp.post("/<slug>/posts")
def create(slug: str) -> Response:
    product = queries.product_by_slug(slug)
    if product is None:
        return redirect(url_for("store.shop"))
    user = account_service.current_user()
    if user is None:
        flash("Sign in before posting a review, comment, or question.", "error")
        return redirect(url_for("accounts.login", next=url_for("store.product", slug=slug)))
    try:
        post = service.create_post(
            user, product, request.form.to_dict(), request.files.getlist("photos")
        )
    except service.ReviewError as exc:
        flash(str(exc), "error")
    else:
        flash(f"Your {post.kind.value} was posted.", "success")
    return redirect(url_for("store.product", slug=slug) + "#reviews")


@bp.post("/posts/<int:post_id>/remove")
def remove(post_id: int) -> Response:
    """Studio and admin accounts take a post off the product page (kept privately, restorable)."""
    from app.extensions import db
    from app.reviews.models import ProductPost

    remover = service.staff_remover()
    if remover is None:
        abort(403)
    post = db.get_or_404(ProductPost, post_id)
    slug = post.product.slug
    try:
        service.remove_post(post, request.form.get("reason", ""), remover)
    except service.ReviewError as exc:
        flash(str(exc), "error")
    else:
        flash("Removed from the website. It can be restored in the Studio if needed.", "success")
    return redirect(url_for("store.product", slug=slug) + "#reviews")


def init_reviews(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.cli.group("reviews")
    def reviews_cli() -> None:
        """Review moderation maintenance."""

    @reviews_cli.command("purge-removed")
    def purge_removed() -> None:
        """Permanently discard expired private removed-text records."""
        click.echo(f"Purged {service.purge_expired()} removed post record(s).")

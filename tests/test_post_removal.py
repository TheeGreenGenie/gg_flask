"""Studio and admin accounts remove reviews/comments/questions, also from the product page
(owner, 2026-10-31). Customers never can. Removed posts are kept privately and restorable."""

from __future__ import annotations

import json

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app.admin.models import AdminAudit, AdminRole
from app.extensions import db
from app.reviews import service as reviews
from app.reviews.models import PostStatus, ProductPost
from app.studio.models import StudioAudit
from tests.test_admin import admin_client
from tests.test_reviews import _customer, _form, _product
from tests.test_studio_review import PREFIX, _staff_client


def _post(app: Flask) -> tuple[int, str]:
    with app.test_request_context("/", headers={"User-Agent": "removal-test"}):
        product = _product()
        post = reviews.create_post(_customer(), product, _form())
        assert post.status == PostStatus.VISIBLE
        return post.id, product.slug


def _status(app: Flask, post_id: int) -> PostStatus:
    with app.app_context():
        post = db.session.get(ProductPost, post_id)
        assert post is not None
        return post.status


def test_customers_and_visitors_cannot_remove_posts(catalog_app: Flask) -> None:
    post_id, slug = _post(catalog_app)
    visitor = catalog_app.test_client()
    page = visitor.get(f"/product/{slug}/").get_data(as_text=True)
    assert "Staff only" not in page and "/remove" not in page
    refused = visitor.post(f"/products/posts/{post_id}/remove", data={"reason": "spam"})
    assert refused.status_code == 403
    assert _status(catalog_app, post_id) == PostStatus.VISIBLE


def test_studio_staff_remove_from_the_product_page_with_a_reason(catalog_app: Flask) -> None:
    catalog_app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    post_id, slug = _post(catalog_app)
    staff: FlaskClient = _staff_client(catalog_app, "branding")  # review mode on
    cookie = catalog_app.config["STUDIO_COOKIE_NAME"]
    assert staff.get_cookie(cookie, path="/") is not None  # sign-in now works site-wide
    page = staff.get(f"/product/{slug}/").get_data(as_text=True)
    assert "Staff only" in page and "Spam or advertising" in page
    no_reason = staff.post(f"/products/posts/{post_id}/remove", follow_redirects=True)
    assert "Choose why" in no_reason.get_data(as_text=True)
    assert _status(catalog_app, post_id) == PostStatus.VISIBLE
    staff.post(f"/products/posts/{post_id}/remove", data={"reason": "spam"})
    assert _status(catalog_app, post_id) == PostStatus.HIDDEN  # at once, not held for review
    public = catalog_app.test_client().get(f"/product/{slug}/").get_data(as_text=True)
    assert "Beautiful fabric" not in public
    with catalog_app.app_context():
        entry = db.session.scalar(
            select(StudioAudit).where(StudioAudit.action == "studio.community.remove")
        )
        assert entry is not None and json.loads(entry.detail)["reason"] == "spam"
    # Restorable from the Studio by the same (reviewed) staff member, immediately.
    staff.post(PREFIX + f"/community/{post_id}/moderate", data={"action": "restore"})
    assert _status(catalog_app, post_id) == PostStatus.VISIBLE


def test_admin_accounts_remove_from_the_product_page(catalog_app: Flask) -> None:
    post_id, slug = _post(catalog_app)
    support = admin_client(catalog_app, AdminRole.SUPPORT)
    assert "Staff only" in support.get(f"/product/{slug}/").get_data(as_text=True)
    support.post(f"/products/posts/{post_id}/remove", data={"reason": "private"})
    assert _status(catalog_app, post_id) == PostStatus.HIDDEN
    with catalog_app.app_context():
        entry = db.session.scalar(
            select(AdminAudit).where(AdminAudit.action == "admin.community.remove")
        )
        assert entry is not None and json.loads(entry.detail)["reason"] == "private"
    again = support.post(
        f"/products/posts/{post_id}/remove", data={"reason": "spam"}, follow_redirects=True
    )
    assert "already been removed" in again.get_data(as_text=True)

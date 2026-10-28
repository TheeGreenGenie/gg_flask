"""Owner approval of changes by reviewed staff, and the scoped staff roles (owner, 2026-10-27)."""

# ruff: noqa: S105, S106  (fixture passwords)
from __future__ import annotations

import io
import json
import re

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image
from sqlalchemy import select

from app.admin import service as admin_service
from app.admin.models import AdminRole
from app.catalog.models import Product, ProductImage, Variant
from app.extensions import db
from app.studio import review, service
from app.studio.models import (
    ChangeStatus,
    InventoryHistory,
    StudioChange,
    StudioLoginRequest,
    StudioUser,
)

PREFIX = "/studio-testing-private-path"
PASSWORD = "Temporary phrase 8472!"
OWNER_PASSWORD = "Long unique phrase 482!"


def _staff_client(
    app: Flask, roles: str, review_on: bool = True, name: str = "kemi"
) -> FlaskClient:
    client = app.test_client()
    with app.test_request_context(PREFIX + "/"):
        user = service.create_user(name, PASSWORD, roles, requires_review=review_on)
        user.must_change_password = False
        db.session.commit()
    client.post(PREFIX + "/", data={"username": name, "password": PASSWORD})
    with app.app_context():
        record = db.session.scalars(
            select(StudioLoginRequest).order_by(StudioLoginRequest.id.desc())
        ).first()
        assert record is not None
        service.decide(record, True)
        request_id = record.request_id
    assert client.get(PREFIX + f"/waiting/{request_id}").status_code == 302
    return client


def _owner_client(app: Flask) -> FlaskClient:
    client = app.test_client()
    with app.test_request_context("/"):
        admin, _ = admin_service.create_admin(
            "owner", "owner@example.com", OWNER_PASSWORD, AdminRole.OWNER
        )
        secret = admin.totp_secret
    client.post("/admin/login", data={"username": "owner", "password": OWNER_PASSWORD})
    client.post("/admin/verify", data={"code": admin_service.totp(secret)})
    return client


def _variant(app: Flask, sku: str = "101001") -> Variant:
    with app.app_context():
        variant = db.session.scalar(select(Variant).where(Variant.sku == sku))
        assert variant is not None
        db.session.expunge(variant)
        return variant


def _review_link(app: Flask) -> str:
    message = app.extensions["outbox"][-1]
    match = re.search(r"https?://[^\s]+/review/[A-Za-z0-9_-]+", message.body)
    assert match, message.body
    return match.group(0).split("localhost", 1)[-1]


@pytest.fixture
def shop_app(catalog_app: Flask) -> Flask:
    catalog_app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    return catalog_app


def test_reviewed_stock_change_waits_and_emails_the_owner(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "maintenance")
    variant = _variant(shop_app)
    response = staff.post(
        PREFIX + f"/inventory/{variant.id}",
        data={"quantity": "9", "reason": "Counted shelf", "mode": "set"},
        headers={"Referer": f"http://localhost{PREFIX}/inventory"},
    )
    assert response.status_code == 303
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).stock_quantity == variant.stock_quantity
        change = db.session.scalars(select(StudioChange)).one()
        assert change.status == ChangeStatus.PENDING
        assert f"{variant.stock_quantity} → 9" in change.summary
        assert "Reason: Counted shelf" in change.summary
    message = shop_app.extensions["outbox"][-1]
    assert message.to == "owner@example.com"
    assert "NOT on the website" in message.body and "Counted shelf" in message.body
    assert "My changes" in staff.get(PREFIX + "/changes").get_data(as_text=True)


def test_owner_approval_applies_it_as_the_staff_member(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "maintenance")
    variant = _variant(shop_app)
    staff.post(
        PREFIX + f"/inventory/{variant.id}",
        data={"quantity": "9", "reason": "Counted shelf", "mode": "set"},
    )
    link = _review_link(shop_app)
    anonymous = shop_app.test_client()
    page = anonymous.get(link).get_data(as_text=True)
    assert "Owner sign in" in page and "Approve and publish" not in page
    assert anonymous.post(link, data={"decision": "approve"}).status_code == 403

    owner = _owner_client(shop_app)
    assert "Approve and publish" in owner.get(link).get_data(as_text=True)
    owner.post(link, data={"decision": "approve"})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).stock_quantity == 9
        change = db.session.scalars(select(StudioChange)).one()
        assert change.status == ChangeStatus.APPLIED and change.decided_by == "owner@example.com"
        history = db.session.scalars(select(InventoryHistory)).one()
        kemi = db.session.scalar(select(StudioUser).where(StudioUser.username == "kemi"))
        assert history.user_id == kemi.id and history.reason == "Counted shelf"
    # A decided link cannot be used again.
    owner.post(link, data={"decision": "approve"})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).stock_quantity == 9


def test_rejected_change_never_reaches_the_shop(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "sales")
    variant = _variant(shop_app)
    staff.post(PREFIX + f"/prices/{variant.id}", data={"price": "1.00", "reason": "oops"})
    link = _review_link(shop_app)
    _owner_client(shop_app).post(link, data={"decision": "reject"})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).price_cents == variant.price_cents
        assert db.session.scalars(select(StudioChange)).one().status == ChangeStatus.REJECTED


def test_conflicting_change_is_not_applied(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "sales")
    variant = _variant(shop_app)
    staff.post(PREFIX + f"/prices/{variant.id}", data={"price": "12.00", "reason": "raise"})
    with shop_app.app_context():
        db.session.get(Variant, variant.id).price_cents = 1500  # someone else changed it
        db.session.commit()
    _owner_client(shop_app).post(_review_link(shop_app), data={"decision": "approve"})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).price_cents == 1500
        change = db.session.scalars(select(StudioChange)).one()
        assert change.status == ChangeStatus.CONFLICT and "nothing was overwritten" in change.result


def test_held_photo_is_private_until_approved(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "branding")
    with shop_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        product_id, before = product.id, len(product.images)
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (200, 120, 30)).save(buffer, "JPEG")
    buffer.seek(0)
    staff.post(
        PREFIX + f"/products/{product_id}/images",
        data={"image": (buffer, "phone photo.jpg"), "alt": "Folded bundle"},
        content_type="multipart/form-data",
    )
    with shop_app.app_context():
        change = db.session.scalars(select(StudioChange)).one()
        stored = json.loads(change.upload or "[]")[0]["stored"]
        held = review.pending_dir() / stored
        assert held.is_file()
        assert not str(held).startswith(shop_app.config["MEDIA_ROOT"])  # never public
        assert len(db.session.get(Product, product_id).images) == before
    _owner_client(shop_app).post(_review_link(shop_app), data={"decision": "approve"})
    with shop_app.app_context():
        images = db.session.scalars(
            select(ProductImage).where(ProductImage.product_id == product_id)
        ).all()
        assert len(images) == before + 1 and images[-1].alt == "Folded bundle"
        assert not held.exists()


def test_drafts_are_not_held_but_publishing_is(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "branding")
    form = {
        "title": "Our new prints",
        "slug": "our-new-prints",
        "category": "Journal",
        "excerpt": "A first look.",
        "cover_path": "static:img/home/post-ankara.webp",
        "cover_alt": "Prints",
        "body": "Hello there.",
    }
    response = staff.post(PREFIX + "/blog/new", data=form)
    assert response.status_code == 302
    from app.content.models import BlogPost, BlogStatus

    with shop_app.app_context():
        post = db.session.scalar(select(BlogPost).where(BlogPost.slug == "our-new-prints"))
        assert post is not None and post.status == BlogStatus.DRAFT
        assert db.session.scalar(select(StudioChange)) is None
        post_id = post.id
    staff.post(PREFIX + f"/blog/{post_id}/publication", data={"action": "publish"})
    with shop_app.app_context():
        assert db.session.get(BlogPost, post_id).status == BlogStatus.DRAFT
        assert "Publish now" in db.session.scalars(select(StudioChange)).one().summary


def test_unreviewed_staff_still_change_things_directly(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "editor", review_on=False)
    variant = _variant(shop_app)
    staff.post(PREFIX + f"/prices/{variant.id}", data={"price": "12.34", "reason": "direct"})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).price_cents == 1234
        assert db.session.scalar(select(StudioChange)) is None


@pytest.mark.parametrize(
    ("roles", "allowed", "refused"),
    [
        ("maintenance", ["/inventory", "/products"], ["/prices", "/blog", "/community", "/sales"]),
        ("sales", ["/prices", "/sales", "/community"], ["/inventory", "/blog"]),
        ("branding", ["/blog", "/products"], ["/inventory", "/prices", "/sales", "/community"]),
    ],
)
def test_scoped_roles_see_only_their_area(
    shop_app: Flask, roles: str, allowed: list[str], refused: list[str]
) -> None:
    staff = _staff_client(shop_app, roles)
    for path in allowed:
        assert staff.get(PREFIX + path).status_code == 200, path
    for path in refused:
        assert staff.get(PREFIX + path).status_code == 403, path
    nav = staff.get(PREFIX + "/home").get_data(as_text=True).split("</nav>", 2)[1]
    for path in refused:
        assert f'{PREFIX}{path}"' not in nav, path


def test_admin_sign_in_returns_to_the_review_page_safely(shop_app: Flask) -> None:
    client = shop_app.test_client()
    with shop_app.test_request_context("/"):
        admin, _ = admin_service.create_admin(
            "owner", "owner@example.com", OWNER_PASSWORD, AdminRole.OWNER
        )
        secret = admin.totp_secret
    client.get("/admin/login?next=" + PREFIX + "/review/abc")
    client.post("/admin/login", data={"username": "owner", "password": OWNER_PASSWORD})
    done = client.post("/admin/verify", data={"code": admin_service.totp(secret)})
    assert done.headers["Location"].endswith(PREFIX + "/review/abc")
    for unsafe in ("https://evil.example/x", "//evil.example/x", "/\\evil.example"):
        client.post("/admin/logout")
        client.get("/admin/login?next=" + unsafe)
        client.post("/admin/login", data={"username": "owner", "password": OWNER_PASSWORD})
        done = client.post("/admin/verify", data={"code": admin_service.totp(secret)})
        assert done.headers["Location"].endswith("/admin/"), unsafe


def test_summary_lists_only_real_edits(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "branding")
    with shop_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        product.description = "Line one.\n\nLine two."
        db.session.commit()
        form = {
            "name": product.name,
            "summary": "A brand new summary",
            "description": "Line one.\r\n\r\nLine two.",  # how browsers send it
            "category_ids": [str(c.id) for c in product.categories],
            "sale_unit": product.sale_unit,
        }
        product_id = product.id
    staff.post(PREFIX + f"/products/{product_id}", data=form)
    with shop_app.app_context():
        summary = db.session.scalars(select(StudioChange)).one().summary
    assert "Summary:" in summary and "Description" not in summary


def test_reviewed_bulk_price_change_waits_for_the_owner(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "sales")
    variant = _variant(shop_app)
    form = {"q": variant.sku, "kind": "set", "amount": "12.00", "reason": "New season"}
    page = staff.get(PREFIX + "/prices/bulk", query_string={**form, "preview": 1})
    fingerprint = re.search(
        r'name="fingerprint" value="([0-9a-f]{64})"', page.get_data(as_text=True)
    )
    assert fingerprint
    staff.post(PREFIX + "/prices/bulk", data={**form, "fingerprint": fingerprint[1]})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).price_cents == variant.price_cents
        change = db.session.scalars(select(StudioChange)).one()
        assert change.status == ChangeStatus.PENDING and "→ $12.00" in change.summary
    _owner_client(shop_app).post(_review_link(shop_app), data={"decision": "approve"})
    with shop_app.app_context():
        assert db.session.get(Variant, variant.id).price_cents == 1200
        assert db.session.scalars(select(StudioChange)).one().status == ChangeStatus.APPLIED


def test_staff_get_the_playful_studio_and_owners_do_not(shop_app: Flask) -> None:
    staff = _staff_client(shop_app, "sales", name="mr.adetolu")
    home = staff.get(PREFIX + "/home").get_data(as_text=True)
    assert "gg-play" in home and "studio-play.css" in home
    assert "Hi Mr. Adetolu!" in home and "Your stars this week" in home
    assert "Every change the owner approves earns you a star" in home
    prices = staff.get(PREFIX + "/prices").get_data(as_text=True)
    assert 'class="gg-play__steps"' in prices and "Press Save" in prices

    # One approved change = one star.
    variant = _variant(shop_app)
    staff.post(PREFIX + f"/prices/{variant.id}", data={"price": "12.00", "reason": "raise"})
    _owner_client(shop_app).post(_review_link(shop_app), data={"decision": "approve"})
    assert "1 star! Great start" in staff.get(PREFIX + "/home").get_data(as_text=True)

    owner = _staff_client(shop_app, "owner", review_on=False, name="mrs.adetolu")
    page = owner.get(PREFIX + "/home").get_data(as_text=True)
    assert "gg-play" not in page and "studio-play.css" not in page
    assert "What needs attention?" in page
    assert "gg-play__steps" not in owner.get(PREFIX + "/prices").get_data(as_text=True)

from __future__ import annotations

from datetime import timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select

from app.catalog.models import utcnow
from app.extensions import db
from app.wishlist import service
from app.wishlist.models import Wishlist, WishlistItem

JSON = {"Accept": "application/json"}


def toggle(client: FlaskClient, slug: str, action: str = "add"):  # type: ignore[no-untyped-def]
    return client.post("/wishlist/toggle", data={"product": slug, "action": action}, headers=JSON)


def test_save_creates_hashed_token_and_lists_item(shop: FlaskClient, catalog_app: Flask) -> None:
    resp = toggle(shop, "ankara-blue")
    assert resp.get_json() == {
        "ok": True,
        "message": "Saved to your wishlist.",
        "count": 1,
        "saved": True,
    }
    cookie = shop.get_cookie(service.COOKIE)
    assert cookie is not None and cookie.http_only and cookie.same_site == "Lax"
    with catalog_app.app_context():
        stored = db.session.scalars(select(Wishlist.token_hash)).all()
        assert stored == [service._hash(cookie.value)]  # raw token never stored
        assert cookie.value not in stored
    api = shop.get("/api/v1/wishlist")
    assert api.headers["Cache-Control"] == "private, no-store"
    assert [i["slug"] for i in api.get_json()["items"]] == ["ankara-blue"]


def test_saving_twice_is_idempotent(shop: FlaskClient, catalog_app: Flask) -> None:
    toggle(shop, "ankara-blue")
    assert toggle(shop, "ankara-blue").get_json()["message"] == "Already in your wishlist."
    with catalog_app.app_context():
        assert db.session.scalar(select(func.count(WishlistItem.id))) == 1


def test_remove_and_clear(shop: FlaskClient) -> None:
    toggle(shop, "ankara-blue")
    toggle(shop, "cushion-g1")
    assert toggle(shop, "ankara-blue", "remove").get_json()["count"] == 1
    assert shop.post("/wishlist/clear", headers=JSON).get_json()["count"] == 0


@pytest.mark.parametrize(
    "slug",
    ["draft-thing", "does-not-exist", "", "x" * 500, "' OR 1=1 --", "<script>alert(1)</script>"],
)
def test_only_active_products_are_accepted(
    shop: FlaskClient, catalog_app: Flask, slug: str
) -> None:
    resp = toggle(shop, slug)
    assert resp.status_code == 400 and resp.get_json()["ok"] is False
    with catalog_app.app_context():
        assert db.session.scalar(select(func.count(WishlistItem.id))) == 0


def test_rejected_input_does_not_create_a_wishlist(shop: FlaskClient, catalog_app: Flask) -> None:
    toggle(shop, "nope")
    assert shop.get_cookie(service.COOKIE) is None
    with catalog_app.app_context():
        assert db.session.scalar(select(func.count(Wishlist.id))) == 0


def test_cannot_read_someone_elses_list(shop: FlaskClient, catalog_app: Flask) -> None:
    toggle(shop, "ankara-blue")
    other = catalog_app.test_client()
    assert other.get("/api/v1/wishlist").get_json() == {"items": [], "count": 0}
    other.set_cookie(service.COOKIE, "guessed-token")
    assert other.get("/api/v1/wishlist").get_json()["count"] == 0


def test_list_is_capped(shop: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "MAX_ITEMS", 1)
    toggle(shop, "ankara-blue")
    resp = toggle(shop, "cushion-g1")
    assert resp.status_code == 400 and "full" in resp.get_json()["message"]


def test_writes_are_rate_limited(shop: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "WRITE_LIMIT", (2, 60))
    assert toggle(shop, "ankara-blue").status_code == 200
    assert toggle(shop, "ankara-blue", "remove").status_code == 200
    resp = toggle(shop, "ankara-blue")
    assert resp.status_code == 400 and "wait" in resp.get_json()["message"]


def test_rate_limit_fails_open_when_redis_is_down(shop: FlaskClient, catalog_app: Flask) -> None:
    catalog_app.extensions["redis"].healthy = False
    assert toggle(shop, "ankara-blue").status_code == 200


def test_writes_require_csrf_token(catalog_app: Flask) -> None:
    catalog_app.config["WTF_CSRF_ENABLED"] = True
    client = catalog_app.test_client()
    resp = client.post("/wishlist/toggle", data={"product": "ankara-blue"}, headers=JSON)
    assert resp.status_code == 400
    catalog_app.config["WTF_CSRF_ENABLED"] = False


def test_archived_products_drop_out_of_the_list(shop: FlaskClient, catalog_app: Flask) -> None:
    from app.catalog.models import Product, ProductStatus

    toggle(shop, "ankara-blue")
    with catalog_app.app_context():
        p = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        p.status = ProductStatus.ARCHIVED
        db.session.commit()
    assert shop.get("/api/v1/wishlist").get_json()["count"] == 0


def test_prune_deletes_only_idle_lists(shop: FlaskClient, catalog_app: Flask) -> None:
    toggle(shop, "ankara-blue")
    catalog_app.test_client().post("/wishlist/toggle", data={"product": "cushion-g1"}, headers=JSON)
    with catalog_app.app_context():
        old = db.session.scalars(select(Wishlist)).first()
        old.updated_at = utcnow() - timedelta(days=service.RETENTION_DAYS + 1)
        db.session.commit()
        assert service.prune() == 1
        assert db.session.scalar(select(func.count(Wishlist.id))) == 1
        assert db.session.scalar(select(func.count(WishlistItem.id))) == 1


def test_pages_show_saved_state(shop: FlaskClient) -> None:
    html = shop.get("/product/ankara-blue/").get_data(as_text=True)
    assert 'aria-pressed="false"' in html and "Add to wishlist" in html
    toggle(shop, "ankara-blue")
    html = shop.get("/product/ankara-blue/").get_data(as_text=True)
    assert 'aria-pressed="true"' in html and "Saved to wishlist" in html
    assert 'aria-label="Wishlist, 1 item"' in html
    page = shop.get("/wishlist/").get_data(as_text=True)
    assert "/product/ankara-blue/" in page and "Clear wishlist" in page


def test_form_post_without_js_redirects_back(shop: FlaskClient) -> None:
    resp = shop.post(
        "/wishlist/toggle",
        data={"product": "ankara-blue"},
        headers={"Referer": "https://evil.example/phish"},
    )
    assert resp.status_code == 302 and resp.headers["Location"] == "/wishlist/"

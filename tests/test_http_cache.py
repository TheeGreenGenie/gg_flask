"""Shared caches (the host's page cache) may only store responses that are explicitly public."""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient

PRIVATE = "private, no-store"


def test_pages_with_per_visitor_content_are_never_shared_cached(shop: FlaskClient) -> None:
    # Pages carry the cart count, CSRF tokens and flashed messages, so none may be stored.
    for path in ("/", "/product/ankara-blue/", "/shop/", "/robots.txt", "/no-such-page/"):
        assert shop.get(path).headers["Cache-Control"] == PRIVATE, path


def test_redirects_and_form_posts_are_private(shop: FlaskClient) -> None:
    assert shop.get("/support/").headers["Cache-Control"] == PRIVATE  # legacy redirect
    response = shop.post("/newsletter", data={"email": "reader@example.com"})
    assert response.headers["Cache-Control"] == PRIVATE


def test_the_public_discovery_feed_stays_cacheable(catalog_app: Flask) -> None:
    response = catalog_app.test_client().get("/api/v1/discovery/fabrics")
    assert not response.headers.getlist("Set-Cookie")
    assert response.headers["Cache-Control"].startswith("public")


def test_explicitly_public_assets_keep_their_long_cache(shop: FlaskClient, app: Flask) -> None:
    versioned = shop.get("/static/css/tokens.css?v=abc")
    assert "public" in versioned.headers["Cache-Control"]
    assert "immutable" in versioned.headers["Cache-Control"]
    media_root = Path(app.config["MEDIA_ROOT"])
    (media_root / "library").mkdir(parents=True, exist_ok=True)
    (media_root / "library" / "x.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    media = app.test_client().get("/media/library/x.svg?v=1")
    assert media.status_code == 200 and "public" in media.headers["Cache-Control"]


def test_policy_overrides_a_stray_public_marker_when_a_cookie_is_set(app: Flask) -> None:
    from flask import Response

    from app.security import apply_shared_cache_policy

    response = Response("x")
    response.cache_control.public = True
    response.cache_control.max_age = 600
    response.set_cookie("session", "abc")
    assert apply_shared_cache_policy(response).headers["Cache-Control"] == PRIVATE
    plain = Response("y")
    plain.headers["Cache-Control"] = "no-cache"
    assert apply_shared_cache_policy(plain).headers["Cache-Control"] == PRIVATE

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app import cache


def test_static_urls_are_fingerprinted_and_cached_for_a_year(shop: FlaskClient) -> None:
    html = shop.get("/").get_data(as_text=True)
    assert "/static/css/tokens.css?v=" in html
    url = html.split('href="/static/css/tokens.css?v=', 1)[1].split('"', 1)[0]
    resp = shop.get(f"/static/css/tokens.css?v={url}")
    cc = resp.headers["Cache-Control"]
    assert "max-age=31536000" in cc and "immutable" in cc and "no-cache" not in cc
    plain = shop.get("/static/css/tokens.css").headers["Cache-Control"]
    assert "max-age=3600" in plain and "immutable" not in plain


def test_product_images_are_fingerprinted(shop: FlaskClient) -> None:
    html = shop.get("/shop/").get_data(as_text=True)
    assert "/media/products/ankara-blue/01.png?v=" in html


def test_discovery_feed_supports_etag(shop: FlaskClient) -> None:
    first = shop.get("/api/v1/discovery/fabrics")
    etag = first.headers["ETag"]
    again = shop.get("/api/v1/discovery/fabrics", headers={"If-None-Match": etag})
    assert again.status_code == 304


def _cache_keys(app: Flask) -> list[str]:
    store = app.extensions["redis"]
    if hasattr(store, "values"):  # tests' FakeRedis
        return list(store.values)
    from sqlalchemy import select

    from app.extensions import db
    from app.kvstore import KVEntry

    with app.app_context():  # the database stand-in (TEST_KV_BACKEND=database)
        return list(db.session.scalars(select(KVEntry.key)))


def test_catalog_cache_is_used_and_invalidated(shop: FlaskClient, catalog_app: Flask) -> None:
    shop.get("/api/v1/discovery/fabrics")
    assert any(k.startswith("cache:catalog:v0:discovery:") for k in _cache_keys(catalog_app))
    with catalog_app.test_request_context():
        cache.bump_catalog_version()
        assert cache.catalog_version() == "1"
    shop.get("/api/v1/discovery/fabrics")
    assert any(k.startswith("cache:catalog:v1:discovery:") for k in _cache_keys(catalog_app))


def test_cache_fails_open_when_redis_is_down(shop: FlaskClient, catalog_app: Flask) -> None:
    catalog_app.extensions["redis"].get = lambda *_: (_ for _ in ()).throw(OSError("down"))
    assert shop.get("/api/v1/discovery/fabrics").status_code == 200


def test_find_fabrics_category_filter(shop: FlaskClient) -> None:
    html = shop.get("/find-fabrics/").get_data(as_text=True)
    assert 'data-gg-ff-cat="fabrics"' in html and 'aria-current="true"' in html
    feed = shop.get("/api/v1/discovery/fabrics?category=fabrics").get_json()
    assert [i["slug"] for i in feed["items"]] == ["ankara-blue"]
    # Anything outside the Fabrics tree falls back to all fabrics (never other categories).
    other = shop.get("/api/v1/discovery/fabrics?category=home-accessories").get_json()
    assert [i["slug"] for i in other["items"]] == ["ankara-blue"]
    unknown = shop.get("/find-fabrics/?category=nope").get_data(as_text=True)
    assert 'data-category="fabrics"' in unknown


def test_theme_assets_present(shop: FlaskClient) -> None:
    html = shop.get("/").get_data(as_text=True)
    head = html.split("</head>", 1)[0]
    # The theme script must run before paint: in <head>, not deferred.
    assert "/static/js/theme-init.js?v=" in head
    assert (
        "theme-init.js?v=" in head
        and "defer" not in head.split("theme-init.js", 1)[1].split(">", 1)[0]
    )
    assert "data-gg-theme-toggle" in html

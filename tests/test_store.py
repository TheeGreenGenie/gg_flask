from __future__ import annotations

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app.cart.models import Cart
from app.cart.service import COOKIE, _hash
from app.catalog.models import Variant
from app.extensions import db
from app.templating import money

PAGES = [
    "/",
    "/shop/",
    "/shop/fabrics/",
    "/shop/ankara/",
    "/collections/new-arrivals/",
    "/collections/sale/",
    "/collections/bhm-collection/",
    "/shop/?q=ankara&sort=price-desc&in_stock=1",
    "/product/ankara-blue/",
    "/product/cushion-g1/",
    "/cart/",
]


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_with_landmarks_and_no_inline_code(shop: FlaskClient, path: str) -> None:
    resp = shop.get(path)
    assert resp.status_code == 200, path
    html = resp.get_data(as_text=True)
    for marker in ('class="gg-skip-link"', "<header", 'id="main"', "<footer", 'aria-label="Main"'):
        assert marker in html, marker
    # The CSP forbids inline scripts and style attributes.
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\sstyle=", html)


@pytest.mark.parametrize("path", ["/shop/nope/", "/collections/nope/", "/product/nope/"])
def test_unknown_pages_404(shop: FlaskClient, path: str) -> None:
    assert shop.get(path).status_code == 404


def test_draft_products_are_hidden(shop: FlaskClient) -> None:
    assert shop.get("/product/draft-thing/").status_code == 404
    assert "Unreleased Print" not in shop.get("/shop/").get_data(as_text=True)


def test_parent_category_includes_children(shop: FlaskClient) -> None:
    html = shop.get("/shop/fabrics/").get_data(as_text=True)
    assert "African Ankara Print - 101001" in html
    assert "Cushion Cover G-1" not in html


def test_sale_and_collection_filters(shop: FlaskClient) -> None:
    sale = shop.get("/collections/sale/").get_data(as_text=True).split('id="results-count"')[1]
    assert "Cushion Cover G-1" in sale and "Ankara Print" not in sale
    bhm = shop.get("/collections/bhm-collection/").get_data(as_text=True)
    assert "African Ankara Print - 101001" in bhm.split('id="results-count"')[1]


def test_search_matches_sku(shop: FlaskClient) -> None:
    html = shop.get("/shop/?q=G1-3").get_data(as_text=True)
    assert "Cushion Cover G-1" in html


def test_bad_query_params_are_safe(shop: FlaskClient) -> None:
    assert shop.get("/shop/?page=-4&sort=;drop&q=%25%25").status_code == 200
    assert shop.get("/shop/?page=99999").status_code == 200


def test_product_page_marks_sold_out_option(shop: FlaskClient, catalog_app: Flask) -> None:
    with catalog_app.app_context():
        sold_out = db.session.scalar(select(Variant).where(Variant.sku == "G1-2"))
    html = shop.get("/product/cushion-g1/").get_data(as_text=True).replace("\n", " ")
    assert re.search(rf'value="{sold_out.id}"[^>]*disabled', html)
    assert "Only 2 left in stock" in html  # first available option is preselected


def test_media_is_served(shop: FlaskClient) -> None:
    resp = shop.get("/media/products/ankara-blue/01.png")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert shop.get("/media/../../secret").status_code == 404


# ---------- Cart ----------


def variant_id(app: Flask, sku: str) -> int:
    with app.app_context():
        return db.session.scalar(select(Variant.id).where(Variant.sku == sku))


def test_add_to_cart_creates_hashed_token_cookie(shop: FlaskClient, catalog_app: Flask) -> None:
    resp = shop.post(
        "/cart/add", data={"variant": variant_id(catalog_app, "101001"), "quantity": 2}
    )
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/cart/")
    cookie = shop.get_cookie(COOKIE)
    assert cookie is not None and cookie.http_only and len(cookie.value) >= 40
    with catalog_app.app_context():
        cart = db.session.scalar(select(Cart))
        assert cart.token_hash == _hash(cookie.value) != cookie.value
        assert cart.lines[0].quantity == 2
    html = shop.get("/cart/").get_data(as_text=True)
    assert money(2 * 899) in html


def test_quantity_is_capped_by_stock_including_cart(shop: FlaskClient, catalog_app: Flask) -> None:
    vid = variant_id(catalog_app, "101001")  # 5 in stock
    shop.post("/cart/add", data={"variant": vid, "quantity": 4})
    shop.post("/cart/add", data={"variant": vid, "quantity": 4})
    with catalog_app.app_context():
        assert db.session.scalar(select(Cart)).lines[0].quantity == 5
    resp = shop.post("/cart/add", data={"variant": vid, "quantity": 1}, follow_redirects=True)
    assert "already have all 5" in resp.get_data(as_text=True)


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"variant": "G1-2", "quantity": 1}, "out of stock"),
        ({"variant": "DRAFT-1", "quantity": 1}, "not available"),
        ({"variant": "101001", "quantity": 0}, "at least 1"),
        ({"variant": "not-a-number", "quantity": 1}, "choose an option"),
    ],
)
def test_add_rejections(shop: FlaskClient, catalog_app: Flask, form: dict, message: str) -> None:
    data = dict(form)
    if data["variant"] != "not-a-number":
        data["variant"] = variant_id(catalog_app, data["variant"])
    html = shop.post("/cart/add", data=data, follow_redirects=True).get_data(as_text=True)
    assert message in html.lower()
    with catalog_app.app_context():
        cart = db.session.scalar(select(Cart))
        assert cart is None or not cart.lines


def test_price_comes_from_database_not_request(shop: FlaskClient, catalog_app: Flask) -> None:
    shop.post(
        "/cart/add",
        data={"variant": variant_id(catalog_app, "101001"), "quantity": 1, "price_cents": 1},
    )
    with catalog_app.app_context():
        v = db.session.scalar(select(Variant).where(Variant.sku == "101001"))
        v.price_cents = 1234  # price changes after the item was added
        db.session.commit()
    assert money(1234) in shop.get("/cart/").get_data(as_text=True)


def test_update_and_remove(shop: FlaskClient, catalog_app: Flask) -> None:
    shop.post("/cart/add", data={"variant": variant_id(catalog_app, "101001"), "quantity": 1})
    shop.post("/cart/add", data={"variant": variant_id(catalog_app, "G1-1"), "quantity": 1})
    with catalog_app.app_context():
        lines = {ln.variant.sku: ln.id for ln in db.session.scalar(select(Cart)).lines}
    shop.post("/cart/update", data={f"qty-{lines['101001']}": 9, f"qty-{lines['G1-1']}": 0})
    with catalog_app.app_context():
        cart = db.session.scalar(select(Cart))
        assert [(ln.variant.sku, ln.quantity) for ln in cart.lines] == [("101001", 5)]
    shop.post("/cart/update", data={"remove": lines["101001"]})
    with catalog_app.app_context():
        assert db.session.scalar(select(Cart)).lines == []


def test_cannot_touch_another_shoppers_cart(catalog_app: Flask) -> None:
    alice, mallory = catalog_app.test_client(), catalog_app.test_client()
    alice.post("/cart/add", data={"variant": variant_id(catalog_app, "101001"), "quantity": 1})
    with catalog_app.app_context():
        line_id = db.session.scalar(select(Cart)).lines[0].id
    mallory.post("/cart/update", data={"remove": line_id, f"qty-{line_id}": 0})
    with catalog_app.app_context():
        assert len(db.session.scalar(select(Cart)).lines) == 1
    mallory.set_cookie(COOKIE, "forged-token")
    assert "Your cart is empty" in mallory.get("/cart/").get_data(as_text=True)


def test_checkout_requires_items(shop: FlaskClient, catalog_app: Flask) -> None:
    assert shop.get("/checkout/").status_code == 302
    shop.post("/cart/add", data={"variant": variant_id(catalog_app, "101001"), "quantity": 1})
    html = shop.get("/checkout/").get_data(as_text=True)
    assert "Shipping address" in html and 'value="WY"' in html


def test_unavailable_line_is_flagged_and_excluded(shop: FlaskClient, catalog_app: Flask) -> None:
    shop.post("/cart/add", data={"variant": variant_id(catalog_app, "G1-1"), "quantity": 2})
    with catalog_app.app_context():
        v = db.session.scalar(select(Variant).where(Variant.sku == "G1-1"))
        v.stock_quantity = 0
        db.session.commit()
    html = shop.get("/cart/").get_data(as_text=True)
    assert "out of stock" in html and money(0) in html


# ---------- Preview, header, bundles ----------


def test_preview_is_a_fragment_with_its_own_ids(shop: FlaskClient) -> None:
    resp = shop.get("/product/cushion-g1/preview")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<html" not in html and "<header" not in html
    assert 'id="qv-qty"' in html and 'id="qty"' not in html
    assert "View full details" in html
    assert shop.get("/product/draft-thing/preview").status_code == 404


def test_cards_link_to_page_and_preview(shop: FlaskClient) -> None:
    html = shop.get("/shop/").get_data(as_text=True)
    assert 'href="/product/ankara-blue/" data-gg-preview="/product/ankara-blue/preview"' in html


def test_add_to_cart_json_for_preview(shop: FlaskClient, catalog_app: Flask) -> None:
    resp = shop.post(
        "/cart/add",
        data={"variant": variant_id(catalog_app, "101001"), "quantity": 2},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {
        "ok": True,
        "message": "Added to your cart.",
        "count": 2,
        "subtotal": money(2 * 899),
        "cart_url": "/cart/",
    }
    bad = shop.post(
        "/cart/add",
        data={"variant": variant_id(catalog_app, "G1-2"), "quantity": 1},
        headers={"Accept": "application/json"},
    )
    assert bad.status_code == 400 and bad.get_json()["ok"] is False


def test_socials_are_inside_sticky_header_and_no_account_links(shop: FlaskClient) -> None:
    html = shop.get("/").get_data(as_text=True)
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "gg-topbar__social" in header
    assert "/my-account/" not in html


@pytest.mark.parametrize(
    "path", ["/shop/", "/product/ankara-blue/", "/product/ankara-blue/preview"]
)
def test_prices_are_bundle_prices_not_per_yard(shop: FlaskClient, path: str) -> None:
    html = shop.get(path).get_data(as_text=True).lower()
    assert "per yard" not in html and "/ yard" not in html

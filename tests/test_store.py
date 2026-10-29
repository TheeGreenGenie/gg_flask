from __future__ import annotations

import re
from datetime import date
from pathlib import Path

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


def test_checkout_creates_order_and_private_payment_page(
    shop: FlaskClient, catalog_app: Flask
) -> None:
    shop.post("/cart/add", data={"variant": variant_id(catalog_app, "101001"), "quantity": 1})
    html = shop.get("/checkout/").get_data(as_text=True)
    nonce = re.search(r'name="checkout_nonce" value="([^"]+)"', html)
    assert nonce
    response = shop.post(
        "/checkout/",
        data={
            "checkout_nonce": nonce.group(1),
            "email": "buyer@example.com",
            "first_name": "Ada",
            "last_name": "Buyer",
            "address1": "123 Market Street",
            "city": "Philadelphia",
            "region": "PA",
            "postcode": "19103",
            "country": "US",
            "payment": "stripe",
            "terms": "1",
        },
    )
    assert response.status_code == 302 and "/payment" in response.location
    page = shop.get(response.location).get_data(as_text=True)
    assert "Stripe payments are not available right now" in page  # no keys in tests
    assert "Nothing has been charged" in page
    # Review-and-pay page: item photo and details, delivery address, reservation time.
    assert 'class="gg-pay__item"' in page and "/media/products/ankara-blue/" in page
    assert "123 Market Street" in page and "Philadelphia, PA 19103" in page
    assert re.search(r"reserved for \d+ more minutes", page)
    catalog_app.config.update(STRIPE_SECRET_KEY="sk_test_x", STRIPE_WEBHOOK_SECRET="whsec_x")  # noqa: S106
    ready = shop.get(response.location).get_data(as_text=True)
    assert re.search(r"Pay \$[\d.,]+ securely", ready)
    assert "We never see or store your card details" in ready
    # Browsers apply form-action to the redirect after "Pay": only this page may continue to
    # the providers' checkout pages; everywhere else forms stay on this site.
    pay_csp = shop.get(response.location).headers["Content-Security-Policy"]
    form_action = next(d for d in pay_csp.split("; ") if d.startswith("form-action"))
    assert "https://checkout.stripe.com" in form_action and "https://www.paypal.com" in form_action
    home_csp = shop.get("/").headers["Content-Security-Policy"]
    assert "form-action 'self';" in home_csp and "checkout.stripe.com" not in home_csp
    assert catalog_app.test_client().get(response.location).status_code == 404


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


def test_desktop_navigation_has_hover_mega_menu(shop: FlaskClient) -> None:
    html = shop.get("/").get_data(as_text=True)
    assert 'class="gg-mainnav__dd gg-mainnav__mega"' in html
    assert "gg-megamenu" in html and "Browse by print" in html
    assert "data-gg-hover-menu" in html


@pytest.mark.parametrize(
    "path", ["/shop/", "/product/ankara-blue/", "/product/ankara-blue/preview"]
)
def test_prices_are_bundle_prices_not_per_yard(shop: FlaskClient, path: str) -> None:
    html = shop.get(path).get_data(as_text=True).lower()
    assert "per yard" not in html and "/ yard" not in html


# ---------- Find Fabrics ----------


def test_find_fabrics_page_has_stack_and_no_js_grid(shop: FlaskClient) -> None:
    html = shop.get("/find-fabrics/").get_data(as_text=True)
    assert "data-gg-ff" in html and "find-fabrics.js" in html
    fallback = html.split("data-gg-ff-fallback", 1)[1]
    assert "/product/ankara-blue/" in fallback  # in-stock fabric
    assert "cushion-g1" not in fallback  # not a fabric
    assert 'href="/find-fabrics/"' in html  # in the menu


def test_find_fabrics_mobile_grid_cannot_expand_past_viewport() -> None:
    css = Path("app/static/css/find-fabrics.css").read_text(encoding="utf-8")
    assert ".gg-ff { grid-template-columns: minmax(0, 1fr); }" in css
    assert ".gg-ff__stage { overflow: clip; }" in css
    assert "width: min(100%, max(13rem" in css


def test_discovery_feed_is_minimal_bounded_and_fabric_only(shop: FlaskClient) -> None:
    data = shop.get("/api/v1/discovery/fabrics").get_json()
    assert [i["slug"] for i in data["items"]] == ["ankara-blue"]
    assert data["next_cursor"] is None
    item = data["items"][0]
    assert set(item) == {
        "slug", "name", "price", "compare_at", "image", "alt",
        "category", "category_url", "url", "preview",
    }  # fmt: skip
    assert item["preview"] == "/product/ankara-blue/preview"
    assert shop.get("/api/v1/discovery/fabrics?cursor=nope").status_code == 200
    assert shop.get("/api/v1/discovery/fabrics?cursor=-5").get_json()["items"]


def test_discovery_paginates_with_stable_cursor(catalog_app: Flask) -> None:
    from app.catalog import queries

    with catalog_app.app_context():
        first, nxt = queries.discovery(0, limit=1)
        assert len(first) == 1 and nxt is None
        assert queries.discovery(5, limit=1) == ([], None)


def test_atelier_cards_are_find_fabrics_wishlist_prints_and_3d_soon(shop: FlaskClient) -> None:
    html = shop.get("/").get_data(as_text=True)
    section = html.split("Explore the atelier", 1)[1].split("</section>", 1)[0]
    titles = ["Find Fabrics", "Wishlist", "African Prints", "3D Models"]
    assert re.findall(r"<h3>([^<]+)</h3>", section) == titles
    assert 'href="/find-fabrics/"' in section and 'href="/wishlist/"' in section
    assert 'href="/shop/fabrics/"' in section
    assert section.count("gg-home-opening-card--soon") == 1 and "Coming soon" in section
    # The main menu is unchanged.
    assert "Real African Prints Shop" in html


@pytest.fixture
def fabric_shop(app: Flask, tmp_path: Path) -> Flask:
    """Catalog with several fabrics of different ages and prices (plus a cushion)."""
    from app.catalog import manifest as mf
    from tests.catalog_fixtures import make_image, manifest, write_assets

    assets = tmp_path / "assets"
    write_assets(assets)
    data = manifest()
    for slug, published, price in [
        ("kente-old", date(2025, 1, 1), 1200),
        ("kente-new", date(2026, 10, 20), 500),
        ("kente-dear", date(2026, 9, 1), 2500),
    ]:
        make_image(assets / f"products/{slug}/01.png", (200, 160, 20))
        data["products"].append(
            {
                "slug": slug,
                "name": f"Kente Print - {slug}",
                "type": "fabric",
                "status": "active",
                "published": published,
                "categories": ["ankara"],
                "images": [{"file": f"products/{slug}/01.png", "alt": slug}],
                "variants": [{"sku": slug.upper(), "price_cents": price, "stock": 3}],
            }
        )
    with app.app_context():
        mf.load(data, assets, Path(app.config["MEDIA_ROOT"]))
    return app


def test_card_photos_newest_priciest_and_random(fabric_shop: Flask) -> None:
    from app.catalog import queries

    with fabric_shop.app_context():
        assert [p.slug for p in queries.newest_fabrics(8)] == [
            "kente-new",
            "ankara-blue",
            "kente-dear",
            "kente-old",
        ]
        assert [p.slug for p in queries.priciest_fabrics(8)] == [
            "kente-dear",
            "kente-old",
            "ankara-blue",
            "kente-new",
        ]
        # Only active fabrics with photos: never the cushion or the draft.
        picks = {queries.random_fabric().slug for _ in range(25)}  # type: ignore[union-attr]
        assert picks <= {"kente-new", "ankara-blue", "kente-dear", "kente-old"} and len(picks) > 1


def test_explore_cards_cycle_product_photos(fabric_shop: Flask) -> None:
    html = fabric_shop.test_client().get("/").get_data(as_text=True)
    section = html.split('id="home-explore"', 1)[1].split("</section>", 1)[0]
    cards = re.split(r'<(?:a|article) class="gg-home-opening-card', section)[1:]
    find, wish, prints, models = cards
    assert find.count("data-gg-cycle-item") == 4 and "data-gg-cycle" in find
    assert wish.count("data-gg-cycle-item") == 4
    # Newest first on Find Fabrics, most expensive first on Wishlist.
    assert find.index("kente-new") < find.index("kente-old")
    assert wish.index("kente-dear") < wish.index("kente-new")
    # 3D Models: one random print, not cycling; African Prints keeps its photo.
    assert "data-gg-cycle" not in models and models.count("<img") == 1
    assert "/media/" in models and "open-african-prints" in prints


def test_home_is_eight_framed_slides_with_icon_pause(shop: FlaskClient) -> None:
    html = shop.get("/").get_data(as_text=True)
    assert (
        html.count("data-gg-section>") == 8 and len(re.findall(r'class="gg-slide[ "]', html)) == 8
    )
    pause = html.split("data-gg-slider-pause ", 1)[1].split("</button>", 1)[0]
    assert "Pause slideshow" in pause and "<svg" in pause

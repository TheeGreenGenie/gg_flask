from __future__ import annotations

import re

import pytest
from flask.testing import FlaskClient

from app import create_app
from app.config import ProductionConfig
from app.templating import money

PAGES = [
    "/prototype/",
    "/prototype/home",
    "/prototype/shop",
    "/prototype/shop?category=adire",
    "/prototype/shop?page_key=sale",
    "/prototype/shop?q=cushion&in_stock=1&sort=price-asc",
    "/prototype/product/african-ankara-print-101025/",
    "/prototype/product/wrap-skirt-wr-2002-101196/",
    "/prototype/cart",
    "/prototype/checkout",
]


@pytest.mark.parametrize("path", PAGES)
def test_prototype_pages_render(client: FlaskClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Shared landmarks and skip link on every page.
    for marker in ('class="gg-skip-link"', "<header", 'id="main"', "<footer", 'aria-label="Main"'):
        assert marker in html, marker


@pytest.mark.parametrize("path", PAGES)
def test_pages_have_no_inline_script_or_style(client: FlaskClient, path: str) -> None:
    """The CSP forbids inline scripts and style attributes; markup must not rely on them."""
    html = client.get(path).get_data(as_text=True)
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script>"
    assert not re.search(r"\sstyle=", html), "inline style attribute"
    assert "<style" not in html


def test_unknown_product_is_404(client: FlaskClient) -> None:
    assert client.get("/prototype/product/nope/").status_code == 404


def test_shop_filters_and_empty_state(client: FlaskClient) -> None:
    html = client.get("/prototype/shop?q=zzzz-no-match").get_data(as_text=True)
    assert "Nothing matches those filters" in html
    sale = client.get("/prototype/shop?page_key=sale").get_data(as_text=True)
    assert "Cushion Cover G-3" in sale
    assert "Adire Print" not in sale


def test_shop_bad_params_are_safe(client: FlaskClient) -> None:
    resp = client.get("/prototype/shop?page=-5&sort=drop-table&category=../../etc")
    assert resp.status_code == 200


def test_sold_out_variant_is_disabled(client: FlaskClient) -> None:
    html = client.get("/prototype/product/african-ankara-print-101025/").get_data(as_text=True)
    assert re.search(r'value="101025-3"[^>]*disabled', html.replace("\n", " "))


def test_forms_carry_csrf_token(client: FlaskClient) -> None:
    html = client.get("/prototype/product/african-ankara-print-101025/").get_data(as_text=True)
    assert html.count('name="csrf_token"') >= 2  # add-to-cart + newsletter


def test_newsletter_redirect_ignores_foreign_referrer(client: FlaskClient) -> None:
    resp = client.post(
        "/prototype/newsletter",
        data={"email": "a@example.com"},
        headers={"Referer": "https://evil.example/phish"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/prototype/home")


def test_prototypes_not_registered_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    app = create_app(ProductionConfig())
    assert app.test_client().get("/prototype/home").status_code == 404


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(0, "$0.00"), (899, "$8.99"), (150000, "$1,500.00"), (-250, "-$2.50"), (5, "$0.05")],
)
def test_money_formatting(cents: int, expected: str) -> None:
    assert money(cents) == expected


def test_money_rejects_other_currencies() -> None:
    with pytest.raises(ValueError):
        money(100, "EUR")

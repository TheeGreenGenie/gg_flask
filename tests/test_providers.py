# ruff: noqa: S105, S106  (fake provider credentials for tests)
from __future__ import annotations

import json
import urllib.parse
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.cart import service as cart_service
from app.catalog.models import Variant
from app.extensions import db
from app.orders import providers, service
from app.orders.models import PaymentProvider
from tests.test_orders import _variant_id, address


class FakeHTTP:
    """Records provider calls and returns canned JSON (or raises)."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> dict[str, Any]:
        self.calls.append((url, headers, body))
        for fragment, response in self.responses.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response  # type: ignore[no-any-return]
        raise AssertionError(f"unexpected call {url}")

    def form(self, index: int = -1) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(self.calls[index][2].decode()))


def _rate(provider: str, amount: str, ref: str, service: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "amount": amount,
        "currency": "USD",
        "object_id": ref,
        "servicelevel": {"name": service},
    }


SHIPPO_RATES = {
    "rates": [
        _rate("USPS", "9.35", "r_ground", "Ground Advantage"),
        _rate("USPS", "12.10", "r_priority", "Priority Mail"),
        _rate("UPS", "7.00", "r_ups", "Ground"),
    ]
}


@pytest.fixture
def shippo(catalog_app: Flask) -> Flask:
    catalog_app.config.update(
        SHIPPING_BACKEND="shippo",
        SHIPPO_API_TOKEN="shippo_test_x",
        SHIP_FROM_STREET="1 Example St",
        SHIP_FROM_CITY="Philadelphia",
        SHIP_FROM_STATE="PA",
        SHIP_FROM_ZIP="19143",
    )
    return catalog_app


# ---------- Shippo ----------


def test_shippo_picks_cheapest_usps_rate(shippo: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeHTTP({"/shipments/": SHIPPO_RATES})
    monkeypatch.setattr(providers, "_http", fake)
    with shippo.app_context():
        quote = providers.quote_shipping(address(), [])
    assert quote == providers.ShippingQuote(935, "USPS", "Ground Advantage", "r_ground")
    url, headers, body = fake.calls[0]
    assert headers["Authorization"] == "ShippoToken shippo_test_x"
    sent = json.loads(body)
    assert sent["address_from"]["zip"] == "19143" and sent["address_to"]["zip"] == "15222"


def test_shippo_weight_uses_variant_weight_or_default(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeHTTP({"/shipments/": SHIPPO_RATES})
    monkeypatch.setattr(providers, "_http", fake)
    with shippo.app_context():
        light = Variant(sku="L", price_cents=1, weight_grams=200)
        unknown = Variant(sku="U", price_cents=1, weight_grams=None)
        providers.quote_shipping(address(), [(light, 2), (unknown, 1)])
    weight = json.loads(fake.calls[0][2])["parcels"][0]["weight"]
    assert (
        int(weight)
        == 2 * 200 + providers.DEFAULT_ITEM_WEIGHT_GRAMS + providers.PACKAGING_WEIGHT_GRAMS
    )


def test_philadelphia_keeps_local_rate_without_calling_shippo(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeHTTP({})
    monkeypatch.setattr(providers, "_http", fake)
    with shippo.app_context():
        assert providers.quote_shipping(address("Philadelphia", "19103"), []).cents == 500
    assert fake.calls == []


def test_shippo_failure_falls_back_to_flat_rate(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        providers, "_http", FakeHTTP({"/shipments/": providers.ProviderError("HTTP 503")})
    )
    with shippo.app_context():
        quote = providers.quote_shipping(address(), [])
    assert (quote.cents, quote.service) == (799, "Flat rate")


def test_incomplete_ship_from_falls_back(shippo: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    shippo.config["SHIP_FROM_ZIP"] = None
    monkeypatch.setattr(providers, "_http", FakeHTTP({}))
    with shippo.app_context():
        assert providers.quote_shipping(address(), []).service == "Flat rate"


def test_handling_fee_is_added_to_live_rates(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    shippo.config["SHIPPING_HANDLING_CENTS"] = 150
    monkeypatch.setattr(providers, "_http", FakeHTTP({"/shipments/": SHIPPO_RATES}))
    with shippo.app_context():
        assert providers.quote_shipping(address(), []).cents == 935 + 150


# ---------- Stripe Tax ----------


def test_discount_allocation_keeps_every_cent() -> None:
    assert providers.allocate_discount([1000, 500, 1], 301) == [800, 400, 0]
    assert sum(providers.allocate_discount([333, 333, 334], 100)) == 900
    assert providers.allocate_discount([500], 0) == [500]


def test_stripe_tax_sends_lines_shipping_and_address(
    catalog_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_app.config.update(TAX_BACKEND="stripe", STRIPE_SECRET_KEY="sk_test_x")
    fake = FakeHTTP({"/tax/calculations": {"id": "taxcalc_1", "tax_amount_exclusive": 72}})
    monkeypatch.setattr(providers, "_http", fake)
    with catalog_app.app_context():
        quote = providers.calculate_tax(
            address("Philadelphia", "19103"),
            [
                providers.TaxLine("101001", 899, 1, "fabric"),
                providers.TaxLine("SK-1", 2500, 1, "clothing"),
            ],
            500,
        )
    assert quote == providers.TaxQuote(72, "taxcalc_1")
    form = fake.form()
    assert form["customer_details[address][postal_code]"] == "19103"
    assert form["shipping_cost[amount]"] == "500"
    assert form["line_items[1][tax_code]"] == "txcd_30011000"
    assert form["line_items[0][tax_behavior]"] == "exclusive"


def test_stripe_tax_failure_blocks_the_order(
    catalog_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_app.config.update(TAX_BACKEND="stripe", STRIPE_SECRET_KEY="sk_test_x")
    monkeypatch.setattr(
        providers, "_http", FakeHTTP({"/tax/": providers.ProviderError("HTTP 500")})
    )
    with catalog_app.app_context(), pytest.raises(service.CheckoutError, match="sales tax"):
        providers.calculate_tax(address(), [providers.TaxLine("x", 100, 1, "fabric")], 0)


def test_tax_not_configured_is_refused(catalog_app: Flask) -> None:
    catalog_app.config["TAX_BACKEND"] = None
    with catalog_app.app_context(), pytest.raises(service.CheckoutError, match="not configured"):
        providers.calculate_tax(address(), [], 0)


def test_order_stores_rate_and_tax_and_records_transaction(
    shop: FlaskClient, catalog_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_app.config.update(
        TAX_BACKEND="stripe",
        STRIPE_SECRET_KEY="sk_test_x",
        SHIPPING_BACKEND="shippo",
        SHIPPO_API_TOKEN="t",
        SHIP_FROM_STREET="1 Example St",
        SHIP_FROM_CITY="Philadelphia",
        SHIP_FROM_STATE="PA",
        SHIP_FROM_ZIP="19143",
    )
    fake = FakeHTTP(
        {
            "/shipments/": SHIPPO_RATES,
            "/tax/calculations": {"id": "taxcalc_9", "tax_amount_exclusive": 108},
            "/create_from_calculation": {"id": "tax_txn_9"},
            "/create_reversal": {"id": "tax_rev_9"},
        }
    )
    monkeypatch.setattr(providers, "_http", fake)
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 1})
    cookie = shop.get_cookie(cart_service.COOKIE)
    with catalog_app.test_request_context(
        "/checkout/", headers={"Cookie": f"{cookie.key}={cookie.value}"}
    ):
        order = service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.STRIPE, "t" * 32
        )
        assert (order.shipping_cents, order.shipping_service, order.shipping_rate_ref) == (
            935,
            "Ground Advantage",
            "r_ground",
        )
        assert order.tax_cents == 108 and order.tax_calculation_ref == "taxcalc_9"
        assert order.total_cents == 899 + 935 + 108

        from app.orders import payments

        payments.complete(order, "pi_test")
        assert order.tax_transaction_ref == "tax_txn_9"
        assert fake.form()["reference"] == order.public_id

        catalog_app.config["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        refund_reply = {
            "id": "re_1",
            "status": "succeeded",
            "amount": order.total_cents,
            "currency": "usd",
        }
        monkeypatch.setattr(payments, "_request", lambda *a, **k: refund_reply)
        assert payments.refund(order) == "completed"
        assert any("/create_reversal" in call[0] for call in fake.calls)
        db.session.rollback()


# ---------- Google Analytics 4 ----------


def test_no_analytics_without_measurement_id(client: FlaskClient) -> None:
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "analytics.js" not in html and "data-gg-consent" not in html
    assert "googletagmanager" not in resp.headers["Content-Security-Policy"]


def test_analytics_waits_for_consent(catalog_app: Flask) -> None:
    catalog_app.config["GA4_MEASUREMENT_ID"] = "G-TEST123"
    from app.security import build_csp

    client = catalog_app.test_client()
    html = client.get("/").get_data(as_text=True)
    # The page never references Google directly; analytics.js adds gtag only after "Accept".
    assert '<meta name="gg-ga4" content="G-TEST123">' in html
    assert "googletagmanager.com/gtag" not in html
    assert "data-gg-consent hidden" in html.replace(
        'role="region" aria-label="Cookie choices" ', ""
    )
    assert "https://www.googletagmanager.com" in build_csp(analytics=True)


def test_product_page_describes_view_item_event(catalog_app: Flask) -> None:
    catalog_app.config["GA4_MEASUREMENT_ID"] = "G-TEST123"
    html = catalog_app.test_client().get("/product/ankara-blue/").get_data(as_text=True)
    assert "data-gg-analytics-event" in html and "view_item" in html and "101001" in html


# ---------- Standard / Express (owner, 2026-10-27) ----------

EXPRESS_RATES = {
    "rates": [
        {**_rate("USPS", "9.35", "r_ground", "Ground Advantage"), "estimated_days": 4,
         "servicelevel": {"name": "Ground Advantage", "token": "usps_ground_advantage"}},
        {**_rate("USPS", "34.10", "r_express", "Priority Mail Express"), "estimated_days": 1,
         "servicelevel": {"name": "Priority Mail Express", "token": "usps_priority_express"}},
    ]
}  # fmt: skip


def test_standard_never_picks_express_and_shows_days(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(providers, "_http", FakeHTTP({"/shipments/": EXPRESS_RATES}))
    with shippo.app_context():
        quote = providers.quote_shipping(address(), [], providers.STANDARD)
    assert (quote.cents, quote.rate_ref) == (935, "r_ground")
    assert quote.service == "Ground Advantage (about 4 business days)"


def test_express_picks_priority_mail_express(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(providers, "_http", FakeHTTP({"/shipments/": EXPRESS_RATES}))
    with shippo.app_context():
        quote = providers.quote_shipping(address("Philadelphia", "19103"), [], providers.EXPRESS)
    # The Philadelphia local rate is a standard rate; express always uses the live price.
    assert (quote.cents, quote.rate_ref) == (3410, "r_express")
    assert quote.service.startswith("Priority Mail Express")


def test_express_unavailable_is_a_clear_checkout_error(
    shippo: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(providers, "_http", FakeHTTP({"/shipments/": SHIPPO_RATES}))
    with shippo.app_context(), pytest.raises(service.CheckoutError, match="choose Standard"):
        providers.quote_shipping(address(), [], providers.EXPRESS)
    shippo.config["SHIPPING_BACKEND"] = "flat"
    with shippo.app_context(), pytest.raises(service.CheckoutError, match="choose Standard"):
        providers.quote_shipping(address(), [], providers.EXPRESS)
    with shippo.app_context(), pytest.raises(service.CheckoutError, match="Standard or Express"):
        providers.quote_shipping(address(), [], "overnight-please")


def test_checkout_offers_express_only_with_live_rates(
    shop: FlaskClient, catalog_app: Flask
) -> None:
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 1})
    html = shop.get("/checkout/").get_data(as_text=True)
    assert 'value="standard"' in html and 'value="express"' not in html
    catalog_app.config.update(SHIPPING_BACKEND="shippo", SHIPPO_API_TOKEN="t")
    html = shop.get("/checkout/").get_data(as_text=True)
    assert 'value="express"' in html and "Priority Mail Express" in html


# Weight bands and free shipping (owner, 2026-10-30) --------------------------------------

BANDS = ((3.0, 999), (8.0, 1699), (15.0, 2499), (25.0, 3499))
ONE_YARD_GRAMS = 560  # roughly one 1-yard precut piece, before packaging


def _cut(price_cents: int = 1200) -> Variant:
    return Variant(sku="CUT", price_cents=price_cents, weight_grams=ONE_YARD_GRAMS)


def test_weight_bands_count_every_piece_ordered(app: Flask) -> None:
    app.config.update(SHIPPING_WEIGHT_RATES=BANDS, SHIPPING_EXTRA_LB_CENTS=125)
    with app.app_context():
        one = providers.quote_shipping(address(), [(_cut(), 1)])
        two = providers.quote_shipping(address(), [(_cut(), 2)])
        five = providers.quote_shipping(address(), [(_cut(), 5)])  # ~6.4 lb with packaging
        many = providers.quote_shipping(address(), [(_cut(), 25)])  # ~31 lb
    assert one.cents == 999 and two.cents == 999  # ~1.5 lb and ~2.7 lb
    assert five.cents == 1699  # five cuts weigh as much as a bundle, and cost like one
    assert many.cents == 3499 + 7 * 125  # past 25 lb: each extra started pound adds $1.25
    assert one.service == "Standard (by weight)"


def test_without_bands_the_flat_rate_still_applies(app: Flask) -> None:
    app.config["SHIPPING_WEIGHT_RATES"] = ()
    with app.app_context():
        assert providers.quote_shipping(address(), [(_cut(), 5)]).cents == 799


def test_free_standard_shipping_from_400_dollars_even_in_philadelphia(app: Flask) -> None:
    app.config.update(SHIPPING_WEIGHT_RATES=BANDS, FREE_SHIPPING_FROM_CENTS=40000)
    with app.app_context():
        just_under = providers.quote_shipping(address(), [(_cut(39999), 1)])
        exactly = providers.quote_shipping(address(), [(_cut(20000), 2)])
        philly = providers.quote_shipping(address("Philadelphia", "19103"), [(_cut(40000), 1)])
    assert just_under.cents == 999
    assert exactly.cents == 0 and exactly.service == "Free shipping"
    assert philly.cents == 0


def test_free_shipping_can_be_turned_off(app: Flask) -> None:
    app.config.update(SHIPPING_WEIGHT_RATES=BANDS, FREE_SHIPPING_FROM_CENTS=0)
    with app.app_context():
        assert providers.quote_shipping(address(), [(_cut(50000), 1)]).cents == 999


def test_bad_band_settings_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import ConfigError, _env_weight_rates

    monkeypatch.setenv("SHIPPING_WEIGHT_RATES", "3:999, 8:1699")
    assert _env_weight_rates("SHIPPING_WEIGHT_RATES") == ((3.0, 999), (8.0, 1699))
    for bad in ("8:1699,3:999", "3:999,8:500", "three:999", "3:"):
        monkeypatch.setenv("SHIPPING_WEIGHT_RATES", bad)
        with pytest.raises(ConfigError):
            _env_weight_rates("SHIPPING_WEIGHT_RATES")


def test_negative_shipping_amounts_stop_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import ConfigError, DevelopmentConfig

    for name in ("SHIPPING_EXTRA_LB_CENTS", "FREE_SHIPPING_FROM_CENTS"):
        monkeypatch.setenv(name, "-1")
        with pytest.raises(ConfigError, match="0 or more"):
            DevelopmentConfig()
        monkeypatch.delenv(name)
    monkeypatch.setenv("FREE_SHIPPING_FROM_CENTS", "0")  # 0 = free shipping off: allowed
    assert DevelopmentConfig().FREE_SHIPPING_FROM_CENTS == 0

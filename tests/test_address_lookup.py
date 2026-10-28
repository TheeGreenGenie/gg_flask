"""Address autocomplete: HERE proxy with per-IP limit, daily cap, cache, fail-closed."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import address_lookup
from tests.conftest import FakeRedis

HERE_REPLY = {
    "items": [
        {
            "title": "1500 Market St, Philadelphia, PA 19102, United States",
            "address": {
                "label": "1500 Market St, Philadelphia, PA 19102, United States",
                "countryCode": "USA",
                "stateCode": "PA",
                "state": "Pennsylvania",
                "city": "Philadelphia",
                "street": "Market St",
                "postalCode": "19102",
                "houseNumber": "1500",
            },
        },
        {"title": "somewhere abroad", "address": {"countryCode": "FRA", "street": "Rue X"}},
    ]
}


@pytest.fixture
def here(app: Flask, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fresh counters, a configured key, and a fake HERE that records each paid call."""
    app.extensions["redis"] = FakeRedis()
    app.config.update(HERE_API_KEY="test-key", SUPPORTED_COUNTRIES=("US", "CA", "MX"))
    calls: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> Any:
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(HERE_REPLY).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _ask(client: FlaskClient, q: str = "1500 market", **headers: str):  # type: ignore[no-untyped-def]
    return client.get("/api/v1/address-suggestions", query_string={"q": q}, headers=headers)


def test_suggestions_are_tidy_and_only_for_shipped_countries(
    client: FlaskClient, here: list[str]
) -> None:
    response = _ask(client)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.get_json()["items"] == [
        {
            "label": "1500 Market St, Philadelphia, PA 19102, United States",
            "address1": "1500 Market St",
            "city": "Philadelphia",
            "region": "PA",
            "postcode": "19102",
            "country": "US",
        }
    ]
    assert "countryCode%3AUSA%2CCAN%2CMEX" in here[0] and "limit=5" in here[0]


def test_short_queries_cost_nothing_and_repeats_come_from_cache(
    client: FlaskClient, here: list[str]
) -> None:
    assert _ask(client, "15").get_json()["items"] == []
    _ask(client, "1500 Market")
    _ask(client, "  1500   market ")  # same search, different spacing/case
    assert len(here) == 1


def test_thirty_lookups_per_ten_minutes_per_ip(client: FlaskClient, here: list[str]) -> None:
    for i in range(address_lookup.IP_LIMIT):
        assert _ask(client, f"{i} main street").status_code == 200
    blocked = _ask(client, "999 main street")
    assert blocked.status_code == 429 and blocked.get_json()["retry_after"] == 600
    assert len(here) == address_lookup.IP_LIMIT


def test_daily_cap_bounds_paid_lookups_across_all_visitors(
    app: Flask, client: FlaskClient, here: list[str]
) -> None:
    app.config["ADDRESS_LOOKUP_DAILY_CAP"] = 2
    assert _ask(client, "1 a street").status_code == 200
    assert _ask(client, "2 b street").status_code == 200
    assert _ask(client, "3 c street").status_code == 503
    assert len(here) == 2
    assert _ask(client, "1 a street").status_code == 200  # cached answers stay free


def test_fails_closed_without_key_or_redis(
    app: Flask, client: FlaskClient, here: list[str]
) -> None:
    app.extensions["redis"] = FakeRedis(healthy=False)
    assert _ask(client).status_code == 503
    app.extensions["redis"] = None
    assert _ask(client).status_code == 503
    app.extensions["redis"] = FakeRedis()
    app.config["HERE_API_KEY"] = None
    assert _ask(client).status_code == 503
    assert here == []


def test_other_websites_cannot_spend_lookups(client: FlaskClient, here: list[str]) -> None:
    assert _ask(client, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert here == []


def test_upstream_failure_returns_no_suggestions_and_never_logs_the_key(
    client: FlaskClient, here: list[str], monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    def broken(request: urllib.request.Request, timeout: float) -> Any:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    response = _ask(client, "77 broadway")
    assert response.status_code == 200 and response.get_json()["items"] == []
    assert "test-key" not in caplog.text and "broadway" not in caplog.text.lower()


def test_forms_load_the_script_only_when_configured(app: Flask, catalog_app: Flask) -> None:
    shop = catalog_app.test_client()
    shop.post("/cart/add", data={"variant": _first_variant(catalog_app), "quantity": 1})
    catalog_app.config["HERE_API_KEY"] = None
    assert b"address-autocomplete.js" not in shop.get("/checkout/").data
    catalog_app.config["HERE_API_KEY"] = "test-key"
    page = shop.get("/checkout/").data
    assert b"address-autocomplete.js" in page and b"data-gg-address" in page


def _first_variant(app: Flask) -> int:
    from app.catalog.models import Variant
    from app.extensions import db

    with app.app_context():
        return int(db.session.query(Variant.id).filter(Variant.stock_quantity > 0).first()[0])

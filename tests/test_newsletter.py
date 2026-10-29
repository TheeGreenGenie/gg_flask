"""Newsletter signups: double opt-in for everyone except proven customers, and bot resistance."""

from __future__ import annotations

import json

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.orders.models import Order, PaymentStatus
from tests.conftest import FakeRedis
from tests.test_accounts import register, user
from tests.test_order_shipping import _paid_order

Call = tuple[str, str, dict[str, str], bytes]


@pytest.fixture
def mailchimp(catalog_app: Flask) -> list[Call]:
    """Records every Mailchimp request; a test can queue status codes to answer with."""
    calls: list[Call] = []
    catalog_app.config.update(
        MAILCHIMP_API_KEY="test-key", MAILCHIMP_SERVER_PREFIX="us1", MAILCHIMP_LIST_ID="audience"
    )
    answers: list[int] = []

    def fake(method: str, url: str, headers: dict[str, str], body: bytes) -> int | None:
        calls.append((method, url, headers, body))
        return answers.pop(0) if answers else None

    catalog_app.extensions["marketing_http"] = fake
    catalog_app.extensions["marketing_answers"] = answers
    return calls


def _bodies(calls: list[Call]) -> list[dict[str, str]]:
    return [json.loads(call[3]) for call in calls]


def _signed_in_customer(app: Flask, *, paid: PaymentStatus | None) -> FlaskClient:
    shop = app.test_client()
    register(shop)  # verified and signed in
    if paid is not None:
        order_id = _paid_order(app)
        with app.app_context():
            order = db.session.get(Order, order_id)
            order.user_id, order.payment_status = user(app).id, paid
            db.session.commit()
    return shop


def _flash(shop: FlaskClient) -> str:
    return shop.get("/").get_data(as_text=True)


def test_visitors_must_confirm_by_email(shop: FlaskClient, mailchimp: list[Call]) -> None:
    response = shop.post("/newsletter", data={"email": "Reader@Example.com"})
    assert response.status_code == 302
    assert _bodies(mailchimp) == [
        {"email_address": "reader@example.com", "status_if_new": "pending"}
    ]
    assert "Check your email to confirm" in _flash(shop)


@pytest.mark.parametrize("paid", [PaymentStatus.PAID, PaymentStatus.REFUNDED])
def test_signed_in_customers_with_a_purchase_skip_the_confirmation(
    catalog_app: Flask, mailchimp: list[Call], paid: PaymentStatus
) -> None:
    shop = _signed_in_customer(catalog_app, paid=paid)
    shop.post("/newsletter", data={"email": "ADA@example.com"})
    assert _bodies(mailchimp) == [
        {"email_address": "ada@example.com", "status_if_new": "subscribed", "status": "subscribed"}
    ]
    assert "You&#39;re subscribed" in _flash(shop)


@pytest.mark.parametrize("paid", [None, PaymentStatus.PENDING, PaymentStatus.FAILED])
def test_customers_without_a_completed_purchase_still_confirm(
    catalog_app: Flask, mailchimp: list[Call], paid: PaymentStatus | None
) -> None:
    shop = _signed_in_customer(catalog_app, paid=paid)
    shop.post("/newsletter", data={"email": "ada@example.com"})
    assert _bodies(mailchimp) == [{"email_address": "ada@example.com", "status_if_new": "pending"}]


def test_a_customer_cannot_skip_confirmation_for_someone_elses_address(
    catalog_app: Flask, mailchimp: list[Call]
) -> None:
    shop = _signed_in_customer(catalog_app, paid=PaymentStatus.PAID)
    shop.post("/newsletter", data={"email": "victim@example.com"})
    assert _bodies(mailchimp) == [
        {"email_address": "victim@example.com", "status_if_new": "pending"}
    ]


def test_signed_out_purchasers_confirm_by_email(catalog_app: Flask, mailchimp: list[Call]) -> None:
    shop = _signed_in_customer(catalog_app, paid=PaymentStatus.PAID)
    shop.post("/account/logout")
    shop.post("/newsletter", data={"email": "ada@example.com"})
    assert _bodies(mailchimp)[-1]["status_if_new"] == "pending"


def test_a_customer_who_unsubscribed_gets_a_fresh_confirmation(
    catalog_app: Flask, mailchimp: list[Call]
) -> None:
    shop = _signed_in_customer(catalog_app, paid=PaymentStatus.PAID)
    catalog_app.extensions["marketing_answers"].append(400)  # Mailchimp: compliance state
    shop.post("/newsletter", data={"email": "ada@example.com"})
    assert [body.get("status") for body in _bodies(mailchimp)] == ["subscribed", "pending"]
    assert "Check your email to confirm" in _flash(shop)


def test_rejected_addresses_get_a_clear_message(shop: FlaskClient, mailchimp: list[Call]) -> None:
    shop.application.extensions["marketing_answers"].append(400)
    shop.post("/newsletter", data={"email": "reader@example.com"})
    assert "couldn&#39;t sign up that email address" in _flash(shop)


def test_bots_filling_the_hidden_field_are_ignored(
    shop: FlaskClient, mailchimp: list[Call]
) -> None:
    form = shop.get("/").get_data(as_text=True)
    assert 'name="gg_hp"' in form and 'tabindex="-1"' in form
    shop.post("/newsletter", data={"email": "victim@example.com", "gg_hp": "http://spam"})
    assert mailchimp == []
    assert "Check your email to confirm" in _flash(shop)  # bots learn nothing


def test_site_wide_hourly_cap_on_confirmation_emails(
    catalog_app: Flask, mailchimp: list[Call]
) -> None:
    catalog_app.config["NEWSLETTER_HOURLY_CAP"] = 2
    for n in range(3):  # a fresh client each time, as a bot spread over many addresses would be
        catalog_app.test_client().post("/newsletter", data={"email": f"r{n}@example.com"})
    assert len(mailchimp) == 2


def test_proven_customers_are_not_blocked_by_the_site_wide_cap(
    catalog_app: Flask, mailchimp: list[Call]
) -> None:
    catalog_app.config["NEWSLETTER_HOURLY_CAP"] = 0
    shop = _signed_in_customer(catalog_app, paid=PaymentStatus.PAID)
    shop.post("/newsletter", data={"email": "ada@example.com"})
    assert _bodies(mailchimp)[0]["status"] == "subscribed"


def test_per_visitor_limit(shop: FlaskClient, mailchimp: list[Call]) -> None:
    for n in range(6):
        shop.post("/newsletter", data={"email": f"r{n}@example.com"})
    assert len(mailchimp) == 5


def test_signups_are_refused_while_limits_cannot_be_counted(
    catalog_app: Flask, mailchimp: list[Call]
) -> None:
    catalog_app.extensions["redis"] = FakeRedis(healthy=False)
    shop = catalog_app.test_client()
    shop.post("/newsletter", data={"email": "reader@example.com"})
    assert mailchimp == []


def test_even_proven_customers_wait_while_limits_cannot_be_counted(
    catalog_app: Flask, mailchimp: list[Call]
) -> None:
    shop = _signed_in_customer(catalog_app, paid=PaymentStatus.PAID)
    catalog_app.extensions["redis"] = FakeRedis(healthy=False)
    shop.post("/newsletter", data={"email": "ada@example.com"})
    assert mailchimp == []

"""Orders count as paid or refunded only for the exact amount and currency of the order."""

from __future__ import annotations

import json
from typing import Any

import pytest
from flask import Flask

from app.cart import service as cart_service
from app.catalog.models import Variant
from app.extensions import db
from app.orders import payments, service
from app.orders.models import Order, OrderStatus, PaymentProvider, PaymentStatus
from tests.test_orders import _cart_context, _variant_id, address

_contexts: list[Any] = []


@pytest.fixture(autouse=True)
def _close_contexts() -> Any:
    """Each test works inside one checkout request; always close it so nothing leaks."""
    yield
    while _contexts:
        _contexts.pop().pop()


def _order(app: Flask, provider: PaymentProvider = PaymentProvider.STRIPE) -> Order:
    shop = app.test_client()
    shop.post("/cart/add", data={"variant": _variant_id(app, "101001"), "quantity": 1})
    context = _cart_context(app, shop)
    context.push()
    _contexts.append(context)
    return service.create_order(cart_service.current_cart(), address(), provider, "p" * 32)


def _stripe_paid(order: Order, **changes: Any) -> dict[str, Any]:
    obj = {
        "amount_total": order.total_cents,
        "currency": "usd",
        "payment_status": "paid",
        "payment_intent": "pi_1",
        "metadata": {"order_id": order.public_id},
    }
    obj.update(changes)
    return obj


@pytest.mark.parametrize(
    "changes",
    [{"currency": "eur"}, {"currency": None}, {"amount_total": 1}, {"amount_total": None}],
)
def test_stripe_payment_with_wrong_amount_or_currency_is_refused(
    catalog_app: Flask, changes: dict[str, Any]
) -> None:
    order = _order(catalog_app)
    obj = {k: v for k, v in _stripe_paid(order, **changes).items() if v is not None}
    with pytest.raises(payments.PaymentError, match="amount or currency"):
        payments._record_and_process(
            PaymentProvider.STRIPE,
            "evt_x",
            "checkout.session.completed",
            order.public_id,
            b"{}",
            obj,
        )
    db.session.refresh(order)
    assert order.payment_status == PaymentStatus.PENDING
    assert db.session.get(Variant, _variant_id(catalog_app, "101001")).stock_quantity == 5


def test_paypal_webhook_checks_amount_and_currency(catalog_app: Flask) -> None:
    order = _order(catalog_app, PaymentProvider.PAYPAL)
    value = f"{order.total_cents / 100:.2f}"
    wrong = {
        "id": "cap_1",
        "custom_id": order.public_id,
        "amount": {"currency_code": "USD", "value": "0.01"},
    }
    with pytest.raises(payments.PaymentError, match="amount or currency"):
        payments._record_and_process(
            PaymentProvider.PAYPAL,
            "WH-1",
            "PAYMENT.CAPTURE.COMPLETED",
            order.public_id,
            b"{}",
            wrong,
        )
    right = {**wrong, "amount": {"currency_code": "USD", "value": value}}
    assert payments._record_and_process(
        PaymentProvider.PAYPAL, "WH-2", "PAYMENT.CAPTURE.COMPLETED", order.public_id, b"{}", right
    )
    assert order.payment_status == PaymentStatus.PAID and order.provider_reference == "cap_1"


def test_paypal_capture_for_a_different_amount_is_refused(catalog_app: Flask) -> None:
    order = _order(catalog_app, PaymentProvider.PAYPAL)
    order.provider_reference = "PAYPAL-ORDER-1"
    catalog_app.config.update(PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET="secret")  # noqa: S106

    def fake(method: str, url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        if url.endswith("/oauth2/token"):
            return {"access_token": "token"}
        capture = {
            "id": "cap_9",
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "1.00"},
        }
        return {"status": "COMPLETED", "purchase_units": [{"payments": {"captures": [capture]}}]}

    catalog_app.extensions["payment_http"] = fake
    with pytest.raises(payments.PaymentError, match="amount or currency"):
        payments.capture_paypal(order)
    assert order.payment_status == PaymentStatus.PENDING


def _paid(app: Flask) -> Order:
    order = _order(app)
    obj = _stripe_paid(order)
    payments._record_and_process(
        PaymentProvider.STRIPE,
        "evt_paid",
        "checkout.session.completed",
        order.public_id,
        b"{}",
        obj,
    )
    app.config.update(STRIPE_SECRET_KEY="sk_test_x", STRIPE_WEBHOOK_SECRET="whsec_x")  # noqa: S106
    return order


def test_pending_refund_stays_paid_until_the_provider_confirms(catalog_app: Flask) -> None:
    order = _paid(catalog_app)
    replies = [{"id": "re_1", "status": "pending", "amount": order.total_cents, "currency": "usd"}]
    catalog_app.extensions["payment_http"] = lambda *a: replies[-1]
    assert payments.refund(order) == "pending"
    assert order.payment_status == PaymentStatus.PAID and order.status == OrderStatus.PAID
    assert (order.refund_reference, order.refund_status) == ("re_1", "pending")
    with pytest.raises(payments.PaymentError, match="already in progress"):
        payments.refund(order)

    assert payments.sync_refunds()["pending"] == 1  # still processing
    replies.append({**replies[0], "status": "succeeded"})
    assert payments.sync_refunds()["completed"] == 1
    db.session.refresh(order)
    assert order.payment_status == PaymentStatus.REFUNDED and order.status == OrderStatus.REFUNDED


@pytest.mark.parametrize(
    "reply",
    [
        {"id": "re_2", "status": "failed"},
        {"id": "re_3", "status": "succeeded", "amount": 1, "currency": "usd"},
        {"status": "succeeded"},  # no refund id
    ],
)
def test_failed_or_mismatched_refund_never_marks_the_order_refunded(
    catalog_app: Flask, reply: dict[str, Any]
) -> None:
    order = _paid(catalog_app)
    catalog_app.extensions["payment_http"] = lambda *a: reply
    with pytest.raises(payments.PaymentError, match="did not (complete|confirm) the refund"):
        payments.refund(order)
    db.session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID and order.refunded_cents == 0


def test_paypal_amount_parsing_is_exact() -> None:
    assert payments._paypal_amount({"currency_code": "USD", "value": "17.99"}) == (1799, "USD")
    assert payments._paypal_amount({"currency_code": "USD", "value": "17.999"}) == (None, None)
    assert payments._paypal_amount({"value": "abc"}) == (None, None)
    assert payments._paypal_amount(json.loads("null")) == (None, None)

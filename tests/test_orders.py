from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
from flask import Flask
from sqlalchemy import select

from app.cart import service as cart_service
from app.catalog.models import Variant, utcnow
from app.extensions import db
from app.orders import payments, service
from app.orders.models import (
    Coupon,
    DiscountType,
    Order,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    StockHold,
)


def address(city: str = "Pittsburgh", postcode: str = "15222") -> service.Address:
    """Pennsylvania but outside Philadelphia, so the flat rate applies by default."""
    return service.Address(
        email="buyer@example.com",
        first_name="Ada",
        last_name="Buyer",
        address1="123 Market Street",
        address2="",
        city=city,
        region="PA",
        postcode=postcode,
        country="US",
        phone=None,
    )


@pytest.mark.parametrize(
    ("postcode", "expected"),
    [
        ("19103", 500),  # Center City
        ("19143-1234", 500),  # ZIP+4
        ("19019", 500),  # Philadelphia special-purpose ZIP
        ("19010", 799),  # Bryn Mawr: close by, not Philadelphia
        ("15222", 799),  # Pittsburgh: Pennsylvania, not Philadelphia
        ("10001", 799),
    ],
)
def test_philadelphia_gets_local_shipping(catalog_app: Flask, postcode: str, expected: int) -> None:
    with catalog_app.app_context():
        assert service.shipping_cents(address("Philadelphia", postcode)) == expected


def test_local_rate_is_optional(catalog_app: Flask) -> None:
    catalog_app.config["LOCAL_SHIPPING_CENTS"] = None
    with catalog_app.app_context():
        assert service.shipping_cents(address("Philadelphia", "19103")) == 799


def test_philadelphia_order_snapshots_local_rate(shop, catalog_app: Flask) -> None:
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 1})
    cookie = shop.get_cookie(cart_service.COOKIE)
    with catalog_app.test_request_context(
        "/checkout/", headers={"Cookie": f"{cookie.key}={cookie.value}"}
    ):
        cart = cart_service.current_cart()
        order = service.create_order(
            cart, address("Philadelphia", "19103"), PaymentProvider.STRIPE, "p" * 32
        )
        assert order.shipping_cents == 500 and order.total_cents == 899 + 500


def test_address_rejects_unsupported_country(catalog_app: Flask) -> None:
    with (
        catalog_app.test_request_context("/"),
        pytest.raises(service.CheckoutError, match="do not currently ship"),
    ):
        service.address_from_form(
            {
                "email": "buyer@example.com",
                "first_name": "Ada",
                "last_name": "Buyer",
                "address1": "123 Road",
                "city": "Toronto",
                "region": "ON",
                "postcode": "M5V 1A1",
                "country": "CA",
                "phone": "",
            }
        )


def test_order_snapshots_prices_holds_stock_and_is_idempotent(shop, catalog_app: Flask) -> None:
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 2})
    cookie = shop.get_cookie(cart_service.COOKIE)
    with catalog_app.test_request_context(
        "/checkout/", headers={"Cookie": f"{cookie.key}={cookie.value}"}
    ):
        cart = cart_service.current_cart()
        order = service.create_order(cart, address(), PaymentProvider.STRIPE, "a" * 32)
        same = service.create_order(cart, address(), PaymentProvider.STRIPE, "a" * 32)
        assert same.id == order.id
        assert order.subtotal_cents == 1798 and order.shipping_cents == 799
        assert order.total_cents == 2597 and order.lines[0].unit_price_cents == 899
        assert order.holds[0].quantity == 2
        assert db.session.scalar(select(StockHold).where(StockHold.order_id == order.id))


def test_active_hold_prevents_overselling(shop, catalog_app: Flask) -> None:
    vid = _variant_id(catalog_app, "101001")  # stock 5
    first, second = catalog_app.test_client(), catalog_app.test_client()
    first.post("/cart/add", data={"variant": vid, "quantity": 4})
    second.post("/cart/add", data={"variant": vid, "quantity": 2})
    with _cart_context(catalog_app, first):
        service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.STRIPE, "b" * 32
        )
    with _cart_context(catalog_app, second), pytest.raises(service.CheckoutError, match="Only 1"):
        service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.PAYPAL, "c" * 32
        )


def test_coupon_and_expired_hold_release(shop, catalog_app: Flask) -> None:
    with catalog_app.app_context():
        db.session.add(
            Coupon(
                code="SAVE10",
                discount_type=DiscountType.PERCENT,
                amount=10,
                active=True,
                uses=0,
                minimum_subtotal_cents=0,
            )
        )
        db.session.commit()
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 1})
    with _cart_context(catalog_app, shop):
        order = service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.STRIPE, "d" * 32, "save10"
        )
        assert order.discount_cents == 89 and order.total_cents == 1609
        order.holds[0].expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        assert service.release_expired_holds() == 1
        assert order.status == OrderStatus.CANCELLED and order.holds[0].released_at is not None
        assert db.session.scalar(select(Coupon).where(Coupon.code == "SAVE10")).uses == 0


def test_stripe_webhook_is_signed_duplicate_safe_and_consumes_stock(
    shop, catalog_app: Flask
) -> None:
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 2})
    with _cart_context(catalog_app, shop):
        order = service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.STRIPE, "e" * 32
        )
        order_id, public_id = order.id, order.public_id
        body = json.dumps(
            {
                "id": "evt_paid_1",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_1",
                        "amount_total": order.total_cents,
                        "currency": "usd",
                        "payment_status": "paid",
                        "metadata": {"order_id": public_id},
                    }
                },
            },
            separators=(",", ":"),
        ).encode()
        timestamp = int(time.time())
        secret = "whsec_test"  # noqa: S105
        catalog_app.config["STRIPE_WEBHOOK_SECRET"] = secret
        signature = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        header = f"t={timestamp},v1={signature}"
        assert payments.handle_stripe(body, header) is True
        assert payments.handle_stripe(body, header) is False
        paid = db.session.get(Order, order_id)
        variant = db.session.get(Variant, _variant_id(catalog_app, "101001"))
        assert paid.payment_status == PaymentStatus.PAID
        assert variant.stock_quantity == 3
        assert paid.holds[0].consumed_at is not None
        assert not paid.cart.lines
        assert len(catalog_app.extensions["outbox"]) == 1


def test_stripe_webhook_rejects_bad_signature(catalog_app: Flask) -> None:
    catalog_app.config["STRIPE_WEBHOOK_SECRET"] = "whsec_test"  # noqa: S105
    with catalog_app.app_context(), pytest.raises(payments.PaymentError, match="signature"):
        payments.handle_stripe(b"{}", "t=1,v1=bad")


def _variant_id(app: Flask, sku: str) -> int:
    with app.app_context():
        return db.session.scalar(select(Variant.id).where(Variant.sku == sku))


def _cart_context(app: Flask, client):
    cookie = client.get_cookie(cart_service.COOKIE)
    return app.test_request_context(
        "/checkout/", headers={"Cookie": f"{cookie.key}={cookie.value}"}
    )

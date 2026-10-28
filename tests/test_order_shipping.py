"""Marking orders shipped with a pasted tracking number (labels bought outside the app)."""

from __future__ import annotations

from flask import Flask

from app.admin.models import AdminRole
from app.cart import service as cart_service
from app.extensions import db
from app.orders import service
from app.orders.models import Order, OrderStatus, PaymentProvider, PaymentStatus
from tests.test_admin import admin_client
from tests.test_orders import _variant_id, address


def _paid_order(app: Flask) -> int:
    shop = app.test_client()
    shop.post("/cart/add", data={"variant": _variant_id(app, "101001"), "quantity": 1})
    cookie = shop.get_cookie(cart_service.COOKIE)
    assert cookie is not None
    with app.test_request_context("/checkout/", headers={"Cookie": f"{cookie.key}={cookie.value}"}):
        order = service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.STRIPE, "s" * 32
        )
        order.status, order.payment_status = OrderStatus.PAID, PaymentStatus.PAID
        db.session.commit()
        return order.id


def test_marking_shipped_records_tracking_and_emails_the_link(catalog_app: Flask) -> None:
    order_id = _paid_order(catalog_app)
    client = admin_client(catalog_app, AdminRole.SUPPORT)
    page = client.post(
        f"/admin/orders/{order_id}/shipped",
        data={"carrier": "usps", "tracking_number": "9400 1000 0000 0000 0000 00", "notify": "1"},
        follow_redirects=True,
    )
    assert b"customer was emailed the tracking link" in page.data
    with catalog_app.app_context():
        order = db.session.get(Order, order_id)
        assert order.status == OrderStatus.SHIPPED and order.shipped_at is not None
        assert order.tracking_number == "9400100000000000000000"  # spaces removed
        assert order.tracking_url.endswith("tLabels=9400100000000000000000")
        shipped_at, public_id, email = order.shipped_at, order.public_id, order.email
    message = catalog_app.extensions["outbox"][-1]
    assert message.to == email and public_id in message.subject
    assert "tools.usps.com" in message.body and "9400100000000000000000" in message.body

    # Correcting a typo keeps the original ship date and can skip the email.
    sent = len(catalog_app.extensions["outbox"])
    client.post(
        f"/admin/orders/{order_id}/shipped",
        data={"carrier": "ups", "tracking_number": "1Z999AA10123456784"},
    )
    with catalog_app.app_context():
        order = db.session.get(Order, order_id)
        assert order.tracking_carrier == "ups" and order.shipped_at == shipped_at
    assert len(catalog_app.extensions["outbox"]) == sent


def test_bad_tracking_input_and_unpaid_orders_are_refused(catalog_app: Flask) -> None:
    order_id = _paid_order(catalog_app)
    client = admin_client(catalog_app, AdminRole.SUPPORT)
    for data, message in (
        ({"carrier": "pigeon", "tracking_number": "12345678"}, b"Choose the carrier"),
        ({"carrier": "usps", "tracking_number": "12"}, b"Paste the tracking number"),
        (
            {"carrier": "usps", "tracking_number": "<script>alert(1)</script>"},
            b"Paste the tracking",
        ),
    ):
        page = client.post(f"/admin/orders/{order_id}/shipped", data=data, follow_redirects=True)
        assert message in page.data
    with catalog_app.app_context():
        order = db.session.get(Order, order_id)
        assert order.status == OrderStatus.PAID and order.tracking_number is None
        order.status, order.payment_status = OrderStatus.PENDING_PAYMENT, PaymentStatus.PENDING
        db.session.commit()
    page = client.post(
        f"/admin/orders/{order_id}/shipped",
        data={"carrier": "usps", "tracking_number": "94001000000000000000"},
        follow_redirects=True,
    )
    assert b"Only paid orders" in page.data


def test_customer_sees_the_tracking_link_on_their_order(catalog_app: Flask) -> None:
    from tests.test_accounts import register, user

    order_id = _paid_order(catalog_app)
    shop = catalog_app.test_client()
    register(shop)
    with catalog_app.app_context():
        order = db.session.get(Order, order_id)
        order.user_id = user(catalog_app).id
        order.tracking_carrier, order.tracking_number = "fedex", "123456789012"
        db.session.commit()
        public_id = order.public_id
    page = shop.get(f"/account/orders/{public_id}").get_data(as_text=True)
    assert "Track your parcel" in page
    assert "https://www.fedex.com/fedextrack/?trknbr=123456789012" in page
    admin_page = admin_client(catalog_app).get(f"/admin/orders/{order_id}").get_data(as_text=True)
    assert "Save corrected tracking" in admin_page

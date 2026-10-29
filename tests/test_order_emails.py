"""Order emails: designed HTML plus plain text, with photos, every order detail and promos."""

from __future__ import annotations

from pathlib import Path

from flask import Flask
from PIL import Image

from app import mail, order_emails
from app.extensions import db
from app.orders.models import Order
from tests.test_order_shipping import _paid_order


def _order(app: Flask) -> Order:
    order = db.session.get(Order, _paid_order(app))
    assert order is not None
    return order


def test_confirmation_has_photos_details_and_promos(catalog_app: Flask) -> None:
    catalog_app.config["SITE_URL"] = "https://shop.example.com"
    with catalog_app.test_request_context("/"):
        order = _order(catalog_app)
        order_emails.send_confirmation(order)
        message = catalog_app.extensions["outbox"][-1]
        assert message.to == order.email and order.public_id in message.subject
        html, text = message.html, message.body
        assert html and "https://shop.example.com/static/img/email/logo.png" in html  # logo
        assert "https://shop.example.com/static/img/email/banner.jpg" in html
        # Product photo through the JPEG email-image route, absolute.
        assert "https://shop.example.com/email-images/products/ankara-blue/" in html
        for detail in (order.first_name, order.address1, order.city, order.postcode, order.email):
            assert detail in html and detail in text
        assert "Card, Apple Pay or Google Pay" in html  # how they paid
        assert "Tracking link emailed when it ships" in html
        for path in ("/shop/wax-prints/", "/collections/new-arrivals/", "/find-fabrics/",
                     "/collections/sale/"):  # fmt: skip
            assert f"https://shop.example.com{path}" in html and path in text
        assert "Total" in text and order.public_id in text


def test_owner_notice_has_packing_details_and_no_promos(catalog_app: Flask) -> None:
    catalog_app.config.update(
        SITE_URL="https://shop.example.com", ORDER_NOTIFICATION_EMAIL="o@x.com"
    )
    with catalog_app.test_request_context("/"):
        order = _order(catalog_app)
        order_emails.send_owner_notice(order)
        message = catalog_app.extensions["outbox"][-1]
        assert message.to == "o@x.com" and order.first_name in message.subject
        assert f"https://shop.example.com/admin/orders/{order.id}" in message.html
        assert "To pack and ship" in message.html and "Keep exploring" not in message.html


def test_shipped_email_names_the_carrier_and_links_tracking(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        order = _order(catalog_app)
        order.tracking_carrier, order.tracking_number = "usps", "9400100000000000000000"
        order_emails.send_shipped(order)
        message = catalog_app.extensions["outbox"][-1]
        assert "is on its way" in message.subject
        assert "USPS" in message.html and "Track your package" in message.html
        assert "tools.usps.com" in message.html and "9400100000000000000000" in message.html
        assert "tools.usps.com" in message.body and "Delivered by:    USPS" in message.body


def test_links_use_the_request_address_without_site_url(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/", base_url="https://ggafrikaneleganz.com"):
        order_emails.send_confirmation(_order(catalog_app))
    assert "https://ggafrikaneleganz.com/static/img/email/logo.png" in (
        catalog_app.extensions["outbox"][-1].html
    )


def test_both_versions_are_sent_as_one_message(app: Flask) -> None:
    app.config.update(MAIL_FROM="GG <shop@example.com>")
    with app.app_context():
        built = mail.build(mail.Message("a@b.com", "Hi", "plain text", "<p>designed</p>"))
    assert built.get_content_type() == "multipart/alternative"
    kinds = [part.get_content_type() for part in built.iter_parts()]
    assert kinds == ["text/plain", "text/html"]


def test_email_images_are_jpeg_and_stay_inside_media(app: Flask) -> None:
    media = Path(app.config["MEDIA_ROOT"])
    (media / "products/x").mkdir(parents=True)
    Image.new("RGB", (900, 600), (200, 40, 40)).save(media / "products/x/01.webp")
    (media / "secret.txt").write_text("no")
    client = app.test_client()
    response = client.get("/email-images/products/x/01.webp")
    assert response.status_code == 200 and response.mimetype == "image/jpeg"
    assert "public" in response.headers["Cache-Control"]
    cached = media / "_email/products/x/01.jpg"
    assert cached.exists() and max(Image.open(cached).size) == 480
    assert client.get("/email-images/products/x/01.webp").status_code == 200  # from the cache
    for bad in (
        "../../etc/passwd",
        "secret.txt",
        "_email/products/x/01.jpg",
        "products/x/none.webp",
    ):
        assert client.get(f"/email-images/{bad}").status_code == 404, bad


def test_email_images_load_while_the_shop_is_locked(app: Flask) -> None:
    media = Path(app.config["MEDIA_ROOT"])
    (media / "p").mkdir(parents=True)
    Image.new("RGB", (100, 100)).save(media / "p/a.png")
    app.config["PREVIEW_PASSWORD"] = "team-preview-2026"  # noqa: S105
    assert app.test_client().get("/email-images/p/a.png").status_code == 200

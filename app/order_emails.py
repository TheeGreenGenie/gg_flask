"""Order emails: a designed HTML version (logo, photos, every order detail) plus plain text.

Images and links are absolute (SITE_URL, or the address the request came in on), point at this
site, and stay readable when a mail app blocks images: everything important is real text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from flask import current_app, has_request_context, render_template, request, url_for

from app import mail, site_content
from app.catalog import units
from app.orders.models import TRACKING_CARRIERS, Order, OrderLine, PaymentProvider

SHOP_TZ = ZoneInfo("America/New_York")
FALLBACK_URL = "https://ggafrikaneleganz.com"

# Other parts of the shop shown under the order in the confirmation email.
PROMOS = (
    ("Wax Prints", "Grand Super, Super and Regular wax in 6-yard bundles.", "/shop/wax-prints/",
     "promo-wax.jpg"),
    ("New Arrivals", "The latest prints, fresh from our shelves.", "/collections/new-arrivals/",
     "promo-new.jpg"),
    ("Find Fabrics", "Swipe through fabrics to find your next favourite.", "/find-fabrics/",
     "promo-find.jpg"),
    ("Sale", "Beautiful prints at reduced prices, while they last.", "/collections/sale/",
     "promo-sale.jpg"),
)  # fmt: skip


def site_url() -> str:
    configured = current_app.config.get("SITE_URL")
    if configured:
        return str(configured).rstrip("/")
    if has_request_context():
        return request.host_url.rstrip("/")
    return FALLBACK_URL


def _absolute(path: str) -> str:
    return site_url() + path


def _static(name: str) -> str:
    return _absolute(url_for("static", filename=f"img/email/{name}"))


def _line_photo(line: OrderLine) -> str:
    variant = line.variant
    picture = None
    if variant is not None:
        if variant.image is not None:
            picture = variant.image.path
        elif variant.product.cover is not None:
            picture = variant.product.cover.path
    if not picture:
        return _static("placeholder.png")
    return _absolute(url_for("email_images.image", path=picture))


def _when(moment: datetime | None) -> str:
    if moment is None:
        return ""
    local = moment.astimezone(SHOP_TZ)
    return f"{local:%B} {local.day}, {local.year}"  # e.g. October 29, 2026


def _payment_method(order: Order) -> str:
    if order.payment_provider == PaymentProvider.STRIPE:
        return "Card, Apple Pay or Google Pay (secured by Stripe)"
    return "PayPal"


def context(order: Order) -> dict[str, Any]:
    carrier = TRACKING_CARRIERS.get(order.tracking_carrier or "")
    account_url = (
        _absolute(url_for("accounts.order", public_id=order.public_id)) if order.user_id else None
    )
    return {
        "order": order,
        "placed_on": _when(order.created_at),
        "shipped_on": _when(order.shipped_at),
        "lines": [{"line": line, "photo": _line_photo(line)} for line in order.lines],
        "payment_method": _payment_method(order),
        "delivery_method": order.shipping_service or "Standard shipping",
        "carrier_name": carrier[0] if carrier else None,
        "tracking_url": order.tracking_url,
        "account_url": account_url,
        "shop_url": _absolute("/"),
        "contact_url": _absolute(url_for("content.contact")),
        "promos": [
            {"title": t, "text": x, "url": _absolute(p), "image": _static(i)}
            for t, x, p, i in PROMOS
        ],
        "logo": _static("logo.png"),
        "banner": _static("banner.jpg"),
        "contact_email": site_content.CONTACT_EMAIL,
        "contact_phone": site_content.CONTACT_PHONE,
        "social": site_content.SOCIAL_LINKS,
        "year": datetime.now(SHOP_TZ).year,
    }


def _render(name: str, **extra: Any) -> tuple[str, str]:
    """(plain text, html) for one email; works inside a request or from a command."""
    with current_app.test_request_context("/", base_url=site_url()):
        values = {**context(extra.pop("order")), **extra}
        return (
            render_template(f"email/{name}.txt", **values),
            render_template(f"email/{name}.html", **values),
        )


def send_confirmation(order: Order) -> bool:
    text, html = _render("order_confirmation", order=order, for_owner=False)
    return mail.send(order.email, f"Your GG Afrikan Eleganz order {order.public_id} is confirmed",
                     text, html)  # fmt: skip


def send_owner_notice(order: Order, to: str | None = None) -> bool:
    """The new-order notice; `to` defaults to ORDER_NOTIFICATION_EMAIL (orders/alerts.py sends
    it to everyone on the admin "Order alerts" list)."""
    owner = to or current_app.config.get("ORDER_NOTIFICATION_EMAIL")
    if not owner:
        return False
    weight = None
    if order.parcel_weight_grams:
        grams = order.parcel_weight_grams
        weight = f"{units.format_weight(grams)} (USPS price step {units.usps_weight_step(grams)})"
    admin_url = _absolute(f"/admin/orders/{order.id}")
    text, html = _render(
        "order_confirmation", order=order, for_owner=True, parcel_weight=weight, admin_url=admin_url
    )
    return mail.send(str(owner), f"New order {order.public_id}: {order.first_name} "
                     f"{order.last_name}", text, html)  # fmt: skip


def send_shipped(order: Order) -> bool:
    text, html = _render("order_shipped", order=order)
    return mail.send(order.email, f"Your GG Afrikan Eleganz order {order.public_id} is on its way",
                     text, html)  # fmt: skip

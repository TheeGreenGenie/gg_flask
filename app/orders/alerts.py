"""New-order alerts to the team (owner, 2026-10-30).

When an order is paid, everyone on the /admin "Order alerts" list hears about it:
- email addresses get the owner notice (items, photos, address, parcel weight, admin link);
- ntfy topics get a phone push (free ntfy app, no sign-up) that opens the order in /admin.
ORDER_NOTIFICATION_EMAIL (server setting) is always included. An alert never blocks or undoes an
order: failures are logged (never the topic, which works like a password) and skipped.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from flask import current_app
from sqlalchemy import select

from app import mail, order_emails
from app.admin.models import AdminUser
from app.extensions import db
from app.orders.models import Order, OrderAlertRecipient
from app.templating import money

_log = logging.getLogger(__name__)

TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class AlertError(ValueError):
    pass


def _active(kind: str) -> list[str]:
    return list(
        db.session.scalars(
            select(OrderAlertRecipient.value).where(
                OrderAlertRecipient.kind == kind, OrderAlertRecipient.active.is_(True)
            )
        )
    )


def email_addresses() -> list[str]:
    owner = (current_app.config.get("ORDER_NOTIFICATION_EMAIL") or "").strip()
    seen: dict[str, str] = {}
    for address in ([owner] if owner else []) + _active("email"):
        seen.setdefault(address.lower(), address)
    return list(seen.values())


def admin_link(order: Order) -> str:
    return order_emails.site_url() + f"/admin/orders/{order.id}"


def push_text(order: Order) -> tuple[str, str]:
    count = sum(line.quantity for line in order.lines)
    items = ", ".join(
        f"{line.product_name}" + (f" ×{line.quantity}" if line.quantity > 1 else "")
        for line in order.lines
    )
    title = f"New order {order.public_id} · {money(order.total_cents)}"
    message = f"{order.first_name} {order.last_name} · {count} item{'s' if count != 1 else ''}"
    if items:
        message += f": {items}"
    return title, message[:400]


def _push(topic: str, title: str, message: str, link: str) -> bool:
    backend = current_app.config.get("STUDIO_APPROVAL_BACKEND", "console")
    if backend != "ntfy":  # development: show it in the log instead (topic left out)
        _log.warning("order_alert_push title=%s url=%s", title, link)
        return True
    headers = {"Content-Type": "application/json"}
    token = current_app.config.get("STUDIO_NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 4,
        "tags": ["shopping_bags"],
        "click": link,
        "actions": [{"action": "view", "label": "Open order", "url": link, "clear": True}],
    }
    req = urllib.request.Request(  # noqa: S310 - fixed HTTPS ntfy server from configuration
        str(current_app.config["STUDIO_NTFY_SERVER"]).rstrip("/") + "/",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3):  # noqa: S310
            return True
    except urllib.error.HTTPError as exc:
        _log.error("order_alert_push_failed status=%s", exc.code)
    except OSError:
        _log.exception("order_alert_push_failed")
    return False


def new_order(order: Order) -> None:
    """Tell the team about a newly paid order. Never raises."""
    try:
        link = admin_link(order)
        for address in email_addresses():
            order_emails.send_owner_notice(order, to=address)
        title, message = push_text(order)
        for topic in _active("ntfy"):
            _push(topic, title, message, link)
    except Exception:  # an alert must never break checkout
        _log.exception("order_alert_failed order=%s", order.public_id)


# ---------- the /admin "Order alerts" list ----------


def add(admin: AdminUser, kind: str, value_raw: str, label_raw: str) -> OrderAlertRecipient:
    from app.admin import service

    value = value_raw.strip()
    label = label_raw.strip()[:80]
    if kind == "email":
        if not EMAIL_RE.fullmatch(value) or len(value) > 254:
            raise AlertError("Enter a valid email address.")
    elif kind == "ntfy":
        if not TOPIC_RE.fullmatch(value):
            raise AlertError(
                "A topic is 6–64 letters, numbers, dashes or underscores. Pick something hard "
                "to guess, like gg-orders-7f3k9q — anyone who knows it can read the alerts."
            )
    else:
        raise AlertError("Choose email or phone.")
    exists = db.session.scalar(
        select(OrderAlertRecipient).where(
            OrderAlertRecipient.kind == kind, OrderAlertRecipient.value == value
        )
    )
    if exists is not None:
        raise AlertError("That one is already on the list.")
    record = OrderAlertRecipient(kind=kind, value=value, label=label, active=True)
    db.session.add(record)
    db.session.flush()
    service.audit(admin, "order_alert.added", "order_alert", str(record.id), {"kind": kind})
    db.session.commit()
    return record


def remove(admin: AdminUser, record: OrderAlertRecipient) -> None:
    from app.admin import service

    service.audit(
        admin, "order_alert.removed", "order_alert", str(record.id), {"kind": record.kind}
    )
    db.session.delete(record)
    db.session.commit()


def set_active(admin: AdminUser, record: OrderAlertRecipient, active: bool) -> None:
    from app.admin import service

    record.active = active
    service.audit(
        admin, "order_alert.paused" if not active else "order_alert.resumed", "order_alert",
        str(record.id),
    )  # fmt: skip
    db.session.commit()


def send_test(record: OrderAlertRecipient) -> bool:
    link = order_emails.site_url() + "/admin/orders/"
    if record.kind == "ntfy":
        return _push(
            record.value,
            "Test: GG order alerts work",
            "New paid orders will appear like this. Tap to open the orders list.",
            link,
        )
    return mail.send(
        record.value,
        "Test: GG Afrikan Eleganz order alerts",
        "This address will get an email for every new paid order, with a link to open it in "
        f"admin.\n\nOrders: {link}\n",
    )

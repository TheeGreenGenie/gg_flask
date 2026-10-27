"""Hosted payment sessions and authenticated, replay-safe provider callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from flask import current_app
from sqlalchemy import select

from app import mail
from app.catalog.models import Variant, utcnow
from app.extensions import db
from app.orders import providers
from app.orders.models import (
    Order,
    OrderStatus,
    PaymentEvent,
    PaymentProvider,
    PaymentStatus,
)


class PaymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostedPayment:
    url: str
    reference: str


def provider_configured(provider: PaymentProvider) -> bool:
    if provider == PaymentProvider.STRIPE:
        return bool(
            current_app.config.get("STRIPE_SECRET_KEY")
            and current_app.config.get("STRIPE_WEBHOOK_SECRET")
        )
    return bool(
        current_app.config.get("PAYPAL_CLIENT_ID")
        and current_app.config.get("PAYPAL_CLIENT_SECRET")
        and current_app.config.get("PAYPAL_WEBHOOK_ID")
    )


def begin(order: Order, success_url: str, cancel_url: str) -> HostedPayment:
    if not provider_configured(order.payment_provider):
        raise PaymentError(f"{order.payment_provider.value.title()} is not configured.")
    if order.status != OrderStatus.PENDING_PAYMENT or _expired(order):
        raise PaymentError("This payment session has expired.")
    if order.payment_provider == PaymentProvider.STRIPE:
        result = _request(
            "POST",
            "https://api.stripe.com/v1/checkout/sessions",
            {
                "Authorization": f"Bearer {current_app.config['STRIPE_SECRET_KEY']}",
                "Idempotency-Key": f"checkout-{order.public_id}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urllib.parse.urlencode(
                {
                    "mode": "payment",
                    "client_reference_id": order.public_id,
                    "metadata[order_id]": order.public_id,
                    "line_items[0][price_data][currency]": order.currency.lower(),
                    "line_items[0][price_data][product_data][name]": (
                        f"GG Afrikan Eleganz order {order.public_id}"
                    ),
                    "line_items[0][price_data][unit_amount]": order.total_cents,
                    "line_items[0][quantity]": 1,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "expires_at": int(time.time()) + 30 * 60,
                }
            ).encode(),
        )
        reference, url = str(result.get("id", "")), str(result.get("url", ""))
    else:
        token = _paypal_token()
        result = _request(
            "POST",
            f"{current_app.config['PAYPAL_API_BASE']}/v2/checkout/orders",
            {
                "Authorization": f"Bearer {token}",
                "PayPal-Request-Id": f"checkout-{order.public_id}",
                "Content-Type": "application/json",
            },
            json.dumps(
                {
                    "intent": "CAPTURE",
                    "purchase_units": [
                        {
                            "custom_id": order.public_id,
                            "invoice_id": order.public_id,
                            "amount": {
                                "currency_code": order.currency,
                                "value": f"{order.total_cents / 100:.2f}",
                            },
                        }
                    ],
                    "payment_source": {
                        "paypal": {
                            "experience_context": {
                                "return_url": success_url,
                                "cancel_url": cancel_url,
                                "user_action": "PAY_NOW",
                            }
                        }
                    },
                }
            ).encode(),
        )
        reference = str(result.get("id", ""))
        url = next(
            (
                str(link["href"])
                for link in result.get("links", [])
                if link.get("rel") == "payer-action"
            ),
            "",
        )
    if not reference or not url:
        raise PaymentError("The payment provider returned an incomplete response.")
    order.provider_reference = reference
    db.session.commit()
    return HostedPayment(url, reference)


def capture_paypal(order: Order) -> None:
    if order.payment_provider != PaymentProvider.PAYPAL or not order.provider_reference:
        raise PaymentError("Invalid PayPal order.")
    result = _request(
        "POST",
        f"{current_app.config['PAYPAL_API_BASE']}/v2/checkout/orders/{order.provider_reference}/capture",
        {
            "Authorization": f"Bearer {_paypal_token()}",
            "PayPal-Request-Id": f"capture-{order.public_id}",
            "Content-Type": "application/json",
        },
        b"{}",
    )
    if result.get("status") != "COMPLETED":
        raise PaymentError("PayPal has not completed this payment.")
    try:
        capture_id = result["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError, TypeError):
        raise PaymentError("PayPal returned an incomplete capture response.") from None
    complete(order, str(capture_id))


def refund(order: Order) -> None:
    if order.payment_status != PaymentStatus.PAID or not order.provider_reference:
        raise PaymentError("Only a paid order can be refunded.")
    if not provider_configured(order.payment_provider):
        raise PaymentError(f"{order.payment_provider.value.title()} is not configured.")
    if order.payment_provider == PaymentProvider.STRIPE:
        _request(
            "POST",
            "https://api.stripe.com/v1/refunds",
            {
                "Authorization": f"Bearer {current_app.config['STRIPE_SECRET_KEY']}",
                "Idempotency-Key": f"refund-{order.public_id}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urllib.parse.urlencode({"payment_intent": order.provider_reference}).encode(),
        )
    else:
        _request(
            "POST",
            f"{current_app.config['PAYPAL_API_BASE']}/v2/payments/captures/{order.provider_reference}/refund",
            {
                "Authorization": f"Bearer {_paypal_token()}",
                "PayPal-Request-Id": f"refund-{order.public_id}",
                "Content-Type": "application/json",
            },
            b"{}",
        )
    order.payment_status = PaymentStatus.REFUNDED
    order.status = OrderStatus.REFUNDED
    order.refunded_cents = order.total_cents
    providers.reverse_tax_transaction(order)
    db.session.commit()


def handle_stripe(body: bytes, signature: str) -> bool:
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not _valid_stripe_signature(body, signature, secret):
        raise PaymentError("Invalid Stripe signature.")
    event = _json(body)
    event_id, event_type = str(event.get("id", "")), str(event.get("type", ""))
    obj = event.get("data", {}).get("object", {})
    public_id = str(obj.get("metadata", {}).get("order_id") or obj.get("client_reference_id") or "")
    return _record_and_process(PaymentProvider.STRIPE, event_id, event_type, public_id, body, obj)


def handle_paypal(body: bytes, headers: dict[str, str]) -> bool:
    event = _json(body)
    if not _verify_paypal(event, headers):
        raise PaymentError("Invalid PayPal signature.")
    resource = event.get("resource", {})
    public_id = str(resource.get("custom_id", ""))
    provider_reference = str(
        resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id", "")
    )
    if not public_id and provider_reference:
        order = db.session.scalar(
            select(Order).where(Order.provider_reference == provider_reference)
        )
        public_id = order.public_id if order else ""
    return _record_and_process(
        PaymentProvider.PAYPAL,
        str(event.get("id", "")),
        str(event.get("event_type", "")),
        public_id,
        body,
        resource,
    )


def complete(order: Order, provider_reference: str) -> bool:
    locked_order = db.session.scalar(select(Order).where(Order.id == order.id).with_for_update())
    if locked_order is None:
        raise PaymentError("Unknown order.")
    order = locked_order
    if order.payment_status == PaymentStatus.PAID:
        return False
    if order.status != OrderStatus.PENDING_PAYMENT or _expired(order):
        raise PaymentError("Payment arrived after the stock reservation expired.")
    now = utcnow()
    variant_ids = [hold.variant_id for hold in order.holds]
    variants = {
        variant.id: variant
        for variant in db.session.scalars(
            select(Variant).where(Variant.id.in_(variant_ids)).with_for_update()
        ).all()
    }
    for hold in order.holds:
        variant = variants.get(hold.variant_id)
        if variant is None or hold.released_at or variant.available_quantity < hold.quantity:
            raise PaymentError("Reserved stock is no longer available.")
        variant.stock_quantity -= hold.quantity
        hold.consumed_at = now
    order.status = OrderStatus.PAID
    order.payment_status = PaymentStatus.PAID
    order.provider_reference = provider_reference
    if order.cart:
        for line in list(order.cart.lines):
            db.session.delete(line)
    db.session.commit()
    providers.record_tax_transaction(order)
    db.session.commit()
    send_order_emails(order)
    return True


def _record_and_process(
    provider: PaymentProvider,
    event_id: str,
    event_type: str,
    public_id: str,
    body: bytes,
    obj: dict[str, Any],
) -> bool:
    if not event_id:
        raise PaymentError("Provider event has no ID.")
    if db.session.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == provider, PaymentEvent.event_id == event_id
        )
    ):
        return False
    order = db.session.scalar(select(Order).where(Order.public_id == public_id))
    paid_type = (
        event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}
        and obj.get("payment_status") == "paid"
        if provider == PaymentProvider.STRIPE
        else event_type == "PAYMENT.CAPTURE.COMPLETED"
    )
    if paid_type:
        if order is None:
            raise PaymentError("Webhook references an unknown order.")
        amount = obj.get("amount_total") if provider == PaymentProvider.STRIPE else None
        if amount is not None and int(amount) != order.total_cents:
            raise PaymentError("Webhook payment amount does not match the order.")
        reference = (
            obj.get("payment_intent") if provider == PaymentProvider.STRIPE else obj.get("id")
        )
        complete(order, str(reference or order.provider_reference or ""))
    db.session.add(
        PaymentEvent(
            provider=provider,
            event_id=event_id,
            event_type=event_type,
            payload_hash=hashlib.sha256(body).hexdigest(),
            order=order,
        )
    )
    db.session.commit()
    return paid_type


def _valid_stripe_signature(body: bytes, header: str, secret: str) -> bool:
    values: dict[str, list[str]] = {}
    for part in header.split(","):
        key, _, value = part.partition("=")
        values.setdefault(key, []).append(value)
    try:
        timestamp = int(values["t"][0])
    except (KeyError, ValueError):
        return False
    if abs(int(time.time()) - timestamp) > 300:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in values.get("v1", []))


def _verify_paypal(event: dict[str, Any], headers: dict[str, str]) -> bool:
    required = {
        "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
        "cert_url": headers.get("PAYPAL-CERT-URL", ""),
        "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
        "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
        "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
        "webhook_id": current_app.config.get("PAYPAL_WEBHOOK_ID", ""),
        "webhook_event": event,
    }
    if not all(required[key] for key in required if key != "webhook_event"):
        return False
    result = _request(
        "POST",
        f"{current_app.config['PAYPAL_API_BASE']}/v1/notifications/verify-webhook-signature",
        {
            "Authorization": f"Bearer {_paypal_token()}",
            "Content-Type": "application/json",
        },
        json.dumps(required).encode(),
    )
    return result.get("verification_status") == "SUCCESS"


def _paypal_token() -> str:
    credentials = base64.b64encode(
        f"{current_app.config['PAYPAL_CLIENT_ID']}:{current_app.config['PAYPAL_CLIENT_SECRET']}".encode()
    ).decode()
    result = _request(
        "POST",
        f"{current_app.config['PAYPAL_API_BASE']}/v1/oauth2/token",
        {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        b"grant_type=client_credentials",
    )
    token = str(result.get("access_token", ""))
    if not token:
        raise PaymentError("PayPal did not issue an access token.")
    return token


def _request(method: str, url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    injected = current_app.extensions.get("payment_http")
    if injected:
        return injected(method, url, headers, body)
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return _json(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PaymentError("The payment provider is temporarily unavailable.") from exc


def _json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PaymentError("Invalid provider response.") from exc
    if not isinstance(value, dict):
        raise PaymentError("Invalid provider response.")
    return value


def _expired(order: Order) -> bool:
    expires = order.hold_expires_at
    if expires.tzinfo is None:
        from datetime import UTC

        expires = expires.replace(tzinfo=UTC)
    return expires <= utcnow()


def send_order_emails(order: Order) -> None:
    lines = "\n".join(f"- {line.quantity} × {line.product_name}" for line in order.lines)
    body = (
        f"Thank you for your order {order.public_id}.\n\n{lines}\n\n"
        f"Total: ${order.total_cents / 100:.2f}\n"
    )
    mail.send(order.email, f"Order {order.public_id} confirmed", body)
    owner = current_app.config.get("ORDER_NOTIFICATION_EMAIL")
    if owner:
        mail.send(owner, f"New order {order.public_id}", body)

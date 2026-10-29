"""Hosted payment sessions and authenticated, replay-safe provider callbacks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import current_app
from sqlalchemy import select

from app import mail
from app.catalog import units
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

log = logging.getLogger(__name__)


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
        reused = _open_stripe_session(order)
        if reused is not None:
            return reused
        # Stripe's minimum is 30 minutes; a minute's margin covers clock and network delay.
        expires_at = int(time.time()) + 31 * 60
        result = _request(
            "POST",
            "https://api.stripe.com/v1/checkout/sessions",
            {
                "Authorization": f"Bearer {current_app.config['STRIPE_SECRET_KEY']}",
                # One key per attempt: Stripe refuses a key reused with different details (the
                # expiry changes every time), which used to make every retry fail.
                "Idempotency-Key": f"checkout-{order.public_id}-{expires_at}",
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
                    "expires_at": expires_at,
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


STRIPE_REUSE_MIN_SECONDS = 5 * 60  # don't send a shopper to a session about to lapse


def _open_stripe_session(order: Order) -> HostedPayment | None:
    """Send a returning shopper back to the Stripe page already made for this order.

    An order never has two open ways to pay: a session that is still open is reused, and one
    about to lapse is closed on Stripe's side before a new one is made.
    """
    reference = order.provider_reference or ""
    if not reference.startswith("cs_"):
        return None
    headers = {"Authorization": f"Bearer {current_app.config['STRIPE_SECRET_KEY']}"}
    url = f"https://api.stripe.com/v1/checkout/sessions/{reference}"
    session = _request("GET", url, headers, None)
    status = session.get("status")
    if status == "complete":
        raise PaymentError(
            "This order has already been paid. Your confirmation email is on its way."
        )
    if status == "open":
        expires = int(session.get("expires_at") or 0)
        if expires > time.time() + STRIPE_REUSE_MIN_SECONDS and session.get("url"):
            return HostedPayment(str(session["url"]), reference)
        _request("POST", f"{url}/expire", headers, b"")
    return None


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
        capture = result["purchase_units"][0]["payments"]["captures"][0]
        capture_id = capture["id"]
    except (KeyError, IndexError, TypeError):
        raise PaymentError("PayPal returned an incomplete capture response.") from None
    if capture.get("status") != "COMPLETED":
        raise PaymentError("PayPal has not completed this payment.")
    _require_amount(order, *_paypal_amount(capture.get("amount")))
    complete(order, str(capture_id))


def refund(order: Order) -> str:
    if order.payment_status != PaymentStatus.PAID or not order.provider_reference:
        raise PaymentError("Only a paid order can be refunded.")
    if not provider_configured(order.payment_provider):
        raise PaymentError(f"{order.payment_provider.value.title()} is not configured.")
    if order.refund_status == "pending":
        raise PaymentError("A refund is already in progress for this order.")
    if order.payment_provider == PaymentProvider.STRIPE:
        result = _request(
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
        result = _request(
            "POST",
            f"{current_app.config['PAYPAL_API_BASE']}/v2/payments/captures/{order.provider_reference}/refund",
            {
                "Authorization": f"Bearer {_paypal_token()}",
                "PayPal-Request-Id": f"refund-{order.public_id}",
                "Content-Type": "application/json",
            },
            b"{}",
        )
    reference = str(result.get("id") or "")
    if not reference:
        raise PaymentError("The payment provider did not confirm the refund.")
    order.refund_reference = reference[:80]
    return _apply_refund_state(order, result)


REFUND_STATES = {
    # Stripe refund statuses
    "succeeded": "completed",
    "pending": "pending",
    "requires_action": "pending",
    "failed": "failed",
    "canceled": "failed",
    # PayPal refund statuses
    "COMPLETED": "completed",
    "PENDING": "pending",
    "FAILED": "failed",
    "CANCELLED": "failed",
}


def _apply_refund_state(order: Order, result: dict[str, Any]) -> str:
    """Store the provider's refund state; only a completed refund marks the order refunded."""
    state = REFUND_STATES.get(str(result.get("status", "")), "pending")
    if order.payment_provider == PaymentProvider.STRIPE:
        cents, currency = result.get("amount"), result.get("currency")
    else:
        cents, currency = _paypal_amount(result.get("amount"))
    if (
        state == "completed"
        and cents is not None
        and (
            int(cents) != order.total_cents or str(currency or "").upper() != order.currency.upper()
        )
    ):
        log.warning("refund_amount_mismatch order=%s", order.public_id)
        state = "failed"
    order.refund_status = state
    if state == "completed":
        order.payment_status = PaymentStatus.REFUNDED
        order.status = OrderStatus.REFUNDED
        order.refunded_cents = order.total_cents
        providers.reverse_tax_transaction(order)
    db.session.commit()
    if state == "failed":
        raise PaymentError("The payment provider did not complete the refund.")
    return state


def sync_refunds() -> dict[str, int]:
    """Ask the provider about refunds still pending and finish the ones that completed."""
    counts = {"completed": 0, "pending": 0, "failed": 0, "error": 0}
    pending = db.session.scalars(
        select(Order).where(Order.refund_status == "pending", Order.refund_reference.is_not(None))
    ).all()
    for order in pending:
        try:
            if order.payment_provider == PaymentProvider.STRIPE:
                result = _request(
                    "GET",
                    f"https://api.stripe.com/v1/refunds/{urllib.parse.quote(str(order.refund_reference))}",
                    {"Authorization": f"Bearer {current_app.config['STRIPE_SECRET_KEY']}"},
                    b"",
                )
            else:
                result = _request(
                    "GET",
                    f"{current_app.config['PAYPAL_API_BASE']}/v2/payments/refunds/"
                    f"{urllib.parse.quote(str(order.refund_reference))}",
                    {"Authorization": f"Bearer {_paypal_token()}"},
                    b"",
                )
            counts[_apply_refund_state(order, result)] += 1
        except PaymentError:
            db.session.rollback()
            counts["failed" if order.refund_status == "failed" else "error"] += 1
    return counts


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
        if provider == PaymentProvider.STRIPE:
            _require_amount(order, obj.get("amount_total"), obj.get("currency"))
        else:
            _require_amount(order, *_paypal_amount(obj.get("amount")))
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


def _paypal_amount(amount: Any) -> tuple[int | None, str | None]:
    """PayPal sends {"currency_code": "USD", "value": "17.99"}; returns (cents, currency)."""
    if not isinstance(amount, dict):
        return None, None
    try:
        cents = Decimal(str(amount["value"])) * 100
    except (KeyError, InvalidOperation):
        return None, None
    if cents != cents.to_integral_value():
        return None, None
    return int(cents), str(amount.get("currency_code") or "") or None


def _require_amount(order: Order, cents: Any, currency: Any) -> None:
    """A payment only counts when the provider confirms exactly this order's amount and currency."""
    try:
        matches = (
            cents is not None
            and currency is not None
            and int(cents) == order.total_cents
            and str(currency).upper() == order.currency.upper()
        )
    except (TypeError, ValueError):
        matches = False
    if not matches:
        log.warning("payment_amount_mismatch order=%s", order.public_id)
        raise PaymentError("The payment amount or currency does not match the order.")


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


def _request(method: str, url: str, headers: dict[str, str], body: bytes | None) -> dict[str, Any]:
    injected = current_app.extensions.get("payment_http")
    if injected:
        return injected(method, url, headers, body)
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return _json(response.read())
    except urllib.error.HTTPError as exc:
        # Keep the provider's reason (error type/code/message; never our keys) for diagnosis.
        log.warning(
            "payment_provider_refused",
            extra={
                "status": exc.code,
                "endpoint": urllib.parse.urlsplit(url).path,
                "reason": _provider_reason(exc),
            },
        )
        raise PaymentError("The payment provider is temporarily unavailable.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("payment_provider_unreachable", extra={"error": type(exc).__name__})
        raise PaymentError("The payment provider is temporarily unavailable.") from exc


def _provider_reason(exc: urllib.error.HTTPError) -> str:
    """Stripe's or PayPal's short explanation of a refusal, for the server log."""
    try:
        data = json.loads(exc.read() or b"{}")
    except (OSError, ValueError):
        return "unreadable error body"
    err = data.get("error", data) if isinstance(data, dict) else {}
    if not isinstance(err, dict):
        return str(err)[:200]
    details = err.get("details") if isinstance(err.get("details"), list) else []
    parts = [
        err.get("type") or err.get("name"),
        err.get("code")
        or (details[0].get("issue") if details and isinstance(details[0], dict) else None),
        f"param={err['param']}" if err.get("param") else None,
        err.get("message"),
    ]
    reason = " | ".join(str(p) for p in parts if p)[:300]
    return re.sub(r"[^\s@|]+@[^\s@|]+", "<email>", reason)  # logs never hold personal data


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
        service = order.shipping_service or "flat rate"
        shipping = f"Shipping: {service} ${order.shipping_cents / 100:.2f}"
        if order.parcel_weight_grams:
            weight = order.parcel_weight_grams
            shipping += (
                f"\nEstimated parcel weight: {units.format_weight(weight)}"
                f" (USPS price step {units.usps_weight_step(weight)})"
            )
        mail.send(owner, f"New order {order.public_id}", f"{body}{shipping}\n")

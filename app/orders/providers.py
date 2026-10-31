"""Shipping rates (Shippo) and sales tax (Stripe Tax).

Owner decisions (2026-10-26): ship anywhere USPS delivers, priced by distance/weight using
live Shippo USPS rates; Philadelphia addresses keep the lower local rate; tax calculated
by Stripe Tax for both Stripe and PayPal orders.

Both providers are optional backends selected by configuration:
- SHIPPING_BACKEND = "flat" (default) | "shippo". If Shippo cannot be reached the flat rate
  is used, so checkout keeps working (feature matrix O-008 "flat-rate fallback").
- TAX_BACKEND = "none" (explicit no-tax/test) | "stripe". Tax fails closed: if Stripe Tax is
  unavailable the order is not created, because charging the wrong tax is not recoverable.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from flask import current_app

if TYPE_CHECKING:
    from app.catalog.models import Variant
    from app.orders.models import Order
    from app.orders.service import Address

log = logging.getLogger(__name__)

SHIPPO_API = "https://api.goshippo.com"
STRIPE_API = "https://api.stripe.com/v1"
# Customer-selectable speeds (owner, 2026-10-27). Standard = cheapest USPS service (Ground
# Advantage); Express = USPS Priority Mail Express, only with live Shippo rates.
STANDARD, EXPRESS = "standard", "express"
SHIPPING_SPEEDS = (STANDARD, EXPRESS)
DEFAULT_ITEM_WEIGHT_GRAMS = 450  # used when a product has no shipping weight yet
PACKAGING_WEIGHT_GRAMS = 120

# Stripe product tax codes per product type. txcd_99999999 = general tangible goods.
# Pennsylvania treats most clothing as exempt; confirm codes with your accountant.
DEFAULT_TAX_CODES = {
    "fabric": "txcd_99999999",
    "clothing": "txcd_30011000",
    "home_accessories": "txcd_99999999",
}


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShippingQuote:
    cents: int
    carrier: str
    service: str
    rate_ref: str | None = None  # Shippo rate id, used later to buy the label


@dataclass(frozen=True)
class TaxQuote:
    cents: int
    calculation_ref: str | None = None


@dataclass(frozen=True)
class TaxLine:
    reference: str  # SKU
    amount_cents: int  # after any discount allocation
    quantity: int
    product_type: str


# ---------------------------------------------------------------------------
# Shipping
# ---------------------------------------------------------------------------


def parcel_weight_grams(lines: list[tuple[Variant, int]]) -> int:
    """Entered weight, else the fabric estimate (16 oz/sq yd), else 450 g; plus packaging."""
    items = sum((v.shipping_weight_grams or DEFAULT_ITEM_WEIGHT_GRAMS) * qty for v, qty in lines)
    return items + PACKAGING_WEIGHT_GRAMS


def express_available() -> bool:
    cfg = current_app.config
    return cfg.get("SHIPPING_BACKEND") == "shippo" and bool(cfg.get("SHIPPO_API_TOKEN"))


def quote_shipping(
    address: Address, lines: list[tuple[Variant, int]], speed: str = STANDARD
) -> ShippingQuote:
    from app.orders.service import CheckoutError, is_philadelphia

    cfg = current_app.config
    if speed not in SHIPPING_SPEEDS:
        raise CheckoutError("Choose Standard or Express shipping.")
    if speed == EXPRESS:
        unavailable = CheckoutError(
            "Express shipping isn't available for this address right now. Please choose Standard."
        )
        if not express_available():
            raise unavailable
        try:
            return _shippo_quote(address, parcel_weight_grams(lines), express=True)
        except ProviderError as exc:
            log.warning("shippo_express_failed", extra={"reason": str(exc)[:200]})
            raise unavailable from None

    free_from = cfg.get("FREE_SHIPPING_FROM_CENTS")
    items_cents = sum(variant.price_cents * qty for variant, qty in lines)
    if isinstance(free_from, int) and free_from > 0 and items_cents >= free_from:
        return ShippingQuote(0, "USPS", "Free shipping")

    flat = cfg.get("FLAT_SHIPPING_CENTS")
    local = cfg.get("LOCAL_SHIPPING_CENTS")
    if isinstance(local, int) and local >= 0 and is_philadelphia(address):
        return ShippingQuote(local, "Local", "Philadelphia local rate")

    if express_available():
        try:
            return _shippo_quote(address, parcel_weight_grams(lines))
        except ProviderError as exc:
            log.warning("shippo_quote_failed", extra={"reason": str(exc)[:200]})

    by_weight = weight_rate_cents(parcel_weight_grams(lines))
    if by_weight is not None:
        return ShippingQuote(by_weight, "USPS", "Standard (by weight)")
    if not isinstance(flat, int) or flat < 0:
        raise CheckoutError("Shipping is not configured yet.")
    return ShippingQuote(flat, "USPS", "Flat rate")


GRAMS_PER_POUND = 453.59237


def weight_rate_cents(weight_grams: int) -> int | None:
    """Price for the whole parcel from SHIPPING_WEIGHT_RATES (None when not configured).
    The weight already counts every item times its quantity, so five 1-yard cuts cost more
    than one."""
    cfg = current_app.config
    bands = cfg.get("SHIPPING_WEIGHT_RATES") or ()
    if not bands:
        return None
    pounds = weight_grams / GRAMS_PER_POUND
    for max_pounds, cents in bands:
        if pounds <= max_pounds:
            return int(cents)
    last_pounds, last_cents = bands[-1]
    extra = math.ceil(pounds - last_pounds)
    return int(last_cents) + extra * int(cfg.get("SHIPPING_EXTRA_LB_CENTS") or 0)


def _is_express(rate: dict[str, Any]) -> bool:
    level = rate.get("servicelevel") or {}
    return "express" in f"{level.get('token', '')} {level.get('name', '')}".lower()


def _shippo_quote(address: Address, weight_grams: int, express: bool = False) -> ShippingQuote:
    cfg = current_app.config
    length, width, height = cfg.get("SHIPPO_PARCEL_INCHES") or (12, 10, 4)
    payload = {
        "address_from": _ship_from(),
        "address_to": {
            "name": f"{address.first_name} {address.last_name}".strip(),
            "street1": address.address1,
            "street2": address.address2,
            "city": address.city,
            "state": address.region,
            "zip": address.postcode,
            "country": address.country,
            "phone": address.phone or "",
            "email": address.email,
        },
        "parcels": [
            {
                "length": str(length),
                "width": str(width),
                "height": str(height),
                "distance_unit": "in",
                "weight": str(weight_grams),
                "mass_unit": "g",
            }
        ],
        "async": False,
    }
    data = _http(
        "POST",
        f"{SHIPPO_API}/shipments/",
        {
            "Authorization": f"ShippoToken {cfg['SHIPPO_API_TOKEN']}",
            "Content-Type": "application/json",
        },
        json.dumps(payload).encode(),
    )
    allowed = {c.strip().upper() for c in (cfg.get("SHIPPO_CARRIERS") or ("USPS",))}
    rates = [
        r
        for r in data.get("rates") or []
        if str(r.get("provider", "")).upper() in allowed
        and r.get("currency") == "USD"
        and _is_express(r) == express
    ]
    if not rates:
        raise ProviderError("no express rates returned" if express else "no USPS rates returned")
    best = min(rates, key=lambda r: Decimal(str(r["amount"])))
    cents = int((Decimal(str(best["amount"])) * 100).quantize(Decimal("1"), ROUND_HALF_UP))
    handling = cfg.get("SHIPPING_HANDLING_CENTS") or 0
    service = (best.get("servicelevel") or {}).get("name") or "USPS"
    days = best.get("estimated_days")
    if isinstance(days, int) and days > 0:
        service = f"{service} (about {days} business day{'s' if days != 1 else ''})"
    return ShippingQuote(
        cents + int(handling), str(best.get("provider")), str(service), best.get("object_id")
    )


def _ship_from() -> dict[str, str]:
    cfg = current_app.config
    missing = [
        k
        for k in ("SHIP_FROM_STREET", "SHIP_FROM_CITY", "SHIP_FROM_STATE", "SHIP_FROM_ZIP")
        if not cfg.get(k)
    ]
    if missing:
        raise ProviderError(f"ship-from address incomplete: {', '.join(missing)}")
    return {
        "name": cfg.get("SHIP_FROM_NAME") or "GG Afrikan Eleganz",
        "street1": cfg["SHIP_FROM_STREET"],
        "city": cfg["SHIP_FROM_CITY"],
        "state": cfg["SHIP_FROM_STATE"],
        "zip": cfg["SHIP_FROM_ZIP"],
        "country": "US",
        "phone": cfg.get("SHIP_FROM_PHONE") or "",
        "email": cfg.get("SHIP_FROM_EMAIL") or "",
    }


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------


def allocate_discount(amounts: list[int], discount: int) -> list[int]:
    """Spread a whole-order discount across lines proportionally (integer cents)."""
    total = sum(amounts)
    if not total or not discount:
        return list(amounts)
    shares = [amount * discount // total for amount in amounts]
    shares[-1] += discount - sum(shares)  # remainder to the last line
    return [amount - share for amount, share in zip(amounts, shares, strict=True)]


def calculate_tax(address: Address, lines: list[TaxLine], shipping_cents: int) -> TaxQuote:
    from app.orders.service import CheckoutError

    backend = current_app.config.get("TAX_BACKEND")
    if backend == "none":
        return TaxQuote(0)
    if backend != "stripe" or not current_app.config.get("STRIPE_SECRET_KEY"):
        raise CheckoutError("Tax calculation is not configured yet.")
    codes = {**DEFAULT_TAX_CODES, **(current_app.config.get("STRIPE_TAX_CODES") or {})}
    fields: list[tuple[str, str]] = [
        ("currency", "usd"),
        ("customer_details[address_source]", "shipping"),
        ("customer_details[address][line1]", address.address1),
        ("customer_details[address][line2]", address.address2),
        ("customer_details[address][city]", address.city),
        ("customer_details[address][state]", address.region),
        ("customer_details[address][postal_code]", address.postcode),
        ("customer_details[address][country]", address.country),
        ("shipping_cost[amount]", str(shipping_cents)),
    ]
    for i, line in enumerate(lines):
        fields += [
            (f"line_items[{i}][amount]", str(line.amount_cents)),
            (f"line_items[{i}][quantity]", str(line.quantity)),
            (f"line_items[{i}][reference]", line.reference[:500]),
            (
                f"line_items[{i}][tax_code]",
                codes.get(line.product_type, DEFAULT_TAX_CODES["fabric"]),
            ),
            (f"line_items[{i}][tax_behavior]", "exclusive"),
        ]
    try:
        data = _stripe("POST", "/tax/calculations", fields)
    except ProviderError as exc:
        log.warning("stripe_tax_failed", extra={"reason": str(exc)[:200]})
        raise CheckoutError(
            "We couldn't calculate sales tax right now. Please try again in a moment."
        ) from None
    return TaxQuote(int(data.get("tax_amount_exclusive", 0)), data.get("id"))


def record_tax_transaction(order: Order) -> None:
    """After payment: turn the calculation into a Stripe Tax transaction for reporting.

    Best effort — a failure is logged for follow-up and never blocks the paid order.
    """
    if order.tax_calculation_ref is None or order.tax_transaction_ref:
        return
    try:
        data = _stripe(
            "POST",
            "/tax/transactions/create_from_calculation",
            [("calculation", order.tax_calculation_ref), ("reference", order.public_id)],
            idempotency_key=f"taxtx-{order.public_id}",
        )
        order.tax_transaction_ref = data.get("id")
    except ProviderError as exc:
        log.error(
            "stripe_tax_record_failed", extra={"order": order.public_id, "reason": str(exc)[:200]}
        )


def reverse_tax_transaction(order: Order) -> None:
    """After a full refund: reverse the recorded tax so filings stay correct."""
    if not order.tax_transaction_ref:
        return
    try:
        _stripe(
            "POST",
            "/tax/transactions/create_reversal",
            [
                ("mode", "full"),
                ("original_transaction", order.tax_transaction_ref),
                ("reference", f"{order.public_id}-refund"),
            ],
            idempotency_key=f"taxrev-{order.public_id}",
        )
    except ProviderError as exc:
        log.error(
            "stripe_tax_reversal_failed", extra={"order": order.public_id, "reason": str(exc)[:200]}
        )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _stripe(
    method: str, path: str, fields: list[tuple[str, str]], idempotency_key: str | None = None
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {current_app.config['STRIPE_SECRET_KEY']}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return _http(method, STRIPE_API + path, headers, urllib.parse.urlencode(fields).encode())


def _http(method: str, url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            parsed: dict[str, Any] = json.loads(response.read() or b"{}")
            return parsed
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProviderError(type(exc).__name__) from None

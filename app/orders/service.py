"""Checkout validation, coupons, duplicate-proof order creation, and stock holds."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func, select

from app.accounts import service as account_service
from app.cart.models import Cart
from app.cart.service import view
from app.catalog.models import ProductStatus, Variant, utcnow
from app.extensions import db
from app.orders import providers
from app.orders.models import (
    Coupon,
    DiscountType,
    Order,
    OrderLine,
    OrderStatus,
    PaymentProvider,
    PaymentStatus,
    StockHold,
)

HOLD_MINUTES = 60
POSTCODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{1,22}[A-Za-z0-9]$")


class CheckoutError(ValueError):
    pass


@dataclass(frozen=True)
class Address:
    email: str
    first_name: str
    last_name: str
    address1: str
    address2: str
    city: str
    region: str
    postcode: str
    country: str
    phone: str | None


def address_from_form(form: dict[str, str]) -> Address:
    email = account_service.normalize_email(form.get("email", ""))
    country = form.get("country", "").strip().upper()
    supported = current_app.config.get("SUPPORTED_COUNTRIES", ())
    if country not in supported:
        raise CheckoutError("We do not currently ship to that country.")
    values = {}
    for key, limit, label in (
        ("first_name", 80, "first name"),
        ("last_name", 80, "last name"),
        ("address1", 160, "street address"),
        ("city", 100, "city"),
        ("region", 100, "state or region"),
    ):
        value = form.get(key, "").strip()
        if not value or len(value) > limit:
            raise CheckoutError(f"Enter a valid {label}.")
        values[key] = value
    postcode = form.get("postcode", "").strip().upper()
    if not POSTCODE_RE.match(postcode):
        raise CheckoutError("Enter a valid postal code.")
    address2 = form.get("address2", "").strip()
    if len(address2) > 160:
        raise CheckoutError("Address line 2 is too long.")
    try:
        phone = account_service.normalize_phone(form.get("phone", ""))
    except account_service.AccountError as exc:
        raise CheckoutError(str(exc)) from None
    return Address(
        email=email, address2=address2, postcode=postcode, country=country, phone=phone, **values
    )


def coupon_discount(code_raw: str, subtotal: int) -> tuple[Coupon | None, int]:
    code = code_raw.strip().upper()
    if not code:
        return None, 0
    coupon = db.session.scalar(select(Coupon).where(Coupon.code == code).with_for_update())
    now = utcnow()
    if (
        coupon is None
        or not coupon.active
        or (coupon.starts_at and _aware(coupon.starts_at) > now)
        or (coupon.ends_at and _aware(coupon.ends_at) <= now)
        or (coupon.max_uses is not None and coupon.uses >= coupon.max_uses)
        or subtotal < coupon.minimum_subtotal_cents
    ):
        raise CheckoutError("That coupon is not available for this order.")
    discount = (
        coupon.amount
        if coupon.discount_type == DiscountType.FIXED
        else subtotal * min(coupon.amount, 100) // 100
    )
    return coupon, min(discount, subtotal)


# Philadelphia ZIP codes: 191xx plus the city's special-purpose codes.
PHILADELPHIA_EXTRA_ZIPS = frozenset({"19019", "19092", "19093", "19099"})


def is_philadelphia(address: Address) -> bool:
    zip5 = address.postcode[:5]
    return (
        address.country == "US"
        and zip5.isdigit()
        and (zip5.startswith("191") or zip5 in PHILADELPHIA_EXTRA_ZIPS)
    )


def shipping_cents(address: Address) -> int:
    """Rate for an empty parcel: the Philadelphia local rate, Shippo live rate or flat rate."""
    return providers.quote_shipping(address, []).cents


def create_order(
    cart: Cart,
    address: Address,
    provider: PaymentProvider,
    idempotency_token: str,
    coupon_code: str = "",
) -> Order:
    if len(idempotency_token) < 20:
        raise CheckoutError("Checkout session expired. Refresh the page and try again.")
    key = hashlib.sha256(idempotency_token.encode()).hexdigest()
    existing = db.session.scalar(select(Order).where(Order.idempotency_key == key))
    if existing:
        return existing
    cart_view = view(cart)
    if cart_view.is_empty or cart_view.has_problems:
        raise CheckoutError("Review your cart before checking out.")
    variant_ids = [line.variant.id for line in cart_view.lines]
    variants = {
        variant.id: variant
        for variant in db.session.scalars(
            select(Variant).where(Variant.id.in_(variant_ids)).with_for_update()
        ).all()
    }
    now = utcnow()
    for line in cart_view.lines:
        variant = variants[line.variant.id]
        held = db.session.scalar(
            select(func.coalesce(func.sum(StockHold.quantity), 0)).where(
                StockHold.variant_id == variant.id,
                StockHold.released_at.is_(None),
                StockHold.consumed_at.is_(None),
                StockHold.expires_at > now,
            )
        )
        available = variant.available_quantity - int(held or 0)
        if (
            not variant.active
            or variant.product.status != ProductStatus.ACTIVE
            or line.line.quantity > available
        ):
            raise CheckoutError(
                f"Only {max(available, 0)} of {variant.product.name} remain available."
            )
    subtotal = sum(
        variants[line.variant.id].price_cents * line.line.quantity for line in cart_view.lines
    )
    coupon, discount = coupon_discount(coupon_code, subtotal)
    taxable = max(subtotal - discount, 0)
    parcel = [(variants[line.variant.id], line.line.quantity) for line in cart_view.lines]
    shipping_quote = providers.quote_shipping(address, parcel)
    parcel_weight = providers.parcel_weight_grams(parcel)
    shipping = shipping_quote.cents
    line_amounts = providers.allocate_discount(
        [variant.price_cents * qty for variant, qty in parcel], discount
    )
    tax_quote = providers.calculate_tax(
        address,
        [
            providers.TaxLine(
                reference=variant.sku,
                amount_cents=amount,
                quantity=qty,
                product_type=variant.product.product_type.value,
            )
            for (variant, qty), amount in zip(parcel, line_amounts, strict=True)
        ],
        shipping,
    )
    tax = tax_quote.cents
    expires = now + timedelta(minutes=HOLD_MINUTES)
    user = account_service.current_user()
    order = Order(
        public_id=secrets.token_urlsafe(12),
        idempotency_key=key,
        user_id=user.id if user else None,
        cart_id=cart.id,
        status=OrderStatus.PENDING_PAYMENT,
        payment_provider=provider,
        payment_status=PaymentStatus.PENDING,
        email=address.email,
        phone=address.phone,
        first_name=address.first_name,
        last_name=address.last_name,
        address1=address.address1,
        address2=address.address2,
        city=address.city,
        region=address.region,
        postcode=address.postcode,
        country=address.country,
        subtotal_cents=subtotal,
        discount_cents=discount,
        shipping_cents=shipping,
        shipping_carrier=shipping_quote.carrier,
        shipping_service=shipping_quote.service,
        shipping_rate_ref=shipping_quote.rate_ref,
        parcel_weight_grams=parcel_weight,
        tax_cents=tax,
        tax_calculation_ref=tax_quote.calculation_ref,
        total_cents=taxable + shipping + tax,
        coupon_code=coupon.code if coupon else None,
        hold_expires_at=expires,
    )
    for line in cart_view.lines:
        variant = variants[line.variant.id]
        quantity = line.line.quantity
        order.lines.append(
            OrderLine(
                variant=variant,
                product_name=variant.product.name,
                variant_label=variant.label,
                sku=variant.sku,
                quantity=quantity,
                unit_price_cents=variant.price_cents,
                line_total_cents=variant.price_cents * quantity,
            )
        )
        order.holds.append(StockHold(variant=variant, quantity=quantity, expires_at=expires))
    if coupon:
        coupon.uses += 1
    db.session.add(order)
    db.session.commit()
    return order


def release_expired_holds() -> int:
    now = utcnow()
    holds = db.session.scalars(
        select(StockHold).where(
            StockHold.released_at.is_(None),
            StockHold.consumed_at.is_(None),
            StockHold.expires_at <= now,
        )
    ).all()
    order_ids = {hold.order_id for hold in holds}
    for hold in holds:
        hold.released_at = now
    for order_id in order_ids:
        order = db.session.get(Order, order_id)
        if order and order.status == OrderStatus.PENDING_PAYMENT:
            order.status = OrderStatus.CANCELLED
            order.payment_status = PaymentStatus.FAILED
            if order.coupon_code:
                coupon = db.session.scalar(
                    select(Coupon).where(Coupon.code == order.coupon_code).with_for_update()
                )
                if coupon:
                    coupon.uses = max(coupon.uses - 1, 0)
    db.session.commit()
    return len(holds)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        from datetime import UTC

        return value.replace(tzinfo=UTC)
    return value

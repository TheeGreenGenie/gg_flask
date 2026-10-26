"""Cart rules (O-001, O-002).

- The browser holds an opaque 256-bit token; only its SHA-256 hash is stored.
- Prices are never taken from the request: every view reprices from current variant data.
- Quantity per line is limited by available stock (including what is already in the cart)
  and by MAX_LINE_QUANTITY.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field

from flask import current_app, g, request
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from werkzeug.wrappers import Response

from app.cart.models import Cart, CartLine
from app.catalog.models import Product, ProductStatus, Variant
from app.extensions import db

COOKIE = "gg_cart"
COOKIE_MAX_AGE = 30 * 24 * 3600
MAX_LINE_QUANTITY = 99


class CartError(ValueError):
    """A request the cart cannot honour; the message is safe to show the shopper."""


@dataclass
class LineView:
    line: CartLine
    available: int
    problem: str = ""

    @property
    def variant(self) -> Variant:
        return self.line.variant

    @property
    def product(self) -> Product:
        return self.line.variant.product

    @property
    def unit_cents(self) -> int:
        return self.variant.price_cents

    @property
    def total_cents(self) -> int:
        return self.unit_cents * self.line.quantity


@dataclass
class CartView:
    lines: list[LineView] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(lv.line.quantity for lv in self.lines if not lv.problem)

    @property
    def subtotal_cents(self) -> int:
        return sum(lv.total_cents for lv in self.lines if not lv.problem)

    @property
    def has_problems(self) -> bool:
        return any(lv.problem for lv in self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.lines


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_cart(create: bool = False) -> Cart | None:
    """The shopper's cart, creating one (and scheduling the cookie) when asked."""
    if "cart" in g:
        return g.cart
    token = request.cookies.get(COOKIE, "")
    cart = None
    if token and len(token) <= 100:
        cart = db.session.scalar(
            select(Cart)
            .where(Cart.token_hash == _hash(token))
            .options(
                selectinload(Cart.lines)
                .selectinload(CartLine.variant)
                .selectinload(Variant.product)
                .selectinload(Product.images)
            )
        )
    if cart is None and create:
        token = secrets.token_urlsafe(32)
        cart = Cart(token_hash=_hash(token))
        db.session.add(cart)
        g.new_cart_token = token
    g.cart = cart
    return cart


def set_cookie_if_new(response: Response) -> Response:
    token = g.pop("new_cart_token", None)
    if token:
        response.set_cookie(
            COOKIE,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
        )
    return response


def view(cart: Cart | None) -> CartView:
    result = CartView()
    for line in cart.lines if cart else []:
        variant = line.variant
        available = min(variant.available_quantity, MAX_LINE_QUANTITY)
        problem = ""
        if not variant.active or variant.product.status != ProductStatus.ACTIVE:
            problem = "This item is no longer available."
        elif available <= 0:
            problem = "This item is out of stock."
        elif line.quantity > available:
            problem = f"Only {available} available; please lower the quantity."
        result.lines.append(LineView(line, available, problem))
    return result


def _sellable_variant(variant_id: int) -> Variant:
    variant = db.session.get(Variant, variant_id)
    if variant is None or not variant.active or variant.product.status != ProductStatus.ACTIVE:
        raise CartError("Sorry, that item is not available.")
    return variant


def add(variant_id: int, quantity: int) -> tuple[CartLine, str]:
    """Add to the cart. Returns the line and a message (quantity may be reduced to stock)."""
    if quantity < 1:
        raise CartError("Choose a quantity of at least 1.")
    variant = _sellable_variant(variant_id)
    limit = min(variant.available_quantity, MAX_LINE_QUANTITY)
    if limit <= 0:
        raise CartError("Sorry, that item is out of stock.")

    cart = current_cart(create=True)
    assert cart is not None
    line = next((ln for ln in cart.lines if ln.variant_id == variant.id), None)
    already = line.quantity if line else 0
    if already >= limit:
        raise CartError(f"You already have all {limit} available in your cart.")
    wanted = already + quantity
    new_qty = min(wanted, limit)
    if line is None:
        line = CartLine(variant=variant, quantity=new_qty)
        cart.lines.append(line)
    else:
        line.quantity = new_qty
    db.session.commit()
    message = (
        f"Added to your cart. Only {limit} available, so your cart has {new_qty}."
        if new_qty < wanted
        else "Added to your cart."
    )
    return line, message


def update(quantities: dict[int, int]) -> list[str]:
    """Set line quantities (0 removes). Returns messages about any adjustments."""
    cart = current_cart()
    if cart is None:
        return []
    messages = []
    for line in list(cart.lines):
        if line.id not in quantities:
            continue
        qty = max(0, quantities[line.id])
        if qty == 0:
            cart.lines.remove(line)
            continue
        limit = min(line.variant.available_quantity, MAX_LINE_QUANTITY)
        if qty > limit:
            messages.append(f"Only {limit} of {line.variant.product.name} available.")
            qty = limit
        if qty <= 0:
            cart.lines.remove(line)
        else:
            line.quantity = qty
    db.session.commit()
    return messages


def remove(line_id: int) -> bool:
    cart = current_cart()
    line = next((ln for ln in cart.lines if ln.id == line_id), None) if cart else None
    if cart is None or line is None:
        return False
    cart.lines.remove(line)
    db.session.commit()
    return True

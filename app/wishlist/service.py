"""Wishlist rules.

- The browser holds an opaque 256-bit token (HttpOnly, SameSite=Lax); only its hash is stored.
- The only input accepted is the slug of an active product; anything else is rejected.
- At most MAX_ITEMS per list; writes are rate-limited per client; idle lists are pruned.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from flask import current_app, g, request
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from werkzeug.wrappers import Response

from app import ratelimit
from app.catalog.models import Product, ProductStatus, utcnow
from app.extensions import db
from app.wishlist.models import Wishlist, WishlistItem

COOKIE = "gg_wishlist"
RETENTION_DAYS = 90
MAX_ITEMS = 100
WRITE_LIMIT = (120, 60)  # 120 changes per minute per client (fits a full list move)


class WishlistError(ValueError):
    """A request the wishlist cannot honour; the message is safe to show the shopper."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current(create: bool = False) -> Wishlist | None:
    if "wishlist" in g and (g.wishlist is not None or not create):
        return g.wishlist
    token = request.cookies.get(COOKIE, "")
    wishlist = None
    if token and len(token) <= 100:
        wishlist = db.session.scalar(
            select(Wishlist)
            .where(Wishlist.token_hash == _hash(token))
            .options(
                selectinload(Wishlist.items)
                .selectinload(WishlistItem.product)
                .selectinload(Product.images)
            )
        )
    if wishlist is None and create:
        token = secrets.token_urlsafe(32)
        wishlist = Wishlist(token_hash=_hash(token))
        db.session.add(wishlist)
        g.new_wishlist_token = token
    g.wishlist = wishlist
    return wishlist


def set_cookie(response: Response) -> Response:
    """Issue a new token, or slide the existing cookie's expiry forward after a change."""
    token = g.pop("new_wishlist_token", None) or (
        request.cookies.get(COOKIE) if g.pop("wishlist_touched", False) else None
    )
    if token:
        response.set_cookie(
            COOKIE,
            token,
            max_age=RETENTION_DAYS * 24 * 3600,
            httponly=True,
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
        )
    return response


def products(wishlist: Wishlist | None) -> list[Product]:
    """Saved products that are still for sale, newest save first."""
    if wishlist is None:
        return []
    return [i.product for i in wishlist.items if i.product.status == ProductStatus.ACTIVE]


def slugs(wishlist: Wishlist | None) -> set[str]:
    return {p.slug for p in products(wishlist)}


def _check_rate() -> None:
    limit, window = WRITE_LIMIT
    if not ratelimit.allow("wishlist", ratelimit.client_key(), limit, window):
        raise WishlistError("You're saving very quickly — please wait a moment and try again.")


def _active_product(slug: str) -> Product:
    if not isinstance(slug, str) or not 0 < len(slug) <= 120:
        raise WishlistError("Sorry, that item is not available.")
    product = db.session.scalar(
        select(Product).where(Product.slug == slug, Product.status == ProductStatus.ACTIVE)
    )
    if product is None:
        raise WishlistError("Sorry, that item is not available.")
    return product


def add(slug: str) -> bool:
    """Save a product. Returns False if it was already saved."""
    _check_rate()
    product = _active_product(slug)
    wishlist = current(create=True)
    assert wishlist is not None
    if any(i.product_id == product.id for i in wishlist.items):
        return False
    if len(wishlist.items) >= MAX_ITEMS:
        raise WishlistError(f"Your wishlist is full ({MAX_ITEMS} items). Remove some to add more.")
    wishlist.items.insert(0, WishlistItem(product=product))
    wishlist.updated_at = utcnow()
    db.session.commit()
    g.wishlist_touched = True
    return True


def remove(slug: str) -> bool:
    _check_rate()
    wishlist = current()
    item = next((i for i in wishlist.items if i.product.slug == slug), None) if wishlist else None
    if wishlist is None or item is None:
        return False
    wishlist.items.remove(item)
    wishlist.updated_at = utcnow()
    db.session.commit()
    g.wishlist_touched = True
    return True


def clear() -> None:
    _check_rate()
    wishlist = current()
    if wishlist is not None:
        wishlist.items.clear()
        wishlist.updated_at = utcnow()
        db.session.commit()


def prune(days: int = RETENTION_DAYS) -> int:
    """Delete wishlists untouched for `days`. Returns how many were removed."""
    cutoff = utcnow() - timedelta(days=days)
    stale = select(Wishlist.id).where(Wishlist.updated_at < cutoff)
    db.session.execute(delete(WishlistItem).where(WishlistItem.wishlist_id.in_(stale)))
    result = db.session.execute(delete(Wishlist).where(Wishlist.updated_at < cutoff))
    db.session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]

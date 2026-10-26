"""Import every model module so SQLAlchemy metadata (and Alembic) sees all tables."""

from app.accounts.models import MarketingConsent, PasswordReset, User
from app.cart.models import Cart, CartLine
from app.catalog.models import (
    Category,
    Collection,
    OptionAxis,
    OptionValue,
    Product,
    ProductImage,
    Variant,
)
from app.wishlist.models import Wishlist, WishlistItem

__all__ = [
    "MarketingConsent",
    "PasswordReset",
    "User",
    "Cart",
    "CartLine",
    "Category",
    "Collection",
    "OptionAxis",
    "OptionValue",
    "Product",
    "ProductImage",
    "Variant",
    "Wishlist",
    "WishlistItem",
]

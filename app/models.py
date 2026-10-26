"""Import every model module so SQLAlchemy metadata (and Alembic) sees all tables."""

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

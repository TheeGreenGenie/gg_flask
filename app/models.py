"""Import every model module so SQLAlchemy metadata (and Alembic) sees all tables."""

from app.accounts.models import Address, MarketingConsent, PasswordReset, StoredPaymentMethod, User
from app.admin.models import AdminAudit, AdminRecoveryCode, AdminRole, AdminUser
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
from app.media.models import MediaAsset
from app.orders.models import Coupon, Order, OrderLine, OrderNote, PaymentEvent, StockHold
from app.wishlist.models import Wishlist, WishlistItem

__all__ = [
    "AdminAudit",
    "AdminRecoveryCode",
    "AdminRole",
    "AdminUser",
    "Address",
    "MarketingConsent",
    "PasswordReset",
    "StoredPaymentMethod",
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
    "MediaAsset",
    "Coupon",
    "Order",
    "OrderLine",
    "OrderNote",
    "PaymentEvent",
    "StockHold",
    "Wishlist",
    "WishlistItem",
]

"""Import every model module so SQLAlchemy metadata (and Alembic) sees all tables."""

from app.accounts.models import (
    Address,
    EmailVerification,
    MarketingConsent,
    PasswordReset,
    StoredPaymentMethod,
    User,
)
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
from app.content.models import BlogPost, BlogRevision
from app.kvstore import KVEntry
from app.media.models import MediaAsset
from app.orders.models import Coupon, Order, OrderLine, OrderNote, PaymentEvent, StockHold
from app.reviews.models import (
    ModerationRemoval,
    ModerationWord,
    ProductPost,
    ProductPostPhoto,
    ProductPostReply,
)
from app.studio.models import (
    InventoryHistory,
    PriceHistory,
    ProductHistory,
    StudioAudit,
    StudioChange,
    StudioKeyEnrollment,
    StudioLoginRequest,
    StudioPasskey,
    StudioSale,
    StudioSaleItem,
    StudioSession,
    StudioUser,
)
from app.wishlist.models import Wishlist, WishlistItem

__all__ = [
    "AdminAudit",
    "AdminRecoveryCode",
    "AdminRole",
    "AdminUser",
    "Address",
    "EmailVerification",
    "MarketingConsent",
    "PasswordReset",
    "StoredPaymentMethod",
    "User",
    "Cart",
    "CartLine",
    "BlogPost",
    "BlogRevision",
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
    "ProductPost",
    "ProductPostReply",
    "ProductPostPhoto",
    "ModerationRemoval",
    "ModerationWord",
    "StudioUser",
    "StudioLoginRequest",
    "StudioSession",
    "StudioAudit",
    "StudioChange",
    "StudioKeyEnrollment",
    "StudioPasskey",
    "InventoryHistory",
    "PriceHistory",
    "ProductHistory",
    "StudioSale",
    "StudioSaleItem",
    "Wishlist",
    "WishlistItem",
    "KVEntry",
]

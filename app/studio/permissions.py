"""Which Studio pages each role may use.

The original roles keep their behaviour (decorators in app/studio/__init__.py). The scoped
roles below (owner, 2026-10-27) are page lists so that a person sees and can do only their
own area; combine roles freely. Owners can use everything.
"""

from __future__ import annotations

from app.studio.models import StudioRole, StudioUser

ALWAYS = frozenset(
    {
        "studio.dashboard",
        "studio.password",
        "studio.logout",
        "studio.changes",
        "studio.key",
        "studio.key_request",
        "studio.key_register_options",
        "studio.key_register",
    }
)

SCOPED: dict[StudioRole, frozenset[str]] = {
    # Brand voice and look: blog, product names/descriptions/photos.
    StudioRole.BRANDING: frozenset(
        {
            "studio.blog_posts",
            "studio.blog_edit",
            "studio.blog_preview",
            "studio.blog_publication",
            "studio.blog_revision_restore",
            "studio.products",
            "studio.product_edit",
            "studio.product_image_upload",
            "studio.product_image_remove",
            "studio.product_history",
            "studio.product_undo",
        }
    ),
    # Keeping the shop accurate: stock and taking products on or off the site.
    StudioRole.MAINTENANCE: frozenset(
        {
            "studio.inventory",
            "studio.inventory_csv",
            "studio.inventory_update",
            "studio.inventory_history",
            "studio.inventory_undo",
            "studio.inventory_bulk",
            "studio.inventory_bulk_apply",
            "studio.products",
            "studio.product_visibility",
            "studio.product_bulk",
            "studio.product_bulk_apply",
            "studio.product_history",
            "studio.product_undo",
        }
    ),
    # Selling: prices, sales, and answering customer reviews, comments, and questions.
    StudioRole.SALES: frozenset(
        {
            "studio.prices",
            "studio.price_update",
            "studio.price_history",
            "studio.price_undo",
            "studio.price_bulk",
            "studio.price_bulk_apply",
            "studio.sales",
            "studio.sale_new",
            "studio.sale_preview",
            "studio.sale_confirm",
            "studio.sale_cancel",
            "studio.community",
            "studio.community_reply",
            "studio.community_moderate",
        }
    ),
}


def scoped_allows(user: StudioUser, endpoint: str | None) -> bool:
    if endpoint in ALWAYS:
        return True
    return any(endpoint in SCOPED.get(role, frozenset()) for role in user.role_set)


def can(user: StudioUser | None, endpoint: str, legacy_roles: tuple[StudioRole, ...]) -> bool:
    """Menu/link helper: owner, one of the page's original roles, or a scoped grant."""
    if user is None:
        return False
    roles = user.role_set
    return (
        StudioRole.OWNER in roles
        or bool(roles.intersection(legacy_roles))
        or scoped_allows(user, endpoint)
    )

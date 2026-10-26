"""Homepage editorial content (copy carried over from the current site).

Becomes editable in the content admin (A-009, later); until then it lives here.
"""

from __future__ import annotations

from typing import Final

IMG = "img/home/"

HERO_SLIDES: Final = (IMG + "hero-1.webp", IMG + "hero-2.webp", IMG + "hero-3.webp")

OPENING_CARDS: Final = (
    {"title": "African Prints", "status": "Shop now", "image": IMG + "open-african-prints.webp",
     "category": "fabrics"},
    {"title": "Bags & Shoes", "status": "Coming soon", "image": IMG + "open-bags-shoes.webp"},
    {"title": "Custom Sewing", "status": "Coming soon", "image": IMG + "open-custom-sewing.webp"},
    {"title": "Beauty & Lifestyle", "status": "Coming soon", "image": IMG + "open-beauty.webp"},
)  # fmt: skip

STORY_COLLECTIONS: Final = (
    {
        "category": "adire",
        "eyebrow": "Fabric collection",
        "description": "Indigo-rich patterns shaped by the expressive tradition of resist-dyed "
        "cloth.",
    },
    {
        "category": "cushion-covers",
        "eyebrow": "Home décor",
        "description": "Bold African prints designed to bring warmth and rhythm into your living "
        "space.",
    },
)

FEATURED_CATEGORIES: Final = ("sarong-wraps", "adire", "jackets")

# The old "Shop by cut" tab is gone: cut lengths are no longer categories. Fabric is sold
# in fixed bundles at a set price (owner, 2026-10-26).
SHOP_GROUPS: Final = (
    ("print", "Print", "Shop by print", ("ankara", "kente", "satin-chiffon", "ethnic-prints")),
    (
        "category",
        "Category",
        "Shop by category",
        ("fabrics", "clothing", "home-accessories", "adire"),
    ),
)

HOME_CATEGORY_SLUGS: Final = sorted(
    {c["category"] for c in STORY_COLLECTIONS}
    | set(FEATURED_CATEGORIES)
    | {s for *_, slugs in SHOP_GROUPS for s in slugs}
)

# Journal posts (no blog listing page at launch, decision #86).
POSTS: Final = (
    {
        "title": "How to Make the Most of Our October Sales",
        "date": "2024-10-01",
        "date_label": "October 1, 2024",
        "image": IMG + "post-promo.webp",
        "excerpt": "October is here, and so are our amazing sales at GG Afrikan Eleganz! This "
        "month, we're excited to offer exclusive deals on our most-loved prints.",
    },
    {
        "title": "The Cultural Significance Behind Ankara Prints",
        "date": "2024-10-01",
        "date_label": "Oct 1, 2024",
        "image": IMG + "post-ankara.webp",
        "excerpt": "Ankara prints carry stories of trade, identity and celebration across "
        "West Africa.",
    },
)

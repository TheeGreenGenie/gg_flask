"""Store-wide content shown in the header and footer.

The menu is hand-written (catalog decision D-008) and is not generated from
categories. Category entries hold a category slug; plain entries hold a page key.
"""

from __future__ import annotations

from typing import Final

MAIN_MENU: Final = (
    {
        "label": "Real African Prints Shop",
        "children": (
            ("Ankara Prints", "ankara"),
            ("Kente Prints", "kente"),
            ("Adire (Tie-Dye)", "adire"),
            ("Satin / Chiffon", "satin-chiffon"),
            ("Ethnic Prints", "ethnic"),
        ),
    },
    {
        "label": "Clothing",
        "children": (("Skirts", "clothing"), ("Reversible Jackets", "reversible-jacket")),
    },
    {
        "label": "Accessories",
        "children": (("Cushion Covers", "cushion-covers"), ("Sarong Wraps", "sarong-wrap")),
    },
    {"label": "New Arrivals", "page": "new"},
    {"label": "Sale", "page": "sale"},
)

FOOTER_SHOP: Final = (
    ("Ankara Fabrics", "ankara"),
    ("Cushion Covers", "cushion-covers"),
    ("Clothing", "clothing"),
    ("Accessories", "sarong-wrap"),
)

# (label, path) — content pages are built in Phase 4; Support is merged into Contact (#87).
FOOTER_QUICK: Final = (
    ("About Us", "/about-us/"),
    ("Contact Us", "/contact-us/"),
    ("FAQ", "/faq/"),
)

FOOTER_POLICIES: Final = (
    ("Privacy Policy", "/privacy-policy/"),
    ("Terms & Conditions", "/terms-of-service/"),
    ("Shipping Information", "/shipping-information/"),
    ("Returns & Exchanges", "/returns-exchanges/"),
)

SOCIAL_LINKS: Final = (
    ("facebook", "Facebook", "https://www.facebook.com/ggafrikaneleganz"),
    ("instagram", "Instagram", "https://www.instagram.com/ggafrikaneleganz/"),
    ("pinterest", "Pinterest", "https://www.pinterest.com/ggafrikan/"),
    ("tiktok", "TikTok", "https://www.tiktok.com/@lydiaade6"),
)

CONTACT_EMAIL: Final = "info@ggafrikaneleganz.com"
CONTACT_PHONE: Final = "+1 215-984-2270"

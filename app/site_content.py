"""Store-wide content shown in the header and footer.

The menu is hand-written (catalog decision D-008) and is not generated from
categories. Entries link to a category slug, a collection slug or a fixed URL.
"""

from __future__ import annotations

from typing import Final

MAIN_MENU: Final = (
    {
        "label": "Real African Prints Shop",
        "category": "fabrics",
        "children": (
            ("Ankara Prints", "ankara"),
            ("Kente Prints", "kente"),
            ("Adire (Tie-Dye)", "adire"),
            ("Satin & Chiffon", "satin-chiffon"),
            ("Ethnic Prints", "ethnic-prints"),
        ),
    },
    {
        "label": "Clothing",
        "category": "clothing",
        "children": (("Skirts", "skirts"), ("Jackets", "jackets")),
    },
    {
        "label": "Accessories",
        "category": "home-accessories",
        "children": (("Cushion Covers", "cushion-covers"), ("Sarong Wraps", "sarong-wraps")),
    },
    {"label": "Find Fabrics", "url": "/find-fabrics/"},
    {"label": "New Arrivals", "collection": "new-arrivals"},
    {"label": "Sale", "collection": "sale"},
)

FOOTER_SHOP: Final = (
    ("Ankara Fabrics", "ankara"),
    ("Cushion Covers", "cushion-covers"),
    ("Clothing", "clothing"),
    ("Accessories", "home-accessories"),
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

# Owner, 2026-10-27: everything goes to the store Gmail until the domain mailboxes
# (info@/returns@/support@ggafrikaneleganz.com) are set up. Shown on the site and used as the
# contact-form recipient and the default owner notification address (config).
CONTACT_EMAIL: Final = "ggafrikan@gmail.com"
CONTACT_PHONE: Final = "+1 215-984-2270"

COUNTRY_NAMES: Final = {
    "US": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
}

US_STATES: Final = (
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("DC", "District of Columbia"), ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
    ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"),
    ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"), ("MD", "Maryland"),
    ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"), ("MS", "Mississippi"),
    ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"), ("OK", "Oklahoma"),
    ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"),
    ("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"),
    ("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"),
    ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
)  # fmt: skip

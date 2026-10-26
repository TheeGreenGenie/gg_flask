"""Sample data for the Phase 2 design prototypes.

Product names, prices and photos come from the old store so the prototypes look
real. This is not the launch catalog: real products arrive through the launch
manifest in Phase 3, and category names follow the decision worksheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

IMG = "img/proto/"


@dataclass(frozen=True)
class Variant:
    sku: str
    label: str
    price_cents: int
    stock: int
    image: str


@dataclass(frozen=True)
class Product:
    slug: str
    name: str
    category: str
    price_cents: int
    image: str
    summary: str
    regular_cents: int | None = None  # set when on sale
    stock: int = 10
    sku: str = ""
    option_name: str = ""
    variants: tuple[Variant, ...] = ()
    gallery: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    is_new: bool = False

    @property
    def on_sale(self) -> bool:
        return self.regular_cents is not None and self.regular_cents > self.price_cents

    @property
    def images(self) -> tuple[str, ...]:
        return (self.image, *self.gallery)


@dataclass(frozen=True)
class Category:
    slug: str
    name: str
    image: str
    count: int


@dataclass(frozen=True)
class Post:
    slug: str
    title: str
    date: str
    date_label: str
    image: str
    excerpt: str


@dataclass
class CartLine:
    product: Product
    quantity: int
    variant: Variant | None = None

    @property
    def unit_cents(self) -> int:
        return self.variant.price_cents if self.variant else self.product.price_cents

    @property
    def total_cents(self) -> int:
        return self.unit_cents * self.quantity


@dataclass
class Cart:
    lines: list[CartLine] = field(default_factory=list)
    shipping_cents: int = 899

    @property
    def count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def subtotal_cents(self) -> int:
        return sum(line.total_cents for line in self.lines)

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents + self.shipping_cents


ANKARA_DETAILS = (
    "Authentic African wax print fabric.",
    "Ideal for crafting, quilting, head wraps and masks.",
    '100% cotton. Sold by the yard, 36" × 45".',
)

PRODUCTS: tuple[Product, ...] = (
    Product(
        slug="african-ankara-print-101025",
        name="African Ankara Print - 101025",
        category="ankara",
        price_cents=899,
        image=IMG + "p-ankara-101025.webp",
        summary="Authentic African wax print fabric, sold by the yard.",
        sku="101025",
        option_name="Colorway",
        variants=tuple(
            Variant(f"101025-{n}", f"Colorway {n}", 899, stock, IMG + f"p-ankara-101025-{n}.webp")
            for n, stock in ((1, 12), (2, 3), (3, 0), (4, 8), (5, 20))
        ),
        details=ANKARA_DETAILS,
        is_new=True,
    ),
    Product(
        slug="wrap-skirt-wr-2002-101196",
        name="Wrap Skirt WR 2002 - 101196",
        category="clothing",
        price_cents=2500,
        image=IMG + "p-wrap-skirt.webp",
        summary="A flowing Ankara wrap skirt that ties at the waist.",
        sku="101196",
        is_new=True,
    ),
    Product(
        slug="african-ankara-print-101054",
        name="African Ankara Print - 101054",
        category="ankara",
        price_cents=899,
        image=IMG + "p-ankara-101054.webp",
        summary="Authentic African wax print fabric, sold by the yard.",
        sku="101054",
        details=ANKARA_DETAILS,
        is_new=True,
    ),
    Product(
        slug="cushion-cover-g-3",
        name="Cushion Cover G-3",
        category="cushion-covers",
        price_cents=750,
        regular_cents=950,
        image=IMG + "p-cushion-g3.webp",
        summary="African ethnic pillowcase for home decor or sofa cushions. 450 × 450 mm.",
        is_new=True,
    ),
    Product(
        slug="adire-print-101219",
        name="Adire Print - 101219",
        category="adire",
        price_cents=4000,
        image=IMG + "p-adire-101219.webp",
        summary="Hand-dyed indigo Adire fabric.",
        sku="101219",
    ),
    Product(
        slug="adire-print-101218",
        name="Adire Print - 101218",
        category="adire",
        price_cents=4000,
        image=IMG + "p-adire-101218.webp",
        summary="Hand-dyed indigo Adire fabric.",
        sku="101218",
        stock=2,
    ),
    Product(
        slug="african-ankara-print-101068",
        name="African Ankara Print - 101068",
        category="ankara",
        price_cents=799,
        regular_cents=899,
        image=IMG + "p-ankara-101068.webp",
        summary="Authentic African wax print fabric, sold by the yard.",
        sku="101068",
        details=ANKARA_DETAILS,
    ),
    Product(
        slug="cushion-cover-g-4",
        name="Cushion Cover G-4",
        category="cushion-covers",
        price_cents=750,
        image=IMG + "p-cushion-g4.webp",
        summary="African ethnic pillowcase for home decor or sofa cushions. 450 × 450 mm.",
        stock=0,
    ),
    Product(
        slug="adire-print-101217",
        name="Adire Print - 101217",
        category="adire",
        price_cents=4000,
        image=IMG + "p-adire-101217.webp",
        summary="Hand-dyed indigo Adire fabric.",
        sku="101217",
    ),
    Product(
        slug="african-ankara-print-101066",
        name="African Ankara Print - 101066",
        category="ankara",
        price_cents=899,
        image=IMG + "p-ankara-101066.webp",
        summary="Authentic African wax print fabric, sold by the yard.",
        sku="101066",
        details=ANKARA_DETAILS,
    ),
    Product(
        slug="adire-print-101216",
        name="Adire Print - 101216",
        category="adire",
        price_cents=4000,
        image=IMG + "p-adire-101216.webp",
        summary="Hand-dyed indigo Adire fabric.",
        sku="101216",
    ),
)

CATEGORIES: dict[str, Category] = {
    c.slug: c
    for c in (
        Category("ankara", "Ankara Prints", IMG + "cat-ankara.webp", 128),
        Category("kente", "Kente Prints", IMG + "cat-kente.webp", 10),
        Category("satin-chiffon", "Satin / Chiffon", IMG + "cat-satin.webp", 21),
        Category("ethnic", "Ethnic Prints", IMG + "cat-ethnic.webp", 31),
        Category("adire", "Adire Fabric (Tie-Dye)", IMG + "cat-adire.webp", 13),
        Category("by-the-yard", "By the Yard", IMG + "cat-by-the-yard.webp", 135),
        Category("3-yards", "3 Yards", IMG + "cat-3-yards.webp", 14),
        Category("6-yards", "6 Yards", IMG + "cat-6-yards.webp", 33),
        Category("pre-cuts", "Pre-Cuts", IMG + "cat-pre-cuts.webp", 0),
        Category("ankara-plus", "Ankara Plus", IMG + "cat-ankara-plus.webp", 11),
        Category("floral", "Floral Designs", IMG + "cat-floral.webp", 5),
        Category("final-cut", "Final Cut", IMG + "cat-final-cut.webp", 6),
        Category("classic", "Classic Designs", IMG + "cat-classic.webp", 6),
        Category("reversible-jacket", "Reversible Jacket", IMG + "cat-reversible-jacket.webp", 5),
        Category("sarong-wrap", "Sarong Wrap", IMG + "cat-sarong.webp", 4),
        Category("cushion-covers", "Cushion Covers", IMG + "p-cushion-g3.webp", 6),
        Category("clothing", "Clothing", IMG + "cat-reversible-jacket.webp", 13),
    )
}

HERO_SLIDES = (IMG + "hero.webp", IMG + "open-african-prints.webp", IMG + "cat-ankara.webp")

OPENING_CARDS = (
    {
        "title": "African Prints",
        "status": "Shop now",
        "image": IMG + "open-african-prints.webp",
        "available": True,
    },
    {
        "title": "Bags & Shoes",
        "status": "Coming soon",
        "image": IMG + "open-bags-shoes.webp",
        "available": False,
    },
    {
        "title": "Custom Sewing",
        "status": "Coming soon",
        "image": IMG + "open-custom-sewing.webp",
        "available": False,
    },
    {
        "title": "Beauty & Lifestyle",
        "status": "Coming soon",
        "image": IMG + "open-beauty.webp",
        "available": False,
    },
)

STORY_COLLECTIONS = (
    {
        "category": "adire",
        "title": "Adire Prints",
        "eyebrow": "Fabric collection",
        "description": (
            "Indigo-rich patterns shaped by the expressive tradition of resist-dyed cloth."
        ),
    },
    {
        "category": "cushion-covers",
        "title": "Cushion Covers",
        "eyebrow": "Home décor",
        "description": (
            "Bold African prints designed to bring warmth and rhythm into your living space."
        ),
    },
)

FEATURED_CATEGORIES = ("sarong-wrap", "adire", "reversible-jacket")

SHOP_GROUPS = (
    ("print", "Print", "Shop by print", ("ankara", "kente", "satin-chiffon", "ethnic")),
    ("cut", "Cut", "Shop by cut", ("by-the-yard", "3-yards", "6-yards", "pre-cuts")),
    (
        "collection",
        "Collection",
        "Shop by collection",
        ("ankara-plus", "floral", "final-cut", "classic"),
    ),
)

POSTS = (
    Post(
        "how-to-make-the-most-of-our-october-sales",
        "How to Make the Most of Our October Sales",
        "2024-10-01",
        "October 1, 2024",
        IMG + "post-promo.webp",
        "October is here, and so are our amazing sales at GG Afrikan Eleganz! "
        "This month, we're excited to offer exclusive deals on our most-loved prints.",
    ),
    Post(
        "the-cultural-significance-behind-ankara-prints",
        "The Cultural Significance Behind Ankara Prints",
        "2024-10-01",
        "Oct 1, 2024",
        IMG + "post-ankara.webp",
        "Ankara prints carry stories of trade, identity and celebration across West Africa.",
    ),
)


def product(slug: str) -> Product | None:
    return next((p for p in PRODUCTS if p.slug == slug), None)


def sample_cart() -> Cart:
    ankara = PRODUCTS[0]
    return Cart(
        lines=[
            CartLine(ankara, 3, ankara.variants[0]),
            CartLine(PRODUCTS[1], 1),
            CartLine(PRODUCTS[3], 2),
        ]
    )

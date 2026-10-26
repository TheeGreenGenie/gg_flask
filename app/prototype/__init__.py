"""Phase 2 design prototypes served from fixture data.

Registered only when PROTOTYPES_ENABLED is true (development and testing). The
pages exist so the owner can approve the look before real routes are built.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app.prototype import fixtures as fx

bp = Blueprint("prototype", __name__, url_prefix="/prototype")

PAGE_SIZE = 8
SORTS = {
    "default": ("Default sorting", None),
    "newest": ("Sort by latest", lambda p: (not p.is_new, p.name)),
    "price-asc": ("Sort by price: low to high", lambda p: p.price_cents),
    "price-desc": ("Sort by price: high to low", lambda p: -p.price_cents),
}


@bp.context_processor
def prototype_globals() -> dict[str, Any]:
    return {"cart": fx.sample_cart(), "shop_endpoint": "prototype.shop"}


@bp.get("/")
def index() -> str:
    return render_template("prototype/index.html")


@bp.get("/home")
def home() -> str:
    by_slug = fx.CATEGORIES
    return render_template(
        "prototype/home.html",
        hero_slides=fx.HERO_SLIDES,
        opening_cards=fx.OPENING_CARDS,
        new_products=[p for p in fx.PRODUCTS if p.is_new][:4],
        featured_products=list(fx.PRODUCTS[4:8]),
        story_collections=[(c, by_slug[c["category"]]) for c in fx.STORY_COLLECTIONS],
        featured_categories=[by_slug[s] for s in fx.FEATURED_CATEGORIES],
        shop_groups=[
            (k, label, title, [by_slug[s] for s in slugs])
            for k, label, title, slugs in fx.SHOP_GROUPS
        ],
        posts=fx.POSTS,
    )


@bp.get("/shop")
def shop() -> str:
    category = fx.CATEGORIES.get(request.args.get("category", ""))
    page_key = request.args.get("page_key", "")
    query = request.args.get("q", "").strip()[:100]
    in_stock = request.args.get("in_stock") == "1"
    sort = request.args.get("sort", "default")
    if sort not in SORTS:
        sort = "default"

    products = list(fx.PRODUCTS)
    if category:
        products = [p for p in products if p.category == category.slug]
    if page_key == "sale":
        products = [p for p in products if p.on_sale]
    elif page_key == "new":
        products = [p for p in products if p.is_new]
    if query:
        products = [p for p in products if query.lower() in p.name.lower() or query in p.sku]
    if in_stock:
        products = [p for p in products if p.stock > 0]
    key = SORTS[sort][1]
    if key:
        products.sort(key=key)

    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    pages = max(1, -(-len(products) // PAGE_SIZE))
    page = min(page, pages)

    if category:
        title = category.name
    elif page_key == "sale":
        title = "Sale"
    elif page_key == "new":
        title = "New Arrivals"
    elif query:
        title = f"Search results for “{query}”"
    else:
        title = "Shop"

    return render_template(
        "prototype/shop.html",
        title=title,
        category=category,
        page_key=page_key,
        query=query,
        in_stock=in_stock,
        sort=sort,
        sorts=SORTS,
        products=products[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
        total=len(products),
        page=page,
        pages=pages,
        filter_categories=[
            fx.CATEGORIES[s] for s in ("ankara", "adire", "cushion-covers", "clothing")
        ],
    )


@bp.get("/product/<slug>/")
def product(slug: str) -> str:
    item = fx.product(slug)
    if item is None:
        abort(404)
    related = [p for p in fx.PRODUCTS if p.slug != slug][:8]
    return render_template(
        "prototype/product.html",
        product=item,
        category=fx.CATEGORIES.get(item.category),
        related=related,
    )


@bp.post("/cart/add")
def cart_add() -> Response:
    flash("Added to your cart.", "success")
    return redirect(url_for("prototype.cart"))


@bp.get("/cart")
def cart() -> str:
    return render_template("prototype/cart.html")


@bp.get("/checkout")
def checkout() -> str:
    return render_template("prototype/checkout.html", hide_newsletter=True)


@bp.post("/newsletter")
def newsletter() -> Response:
    flash("Thanks for subscribing. Look out for our next email.", "success")
    return redirect(request.referrer if _is_local(request.referrer) else url_for("prototype.home"))


def _is_local(url: str | None) -> bool:
    return bool(url) and url.startswith(request.host_url)  # type: ignore[union-attr]

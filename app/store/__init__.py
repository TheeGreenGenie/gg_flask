"""Public storefront: home, browse, product, cart and checkout pages."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import site_content
from app.cart import service as cart_service
from app.catalog import queries
from app.catalog.models import Product
from app.store import content
from app.templating import money

bp = Blueprint("store", __name__)


def _media_url(path: str | None) -> str:
    if not path:
        return url_for("static", filename="img/placeholder.svg")
    return url_for("media.media_file", path=path)


@bp.app_context_processor
def store_globals() -> dict[str, Any]:
    cart = cart_service.view(cart_service.current_cart())
    return {"media_url": _media_url, "cart": cart}


@bp.after_app_request
def _cart_cookie(response: Response) -> Response:
    return cart_service.set_cookie_if_new(response)


# ---------- Browse ----------


@bp.get("/")
def home() -> str:
    cats = {c.slug: c for c in queries.categories_by_slug(content.HOME_CATEGORY_SLUGS)}
    counts = queries.category_counts()
    return render_template(
        "store/home.html",
        hero_slides=content.HERO_SLIDES,
        opening_cards=content.OPENING_CARDS,
        new_products=queries.newest(4),
        featured_products=queries.featured(4),
        # Sections skip categories that are not in the catalog (e.g. removed from the manifest).
        story_collections=[
            (meta, cats[meta["category"]])
            for meta in content.STORY_COLLECTIONS
            if meta["category"] in cats
        ],
        featured_categories=[cats[s] for s in content.FEATURED_CATEGORIES if s in cats],
        shop_groups=[
            (key, label, title, [cats[s] for s in slugs if s in cats])
            for key, label, title, slugs in content.SHOP_GROUPS
        ],
        counts=counts,
        posts=content.POSTS,
    )


def _listing_args() -> dict[str, Any]:
    sort = request.args.get("sort", "default")
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    return {
        "query": request.args.get("q", "").strip()[:100],
        "in_stock": request.args.get("in_stock") == "1",
        "sort": sort if sort in queries.SORTS else "default",
        "page": page,
    }


def _render_listing(title: str, description: str = "", **kwargs: Any) -> str:
    args = _listing_args()
    category = kwargs.get("category")
    collection = kwargs.get("collection_slug")
    cats = None
    if category is not None:
        cats = [category, *category.children]
    page = queries.listing(categories=cats, collection=collection, **args)
    if args["query"] and category is None and collection is None:
        title = f"Search results for “{args['query']}”"
    return render_template(
        "store/listing.html",
        title=title,
        description=description,
        result=page,
        sorts=queries.SORTS,
        filter_categories=queries.top_categories(),
        counts=queries.category_counts(),
        endpoint=request.endpoint,
        view_args=request.view_args or {},
        **args,
        **kwargs,
    )


@bp.get("/shop/")
def shop() -> str:
    return _render_listing("Shop")


@bp.get("/shop/<slug>/")
def category(slug: str) -> str:
    cat = queries.category_by_slug(slug)
    if cat is None:
        abort(404)
    return _render_listing(cat.name, cat.description, category=cat)


@bp.get("/collections/<slug>/")
def collection(slug: str) -> str:
    if slug in queries.VIRTUAL_COLLECTIONS:
        name, description = queries.VIRTUAL_COLLECTIONS[slug]
        return _render_listing(name, description, collection_slug=slug)
    col = queries.collection_by_slug(slug)
    if col is None:
        abort(404)
    return _render_listing(col.name, col.description, collection_slug=slug, collection=col)


@bp.get("/product/<slug>/")
def product(slug: str) -> str:
    item = queries.product_by_slug(slug)
    if item is None:
        abort(404)
    return render_template(
        "store/product.html",
        product=item,
        category=item.primary_category,
        gallery=_gallery(item),
        related=queries.related(item),
    )


def card_json(p: Product) -> dict[str, Any]:
    """Minimal product fields for client-side cards (Find Fabrics, wishlist)."""
    category = p.primary_category
    return {
        "slug": p.slug,
        "name": p.name,
        "price": money(p.price_cents),
        "compare_at": money(p.compare_at_cents) if p.compare_at_cents else None,
        "image": _media_url(p.cover.path if p.cover else None),
        "alt": p.cover.alt if p.cover else p.name,
        "category": category.name if category else "",
        "category_url": url_for("store.category", slug=category.slug)
        if category
        else url_for("store.shop"),
        "url": url_for("store.product", slug=p.slug),
        "preview": url_for("store.product_preview", slug=p.slug),
    }


@bp.get("/find-fabrics/")
def find_fabrics() -> str:
    """Swipe-to-save fabric discovery. The first batch is server-rendered for no-JS visitors."""
    items, _ = queries.discovery(0)
    return render_template("store/find_fabrics.html", products=items)


@bp.get("/api/v1/discovery/fabrics")
def discovery_feed() -> Response:
    """Bounded, minimal JSON feed for the Find Fabrics card stack (W-003)."""
    try:
        offset = int(request.args.get("cursor", "0"))
    except ValueError:
        offset = 0
    items, next_offset = queries.discovery(min(max(offset, 0), 10_000))
    payload = [card_json(p) for p in items]
    resp = jsonify(items=payload, next_cursor=next_offset)
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.get("/product/<slug>/preview")
def product_preview(slug: str) -> str:
    """Quick-view fragment loaded into a dialog when a product card is clicked."""
    item = queries.product_by_slug(slug)
    if item is None:
        abort(404)
    return render_template("store/_preview.html", product=item, gallery=_gallery(item))


def _gallery(item: Product) -> list[str]:
    paths = [img.path for img in item.images]
    for v in item.active_variants:  # variant photos are always reachable from the gallery
        if v.image and v.image.path not in paths:
            paths.append(v.image.path)
    return paths


# ---------- Cart ----------


@bp.get("/cart/")
def cart() -> str:
    return render_template("store/cart.html")


@bp.post("/cart/add")
def cart_add() -> Response | tuple[Response, int]:
    """Add to cart. Browsers get a redirect; the quick-view preview asks for JSON."""
    wants_json = request.accept_mimetypes.best == "application/json"
    try:
        variant_id = int(request.form.get("variant", ""))
        quantity = int(request.form.get("quantity", "1"))
        _, message = cart_service.add(variant_id, quantity)
    except ValueError as exc:  # includes CartError
        msg = str(exc) if isinstance(exc, cart_service.CartError) else "Please choose an option."
        if wants_json:
            return jsonify(ok=False, message=msg), 400
        flash(msg, "error")
        return redirect(_safe_back(url_for("store.shop")))
    if wants_json:
        view = cart_service.view(cart_service.current_cart())
        return jsonify(
            ok=True,
            message=message,
            count=view.count,
            subtotal=money(view.subtotal_cents),
            cart_url=url_for("store.cart"),
        )
    flash(message, "success")
    return redirect(url_for("store.cart"))


@bp.post("/cart/update")
def cart_update() -> Response:
    if request.form.get("remove"):
        try:
            cart_service.remove(int(request.form["remove"]))
            flash("Item removed.", "success")
        except ValueError:
            pass
        return redirect(url_for("store.cart"))
    quantities: dict[int, int] = {}
    for key, value in request.form.items():
        if key.startswith("qty-"):
            try:
                quantities[int(key[4:])] = int(value)
            except ValueError:
                continue
    for message in cart_service.update(quantities):
        flash(message, "error")
    flash("Cart updated.", "success")
    return redirect(url_for("store.cart"))


@bp.get("/checkout/")
def checkout() -> Response | str:
    view = cart_service.view(cart_service.current_cart())
    if view.is_empty:
        flash("Your cart is empty.", "error")
        return redirect(url_for("store.cart"))
    return render_template(
        "store/checkout.html", hide_newsletter=True, us_states=site_content.US_STATES
    )


@bp.post("/newsletter")
def newsletter() -> Response:
    # Mailchimp is connected in Phase 9; for now acknowledge without storing anything.
    flash("Thanks for subscribing. Look out for our next email.", "success")
    return redirect(_safe_back(url_for("store.home")))


def _safe_back(default: str) -> str:
    ref = request.referrer or ""
    return ref if ref.startswith(request.host_url) else default

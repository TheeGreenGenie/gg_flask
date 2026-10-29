"""Public storefront: home, browse, product, cart and checkout pages."""

from __future__ import annotations

import secrets
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select
from werkzeug.wrappers import Response

from app import address_lookup, cache, marketing, ratelimit, site_content
from app.accounts import service as account_service
from app.assets import media_version
from app.cart import service as cart_service
from app.catalog import queries
from app.catalog.models import Product
from app.content import blog
from app.extensions import csrf, db
from app.orders import payments, providers
from app.orders import service as order_service
from app.orders.models import Order, PaymentProvider, PaymentStatus
from app.reviews import images as review_images
from app.reviews import service as review_service
from app.store import content
from app.templating import money

bp = Blueprint("store", __name__)


def _media_url(path: str | None) -> str:
    if not path:
        return url_for("static", filename="img/placeholder.svg")
    version = media_version(path)
    if version:
        return url_for("media.media_file", path=path, v=version)
    return url_for("media.media_file", path=path)


@bp.app_context_processor
def store_globals() -> dict[str, Any]:
    cart = cart_service.view(cart_service.current_cart())
    return {"media_url": _media_url, "cart": cart}


@bp.after_app_request
def _cart_cookie(response: Response) -> Response:
    if request.path.startswith("/checkout"):
        response.headers["Cache-Control"] = "private, no-store"
    return cart_service.set_cookie_if_new(response)


# ---------- Browse ----------


@bp.get("/")
def home() -> str:
    cats = {c.slug: c for c in queries.categories_by_slug(content.HOME_CATEGORY_SLUGS)}
    counts = queries.category_counts()
    return render_template(
        "store/home.html",
        hero_slides=content.HERO_SLIDES,
        opening_cards=[(card, _card_photos(card.get("photos"))) for card in content.OPENING_CARDS],
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
        posts=blog.public_posts(),
    )


def _card_photos(kind: str | None) -> list[Product]:
    """Products whose cover photos fill a homepage card (see content.OPENING_CARDS)."""
    if kind == "newest":
        return queries.newest_fabrics(content.CARD_CYCLE_LENGTH)
    if kind == "priciest":
        return queries.priciest_fabrics(content.CARD_CYCLE_LENGTH)
    if kind == "random":
        pick = queries.random_fabric()
        return [pick] if pick else []
    return []


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
        cats = category.subtree()  # a group's page shows everything under it, at any depth
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
    rating_average, rating_count = review_service.rating_summary(item)
    return render_template(
        "store/product.html",
        product=item,
        category=item.primary_category,
        gallery=_gallery(item),
        related=queries.related(item),
        product_posts=review_service.public_posts(item),
        review_photos=review_images.photos_enabled(),
        rating_average=rating_average,
        rating_count=rating_count,
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
    categories = queries.discovery_categories()
    selected = request.args.get("category", "")
    if selected not in {c["slug"] for c in categories}:
        selected = queries.DISCOVERY_ROOT
    items, _ = queries.discovery(0, category=selected)
    return render_template(
        "store/find_fabrics.html", products=items, categories=categories, selected=selected
    )


@bp.get("/api/v1/address-suggestions")
def address_suggestions() -> tuple[Response, int]:
    """Address autocomplete for our own checkout/address forms only (see app/address_lookup)."""
    if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        return jsonify(items=[]), 403  # other websites may not spend our lookups
    try:
        items = address_lookup.suggest(
            request.args.get("q", ""), request.args.get("country", "")[:2]
        )
    except address_lookup.Limited:
        return jsonify(items=[], retry_after=address_lookup.IP_WINDOW), 429
    except address_lookup.Unavailable:
        return jsonify(items=[]), 503
    response = jsonify(items=items)
    response.headers["Cache-Control"] = "private, no-store"  # addresses are personal data
    return response, 200


@bp.get("/api/v1/discovery/fabrics")
def discovery_feed() -> Response:
    """Bounded, minimal JSON feed for the Find Fabrics card stack (W-003). Cached in Redis,
    by browsers/CDNs for a minute, and revalidated with an ETag."""
    try:
        offset = min(max(int(request.args.get("cursor", "0")), 0), 10_000)
    except ValueError:
        offset = 0
    category = request.args.get("category", "")[:80] or queries.DISCOVERY_ROOT

    def compute() -> dict[str, Any]:
        items, next_offset = queries.discovery(offset, category=category)
        return {"items": [card_json(p) for p in items], "next_cursor": next_offset}

    data = cache.cached_json(f"discovery:{category}:{offset}", compute)
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "public, max-age=60"
    resp.add_etag()
    return resp.make_conditional(request)


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


@bp.route("/checkout/", methods=["GET", "POST"])
def checkout() -> Response | str:
    view = cart_service.view(cart_service.current_cart())
    if view.is_empty:
        flash("Your cart is empty.", "error")
        return redirect(url_for("store.cart"))
    error = ""
    form: Any = request.form
    if request.method == "GET":
        user = account_service.current_user()
        default_address = (
            next((address for address in user.addresses if address.is_default), None)
            if user
            else None
        )
        if user:
            form = {
                "email": user.email,
                "first_name": default_address.first_name if default_address else user.first_name,
                "last_name": default_address.last_name if default_address else "",
                "address1": default_address.address1 if default_address else "",
                "address2": default_address.address2 if default_address else "",
                "city": default_address.city if default_address else "",
                "region": default_address.region if default_address else "",
                "postcode": default_address.postcode if default_address else "",
                "country": default_address.country if default_address else "",
                "phone": (default_address.phone if default_address else user.phone) or "",
            }
    nonce = (
        form.get("checkout_nonce", "") if request.method == "POST" else secrets.token_urlsafe(24)
    )
    if request.method == "POST":
        try:
            if form.get("terms") != "1":
                raise order_service.CheckoutError("You must agree to the terms and conditions.")
            address = order_service.address_from_form(form.to_dict())
            provider = PaymentProvider(form.get("payment", ""))
            cart_model = cart_service.current_cart()
            if cart_model is None:
                raise order_service.CheckoutError("Your cart is empty.")
            order = order_service.create_order(
                cart_model,
                address,
                provider,
                nonce,
                form.get("coupon", ""),
                form.get("shipping", providers.STANDARD),
            )
        except (order_service.CheckoutError, ValueError) as exc:
            db.session.rollback()
            error = (
                str(exc)
                if isinstance(exc, order_service.CheckoutError)
                else "Choose a payment method."
            )
        else:
            allowed = set(session.get("checkout_orders", []))
            allowed.add(order.public_id)
            session["checkout_orders"] = list(allowed)[-10:]
            return redirect(url_for("store.order_payment", public_id=order.public_id))
    countries = [
        (code, site_content.COUNTRY_NAMES.get(code, code))
        for code in current_app.config.get("SUPPORTED_COUNTRIES", ())
    ]
    configured = (
        current_app.config.get("FLAT_SHIPPING_CENTS") is not None
        and current_app.config.get("TAX_BACKEND") is not None
        and bool(countries)
    )
    return render_template(
        "store/checkout.html",
        hide_newsletter=True,
        us_states=site_content.US_STATES,
        countries=countries,
        shipping_cents=current_app.config.get("FLAT_SHIPPING_CENTS"),
        local_shipping_cents=current_app.config.get("LOCAL_SHIPPING_CENTS"),
        shipping_backend=current_app.config.get("SHIPPING_BACKEND", "flat"),
        express_available=providers.express_available(),
        configured=configured,
        checkout_nonce=nonce,
        error=error,
        form=form,
    )


@bp.get("/checkout/order/<public_id>/payment")
def order_payment(public_id: str) -> str:
    order = db.session.scalar(select(Order).where(Order.public_id == public_id))
    if order is None or public_id not in set(session.get("checkout_orders", [])):
        abort(404)
    return render_template(
        "store/payment.html",
        order=order,
        provider_configured=payments.provider_configured(order.payment_provider),
        hide_newsletter=True,
    )


@bp.post("/checkout/order/<public_id>/payment")
def begin_order_payment(public_id: str) -> Response:
    order = db.session.scalar(select(Order).where(Order.public_id == public_id))
    if order is None or public_id not in set(session.get("checkout_orders", [])):
        abort(404)
    try:
        hosted = payments.begin(
            order,
            url_for("store.payment_return", public_id=public_id, _external=True),
            url_for("store.order_payment", public_id=public_id, _external=True),
        )
    except payments.PaymentError as exc:
        flash(str(exc), "error")
        return redirect(url_for("store.order_payment", public_id=public_id))
    return redirect(hosted.url)


@bp.get("/checkout/order/<public_id>/return")
def payment_return(public_id: str) -> Response:
    order = db.session.scalar(select(Order).where(Order.public_id == public_id))
    if order is None or public_id not in set(session.get("checkout_orders", [])):
        abort(404)
    if order.payment_provider == PaymentProvider.PAYPAL and order.payment_status.value != "paid":
        try:
            payments.capture_paypal(order)
        except payments.PaymentError as exc:
            flash(str(exc), "error")
            return redirect(url_for("store.order_payment", public_id=public_id))
    return redirect(url_for("store.order_confirmation", public_id=public_id))


@bp.get("/checkout/order/<public_id>/confirmation")
def order_confirmation(public_id: str) -> str:
    order = db.session.scalar(select(Order).where(Order.public_id == public_id))
    if order is None or public_id not in set(session.get("checkout_orders", [])):
        abort(404)
    return render_template("store/confirmation.html", order=order, hide_newsletter=True)


@bp.post("/webhooks/stripe")
@csrf.exempt
def stripe_webhook() -> tuple[dict[str, bool] | dict[str, str], int]:
    try:
        processed = payments.handle_stripe(
            request.get_data(cache=False), request.headers.get("Stripe-Signature", "")
        )
    except payments.PaymentError as exc:
        return {"error": str(exc)}, 400
    return {"processed": processed}, 200


@bp.post("/webhooks/paypal")
@csrf.exempt
def paypal_webhook() -> tuple[dict[str, bool] | dict[str, str], int]:
    headers = {key.upper(): value for key, value in request.headers.items()}
    try:
        processed = payments.handle_paypal(request.get_data(cache=False), headers)
    except payments.PaymentError as exc:
        return {"error": str(exc)}, 400
    return {"processed": processed}, 200


NEWSLETTER_CHECK_EMAIL = "Check your email to confirm your subscription."
NEWSLETTER_BUSY = "Too many signup attempts. Please try again later."


@bp.post("/newsletter")
def newsletter() -> Response:
    back = _safe_back(url_for("store.home"))
    if request.form.get("gg_hp"):
        # Honeypot: the field is hidden from people, so only form-filling bots send it. They get
        # the normal reply and nothing is sent to Mailchimp.
        flash(NEWSLETTER_CHECK_EMAIL, "success")
        return redirect(back)
    # Fail closed: each signup can make Mailchimp email a stranger, so no limiter means no signup.
    if not ratelimit.allow("newsletter", ratelimit.client_key(), 5, 3600, fail_closed=True):
        flash(NEWSLETTER_BUSY, "error")
        return redirect(back)
    try:
        email = account_service.normalize_email(request.form.get("email", ""))
        confirmed = _proven_customer_email(email)
        if not confirmed and not ratelimit.allow(
            "newsletter-site",
            "all",
            current_app.config["NEWSLETTER_HOURLY_CAP"],
            3600,
            fail_closed=True,
        ):
            flash(NEWSLETTER_BUSY, "error")
            return redirect(back)
        state = marketing.subscribe(email, confirmed=confirmed)
    except (account_service.AccountError, marketing.MarketingError) as exc:
        flash(str(exc), "error")
    else:
        if state == marketing.SUBSCRIBED:
            flash("You're subscribed. Thank you for staying connected!", "success")
        else:
            flash(NEWSLETTER_CHECK_EMAIL, "success")
    return redirect(back)


def _proven_customer_email(email: str) -> bool:
    """True only for a signed-in customer (verified email) subscribing their own account email
    who has paid for an order. Anyone else, or any other address, gets the confirmation email."""
    user = account_service.current_user()
    if user is None or user.email != email:
        return False
    paid = db.session.scalar(
        select(Order.id)
        .where(
            Order.user_id == user.id,
            Order.payment_status.in_((PaymentStatus.PAID, PaymentStatus.REFUNDED)),
        )
        .limit(1)
    )
    return paid is not None


def _safe_back(default: str) -> str:
    ref = request.referrer or ""
    return ref if ref.startswith(request.host_url) else default

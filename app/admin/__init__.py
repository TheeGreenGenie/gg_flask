"""Administrator UI and provisioning commands."""

from __future__ import annotations

import functools
import ipaddress
import time
from collections.abc import Callable
from typing import Any

import click
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select
from werkzeug.wrappers import Response

from app.admin import catalog as catalog_service
from app.admin import orders as order_service
from app.admin import service
from app.admin.models import AdminRole, AdminUser
from app.catalog.models import (
    Category,
    Product,
    ProductImage,
    ProductStatus,
    ProductType,
    SaleUnit,
    Variant,
)
from app.extensions import db
from app.orders.models import TRACKING_CARRIERS, Coupon, DiscountType, Order, OrderStatus

bp = Blueprint("admin", __name__, url_prefix="/admin")


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if service.current_admin() is None:
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def roles(*allowed: AdminRole) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(view: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            admin = service.current_admin()
            if admin is None or not service.role_allows(admin, set(allowed)):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


@bp.before_request
def _admin_network_allowlist() -> None:
    """Replaces Apache's `Require ip` now that the app may run without Apache in front."""
    allowed = current_app.config.get("ADMIN_ALLOWED_IPS") or ()
    if not allowed:
        return
    try:
        client = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        abort(404)
    if not any(client in ipaddress.ip_network(net, strict=False) for net in allowed):
        abort(404)  # same as any unknown page: do not reveal that /admin exists


@bp.after_request
def _admin_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response:
    wanted = _safe_next(request.args.get("next"))
    if service.current_admin():
        return redirect(wanted or url_for("admin.dashboard"))
    if wanted:
        # Remembered outside the admin_* keys, which sign-in clears (e.g. a Studio approval page).
        session["owner_return_to"] = wanted
    error = ""
    if request.method == "POST":
        try:
            service.begin_login(request.form.get("username", ""), request.form.get("password", ""))
        except service.AdminError as exc:
            error = str(exc)
        else:
            return redirect(url_for("admin.verify"))
    return render_template(
        "admin/login.html", error=error, username=request.form.get("username", "")
    )


@bp.route("/verify", methods=["GET", "POST"])
def verify() -> str | Response:
    if service.current_admin():
        return redirect(url_for("admin.dashboard"))
    error = ""
    if request.method == "POST":
        try:
            service.complete_login(request.form.get("code", ""))
        except service.AdminError as exc:
            error = str(exc)
        else:
            wanted = _safe_next(session.pop("owner_return_to", None))
            return redirect(wanted or url_for("admin.dashboard"))
    return render_template("admin/verify.html", error=error)


@bp.post("/logout")
def logout() -> Response:
    service.logout()
    flash("Administrator signed out.", "success")
    return redirect(url_for("admin.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def password() -> str | Response:
    error = ""
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        if new_password != request.form.get("confirm_password", ""):
            error = "The new passwords do not match."
        else:
            try:
                service.change_password(
                    _admin(), request.form.get("current_password", ""), new_password
                )
            except service.AdminError as exc:
                db.session.rollback()
                error = str(exc)
            else:
                flash("Password changed. Other administrator sessions were signed out.", "success")
                return redirect(url_for("admin.dashboard"))
    return render_template("admin/password.html", admin=_admin(), error=error)


@bp.get("/")
@login_required
def dashboard() -> str:
    return render_template("admin/dashboard.html", admin=_admin())


@bp.get("/catalog/")
@roles(AdminRole.CATALOG)
def catalog() -> str:
    products = db.session.scalars(select(Product).order_by(Product.name)).all()
    categories = db.session.scalars(
        select(Category).order_by(Category.position, Category.name)
    ).all()
    return render_template(
        "admin/catalog.html", admin=_admin(), products=products, categories=categories
    )


@bp.route("/catalog/products/<int:product_id>", methods=["GET", "POST"])
@roles(AdminRole.CATALOG)
def product_edit(product_id: int) -> str | Response:
    product = db.get_or_404(Product, product_id)
    error = ""
    if request.method == "POST":
        form = request.form.to_dict()
        form["category_ids"] = ",".join(request.form.getlist("category_ids"))
        try:
            catalog_service.update_product(_admin(), product, form)
        except catalog_service.CatalogError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Product saved.", "success")
            return redirect(url_for("admin.product_edit", product_id=product.id))
    categories = db.session.scalars(
        select(Category).order_by(Category.position, Category.name)
    ).all()
    return render_template(
        "admin/product.html",
        admin=_admin(),
        product=product,
        categories=categories,
        statuses=ProductStatus,
        product_types=ProductType,
        sale_units=SaleUnit,
        error=error,
    )


@bp.post("/catalog/products/<int:product_id>/images")
@roles(AdminRole.CATALOG)
def image_upload(product_id: int) -> Response:
    product = db.get_or_404(Product, product_id)
    try:
        catalog_service.upload_image(
            _admin(), product, request.files.get("image"), request.form.get("alt", "")
        )
        flash("Image uploaded and safely re-encoded.", "success")
    except catalog_service.CatalogError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.product_edit", product_id=product.id) + "#images")


@bp.post("/catalog/images/<int:image_id>/delete")
@roles(AdminRole.CATALOG)
def image_delete(image_id: int) -> Response:
    image = db.get_or_404(ProductImage, image_id)
    product_id = image.product_id
    try:
        catalog_service.delete_image(_admin(), image)
        flash("Image removed.", "success")
    except catalog_service.CatalogError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.product_edit", product_id=product_id) + "#images")


@bp.route("/catalog/variants/<int:variant_id>", methods=["GET", "POST"])
@roles(AdminRole.CATALOG)
def variant_edit(variant_id: int) -> str | Response:
    variant = db.get_or_404(Variant, variant_id)
    error = ""
    if request.method == "POST":
        try:
            catalog_service.update_variant(_admin(), variant, request.form.to_dict())
        except catalog_service.CatalogError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Variant and inventory saved.", "success")
            return redirect(url_for("admin.variant_edit", variant_id=variant.id))
    return render_template("admin/variant.html", admin=_admin(), variant=variant, error=error)


@bp.route("/catalog/categories/<int:category_id>", methods=["GET", "POST"])
@roles(AdminRole.CATALOG)
def category_edit(category_id: int) -> str | Response:
    category = db.get_or_404(Category, category_id)
    error = ""
    if request.method == "POST":
        try:
            catalog_service.update_category(_admin(), category, request.form.to_dict())
        except catalog_service.CatalogError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Category saved.", "success")
            return redirect(url_for("admin.category_edit", category_id=category.id))
    return render_template("admin/category.html", admin=_admin(), category=category, error=error)


@bp.get("/orders/")
@roles(AdminRole.SUPPORT)
def orders() -> str:
    records = db.session.scalars(select(Order).order_by(Order.created_at.desc()).limit(200)).all()
    return render_template("admin/orders.html", admin=_admin(), orders=records)


@bp.get("/orders/<int:order_id>")
@roles(AdminRole.SUPPORT)
def order_view(order_id: int) -> str:
    return render_template(
        "admin/order.html",
        admin=_admin(),
        order=db.get_or_404(Order, order_id),
        statuses=OrderStatus,
        carriers=TRACKING_CARRIERS,
    )


@bp.post("/orders/<int:order_id>/status")
@roles(AdminRole.SUPPORT)
def order_status(order_id: int) -> Response:
    order = db.get_or_404(Order, order_id)
    try:
        order_service.change_status(_admin(), order, request.form.get("status", ""))
        flash("Order status updated.", "success")
    except order_service.OrderAdminError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.order_view", order_id=order.id))


@bp.post("/orders/<int:order_id>/shipped")
@roles(AdminRole.SUPPORT)
def order_shipped(order_id: int) -> Response:
    order = db.get_or_404(Order, order_id)
    try:
        emailed = order_service.mark_shipped(
            _admin(),
            order,
            request.form.get("carrier", ""),
            request.form.get("tracking_number", ""),
            request.form.get("notify") == "1",
        )
        flash(
            "Marked as shipped and the customer was emailed the tracking link."
            if emailed
            else "Marked as shipped. No email was sent"
            + ("." if request.form.get("notify") != "1" else " (email is not set up yet)."),
            "success",
        )
    except order_service.OrderAdminError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.order_view", order_id=order.id))


@bp.post("/orders/<int:order_id>/notes")
@roles(AdminRole.SUPPORT)
def order_note(order_id: int) -> Response:
    order = db.get_or_404(Order, order_id)
    try:
        order_service.add_note(_admin(), order, request.form.get("body", ""))
        flash("Private note added.", "success")
    except order_service.OrderAdminError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.order_view", order_id=order.id))


@bp.post("/orders/<int:order_id>/refund")
@roles(AdminRole.OWNER)
def order_refund(order_id: int) -> Response:
    order = db.get_or_404(Order, order_id)
    try:
        state = order_service.refund(_admin(), order)
        flash(
            "The full payment was refunded."
            if state == "completed"
            else "Refund requested. The payment provider is still processing it; the order "
            "will show Refunded once they confirm (checked every 15 minutes).",
            "success",
        )
    except order_service.OrderAdminError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.order_view", order_id=order.id))


@bp.post("/orders/<int:order_id>/resend")
@roles(AdminRole.SUPPORT)
def order_resend(order_id: int) -> Response:
    order = db.get_or_404(Order, order_id)
    try:
        order_service.resend_email(_admin(), order)
        flash("Order email sent again.", "success")
    except order_service.OrderAdminError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.order_view", order_id=order.id))


@bp.get("/orders/<int:order_id>/<document>")
@roles(AdminRole.SUPPORT)
def order_document(order_id: int, document: str) -> str:
    if document not in {"invoice", "packing-slip"}:
        abort(404)
    return render_template(
        "admin/order_document.html",
        admin=_admin(),
        order=db.get_or_404(Order, order_id),
        document=document,
    )


@bp.get("/coupons/")
@roles(AdminRole.CATALOG)
def coupons() -> str:
    records = db.session.scalars(select(Coupon).order_by(Coupon.code)).all()
    return render_template("admin/coupons.html", admin=_admin(), coupons=records)


@bp.route("/coupons/new", methods=["GET", "POST"])
@bp.route("/coupons/<int:coupon_id>", methods=["GET", "POST"])
@roles(AdminRole.CATALOG)
def coupon_edit(coupon_id: int | None = None) -> str | Response:
    coupon = db.get_or_404(Coupon, coupon_id) if coupon_id is not None else None
    error = ""
    if request.method == "POST":
        try:
            coupon = order_service.save_coupon(_admin(), coupon, request.form.to_dict())
        except order_service.OrderAdminError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Coupon saved.", "success")
            return redirect(url_for("admin.coupon_edit", coupon_id=coupon.id))
    return render_template(
        "admin/coupon.html",
        admin=_admin(),
        coupon=coupon,
        discount_types=DiscountType,
        error=error,
    )


def _safe_next(value: str | None) -> str | None:
    """A same-site path only (no scheme, host, backslashes or protocol-relative //)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value[:500]


def _admin() -> AdminUser:
    admin = service.current_admin()
    if admin is None:  # pragma: no cover - protected routes only
        abort(401)
    return admin


def init_admin(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.cli.group("admin")
    def admin_cli() -> None:
        """Administrator setup and recovery tools."""

    @admin_cli.command("create")
    @click.option("--username", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--role", type=click.Choice([r.value for r in AdminRole]), default="owner")
    @click.password_option(confirmation_prompt=True)
    def create(username: str, email: str, role: str, password: str) -> None:
        """Create an administrator and print one-time 2FA setup information."""
        try:
            user, codes = service.create_admin(username, email, password, AdminRole(role))
        except service.AdminError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo("Administrator created. Add this account to an authenticator app:")
        click.echo(f"Secret: {user.totp_secret}")
        click.echo(f"URI: {service.provisioning_uri(user)}")
        click.echo("Recovery codes (store securely; each works once):")
        for code in codes:
            click.echo(f"  {code}")

    def _admin_named(username: str) -> AdminUser:
        try:
            normalized = service.normalize_username(username)
        except service.AdminError as exc:
            raise click.ClickException(str(exc)) from None
        user = db.session.scalar(select(AdminUser).where(AdminUser.username == normalized))
        if user is None:
            raise click.ClickException("Administrator username not found.")
        return user

    @admin_cli.command("check-code")
    @click.argument("username")
    def check_code(username: str) -> None:
        """Check a code from the authenticator app against this admin's key (prints no secrets)."""
        user = _admin_named(username)
        click.echo(f"Server time (UTC): {time.strftime('%H:%M:%S', time.gmtime())}")
        code = click.prompt("Current code from your authenticator app", hide_input=True)
        offset = service.code_offset(user.totp_secret, code)
        if offset is None:
            click.echo(
                "NO MATCH: this authenticator entry holds a different key (an older entry, or a "
                "typo when it was added). Fix it with: admin reset-authenticator " + user.username
            )
        elif abs(offset) <= 30:
            click.echo("MATCH: the key and clocks agree. Sign in with a fresh code.")
        else:
            click.echo(
                f"CLOCK: the code matches only if the clocks are {offset:+d} seconds apart. "
                "Turn on automatic date and time on the phone (Google Authenticator: "
                "Settings > Time correction for codes)."
            )

    @admin_cli.command("reset-authenticator")
    @click.argument("username")
    def reset_authenticator(username: str) -> None:
        """Give an admin a new authenticator key; saved only after a code from it is confirmed."""
        user = _admin_named(username)
        secret = service.generate_totp_secret()
        grouped = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
        click.echo(
            "In your authenticator app, add a NEW entry (Google Authenticator: + > Enter a setup "
            "key; delete the old GG entry afterwards):"
        )
        click.echo(f"  Account: GG Afrikan Eleganz ({user.username})")
        click.echo(f"  Key:     {grouped}")
        click.echo("  Type:    Time based")
        click.echo(
            f"Or open this setup link on the phone: {service.provisioning_uri(user, secret)}"
        )
        for _ in range(3):
            code = click.prompt("Type the 6-digit code the NEW entry shows")
            if service.verify_totp(secret, code.replace(" ", "").strip()):
                service.replace_totp_secret(user, secret)
                click.echo(
                    "Saved. Sign in with codes from the new entry; other sessions were signed out."
                )
                return
            click.echo("That code does not match the new key yet. Check the key, then try again.")
        raise click.ClickException("Not saved: the old key still works. Run the command again.")

    @admin_cli.command("reset-password")
    @click.argument("username")
    @click.password_option(confirmation_prompt=True)
    def reset_password(username: str, password: str) -> None:
        """Set a new password and revoke every session for one admin username."""
        try:
            normalized = service.normalize_username(username)
        except service.AdminError as exc:
            raise click.ClickException(str(exc)) from None
        user = db.session.scalar(select(AdminUser).where(AdminUser.username == normalized))
        if user is None:
            raise click.ClickException("Administrator username not found.")
        try:
            service.reset_password(user, password)
        except service.AdminError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"Password reset for {user.username}; all existing sessions were revoked.")

"""Administrator UI and provisioning commands."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import click
from flask import Blueprint, Flask, abort, flash, redirect, render_template, request, url_for
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
from app.orders.models import Coupon, DiscountType, Order, OrderStatus

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


@bp.after_request
def _admin_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response:
    if service.current_admin():
        return redirect(url_for("admin.dashboard"))
    error = ""
    if request.method == "POST":
        try:
            service.begin_login(request.form.get("email", ""), request.form.get("password", ""))
        except service.AdminError as exc:
            error = str(exc)
        else:
            return redirect(url_for("admin.verify"))
    return render_template("admin/login.html", error=error, email=request.form.get("email", ""))


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
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/verify.html", error=error)


@bp.post("/logout")
def logout() -> Response:
    service.logout()
    flash("Administrator signed out.", "success")
    return redirect(url_for("admin.login"))


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
        order_service.refund(_admin(), order)
        flash("The full payment was refunded.", "success")
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
    @click.option("--email", prompt=True)
    @click.option("--role", type=click.Choice([r.value for r in AdminRole]), default="owner")
    @click.password_option(confirmation_prompt=True)
    def create(email: str, role: str, password: str) -> None:
        """Create an administrator and print one-time 2FA setup information."""
        try:
            user, codes = service.create_admin(email, password, AdminRole(role))
        except service.AdminError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo("Administrator created. Add this account to an authenticator app:")
        click.echo(f"Secret: {user.totp_secret}")
        click.echo(f"URI: {service.provisioning_uri(user)}")
        click.echo("Recovery codes (store securely; each works once):")
        for code in codes:
            click.echo(f"  {code}")

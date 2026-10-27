"""Hidden, owner-approved Staff Studio routes and server-only account commands."""

from __future__ import annotations

import csv
import functools
import io
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import click
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func, or_, select
from werkzeug.wrappers import Response

from app.admin import service as admin_service
from app.admin.models import AdminRole
from app.catalog.models import (
    Category,
    Collection,
    Product,
    ProductImage,
    ProductStatus,
    SaleUnit,
    Variant,
    utcnow,
)
from app.content import blog as blog_service
from app.content.models import BlogPost, BlogRevision
from app.extensions import db
from app.reviews.models import ModerationWord, PostStatus, ProductPost
from app.studio import service
from app.studio.models import (
    ApprovalStatus,
    InventoryHistory,
    PriceHistory,
    SaleDiscountKind,
    SaleStatus,
    StudioAudit,
    StudioRole,
    StudioSale,
    StudioUser,
)

bp = Blueprint("studio", __name__)


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = service.current_user()
        if user is None:
            return redirect(url_for("studio.login"))
        if user.must_change_password and request.endpoint != "studio.password":
            return redirect(url_for("studio.password"))
        return view(*args, **kwargs)

    return wrapped


def roles(*allowed: StudioRole) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(view: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user = service.current_user()
            if user is None or not service.allows(user, *allowed):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


@bp.after_request
def private_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@bp.route("/", methods=["GET", "POST"])
def login() -> str | Response:
    if service.current_user():
        return redirect(url_for("studio.dashboard"))
    error = ""
    if request.method == "POST":
        try:
            record, _token = service.begin_login(
                request.form.get("username", ""), request.form.get("password", "")
            )
        except service.StudioError as exc:
            error = str(exc)
        else:
            return redirect(url_for("studio.waiting", request_id=record.request_id))
    return render_template(
        "studio/login.html", error=error, username=request.form.get("username", "")
    )


@bp.get("/waiting/<request_id>")
def waiting(request_id: str) -> str | Response:
    record = service.find_request(request_id)
    if record is None:
        abort(404)
    if record.status == ApprovalStatus.APPROVED:
        try:
            user, token = service.consume_approval(record)
        except service.StudioError as exc:
            return render_template("studio/waiting.html", record=record, error=str(exc))
        target = "studio.password" if user.must_change_password else "studio.dashboard"
        response = make_response(redirect(url_for(target)))
        response.set_cookie(
            current_app.config["STUDIO_COOKIE_NAME"],
            token,
            secure=bool(current_app.config["SESSION_COOKIE_SECURE"]),
            httponly=True,
            samesite="Strict",
            path=url_for("studio.login"),
        )
        return response
    response = make_response(render_template("studio/waiting.html", record=record, error=""))
    if record.status == ApprovalStatus.PENDING:
        response.headers["Refresh"] = "2"
    return response


@bp.get("/status/<request_id>")
def approval_status(request_id: str) -> Response:
    record = service.find_request(request_id)
    if record is None:
        abort(404)
    response = jsonify(status=record.status.value)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/approve/<token>", methods=["GET", "POST"])
def approval(token: str) -> str | Response:
    record = service.request_by_token(token)
    if record is None:
        abort(404)
    owner = admin_service.current_admin()
    is_owner = bool(owner and owner.role == AdminRole.OWNER)
    error = ""
    if request.method == "POST":
        if not is_owner:
            abort(403)
        try:
            service.decide(record, request.form.get("decision") == "approve")
        except service.StudioError as exc:
            error = str(exc)
        else:
            return render_template("studio/approval_done.html", record=record)
    return render_template(
        "studio/approval.html", record=record, token=token, is_owner=is_owner, error=error
    )


@bp.post("/logout")
def logout() -> Response:
    service.revoke_current()
    response = make_response(redirect(url_for("studio.login")))
    response.delete_cookie(
        current_app.config["STUDIO_COOKIE_NAME"],
        path=url_for("studio.login"),
        secure=bool(current_app.config["SESSION_COOKIE_SECURE"]),
        httponly=True,
        samesite="Strict",
    )
    return response


@bp.route("/password", methods=["GET", "POST"])
@login_required
def password() -> str | Response:
    user = _user()
    error = ""
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if new != request.form.get("confirm_password", ""):
            error = "The new passwords do not match."
        else:
            try:
                service.change_password(user, request.form.get("current_password", ""), new)
            except (service.StudioError, ValueError) as exc:
                error = str(exc)
            else:
                flash("Password changed. Welcome to the Studio.", "success")
                return redirect(url_for("studio.dashboard"))
    return render_template("studio/password.html", staff=user, error=error)


@bp.get("/home")
@login_required
def dashboard() -> str:
    user = _user()
    low_stock = db.session.scalar(
        select(func.count(Variant.id)).where(
            Variant.active.is_(True), Variant.track_inventory.is_(True), Variant.stock_quantity <= 5
        )
    )
    hidden = db.session.scalar(
        select(func.count(Product.id)).where(Product.status != ProductStatus.ACTIVE)
    )
    upcoming_sales = db.session.scalar(
        select(func.count(StudioSale.id)).where(
            StudioSale.status.in_([SaleStatus.SCHEDULED, SaleStatus.ACTIVE])
        )
    )
    community_attention = db.session.scalar(
        select(func.count(ProductPost.id)).where(
            or_(
                ProductPost.status.in_([PostStatus.COLLAPSED, PostStatus.REMOVED]),
                ~ProductPost.replies.any(),
            )
        )
    )
    recent = db.session.scalars(
        select(StudioAudit).order_by(StudioAudit.created_at.desc()).limit(12)
    ).all()
    return render_template(
        "studio/dashboard.html",
        staff=user,
        low_stock=int(low_stock or 0),
        hidden=int(hidden or 0),
        upcoming_sales=int(upcoming_sales or 0),
        community_attention=int(community_attention or 0),
        recent=recent,
    )


@bp.get("/inventory")
@roles(StudioRole.EDITOR, StudioRole.INVENTORY)
def inventory() -> str:
    query = request.args.get("q", "").strip()[:100]
    statement = select(Variant).join(Variant.product).order_by(Product.name, Variant.position)
    if query:
        like = f"%{query}%"
        statement = statement.where(or_(Product.name.ilike(like), Variant.sku.ilike(like)))
    if request.args.get("attention") == "1":
        statement = statement.where(Variant.track_inventory.is_(True), Variant.stock_quantity <= 5)
    variants = db.session.scalars(statement.limit(500)).all()
    return render_template("studio/inventory.html", staff=_user(), variants=variants, query=query)


@bp.get("/inventory.csv")
@roles(StudioRole.EDITOR, StudioRole.INVENTORY)
def inventory_csv() -> Response:
    variants = db.session.scalars(
        select(Variant).join(Variant.product).order_by(Product.name, Variant.position)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Product", "Option", "SKU", "Stock", "Tracking", "Status"])
    for variant in variants:
        writer.writerow(
            [
                _csv_cell(variant.product.name),
                _csv_cell(variant.label),
                _csv_cell(variant.sku),
                variant.stock_quantity,
                "yes" if variant.track_inventory else "no",
                variant.product.status.value,
            ]
        )
    response = make_response(output.getvalue())
    response.mimetype = "text/csv"
    response.headers["Content-Disposition"] = "attachment; filename=inventory.csv"
    return response


@bp.post("/inventory/<int:variant_id>")
@roles(StudioRole.EDITOR, StudioRole.INVENTORY)
def inventory_update(variant_id: int) -> Response:
    variant = db.get_or_404(Variant, variant_id)
    try:
        service.update_inventory(
            _user(),
            variant,
            request.form.get("quantity", ""),
            request.form.get("reason", ""),
            mode=request.form.get("mode", "set"),
            expected_raw=request.form.get("expected", ""),
        )
        flash(f"Stock updated for {variant.sku}.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.inventory", q=request.form.get("return_q", "")))


@bp.get("/inventory/history")
@roles(StudioRole.EDITOR, StudioRole.INVENTORY)
def inventory_history() -> str:
    records = db.session.scalars(
        select(InventoryHistory).order_by(InventoryHistory.created_at.desc()).limit(500)
    ).all()
    variant_ids = {record.variant_id for record in records}
    variants = {
        variant.id: variant
        for variant in db.session.scalars(select(Variant).where(Variant.id.in_(variant_ids))).all()
    }
    users = {
        user.id: user
        for user in db.session.scalars(
            select(StudioUser).where(
                StudioUser.id.in_({record.user_id for record in records if record.user_id})
            )
        ).all()
    }
    return render_template(
        "studio/inventory_history.html",
        staff=_user(),
        records=records,
        variants=variants,
        users=users,
    )


@bp.post("/inventory/history/<int:history_id>/undo")
@roles(StudioRole.EDITOR, StudioRole.INVENTORY)
def inventory_undo(history_id: int) -> Response:
    history = db.get_or_404(InventoryHistory, history_id)
    try:
        service.undo_inventory(_user(), history)
        flash("Stock change undone.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.inventory_history"))


@bp.get("/prices")
@roles(StudioRole.EDITOR)
def prices() -> str:
    query = request.args.get("q", "").strip()[:100]
    statement = select(Variant).join(Variant.product).order_by(Product.name, Variant.position)
    if query:
        like = f"%{query}%"
        statement = statement.where(or_(Product.name.ilike(like), Variant.sku.ilike(like)))
    variants = db.session.scalars(statement.limit(500)).all()
    return render_template("studio/prices.html", staff=_user(), variants=variants, query=query)


@bp.post("/prices/<int:variant_id>")
@roles(StudioRole.EDITOR)
def price_update(variant_id: int) -> Response:
    variant = db.get_or_404(Variant, variant_id)
    try:
        service.update_price(
            _user(),
            variant,
            request.form.get("price", ""),
            request.form.get("reason", ""),
            expected_raw=request.form.get("expected", ""),
        )
        flash(f"Price updated for {variant.sku}.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.prices", q=request.form.get("return_q", "")))


@bp.get("/prices/history")
@roles(StudioRole.EDITOR)
def price_history() -> str:
    records = db.session.scalars(
        select(PriceHistory).order_by(PriceHistory.created_at.desc()).limit(500)
    ).all()
    variant_ids = {record.variant_id for record in records}
    variants = {
        variant.id: variant
        for variant in db.session.scalars(select(Variant).where(Variant.id.in_(variant_ids))).all()
    }
    users = {
        user.id: user
        for user in db.session.scalars(
            select(StudioUser).where(
                StudioUser.id.in_({record.user_id for record in records if record.user_id})
            )
        ).all()
    }
    return render_template(
        "studio/price_history.html",
        staff=_user(),
        records=records,
        variants=variants,
        users=users,
    )


@bp.post("/prices/history/<int:history_id>/undo")
@roles(StudioRole.EDITOR)
def price_undo(history_id: int) -> Response:
    history = db.get_or_404(PriceHistory, history_id)
    try:
        service.undo_price(_user(), history)
        flash("Price change undone.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.price_history"))


@bp.get("/products")
@roles(StudioRole.EDITOR)
def products() -> str:
    query = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "").strip()
    statement = select(Product).order_by(Product.name)
    if query:
        statement = statement.where(Product.name.ilike(f"%{query}%"))
    if status in {value.value for value in ProductStatus}:
        statement = statement.where(Product.status == ProductStatus(status))
    records = db.session.scalars(statement.limit(500)).unique().all()
    return render_template(
        "studio/products.html",
        staff=_user(),
        products=records,
        query=query,
        selected_status=status,
        statuses=ProductStatus,
    )


@bp.route("/products/<int:product_id>", methods=["GET", "POST"])
@roles(StudioRole.EDITOR)
def product_edit(product_id: int) -> str | Response:
    product = db.get_or_404(Product, product_id)
    error = ""
    if request.method == "POST":
        try:
            service.update_product_details(
                _user(),
                product,
                request.form.get("name", ""),
                request.form.get("summary", ""),
                request.form.get("description", ""),
                request.form.getlist("category_ids"),
                request.form.get("sale_unit"),
                request.form.get("piece_length_yards"),
                request.form.get("width_inches"),
            )
        except service.StudioError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Product details saved.", "success")
            return redirect(url_for("studio.product_edit", product_id=product.id))
    categories = db.session.scalars(
        select(Category).order_by(Category.position, Category.name)
    ).all()
    return render_template(
        "studio/product.html",
        staff=_user(),
        product=product,
        categories=categories,
        sale_units=SaleUnit,
        error=error,
    )


@bp.post("/products/<int:product_id>/visibility")
@roles(StudioRole.EDITOR)
def product_visibility(product_id: int) -> Response:
    product = db.get_or_404(Product, product_id)
    try:
        service.set_product_visibility(
            _user(),
            product,
            request.form.get("live") == "1",
            request.form.get("reason", ""),
            request.form.get("expected", ""),
        )
        flash(
            "Product is live again."
            if product.status == ProductStatus.ACTIVE
            else "Product hidden.",
            "success",
        )
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.products", status=request.form.get("return_status", "")))


@bp.post("/products/<int:product_id>/images")
@roles(StudioRole.EDITOR)
def product_image_upload(product_id: int) -> Response:
    product = db.get_or_404(Product, product_id)
    try:
        service.upload_product_image(
            _user(), product, request.files.get("image"), request.form.get("alt", "")
        )
        flash("Photo uploaded and safely resized.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.product_edit", product_id=product.id) + "#photos")


@bp.post("/products/images/<int:image_id>/remove")
@roles(StudioRole.EDITOR)
def product_image_remove(image_id: int) -> Response:
    image = db.get_or_404(ProductImage, image_id)
    product_id = image.product_id
    try:
        service.delete_product_image(_user(), image)
        flash("Photo removed.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.product_edit", product_id=product_id) + "#photos")


@bp.get("/sales")
@roles(StudioRole.EDITOR)
def sales() -> str:
    service.sync_sales()
    records = db.session.scalars(
        select(StudioSale).order_by(StudioSale.created_at.desc()).limit(200)
    ).all()
    return render_template("studio/sales.html", staff=_user(), sales=records)


@bp.route("/sales/new", methods=["GET", "POST"])
@roles(StudioRole.EDITOR)
def sale_new() -> str | Response:
    error = ""
    if request.method == "POST":
        try:
            sale = service.draft_sale(_user(), request.form.to_dict())
        except service.StudioError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            return redirect(url_for("studio.sale_preview", sale_id=sale.id))
    categories = db.session.scalars(select(Category).order_by(Category.name)).all()
    collections = db.session.scalars(select(Collection).order_by(Collection.name)).all()
    products = db.session.scalars(
        select(Product).where(Product.status == ProductStatus.ACTIVE).order_by(Product.name)
    ).all()
    local_now = utcnow().astimezone(ZoneInfo("America/New_York"))
    defaults = {
        "starts_at": (local_now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        "ends_at": (local_now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"),
    }
    return render_template(
        "studio/sale_new.html",
        staff=_user(),
        error=error,
        categories=categories,
        collections=collections,
        products=products,
        discount_kinds=SaleDiscountKind,
        defaults=defaults,
    )


@bp.get("/sales/<int:sale_id>")
@roles(StudioRole.EDITOR)
def sale_preview(sale_id: int) -> str:
    sale = db.get_or_404(StudioSale, sale_id)
    return render_template("studio/sale_preview.html", staff=_user(), sale=sale)


@bp.post("/sales/<int:sale_id>/confirm")
@roles(StudioRole.EDITOR)
def sale_confirm(sale_id: int) -> Response:
    sale = db.get_or_404(StudioSale, sale_id)
    try:
        service.confirm_sale(_user(), sale)
        flash("Sale confirmed and scheduled.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.sale_preview", sale_id=sale.id))


@bp.post("/sales/<int:sale_id>/cancel")
@roles(StudioRole.EDITOR)
def sale_cancel(sale_id: int) -> Response:
    sale = db.get_or_404(StudioSale, sale_id)
    try:
        service.cancel_sale(_user(), sale)
        flash("Sale cancelled. Applied prices were safely restored.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.sale_preview", sale_id=sale.id))


@bp.get("/blog")
@roles(StudioRole.EDITOR)
def blog_posts() -> str:
    blog_service.sync_publication()
    posts = db.session.scalars(
        select(BlogPost).order_by(BlogPost.created_at.desc()).limit(200)
    ).all()
    return render_template("studio/blog.html", staff=_user(), posts=posts)


@bp.route("/blog/new", methods=["GET", "POST"])
@bp.route("/blog/<int:post_id>", methods=["GET", "POST"])
@roles(StudioRole.EDITOR)
def blog_edit(post_id: int | None = None) -> str | Response:
    post = db.get_or_404(BlogPost, post_id) if post_id is not None else None
    error = ""
    if request.method == "POST":
        try:
            post = service.save_blog_post(_user(), post, request.form.to_dict())
        except service.StudioError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Article saved.", "success")
            return redirect(url_for("studio.blog_edit", post_id=post.id))
    body = ""
    if post is not None:
        try:
            import json

            body = blog_service.body_to_editor(json.loads(post.body_json))
        except (ValueError, TypeError):
            body = ""
    revisions = (
        db.session.scalars(
            select(BlogRevision)
            .where(BlogRevision.post_id == post.id)
            .order_by(BlogRevision.created_at.desc())
            .limit(30)
        ).all()
        if post
        else []
    )
    return render_template(
        "studio/blog_edit.html",
        staff=_user(),
        post=post,
        error=error,
        body=body,
        revisions=revisions,
    )


@bp.post("/blog/<int:post_id>/publication")
@roles(StudioRole.EDITOR)
def blog_publication(post_id: int) -> Response:
    post = db.get_or_404(BlogPost, post_id)
    try:
        service.set_blog_publication(
            _user(), post, request.form.get("action", ""), request.form.get("publish_at", "")
        )
        flash("Article publication updated.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.blog_edit", post_id=post.id))


@bp.post("/blog/revisions/<int:revision_id>/restore")
@roles(StudioRole.EDITOR)
def blog_revision_restore(revision_id: int) -> Response:
    revision = db.get_or_404(BlogRevision, revision_id)
    try:
        post = service.undo_blog_revision(_user(), revision)
        flash("Earlier article version restored.", "success")
    except (service.StudioError, ValueError, KeyError) as exc:
        db.session.rollback()
        flash(f"That version could not be restored: {exc}", "error")
        post = revision.post
    return redirect(url_for("studio.blog_edit", post_id=post.id))


@bp.get("/community")
@roles(StudioRole.SUPPORT)
def community() -> str:
    selected = request.args.get("status", "").strip()
    statement = select(ProductPost).order_by(ProductPost.created_at.desc())
    if selected in {status.value for status in PostStatus}:
        statement = statement.where(ProductPost.status == PostStatus(selected))
    posts = db.session.scalars(statement.limit(500)).all()
    words = db.session.scalars(
        select(ModerationWord).order_by(ModerationWord.language, ModerationWord.word)
    ).all()
    return render_template(
        "studio/community.html",
        staff=_user(),
        posts=posts,
        words=words,
        statuses=PostStatus,
        selected=selected,
    )


@bp.post("/community/<int:post_id>/reply")
@roles(StudioRole.SUPPORT)
def community_reply(post_id: int) -> Response:
    post = db.get_or_404(ProductPost, post_id)
    try:
        service.reply_to_post(_user(), post, request.form.get("body", ""))
        flash("Public reply posted as GG Afrikan Eleganz.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.community", status=request.form.get("return_status", "")))


@bp.post("/community/<int:post_id>/moderate")
@roles(StudioRole.SUPPORT)
def community_moderate(post_id: int) -> Response:
    post = db.get_or_404(ProductPost, post_id)
    try:
        service.moderate_post(_user(), post, request.form.get("action", ""))
        flash("Post moderation updated.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.community", status=request.form.get("return_status", "")))


@bp.post("/community/words")
@roles(StudioRole.SUPPORT)
def community_word_add() -> Response:
    try:
        service.add_moderation_word(
            _user(),
            request.form.get("language", ""),
            request.form.get("word", ""),
            request.form.get("word_type", ""),
        )
        flash("Moderation word added.", "success")
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("studio.community") + "#words")


@bp.post("/community/words/<int:word_id>/remove")
@roles(StudioRole.SUPPORT)
def community_word_remove(word_id: int) -> Response:
    word = db.get_or_404(ModerationWord, word_id)
    service.remove_moderation_word(_user(), word)
    flash("Moderation word removed.", "success")
    return redirect(url_for("studio.community") + "#words")


def _user() -> StudioUser:
    user = service.current_user()
    if user is None:  # pragma: no cover - protected routes only
        abort(401)
    return user


def _csv_cell(value: str) -> str:
    """Prevent spreadsheet formula execution when staff open an exported CSV."""
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def init_studio(app: Flask) -> None:
    path = str(app.config.get("STUDIO_PATH") or "").strip("/")
    if path:
        app.register_blueprint(bp, url_prefix=f"/{path}")

    @app.cli.group("studio")
    def studio_cli() -> None:
        """Server-only Staff Studio account tools."""

    @studio_cli.command("create")
    @click.option("--username", prompt=True)
    @click.option(
        "--roles",
        prompt="Roles (owner,editor,inventory,support; comma-separated)",
        default="editor",
    )
    @click.password_option(confirmation_prompt=True)
    def create(username: str, roles: str, password: str) -> None:
        """Create a Studio user who must change the temporary password."""
        try:
            user = service.create_user(username, password, roles)
        except (service.StudioError, ValueError) as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"Created {user.username} with roles: {user.roles}")

    @studio_cli.command("reset")
    @click.argument("username")
    @click.password_option(confirmation_prompt=True)
    def reset(username: str, password: str) -> None:
        """Set a temporary password and revoke all Studio sessions."""
        user = _cli_user(username)
        try:
            service.reset_password(user, password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"Reset {user.username}; password change required at next sign-in.")

    @studio_cli.command("disable")
    @click.argument("username")
    def disable(username: str) -> None:
        user = _cli_user(username)
        service.set_active(user, False)
        click.echo(f"Disabled {user.username} and revoked existing sessions.")

    @studio_cli.command("enable")
    @click.argument("username")
    def enable(username: str) -> None:
        user = _cli_user(username)
        service.set_active(user, True)
        click.echo(f"Enabled {user.username}.")

    @studio_cli.command("list")
    def list_users() -> None:
        users = db.session.scalars(select(StudioUser).order_by(StudioUser.username)).all()
        for user in users:
            click.echo(f"{user.username}\t{user.roles}\t{'active' if user.active else 'disabled'}")

    @studio_cli.command("sync-users")
    @click.option(
        "--file",
        "file_name",
        type=click.Path(path_type=Path, dir_okay=False),
        default=lambda: str(app.config["STUDIO_USERS_FILE"]),
        show_default=True,
    )
    @click.option("--apply", is_flag=True, help="Apply the displayed plan.")
    def sync_users(file_name: Path, apply: bool) -> None:
        """Preview or apply the password-free Studio user manifest."""
        entries = _read_user_manifest(file_name)
        existing = {
            user.username: user
            for user in db.session.scalars(select(StudioUser).order_by(StudioUser.username)).all()
        }
        desired_names = {entry[0] for entry in entries}
        plan: list[tuple[str, str, str, bool]] = []
        for username, role_values, active in entries:
            user = existing.get(username)
            if user is None:
                plan.append(
                    ("create" if active else "skip-disabled", username, role_values, active)
                )
            elif user.roles != role_values or user.active != active:
                plan.append(("update", username, role_values, active))
        for username, user in existing.items():
            if username not in desired_names and user.active:
                plan.append(("disable-missing", username, user.roles, False))
        if not plan:
            click.echo("No Studio user changes.")
            return
        for action, username, role_values, active in plan:
            click.echo(
                f"{action}: {username} roles={role_values} status="
                f"{'active' if active else 'disabled'}"
            )
        if not apply:
            click.echo("Preview only. Run again with --apply to make these changes.")
            return
        for action, username, role_values, active in plan:
            if action == "create":
                password = click.prompt(
                    f"Temporary password for {username}",
                    hide_input=True,
                    confirmation_prompt=True,
                )
                service.create_user(username, password, role_values)
            elif action == "update":
                service.configure_user(existing[username], role_values, active)
            elif action == "disable-missing":
                service.set_active(existing[username], False)
        click.echo("Studio users synchronized. Removed entries were disabled, never deleted.")

    @studio_cli.command("approve-local")
    @click.argument("request_id")
    @click.option("--deny", is_flag=True)
    def approve_local(request_id: str, deny: bool) -> None:
        """Development-only fallback while the owner selects a phone push provider."""
        if app.config["APP_ENV"] == "production":
            raise click.ClickException("approve-local is disabled in production")
        record = service.find_request(request_id)
        if record is None:
            raise click.ClickException("Request not found")
        try:
            service.decide(record, not deny)
        except service.StudioError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo("Denied." if deny else "Approved.")

    @studio_cli.command("sync-sales")
    def sync_sales() -> None:
        """Activate and end scheduled sales; run at least once per minute."""
        activated, ended = service.sync_sales()
        click.echo(f"Activated {activated}; ended {ended}.")

    @studio_cli.command("sync-blog")
    def sync_blog() -> None:
        """Publish scheduled journal posts; run at least once per minute."""
        published, _ = blog_service.sync_publication()
        click.echo(f"Published {published} article(s).")

    @studio_cli.command("daily-summary")
    def daily_summary() -> None:
        """Send the owner a summary of Studio attention items and activity."""
        try:
            sent = service.send_daily_summary()
        except service.StudioError as exc:
            raise click.ClickException(str(exc)) from None
        if not sent:
            raise click.ClickException("The configured mail backend did not send the summary.")
        click.echo("Daily Staff Studio summary sent.")


def _cli_user(username: str) -> StudioUser:
    try:
        normalized = service.normalize_username(username)
    except service.StudioError as exc:
        raise click.ClickException(str(exc)) from None
    user = db.session.scalar(select(StudioUser).where(StudioUser.username == normalized))
    if user is None:
        raise click.ClickException("Studio user not found")
    return user


def _read_user_manifest(path: Path) -> list[tuple[str, str, bool]]:
    try:
        if path.stat().st_size > 1_000_000:
            raise click.ClickException("Studio user manifest is unexpectedly large.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Could not read Studio user manifest: {exc}") from None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise click.ClickException("Studio user manifest must be an object with version 1.")
    raw_users = payload.get("users")
    if not isinstance(raw_users, list):
        raise click.ClickException("Studio user manifest users must be a list.")
    entries: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_users, start=1):
        if not isinstance(item, dict) or set(item) - {"username", "roles", "active"}:
            raise click.ClickException(f"User {index} has unknown or invalid fields.")
        try:
            username = service.normalize_username(str(item.get("username", "")))
            roles = service.parse_roles(item.get("roles", []))
        except service.StudioError as exc:
            raise click.ClickException(f"User {index}: {exc}") from None
        if username in seen:
            raise click.ClickException(f"Studio username appears twice: {username}")
        active = item.get("active", True)
        if not isinstance(active, bool):
            raise click.ClickException(f"User {index}: active must be true or false.")
        seen.add(username)
        entries.append((username, ",".join(sorted(role.value for role in roles)), active))
    return entries

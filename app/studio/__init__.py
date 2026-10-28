"""Hidden, owner-approved Staff Studio routes and server-only account commands."""

from __future__ import annotations

import csv
import functools
import io
import json
import urllib.parse
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple
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
from app.studio import blog_media, passkeys, permissions, review, service, sms
from app.studio.models import (
    ApprovalStatus,
    InventoryHistory,
    PriceHistory,
    SaleDiscountKind,
    SaleStatus,
    StudioAudit,
    StudioChange,
    StudioLoginRequest,
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
            if user is None or not (
                service.allows(user, *allowed) or permissions.scoped_allows(user, request.endpoint)
            ):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


@bp.context_processor
def studio_helpers() -> dict[str, Any]:
    def studio_can(endpoint: str, *legacy: str) -> bool:
        return permissions.can(
            service.current_user(), endpoint, tuple(StudioRole(role) for role in legacy)
        )

    return {"studio_can": studio_can}


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
        if sms.needed(record):
            return redirect(url_for("studio.sms_verify", request_id=record.request_id))
        return _finish_sign_in(record)
    own_key = passkeys.can_self_approve(record.user)
    response = make_response(
        render_template("studio/waiting.html", record=record, error="", own_key=own_key)
    )
    if record.status == ApprovalStatus.PENDING and not own_key:
        response.headers["Refresh"] = "2"  # (not while a phone key prompt may be open)
    return response


def _finish_sign_in(record: StudioLoginRequest) -> str | Response:
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


@bp.route("/verify/<request_id>", methods=["GET", "POST"])
def sms_verify(request_id: str) -> str | Response:
    """Second step for accounts that need a texted code after approval (tgg)."""
    record = service.find_request(request_id)
    if record is None or record.status != ApprovalStatus.APPROVED:
        abort(404)
    if not sms.needed(record):
        return _finish_sign_in(record)
    error = ""
    try:
        if request.method == "POST" and request.form.get("action") == "resend":
            sms.send_code(record, force=True)
        elif request.method == "POST":
            sms.verify(record, request.form.get("code", ""))
            return _finish_sign_in(record)
        else:
            sms.send_code(record)
    except sms.SmsError as exc:
        error = str(exc)
    return render_template(
        "studio/sms_verify.html",
        record=record,
        error=error,
        phone=sms.masked(record.user.sms_phone),
        app_code=sms.uses_app(record.user),
    )


@bp.get("/waiting/<request_id>/key-options")
def waiting_key_options(request_id: str) -> Response:
    record = service.find_request(request_id)
    if record is None or record.status != ApprovalStatus.PENDING:
        abort(404)
    try:
        return _json_response(passkeys.authentication_options(record))
    except passkeys.PasskeyError as exc:
        return _json_error(str(exc))


@bp.post("/waiting/<request_id>/key")
def waiting_key(request_id: str) -> Response:
    record = service.find_request(request_id)
    if record is None or record.status != ApprovalStatus.PENDING:
        abort(404)
    try:
        passkeys.self_approve(record, _json_body())
    except (passkeys.PasskeyError, service.StudioError) as exc:
        db.session.rollback()
        return _json_error(str(exc))
    return jsonify(ok=True, next=url_for("studio.waiting", request_id=request_id))


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


@bp.route("/review/<token>", methods=["GET", "POST"])
def review_change(token: str) -> str | Response:
    """The owner's approve/reject page for a held change (link from the email)."""
    change = review.change_by_token(token)
    if change is None:
        abort(404)
    owner = admin_service.current_admin()
    is_owner = bool(owner and owner.role == AdminRole.OWNER)
    error = ""
    if request.method == "POST":
        if not is_owner or owner is None:
            abort(403)
        try:
            review.decide(change, request.form.get("decision") == "approve", owner.email)
        except service.StudioError as exc:
            error = str(exc)
    return render_template(
        "studio/review.html", change=change, token=token, is_owner=is_owner, error=error
    )


@bp.get("/key")
@login_required
def key() -> str:
    user = _user()
    if not user.may_self_approve:
        abort(404)
    return render_template(
        "studio/key.html",
        staff=user,
        keys=passkeys.keys_for(user),
        enrollment=passkeys.open_enrollment(user),
    )


@bp.post("/key/request")
@login_required
def key_request() -> Response:
    try:
        passkeys.request_enrollment(_user())
        flash("Request sent. The owner will confirm it from their email.", "success")
    except passkeys.PasskeyError as exc:
        flash(str(exc), "error")
    return redirect(url_for("studio.key"))


@bp.get("/key/register/options")
@login_required
def key_register_options() -> Response:
    try:
        return _json_response(passkeys.registration_options(_user()))
    except passkeys.PasskeyError as exc:
        return _json_error(str(exc))


@bp.post("/key/register")
@login_required
def key_register() -> Response:
    try:
        passkeys.register(_user(), _json_body(), request.headers.get("X-Key-Label", ""))
    except passkeys.PasskeyError as exc:
        db.session.rollback()
        return _json_error(str(exc))
    flash("Your sign-in key is ready. Next time, approve your sign-in with it.", "success")
    return jsonify(ok=True, next=url_for("studio.key"))


@bp.route("/key-approval/<token>", methods=["GET", "POST"])
def key_approval(token: str) -> str | Response:
    """The owner confirms or refuses a co-owner's request to add a sign-in key."""
    record = passkeys.enrollment_by_token(token)
    if record is None:
        abort(404)
    owner = admin_service.current_admin()
    is_owner = bool(owner and owner.role == AdminRole.OWNER)
    error = ""
    if request.method == "POST":
        if not is_owner or owner is None:
            abort(403)
        try:
            passkeys.decide_enrollment(
                record, request.form.get("decision") == "approve", owner.email
            )
        except passkeys.PasskeyError as exc:
            error = str(exc)
    return render_template(
        "studio/key_approval.html", record=record, is_owner=is_owner, error=error
    )


@bp.get("/password-swaps")
@login_required
def password_swaps() -> str:
    user = _user()
    if not service.can_swap_passwords(user):
        abort(403)
    users = db.session.scalars(
        select(StudioUser)
        .where(StudioUser.id != user.id, StudioUser.active.is_(True))
        .order_by(StudioUser.username)
    ).all()
    return render_template("studio/password_swaps.html", staff=user, users=users)


@bp.get("/password-swaps/key-options")
@login_required
def password_swap_key_options() -> Response:
    user = _user()
    if not service.can_swap_passwords(user):
        abort(403)
    try:
        return _json_response(passkeys.password_swap_options(user))
    except passkeys.PasskeyError as exc:
        return _json_error(str(exc))


@bp.post("/password-swaps")
@login_required
def password_swap() -> Response:
    user = _user()
    if not service.can_swap_passwords(user):
        abort(403)
    body = _json_body()
    credential = body.get("credential")
    if not isinstance(credential, dict):
        return _json_error("Use tgg's phone key to approve this password swap.")
    try:
        username = service.normalize_username(str(body.get("username") or ""))
        target = db.session.scalar(select(StudioUser).where(StudioUser.username == username))
        if target is None:
            raise service.StudioError("Studio username not found.")
        passkeys.verify_password_swap_key(user, credential)
        temporary = service.issue_temporary_password(user, target)
    except (passkeys.PasskeyError, service.StudioError) as exc:
        db.session.rollback()
        return _json_error(str(exc))
    return jsonify(
        ok=True,
        username=target.username,
        temporary_password=temporary,
    )


PAGE_SIZE = 25  # rows per Studio list page (comfortable on a phone)


def _paginate(statement: Any) -> tuple[list[Any], dict[str, int]]:
    """One page of results plus what the pager partial needs (page, pages, total, first, last)."""
    total = int(
        db.session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
        or 0
    )
    pages = max(1, -(-total // PAGE_SIZE))
    try:
        page = min(max(1, int(request.args.get("page", "1"))), pages)
    except ValueError:
        page = 1
    items = list(db.session.scalars(statement.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)))
    first = (page - 1) * PAGE_SIZE + 1 if total else 0
    return items, {
        "page": page,
        "pages": pages,
        "total": total,
        "first": first,
        "last": first + len(items) - 1 if items else 0,
    }


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400)
    return body


def _json_response(payload: str) -> Response:
    response = current_app.response_class(payload, mimetype="application/json")
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json_error(message: str) -> Response:
    response = jsonify(error=message)
    response.status_code = 400
    return response


@bp.get("/changes")
@login_required
def changes() -> str:
    user = _user()
    query = select(StudioChange).order_by(StudioChange.created_at.desc()).limit(200)
    if StudioRole.OWNER not in user.role_set:
        query = query.where(StudioChange.user_id == user.id)
    return render_template(
        "studio/changes.html", staff=user, changes=db.session.scalars(query).all()
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
        my_pending=review.pending_count(user),
        stars=review.applied_this_week(user) if user.playful else 0,
    )


@bp.get("/inventory")
@roles(StudioRole.EDITOR, StudioRole.INVENTORY)
def inventory() -> str:
    query = request.args.get("q", "").strip()[:100]
    statement = select(Variant).join(Variant.product).order_by(Product.name, Variant.position)
    if query:
        like = f"%{query}%"
        statement = statement.where(or_(Product.name.ilike(like), Variant.sku.ilike(like)))
    attention = request.args.get("attention") == "1"
    if attention:
        statement = statement.where(Variant.track_inventory.is_(True), Variant.stock_quantity <= 5)
    variants, pager = _paginate(statement)
    return render_template(
        "studio/inventory.html",
        staff=_user(),
        variants=variants,
        query=query,
        pager=pager,
        attention=attention,
    )


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
    return redirect(
        url_for(
            "studio.inventory",
            q=request.form.get("return_q") or None,
            page=request.form.get("return_page") or None,
            attention=request.form.get("return_attention") or None,
        )
    )


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
    variants, pager = _paginate(statement)
    return render_template(
        "studio/prices.html", staff=_user(), variants=variants, query=query, pager=pager
    )


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
    return redirect(
        url_for(
            "studio.prices",
            q=request.form.get("return_q") or None,
            page=request.form.get("return_page") or None,
        )
    )


@bp.get("/prices/bulk")
@roles(StudioRole.EDITOR)
def price_bulk() -> str:
    """Bulk change form; with `preview=1` it shows every before -> after without saving."""
    form = {key: request.args.get(key, "") for key in ("category", "q", "kind", "amount")}
    form["reason"] = request.args.get("reason", "")
    form["round_99"] = request.args.get("round_99", "")
    plan, error = None, ""
    if request.args.get("preview"):
        try:
            plan = service.plan_bulk_price(form)
        except service.StudioError as exc:
            error = str(exc)
    categories = db.session.scalars(
        select(Category).where(Category.parent_id.is_(None)).order_by(Category.position)
    ).all()
    return render_template(
        "studio/price_bulk.html",
        staff=_user(),
        form=form,
        plan=plan,
        error=error,
        categories=categories,
        kinds=service.BULK_PRICE_KINDS,
    )


@bp.post("/prices/bulk")
@roles(StudioRole.EDITOR)
def price_bulk_apply() -> Response:
    form = {key: request.form.get(key, "") for key in request.form if key != "csrf_token"}
    try:
        count = service.apply_bulk_price(_user(), form)
    except service.StudioError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        args: dict[str, Any] = {k: v for k, v in form.items() if k != "fingerprint" and v}
        return redirect(url_for("studio.price_bulk", preview=1, **args))
    flash(f"{count} price(s) updated. Each one can be undone in price history.", "success")
    return redirect(url_for("studio.price_history"))


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
    records, pager = _paginate(statement)
    return render_template(
        "studio/products.html",
        staff=_user(),
        products=records,
        query=query,
        selected_status=status,
        statuses=ProductStatus,
        pager=pager,
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
            post = service.save_blog_post(_user(), post, _blog_form_with_uploads())
        except (service.StudioError, blog_media.BlogImageError) as exc:
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


def _blog_form_with_uploads() -> dict[str, str]:
    """The editor form, with any uploaded cover/article photo turned into a media: reference."""
    form = request.form.to_dict()
    cover = request.files.get("cover_upload")
    if cover is not None and cover.filename:
        form["cover_path"] = blog_media.save_image(cover)
    if not form.get("cover_path", "").strip():
        raise blog_media.BlogImageError("Choose a cover photo for the article.")
    photo = request.files.get("body_image")
    if photo is not None and photo.filename:
        reference = blog_media.save_image(photo)
        alt = (form.get("body_image_alt") or form.get("title") or "Photo").strip()[:200]
        alt = alt.replace("]", "").replace("[", "") or "Photo"
        form["body"] = f"{form.get('body', '').rstrip()}\n\n![{alt}]({reference})"
    return form


@bp.get("/blog/<int:post_id>/preview")
@roles(StudioRole.EDITOR)
def blog_preview(post_id: int) -> str:
    """How the article will look on the website, including drafts (never cached or indexed)."""
    post = db.get_or_404(BlogPost, post_id)
    return render_template(
        "content/post.html",
        post=blog_service.view_for_preview(post),
        previous_post=None,
        next_post=None,
        studio_preview=post,
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
        review.install(app)  # hold reviewed staff's changes for the owner's approval

    @app.cli.group("studio")
    def studio_cli() -> None:
        """Server-only Staff Studio account tools."""

    @studio_cli.command("create")
    @click.option("--username", prompt=True)
    @click.option(
        "--roles",
        prompt=(
            "Roles (owner,editor,inventory,support,branding,maintenance,sales; comma-separated)"
        ),
        default="editor",
    )
    @click.option(
        "--review/--no-review",
        default=False,
        help="Hold this person's changes until the owner approves them by email.",
    )
    @click.option(
        "--self-approve",
        is_flag=True,
        help="Allow approving their own sign-ins with a phone key the owner confirms.",
    )
    @click.option(
        "--sms",
        is_flag=True,
        help="Also require a texted code at every sign-in (set the number with set-phone).",
    )
    @click.password_option(confirmation_prompt=True)
    def create(
        username: str, roles: str, review: bool, self_approve: bool, sms: bool, password: str
    ) -> None:
        """Create a Studio user who must change the temporary password."""
        try:
            user = service.create_user(
                username,
                password,
                roles,
                requires_review=review,
                may_self_approve=self_approve,
                requires_sms=sms,
            )
        except (service.StudioError, ValueError) as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"Created {user.username} with roles: {user.roles}{_flag_text(user)}")

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
            status = "active" if user.active else "disabled"
            click.echo(f"{user.username}\t{user.roles}\t{status}{_flag_text(user)}")

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
        desired_names = {entry.username for entry in entries}
        plan: list[tuple[str, UserEntry]] = []
        for entry in entries:
            user = existing.get(entry.username)
            if user is None:
                plan.append(("create" if entry.active else "skip-disabled", entry))
            elif (
                user.roles,
                user.active,
                user.requires_review,
                user.may_self_approve,
                user.requires_sms,
            ) != (entry.roles, entry.active, entry.review, entry.self_approve, entry.sms):
                plan.append(("update", entry))
        for username, user in existing.items():
            if username not in desired_names and user.active:
                missing = UserEntry(username, user.roles, False, user.requires_review, False)
                plan.append(("disable-missing", missing))
        if not plan:
            click.echo("No Studio user changes.")
            return
        for action, entry in plan:
            flags = (
                (" review" if entry.review else "")
                + (" self-approve" if entry.self_approve else "")
                + (" sms" if entry.sms else "")
            )
            status = "active" if entry.active else "disabled"
            click.echo(f"{action}: {entry.username} roles={entry.roles} status={status}{flags}")
        if not apply:
            click.echo("Preview only. Run again with --apply to make these changes.")
            return
        for action, entry in plan:
            if action == "create":
                password = click.prompt(
                    f"Temporary password for {entry.username}",
                    hide_input=True,
                    confirmation_prompt=True,
                )
                service.create_user(
                    entry.username,
                    password,
                    entry.roles,
                    requires_review=entry.review,
                    may_self_approve=entry.self_approve,
                    requires_sms=entry.sms,
                )
            elif action == "update":
                service.configure_user(
                    existing[entry.username],
                    entry.roles,
                    entry.active,
                    entry.review,
                    entry.self_approve,
                    entry.sms,
                )
            elif action == "disable-missing":
                service.set_active(existing[entry.username], False)
        click.echo("Studio users synchronized. Removed entries were disabled, never deleted.")

    @studio_cli.command("set-phone")
    @click.argument("username")
    @click.argument("phone")
    def set_phone(username: str, phone: str) -> None:
        """Set the number that receives sign-in codes (kept only in the database)."""
        from app.accounts.service import AccountError, normalize_phone

        user = _cli_user(username)
        try:
            number = normalize_phone(phone)
        except AccountError as exc:
            raise click.ClickException(str(exc)) from None
        user.sms_phone = number
        user.session_version += 1
        service.audit(user, "studio.user.phone_set", "studio_user", str(user.id))
        db.session.commit()
        click.echo(f"Sign-in codes for {user.username} go to {sms.masked(number)}.")

    @studio_cli.command("set-authenticator")
    @click.argument("username")
    def set_authenticator(username: str) -> None:
        """Use an authenticator app for the sign-in code instead of texts (no SMS provider)."""
        user = _cli_user(username)
        user.totp_secret = admin_service.generate_totp_secret()
        user.totp_last_step = None
        user.requires_sms = True
        user.session_version += 1
        service.audit(user, "studio.user.authenticator_set", "studio_user", str(user.id))
        db.session.commit()
        label = urllib.parse.quote(f"GG Studio:{user.username}")
        click.echo("In Google Authenticator (or any authenticator app) choose 'Enter a setup key':")
        click.echo(f"  Account: GG Studio {user.username}")
        click.echo(f"  Key: {user.totp_secret}")
        click.echo(
            f"Or open this link on the phone: otpauth://totp/{label}?secret={user.totp_secret}"
            "&issuer=GG%20Studio&digits=6&period=30"
        )
        click.echo(
            "Every sign-in now asks for the app's 6-digit code. Running this again replaces it."
        )

    @studio_cli.command("remove-keys")
    @click.argument("username")
    def remove_keys(username: str) -> None:
        """Delete a person's sign-in keys (lost phone) and sign them out everywhere."""
        user = _cli_user(username)
        count = passkeys.remove_keys(user)
        click.echo(f"Removed {count} key(s) for {user.username}; their sessions were ended.")

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


class UserEntry(NamedTuple):
    username: str
    roles: str
    active: bool
    review: bool
    self_approve: bool
    sms: bool = False


def _flag_text(user: StudioUser) -> str:
    flags = [
        label
        for label, on in (
            ("changes need owner approval", user.requires_review),
            ("may approve own sign-in with a key", user.may_self_approve),
            ("texted code at every sign-in", user.requires_sms),
        )
        if on
    ]
    return f" ({'; '.join(flags)})" if flags else ""


def _read_user_manifest(path: Path) -> list[UserEntry]:
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
    entries: list[UserEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_users, start=1):
        allowed = {"username", "roles", "active", "review", "self_approve", "sms"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise click.ClickException(f"User {index} has unknown or invalid fields.")
        try:
            username = service.normalize_username(str(item.get("username", "")))
            roles = service.parse_roles(item.get("roles", []))
        except service.StudioError as exc:
            raise click.ClickException(f"User {index}: {exc}") from None
        if username in seen:
            raise click.ClickException(f"Studio username appears twice: {username}")
        active = item.get("active", True)
        review_on = item.get("review", False)
        self_approve = item.get("self_approve", False)
        sms_on = item.get("sms", False)
        if not all(isinstance(flag, bool) for flag in (active, review_on, self_approve, sms_on)):
            raise click.ClickException(
                f"User {index}: active, review, self_approve and sms must be true or false."
            )
        seen.add(username)
        role_values = ",".join(sorted(role.value for role in roles))
        entries.append(UserEntry(username, role_values, active, review_on, self_approve, sms_on))
    return entries

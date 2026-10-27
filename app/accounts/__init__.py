"""Customer account pages: register, sign in/out, profile, preferences, password, delete."""

from __future__ import annotations

import csv
import functools
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

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
    url_for,
)
from sqlalchemy import select
from werkzeug.wrappers import Response

from app import site_content
from app.accounts import service
from app.accounts.models import Address, Channel, StoredPaymentMethod, User
from app.extensions import db
from app.orders.models import Order

bp = Blueprint("accounts", __name__, url_prefix="/account")


@bp.app_context_processor
def account_globals() -> dict[str, Any]:
    return {"current_user": service.current_user()}


@bp.after_request
def _no_store(response: Response) -> Response:
    # Account pages show personal details: never cache them anywhere.
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _safe_next(default: str) -> str:
    """Only allow same-site relative paths (no open redirect)."""
    target = request.values.get("next", "")
    parts = urlsplit(target)
    if target.startswith("/") and not target.startswith("//") and not parts.netloc:
        return target
    return default


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if service.current_user() is None:
            return redirect(url_for("accounts.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _user() -> User:
    user = service.current_user()
    if user is None:  # pragma: no cover - guarded by login_required
        abort(401)
    return user


# ---------- register / login / logout ----------


@bp.route("/register", methods=["GET", "POST"])
def register() -> Response | str:
    if service.current_user():
        return redirect(url_for("accounts.dashboard"))
    form = request.form
    error = ""
    if request.method == "POST":
        try:
            user = service.register(
                service.Registration(
                    email=form.get("email", ""),
                    password=form.get("password", ""),
                    first_name=form.get("first_name", ""),
                    phone=form.get("phone", ""),
                    email_opt_in=form.get("email_opt_in") == "1",
                    sms_opt_in=form.get("sms_opt_in") == "1",
                )
            )
        except service.AccountError as exc:
            error = str(exc)
        else:
            service.login(user, remember=form.get("remember") == "1")
            flash("Welcome! Your account is ready.", "success")
            return redirect(_safe_next(url_for("accounts.dashboard")))
    return render_template(
        "accounts/register.html",
        error=error,
        form=form,
        email_wording=service.EMAIL_WORDING,
        sms_wording=service.SMS_WORDING,
    )


@bp.route("/login", methods=["GET", "POST"])
def login() -> Response | str:
    if service.current_user():
        return redirect(_safe_next(url_for("accounts.dashboard")))
    error = ""
    if request.method == "POST":
        try:
            user = service.authenticate(
                request.form.get("email", ""), request.form.get("password", "")
            )
        except service.AccountError as exc:
            error = str(exc)
        else:
            service.login(user, remember=request.form.get("remember") == "1")
            flash("You're signed in.", "success")
            return redirect(_safe_next(url_for("accounts.dashboard")))
    return render_template("accounts/login.html", error=error, email=request.form.get("email", ""))


@bp.post("/logout")
def logout() -> Response:
    service.logout()
    flash("You're signed out.", "success")
    return redirect(url_for("store.home"))


# ---------- account page ----------


@bp.get("/")
@login_required
def dashboard() -> str:
    user = _user()
    return render_template(
        "accounts/dashboard.html",
        user=user,
        email_on=user.consented(Channel.EMAIL),
        sms_on=user.consented(Channel.SMS),
        email_wording=service.EMAIL_WORDING,
        sms_wording=service.SMS_WORDING,
    )


@bp.post("/details")
@login_required
def details() -> Response:
    try:
        for notice in service.update_profile(
            _user(), request.form.get("first_name", ""), request.form.get("phone", "")
        ):
            flash(notice, "error")
        flash("Your details are saved.", "success")
    except service.AccountError as exc:
        flash(str(exc), "error")
    return redirect(url_for("accounts.dashboard"))


@bp.post("/preferences")
@login_required
def preferences() -> Response:
    user = _user()
    try:
        service.set_consent(user, Channel.EMAIL, request.form.get("email_opt_in") == "1", "account")
        service.set_consent(user, Channel.SMS, request.form.get("sms_opt_in") == "1", "account")
    except service.AccountError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash("Your communication preferences are saved.", "success")
    return redirect(url_for("accounts.dashboard") + "#preferences")


@bp.post("/password")
@login_required
def password() -> Response:
    try:
        service.change_password(
            _user(), request.form.get("current_password", ""), request.form.get("new_password", "")
        )
        flash("Password changed. Other devices have been signed out.", "success")
    except service.AccountError as exc:
        flash(str(exc), "error")
    return redirect(url_for("accounts.dashboard") + "#password")


@bp.post("/delete")
@login_required
def delete() -> Response:
    if request.form.get("confirm") != "DELETE":
        flash("Type DELETE in capitals to confirm.", "error")
        return redirect(url_for("accounts.dashboard") + "#delete")
    try:
        service.delete_account(_user(), request.form.get("password", ""))
    except service.AccountError as exc:
        flash(str(exc), "error")
        return redirect(url_for("accounts.dashboard") + "#delete")
    flash("Your account and its details have been deleted.", "success")
    return redirect(url_for("store.home"))


# ---------- addresses, orders and provider payment references ----------


@bp.get("/addresses")
@login_required
def addresses() -> str:
    return render_template("accounts/addresses.html", user=_user())


@bp.route("/addresses/new", methods=["GET", "POST"])
@bp.route("/addresses/<int:address_id>", methods=["GET", "POST"])
@login_required
def address_edit(address_id: int | None = None) -> str | Response:
    user = _user()
    address = (
        db.session.scalar(
            select(Address).where(Address.id == address_id, Address.user_id == user.id)
        )
        if address_id is not None
        else None
    )
    if address_id is not None and address is None:
        abort(404)
    error = ""
    if request.method == "POST":
        try:
            address = service.save_address(user, address, request.form.to_dict())
        except service.AccountError as exc:
            db.session.rollback()
            error = str(exc)
        else:
            flash("Address saved.", "success")
            return redirect(url_for("accounts.addresses"))
    countries = [
        (code, site_content.COUNTRY_NAMES.get(code, code))
        for code in current_app.config.get("SUPPORTED_COUNTRIES", ())
    ]
    return render_template(
        "accounts/address.html",
        user=user,
        address=address,
        countries=countries,
        error=error,
        form=request.form,
    )


@bp.post("/addresses/<int:address_id>/delete")
@login_required
def address_delete(address_id: int) -> Response:
    user = _user()
    address = db.session.scalar(
        select(Address).where(Address.id == address_id, Address.user_id == user.id)
    )
    if address is None:
        abort(404)
    service.delete_address(user, address)
    flash("Address removed.", "success")
    return redirect(url_for("accounts.addresses"))


@bp.get("/orders")
@login_required
def orders() -> str:
    records = db.session.scalars(
        select(Order).where(Order.user_id == _user().id).order_by(Order.created_at.desc())
    ).all()
    return render_template("accounts/orders.html", user=_user(), orders=records)


@bp.get("/orders/<public_id>")
@login_required
def order(public_id: str) -> str:
    record = db.session.scalar(
        select(Order).where(Order.public_id == public_id, Order.user_id == _user().id)
    )
    if record is None:
        abort(404)
    return render_template("accounts/order.html", user=_user(), order=record)


@bp.get("/payment-methods")
@login_required
def payment_methods() -> str:
    return render_template("accounts/payment_methods.html", user=_user())


@bp.post("/payment-methods/<int:method_id>/delete")
@login_required
def payment_method_delete(method_id: int) -> Response:
    method = db.session.scalar(
        select(StoredPaymentMethod).where(
            StoredPaymentMethod.id == method_id, StoredPaymentMethod.user_id == _user().id
        )
    )
    if method is None:
        abort(404)
    db.session.delete(method)
    db.session.commit()
    flash("Saved payment method removed from this account.", "success")
    return redirect(url_for("accounts.payment_methods"))


# ---------- password reset ----------


@bp.route("/password/forgot", methods=["GET", "POST"])
def forgot_password() -> Response | str:
    available = service.reset_available()
    if request.method == "POST" and available:
        try:
            service.start_reset(request.form.get("email", ""))
        except service.RateLimited as exc:
            flash(str(exc), "error")
            return redirect(url_for("accounts.forgot_password"))
        flash(
            "If an account uses that email, we've sent a link to reset the password. "
            "It works for one hour.",
            "success",
        )
        return redirect(url_for("accounts.login"))
    return render_template("accounts/forgot.html", available=available)


@bp.route("/password/reset/<token>", methods=["GET", "POST"])
def reset_password(token: str) -> Response | str:
    if request.method == "POST":
        try:
            service.complete_reset(token, request.form.get("password", ""))
        except service.AccountError as exc:
            if not service.reset_token_ok(token):
                flash(str(exc), "error")
                return redirect(url_for("accounts.forgot_password"))
            return render_template("accounts/reset.html", error=str(exc), token=token)
        flash("Your password has been changed. Please sign in.", "success")
        return redirect(url_for("accounts.login"))
    if not service.reset_token_ok(token):
        flash("This reset link has expired or was already used. Request a new one.", "error")
        return redirect(url_for("accounts.forgot_password"))
    return render_template("accounts/reset.html", error="", token=token)


# ---------- setup & owner tools ----------


def init_accounts(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.cli.group("accounts")
    def accounts_cli() -> None:
        """Customer account tools."""

    @accounts_cli.command("export-marketing")
    @click.option("--channel", type=click.Choice(["email", "sms"]), required=True)
    def export_marketing(channel: str) -> None:
        """Write opted-in customers as CSV to stdout (for your email/SMS provider).

        Contains personal data: import it into the provider, then delete the file.
        """
        chan = Channel(channel)
        writer = csv.writer(sys.stdout)
        writer.writerow(
            ["first_name", "email" if chan == Channel.EMAIL else "phone", "opted_in_at"]
        )
        for user in service.opted_in(chan):
            latest = next(c for c in reversed(user.consents) if c.channel == chan)
            writer.writerow(
                [
                    user.first_name,
                    user.email if chan == Channel.EMAIL else user.phone,
                    latest.created_at.isoformat(),
                ]
            )

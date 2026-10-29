"""Preview lock: the shop is live on its real domain but closed to the public while it is built.

SiteGround only routes a Node.js project's main domain to the app, so development happens on
ggafrikaneleganz.com itself. Setting PREVIEW_PASSWORD turns the lock on:

* visitors get a "coming soon" page (503 + noindex, so search engines keep nothing);
* anyone who enters the password at /preview gets a signed cookie and the whole site;
* payment webhooks, health checks, /admin, the Studio and static files are never locked:
  they have their own protection and must keep working for testing.

Remove PREVIEW_PASSWORD at launch. Changing it signs every previewer out.
"""

from __future__ import annotations

import hashlib
import hmac

from flask import (
    Blueprint,
    Flask,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.wrappers import Response

from app import ratelimit

bp = Blueprint("preview", __name__)

COOKIE = "gg_preview"
MAX_AGE = 30 * 86400  # previewers re-enter the password monthly
TRIES = 10  # wrong passwords per visitor per hour
# Whole path segments only, so lookalikes such as /administrator stay locked.
ALWAYS_OPEN = (
    "static",
    "media",
    "health",
    "webhooks",  # Stripe/PayPal must reach these for test orders to complete
    "admin",  # owner sign-in (password + authenticator); phone approvals use it
    "preview",
    ".well-known",
)
ALWAYS_OPEN_FILES = ("/robots.txt", "/favicon.ico")


def enabled() -> bool:
    return bool(current_app.config.get("PREVIEW_PASSWORD"))


def _fingerprint() -> str:
    # Ties cookies to the current password: changing it locks everyone out again.
    password = str(current_app.config["PREVIEW_PASSWORD"]).encode()
    return hashlib.sha256(password).hexdigest()[:24]


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="gg-preview")


def unlocked() -> bool:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return False
    try:
        value = _serializer().loads(raw, max_age=MAX_AGE)
    except BadSignature:
        return False
    return hmac.compare_digest(str(value), _fingerprint())


def _open_path(path: str) -> bool:
    if path in ALWAYS_OPEN_FILES:
        return True
    first = path.lstrip("/").split("/", 1)[0]
    studio = str(current_app.config.get("STUDIO_PATH") or "").strip("/")
    # The Studio has its own sign-in and owner approval.
    return first in ALWAYS_OPEN or (bool(studio) and first == studio)


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def _coming_soon() -> Response:
    response = Response(render_template("preview/coming_soon.html"), status=503)
    response.headers["Retry-After"] = "86400"
    return response


@bp.route("/preview", methods=["GET", "POST"])
def preview() -> Response | str:
    if not enabled():
        return redirect(url_for("store.home"))
    wanted = _safe_next(request.values.get("next"))
    error = ""
    if request.method == "POST":
        # Fail closed: without a working limiter the password could be guessed freely.
        if not ratelimit.allow("preview", ratelimit.client_key(), TRIES, 3600, fail_closed=True):
            error = "Too many attempts. Please try again later."
        elif hmac.compare_digest(
            request.form.get("password", "").encode(),
            str(current_app.config["PREVIEW_PASSWORD"]).encode(),
        ):
            response = redirect(wanted)
            response.set_cookie(
                COOKIE,
                _serializer().dumps(_fingerprint()),
                max_age=MAX_AGE,
                secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")),
                httponly=True,
                samesite="Lax",
            )
            return response
        else:
            error = "That password is not right."
    return render_template("preview/unlock.html", error=error, next=wanted)


def init_preview(app: Flask) -> None:
    app.register_blueprint(bp)

    @app.before_request
    def _preview_lock() -> Response | None:
        if not enabled() or _open_path(request.path) or unlocked():
            return None
        return _coming_soon()

    @app.after_request
    def _no_indexing(response: Response) -> Response:
        if enabled():
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

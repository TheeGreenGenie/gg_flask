"""Text-message step for Studio sign-ins (owner, 2026-10-27: the tgg web-developer account).

For a user with `requires_sms`, an approved sign-in (owner approval or their own phone key) is
not enough: a 6-digit code is texted to their number and must be typed in before the session
starts. The code is stored only as a hash, expires after 5 minutes, allows 5 guesses and at most
3 sends per sign-in.

Backends (STUDIO_SMS_BACKEND): "twilio" (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
TWILIO_FROM_NUMBER) for real texts; "console" for local testing only (the code is written to the
server log). With no backend, or no phone number on the account, the sign-in is refused — the
account stays locked rather than skipping the text (fail closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

from flask import current_app

from app.catalog.models import utcnow
from app.extensions import db
from app.studio.models import StudioLoginRequest

log = logging.getLogger(__name__)
CODE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_SENDS = 3
RESEND_AFTER = timedelta(seconds=30)
TWILIO_API = "https://api.twilio.com/2010-04-01"


class SmsError(ValueError):
    """A plain-language reason the text step could not continue."""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _digest(record: StudioLoginRequest, code: str) -> str:
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, f"{record.request_id}:{code}".encode(), hashlib.sha256).hexdigest()


def needed(record: StudioLoginRequest) -> bool:
    return bool(record.user.requires_sms) and record.sms_verified_at is None


def masked(phone: str | None) -> str:
    return f"•••• {phone[-4:]}" if phone and len(phone) >= 4 else "your phone"


def send_code(record: StudioLoginRequest, force: bool = False) -> None:
    """Text a fresh code unless one was sent moments ago."""
    from app.studio import service

    user = record.user
    backend = current_app.config.get("STUDIO_SMS_BACKEND")
    if not user.sms_phone or backend not in {"twilio", "console"}:
        raise SmsError(
            "Text-message sign-in is not set up for this account yet, so it can't be used."
        )
    now = utcnow()
    if record.sms_sent_at and not force and now - _aware(record.sms_sent_at) < RESEND_AFTER:
        return
    if record.sms_sends >= MAX_SENDS:
        raise SmsError("Too many codes were sent. Start the sign-in again.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    record.sms_code_hash = _digest(record, code)
    record.sms_sent_at = now
    record.sms_sends += 1
    record.sms_attempts = 0
    # The text step has its own window; the 2-minute approval window may already be used up.
    record.expires_at = max(_aware(record.expires_at), now + CODE_TTL)
    service.audit(user, "studio.login.sms_sent", "login_request", record.request_id)
    db.session.commit()
    message = f"GG Afrikan Eleganz Studio sign-in code: {code}. It expires in 5 minutes."
    if backend == "console":
        log.warning("studio_sms_code user=%s code=%s (console backend)", user.username, code)
        return
    _send_twilio(user.sms_phone, message)


def verify(record: StudioLoginRequest, code_raw: str) -> None:
    from app.studio import service

    code = "".join(ch for ch in (code_raw or "") if ch.isdigit())
    if record.sms_code_hash is None or record.sms_sent_at is None:
        raise SmsError("Ask for a code first.")
    if utcnow() - _aware(record.sms_sent_at) > CODE_TTL:
        raise SmsError("That code expired. Send a new one.")
    if record.sms_attempts >= MAX_ATTEMPTS:
        raise SmsError("Too many wrong codes. Start the sign-in again.")
    record.sms_attempts += 1
    if len(code) != 6 or not hmac.compare_digest(_digest(record, code), record.sms_code_hash):
        db.session.commit()
        raise SmsError("That code is not right. Check the text and try again.")
    record.sms_verified_at = utcnow()
    record.sms_code_hash = None
    service.audit(record.user, "studio.login.sms_verified", "login_request", record.request_id)
    db.session.commit()


def _send_twilio(to: str, body: str) -> None:
    cfg = current_app.config
    sid, token, sender = (
        cfg.get("TWILIO_ACCOUNT_SID"),
        cfg.get("TWILIO_AUTH_TOKEN"),
        cfg.get("TWILIO_FROM_NUMBER"),
    )
    if not (sid and token and sender):
        raise SmsError("Text messages are not configured on the server.")
    url = f"{TWILIO_API}/Accounts/{urllib.parse.quote(str(sid))}/Messages.json"
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    data = urllib.parse.urlencode({"To": to, "From": sender, "Body": body}).encode()
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS API host
        url, data=data, headers={"Authorization": f"Basic {auth}"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass
    except (urllib.error.URLError, TimeoutError) as exc:
        log.error("studio_sms_failed reason=%s", type(exc).__name__)
        raise SmsError("The text could not be sent. Try again in a moment.") from None

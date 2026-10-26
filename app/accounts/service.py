"""Account rules (security doc §3 "Authentication and sessions", feature matrix A-001–A-006).

- Argon2id password hashes; parameters upgraded transparently on login.
- Login/reset responses never reveal whether an email is registered; a dummy hash check
  keeps timing similar for unknown emails.
- The session cookie (signed, HttpOnly, SameSite=Lax) holds the user id and a session
  version; changing or resetting the password bumps the version, ending every other login.
- Session data is cleared on login (no fixation) and logout.
- Marketing consent is opt-in per channel, recorded with its exact wording, and an SMS
  consent covers one phone number: changing or removing the number withdraws it.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import g, request, session, url_for
from sqlalchemy import delete, select

from app import mail, ratelimit
from app.accounts.models import Channel, MarketingConsent, PasswordReset, User
from app.catalog.models import utcnow
from app.extensions import db

EMAIL_WORDING = (
    "Email me about new prints, offers and opportunities from GG Afrikan Eleganz. "
    "I can unsubscribe at any time."
)
SMS_WORDING = (
    "Text me about new prints, offers and opportunities from GG Afrikan Eleganz at the "
    "number above. Message frequency varies. Msg & data rates may apply. Reply STOP to opt "
    "out or HELP for help. Consent is not a condition of purchase."
)
WORDING = {Channel.EMAIL: EMAIL_WORDING, Channel.SMS: SMS_WORDING}

RESET_TTL = timedelta(hours=1)
MIN_PASSWORD = 10
MAX_PASSWORD = 128
COMMON_PASSWORDS = frozenset(
    {
        "password12", "password123", "1234567890", "12345678910", "qwertyuiop",
        "iloveyou123", "passw0rd123", "welcome123", "abc1234567", "1q2w3e4r5t",
        "qwerty1234", "letmein123", "football123", "baseball123", "sunshine123",
    }
)  # fmt: skip

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class AccountError(ValueError):
    """Problem the visitor can fix; the message is safe to show."""


class RateLimited(AccountError):
    pass


# ---------- normalisation & validation ----------


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AccountError("Please enter a valid email address.")
    return email


def normalize_phone(raw: str) -> str | None:
    """Return E.164 (+15551234567) or None for blank. US numbers may omit +1."""
    text = (raw or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        if 8 <= len(digits) <= 15:
            return "+" + digits
    elif len(digits) == 10:
        return "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise AccountError("Please enter a valid phone number, e.g. 215-555-0123 or +44 20 7946 0958.")


def check_password(password: str, email: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AccountError(f"Use a password of {MIN_PASSWORD} to {MAX_PASSWORD} characters.")
    lowered = password.lower()
    local = email.split("@", 1)[0]
    if lowered in COMMON_PASSWORDS or email in lowered or (len(local) >= 3 and local in lowered):
        raise AccountError("That password is too easy to guess. Try a longer phrase.")


def _limit(bucket: str, key: str, limit: int, window: int) -> None:
    if not ratelimit.allow(bucket, key, limit, window):
        raise RateLimited("Too many attempts. Please wait a few minutes and try again.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- consent ----------


def _record_consent(user: User, channel: Channel, granted: bool, source: str) -> None:
    destination = user.email if channel == Channel.EMAIL else (user.phone or "")
    user.consents.append(
        MarketingConsent(
            channel=channel,
            granted=granted,
            wording=WORDING[channel],
            source=source,
            destination=destination,
            ip_address=(ratelimit.client_key() or "")[:45] or None,
            user_agent=(request.user_agent.string or "")[:300] or None,
        )
    )


def set_consent(user: User, channel: Channel, granted: bool, source: str) -> None:
    """Record a change of mind (no row when nothing changes)."""
    if channel == Channel.SMS and granted and not user.phone:
        raise AccountError("Add a mobile number to receive texts.")
    if user.consented(channel) != granted:
        _record_consent(user, channel, granted, source)


# ---------- registration & login ----------


@dataclass
class Registration:
    email: str
    password: str
    first_name: str = ""
    phone: str = ""
    email_opt_in: bool = False
    sms_opt_in: bool = False


def register(data: Registration) -> User:
    _limit("register", ratelimit.client_key(), 10, 3600)
    email = normalize_email(data.email)
    check_password(data.password, email)
    phone = normalize_phone(data.phone)
    if data.sms_opt_in and not phone:
        raise AccountError("Add a mobile number to receive texts, or untick the text option.")
    if db.session.scalar(select(User.id).where(User.email == email)) is not None:
        raise AccountError(
            "An account with this email already exists. Log in, or reset your password."
        )
    user = User(
        email=email,
        password_hash=_hasher.hash(data.password),
        first_name=(data.first_name or "").strip()[:80],
        phone=phone,
    )
    db.session.add(user)
    if data.email_opt_in:
        _record_consent(user, Channel.EMAIL, True, "register")
    if data.sms_opt_in:
        _record_consent(user, Channel.SMS, True, "register")
    db.session.commit()
    return user


def authenticate(email_raw: str, password: str) -> User:
    generic = AccountError("Email or password is incorrect.")
    _limit("login-ip", ratelimit.client_key(), 20, 900)
    try:
        email = normalize_email(email_raw)
    except AccountError:
        raise generic from None
    _limit("login-email", email, 10, 900)
    user = db.session.scalar(select(User).where(User.email == email))
    try:
        _hasher.verify(user.password_hash if user else _DUMMY_HASH, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise generic from None
    if user is None:
        raise generic
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    return user


def login(user: User, remember: bool) -> None:
    from app.wishlist import service as wishlist_service

    anonymous_wishlist = wishlist_service.current()  # before the session is reset
    session.clear()  # new session on login: prevents fixation
    session["uid"] = user.id
    session["sv"] = user.session_version
    session.permanent = remember
    user.last_login_at = utcnow()
    g.pop("user", None)
    wishlist_service.claim_for_user(anonymous_wishlist, user)
    db.session.commit()


def logout() -> None:
    session.clear()
    g.pop("user", None)
    g.pop("wishlist", None)


def current_user() -> User | None:
    if "user" not in g:
        user = None
        uid = session.get("uid")
        if isinstance(uid, int):
            candidate = db.session.get(User, uid)
            if candidate is not None and session.get("sv") == candidate.session_version:
                user = candidate
            else:
                session.clear()  # password changed elsewhere or account deleted
        g.user = user
    return g.user


# ---------- profile ----------


def update_profile(user: User, first_name: str, phone_raw: str) -> list[str]:
    """Update name/phone. Returns notices (e.g. text consent withdrawn by a number change)."""
    notices: list[str] = []
    phone = normalize_phone(phone_raw)
    user.first_name = (first_name or "").strip()[:80]
    if phone != user.phone:
        if user.consented(Channel.SMS):
            # The consent covered the old number; it does not carry over.
            _record_consent(user, Channel.SMS, False, "phone-changed")
            notices.append(
                "Your text-message opt-in was for your previous number, so it has been "
                "turned off. Tick it again below if you'd like texts at the new number."
            )
        user.phone = phone
    db.session.commit()
    return notices


def change_password(user: User, current: str, new: str) -> None:
    try:
        _hasher.verify(user.password_hash, current[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise AccountError("Your current password is incorrect.") from None
    check_password(new, user.email)
    user.password_hash = _hasher.hash(new)
    user.session_version += 1  # signs out every other device
    db.session.commit()
    session["sv"] = user.session_version  # keep this device signed in


def delete_account(user: User, password: str) -> None:
    try:
        _hasher.verify(user.password_hash, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise AccountError("Your password is incorrect.") from None
    from app.wishlist import service as wishlist_service

    wishlist_service.delete_for_user(user)
    db.session.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id))
    db.session.delete(user)  # consents cascade with it
    db.session.commit()
    logout()


# ---------- password reset ----------


def reset_available() -> bool:
    return mail.available()


def start_reset(email_raw: str) -> None:
    """Email a single-use link if the account exists. Same outcome either way."""
    _limit("reset-ip", ratelimit.client_key(), 10, 3600)
    try:
        email = normalize_email(email_raw)
    except AccountError:
        return
    _limit("reset-email", email, 3, 3600)
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None:
        return
    token = secrets.token_urlsafe(32)
    db.session.add(
        PasswordReset(user=user, token_hash=_hash_token(token), expires_at=utcnow() + RESET_TTL)
    )
    db.session.commit()
    link = url_for("accounts.reset_password", token=token, _external=True)
    mail.send(
        user.email,
        "Reset your GG Afrikan Eleganz password",
        f"Hi {user.first_name or 'there'},\n\nUse this link within one hour to choose a new "
        f"password:\n{link}\n\nIf you didn't ask for this, you can ignore this email.",
    )


def _valid_reset(token: str) -> PasswordReset | None:
    if not token or len(token) > 100:
        return None
    reset = db.session.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == _hash_token(token))
    )
    if reset is None or reset.used_at is not None:
        return None
    expires = reset.expires_at
    if expires.tzinfo is None:  # SQLite returns naive datetimes
        from datetime import UTC

        expires = expires.replace(tzinfo=UTC)
    return reset if expires > utcnow() else None


def reset_token_ok(token: str) -> bool:
    return _valid_reset(token) is not None


def complete_reset(token: str, new_password: str) -> User:
    reset = _valid_reset(token)
    if reset is None:
        raise AccountError("This reset link has expired or was already used. Request a new one.")
    user = reset.user
    check_password(new_password, user.email)
    user.password_hash = _hasher.hash(new_password)
    user.session_version += 1
    reset.used_at = utcnow()
    db.session.execute(
        delete(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.id != reset.id)
    )
    db.session.commit()
    return user


# ---------- marketing export (owner tool) ----------


def opted_in(channel: Channel) -> list[User]:
    users = db.session.scalars(select(User).order_by(User.created_at)).all()
    return [u for u in users if u.consented(channel) and (channel == Channel.EMAIL or u.phone)]

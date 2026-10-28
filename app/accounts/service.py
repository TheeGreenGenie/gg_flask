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
from flask import current_app, g, request, session, url_for
from sqlalchemy import delete, func, select

from app import mail, ratelimit
from app.accounts.models import (
    Address,
    Channel,
    EmailVerification,
    MarketingConsent,
    PasswordReset,
    User,
)
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
VERIFY_TTL = timedelta(hours=24)
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
POSTCODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{1,22}[A-Za-z0-9]$")
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")


class AccountError(ValueError):
    """Problem the visitor can fix; the message is safe to show."""


class RateLimited(AccountError):
    pass


class EmailNotVerified(AccountError):
    def __init__(self, email: str):
        super().__init__("Verify your email address before signing in.")
        self.email = email


# ---------- normalisation & validation ----------


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AccountError("Please enter a valid email address.")
    return email


def normalize_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AccountError(
            "Username must be 3 to 64 characters: lowercase letters, numbers, dots, "
            "dashes or underscores, beginning with a letter."
        )
    return username


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


def _limit(bucket: str, key: str, limit: int, window: int, *, fail_closed: bool = False) -> None:
    if not ratelimit.allow(bucket, key, limit, window, fail_closed=fail_closed):
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
    username: str
    email: str
    password: str
    first_name: str = ""
    phone: str = ""
    email_opt_in: bool = False
    sms_opt_in: bool = False


def register(data: Registration) -> tuple[User, bool]:
    _limit("register", ratelimit.client_key(), 10, 3600)
    username = normalize_username(data.username)
    email = normalize_email(data.email)
    check_password(data.password, email)
    phone = normalize_phone(data.phone)
    if data.sms_opt_in and not phone:
        raise AccountError("Add a mobile number to receive texts, or untick the text option.")
    existing = db.session.scalar(select(User).where(User.email == email))
    if existing is not None and existing.email_verified_at is not None:
        raise AccountError(
            "An account with this email already exists. Log in, or reset your password."
        )
    from app.admin.models import AdminUser

    customer_taken = db.session.scalar(select(User.id).where(User.username == username))
    admin_taken = db.session.scalar(select(AdminUser.id).where(AdminUser.username == username))
    if (customer_taken is not None and (existing is None or customer_taken != existing.id)) or (
        admin_taken is not None
    ):
        raise AccountError("That username is already in use. Choose another one.")
    if db.session.scalar(select(AdminUser.id).where(AdminUser.email == email)) is not None:
        raise AccountError("An administrator already uses this email address.")
    if existing is not None:
        # A pending account proved nothing; whoever registers this address again replaces it,
        # so a stranger cannot lock the real owner out until the 7-day purge.
        db.session.delete(existing)
        db.session.flush()
    user = User(
        username=username,
        email=email,
        password_hash=_hasher.hash(data.password),
        first_name=(data.first_name or "").strip()[:80],
        phone=phone,
    )
    db.session.add(user)
    db.session.commit()
    sent = _issue_email_verification(user, data.email_opt_in, data.sms_opt_in)
    return user, sent


def _issue_email_verification(user: User, email_opt_in: bool, sms_opt_in: bool) -> bool:
    token = secrets.token_urlsafe(32)
    db.session.add(
        EmailVerification(
            user=user,
            token_hash=_hash_token(token),
            email_opt_in=email_opt_in,
            sms_opt_in=sms_opt_in,
            expires_at=utcnow() + VERIFY_TTL,
        )
    )
    db.session.commit()
    link = url_for("accounts.verify_email", token=token, _external=True)
    return mail.send(
        user.email,
        "Verify your GG Afrikan Eleganz email",
        f"Hi {user.first_name or user.username},\n\nConfirm that this email belongs to you by "
        f"opening this link within 24 hours, then enter the password you chose:\n{link}\n\n"
        "If you did not create this account, you can ignore this message.",
    )


def resend_verification(email_raw: str) -> bool:
    """Best-effort resend with a generic browser response to prevent account discovery."""
    _limit("verify-resend-ip", ratelimit.client_key(), 10, 3600, fail_closed=True)
    try:
        email = normalize_email(email_raw)
    except AccountError:
        return False
    _limit("verify-resend-email", email, 3, 3600, fail_closed=True)
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None or user.email_verified_at is not None:
        return False
    latest = db.session.scalar(
        select(EmailVerification)
        .where(EmailVerification.user_id == user.id)
        .order_by(EmailVerification.id.desc())
    )
    return _issue_email_verification(
        user,
        bool(latest and latest.email_opt_in),
        bool(latest and latest.sms_opt_in),
    )


def _valid_email_verification(token: str) -> EmailVerification | None:
    if not token or len(token) > 100:
        return None
    verification = db.session.scalar(
        select(EmailVerification).where(EmailVerification.token_hash == _hash_token(token))
    )
    if verification is None or verification.used_at is not None:
        return None
    expires = verification.expires_at
    if expires.tzinfo is None:
        from datetime import UTC

        expires = expires.replace(tzinfo=UTC)
    user = verification.user
    if expires <= utcnow() or user.email_verified_at is not None:
        return None
    return verification


def verification_token_ok(token: str) -> bool:
    return _valid_email_verification(token) is not None


def verify_email(token: str, password: str) -> User:
    verification = _valid_email_verification(token)
    if verification is None:
        raise AccountError("That verification link is invalid or expired.")
    _limit("verify-email", f"{verification.id}:{ratelimit.client_key()}", 10, 900)
    user = verification.user
    try:
        _hasher.verify(user.password_hash, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise AccountError("That password is not correct.") from None
    now = utcnow()
    user.email_verified_at = now
    for record in user.email_verifications:
        if record.used_at is None:
            record.used_at = now
    if verification.email_opt_in:
        _record_consent(user, Channel.EMAIL, True, "register")
    if verification.sms_opt_in:
        _record_consent(user, Channel.SMS, True, "register")
    db.session.commit()
    return user


def authenticate(identity_raw: str, password: str) -> User:
    generic = AccountError("Username/email or password is incorrect.")
    _limit("login-ip", ratelimit.client_key(), 20, 900)
    try:
        identity = (
            normalize_email(identity_raw)
            if "@" in identity_raw
            else normalize_username(identity_raw)
        )
    except AccountError:
        raise generic from None
    _limit("login-identity", identity, 10, 900)
    field = User.email if "@" in identity else User.username
    user = db.session.scalar(select(User).where(field == identity))
    if user is not None and _linked_admin(user) is not None:
        user = None  # linked administrators must complete private-admin 2FA
    try:
        _hasher.verify(user.password_hash if user else _DUMMY_HASH, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise generic from None
    if user is None:
        raise generic
    if user.email_verified_at is None:
        raise EmailNotVerified(user.email)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    return user


def login(user: User, remember: bool) -> None:
    from app.wishlist import service as wishlist_service

    if user.email_verified_at is None:
        raise EmailNotVerified(user.email)
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
            if (
                candidate is not None
                and candidate.email_verified_at is not None
                and session.get("sv") == candidate.session_version
            ):
                user = candidate
            else:
                session.clear()  # password changed elsewhere or account deleted
        g.user = user
    return g.user


# ---------- profile ----------


def update_profile(user: User, username_raw: str, first_name: str, phone_raw: str) -> list[str]:
    """Update name/phone. Returns notices (e.g. text consent withdrawn by a number change)."""
    notices: list[str] = []
    username = normalize_username(username_raw)
    phone = normalize_phone(phone_raw)
    linked_admin = _linked_admin(user)
    if linked_admin is not None and username != user.username:
        raise AccountError("Administrator usernames are managed in the private admin account.")
    if username != user.username:
        from app.admin.models import AdminUser

        conflict = db.session.scalar(select(User.id).where(User.username == username))
        admin_conflict = db.session.scalar(
            select(AdminUser.id).where(AdminUser.username == username)
        )
        if conflict is not None or admin_conflict is not None:
            raise AccountError("That username is already in use. Choose another one.")
        user.username = username
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
    if _linked_admin(user) is not None:
        raise AccountError("Change this password from the private admin area.")
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
    if _linked_admin(user) is not None:
        raise AccountError("Administrator storefront accounts cannot be deleted here.")
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


def _linked_admin(user: User) -> object | None:
    from app.admin.models import AdminUser

    return db.session.scalar(select(AdminUser).where(AdminUser.customer_user_id == user.id))


def is_admin_linked(user: User) -> bool:
    return _linked_admin(user) is not None


def customer_for_admin(admin: object) -> User:
    """Return/create the non-privileged storefront identity behind a 2FA admin login."""
    from app.admin.models import AdminUser

    if not isinstance(admin, AdminUser):
        raise AccountError("Administrator account not found.")
    if admin.customer_user_id:
        user = db.session.get(User, admin.customer_user_id)
        if user is not None:
            return user
    username_taken = db.session.scalar(select(User.id).where(User.username == admin.username))
    email_taken = db.session.scalar(select(User.id).where(User.email == admin.email))
    if username_taken is not None or email_taken is not None:
        raise AccountError(
            "A customer account already uses this administrator username or email. "
            "Resolve the duplicate before linking accounts."
        )
    user = User(
        username=admin.username,
        email=admin.email,
        password_hash=_hasher.hash(secrets.token_urlsafe(32)),
        first_name=admin.username,
        email_verified_at=utcnow(),
    )
    db.session.add(user)
    db.session.flush()
    admin.customer_user_id = user.id
    db.session.commit()
    return user


# ---------- address book ----------


def save_address(user: User, address: Address | None, form: dict[str, str]) -> Address:
    is_first = not user.addresses
    if address is None:
        count = db.session.scalar(select(func.count(Address.id)).where(Address.user_id == user.id))
        if int(count or 0) >= 20:
            raise AccountError("You can save up to 20 addresses.")
        address = Address(user=user)
    elif address.user_id != user.id:
        raise AccountError("Address not found.")
    label = form.get("label", "").strip()
    if not 1 <= len(label) <= 40:
        raise AccountError("Give this address a label of 1 to 40 characters.")
    values: dict[str, str] = {}
    for key, limit, title in (
        ("first_name", 80, "first name"),
        ("last_name", 80, "last name"),
        ("address1", 160, "street address"),
        ("city", 100, "city"),
        ("region", 100, "state or region"),
    ):
        value = form.get(key, "").strip()
        if not value or len(value) > limit:
            raise AccountError(f"Enter a valid {title}.")
        values[key] = value
    address2 = form.get("address2", "").strip()
    if len(address2) > 160:
        raise AccountError("Address line 2 is too long.")
    postcode = form.get("postcode", "").strip().upper()
    if not POSTCODE_RE.match(postcode):
        raise AccountError("Enter a valid postal code.")
    country = form.get("country", "").strip().upper()
    if country not in current_app.config.get("SUPPORTED_COUNTRIES", ()):
        raise AccountError("We do not currently ship to that country.")
    address.label = label
    address.address2 = address2
    address.postcode = postcode
    address.country = country
    address.phone = normalize_phone(form.get("phone", ""))
    for key, value in values.items():
        setattr(address, key, value)
    make_default = form.get("is_default") == "1" or is_first
    if make_default:
        for saved in user.addresses:
            saved.is_default = False
        address.is_default = True
    db.session.add(address)
    db.session.commit()
    return address


def delete_address(user: User, address: Address) -> None:
    if address.user_id != user.id:
        raise AccountError("Address not found.")
    was_default = address.is_default
    db.session.delete(address)
    db.session.flush()
    if was_default:
        replacement = db.session.scalar(
            select(Address).where(Address.user_id == user.id).order_by(Address.id)
        )
        if replacement:
            replacement.is_default = True
    db.session.commit()


# ---------- password reset ----------


def reset_available() -> bool:
    return mail.available()


def start_reset(email_raw: str) -> None:
    """Email a single-use link if the account exists. Same outcome either way."""
    _limit("reset-ip", ratelimit.client_key(), 10, 3600, fail_closed=True)
    try:
        email = normalize_email(email_raw)
    except AccountError:
        return
    _limit("reset-email", email, 3, 3600, fail_closed=True)
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None or user.email_verified_at is None or _linked_admin(user) is not None:
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
    if _linked_admin(user) is not None:
        raise AccountError("Administrator passwords are reset from the private admin area.")
    check_password(new_password, user.email)
    user.password_hash = _hasher.hash(new_password)
    user.session_version += 1
    reset.used_at = utcnow()
    db.session.execute(
        delete(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.id != reset.id)
    )
    db.session.commit()
    return user


def purge_unverified(days: int = 7) -> int:
    """Delete abandoned pending accounts so random addresses cannot reserve names forever."""
    cutoff = utcnow() - timedelta(days=days)
    users = db.session.scalars(
        select(User).where(User.email_verified_at.is_(None), User.created_at <= cutoff)
    ).all()
    for user in users:
        db.session.delete(user)
    db.session.commit()
    return len(users)


# ---------- marketing export (owner tool) ----------


def opted_in(channel: Channel) -> list[User]:
    users = db.session.scalars(select(User).order_by(User.created_at)).all()
    return [u for u in users if u.consented(channel) and (channel == Channel.EMAIL or u.phone)]

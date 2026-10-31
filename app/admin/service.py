"""Admin authentication, TOTP, recovery codes, sessions, roles, and audit events."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import current_app, g, has_request_context, request, session
from sqlalchemy import select, update

from app import ratelimit, security_events
from app.accounts.models import User
from app.accounts.service import MAX_PASSWORD, AccountError, check_password, normalize_email
from app.admin.models import (
    AdminAudit,
    AdminLoginRequest,
    AdminRecoveryCode,
    AdminRole,
    AdminUser,
)
from app.catalog.models import utcnow
from app.extensions import db
from app.passwords import DUMMY_HASH

IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 8 * 60 * 60
PENDING_SECONDS = 5 * 60
RECOVERY_COUNT = 10
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")

_hasher = PasswordHasher()
_dummy_hash = DUMMY_HASH
_log = logging.getLogger(__name__)


class AdminError(AccountError):
    pass


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def site_tag() -> str:
    """Which site an authenticator entry belongs to, so live and local entries never look alike
    in the app (two identical "owner" entries is how a stale key got used on 2026-10-29)."""
    site_url = current_app.config.get("SITE_URL")
    if site_url:
        return urlsplit(str(site_url)).hostname or "live"
    return "live" if current_app.config.get("APP_ENV") == "production" else "local"


def provisioning_uri(username: str, secret: str) -> str:
    label = quote(f"GG Afrikan Eleganz:{username} @ {site_tag()}")
    issuer = quote("GG Afrikan Eleganz")
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&digits=6&period=30"


def totp(secret: str, at: int | None = None) -> str:
    now = int(time.time()) if at is None else at
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", now // 30), hashlib.sha1).digest()  # noqa: S324
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify_totp(secret: str, code: str, at: int | None = None) -> bool:
    if len(code) != 6 or not code.isdigit():
        return False
    now = int(time.time()) if at is None else at
    return any(hmac.compare_digest(totp(secret, now + drift), code) for drift in (-30, 0, 30))


def _recovery_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().upper().encode()).hexdigest()


def new_recovery_codes(admin: AdminUser) -> list[str]:
    db.session.query(AdminRecoveryCode).filter_by(admin_id=admin.id).delete()
    raw_codes = [secrets.token_hex(4).upper() for _ in range(RECOVERY_COUNT)]
    codes = [f"{code[:4]}-{code[4:]}" for code in raw_codes]
    admin.recovery_codes.extend(AdminRecoveryCode(code_hash=_recovery_hash(c)) for c in codes)
    return codes


def normalize_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AdminError(
            "Username must be 3 to 64 characters: lowercase letters, numbers, dots, "
            "dashes or underscores."
        )
    return username


def _check_admin_password(password: str, email: str) -> None:
    try:
        check_password(password, email)
    except AccountError as exc:
        raise AdminError(str(exc)) from None


def check_new_admin(username_raw: str, email_raw: str, password: str) -> tuple[str, str]:
    """Everything create_admin refuses, checked before anything is added to an authenticator."""
    username = normalize_username(username_raw)
    email = normalize_email(email_raw)
    _check_admin_password(password, email)
    if db.session.scalar(select(AdminUser.id).where(AdminUser.username == username)) is not None:
        raise AdminError("An administrator with that username already exists.")
    if db.session.scalar(select(User.id).where(User.username == username)) is not None:
        raise AdminError("A customer account with that username already exists.")
    if db.session.scalar(select(AdminUser.id).where(AdminUser.email == email)) is not None:
        raise AdminError("An administrator with that email already exists.")
    if db.session.scalar(select(User.id).where(User.email == email)) is not None:
        raise AdminError("A customer account with that email already exists.")
    return username, email


def create_admin(
    username_raw: str,
    email_raw: str,
    password: str,
    role: AdminRole,
    totp_secret: str | None = None,
) -> tuple[AdminUser, list[str]]:
    username, email = check_new_admin(username_raw, email_raw, password)
    admin = AdminUser(
        username=username,
        email=email,
        password_hash=_hasher.hash(password),
        role=role,
        totp_secret=totp_secret or generate_totp_secret(),
    )
    db.session.add(admin)
    db.session.flush()
    codes = new_recovery_codes(admin)
    audit(admin, "admin.created", "admin", str(admin.id), {"role": role.value})
    db.session.commit()
    return admin, codes


def begin_login(username_raw: str, password: str) -> None:
    generic = AdminError("Username or password is incorrect.")
    if not ratelimit.allow("admin-login-ip", ratelimit.client_key(), 20, 900, fail_closed=True):
        raise AdminError("Too many attempts. Please wait and try again.")
    try:
        username = normalize_username(username_raw)
    except AdminError:
        raise generic from None
    if not ratelimit.allow("admin-login-username", username, 10, 900, fail_closed=True):
        raise AdminError("Too many attempts. Please wait and try again.")
    admin = db.session.scalar(select(AdminUser).where(AdminUser.username == username))
    try:
        _hasher.verify(admin.password_hash if admin else _dummy_hash, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        security_events.record("admin_password")
        raise generic from None
    if admin is None or not admin.active:
        security_events.record("admin_password")
        raise generic
    if _hasher.check_needs_rehash(admin.password_hash):
        admin.password_hash = _hasher.hash(password)
        db.session.commit()
    _clear_admin_session()
    session["admin_pending"] = admin.id
    session["admin_pending_at"] = int(time.time())


APPROVAL_TTL = timedelta(minutes=2)  # same as Studio sign-in approvals


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


APPROVAL_REQUESTS = 5  # per account per 15 minutes, so nobody can flood the owner's phone
_MATCH_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def can_approve_sign_ins(admin: AdminUser | None) -> bool:
    """Only an owner who signs in with an authenticator approves sign-ins (Studio or admin).
    An approval-only account never approves anything, including itself."""
    return bool(admin and admin.role == AdminRole.OWNER and not admin.uses_owner_approval)


def _pending_admin() -> AdminUser | None:
    pending = session.get("admin_pending")
    started = session.get("admin_pending_at", 0)
    if not isinstance(pending, int) or not isinstance(started, int):
        return None
    if time.time() - started > PENDING_SECONDS:
        return None
    admin = db.session.get(AdminUser, pending)
    return admin if admin is not None and admin.active else None


def start_owner_approval() -> AdminLoginRequest | None:
    """After a correct password: for an approval account, ask the owner's phone and return the
    request (None for authenticator accounts, which go on to the code page)."""
    admin = _pending_admin()
    if admin is None or not admin.uses_owner_approval:
        return None
    if not ratelimit.allow(
        "admin-approval", str(admin.id), APPROVAL_REQUESTS, 900, fail_closed=True
    ):
        raise AdminError("Too many sign-in requests. Please wait a few minutes and try again.")
    token = secrets.token_urlsafe(32)
    record = AdminLoginRequest(
        request_id=secrets.token_urlsafe(24),
        admin_id=admin.id,
        approval_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        match_code=secrets.choice(_MATCH_ALPHABET) + secrets.choice(_MATCH_ALPHABET),
        status="pending",
        ip_address=(ratelimit.client_key() or "")[:45] or None,
        user_agent=(request.user_agent.string or "")[:300] or None,
        expires_at=utcnow() + APPROVAL_TTL,
    )
    db.session.add(record)
    audit(admin, "admin.login.approval_requested", "login_request", record.request_id)
    db.session.commit()
    session["admin_request"] = record.request_id  # only this browser may use the approval
    from flask import url_for

    from app.studio import service as studio_service

    studio_service.send_push(
        "Admin sign-in request",
        f"{admin.username} wants to sign in to admin. Match {record.match_code}.",
        url_for("admin.approve_sign_in", token=token, _external=True),
        record.expires_at,
        log_label=admin.username,
    )
    return record


def find_login_request(request_id: str) -> AdminLoginRequest | None:
    if not request_id or len(request_id) > 64:
        return None
    record = db.session.scalar(
        select(AdminLoginRequest).where(AdminLoginRequest.request_id == request_id)
    )
    if record and record.status == "pending" and _aware(record.expires_at) <= utcnow():
        record.status, record.decided_at = "expired", utcnow()
        db.session.commit()
    return record


def login_request_by_token(token: str) -> AdminLoginRequest | None:
    if not token or len(token) > 100:
        return None
    record = db.session.scalar(
        select(AdminLoginRequest).where(
            AdminLoginRequest.approval_token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    return find_login_request(record.request_id) if record else None


def _move(record: AdminLoginRequest, before: str, after: str, **values: Any) -> bool:
    """One conditional UPDATE, so two simultaneous clicks can never both win."""
    now = utcnow()
    changed = db.session.execute(
        update(AdminLoginRequest)
        .where(
            AdminLoginRequest.id == record.id,
            AdminLoginRequest.status == before,
            AdminLoginRequest.expires_at > now,
        )
        .values(status=after, **values)
        .execution_options(synchronize_session=False)
    ).rowcount  # type: ignore[attr-defined]
    db.session.refresh(record)
    return changed == 1


APPROVE_CODE_TRIES = 5  # wrong authenticator codes per request before it can't be approved


def approver_by_code(code_raw: str, request_key: str, requester_id: int | None = None) -> AdminUser:
    """The owner whose authenticator shows this code right now (approval without signing in).
    `request_key` names the sign-in request (admin or Studio) the wrong-code limit applies to;
    `requester_id` is the admin asking to sign in, who can never approve themselves."""
    if not ratelimit.allow("approve-code", request_key, APPROVE_CODE_TRIES, 900, fail_closed=True):
        raise AdminError("Too many wrong codes. Ask them to sign in again for a new request.")
    code = code_raw.replace(" ", "").strip()
    owners = db.session.scalars(
        select(AdminUser).where(AdminUser.active.is_(True), AdminUser.role == AdminRole.OWNER)
    ).all()
    for owner in owners:
        if (
            can_approve_sign_ins(owner)
            and owner.id != requester_id
            and verify_totp(owner.totp_secret, code)
        ):
            return owner
    raise AdminError("That code doesn't match the owner's authenticator app. Try the newest code.")


def decide_login_request(
    record: AdminLoginRequest, approve: bool, approver: AdminUser | None
) -> None:
    """Approving needs a proven owner; declining only needs the private link from the push."""
    if approver is not None and (
        not can_approve_sign_ins(approver) or approver.id == record.admin_id
    ):
        raise AdminError("Only the owner, signed in with an authenticator, can approve this.")
    if approve and approver is None:
        raise AdminError("Enter the code from your authenticator app to approve.")
    after = "approved" if approve else "denied"
    decided_by = approver.id if approver else None
    if not _move(record, "pending", after, decided_at=utcnow(), decided_by_id=decided_by):
        db.session.rollback()
        raise AdminError("This sign-in request has expired or was already decided.")
    audit(
        record.admin,
        f"admin.login.{after}",
        "login_request",
        record.request_id,
        {"by": approver.username if approver else "push link"},
    )
    db.session.commit()


def finish_approved_login(record: AdminLoginRequest) -> AdminUser:
    """Turn an approved request into a session, once, in the browser that asked for it."""
    if (
        session.get("admin_request") != record.request_id
        or session.get("admin_pending") != record.admin_id
    ):
        raise AdminError("This approval belongs to a different browser. Sign in again.")
    if record.status != "approved" or not _move(record, "approved", "consumed"):
        db.session.rollback()
        raise AdminError("This sign-in has not been approved, or was already used.")
    return _start_session(record.admin, "owner_approval")


def complete_login(code_raw: str) -> AdminUser:
    pending = session.get("admin_pending")
    started = session.get("admin_pending_at", 0)
    if (
        not isinstance(pending, int)
        or not isinstance(started, int)
        or time.time() - started > PENDING_SECONDS
    ):
        _clear_admin_session()
        raise AdminError("Your sign-in attempt expired. Start again.")
    admin = db.session.get(AdminUser, pending)
    if admin is None or not admin.active:
        _clear_admin_session()
        raise AdminError("Your sign-in attempt expired. Start again.")
    if admin.uses_owner_approval:  # no codes or recovery codes for these accounts
        raise AdminError("This account signs in with the owner's approval.")
    if not ratelimit.allow(
        "admin-otp", f"{admin.id}:{ratelimit.client_key()}", 10, 900, fail_closed=True
    ):
        raise AdminError("Too many attempts. Please wait and try again.")
    code = code_raw.replace(" ", "").strip().upper()
    recovery = db.session.scalar(
        select(AdminRecoveryCode).where(
            AdminRecoveryCode.admin_id == admin.id,
            AdminRecoveryCode.code_hash == _recovery_hash(code),
            AdminRecoveryCode.used_at.is_(None),
        )
    )
    if verify_totp(admin.totp_secret, code):
        method = "totp"
    elif recovery is not None:
        recovery.used_at = utcnow()
        method = "recovery"
    else:
        _wrong_code_after_right_password(admin)
        raise AdminError("That verification code is not valid.")
    return _start_session(admin, method)


CODE_ALERT_AFTER = 3  # wrong codes in 15 minutes, after a correct password


def _wrong_code_after_right_password(admin: AdminUser) -> None:
    """Reaching the code step means the password was right. Repeated wrong codes there mean
    someone else probably knows it: tell the owner at once (at most once an hour)."""
    security_events.record("admin_code")
    within_limit = ratelimit.allow(
        "admin-code-wrong", str(admin.id), CODE_ALERT_AFTER - 1, 900, fail_closed=False
    )
    if within_limit or not ratelimit.allow("admin-code-alerted", str(admin.id), 1, 3600):
        return
    audit(admin, "admin.login.code_alert", "admin", str(admin.id))
    db.session.commit()
    _notify_owner(
        "Admin sign-in warning",
        f"Someone entered the correct password for admin {admin.username} but then "
        f"{CODE_ALERT_AFTER} wrong sign-in codes. If this was not you or a teammate, change "
        "that password now (from the server: flask admin reset-password).",
    )


def _start_session(admin: AdminUser, method: str) -> AdminUser:
    _clear_admin_session()
    now = int(time.time())
    session.update(
        admin_id=admin.id,
        admin_sv=admin.session_version,
        admin_auth_at=now,
        admin_seen_at=now,
    )
    admin.last_login_at = utcnow()
    audit(admin, "admin.login", "admin", str(admin.id), {"method": method})
    db.session.commit()
    g.admin = admin
    return admin


def current_admin() -> AdminUser | None:
    if "admin" in g:
        return g.admin
    admin = None
    admin_id = session.get("admin_id")
    now = int(time.time())
    auth_at = session.get("admin_auth_at", 0)
    seen_at = session.get("admin_seen_at", 0)
    if (
        isinstance(admin_id, int)
        and isinstance(auth_at, int)
        and isinstance(seen_at, int)
        and now - auth_at <= ABSOLUTE_SECONDS
        and now - seen_at <= IDLE_SECONDS
    ):
        candidate = db.session.get(AdminUser, admin_id)
        if candidate and candidate.active and session.get("admin_sv") == candidate.session_version:
            admin = candidate
            session["admin_seen_at"] = now
    if admin is None and admin_id is not None:
        _clear_admin_session()
    g.admin = admin
    return admin


def logout() -> None:
    admin = current_admin()
    if admin:
        audit(admin, "admin.logout", "admin", str(admin.id))
        db.session.commit()
    _clear_admin_session()
    g.pop("admin", None)


def change_password(admin: AdminUser, current_password: str, new_password: str) -> None:
    try:
        _hasher.verify(admin.password_hash, current_password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise AdminError("Your current password is incorrect.") from None
    _check_admin_password(new_password, admin.email)
    try:
        _hasher.verify(admin.password_hash, new_password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        pass
    else:
        raise AdminError("Choose a password you have not already been using.")
    admin.password_hash = _hasher.hash(new_password)
    admin.session_version += 1
    audit(admin, "admin.password.changed", "admin", str(admin.id))
    db.session.commit()
    # Keep this browser signed in while invalidating every other existing session.
    session["admin_sv"] = admin.session_version
    _password_change_notice(admin, reset=False)


def code_offset(secret: str, code: str, at: int | None = None, span: int = 600) -> int | None:
    """How far apart the clocks are for `code` to match (0 = now), or None if it never matches
    within +/- `span` seconds, which means the authenticator entry holds a different key."""
    now = int(time.time()) if at is None else at
    code = code.replace(" ", "").strip()
    for drift in sorted(range(-span, span + 1, 30), key=abs):
        if hmac.compare_digest(totp(secret, now + drift), code):
            return drift
    return None


def set_owner_approval(admin: AdminUser, enabled: bool) -> None:
    """Switch how this admin proves it is them; signs out every browser either way."""
    if admin.uses_owner_approval != enabled:
        admin.uses_owner_approval = enabled
        admin.session_version += 1
        audit(admin, "admin.second_factor", "admin", str(admin.id), {"owner_approval": enabled})
        db.session.commit()


def replace_totp_secret(admin: AdminUser, secret: str) -> None:
    """Server-only recovery: switch to a new authenticator key and sign out every browser."""
    admin.totp_secret = secret
    admin.session_version += 1
    audit(admin, "admin.authenticator.reset", "admin", str(admin.id))
    db.session.commit()


def reset_password(admin: AdminUser, new_password: str) -> None:
    """Server-only recovery: replace the hash and revoke every browser session."""
    _check_admin_password(new_password, admin.email)
    admin.password_hash = _hasher.hash(new_password)
    admin.session_version += 1
    audit(admin, "admin.password.reset", "admin", str(admin.id))
    db.session.commit()
    _password_change_notice(admin, reset=True)


def _password_change_notice(admin: AdminUser, *, reset: bool) -> None:
    """Notify without ever copying the password into email, push, logs, or audit data."""
    action = "reset by the server owner" if reset else "changed by the signed-in administrator"
    message = (
        f"The password for private admin username {admin.username} was {action}. "
        "The password itself is not stored or sent. If this was unexpected, disable the account "
        "and reset it from the server immediately."
    )
    _notify_owner("Private admin password changed", message)


def _notify_owner(subject: str, message: str) -> None:
    """Security notices: email to the owner address and a push to the owner's ntfy topic."""
    from app import mail

    recipient = str(current_app.config.get("ORDER_NOTIFICATION_EMAIL") or "").strip()
    if recipient and mail.available():
        try:
            mail.send(recipient, subject, message)
        except Exception:  # pragma: no cover - provider failures vary
            _log.exception("admin_security_notice_email_failed")
    if current_app.config.get("STUDIO_APPROVAL_BACKEND") == "ntfy" and current_app.config.get(
        "STUDIO_NTFY_TOPIC"
    ):
        _send_ntfy_security_notice(message)


def _send_ntfy_security_notice(message: str) -> None:
    import urllib.request

    server = str(current_app.config["STUDIO_NTFY_SERVER"]).rstrip("/") + "/"
    payload = json.dumps(
        {
            "topic": current_app.config["STUDIO_NTFY_TOPIC"],
            "title": "Private admin security notice",
            "message": message,
            "priority": 5,  # ntfy's JSON API takes 1-5 only; "high" is rejected with a 400
            "tags": ["warning"],
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if current_app.config.get("STUDIO_NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {current_app.config['STUDIO_NTFY_TOKEN']}"
    request_record = urllib.request.Request(  # noqa: S310 - config requires HTTPS
        server, data=payload, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request_record, timeout=3):  # noqa: S310
            pass
    except OSError:
        _log.exception("admin_security_notice_push_failed")


def audit(
    admin: AdminUser,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    ip_address = None
    user_agent = None
    if has_request_context():
        ip_address = (ratelimit.client_key() or "")[:45] or None
        user_agent = (request.user_agent.string or "")[:300] or None
    db.session.add(
        AdminAudit(
            admin_id=admin.id,
            actor_email=admin.email,
            action=action[:80],
            entity_type=entity_type[:80] if entity_type else None,
            entity_id=entity_id[:120] if entity_id else None,
            detail=json.dumps(detail or {}, sort_keys=True, separators=(",", ":")),
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )


def role_allows(admin: AdminUser, allowed: set[AdminRole]) -> bool:
    return admin.role == AdminRole.OWNER or admin.role in allowed


def _clear_admin_session() -> None:
    for key in tuple(session):
        if key.startswith("admin_"):
            session.pop(key, None)

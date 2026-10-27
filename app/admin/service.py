"""Admin authentication, TOTP, recovery codes, sessions, roles, and audit events."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import Any
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import g, has_request_context, request, session
from sqlalchemy import select

from app import ratelimit
from app.accounts.service import MAX_PASSWORD, AccountError, check_password, normalize_email
from app.admin.models import AdminAudit, AdminRecoveryCode, AdminRole, AdminUser
from app.catalog.models import utcnow
from app.extensions import db

IDLE_SECONDS = 30 * 60
ABSOLUTE_SECONDS = 8 * 60 * 60
PENDING_SECONDS = 5 * 60
RECOVERY_COUNT = 10

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(secrets.token_urlsafe(16))


class AdminError(AccountError):
    pass


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(admin: AdminUser) -> str:
    label = quote(f"GG Afrikan Eleganz:{admin.email}")
    issuer = quote("GG Afrikan Eleganz")
    return f"otpauth://totp/{label}?secret={admin.totp_secret}&issuer={issuer}&digits=6&period=30"


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


def create_admin(email_raw: str, password: str, role: AdminRole) -> tuple[AdminUser, list[str]]:
    email = normalize_email(email_raw)
    check_password(password, email)
    if db.session.scalar(select(AdminUser.id).where(AdminUser.email == email)) is not None:
        raise AdminError("An administrator with that email already exists.")
    admin = AdminUser(
        email=email,
        password_hash=_hasher.hash(password),
        role=role,
        totp_secret=generate_totp_secret(),
    )
    db.session.add(admin)
    db.session.flush()
    codes = new_recovery_codes(admin)
    audit(admin, "admin.created", "admin", str(admin.id), {"role": role.value})
    db.session.commit()
    return admin, codes


def begin_login(email_raw: str, password: str) -> None:
    generic = AdminError("Email or password is incorrect.")
    if not ratelimit.allow("admin-login-ip", ratelimit.client_key(), 20, 900):
        raise AdminError("Too many attempts. Please wait and try again.")
    try:
        email = normalize_email(email_raw)
    except AccountError:
        raise generic from None
    if not ratelimit.allow("admin-login-email", email, 10, 900):
        raise AdminError("Too many attempts. Please wait and try again.")
    admin = db.session.scalar(select(AdminUser).where(AdminUser.email == email))
    try:
        _hasher.verify(admin.password_hash if admin else _dummy_hash, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise generic from None
    if admin is None or not admin.active:
        raise generic
    if _hasher.check_needs_rehash(admin.password_hash):
        admin.password_hash = _hasher.hash(password)
        db.session.commit()
    _clear_admin_session()
    session["admin_pending"] = admin.id
    session["admin_pending_at"] = int(time.time())


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
    if not ratelimit.allow("admin-otp", f"{admin.id}:{ratelimit.client_key()}", 10, 900):
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
        raise AdminError("That verification code is not valid.")
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

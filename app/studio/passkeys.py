"""Phone passkeys for co-owners who approve their own Studio sign-ins (owner, 2026-10-27).

A Studio user with `may_self_approve` (Mrs. Adetolu) can add a passkey on her own phone, but
only after the owner confirms that request from the email we send them (owner admin sign-in
required). Once registered, her sign-ins skip the owner's approval: the waiting screen asks her
phone for the passkey (Face ID / fingerprint) instead, and the owner gets an FYI email.

Only the public key is stored. Challenges are single-use and live in the signed session cookie,
bound to the enrollment or sign-in request they were issued for. WebAuthn needs HTTPS (or
localhost); the relying-party ID defaults to the site's host name (STUDIO_RP_ID overrides).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Any

from flask import current_app, request, session, url_for
from sqlalchemy import select
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app import mail
from app.catalog.models import utcnow
from app.extensions import db
from app.studio.models import (
    KeyEnrollmentStatus,
    StudioKeyEnrollment,
    StudioLoginRequest,
    StudioPasskey,
    StudioUser,
)

log = logging.getLogger(__name__)
ENROLLMENT_TTL = timedelta(hours=24)
RP_NAME = "GG Afrikan Eleganz Staff Studio"
_REG = "studio_key_reg"
_AUTH = "studio_key_auth"
_SWAP_AUTH = "studio_password_swap_auth"


class PasskeyError(ValueError):
    """A plain-language reason the key step could not finish."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def rp_id() -> str:
    configured = current_app.config.get("STUDIO_RP_ID")
    return str(configured or request.host.split(":")[0])


def origin() -> str:
    return request.host_url.rstrip("/")


def keys_for(user: StudioUser) -> list[StudioPasskey]:
    return list(
        db.session.scalars(
            select(StudioPasskey)
            .where(StudioPasskey.user_id == user.id)
            .order_by(StudioPasskey.created_at)
        )
    )


def can_self_approve(user: StudioUser) -> bool:
    return bool(user.may_self_approve and user.active and keys_for(user))


def _approver() -> str | None:
    cfg = current_app.config
    return cfg.get("STUDIO_APPROVER_EMAIL") or cfg.get("ORDER_NOTIFICATION_EMAIL")


def _tell_owner(subject: str, body: str) -> None:
    recipient = _approver()
    try:
        sent = bool(recipient) and mail.send(str(recipient), subject, body)
    except Exception:  # never fail the sign-in or key step because email failed
        log.exception("studio_key_notification_failed")
        sent = False
    if not sent:
        log.warning("studio_key_notification_not_sent subject=%s", subject)


# ---------- enrollment (owner must confirm) ----------


def open_enrollment(user: StudioUser) -> StudioKeyEnrollment | None:
    """The newest enrollment that is still pending or approved-but-unused."""
    record = db.session.scalar(
        select(StudioKeyEnrollment)
        .where(
            StudioKeyEnrollment.user_id == user.id,
            StudioKeyEnrollment.status.in_(
                [KeyEnrollmentStatus.PENDING, KeyEnrollmentStatus.APPROVED]
            ),
        )
        .order_by(StudioKeyEnrollment.created_at.desc())
    )
    if record and _aware(record.expires_at) <= utcnow():
        record.status = KeyEnrollmentStatus.EXPIRED
        db.session.commit()
        return None
    return record


def request_enrollment(user: StudioUser) -> StudioKeyEnrollment:
    from app.studio import service

    if not user.may_self_approve:
        raise PasskeyError("Your account is not set up to use a sign-in key.")
    existing = open_enrollment(user)
    if existing is not None:
        return existing
    token = secrets.token_urlsafe(32)
    record = StudioKeyEnrollment(
        user_id=user.id,
        token_hash=_hash(token),
        status=KeyEnrollmentStatus.PENDING,
        device=(request.user_agent.string or "")[:300],
        ip_address=(request.remote_addr or "")[:45] or None,
        expires_at=utcnow() + ENROLLMENT_TTL,
    )
    db.session.add(record)
    db.session.flush()
    service.audit(user, "studio.key.requested", "key_enrollment", str(record.id))
    db.session.commit()
    link = url_for("studio.key_approval", token=token, _external=True)
    _tell_owner(
        f"Confirm: {user.username} wants to add a sign-in key",
        f"{user.username} asked to set up a sign-in key on this device:\n{record.device}\n\n"
        "With the key she can approve her own Studio sign-ins (you will get an email each"
        " time). Only confirm if you expected this.\n\n"
        f"Confirm or refuse (owner sign-in required; expires in 24 hours):\n{link}\n",
    )
    return record


def enrollment_by_token(token: str) -> StudioKeyEnrollment | None:
    if not token or len(token) > 100:
        return None
    return db.session.scalar(
        select(StudioKeyEnrollment).where(StudioKeyEnrollment.token_hash == _hash(token))
    )


def decide_enrollment(record: StudioKeyEnrollment, approve: bool, decided_by: str) -> None:
    from app.studio import service

    if record.status != KeyEnrollmentStatus.PENDING or _aware(record.expires_at) <= utcnow():
        raise PasskeyError("This request has expired or was already decided.")
    record.status = KeyEnrollmentStatus.APPROVED if approve else KeyEnrollmentStatus.DENIED
    record.decided_at = utcnow()
    service.audit(
        record.user,
        "studio.key.confirmed" if approve else "studio.key.refused",
        "key_enrollment",
        str(record.id),
        {"decided_by": decided_by},
    )
    db.session.commit()


def registration_options(user: StudioUser) -> str:
    record = open_enrollment(user)
    if record is None or record.status != KeyEnrollmentStatus.APPROVED:
        raise PasskeyError("The owner has not confirmed a key for you yet.")
    options = generate_registration_options(
        rp_id=rp_id(),
        rp_name=RP_NAME,
        user_id=f"studio-user-{user.id}".encode(),
        user_name=user.username,
        user_display_name=user.username,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(key.credential_id))
            for key in keys_for(user)
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    session[_REG] = {
        "enrollment": record.id,
        "user": user.id,
        "challenge": bytes_to_base64url(options.challenge),
    }
    return options_to_json(options)


def register(user: StudioUser, credential: dict[str, Any], label: str = "") -> StudioPasskey:
    from app.studio import service

    pending = session.pop(_REG, None)
    record = open_enrollment(user)
    if (
        not isinstance(pending, dict)
        or pending.get("user") != user.id
        or record is None
        or record.status != KeyEnrollmentStatus.APPROVED
        or pending.get("enrollment") != record.id
    ):
        raise PasskeyError("Start the key setup again.")
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(pending["challenge"]),
            expected_rp_id=rp_id(),
            expected_origin=origin(),
            require_user_verification=True,
        )
    except InvalidRegistrationResponse:
        raise PasskeyError("The phone's key could not be verified. Please try again.") from None
    key = StudioPasskey(
        user_id=user.id,
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        label=(label or record.device)[:120],
    )
    db.session.add(key)
    record.status = KeyEnrollmentStatus.REGISTERED
    service.audit(user, "studio.key.registered", "studio_user", str(user.id))
    db.session.commit()
    _tell_owner(
        f"{user.username} added a sign-in key",
        f"{user.username} finished setting up a sign-in key on:\n{record.device}\n\n"
        "From now on she can approve her own Studio sign-ins with it. To remove the key, run"
        f" `flask --app wsgi studio remove-keys {user.username}` on the server.\n",
    )
    return key


# ---------- self-approval at sign-in ----------


def authentication_options(record: StudioLoginRequest) -> str:
    user = record.user
    if not can_self_approve(user):
        raise PasskeyError("This account has no sign-in key.")
    options = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(key.credential_id))
            for key in keys_for(user)
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session[_AUTH] = {
        "request": record.request_id,
        "challenge": bytes_to_base64url(options.challenge),
    }
    return options_to_json(options)


def self_approve(record: StudioLoginRequest, credential: dict[str, Any]) -> None:
    from app.studio import service

    pending = session.pop(_AUTH, None)
    user = record.user
    if not isinstance(pending, dict) or pending.get("request") != record.request_id:
        raise PasskeyError("Start the key check again.")
    if not can_self_approve(user):
        raise PasskeyError("This account has no sign-in key.")
    credential_id = str(credential.get("id") or credential.get("rawId") or "")
    key = db.session.scalar(
        select(StudioPasskey).where(
            StudioPasskey.user_id == user.id, StudioPasskey.credential_id == credential_id
        )
    )
    if key is None:
        raise PasskeyError("That key is not registered for this account.")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(pending["challenge"]),
            expected_rp_id=rp_id(),
            expected_origin=origin(),
            credential_public_key=key.public_key,
            credential_current_sign_count=key.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse:
        raise PasskeyError("The key could not be verified. Please try again.") from None
    key.sign_count = verified.new_sign_count
    key.last_used_at = utcnow()
    service.audit(user, "studio.login.self_approved", "login_request", record.request_id)
    service.decide(record, True)  # commits
    _tell_owner(
        f"{user.username} signed in to the Studio with her key",
        f"{user.username} approved her own Staff Studio sign-in with her phone key at"
        f" {utcnow():%Y-%m-%d %H:%M} UTC.\nBrowser: {record.user_agent or 'unknown'}\n"
        f"Address: {record.ip_address or 'unknown'}\n\n"
        "If this wasn't her, disable the account on the server:"
        f" `flask --app wsgi studio disable {user.username}`\n",
    )


def password_swap_options(user: StudioUser) -> str:
    """Create a new phone-key challenge for one tgg password swap."""
    from app.studio import service

    if not service.can_swap_passwords(user):
        raise PasskeyError("This Studio account cannot swap passwords.")
    if not can_self_approve(user):
        raise PasskeyError("Set up tgg's phone sign-in key before swapping passwords.")
    options = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(key.credential_id))
            for key in keys_for(user)
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session[_SWAP_AUTH] = {
        "user": user.id,
        "challenge": bytes_to_base64url(options.challenge),
    }
    return options_to_json(options)


def verify_password_swap_key(user: StudioUser, credential: dict[str, Any]) -> None:
    """Consume the challenge and verify tgg's key immediately before a swap."""
    from app.studio import service

    pending = session.pop(_SWAP_AUTH, None)
    if not isinstance(pending, dict) or pending.get("user") != user.id:
        raise PasskeyError("Start the phone-key check again.")
    if not service.can_swap_passwords(user) or not can_self_approve(user):
        raise PasskeyError("This Studio account cannot swap passwords.")
    credential_id = str(credential.get("id") or credential.get("rawId") or "")
    key = db.session.scalar(
        select(StudioPasskey).where(
            StudioPasskey.user_id == user.id,
            StudioPasskey.credential_id == credential_id,
        )
    )
    if key is None:
        raise PasskeyError("That key is not registered for tgg.")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(pending["challenge"]),
            expected_rp_id=rp_id(),
            expected_origin=origin(),
            credential_public_key=key.public_key,
            credential_current_sign_count=key.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse:
        raise PasskeyError("The phone key could not be verified. Please try again.") from None
    key.sign_count = verified.new_sign_count
    key.last_used_at = utcnow()
    service.audit(user, "studio.password_swap.key_verified", "studio_user", str(user.id))


def remove_keys(user: StudioUser) -> int:
    from app.studio import service

    keys = keys_for(user)
    for key in keys:
        db.session.delete(key)
    user.session_version += 1
    service.audit(user, "studio.key.removed", "studio_user", str(user.id), {"count": len(keys)})
    db.session.commit()
    return len(keys)


def _aware(value: Any) -> Any:
    from datetime import UTC

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value

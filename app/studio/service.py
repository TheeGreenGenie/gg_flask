"""Staff Studio authentication, approval workflow, permissions, and mutations."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import current_app, g, has_request_context, request
from sqlalchemy import func, or_, select, update
from werkzeug.datastructures import FileStorage

from app import cache, mail, ratelimit
from app.accounts.models import User
from app.accounts.service import MAX_PASSWORD, check_password
from app.catalog import images as image_service
from app.catalog import units
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
from app.content.models import BlogPost, BlogRevision, BlogStatus
from app.extensions import db
from app.passwords import DUMMY_HASH
from app.reviews.models import (
    ModerationWord,
    PostStatus,
    ProductPost,
    ProductPostReply,
)
from app.studio.models import (
    ApprovalStatus,
    InventoryHistory,
    PriceHistory,
    ProductHistory,
    SaleDiscountKind,
    SaleStatus,
    StudioAudit,
    StudioLoginRequest,
    StudioRole,
    StudioSale,
    StudioSaleItem,
    StudioSession,
    StudioUser,
)

APPROVAL_TTL = timedelta(minutes=2)
IDLE_TTL = timedelta(minutes=30)
ABSOLUTE_TTL = timedelta(hours=8)
LOCK_TTL = timedelta(minutes=15)
USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
COOKIE_MAX = 200
PASSWORD_SWAP_USERNAME = "tgg"  # noqa: S105 - account name, not a secret

_hasher = PasswordHasher()
_dummy_hash = DUMMY_HASH
_log = logging.getLogger(__name__)


class StudioError(ValueError):
    """A safe, actionable Studio error."""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise StudioError(
            "Username must be 3 to 64 characters: lowercase letters, numbers, dots, "
            "dashes or underscores."
        )
    return username


def parse_roles(values: str | list[str]) -> set[StudioRole]:
    raw = values.split(",") if isinstance(values, str) else values
    try:
        roles = {StudioRole(value.strip().lower()) for value in raw if value.strip()}
    except ValueError:
        raise StudioError("Choose only valid Studio roles.") from None
    if not roles:
        raise StudioError("Choose at least one Studio role.")
    return roles


def _check_studio_password(password: str, username: str) -> None:
    if len(password) < 12:
        raise StudioError("Use a password of 12 to 128 characters.")
    try:
        check_password(password, f"{username}@studio.invalid")
    except ValueError as exc:
        raise StudioError(str(exc)) from None


def create_user(
    username_raw: str,
    password: str,
    roles_raw: str | list[str],
    *,
    requires_review: bool = False,
    may_self_approve: bool = False,
    requires_sms: bool = False,
) -> StudioUser:
    username = normalize_username(username_raw)
    _check_studio_password(password, username)
    roles = parse_roles(roles_raw)
    if db.session.scalar(select(StudioUser.id).where(StudioUser.username == username)):
        raise StudioError("That Studio username already exists.")
    user = StudioUser(
        username=username,
        password_hash=_hasher.hash(password),
        roles=",".join(sorted(role.value for role in roles)),
        requires_review=requires_review,
        may_self_approve=may_self_approve,
        requires_sms=requires_sms,
    )
    db.session.add(user)
    db.session.flush()
    audit(
        user,
        "studio.user.created",
        "studio_user",
        str(user.id),
        {
            "roles": user.roles,
            "review": requires_review,
            "self_approve": may_self_approve,
            "sms": requires_sms,
        },
    )
    db.session.commit()
    return user


def reset_password(user: StudioUser, password: str) -> None:
    _check_studio_password(password, user.username)
    user.password_hash = _hasher.hash(password)
    user.must_change_password = True
    user.session_version += 1
    audit(user, "studio.user.password_reset", "studio_user", str(user.id))
    db.session.commit()


def can_swap_passwords(user: StudioUser | None) -> bool:
    return user is not None and user.username == PASSWORD_SWAP_USERNAME


def issue_temporary_password(actor: StudioUser, target: StudioUser) -> str:
    """Reset one Studio account after the route verifies tgg's phone passkey."""
    if not can_swap_passwords(actor):
        raise StudioError("This Studio account cannot swap passwords.")
    if target.id == actor.id:
        raise StudioError("Use Change password to update your own account.")
    if not target.active:
        raise StudioError("That Studio account is disabled.")
    if not ratelimit.allow(
        "studio-password-swap",
        f"{actor.id}:{ratelimit.client_key()}",
        10,
        900,
        fail_closed=True,
    ):
        raise StudioError("Too many password swaps. Please wait and try again.")
    temporary = secrets.token_urlsafe(18)
    _check_studio_password(temporary, target.username)
    target.password_hash = _hasher.hash(temporary)
    target.must_change_password = True
    target.session_version += 1
    audit(
        actor,
        "studio.user.password_swapped",
        "studio_user",
        str(target.id),
        {"username": target.username},
    )
    db.session.commit()
    return temporary


def set_active(user: StudioUser, active: bool) -> None:
    user.active = active
    user.session_version += 1
    audit(
        user,
        "studio.user.enabled" if active else "studio.user.disabled",
        "studio_user",
        str(user.id),
    )
    db.session.commit()


def configure_user(
    user: StudioUser,
    roles_raw: str | list[str],
    active: bool,
    requires_review: bool | None = None,
    may_self_approve: bool | None = None,
    requires_sms: bool | None = None,
) -> bool:
    """Apply non-secret manifest fields and revoke sessions when access changes."""
    roles = ",".join(sorted(role.value for role in parse_roles(roles_raw)))
    review = user.requires_review if requires_review is None else requires_review
    self_approve = user.may_self_approve if may_self_approve is None else may_self_approve
    sms_on = user.requires_sms if requires_sms is None else requires_sms
    before = {
        "roles": user.roles,
        "active": user.active,
        "review": user.requires_review,
        "self_approve": user.may_self_approve,
        "sms": user.requires_sms,
    }
    after = {
        "roles": roles,
        "active": active,
        "review": review,
        "self_approve": self_approve,
        "sms": sms_on,
    }
    if before == after:
        return False
    user.roles = roles
    user.active = active
    user.requires_review = review
    user.may_self_approve = self_approve
    user.requires_sms = sms_on
    user.session_version += 1
    audit(
        user,
        "studio.user.manifest_updated",
        "studio_user",
        str(user.id),
        {"before": before, "after": after},
    )
    db.session.commit()
    return True


def begin_login(username_raw: str, password: str) -> tuple[StudioLoginRequest, str]:
    generic = StudioError("Username or password is incorrect.")
    client = ratelimit.client_key()
    if not ratelimit.allow("studio-login-ip", client, 20, 900, fail_closed=True):
        raise StudioError("Too many attempts. Please wait and try again.")
    try:
        username = normalize_username(username_raw)
    except StudioError:
        username = "invalid"
    if not ratelimit.allow("studio-login-user", username, 10, 900, fail_closed=True):
        raise StudioError("Too many attempts. Please wait and try again.")
    user = db.session.scalar(select(StudioUser).where(StudioUser.username == username))
    if user and user.locked_until and _aware(user.locked_until) > utcnow():
        _hasher.verify(_dummy_hash, password[:MAX_PASSWORD])
        raise generic
    try:
        _hasher.verify(user.password_hash if user else _dummy_hash, password[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= 5:
                user.locked_until = utcnow() + LOCK_TTL
                user.failed_attempts = 0
                audit(user, "studio.login.locked", "studio_user", str(user.id))
            db.session.commit()
        raise generic from None
    if user is None or not user.active:
        raise generic
    user.failed_attempts = 0
    user.locked_until = None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    token = secrets.token_urlsafe(32)
    record = StudioLoginRequest(
        request_id=secrets.token_urlsafe(24),
        user=user,
        approval_token_hash=_hash(token),
        match_code=secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        + secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"),
        status=ApprovalStatus.PENDING,
        ip_address=(client or "")[:45] or None,
        user_agent=(request.user_agent.string or "")[:300] or None,
        expires_at=utcnow() + APPROVAL_TTL,
    )
    db.session.add(record)
    audit(user, "studio.login.requested", "login_request", record.request_id)
    db.session.commit()
    if _signed_in_as_linked_owner(user):
        decide(record, True)  # the owner is approving their own sign-in by being signed in
        audit(user, "studio.login.approved_by_admin_session", "login_request", record.request_id)
        db.session.commit()
        return record, token
    from app.studio import passkeys

    if not passkeys.can_self_approve(user):  # a co-owner approves with her own phone key
        _send_approval(record, token)
    return record, token


def _signed_in_as_linked_owner(user: StudioUser) -> bool:
    """True when this browser holds a fully verified /admin owner session for the account
    linked to this Studio user (password + authenticator already proved it is that person)."""
    from app.admin import service as admin_service
    from app.admin.models import AdminRole

    if user.admin_user_id is None:
        return False
    admin = admin_service.current_admin()
    return bool(admin and admin.id == user.admin_user_id and admin.role == AdminRole.OWNER)


def set_admin_link(user: StudioUser, admin_username: str | None) -> None:
    """Link (or unlink with None) a Studio account to the same person's /admin owner account."""
    from app.admin.models import AdminRole, AdminUser

    admin = None
    if admin_username:
        admin = db.session.scalar(select(AdminUser).where(AdminUser.username == admin_username))
        if admin is None or admin.role != AdminRole.OWNER:
            raise StudioError(f"{admin_username} is not an owner account in /admin.")
    before = user.admin_user_id
    user.admin_user_id = admin.id if admin else None
    if before != user.admin_user_id:
        user.session_version += 1
        audit(
            user,
            "studio.user.admin_link",
            "studio_user",
            str(user.id),
            {"admin": admin.username if admin else None},
        )
    db.session.commit()


def _send_approval(record: StudioLoginRequest, token: str) -> None:
    from flask import url_for

    send_push(
        "Staff Studio sign-in request",
        f"{record.user.username} wants to sign in. Match {record.match_code}.",
        url_for("studio.approval", token=token, _external=True),
        record.expires_at,
        log_label=record.user.username,
    )


def send_push(
    title: str, message: str, link: str, expires_at: datetime, *, log_label: str = ""
) -> None:
    """Approval push to the owner's phone (ntfy, or a webhook). Shared by Studio and /admin
    sign-ins. The console backend (development only) logs the link instead."""
    backend = current_app.config.get("STUDIO_APPROVAL_BACKEND", "console")
    if backend == "console":
        _log.warning("approval_requested who=%s title=%s url=%s", log_label, title, link)
        return
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if backend == "ntfy":
        target = str(current_app.config["STUDIO_NTFY_SERVER"]).rstrip("/") + "/"
        payload_data: dict[str, Any] = {
            "topic": current_app.config["STUDIO_NTFY_TOPIC"],
            "title": title,
            "message": message,
            "priority": 4,  # ntfy's JSON API takes 1-5 only; "high" is rejected with a 400
            "tags": ["key"],
            "click": link,
            "actions": [{"action": "view", "label": "Review sign-in", "url": link, "clear": True}],
        }
        ntfy_token = current_app.config.get("STUDIO_NTFY_TOKEN")
        if ntfy_token:
            headers["Authorization"] = f"Bearer {ntfy_token}"
    else:
        target = current_app.config["STUDIO_APPROVAL_WEBHOOK_URL"]
        payload_data = {
            "title": title,
            "message": message,
            "url": link,
            "expires_at": expires_at.isoformat(),
        }
    payload = json.dumps(payload_data).encode()
    req = urllib.request.Request(  # noqa: S310 - production configuration requires HTTPS
        target,
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3):  # noqa: S310
            pass
    except OSError:
        _log.exception("approval_notification_failed")


def find_request(request_id: str) -> StudioLoginRequest | None:
    if not request_id or len(request_id) > 64:
        return None
    record = db.session.scalar(
        select(StudioLoginRequest).where(StudioLoginRequest.request_id == request_id)
    )
    if record and record.status == ApprovalStatus.PENDING and _aware(record.expires_at) <= utcnow():
        record.status = ApprovalStatus.EXPIRED
        record.decided_at = utcnow()
        audit(record.user, "studio.login.expired", "login_request", record.request_id)
        db.session.commit()
    return record


def request_by_token(token: str) -> StudioLoginRequest | None:
    if not token or len(token) > 100:
        return None
    record = db.session.scalar(
        select(StudioLoginRequest).where(StudioLoginRequest.approval_token_hash == _hash(token))
    )
    return find_request(record.request_id) if record else None


def _transition(record: StudioLoginRequest, before: ApprovalStatus, after: ApprovalStatus) -> bool:
    """Move a login request between states in one UPDATE, so two simultaneous requests can never
    both win (e.g. one approval turned into two sessions, or approve racing deny)."""
    now = utcnow()
    changed = db.session.execute(
        update(StudioLoginRequest)
        .where(
            StudioLoginRequest.id == record.id,
            StudioLoginRequest.status == before,
            StudioLoginRequest.expires_at > now,
        )
        .values(
            status=after, decided_at=now if before == ApprovalStatus.PENDING else record.decided_at
        )
        .execution_options(synchronize_session=False)
    ).rowcount  # type: ignore[attr-defined]
    db.session.refresh(record)
    return changed == 1


def decide(record: StudioLoginRequest, approve: bool) -> None:
    target = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
    if not _transition(record, ApprovalStatus.PENDING, target):
        db.session.rollback()
        raise StudioError("This sign-in request has expired or was already decided.")
    audit(
        record.user,
        "studio.login.approved" if approve else "studio.login.denied",
        "login_request",
        record.request_id,
    )
    db.session.commit()


def consume_approval(record: StudioLoginRequest) -> tuple[StudioUser, str]:
    if record.status != ApprovalStatus.APPROVED or _aware(record.expires_at) <= utcnow():
        raise StudioError("This sign-in has not been approved.")
    if record.user.requires_sms and record.sms_verified_at is None:
        raise StudioError("Enter the code texted to your phone first.")
    if not _transition(record, ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED):
        db.session.rollback()
        raise StudioError("This sign-in approval has already been used.")
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session_record = StudioSession(
        token_hash=_hash(token),
        user=record.user,
        session_version=record.user.session_version,
        created_at=now,
        last_seen_at=now,
        expires_at=now + ABSOLUTE_TTL,
    )
    db.session.add(session_record)
    record.user.last_login_at = now
    audit(record.user, "studio.login.completed", "studio_user", str(record.user.id))
    db.session.commit()
    return record.user, token


def current_user() -> StudioUser | None:
    if "studio_user" in g:
        return g.studio_user
    raw = request.cookies.get(current_app.config["STUDIO_COOKIE_NAME"], "")[:COOKIE_MAX]
    record = None
    if raw:
        record = db.session.scalar(
            select(StudioSession).where(StudioSession.token_hash == _hash(raw))
        )
    now = utcnow()
    if (
        record is None
        or record.revoked_at is not None
        or _aware(record.expires_at) <= now
        or now - _aware(record.last_seen_at) > IDLE_TTL
        or not record.user.active
        or record.session_version != record.user.session_version
    ):
        g.studio_user = None
        return None
    record.last_seen_at = now
    db.session.commit()
    g.studio_user = record.user
    g.studio_session = record
    return record.user


def revoke_current() -> None:
    user = current_user()
    record = g.get("studio_session")
    if record:
        record.revoked_at = utcnow()
    if user:
        audit(user, "studio.logout", "studio_user", str(user.id))
    db.session.commit()
    g.pop("studio_user", None)
    g.pop("studio_session", None)


def change_password(user: StudioUser, current: str, new: str) -> None:
    try:
        _hasher.verify(user.password_hash, current[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        raise StudioError("Your current password is incorrect.") from None
    _check_studio_password(new, user.username)
    user.password_hash = _hasher.hash(new)
    user.must_change_password = False
    audit(user, "studio.password.changed", "studio_user", str(user.id))
    db.session.commit()


def allows(user: StudioUser, *roles: StudioRole) -> bool:
    return StudioRole.OWNER in user.role_set or bool(user.role_set.intersection(roles))


def update_inventory(
    user: StudioUser,
    variant: Variant,
    quantity_raw: str,
    reason_raw: str,
    *,
    mode: str = "set",
    expected_raw: str = "",
) -> None:
    try:
        quantity = int(quantity_raw)
    except ValueError:
        raise StudioError("Stock must be a whole number.") from None
    before = variant.stock_quantity
    if expected_raw:
        try:
            expected = int(expected_raw)
        except ValueError:
            raise StudioError("Reload the page before changing this stock amount.") from None
        if expected != before:
            raise StudioError("Stock changed since this page loaded. Review the new amount first.")
    if mode == "adjust":
        quantity += before
    elif mode != "set":
        raise StudioError("Choose a valid stock-change method.")
    if not 0 <= quantity <= 1_000_000:
        raise StudioError("Stock must be between 0 and 1,000,000.")
    reason = reason_raw.strip()
    if not 2 <= len(reason) <= 160:
        raise StudioError("Add a short reason for the stock change.")
    if before == quantity:
        raise StudioError("The stock amount has not changed.")
    variant.stock_quantity = quantity
    db.session.add(
        InventoryHistory(
            variant_id=variant.id,
            user_id=user.id,
            before_quantity=before,
            after_quantity=quantity,
            reason=reason,
        )
    )
    audit(
        user,
        "studio.inventory.updated",
        "variant",
        str(variant.id),
        {"before": before, "after": quantity, "reason": reason},
    )
    db.session.commit()
    cache.bump_catalog_version()


def undo_inventory(user: StudioUser, history: InventoryHistory) -> None:
    if history.undone_at is not None:
        raise StudioError("This stock change was already undone.")
    variant = db.session.get(Variant, history.variant_id)
    if variant is None:
        raise StudioError("The product option no longer exists.")
    if variant.stock_quantity != history.after_quantity:
        raise StudioError(
            "This stock changed again afterward, so it cannot be safely undone automatically."
        )
    current = variant.stock_quantity
    variant.stock_quantity = history.before_quantity
    history.undone_at = utcnow()
    db.session.add(
        InventoryHistory(
            variant_id=variant.id,
            user_id=user.id,
            before_quantity=current,
            after_quantity=history.before_quantity,
            reason=f"Undo change #{history.id}",
        )
    )
    audit(
        user,
        "studio.inventory.undone",
        "inventory_history",
        str(history.id),
        {"variant_id": variant.id, "restored_quantity": history.before_quantity},
    )
    db.session.commit()
    cache.bump_catalog_version()


def update_price(
    user: StudioUser,
    variant: Variant,
    price_raw: str,
    reason_raw: str,
    *,
    expected_raw: str = "",
) -> None:
    locked = db.session.scalar(
        select(StudioSaleItem.id)
        .join(StudioSaleItem.sale)
        .where(
            StudioSaleItem.variant_id == variant.id,
            StudioSale.status.in_([SaleStatus.SCHEDULED, SaleStatus.ACTIVE]),
        )
    )
    if locked is not None:
        raise StudioError(
            "This price belongs to a scheduled or active sale. Change the sale first."
        )
    try:
        text = price_raw.strip().replace("$", "").replace(",", "")
        whole, dot, fraction = text.partition(".")
        if not whole.isdigit() or (dot and (not fraction.isdigit() or len(fraction) > 2)):
            raise ValueError
        cents = int(whole) * 100 + int((fraction + "00")[:2])
    except ValueError:
        raise StudioError("Price must be a valid dollar amount.") from None
    if not 0 <= cents <= 100_000_000:
        raise StudioError("Price is outside the allowed range.")
    before = variant.price_cents
    if expected_raw:
        try:
            expected = int(expected_raw)
        except ValueError:
            raise StudioError("Reload the page before changing this price.") from None
        if expected != before:
            raise StudioError("Price changed since this page loaded. Review the new price first.")
    if variant.compare_at_cents is not None and variant.compare_at_cents <= cents:
        raise StudioError("Price must stay below the compare-at price, or remove that sale first.")
    reason = reason_raw.strip()
    if not 2 <= len(reason) <= 160:
        raise StudioError("Add a short reason for the price change.")
    if before == cents:
        raise StudioError("The price has not changed.")
    variant.price_cents = cents
    db.session.add(
        PriceHistory(
            variant_id=variant.id,
            user_id=user.id,
            before_cents=before,
            after_cents=cents,
            reason=reason,
        )
    )
    audit(
        user,
        "studio.price.updated",
        "variant",
        str(variant.id),
        {"before_cents": before, "after_cents": cents, "reason": reason},
    )
    db.session.commit()
    cache.bump_catalog_version()


def undo_price(user: StudioUser, history: PriceHistory) -> None:
    if history.undone_at is not None:
        raise StudioError("This price change was already undone.")
    variant = db.session.get(Variant, history.variant_id)
    if variant is None:
        raise StudioError("The product option no longer exists.")
    if variant.price_cents != history.after_cents:
        raise StudioError(
            "This price changed again afterward, so it cannot be safely undone automatically."
        )
    current = variant.price_cents
    variant.price_cents = history.before_cents
    history.undone_at = utcnow()
    db.session.add(
        PriceHistory(
            variant_id=variant.id,
            user_id=user.id,
            before_cents=current,
            after_cents=history.before_cents,
            reason=f"Undo change #{history.id}",
        )
    )
    audit(
        user,
        "studio.price.undone",
        "price_history",
        str(history.id),
        {"variant_id": variant.id, "restored_cents": history.before_cents},
    )
    db.session.commit()
    cache.bump_catalog_version()


BULK_PRICE_KINDS = {
    "raise_percent": "Raise by %",
    "lower_percent": "Lower by %",
    "raise_amount": "Raise by $",
    "lower_amount": "Lower by $",
    "set": "Set every price to $",
}
BULK_LIMIT = 500  # options one bulk price/stock change may touch


@dataclass
class BulkPriceRow:
    variant: Variant
    before: int
    after: int
    skip: str = ""  # why this option is left unchanged (shown in the preview)


@dataclass
class BulkPricePlan:
    rows: list[BulkPriceRow]
    reason: str
    summary: str
    fingerprint: str

    @property
    def changes(self) -> list[BulkPriceRow]:
        return [row for row in self.rows if not row.skip]


def _bulk_new_price(before: int, kind: str, amount: Decimal, round_99: bool) -> int:
    if kind in ("raise_percent", "lower_percent"):
        factor = (100 + amount if kind == "raise_percent" else 100 - amount) / 100
        cents = int((Decimal(before) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        step = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        cents = {"raise_amount": before + step, "lower_amount": before - step}.get(kind, step)
    if round_99 and cents > 99:
        cents = (cents // 100) * 100 + 99 if cents % 100 == 99 else (cents // 100) * 100 - 1
    return cents


def _bulk_selection(form: dict[str, str]) -> tuple[list[Variant], str]:
    """Active options picked by category (with subcategories) and/or search, for bulk edits."""
    statement = select(Variant).join(Variant.product).where(Variant.active.is_(True))
    category_id = form.get("category", "")
    query = form.get("q", "").strip()[:100]
    label = []
    if category_id:
        category = db.session.get(Category, int(category_id)) if category_id.isdigit() else None
        if category is None:
            raise StudioError("That category no longer exists.")
        ids = category.subtree_ids()  # with its subcategories at every level
        statement = statement.where(Product.categories.any(Category.id.in_(ids)))
        label.append(f"category “{category.name}”")
    if query:
        like = f"%{query}%"
        statement = statement.where(or_(Product.name.ilike(like), Variant.sku.ilike(like)))
        label.append(f"matching “{query}”")
    if not label:
        raise StudioError("Choose a category or search words so the change can't hit everything.")
    variants = db.session.scalars(
        statement.order_by(Product.name, Variant.position, Variant.id).limit(BULK_LIMIT + 1)
    ).all()
    if not variants:
        raise StudioError("No products match. Nothing would change.")
    if len(variants) > BULK_LIMIT:
        raise StudioError(f"More than {BULK_LIMIT} options match. Narrow the selection.")
    return list(variants), " ".join(label)


def plan_bulk_price(form: dict[str, str]) -> BulkPricePlan:
    """Work out (without saving) what a bulk price change would do to each matching option."""
    kind = form.get("kind", "")
    if kind not in BULK_PRICE_KINDS:
        raise StudioError("Choose how the prices should change.")
    try:
        amount = Decimal(form.get("amount", "").strip().replace("$", "").replace(",", ""))
    except InvalidOperation:
        raise StudioError("Enter a valid amount.") from None
    if kind.endswith("percent") and not Decimal("0.01") <= amount <= Decimal("90"):
        raise StudioError("Percentage must be between 0.01 and 90.")
    if not kind.endswith("percent") and not Decimal("0.01") <= amount <= Decimal("1000000"):
        raise StudioError("Dollar amount must be between $0.01 and $1,000,000.")
    reason = form.get("reason", "").strip()
    if not 2 <= len(reason) <= 120:
        raise StudioError("Add a short reason for the price change.")
    round_99 = form.get("round_99") == "1"

    variants, label = _bulk_selection(form)

    in_sale = set(
        db.session.scalars(
            select(StudioSaleItem.variant_id)
            .join(StudioSaleItem.sale)
            .where(
                StudioSaleItem.variant_id.in_([v.id for v in variants]),
                StudioSale.status.in_([SaleStatus.SCHEDULED, SaleStatus.ACTIVE]),
            )
        ).all()
    )
    rows = []
    for variant in variants:
        before = variant.price_cents
        after = _bulk_new_price(before, kind, amount, round_99)
        skip = ""
        if variant.id in in_sale:
            skip = "In a scheduled or active sale"
        elif not 1 <= after <= 100_000_000:
            skip = "New price would be outside the allowed range"
        elif variant.compare_at_cents is not None and after >= variant.compare_at_cents:
            skip = "Would reach its compare-at (was) price"
        elif after == before:
            skip = "No change"
        rows.append(BulkPriceRow(variant, before, after if not skip else before, skip))
    shown = format(amount.normalize(), "f")  # 10 -> "10", 2.50 -> "2.5"
    how = BULK_PRICE_KINDS[kind].replace("%", f"{shown}%").replace("$", f"${shown}")
    summary = f"{how}{' (ending in .99)' if round_99 else ''}, {label}"
    fingerprint = hashlib.sha256(
        json.dumps(
            [kind, str(amount), round_99, reason, [(r.variant.id, r.before, r.after) for r in rows]]
        ).encode()
    ).hexdigest()
    return BulkPricePlan(rows, reason, summary, fingerprint)


def apply_bulk_price(user: StudioUser, form: dict[str, str]) -> int:
    """Apply a previewed bulk change only if every price is still what the preview showed."""
    plan = plan_bulk_price(form)
    if not hmac.compare_digest(plan.fingerprint, form.get("fingerprint", "")):
        raise StudioError("Prices changed since the preview. Review the new preview first.")
    if not plan.changes:
        raise StudioError("Nothing would change.")
    reason = f"Bulk: {plan.reason}"[:160]
    for row in plan.changes:
        row.variant.price_cents = row.after
        db.session.add(
            PriceHistory(
                variant_id=row.variant.id,
                user_id=user.id,
                before_cents=row.before,
                after_cents=row.after,
                reason=reason,
            )
        )
    audit(
        user,
        "studio.price.bulk_updated",
        "variant_bulk",
        str(len(plan.changes)),
        {
            "summary": plan.summary,
            "reason": plan.reason,
            "variant_ids": [row.variant.id for row in plan.changes],
        },
    )
    db.session.commit()
    cache.bump_catalog_version()
    return len(plan.changes)


BULK_STOCK_KINDS = {
    "set": "Set every option to",
    "add": "Add to every option",
    "remove": "Take away from every option",
}


@dataclass
class BulkStockPlan:
    rows: list[BulkPriceRow]  # before/after are quantities here
    reason: str
    summary: str
    fingerprint: str

    @property
    def changes(self) -> list[BulkPriceRow]:
        return [row for row in self.rows if not row.skip]


def plan_bulk_stock(form: dict[str, str]) -> BulkStockPlan:
    """Work out (without saving) what a bulk stock change would do to each matching option."""
    kind = form.get("kind", "")
    if kind not in BULK_STOCK_KINDS:
        raise StudioError("Choose how the stock should change.")
    try:
        amount = int(form.get("amount", "").strip())
    except ValueError:
        raise StudioError("Stock must be a whole number.") from None
    if not (0 if kind == "set" else 1) <= amount <= 1_000_000:
        raise StudioError("Enter a number between 1 and 1,000,000 (0 is allowed for “set”).")
    reason = form.get("reason", "").strip()
    if not 2 <= len(reason) <= 120:
        raise StudioError("Add a short reason for the stock change.")
    variants, label = _bulk_selection(form)
    rows = []
    for variant in variants:
        before = variant.stock_quantity
        after = {"set": amount, "add": before + amount}.get(kind, before - amount)
        skip = ""
        if not variant.track_inventory:
            skip = "Stock isn't counted for this item"
        elif after < 0:
            skip = "Would go below zero"
        elif after > 1_000_000:
            skip = "Would go above 1,000,000"
        elif after == before:
            skip = "No change"
        rows.append(BulkPriceRow(variant, before, before if skip else after, skip))
    summary = f"{BULK_STOCK_KINDS[kind]} {amount}, {label}"
    fingerprint = hashlib.sha256(
        json.dumps(
            ["stock", kind, amount, reason, [(r.variant.id, r.before, r.after) for r in rows]]
        ).encode()
    ).hexdigest()
    return BulkStockPlan(rows, reason, summary, fingerprint)


def apply_bulk_stock(user: StudioUser, form: dict[str, str]) -> int:
    """Apply a previewed bulk stock change only if every amount is still what the preview showed."""
    plan = plan_bulk_stock(form)
    if not hmac.compare_digest(plan.fingerprint, form.get("fingerprint", "")):
        raise StudioError("Stock changed since the preview. Review the new preview first.")
    if not plan.changes:
        raise StudioError("Nothing would change.")
    reason = f"Bulk: {plan.reason}"[:160]
    for row in plan.changes:
        row.variant.stock_quantity = row.after
        db.session.add(
            InventoryHistory(
                variant_id=row.variant.id,
                user_id=user.id,
                before_quantity=row.before,
                after_quantity=row.after,
                reason=reason,
            )
        )
    audit(
        user,
        "studio.inventory.bulk_updated",
        "variant_bulk",
        str(len(plan.changes)),
        {
            "summary": plan.summary,
            "reason": plan.reason,
            "variant_ids": [row.variant.id for row in plan.changes],
        },
    )
    db.session.commit()
    cache.bump_catalog_version()
    return len(plan.changes)


STEP_UP_TRIES, STEP_UP_WINDOW = 5, 900


def confirm_password(user: StudioUser, password: str) -> None:
    """Risky actions ask for the signed-in person's password again (owner plan, step-up)."""
    if not ratelimit.allow(
        "studio-step-up", str(user.id), STEP_UP_TRIES, STEP_UP_WINDOW, fail_closed=True
    ):
        raise StudioError("Too many wrong passwords. Wait 15 minutes, then try again.")
    try:
        _hasher.verify(user.password_hash, (password or "")[:MAX_PASSWORD])
    except (VerificationError, InvalidHashError):
        audit(user, "studio.step_up.failed", "studio_user", str(user.id))
        db.session.commit()
        raise StudioError(
            "That password is not right. Type the password you use to sign in."
        ) from None


def set_product_visibility(
    user: StudioUser,
    product: Product,
    live: bool,
    reason_raw: str,
    expected_raw: str,
) -> None:
    if expected_raw and expected_raw != product.status.value:
        raise StudioError("Product visibility changed since this page loaded. Review it again.")
    target = ProductStatus.ACTIVE if live else ProductStatus.DRAFT
    if product.status == target:
        raise StudioError("This product already has that visibility.")
    reason = reason_raw.strip()
    if not live and not 2 <= len(reason) <= 160:
        raise StudioError("Add a short reason for hiding this product.")
    if len(reason) > 160:
        raise StudioError("The visibility reason is too long.")
    before = product.status
    product.status = target
    _record_product(user, product, "visibility", {"status": before.value}, reason)
    audit(
        user,
        "studio.product.restored" if live else "studio.product.hidden",
        "product",
        str(product.id),
        {"before": before.value, "after": target.value, "reason": reason},
    )
    db.session.commit()
    cache.bump_catalog_version()


def update_product_details(
    user: StudioUser,
    product: Product,
    name_raw: str,
    summary_raw: str,
    description_raw: str,
    category_ids_raw: list[str],
    sale_unit_raw: str | None = None,
    piece_length_raw: str | None = None,
    width_raw: str | None = None,
) -> None:
    """Length/width left as None keep their current values (forms without those fields)."""
    name = name_raw.strip()
    summary = summary_raw.strip()
    description = description_raw.strip()
    if not 1 <= len(name) <= 200:
        raise StudioError("Product name must be between 1 and 200 characters.")
    if len(summary) > 300:
        raise StudioError("Summary must be 300 characters or fewer.")
    if len(description) > 20_000:
        raise StudioError("Description must be 20,000 characters or fewer.")
    try:
        sale_unit = SaleUnit(sale_unit_raw or product.sale_unit)
    except ValueError:
        raise StudioError("Choose bundle, yard, or metre for the selling unit.") from None
    try:
        piece_length, piece_max = (
            (product.piece_length_yards, product.piece_length_max_yards)
            if piece_length_raw is None
            else units.parse_piece_range(piece_length_raw)
        )
        width = product.width_inches if width_raw is None else units.parse_width(width_raw)
    except ValueError as exc:
        raise StudioError(str(exc)) from None
    try:
        category_ids = {int(value) for value in category_ids_raw}
    except ValueError:
        raise StudioError("Choose valid categories.") from None
    categories = db.session.scalars(select(Category).where(Category.id.in_(category_ids))).all()
    if not categories or {category.id for category in categories} != category_ids:
        raise StudioError("Choose at least one valid category.")
    before = _details_snapshot(product)
    product.name = name
    product.summary = summary
    product.description = description
    product.categories = list(categories)
    product.sale_unit = sale_unit.value
    product.piece_length_yards = piece_length
    product.piece_length_max_yards = piece_max
    product.width_inches = width
    after = _details_snapshot(product)
    if after != before:
        _record_product(user, product, "details", before, "")
    audit(
        user,
        "studio.product.updated",
        "product",
        str(product.id),
        {"before": before, "after": after},
    )
    db.session.commit()
    cache.bump_catalog_version()


def _details_snapshot(product: Product) -> dict[str, Any]:
    """The product fields the Studio details form edits, as plain JSON values."""
    return {
        "name": product.name,
        "summary": product.summary,
        "description": product.description,
        "category_ids": sorted(category.id for category in product.categories),
        "sale_unit": product.sale_unit,
        "piece_length_yards": product.piece_length_text or None,
        "width_inches": product.width_inches,
    }


def _snapshot(product: Product, kind: str) -> dict[str, Any]:
    return {"status": product.status.value} if kind == "visibility" else _details_snapshot(product)


def _record_product(
    user: StudioUser, product: Product, kind: str, before: dict[str, Any], reason: str
) -> None:
    db.session.add(
        ProductHistory(
            product_id=product.id,
            user_id=user.id,
            kind=kind,
            before=json.dumps(before, sort_keys=True),
            after=json.dumps(_snapshot(product, kind), sort_keys=True),
            reason=reason[:160],
        )
    )


def undo_product_change(user: StudioUser, history: ProductHistory) -> None:
    """Put a product back as it was before one Studio change, unless it changed again since."""
    if history.undone_at is not None:
        raise StudioError("This product change was already undone.")
    product = db.session.get(Product, history.product_id)
    if product is None:
        raise StudioError("The product no longer exists.")
    if _snapshot(product, history.kind) != json.loads(history.after):
        raise StudioError(
            "This product changed again afterward, so it cannot be safely undone automatically."
        )
    before = json.loads(history.before)
    current = _snapshot(product, history.kind)
    if history.kind == "visibility":
        product.status = ProductStatus(before["status"])
    else:
        categories = db.session.scalars(
            select(Category).where(Category.id.in_(before["category_ids"]))
        ).all()
        if len(categories) != len(before["category_ids"]):
            raise StudioError("A category this product was in no longer exists.")
        product.name = before["name"]
        product.summary = before["summary"]
        product.description = before["description"]
        product.categories = list(categories)
        product.sale_unit = before["sale_unit"]
        low, high = units.parse_piece_range(before["piece_length_yards"])
        product.piece_length_yards, product.piece_length_max_yards = low, high
        product.width_inches = before["width_inches"]
    history.undone_at = utcnow()
    _record_product(user, product, history.kind, current, f"Undo change #{history.id}")
    audit(
        user,
        "studio.product.undone",
        "product_history",
        str(history.id),
        {"product_id": product.id, "kind": history.kind},
    )
    db.session.commit()
    cache.bump_catalog_version()


BULK_VISIBILITY_KINDS = {"hide": "Hide from the shop", "show": "Put back live"}


@dataclass
class BulkProductRow:
    product: Product
    before: str
    after: str
    skip: str = ""


@dataclass
class BulkVisibilityPlan:
    rows: list[BulkProductRow]
    reason: str
    summary: str
    fingerprint: str

    @property
    def changes(self) -> list[BulkProductRow]:
        return [row for row in self.rows if not row.skip]


def plan_bulk_visibility(form: dict[str, str]) -> BulkVisibilityPlan:
    """Work out (without saving) which products a bulk hide / put-back-live would change."""
    kind = form.get("kind", "")
    if kind not in BULK_VISIBILITY_KINDS:
        raise StudioError("Choose hide or put back live.")
    reason = form.get("reason", "").strip()
    if not 2 <= len(reason) <= 120:
        raise StudioError("Add a short reason.")
    statement = select(Product)
    category_id = form.get("category", "")
    query = form.get("q", "").strip()[:100]
    label = []
    if category_id:
        category = db.session.get(Category, int(category_id)) if category_id.isdigit() else None
        if category is None:
            raise StudioError("That category no longer exists.")
        ids = category.subtree_ids()  # with its subcategories at every level
        statement = statement.where(Product.categories.any(Category.id.in_(ids)))
        label.append(f"category “{category.name}”")
    if query:
        like = f"%{query}%"
        statement = statement.where(
            or_(Product.name.ilike(like), Product.variants.any(Variant.sku.ilike(like)))
        )
        label.append(f"matching “{query}”")
    if not label:
        raise StudioError("Choose a category or search words so the change can't hit everything.")
    products = db.session.scalars(
        statement.order_by(Product.name, Product.id).limit(BULK_LIMIT + 1)
    ).all()
    if not products:
        raise StudioError("No products match. Nothing would change.")
    if len(products) > BULK_LIMIT:
        raise StudioError(f"More than {BULK_LIMIT} products match. Narrow the selection.")
    target = ProductStatus.DRAFT if kind == "hide" else ProductStatus.ACTIVE
    rows = []
    for product in products:
        skip = ""
        if product.status == ProductStatus.ARCHIVED:
            skip = "Removed from the catalog"
        elif product.status == target:
            skip = "Already hidden" if kind == "hide" else "Already live"
        after = product.status.value if skip else target.value
        rows.append(BulkProductRow(product, product.status.value, after, skip))
    summary = f"{BULK_VISIBILITY_KINDS[kind]}: {' '.join(label)}"
    fingerprint = hashlib.sha256(
        json.dumps(
            ["visibility", kind, reason, [(r.product.id, r.before, r.after) for r in rows]]
        ).encode()
    ).hexdigest()
    return BulkVisibilityPlan(rows, reason, summary, fingerprint)


def apply_bulk_visibility(user: StudioUser, form: dict[str, str]) -> int:
    """Apply a previewed bulk hide / put-back-live only if nothing changed since the preview."""
    plan = plan_bulk_visibility(form)
    if not hmac.compare_digest(plan.fingerprint, form.get("fingerprint", "")):
        raise StudioError("Products changed since the preview. Review the new preview first.")
    if not plan.changes:
        raise StudioError("Nothing would change.")
    reason = f"Bulk: {plan.reason}"
    for row in plan.changes:
        row.product.status = ProductStatus(row.after)
        _record_product(user, row.product, "visibility", {"status": row.before}, reason)
    audit(
        user,
        "studio.product.bulk_visibility",
        "product_bulk",
        str(len(plan.changes)),
        {
            "summary": plan.summary,
            "reason": plan.reason,
            "product_ids": [row.product.id for row in plan.changes],
        },
    )
    db.session.commit()
    cache.bump_catalog_version()
    return len(plan.changes)


def upload_product_image(
    user: StudioUser, product: Product, upload: FileStorage | None, alt: str
) -> ProductImage:
    try:
        record, destination = image_service.prepare_upload(product, upload, alt)
    except image_service.ImageError as exc:
        raise StudioError(str(exc)) from None
    audit(
        user,
        "studio.product.image_uploaded",
        "product",
        str(product.id),
        {"path": record.path},
    )
    try:
        db.session.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    cache.bump_catalog_version()
    return record


def delete_product_image(user: StudioUser, image: ProductImage) -> int:
    try:
        image_service.ensure_deletable(image)
    except image_service.ImageError as exc:
        raise StudioError(str(exc)) from None
    product_id = image.product_id
    path = image.path
    db.session.delete(image)
    audit(
        user,
        "studio.product.image_removed",
        "product",
        str(product_id),
        {"path": path},
    )
    db.session.commit()
    target = image_service.disk_path(path)
    if target:
        target.unlink(missing_ok=True)
    cache.bump_catalog_version()
    return product_id


def draft_sale(user: StudioUser, form: dict[str, str]) -> StudioSale:
    name = form.get("name", "").strip()
    if not 2 <= len(name) <= 120:
        raise StudioError("Sale name must be between 2 and 120 characters.")
    try:
        kind = SaleDiscountKind(form.get("discount_kind", ""))
    except ValueError:
        raise StudioError("Choose a valid discount type.") from None
    starts = _local_datetime(form.get("starts_at", ""), "start")
    ends = _local_datetime(form.get("ends_at", ""), "end")
    if ends <= starts:
        raise StudioError("Sale end must be after its start.")
    if ends <= utcnow():
        raise StudioError("Sale end must be in the future.")
    amount = _sale_amount(form.get("amount", ""), kind)
    try:
        scope_type, scope_id_raw = form.get("scope", "").split(":", 1)
        scope_id = int(scope_id_raw)
    except (ValueError, TypeError):
        raise StudioError("Choose what the sale applies to.") from None
    statement = (
        select(Variant)
        .join(Variant.product)
        .where(Variant.active.is_(True), Product.status == ProductStatus.ACTIVE)
    )
    if scope_type == "product":
        statement = statement.where(Product.id == scope_id)
    elif scope_type == "category":
        statement = statement.join(Product.categories).where(Category.id == scope_id)
    elif scope_type == "collection":
        statement = statement.join(Product.collections).where(Collection.id == scope_id)
    else:
        raise StudioError("Choose a product, category, or collection for this sale.")
    variants = db.session.scalars(statement.order_by(Product.name, Variant.position)).unique().all()
    if not variants:
        raise StudioError("No live product options match that sale scope.")
    if any(variant.compare_at_cents is not None for variant in variants):
        raise StudioError("One or more selected prices are already on sale.")
    sale = StudioSale(
        name=name,
        discount_kind=kind,
        amount=amount,
        status=SaleStatus.DRAFT,
        starts_at=starts,
        ends_at=ends,
        created_by_id=user.id,
    )
    round_99 = form.get("round_99") == "1"
    for variant in variants:
        sale_price = _discounted_price(variant.price_cents, kind, amount, round_99)
        if sale_price >= variant.price_cents:
            raise StudioError(f"The discount does not lower the price of {variant.sku}.")
        sale.items.append(
            StudioSaleItem(
                variant_id=variant.id,
                original_price_cents=variant.price_cents,
                sale_price_cents=sale_price,
            )
        )
    db.session.add(sale)
    db.session.flush()
    audit(
        user,
        "studio.sale.previewed",
        "sale",
        str(sale.id),
        {"name": sale.name, "items": len(sale.items)},
    )
    db.session.commit()
    return sale


def confirm_sale(user: StudioUser, sale: StudioSale) -> None:
    if sale.status != SaleStatus.DRAFT:
        raise StudioError("This sale was already confirmed or cancelled.")
    variant_ids = [item.variant_id for item in sale.items]
    overlap = db.session.scalar(
        select(StudioSaleItem.id)
        .join(StudioSaleItem.sale)
        .where(
            StudioSaleItem.variant_id.in_(variant_ids),
            StudioSale.status.in_([SaleStatus.SCHEDULED, SaleStatus.ACTIVE]),
            StudioSale.starts_at < sale.ends_at,
            StudioSale.ends_at > sale.starts_at,
        )
    )
    if overlap is not None:
        raise StudioError("Another scheduled sale overlaps one or more of these product options.")
    for item in sale.items:
        if item.variant.price_cents != item.original_price_cents:
            raise StudioError(
                f"{item.variant.sku} changed after the preview. Create a fresh preview."
            )
    sale.status = SaleStatus.SCHEDULED
    sale.confirmed_at = utcnow()
    audit(user, "studio.sale.confirmed", "sale", str(sale.id), {"items": len(sale.items)})
    db.session.commit()
    sync_sales()


def cancel_sale(user: StudioUser, sale: StudioSale) -> None:
    if sale.status in {SaleStatus.ENDED, SaleStatus.CANCELLED}:
        raise StudioError("This sale has already ended or been cancelled.")
    if sale.status == SaleStatus.ACTIVE:
        _restore_sale(sale)
    sale.status = SaleStatus.CANCELLED
    sale.ended_at = utcnow()
    audit(user, "studio.sale.cancelled", "sale", str(sale.id))
    db.session.commit()
    cache.bump_catalog_version()


def sync_sales(now: datetime | None = None) -> tuple[int, int]:
    now = _aware(now or utcnow())
    activated = ended = 0
    scheduled = db.session.scalars(
        select(StudioSale).where(
            StudioSale.status == SaleStatus.SCHEDULED, StudioSale.starts_at <= now
        )
    ).all()
    for sale in scheduled:
        if _aware(sale.ends_at) <= now:
            sale.status = SaleStatus.ENDED
            sale.ended_at = now
            ended += 1
            continue
        conflicts = False
        for item in sale.items:
            variant = item.variant
            if variant.price_cents != item.original_price_cents or variant.compare_at_cents:
                item.conflict_reason = "Price changed after sale confirmation"
                conflicts = True
                continue
            variant.compare_at_cents = item.original_price_cents
            variant.price_cents = item.sale_price_cents
            item.applied_at = now
        if conflicts:
            _restore_sale(sale)
            sale.status = SaleStatus.CANCELLED
            sale.ended_at = now
            audit_system("studio.sale.activation_conflict", "sale", str(sale.id))
        else:
            sale.status = SaleStatus.ACTIVE
            activated += 1
            audit_system("studio.sale.activated", "sale", str(sale.id))
    active = db.session.scalars(
        select(StudioSale).where(StudioSale.status == SaleStatus.ACTIVE, StudioSale.ends_at <= now)
    ).all()
    for sale in active:
        _restore_sale(sale)
        sale.status = SaleStatus.ENDED
        sale.ended_at = now
        ended += 1
        audit_system("studio.sale.ended", "sale", str(sale.id))
    db.session.commit()
    if activated or ended:
        cache.bump_catalog_version()
    return activated, ended


def _restore_sale(sale: StudioSale) -> None:
    now = utcnow()
    for item in sale.items:
        if item.applied_at is None or item.restored_at is not None:
            continue
        variant = item.variant
        if (
            variant.price_cents == item.sale_price_cents
            and variant.compare_at_cents == item.original_price_cents
        ):
            variant.price_cents = item.original_price_cents
            variant.compare_at_cents = None
            item.restored_at = now
        else:
            item.conflict_reason = "Price changed while sale was active; not overwritten"


def _local_datetime(raw: str, label: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise StudioError(f"Enter a valid sale {label} date and time.") from None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("America/New_York"))
    return value.astimezone(UTC)


def _sale_amount(raw: str, kind: SaleDiscountKind) -> int:
    try:
        value = Decimal(raw.strip().replace("$", "").replace(",", ""))
    except InvalidOperation:
        raise StudioError("Enter a valid discount amount.") from None
    if kind == SaleDiscountKind.PERCENT:
        if not Decimal("0.01") <= value <= Decimal("99.99"):
            raise StudioError("Percentage discount must be between 0.01 and 99.99.")
        return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not 1 <= cents <= 100_000_000:
        raise StudioError("Discount amount is outside the allowed range.")
    return cents


def _discounted_price(original: int, kind: SaleDiscountKind, amount: int, round_99: bool) -> int:
    if kind == SaleDiscountKind.PERCENT:
        result = (Decimal(original) * (Decimal(10_000 - amount) / Decimal(10_000))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        cents = int(result)
    elif kind == SaleDiscountKind.FIXED:
        cents = original - amount
    else:
        cents = amount
    cents = max(1, cents)
    if round_99 and cents > 99:
        rounded = (cents // 100) * 100 + 99
        if rounded > cents:
            rounded -= 100
        cents = max(99, rounded)
    return cents


def save_blog_post(user: StudioUser, post: BlogPost | None, form: dict[str, str]) -> BlogPost:
    creating = post is None
    title = form.get("title", "").strip()
    slug = form.get("slug", "").strip().lower()
    category = form.get("category", "").strip()
    excerpt = form.get("excerpt", "").strip()
    cover_alt = form.get("cover_alt", "").strip()
    if not 2 <= len(title) <= 220:
        raise StudioError("Title must be between 2 and 220 characters.")
    if not blog_service.SLUG_RE.fullmatch(slug):
        raise StudioError("URL name must use lowercase words separated by dashes.")
    if not creating and post and post.slug != slug:
        raise StudioError("An existing article's URL name cannot be changed.")
    duplicate = db.session.scalar(
        select(BlogPost.id).where(BlogPost.slug == slug, BlogPost.id != (post.id if post else 0))
    )
    if duplicate:
        raise StudioError("That article URL name is already in use.")
    if not 1 <= len(category) <= 100:
        raise StudioError("Category must be between 1 and 100 characters.")
    if not 10 <= len(excerpt) <= 400:
        raise StudioError("Summary must be between 10 and 400 characters.")
    if not 3 <= len(cover_alt) <= 255:
        raise StudioError("Cover description must be between 3 and 255 characters.")
    try:
        cover_path = blog_service.validate_cover(form.get("cover_path", ""))
        blocks = blog_service.parse_body(form.get("body", ""))
    except blog_service.BlogError as exc:
        raise StudioError(str(exc)) from None
    if post is None:
        post = BlogPost(slug=slug, created_by_id=user.id)
        db.session.add(post)
    else:
        blog_service.record_revision(post, user.id, "Before edit")
    post.title = title
    post.category = category
    post.excerpt = excerpt
    post.cover_path = cover_path
    post.cover_alt = cover_alt
    post.body_json = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))
    post.seo_title = (form.get("seo_title", "").strip() or title)[:220]
    post.seo_description = (form.get("seo_description", "").strip() or excerpt)[:400]
    db.session.flush()
    audit(
        user,
        "studio.blog.created" if creating else "studio.blog.updated",
        "blog_post",
        str(post.id),
        {"slug": post.slug, "title": post.title},
    )
    db.session.commit()
    return post


def set_blog_publication(
    user: StudioUser, post: BlogPost, action: str, publish_at_raw: str = ""
) -> None:
    blog_service.record_revision(post, user.id, f"Before {action}")
    now = utcnow()
    if action == "publish":
        post.status = BlogStatus.PUBLISHED
        post.publish_at = now
        post.published_at = now
    elif action == "schedule":
        publish_at = _local_datetime(publish_at_raw, "publication")
        if publish_at <= now:
            raise StudioError("Scheduled publication must be in the future.")
        post.status = BlogStatus.SCHEDULED
        post.publish_at = publish_at
        post.published_at = None
    elif action == "unpublish":
        post.status = BlogStatus.DRAFT
        post.publish_at = None
    else:
        raise StudioError("Choose a valid publication action.")
    audit(
        user,
        f"studio.blog.{action}",
        "blog_post",
        str(post.id),
        {"status": post.status.value},
    )
    db.session.commit()


def undo_blog_revision(user: StudioUser, revision: BlogRevision) -> BlogPost:
    post = revision.post
    snapshot = json.loads(revision.snapshot_json)
    blog_service.record_revision(post, user.id, f"Before restoring revision {revision.id}")
    for field in (
        "title",
        "category",
        "excerpt",
        "cover_path",
        "cover_alt",
        "body_json",
        "seo_title",
        "seo_description",
    ):
        setattr(post, field, snapshot[field])
    post.status = BlogStatus(snapshot["status"])
    for field in ("publish_at", "published_at"):
        raw = snapshot.get(field)
        setattr(post, field, datetime.fromisoformat(raw) if raw else None)
    audit(
        user,
        "studio.blog.revision_restored",
        "blog_revision",
        str(revision.id),
        {"post_id": post.id},
    )
    db.session.commit()
    return post


def reply_to_post(user: StudioUser, post: ProductPost, body_raw: str) -> ProductPostReply:
    body = body_raw.strip()
    if not 2 <= len(body) <= 2000:
        raise StudioError("Reply must be between 2 and 2,000 characters.")
    if post.status == PostStatus.REMOVED:
        raise StudioError("Restore the removed post before replying.")
    reply = ProductPostReply(post_id=post.id, studio_user_id=user.id, body=body)
    db.session.add(reply)
    audit(user, "studio.community.replied", "product_post", str(post.id))
    db.session.commit()
    customer = db.session.get(User, post.user_id) if post.user_id else None
    if customer:
        mail.send(
            customer.email,
            f"GG Afrikan Eleganz replied to your {post.kind.value}",
            f"We replied to your {post.kind.value} on {post.product.name}.\n\nOur reply:\n{body}\n",
        )
    return reply


def moderate_post(user: StudioUser, post: ProductPost, action: str) -> None:
    before = post.status
    if action == "hide":
        post.status = PostStatus.HIDDEN
    elif action == "restore":
        post.status = PostStatus.VISIBLE if post.relevance_score >= 20 else PostStatus.COLLAPSED
    elif action == "relevant":
        post.staff_relevance_override = True
        post.status = PostStatus.VISIBLE
    elif action == "not_relevant":
        post.staff_relevance_override = False
        post.status = PostStatus.COLLAPSED
    elif action == "restore_removed":
        removal = post.removal
        if post.status != PostStatus.REMOVED or removal is None:
            raise StudioError("The removed text is no longer available to restore.")
        post.title = removal.original_title
        post.body = removal.original_body
        post.status = PostStatus.VISIBLE if post.relevance_score >= 20 else PostStatus.COLLAPSED
        db.session.delete(removal)
    else:
        raise StudioError("Choose a valid moderation action.")
    audit(
        user,
        f"studio.community.{action}",
        "product_post",
        str(post.id),
        {"before": before.value, "after": post.status.value},
    )
    db.session.commit()


def add_moderation_word(
    user: StudioUser, language_raw: str, word_raw: str, word_type: str
) -> ModerationWord:
    language = language_raw.strip().lower()
    if language not in {"en", "fr", "es", "pt", "sw", "yo", "ig", "ha", "tw"}:
        raise StudioError("Choose a supported language.")
    from app.reviews.service import normalize

    tokens = normalize(word_raw)
    if len(tokens) != 1 or not 2 <= len(tokens[0]) <= 100:
        raise StudioError("Add one word between 2 and 100 characters.")
    is_topic = word_type == "topic"
    if word_type not in {"topic", "blocked"}:
        raise StudioError("Choose on-topic or blocked.")
    duplicate = db.session.scalar(
        select(ModerationWord.id).where(
            ModerationWord.language == language,
            ModerationWord.word == tokens[0],
            ModerationWord.is_topic == is_topic,
        )
    )
    if duplicate:
        raise StudioError("That moderation word already exists.")
    record = ModerationWord(
        language=language, word=tokens[0], is_topic=is_topic, created_by_id=user.id
    )
    db.session.add(record)
    audit(
        user,
        "studio.moderation.word_added",
        "moderation_word",
        None,
        {"language": language, "word": tokens[0], "type": word_type},
    )
    db.session.commit()
    return record


def remove_moderation_word(user: StudioUser, word: ModerationWord) -> None:
    detail = {"language": word.language, "word": word.word, "is_topic": word.is_topic}
    db.session.delete(word)
    audit(user, "studio.moderation.word_removed", "moderation_word", str(word.id), detail)
    db.session.commit()


def send_daily_summary(now: datetime | None = None) -> bool:
    """Send a low-detail operational digest through the configured mail adapter."""
    now = now or utcnow()
    cutoff = now - timedelta(days=1)
    recipient = str(current_app.config.get("ORDER_NOTIFICATION_EMAIL") or "").strip()
    if not recipient:
        raise StudioError("Set ORDER_NOTIFICATION_EMAIL before scheduling the Studio summary.")
    if not mail.available():
        raise StudioError("Configure MAIL_BACKEND before scheduling the Studio summary.")
    activity = int(
        db.session.scalar(
            select(func.count(StudioAudit.id)).where(StudioAudit.created_at >= cutoff)
        )
        or 0
    )
    low_stock = int(
        db.session.scalar(
            select(func.count(Variant.id)).where(
                Variant.active.is_(True),
                Variant.track_inventory.is_(True),
                Variant.stock_quantity <= 5,
            )
        )
        or 0
    )
    community = int(
        db.session.scalar(
            select(func.count(ProductPost.id)).where(
                or_(
                    ProductPost.status.in_([PostStatus.COLLAPSED, PostStatus.REMOVED]),
                    ~ProductPost.replies.any(),
                )
            )
        )
        or 0
    )
    sales = int(
        db.session.scalar(
            select(func.count(StudioSale.id)).where(
                StudioSale.status.in_([SaleStatus.SCHEDULED, SaleStatus.ACTIVE])
            )
        )
        or 0
    )
    body = (
        f"Staff Studio summary for {now.date().isoformat()}\n\n"
        f"Activity in the last 24 hours: {activity}\n"
        f"Low-stock or sold-out options: {low_stock}\n"
        f"Reviews/questions needing attention: {community}\n"
        f"Scheduled or active sales: {sales}\n\n"
        f"{_job_lines()}"
        "Open the private Staff Studio address to review details."
    )
    return mail.send(recipient, "Daily Staff Studio summary", body)


def _job_lines() -> str:
    """Daily-summary warning for scheduled jobs or the backup that failed or stopped running."""
    from app import jobs

    trouble = jobs.problems()
    if not trouble:
        return "All scheduled jobs and the nightly backup ran normally.\n\n"
    lines = "".join(f"- {state.name}: {state.problem} ({state.detail})\n" for state in trouble)
    return (
        "Scheduled jobs needing attention (run `flask --app wsgi jobs check` on the server):\n"
        f"{lines}\n"
    )


def audit(
    user: StudioUser,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    ip = agent = None
    if has_request_context():
        ip = (ratelimit.client_key() or "")[:45] or None
        agent = (request.user_agent.string or "")[:300] or None
    db.session.add(
        StudioAudit(
            user_id=user.id,
            actor_username=user.username,
            action=action[:80],
            entity_type=entity_type[:80] if entity_type else None,
            entity_id=entity_id[:120] if entity_id else None,
            detail=json.dumps(detail or {}, sort_keys=True, separators=(",", ":")),
            ip_address=ip,
            user_agent=agent,
        )
    )


def audit_system(
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    db.session.add(
        StudioAudit(
            user_id=None,
            actor_username="system",
            action=action[:80],
            entity_type=entity_type[:80] if entity_type else None,
            entity_id=entity_id[:120] if entity_id else None,
            detail=json.dumps(detail or {}, sort_keys=True, separators=(",", ":")),
        )
    )

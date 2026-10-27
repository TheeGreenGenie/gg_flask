"""Private Staff Studio identities, approvals, sessions, and change history."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import Variant, utcnow
from app.extensions import db


class StudioRole(enum.StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    INVENTORY = "inventory"
    SUPPORT = "support"
    # Scoped staff roles (owner, 2026-10-27); their pages are listed in studio/permissions.py.
    BRANDING = "branding"
    MAINTENANCE = "maintenance"
    SALES = "sales"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class ChangeStatus(enum.StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    CONFLICT = "conflict"  # the item changed after the request; nothing was overwritten
    FAILED = "failed"  # the action itself refused when replayed (validation)


class KeyEnrollmentStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REGISTERED = "registered"
    EXPIRED = "expired"


class SaleStatus(enum.StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"


class SaleDiscountKind(enum.StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"
    SET_PRICE = "set_price"


class StudioUser(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Comma-separated controlled values permit mixed roles without a join table.
    roles: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Owner, 2026-10-27: these staff's changes wait for the owner's emailed approval.
    requires_review: Mapped[bool] = mapped_column(default=False, server_default=sa_false())
    # May approve their own sign-ins with a phone passkey the owner confirmed (co-owner).
    may_self_approve: Mapped[bool] = mapped_column(default=False, server_default=sa_false())
    # Every sign-in also needs a 6-digit code texted to this number (owner, 2026-10-27: tgg).
    requires_sms: Mapped[bool] = mapped_column(default=False, server_default=sa_false())
    sms_phone: Mapped[str | None] = mapped_column(String(20))  # E.164, set on the server only

    approvals: Mapped[list[StudioLoginRequest]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role_set(self) -> set[StudioRole]:
        return {StudioRole(value) for value in self.roles.split(",") if value}


class StudioLoginRequest(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_login_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("studio_users.id", ondelete="CASCADE"))
    approval_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    match_code: Mapped[str] = mapped_column(String(2))
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            name="studio_approval_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=ApprovalStatus.PENDING,
        index=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Text-message step (only for users with requires_sms); the code is stored hashed.
    sms_code_hash: Mapped[str | None] = mapped_column(String(64))
    sms_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sms_sends: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sms_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sms_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[StudioUser] = relationship(back_populates="approvals")


class StudioSession(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("studio_users.id", ondelete="CASCADE"))
    session_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[StudioUser] = relationship()


class StudioAudit(db.Model):  # type: ignore[name-defined]
    """Append-only Studio activity record."""

    __tablename__ = "studio_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("studio_users.id", ondelete="SET NULL"), index=True
    )
    actor_username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class InventoryHistory(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_inventory_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("studio_users.id", ondelete="SET NULL"))
    before_quantity: Mapped[int] = mapped_column(Integer)
    after_quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(160))
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PriceHistory(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("studio_users.id", ondelete="SET NULL"))
    before_cents: Mapped[int] = mapped_column(Integer)
    after_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(160))
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class StudioSale(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    discount_kind: Mapped[SaleDiscountKind] = mapped_column(
        Enum(
            SaleDiscountKind,
            name="studio_sale_discount_kind",
            values_callable=lambda values: [value.value for value in values],
        )
    )
    # Percent is stored in basis points (1250 = 12.50%); money modes store cents.
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[SaleStatus] = mapped_column(
        Enum(
            SaleStatus,
            name="studio_sale_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=SaleStatus.DRAFT,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("studio_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[StudioSaleItem]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )


class StudioSaleItem(db.Model):  # type: ignore[name-defined]
    __tablename__ = "studio_sale_items"
    __table_args__ = (UniqueConstraint("sale_id", "variant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(
        ForeignKey("studio_sales.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="RESTRICT"), index=True
    )
    original_price_cents: Mapped[int] = mapped_column(Integer)
    sale_price_cents: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict_reason: Mapped[str | None] = mapped_column(String(200))

    sale: Mapped[StudioSale] = relationship(back_populates="items")
    variant: Mapped[Variant] = relationship()


class StudioChange(db.Model):  # type: ignore[name-defined]
    """A change by a reviewed staff member, held until the owner approves it.

    The submitted form (and any uploaded file, kept privately) is replayed through the same
    Studio action when approved, so every check, history entry and undo still applies.
    """

    __tablename__ = "studio_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("studio_users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(String(80))
    view_args: Mapped[str] = mapped_column(Text, default="{}")
    form_data: Mapped[str] = mapped_column(Text, default="[]")
    upload: Mapped[str | None] = mapped_column(Text)  # JSON: field, stored name, filename, type
    summary: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ChangeStatus] = mapped_column(
        Enum(
            ChangeStatus,
            name="studio_change_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=ChangeStatus.PENDING,
        index=True,
    )
    result: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[StudioUser] = relationship()


class StudioPasskey(db.Model):  # type: ignore[name-defined]
    """A WebAuthn credential (public key only) that lets its owner approve their own sign-in."""

    __tablename__ = "studio_passkeys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("studio_users.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[str] = mapped_column(String(512), unique=True)  # base64url
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[StudioUser] = relationship()


class StudioKeyEnrollment(db.Model):  # type: ignore[name-defined]
    """Request to add a passkey; the owner must confirm it (email) before it can be registered."""

    __tablename__ = "studio_key_enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("studio_users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[KeyEnrollmentStatus] = mapped_column(
        Enum(
            KeyEnrollmentStatus,
            name="studio_key_enrollment_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=KeyEnrollmentStatus.PENDING,
        index=True,
    )
    device: Mapped[str] = mapped_column(String(300), default="")
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[StudioUser] = relationship()

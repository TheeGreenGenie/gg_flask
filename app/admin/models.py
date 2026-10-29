"""Administrator identities, recovery codes, and immutable security audit events."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import utcnow
from app.extensions import db

if TYPE_CHECKING:
    from app.accounts.models import User


class AdminRole(enum.StrEnum):
    OWNER = "owner"
    CATALOG = "catalog"
    SUPPORT = "support"


class AdminUser(db.Model):  # type: ignore[name-defined]
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[AdminRole] = mapped_column(
        Enum(AdminRole, name="admin_role", values_callable=lambda e: [m.value for m in e])
    )
    totp_secret: Mapped[str] = mapped_column(String(64))
    # Signs in with the owner's approval (ntfy push, like Studio staff) instead of an
    # authenticator app. Such an account can never approve sign-ins itself.
    uses_owner_approval: Mapped[bool] = mapped_column(default=False, server_default="false")
    active: Mapped[bool] = mapped_column(default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recovery_codes: Mapped[list[AdminRecoveryCode]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )
    customer_user: Mapped[User | None] = relationship("User")


class AdminLoginRequest(db.Model):  # type: ignore[name-defined]
    """One sign-in waiting for the owner's approval (accounts with uses_owner_approval)."""

    __tablename__ = "admin_login_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"))
    approval_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    match_code: Mapped[str] = mapped_column(String(2))
    # pending -> approved | denied | expired; approved -> consumed (one session, once)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL")
    )

    admin: Mapped[AdminUser] = relationship(foreign_keys=[admin_id])


class AdminRecoveryCode(db.Model):  # type: ignore[name-defined]
    __tablename__ = "admin_recovery_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    admin: Mapped[AdminUser] = relationship(back_populates="recovery_codes")


class AdminAudit(db.Model):  # type: ignore[name-defined]
    """Append-only record. Application code never updates or deletes these rows."""

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), index=True
    )
    actor_email: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

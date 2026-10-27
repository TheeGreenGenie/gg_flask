"""Customer accounts (A-001, A-002, A-006).

Fresh accounts only — nothing is imported from WordPress. Passwords are Argon2id hashes.
Marketing consent is an append-only log: every opt-in/opt-out is a new row recording the
exact wording shown, so there is evidence of what each person agreed to and when.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.models import utcnow
from app.extensions import db


class Channel(enum.StrEnum):
    EMAIL = "email"
    SMS = "sms"


class User(db.Model):  # type: ignore[name-defined]
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-cased
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(80), default="")
    phone: Mapped[str | None] = mapped_column(String(20))  # E.164, e.g. +12155550123
    # Bumped on password change/reset: every existing login session stops working.
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    consents: Mapped[list[MarketingConsent]] = relationship(
        back_populates="user",
        order_by="MarketingConsent.id",
        cascade="all, delete-orphan",
    )
    addresses: Mapped[list[Address]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="Address.id"
    )
    payment_methods: Mapped[list[StoredPaymentMethod]] = relationship(
        back_populates="user", cascade="all, delete-orphan", order_by="StoredPaymentMethod.id"
    )

    def consented(self, channel: Channel) -> bool:
        latest = next((c for c in reversed(self.consents) if c.channel == channel), None)
        return bool(latest and latest.granted)


class MarketingConsent(db.Model):  # type: ignore[name-defined]
    __tablename__ = "marketing_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, name="consent_channel", values_callable=lambda e: [m.value for m in e])
    )
    granted: Mapped[bool] = mapped_column()
    wording: Mapped[str] = mapped_column(Text)  # exact text shown next to the checkbox
    source: Mapped[str] = mapped_column(String(40))  # e.g. "register", "account"
    # Destination at the time (the address/number the consent covers).
    destination: Mapped[str] = mapped_column(String(254))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="consents")


class PasswordReset(db.Model):  # type: ignore[name-defined]
    """Single-use reset link. Only the token's SHA-256 hash is stored."""

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()


class Address(db.Model):  # type: ignore[name-defined]
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40), default="Home")
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    address1: Mapped[str] = mapped_column(String(160))
    address2: Mapped[str] = mapped_column(String(160), default="")
    city: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(100))
    postcode: Mapped[str] = mapped_column(String(24))
    country: Mapped[str] = mapped_column(String(2))
    phone: Mapped[str | None] = mapped_column(String(30))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="addresses")


class StoredPaymentMethod(db.Model):  # type: ignore[name-defined]
    """Provider token and display metadata only; never card/account credentials."""

    __tablename__ = "stored_payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_customer_ref: Mapped[str] = mapped_column(String(160))
    provider_method_ref: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    brand: Mapped[str] = mapped_column(String(40), default="")
    last4: Mapped[str] = mapped_column(String(4), default="")
    expiry_month: Mapped[int | None] = mapped_column(Integer)
    expiry_year: Mapped[int | None] = mapped_column(Integer)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="payment_methods")

    __table_args__ = (UniqueConstraint("user_id", "provider", "provider_method_ref"),)

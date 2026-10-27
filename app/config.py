"""Environment-based configuration.

Every setting comes from environment variables. Production refuses to start
when a required value is missing (fail closed) instead of falling back to an
insecure default.
"""

from __future__ import annotations

import os
from datetime import timedelta


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value not in ("", None) else None


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw is not None else default


def _env_optional_int(name: str) -> int | None:
    raw = _env(name)
    return int(raw) if raw is not None else None


def _env_csv(name: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in (_env(name) or "").split(",") if part.strip())


class BaseConfig:
    APP_ENV = "base"
    TESTING = False
    DEBUG = False

    SECRET_KEY: str | None = None
    SQLALCHEMY_DATABASE_URI: str | None = None
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    REDIS_URL: str | None = None

    # Number of reverse proxies in front of the app that we trust to set
    # X-Forwarded-* headers. 0 means trust none (direct exposure / local dev).
    TRUSTED_PROXY_HOPS = 0

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    # Used only when a customer ticks "Keep me signed in"; otherwise the login cookie
    # ends with the browser session.
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    MAIL_BACKEND: str | None = None  # see app/mail.py
    SUPPORTED_COUNTRIES: tuple[str, ...] = ()
    FLAT_SHIPPING_CENTS: int | None = None
    # Lower rate for Philadelphia delivery addresses (owner, 2026-10-26). Unset = flat rate.
    LOCAL_SHIPPING_CENTS: int | None = None
    TAX_BACKEND: str | None = None  # "stripe" (Stripe Tax) or "none" for explicit no-tax
    # Shipping: "flat" or "shippo" (live USPS rates; flat rate is the fallback).
    SHIPPING_BACKEND = "flat"
    SHIPPO_API_TOKEN: str | None = None
    SHIPPO_CARRIERS: tuple[str, ...] = ("USPS",)
    SHIPPO_PARCEL_INCHES: tuple[int, int, int] = (12, 10, 4)
    SHIPPING_HANDLING_CENTS = 0
    SHIP_FROM_NAME = "GG Afrikan Eleganz"
    SHIP_FROM_STREET: str | None = None
    SHIP_FROM_CITY: str | None = None
    SHIP_FROM_STATE: str | None = None
    SHIP_FROM_ZIP: str | None = None
    SHIP_FROM_PHONE: str | None = None
    SHIP_FROM_EMAIL: str | None = None
    GA4_MEASUREMENT_ID: str | None = None  # e.g. G-XXXXXXXXXX
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    PAYPAL_CLIENT_ID: str | None = None
    PAYPAL_CLIENT_SECRET: str | None = None
    PAYPAL_WEBHOOK_ID: str | None = None
    PAYPAL_API_BASE = "https://api-m.sandbox.paypal.com"
    ORDER_NOTIFICATION_EMAIL: str | None = None
    MAILCHIMP_API_KEY: str | None = None
    MAILCHIMP_SERVER_PREFIX: str | None = None
    MAILCHIMP_LIST_ID: str | None = None
    APPLE_PAY_DOMAIN_ASSOCIATION: str | None = None

    WTF_CSRF_TIME_LIMIT = 3600
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # request bodies; uploads get their own path later

    ENABLE_HSTS = False

    def __init__(self) -> None:
        self.SECRET_KEY = _env("SECRET_KEY")
        self.SQLALCHEMY_DATABASE_URI = _env("DATABASE_URL")
        self.REDIS_URL = _env("REDIS_URL")
        self.TRUSTED_PROXY_HOPS = _env_int("TRUSTED_PROXY_HOPS", self.TRUSTED_PROXY_HOPS)
        self.SUPPORTED_COUNTRIES = _env_csv("SUPPORTED_COUNTRIES")
        self.FLAT_SHIPPING_CENTS = _env_optional_int("FLAT_SHIPPING_CENTS")
        self.LOCAL_SHIPPING_CENTS = _env_optional_int("LOCAL_SHIPPING_CENTS")
        self.TAX_BACKEND = _env("TAX_BACKEND")
        self.SHIPPING_BACKEND = _env("SHIPPING_BACKEND", self.SHIPPING_BACKEND) or "flat"
        self.SHIPPO_API_TOKEN = _env("SHIPPO_API_TOKEN")
        self.SHIPPO_CARRIERS = _env_csv("SHIPPO_CARRIERS") or self.SHIPPO_CARRIERS
        self.SHIPPING_HANDLING_CENTS = _env_optional_int("SHIPPING_HANDLING_CENTS") or 0
        for name in ("STREET", "CITY", "STATE", "ZIP", "PHONE", "EMAIL"):
            setattr(self, f"SHIP_FROM_{name}", _env(f"SHIP_FROM_{name}"))
        self.SHIP_FROM_NAME = _env("SHIP_FROM_NAME", self.SHIP_FROM_NAME) or self.SHIP_FROM_NAME
        self.GA4_MEASUREMENT_ID = _env("GA4_MEASUREMENT_ID")
        self.STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")
        self.STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")
        self.PAYPAL_CLIENT_ID = _env("PAYPAL_CLIENT_ID")
        self.PAYPAL_CLIENT_SECRET = _env("PAYPAL_CLIENT_SECRET")
        self.PAYPAL_WEBHOOK_ID = _env("PAYPAL_WEBHOOK_ID")
        self.PAYPAL_API_BASE = _env("PAYPAL_API_BASE", self.PAYPAL_API_BASE) or self.PAYPAL_API_BASE
        self.ORDER_NOTIFICATION_EMAIL = _env("ORDER_NOTIFICATION_EMAIL")
        self.MAILCHIMP_API_KEY = _env("MAILCHIMP_API_KEY")
        self.MAILCHIMP_SERVER_PREFIX = _env("MAILCHIMP_SERVER_PREFIX")
        self.MAILCHIMP_LIST_ID = _env("MAILCHIMP_LIST_ID")
        self.APPLE_PAY_DOMAIN_ASSOCIATION = _env("APPLE_PAY_DOMAIN_ASSOCIATION")
        media_root = _env("MEDIA_ROOT")
        if media_root:  # otherwise the app uses <instance>/media
            self.MEDIA_ROOT = media_root
        self.SERVE_MEDIA = _env("SERVE_MEDIA", "1") != "0"

    def validate(self) -> None:
        """Hook for environments that must fail closed."""


class DevelopmentConfig(BaseConfig):
    APP_ENV = "development"
    DEBUG = True
    MAIL_BACKEND = "console"
    SESSION_COOKIE_SECURE = False

    def __init__(self) -> None:
        super().__init__()
        # Local-only fallbacks so `flask run` works out of the box.
        self.SECRET_KEY = self.SECRET_KEY or "dev-only-not-secret"  # noqa: S105
        self.SQLALCHEMY_DATABASE_URI = (
            self.SQLALCHEMY_DATABASE_URI or "postgresql+psycopg://gg:gg@localhost:55432/gg_site"
        )
        self.REDIS_URL = self.REDIS_URL or "redis://localhost:6379/0"


class TestingConfig(BaseConfig):
    APP_ENV = "testing"
    TESTING = True
    MAIL_BACKEND = "memory"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False

    def __init__(self) -> None:
        super().__init__()
        self.SECRET_KEY = "test-only-not-secret"  # noqa: S105
        self.SQLALCHEMY_DATABASE_URI = _env("TEST_DATABASE_URL") or "sqlite:///:memory:"
        self.REDIS_URL = _env("TEST_REDIS_URL")
        self.SUPPORTED_COUNTRIES = ("US",)
        self.FLAT_SHIPPING_CENTS = 799
        self.LOCAL_SHIPPING_CENTS = 500
        self.TAX_BACKEND = "none"


class ProductionConfig(BaseConfig):
    """Used for both staging and production; they differ only by environment values."""

    APP_ENV = "production"
    ENABLE_HSTS = True

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("SECRET_KEY", self.SECRET_KEY),
                ("DATABASE_URL", self.SQLALCHEMY_DATABASE_URI),
                ("REDIS_URL", self.REDIS_URL),
            )
            if value is None
        ]
        if missing:
            raise ConfigError(f"Missing required configuration: {', '.join(missing)}")
        if self.SECRET_KEY is not None and len(self.SECRET_KEY) < 32:
            raise ConfigError("SECRET_KEY must be at least 32 characters")
        if self.TAX_BACKEND not in (None, "none", "stripe"):
            raise ConfigError("TAX_BACKEND must be 'stripe' or 'none'")
        if self.SHIPPING_BACKEND not in ("flat", "shippo"):
            raise ConfigError("SHIPPING_BACKEND must be 'flat' or 'shippo'")
        if self.GA4_MEASUREMENT_ID and not self.GA4_MEASUREMENT_ID.startswith("G-"):
            raise ConfigError("GA4_MEASUREMENT_ID must look like G-XXXXXXXXXX")


CONFIGS: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def load_config(name: str | None = None) -> BaseConfig:
    env_name = name or _env("APP_ENV", "development") or "development"
    try:
        config = CONFIGS[env_name]()
    except KeyError as exc:
        raise ConfigError(f"Unknown APP_ENV {env_name!r}") from exc
    config.validate()
    return config

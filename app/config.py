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
    # SMTP works with any transactional provider (Postmark, SES, SendGrid, Workspace, own server).
    MAIL_FROM: str | None = None  # e.g. "GG Afrikan Eleganz <orders@ggafrikaneleganz.com>"
    MAIL_REPLY_TO: str | None = None
    SMTP_HOST: str | None = None
    SMTP_PORT = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_SECURITY = "starttls"  # starttls (port 587) | ssl (port 465)
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

    # Staff Studio. The private path must be supplied by the environment in
    # production and is deliberately not linked from the public storefront.
    STUDIO_PATH: str | None = None
    STUDIO_APPROVAL_BACKEND = "console"
    STUDIO_APPROVAL_WEBHOOK_URL: str | None = None
    STUDIO_NTFY_SERVER = "https://ntfy.sh"
    STUDIO_NTFY_TOPIC: str | None = None
    STUDIO_NTFY_TOKEN: str | None = None
    # Where held staff changes and key requests are emailed (default: ORDER_NOTIFICATION_EMAIL).
    STUDIO_APPROVER_EMAIL: str | None = None
    # WebAuthn relying-party ID for Studio phone keys (default: the request's host name).
    STUDIO_RP_ID: str | None = None
    # Review photos: "local" = private on-server nudity check (owner, 2026-10-27; needs the
    # optional nudenet package); anything else keeps photo posting switched off.
    IMAGE_MODERATION_BACKEND: str | None = None
    IMAGE_MODERATION_THRESHOLD = 0.5
    # Texted sign-in codes for accounts with requires_sms: "twilio" (production) or "console".
    STUDIO_SMS_BACKEND: str | None = None
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_FROM_NUMBER: str | None = None
    STUDIO_COOKIE_NAME = "gg_studio"
    # None means removed text is discarded immediately; owner must explicitly
    # choose a retention period before production moderation begins.
    MODERATION_RETENTION_DAYS: int | None = None
    STUDIO_USERS_FILE = "config/studio_users.json"

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
        self.MAIL_BACKEND = _env("MAIL_BACKEND") or self.MAIL_BACKEND
        self.MAIL_FROM = _env("MAIL_FROM")
        self.MAIL_REPLY_TO = _env("MAIL_REPLY_TO")
        self.SMTP_HOST = _env("SMTP_HOST")
        self.SMTP_PORT = _env_int("SMTP_PORT", self.SMTP_PORT)
        self.SMTP_USERNAME = _env("SMTP_USERNAME")
        self.SMTP_PASSWORD = _env("SMTP_PASSWORD")
        self.SMTP_SECURITY = (_env("SMTP_SECURITY") or self.SMTP_SECURITY).lower()
        # New orders, Studio approvals and the daily summary (owner, 2026-10-27).
        self.ORDER_NOTIFICATION_EMAIL = _env("ORDER_NOTIFICATION_EMAIL") or "ggafrikan@gmail.com"
        self.MAILCHIMP_API_KEY = _env("MAILCHIMP_API_KEY")
        self.MAILCHIMP_SERVER_PREFIX = _env("MAILCHIMP_SERVER_PREFIX")
        self.MAILCHIMP_LIST_ID = _env("MAILCHIMP_LIST_ID")
        self.APPLE_PAY_DOMAIN_ASSOCIATION = _env("APPLE_PAY_DOMAIN_ASSOCIATION")
        self.STUDIO_PATH = _env("STUDIO_PATH")
        self.STUDIO_APPROVAL_BACKEND = (
            _env("STUDIO_APPROVAL_BACKEND", self.STUDIO_APPROVAL_BACKEND) or "console"
        )
        self.STUDIO_APPROVAL_WEBHOOK_URL = _env("STUDIO_APPROVAL_WEBHOOK_URL")
        self.STUDIO_NTFY_SERVER = (
            _env("STUDIO_NTFY_SERVER", self.STUDIO_NTFY_SERVER) or self.STUDIO_NTFY_SERVER
        )
        self.STUDIO_NTFY_TOPIC = _env("STUDIO_NTFY_TOPIC")
        self.STUDIO_NTFY_TOKEN = _env("STUDIO_NTFY_TOKEN")
        self.STUDIO_APPROVER_EMAIL = _env("STUDIO_APPROVER_EMAIL")
        self.STUDIO_RP_ID = _env("STUDIO_RP_ID")
        self.IMAGE_MODERATION_BACKEND = _env("IMAGE_MODERATION_BACKEND")
        self.STUDIO_SMS_BACKEND = _env("STUDIO_SMS_BACKEND")
        self.TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
        self.TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
        self.TWILIO_FROM_NUMBER = _env("TWILIO_FROM_NUMBER")
        threshold = _env("IMAGE_MODERATION_THRESHOLD")
        if threshold:
            self.IMAGE_MODERATION_THRESHOLD = float(threshold)
        self.MODERATION_RETENTION_DAYS = _env_optional_int("MODERATION_RETENTION_DAYS")
        self.STUDIO_USERS_FILE = (
            _env("STUDIO_USERS_FILE", self.STUDIO_USERS_FILE) or self.STUDIO_USERS_FILE
        )
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
        self.STUDIO_PATH = self.STUDIO_PATH or "studio-local-development-only"
        self.STUDIO_SMS_BACKEND = self.STUDIO_SMS_BACKEND or "console"


class TestingConfig(BaseConfig):
    APP_ENV = "testing"
    TESTING = True
    MAIL_BACKEND = "memory"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False

    def __init__(self) -> None:
        super().__init__()
        self.SECRET_KEY = "test-only-not-secret"  # noqa: S105
        self.MAIL_BACKEND = "memory"  # never send real email from tests, whatever the shell says
        self.ORDER_NOTIFICATION_EMAIL = _env("ORDER_NOTIFICATION_EMAIL")  # tests opt in
        self.SQLALCHEMY_DATABASE_URI = _env("TEST_DATABASE_URL") or "sqlite:///:memory:"
        self.REDIS_URL = _env("TEST_REDIS_URL")
        self.SUPPORTED_COUNTRIES = ("US",)
        self.FLAT_SHIPPING_CENTS = 799
        self.LOCAL_SHIPPING_CENTS = 500
        self.TAX_BACKEND = "none"
        self.STUDIO_PATH = "studio-testing-private-path"
        self.MODERATION_RETENTION_DAYS = 15


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
        if self.MAIL_BACKEND not in (None, "smtp"):
            # console/memory would print reset links and order details into server logs.
            raise ConfigError("MAIL_BACKEND must be 'smtp' (or unset) in production")
        if self.MAIL_BACKEND == "smtp":
            if not self.SMTP_HOST or not self.MAIL_FROM:
                raise ConfigError("MAIL_BACKEND=smtp needs SMTP_HOST and MAIL_FROM")
            if self.SMTP_SECURITY not in ("starttls", "ssl"):
                raise ConfigError("SMTP_SECURITY must be 'starttls' or 'ssl'")
        if self.GA4_MEASUREMENT_ID and not self.GA4_MEASUREMENT_ID.startswith("G-"):
            raise ConfigError("GA4_MEASUREMENT_ID must look like G-XXXXXXXXXX")
        if self.STUDIO_PATH is not None:
            path = self.STUDIO_PATH.strip("/")
            if len(path) < 24 or not all(char.isalnum() or char in "-_" for char in path):
                raise ConfigError("STUDIO_PATH must be a 24+ character URL-safe path segment")
        if self.STUDIO_APPROVAL_BACKEND not in {"console", "webhook", "ntfy"}:
            raise ConfigError("STUDIO_APPROVAL_BACKEND must be 'console', 'webhook', or 'ntfy'")
        if self.STUDIO_APPROVAL_BACKEND == "webhook" and not self.STUDIO_APPROVAL_WEBHOOK_URL:
            raise ConfigError("STUDIO_APPROVAL_WEBHOOK_URL is required for webhook approval")
        if self.STUDIO_APPROVAL_WEBHOOK_URL and not self.STUDIO_APPROVAL_WEBHOOK_URL.startswith(
            "https://"
        ):
            raise ConfigError("STUDIO_APPROVAL_WEBHOOK_URL must use HTTPS")
        if self.STUDIO_APPROVAL_BACKEND == "ntfy" and not self.STUDIO_NTFY_TOPIC:
            raise ConfigError("STUDIO_NTFY_TOPIC is required for ntfy approval")
        if not self.STUDIO_NTFY_SERVER.startswith("https://"):
            raise ConfigError("STUDIO_NTFY_SERVER must use HTTPS")
        if self.STUDIO_SMS_BACKEND not in (None, "twilio"):
            # console would write sign-in codes into the server log.
            raise ConfigError("STUDIO_SMS_BACKEND must be 'twilio' (or unset) in production")
        if self.STUDIO_SMS_BACKEND == "twilio" and not (
            self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN and self.TWILIO_FROM_NUMBER
        ):
            raise ConfigError(
                "Twilio texts need TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER"
            )
        if self.IMAGE_MODERATION_BACKEND not in (None, "none", "local"):
            raise ConfigError("IMAGE_MODERATION_BACKEND must be 'local' or 'none'")
        if not 0.1 <= self.IMAGE_MODERATION_THRESHOLD <= 0.95:
            raise ConfigError("IMAGE_MODERATION_THRESHOLD must be between 0.1 and 0.95")
        if self.MODERATION_RETENTION_DAYS not in {None, 0, 15}:
            raise ConfigError("MODERATION_RETENTION_DAYS must be 0 or 15")


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

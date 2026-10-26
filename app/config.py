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
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    WTF_CSRF_TIME_LIMIT = 3600
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # request bodies; uploads get their own path later

    ENABLE_HSTS = False

    def __init__(self) -> None:
        self.SECRET_KEY = _env("SECRET_KEY")
        self.SQLALCHEMY_DATABASE_URI = _env("DATABASE_URL")
        self.REDIS_URL = _env("REDIS_URL")
        self.TRUSTED_PROXY_HOPS = _env_int("TRUSTED_PROXY_HOPS", self.TRUSTED_PROXY_HOPS)

    def validate(self) -> None:
        """Hook for environments that must fail closed."""


class DevelopmentConfig(BaseConfig):
    APP_ENV = "development"
    DEBUG = True
    SESSION_COOKIE_SECURE = False

    def __init__(self) -> None:
        super().__init__()
        # Local-only fallbacks so `flask run` works out of the box.
        self.SECRET_KEY = self.SECRET_KEY or "dev-only-not-secret"  # noqa: S105
        self.SQLALCHEMY_DATABASE_URI = (
            self.SQLALCHEMY_DATABASE_URI or "postgresql+psycopg://gg:gg@localhost:55432/gg_dev"
        )
        self.REDIS_URL = self.REDIS_URL or "redis://localhost:6379/0"


class TestingConfig(BaseConfig):
    APP_ENV = "testing"
    TESTING = True
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = False

    def __init__(self) -> None:
        super().__init__()
        self.SECRET_KEY = "test-only-not-secret"  # noqa: S105
        self.SQLALCHEMY_DATABASE_URI = _env("TEST_DATABASE_URL") or "sqlite:///:memory:"
        self.REDIS_URL = _env("TEST_REDIS_URL")


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

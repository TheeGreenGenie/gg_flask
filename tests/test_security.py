from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from app import create_app
from app.config import ConfigError, ProductionConfig, load_config


def test_security_headers_on_every_response(client: FlaskClient) -> None:
    for path in ("/", "/health/live", "/does-not-exist"):
        headers = client.get(path).headers
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_no_hsts_outside_production(client: FlaskClient) -> None:
    assert "Strict-Transport-Security" not in client.get("/").headers


def test_production_refuses_to_start_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SECRET_KEY", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match="SECRET_KEY, DATABASE_URL, REDIS_URL"):
        load_config("production")


def test_production_rejects_short_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gg@localhost/unused")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    with pytest.raises(ConfigError, match="at least 32"):
        load_config("production")


def test_production_sets_hsts_and_secure_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gg@localhost/unused")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    app = create_app(ProductionConfig())
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["DEBUG"] is False
    resp = app.test_client().get("/health/live")
    assert resp.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ConfigError):
        load_config("prod")


def test_forwarded_headers_ignored_without_trusted_proxy(client: FlaskClient) -> None:
    from flask import request

    app = client.application

    @app.get("/_test/ip")
    def _ip() -> str:
        return request.remote_addr or ""

    resp = client.get(
        "/_test/ip",
        headers={"X-Forwarded-For": "6.6.6.6"},
        environ_base={"REMOTE_ADDR": "10.0.0.1"},
    )
    assert resp.text == "10.0.0.1"


def test_forwarded_headers_used_with_one_trusted_proxy() -> None:
    from flask import request

    from app.config import TestingConfig

    cfg = TestingConfig()
    cfg.TRUSTED_PROXY_HOPS = 1
    app = create_app(cfg)

    @app.get("/_test/ip")
    def _ip() -> str:
        return request.remote_addr or ""

    resp = app.test_client().get(
        "/_test/ip",
        headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"},
        environ_base={"REMOTE_ADDR": "10.0.0.1"},
    )
    # Only the hop added by our own proxy (the last one) is trusted.
    assert resp.text == "203.0.113.9"


def test_fixed_dummy_hash_matches_current_password_settings() -> None:
    import pytest
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    from app.passwords import DUMMY_HASH

    hasher = PasswordHasher()
    # Same cost as a real hash, so an unknown username takes as long as a wrong password.
    assert not hasher.check_needs_rehash(DUMMY_HASH), "regenerate DUMMY_HASH with PasswordHasher()"
    with pytest.raises(VerifyMismatchError):
        hasher.verify(DUMMY_HASH, "")

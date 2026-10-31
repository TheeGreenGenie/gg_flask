from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_isolated_tests import (
    IsolationError,
    child_environment,
    database_name,
    validate_base_url,
)


def test_generated_database_names_are_unique_and_narrow() -> None:
    first = database_name("Codex security", pid=123, nonce="a1b2c3d4")
    second = database_name("Codex security", pid=123, nonce="ffffffff")
    assert first == "gg_iso_codex_security_123_a1b2c3d4"
    assert first != second and len(first) <= 63


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://gg:gg@example.com:5432/gg_test",
        "postgresql+psycopg://gg:gg@127.0.0.1:55432/gg_test?host=example.com",
        "postgresql+psycopg://gg:gg@127.0.0.1:55432/gg_testprod",
        "postgresql+psycopg://gg:gg@127.0.0.1:55432/gg_site",
        "sqlite:///gg_test.db",
    ],
)
def test_runner_refuses_remote_production_and_non_postgres_urls(url: str) -> None:
    with pytest.raises(IsolationError):
        validate_base_url(url)


def test_child_environment_uses_only_isolated_test_settings() -> None:
    base = validate_base_url("postgresql+psycopg://gg:gg@127.0.0.1:55432/gg_test")
    parent = {
        "PATH": "safe-path",
        "SYSTEMROOT": "safe-root",
        "STRIPE_SECRET_KEY": "must-not-pass",
        "PAYPAL_CLIENT_SECRET": "must-not-pass",
        "DATABASE_URL": "must-not-pass",
        "PREVIEW_PASSWORD": "must-not-pass",
    }
    env = child_environment(parent, base, "gg_iso_codex_123_a1b2c3d4", Path("isolated-test-temp"))
    assert env["PATH"] == "safe-path" and env["SYSTEMROOT"] == "safe-root"
    assert env["APP_ENV"] == "testing" and env["TEST_KV_BACKEND"] == "database"
    assert env["TEST_DATABASE_URL"].endswith("/gg_iso_codex_123_a1b2c3d4")
    for secret in ("STRIPE_SECRET_KEY", "PAYPAL_CLIENT_SECRET", "DATABASE_URL", "PREVIEW_PASSWORD"):
        assert secret not in env

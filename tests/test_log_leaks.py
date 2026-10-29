"""One-time links, the Studio address and address searches must never reach log files."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import Flask

from app.config import ConfigError, load_config

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
ROOT = DEPLOY.parent


def test_gunicorn_keeps_no_raw_access_log() -> None:
    for path in (DEPLOY / "gg-flask.service", ROOT / "Dockerfile"):
        assert "access-logfile" not in path.read_text(encoding="utf-8"), path.name


def test_apache_logs_paths_without_queries_and_redacts_secret_paths() -> None:
    conf = (DEPLOY / "apache-site.example.conf").read_text(encoding="utf-8")
    custom_logs = re.findall(r"^\s*CustomLog\s.*$", conf, re.M)
    assert custom_logs and all("combined" not in line for line in custom_logs)
    log_formats = re.findall(r'^\s*LogFormat\s+"(.*)"\s+(\w+)$', conf, re.M)
    formats = {name: fmt for fmt, name in log_formats}
    assert "%r" not in formats["gg_safe"] and "%U" in formats["gg_safe"]  # %U has no query string
    assert "%{Referer}i" not in formats["gg_safe"] and "%q" not in formats["gg_safe"]
    assert "[redacted]" in formats["gg_redacted"] and "%U" not in formats["gg_redacted"]
    redacted = re.findall(r'SetEnvIf Request_URI "([^"]+)" gg_secret_path', conf)
    for secret in (
        "/account/verify-email/abc",
        "/account/password/reset/abc",
        "/account/orders/ORD1",
        "/checkout/order/ORD1/return",
        "/REPLACE_WITH_STUDIO_PATH/approve/abc",
    ):
        assert any(re.match(pattern, secret) for pattern in redacted), secret
    assert not any(re.match(pattern, "/shop/ankara/") for pattern in redacted)


def test_held_change_link_is_not_logged_when_email_fails(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.studio import review
    from app.studio.models import StudioChange

    logged: list[str] = []
    monkeypatch.setattr(review, "_approver_email", lambda: None)
    monkeypatch.setattr(review.log, "warning", lambda msg, *args: logged.append(msg % args))
    with app.test_request_context("/studio-testing-private-path/"):
        from app.studio import service

        user = service.create_user("kemi", "Temporary phrase 8472!", "sales", requires_review=True)
        change = StudioChange(
            token_hash="x" * 64, user_id=user.id, endpoint="studio.price_update", summary="Price"
        )
        from app.extensions import db

        db.session.add(change)
        db.session.commit()
        review._notify_owner(change, "SECRET-REVIEW-TOKEN-123")
    text = " | ".join(logged)
    assert "studio_change_notification_not_sent" in text
    assert "SECRET-REVIEW-TOKEN-123" not in text and "/review/" not in text


def test_production_refuses_console_sign_in_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gg@localhost/unused")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("STUDIO_PATH", "a" * 30)
    monkeypatch.delenv("STUDIO_APPROVAL_BACKEND", raising=False)
    with pytest.raises(ConfigError, match="ntfy"):
        load_config("production")
    monkeypatch.setenv("STUDIO_APPROVAL_BACKEND", "ntfy")
    monkeypatch.setenv("STUDIO_NTFY_TOPIC", "private-topic")
    load_config("production")

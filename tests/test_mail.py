# ruff: noqa: S105, S106  (fake SMTP credentials for tests)
from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from app import mail
from app.config import ConfigError, load_config


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL and records what the app did."""

    instances: list[FakeSMTP] = []
    fail_with: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float, context: Any = None) -> None:
        self.host, self.port, self.timeout, self.ssl_context = host, port, timeout, context
        self.calls: list[str] = []
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message: Any) -> None:
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with
        self.calls.append("send")
        self.sent.append(message)


@pytest.fixture
def smtp_app(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Flask:
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    app.config.update(
        MAIL_BACKEND="smtp",
        SMTP_HOST="smtp.example.test",
        SMTP_PORT=587,
        SMTP_USERNAME="apikey",
        SMTP_PASSWORD="secret",
        SMTP_SECURITY="starttls",
        MAIL_FROM="GG Afrikan Eleganz <orders@ggafrikaneleganz.com>",
        MAIL_REPLY_TO="info@ggafrikaneleganz.com",
    )
    return app


def test_smtp_sends_over_starttls_with_login(smtp_app: Flask) -> None:
    with smtp_app.app_context():
        assert mail.available()
        assert mail.send("ada@example.com", "Order GG-1 confirmed", "Thank you!")
    (conn,) = FakeSMTP.instances
    assert (conn.host, conn.port) == ("smtp.example.test", 587)
    assert conn.calls == ["starttls", "login:apikey", "send", "quit"]
    sent = conn.sent[0]
    assert sent["To"] == "ada@example.com" and sent["Subject"] == "Order GG-1 confirmed"
    assert sent["From"] == "GG Afrikan Eleganz <orders@ggafrikaneleganz.com>"
    assert sent["Reply-To"] == "info@ggafrikaneleganz.com"
    assert sent["Message-ID"].endswith("@ggafrikaneleganz.com>")
    assert sent.get_content().strip() == "Thank you!"


def test_smtp_ssl_mode_uses_implicit_tls(smtp_app: Flask) -> None:
    smtp_app.config.update(SMTP_SECURITY="ssl", SMTP_PORT=465)
    with smtp_app.app_context():
        assert mail.send("ada@example.com", "Hi", "Body")
    (conn,) = FakeSMTP.instances
    assert conn.port == 465 and conn.ssl_context is not None
    assert "starttls" not in conn.calls


@pytest.mark.parametrize(
    ("to", "subject"),
    [("ada@example.com\r\nBcc: everyone@example.com", "Hi"), ("ada@example.com", "Hi\nBcc: x")],
)
def test_header_injection_is_refused(smtp_app: Flask, to: str, subject: str) -> None:
    with smtp_app.app_context():
        assert mail.send(to, subject, "Body") is False
    assert FakeSMTP.instances == []


def test_provider_failure_returns_false_without_logging_content(
    smtp_app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    import smtplib

    FakeSMTP.fail_with = smtplib.SMTPRecipientsRefused({})
    with smtp_app.app_context():
        assert mail.send("ada@example.com", "Reset your password", "secret-reset-link") is False
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "secret-reset-link" not in logged and "ada@" not in logged


def test_unconfigured_smtp_is_unavailable(smtp_app: Flask) -> None:
    smtp_app.config["SMTP_HOST"] = None
    with smtp_app.app_context():
        assert not mail.available()
        assert mail.send("ada@example.com", "Hi", "Body") is False


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


def test_production_reads_smtp_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.postmarkapp.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("MAIL_FROM", "orders@ggafrikaneleganz.com")
    cfg = load_config("production")
    assert (cfg.MAIL_BACKEND, cfg.SMTP_HOST) == ("smtp", "smtp.postmarkapp.com")
    assert cfg.SMTP_PORT == 2525


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MAIL_BACKEND": "console"}, "must be 'smtp'"),
        ({"MAIL_BACKEND": "smtp", "MAIL_FROM": "a@b.c"}, "SMTP_HOST and MAIL_FROM"),
        (
            {"MAIL_BACKEND": "smtp", "SMTP_HOST": "h", "MAIL_FROM": "a@b", "SMTP_SECURITY": "none"},
            "SMTP_SECURITY",
        ),
    ],
)
def test_production_rejects_unsafe_mail_settings(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
) -> None:
    _production_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError, match=message):
        load_config("production")


def test_tests_never_send_real_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    assert load_config("testing").MAIL_BACKEND == "memory"


def test_mail_test_command_sends_delivery_probe(app: Flask) -> None:
    result = app.test_cli_runner().invoke(args=["mail", "test", "owner@example.com"])

    assert result.exit_code == 0
    assert "accepted for delivery" in result.output
    assert app.extensions["outbox"][-1].to == "owner@example.com"


def test_mail_test_command_fails_when_unconfigured(app: Flask) -> None:
    app.config["MAIL_BACKEND"] = None
    result = app.test_cli_runner().invoke(args=["mail", "test", "owner@example.com"])

    assert result.exit_code != 0
    assert "Email is not configured" in result.output


def test_console_mail_never_crashes_on_a_non_utf8_console(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys

    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")  # a typical Windows console
    monkeypatch.setattr(sys, "stdout", console)
    app.config["MAIL_BACKEND"] = "console"
    with app.app_context():
        assert mail.send("owner@example.com", "Approve? Price $40.00 → $42.50", "Stock 10 → 12")
    console.flush()
    assert b"Price $40.00 ? $42.50" in raw.getvalue()

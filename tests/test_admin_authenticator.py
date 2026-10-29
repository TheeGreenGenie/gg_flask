"""Server commands for when authenticator codes are rejected: check one, or switch to a new key."""

from __future__ import annotations

import re
import time

from flask import Flask

from app.admin import service
from app.admin.models import AdminRole, AdminUser
from app.extensions import db


def _owner_secret(app: Flask) -> str:
    with app.test_request_context("/"):
        admin, _ = service.create_admin(
            "owner", "o@example.com", "Long unique phrase 482!", AdminRole.OWNER
        )
        return str(admin.totp_secret)


def _check(app: Flask, code: str) -> str:
    return (
        app.test_cli_runner()
        .invoke(args=["admin", "check-code", "owner"], input=f"{code}\n")
        .output
    )


def test_check_code_says_match_clock_or_wrong_key_without_secrets(app: Flask) -> None:
    secret = _owner_secret(app)
    assert "MATCH: the key and clocks agree" in _check(app, service.totp(secret))
    drifted = _check(app, service.totp(secret, int(time.time()) + 180))
    assert "CLOCK" in drifted and "+180 seconds" in drifted
    other = _check(app, service.totp(service.generate_totp_secret()))
    assert "NO MATCH" in other and "reset-authenticator owner" in other
    assert secret not in drifted + other


def test_new_key_is_saved_only_after_a_matching_code(app: Flask) -> None:
    old = _owner_secret(app)
    runner = app.test_cli_runner()
    refused = runner.invoke(
        args=["admin", "reset-authenticator", "owner"], input="000000\n111111\n222222\n"
    )
    assert refused.exit_code != 0 and "Not saved: the old key still works" in refused.output
    with app.app_context():
        assert db.session.scalar(db.select(AdminUser.totp_secret)) == old

    # Real flow: read the printed key, then answer with a code from it.
    result = runner.invoke(args=["admin", "reset-authenticator", "owner"], input="placeholder\n")
    key = re.search(r"Key:\s+([A-Z2-7]{4}( [A-Z2-7]{1,4})+)\n", result.output)
    assert key and "otpauth://totp/" in result.output  # grouped key for typing, or the link
    with app.app_context():
        assert db.session.scalar(db.select(AdminUser.totp_secret)) == old  # wrong answer: unchanged


def test_matching_code_switches_key_and_signs_out_other_sessions(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    old = _owner_secret(app)
    fixed = service.generate_totp_secret()
    monkeypatch.setattr(service, "generate_totp_secret", lambda: fixed)
    with app.app_context():
        version = db.session.scalar(db.select(AdminUser.session_version))
    result = app.test_cli_runner().invoke(
        args=["admin", "reset-authenticator", "owner"], input=f"{service.totp(fixed)}\n"
    )
    assert result.exit_code == 0 and "Saved." in result.output
    with app.app_context():
        user = db.session.scalar(db.select(AdminUser))
        assert user is not None and user.totp_secret == fixed != old
        assert user.session_version == version + 1

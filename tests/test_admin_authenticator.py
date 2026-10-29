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


PASSWORD = "Long unique phrase 482!"  # noqa: S105 (test fixture)


def _create(app: Flask, answers: str):  # type: ignore[no-untyped-def]
    return app.test_cli_runner().invoke(
        args=["admin", "create", "--username", "boss", "--email", "boss@example.com"],
        input=f"{PASSWORD}\n{PASSWORD}\n{answers}",
    )


def test_create_only_finishes_once_the_new_entry_gives_a_matching_code(
    app: Flask, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    fixed = service.generate_totp_secret()
    monkeypatch.setattr(service, "generate_totp_secret", lambda: fixed)
    refused = _create(app, "000000\n111111\n222222\n")
    assert refused.exit_code != 0 and "Nothing created" in refused.output
    with app.app_context():
        assert db.session.scalar(db.select(AdminUser.id)) is None  # a stale entry can't slip by

    created = _create(app, f"{service.totp(fixed)}\n")
    assert created.exit_code == 0, created.output
    assert (
        "authenticator entry is confirmed" in created.output and "Recovery codes" in created.output
    )
    with app.app_context():
        assert db.session.scalar(db.select(AdminUser.totp_secret)) == fixed


def test_create_refuses_bad_details_before_any_key_is_shown(app: Flask) -> None:
    _owner_secret(app)  # "owner" exists
    result = app.test_cli_runner().invoke(
        args=["admin", "create", "--username", "owner", "--email", "x@example.com"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code != 0 and "already exists" in result.output
    assert "Key:" not in result.output


def test_entries_are_labelled_with_their_site(app: Flask) -> None:
    with app.app_context():
        assert "owner%20%40%20local" in service.provisioning_uri("owner", "ABC")
        app.config["SITE_URL"] = "https://ggafrikaneleganz.com"
        assert "owner%20%40%20ggafrikaneleganz.com" in service.provisioning_uri("owner", "ABC")
    output = (
        app.test_cli_runner()
        .invoke(
            args=["admin", "create", "--username", "boss", "--email", "boss@example.com"],
            input=f"{PASSWORD}\n{PASSWORD}\nx\nx\nx\n",
        )
        .output
    )
    assert "Account: GG Afrikan Eleganz (boss @ ggafrikaneleganz.com)" in output

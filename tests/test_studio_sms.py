"""tgg: phone key AND a texted code at every sign-in (owner, 2026-10-27)."""

# ruff: noqa: S105  (fixture password)
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import select

from app.config import ConfigError, load_config
from app.extensions import db
from app.studio import passkeys, service, sms
from app.studio.models import StudioLoginRequest, StudioPasskey, StudioUser

PREFIX = "/studio-testing-private-path"
PASSWORD = "Temporary phrase 8472!"


@pytest.fixture
def tgg_app(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Flask:
    app.config["STUDIO_SMS_BACKEND"] = "console"
    with app.test_request_context(PREFIX + "/"):
        user = service.create_user(
            "tgg", PASSWORD, "owner", may_self_approve=True, requires_sms=True
        )
        user.must_change_password = False
        user.sms_phone = "+12155550123"
        db.session.add(StudioPasskey(user_id=user.id, credential_id="k1", public_key=b"PUB"))
        db.session.commit()
    verified = SimpleNamespace(new_sign_count=1, credential_id=b"k1")
    monkeypatch.setattr(passkeys, "verify_authentication_response", lambda **_: verified)
    return app


def _key_approved_request(app: Flask, client) -> str:  # type: ignore[no-untyped-def]
    client.post(PREFIX + "/", data={"username": "tgg", "password": PASSWORD})
    with app.test_request_context(PREFIX + "/"):
        record = db.session.scalars(
            select(StudioLoginRequest).order_by(StudioLoginRequest.id.desc())
        ).first()
        assert record is not None
        passkeys.authentication_options(record)
        passkeys.self_approve(record, {"id": "k1"})
        return record.request_id


def _logged_code(caplog: pytest.LogCaptureFixture) -> str:
    codes = re.findall(r"code=(\d{6})", caplog.text)
    assert codes, caplog.text
    return codes[-1]


def test_key_alone_is_not_enough(tgg_app: Flask, caplog: pytest.LogCaptureFixture) -> None:
    client = tgg_app.test_client()
    request_id = _key_approved_request(tgg_app, client)
    step = client.get(PREFIX + f"/waiting/{request_id}")
    assert step.status_code == 302 and f"/verify/{request_id}" in step.headers["Location"]
    page = client.get(PREFIX + f"/verify/{request_id}").get_data(as_text=True)
    assert "•••• 0123" in page and "+12155550123" not in page
    assert "gg_studio=" not in str(client.get_cookie("gg_studio"))
    with tgg_app.app_context():
        record = db.session.scalar(select(StudioLoginRequest))
        with pytest.raises(service.StudioError, match="texted"):
            service.consume_approval(record)  # type: ignore[arg-type]


def test_right_code_signs_in_wrong_codes_lock_out(
    tgg_app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    client = tgg_app.test_client()
    request_id = _key_approved_request(tgg_app, client)
    client.get(PREFIX + f"/verify/{request_id}")
    code = _logged_code(caplog)
    wrong = "000000" if code != "000000" else "111111"
    page = client.post(PREFIX + f"/verify/{request_id}", data={"code": wrong})
    assert "not right" in page.get_data(as_text=True)
    done = client.post(PREFIX + f"/verify/{request_id}", data={"code": code})
    assert done.status_code == 302 and done.headers["Location"].endswith("/home")
    assert "gg_studio=" in done.headers["Set-Cookie"]

    client2 = tgg_app.test_client()
    other = _key_approved_request(tgg_app, client2)
    client2.get(PREFIX + f"/verify/{other}")
    for _ in range(sms.MAX_ATTEMPTS):
        client2.post(PREFIX + f"/verify/{other}", data={"code": wrong})
    page = client2.post(PREFIX + f"/verify/{other}", data={"code": _logged_code(caplog)})
    assert "Too many wrong codes" in page.get_data(as_text=True)


def test_no_phone_or_no_sms_service_means_no_sign_in(tgg_app: Flask) -> None:
    client = tgg_app.test_client()
    request_id = _key_approved_request(tgg_app, client)
    tgg_app.config["STUDIO_SMS_BACKEND"] = None
    page = client.get(PREFIX + f"/verify/{request_id}").get_data(as_text=True)
    assert "not set up" in page
    with tgg_app.app_context():
        user = db.session.scalar(select(StudioUser).where(StudioUser.username == "tgg"))
        assert user is not None
        user.sms_phone = None
        db.session.commit()
    tgg_app.config["STUDIO_SMS_BACKEND"] = "console"
    assert "not set up" in client.get(PREFIX + f"/verify/{request_id}").get_data(as_text=True)


def test_codes_are_stored_hashed(tgg_app: Flask, caplog: pytest.LogCaptureFixture) -> None:
    client = tgg_app.test_client()
    request_id = _key_approved_request(tgg_app, client)
    client.get(PREFIX + f"/verify/{request_id}")
    code = _logged_code(caplog)
    with tgg_app.app_context():
        record = db.session.scalar(select(StudioLoginRequest))
        assert record is not None and record.sms_code_hash and code not in record.sms_code_hash


def test_production_never_logs_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("STUDIO_SMS_BACKEND", "console")
    with pytest.raises(ConfigError, match="twilio"):
        load_config("production")
    monkeypatch.setenv("STUDIO_SMS_BACKEND", "twilio")
    with pytest.raises(ConfigError, match="TWILIO_ACCOUNT_SID"):
        load_config("production")


def test_cli_sets_phone_and_flag(app: Flask) -> None:
    runner = app.test_cli_runner()
    out = runner.invoke(
        args=[
            "studio",
            "create",
            "--username",
            "tgg",
            "--roles",
            "owner",
            "--self-approve",
            "--sms",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    ).output
    assert "texted code at every sign-in" in out, out
    out = runner.invoke(args=["studio", "set-phone", "tgg", "215-555-0123"]).output
    assert "•••• 0123" in out

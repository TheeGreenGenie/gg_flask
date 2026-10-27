"""Owner-confirmed phone keys for co-owners.

Server-side rules are tested here; the browser ceremony itself was verified end to end with
Chrome's virtual authenticator.
"""

# ruff: noqa: S105, S106  (fixture passwords)
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask
from sqlalchemy import select

from app.extensions import db
from app.studio import passkeys, service
from app.studio.models import (
    ApprovalStatus,
    KeyEnrollmentStatus,
    StudioAudit,
    StudioKeyEnrollment,
    StudioLoginRequest,
    StudioPasskey,
    StudioUser,
)

PREFIX = "/studio-testing-private-path"
PASSWORD = "Temporary phrase 8472!"


@pytest.fixture
def owner_app(app: Flask) -> Flask:
    app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    with app.test_request_context(PREFIX + "/"):
        service.create_user("mrs.adetolu", PASSWORD, "owner", may_self_approve=True)
        service.create_user("mr.adetolu", PASSWORD, "sales", requires_review=True)
    return app


def _user(name: str) -> StudioUser:
    user = db.session.scalar(select(StudioUser).where(StudioUser.username == name))
    assert user is not None
    return user


def _with_key(user: StudioUser) -> None:
    db.session.add(StudioPasskey(user_id=user.id, credential_id="cred-1", public_key=b"PUBLIC"))
    db.session.commit()


def _sign_in(app: Flask, client: Any, username: str, password: str) -> None:
    response = client.post(PREFIX + "/", data={"username": username, "password": password})
    assert response.status_code == 302
    with app.app_context():
        record = db.session.scalars(
            select(StudioLoginRequest).where(StudioLoginRequest.user.has(username=username))
        ).one()
        service.decide(record, True)
        request_id = record.request_id
    assert client.get(PREFIX + f"/waiting/{request_id}").status_code == 302


def test_only_allowed_people_can_ask_for_a_key(owner_app: Flask) -> None:
    with owner_app.test_request_context(PREFIX + "/key/request", method="POST"):
        with pytest.raises(passkeys.PasskeyError, match="not set up"):
            passkeys.request_enrollment(_user("mr.adetolu"))
        record = passkeys.request_enrollment(_user("mrs.adetolu"))
        assert record.status == KeyEnrollmentStatus.PENDING
        # Asking twice reuses the open request instead of emailing the owner again.
        assert passkeys.request_enrollment(_user("mrs.adetolu")).id == record.id
    message = owner_app.extensions["outbox"][-1]
    assert message.to == "owner@example.com" and "/key-approval/" in message.body


def test_no_key_can_be_created_before_the_owner_confirms(owner_app: Flask) -> None:
    with owner_app.test_request_context(PREFIX + "/key/register/options"):
        user = _user("mrs.adetolu")
        passkeys.request_enrollment(user)
        with pytest.raises(passkeys.PasskeyError, match="not confirmed"):
            passkeys.registration_options(user)
        record = db.session.scalars(select(StudioKeyEnrollment)).one()
        passkeys.decide_enrollment(record, True, "owner@example.com")
        assert '"challenge"' in passkeys.registration_options(user)
        with pytest.raises(passkeys.PasskeyError, match="already decided"):
            passkeys.decide_enrollment(record, False, "owner@example.com")


def test_registration_stores_only_the_public_key(
    owner_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SimpleNamespace(credential_id=b"cred-1", credential_public_key=b"PUBLIC", sign_count=0)
    monkeypatch.setattr(passkeys, "verify_registration_response", lambda **_: fake)
    with owner_app.test_request_context(PREFIX + "/key/register", method="POST"):
        user = _user("mrs.adetolu")
        passkeys.request_enrollment(user)
        record = db.session.scalars(select(StudioKeyEnrollment)).one()
        passkeys.decide_enrollment(record, True, "owner@example.com")
        passkeys.registration_options(user)
        key = passkeys.register(user, {"id": "x"})
        assert key.public_key == b"PUBLIC"
        assert record.status == KeyEnrollmentStatus.REGISTERED
        # One confirmation, one key.
        with pytest.raises(passkeys.PasskeyError):
            passkeys.register(user, {"id": "x"})


def test_key_holder_is_not_paged_and_approves_herself(
    owner_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(service, "_send_approval", lambda *a: sent.append(a))
    verified = SimpleNamespace(new_sign_count=5, credential_id=b"cred-1")
    monkeypatch.setattr(passkeys, "verify_authentication_response", lambda **_: verified)
    with owner_app.test_request_context(PREFIX + "/", method="POST"):
        _with_key(_user("mrs.adetolu"))
        record, _token = service.begin_login("mrs.adetolu", PASSWORD)
        assert sent == []  # the owner's phone is not asked
        with pytest.raises(passkeys.PasskeyError, match="Start the key check again"):
            passkeys.self_approve(record, {"id": "cred-1"})
        passkeys.authentication_options(record)
        with pytest.raises(passkeys.PasskeyError, match="not registered"):
            passkeys.self_approve(record, {"id": "another-persons-key"})
        passkeys.authentication_options(record)
        passkeys.self_approve(record, {"id": "cred-1"})
        assert record.status == ApprovalStatus.APPROVED
        assert db.session.scalars(select(StudioPasskey)).one().sign_count == 5
    assert "signed in to the Studio with her key" in owner_app.extensions["outbox"][-1].subject


def test_people_without_keys_still_need_the_owner(
    owner_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(service, "_send_approval", lambda *a: sent.append(a))
    with owner_app.test_request_context(PREFIX + "/", method="POST"):
        record, _ = service.begin_login("mr.adetolu", PASSWORD)
        assert len(sent) == 1
        with pytest.raises(passkeys.PasskeyError, match="no sign-in key"):
            passkeys.authentication_options(record)


def test_removing_keys_signs_her_out(owner_app: Flask) -> None:
    with owner_app.app_context():
        user = _user("mrs.adetolu")
        _with_key(user)
        version = user.session_version
    result = owner_app.test_cli_runner().invoke(args=["studio", "remove-keys", "mrs.adetolu"])
    assert "Removed 1 key" in result.output
    with owner_app.app_context():
        assert db.session.scalar(select(StudioPasskey)) is None
        assert _user("mrs.adetolu").session_version == version + 1


def test_tgg_phone_key_issues_one_display_temporary_password(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app.test_request_context(PREFIX + "/"):
        tgg = service.create_user("tgg", PASSWORD, "owner", may_self_approve=True)
        target = service.create_user("mrs.adetolu", PASSWORD, "owner", may_self_approve=True)
        tgg.must_change_password = False
        target.must_change_password = False
        _with_key(tgg)
        target_version = target.session_version

    client = app.test_client()
    _sign_in(app, client, "tgg", PASSWORD)
    page = client.get(PREFIX + "/password-swaps")
    assert page.status_code == 200 and b"mrs.adetolu" in page.data

    verified = SimpleNamespace(new_sign_count=7, credential_id=b"cred-1")
    monkeypatch.setattr(passkeys, "verify_authentication_response", lambda **_: verified)
    assert client.get(PREFIX + "/password-swaps/key-options").status_code == 200
    response = client.post(
        PREFIX + "/password-swaps",
        json={"username": "mrs.adetolu", "credential": {"id": "cred-1"}},
    )
    assert response.status_code == 200
    temporary = response.get_json()["temporary_password"]
    assert len(temporary) >= 12

    with app.app_context():
        target = _user("mrs.adetolu")
        assert target.must_change_password
        assert target.session_version == target_version + 1
        assert temporary not in target.password_hash
        swap = db.session.scalar(
            select(StudioAudit).where(StudioAudit.action == "studio.user.password_swapped")
        )
        assert swap is not None and swap.actor_username == "tgg"
        assert db.session.scalars(select(StudioPasskey)).one().sign_count == 7

    # The challenge is consumed: every additional swap prompts for the phone key again.
    replay = client.post(
        PREFIX + "/password-swaps",
        json={"username": "mrs.adetolu", "credential": {"id": "cred-1"}},
    )
    assert replay.status_code == 400
    assert "phone-key check again" in replay.get_json()["error"]


def test_mrs_adetolu_cannot_open_or_call_password_swaps(app: Flask) -> None:
    with app.test_request_context(PREFIX + "/"):
        user = service.create_user(
            "mrs.adetolu", PASSWORD, "owner", may_self_approve=True
        )
        user.must_change_password = False
        _with_key(user)
    client = app.test_client()
    _sign_in(app, client, "mrs.adetolu", PASSWORD)
    home = client.get(PREFIX + "/home")
    assert b"Password swaps" not in home.data
    assert client.get(PREFIX + "/password-swaps").status_code == 403
    assert client.get(PREFIX + "/password-swaps/key-options").status_code == 403
    assert client.post(PREFIX + "/password-swaps", json={}).status_code == 403


def test_cli_and_manifest_record_review_and_key_permissions(app: Flask, tmp_path: Path) -> None:
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["studio", "create", "--username", "ms.adetolu", "--roles", "branding", "--review"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert "changes need owner approval" in result.output, result.output
    manifest = tmp_path / "users.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "users": [
                    {"username": "ms.adetolu", "roles": ["branding"], "review": True},
                    {"username": "mrs.adetolu", "roles": ["owner"], "self_approve": True},
                ],
            }
        )
    )
    preview = runner.invoke(args=["studio", "sync-users", "--file", str(manifest)])
    assert "create: mrs.adetolu roles=owner status=active self-approve" in preview.output
    assert "ms.adetolu" not in preview.output  # already matches the file

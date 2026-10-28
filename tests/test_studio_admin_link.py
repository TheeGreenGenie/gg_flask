"""tgg <-> owner: a Studio sign-in from a browser signed in to /admin as the linked owner is
approved on the spot; every other case still waits for the owner's approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.admin import service as admin_service
from app.admin.models import AdminRole
from app.extensions import db
from app.studio import service
from app.studio.models import ApprovalStatus, StudioLoginRequest, StudioUser

PREFIX = "/studio-testing-private-path"
PASSWORD = "Temporary phrase 8472!"  # noqa: S105
ADMIN_PASSWORD = "Long unique phrase 482!"  # noqa: S105


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    notices: list[str] = []
    monkeypatch.setattr(service, "_send_approval", lambda record, token: notices.append(token))
    return notices


def _admin(app: Flask, username: str) -> FlaskClient:
    with app.test_request_context("/"):
        admin, _ = admin_service.create_admin(
            username, f"{username}@example.com", ADMIN_PASSWORD, AdminRole.OWNER
        )
        secret = admin.totp_secret
    client = app.test_client()
    client.post("/admin/login", data={"username": username, "password": ADMIN_PASSWORD})
    client.post("/admin/verify", data={"code": admin_service.totp(secret)})
    return client


def _studio_user(app: Flask, name: str, roles: str, link: str | None) -> None:
    with app.test_request_context(PREFIX + "/"):
        user = service.create_user(name, PASSWORD, roles)
        user.must_change_password = False
        db.session.commit()
        if link:
            service.set_admin_link(user, link)


def _sign_in(app: Flask, client: FlaskClient, name: str) -> Any:
    response = client.post(PREFIX + "/", data={"username": name, "password": PASSWORD})
    with app.app_context():
        record = db.session.query(StudioLoginRequest).order_by(StudioLoginRequest.id.desc()).first()
        return response, record.status, record.request_id


def test_signed_in_owner_gets_straight_into_the_studio(app: Flask, sent: list[str]) -> None:
    owner = _admin(app, "owner")
    _studio_user(app, "tgg", "owner", "owner")
    _, status, request_id = _sign_in(app, owner, "tgg")
    assert status == ApprovalStatus.APPROVED and sent == []  # no phone notification needed
    page = owner.get(PREFIX + f"/waiting/{request_id}")
    assert page.status_code == 302 and "gg_studio=" in page.headers["Set-Cookie"]


def test_without_the_admin_session_tgg_still_waits(app: Flask, sent: list[str]) -> None:
    _admin(app, "owner")
    _studio_user(app, "tgg", "owner", "owner")
    _, status, _ = _sign_in(app, app.test_client(), "tgg")
    assert status == ApprovalStatus.PENDING and len(sent) == 1


def test_a_different_owner_session_does_not_count(app: Flask, sent: list[str]) -> None:
    _admin(app, "owner")
    other = _admin(app, "partner")
    _studio_user(app, "tgg", "owner", "owner")
    _, status, _ = _sign_in(app, other, "tgg")
    assert status == ApprovalStatus.PENDING


def test_unlinked_staff_never_skip_approval(app: Flask, sent: list[str]) -> None:
    owner = _admin(app, "owner")
    _studio_user(app, "ms.adetolu", "branding", None)
    _, status, _ = _sign_in(app, owner, "ms.adetolu")
    assert status == ApprovalStatus.PENDING


def test_roster_links_only_to_owner_accounts(app: Flask, tmp_path: Path) -> None:
    with app.test_request_context("/"):
        admin_service.create_admin("owner", "owner@example.com", ADMIN_PASSWORD, AdminRole.OWNER)
        admin_service.create_admin(
            "helper", "helper@example.com", ADMIN_PASSWORD, AdminRole.SUPPORT
        )
    _studio_user(app, "tgg", "owner", None)
    roster = tmp_path / "users.json"
    runner = app.test_cli_runner()

    def sync(admin: str) -> Any:
        users = [{"username": "tgg", "roles": ["owner"], "admin": admin}]
        roster.write_text(json.dumps({"version": 1, "users": users}), encoding="utf-8")
        return runner.invoke(args=["studio", "sync-users", "--file", str(roster), "--apply"])

    assert "not an owner account" in sync("helper").output
    assert "admin=owner" in sync("owner").output
    with app.app_context():
        tgg = db.session.query(StudioUser).filter_by(username="tgg").one()
        assert tgg.admin_user_id is not None

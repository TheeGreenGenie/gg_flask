"""Admin accounts that sign in with the owner's phone approval (ntfy), not an authenticator."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.admin import service
from app.admin.models import AdminLoginRequest, AdminRole, AdminUser
from app.catalog.models import utcnow
from app.extensions import db
from app.studio import service as studio_service
from tests.test_admin import admin_client

PASSWORD = "Her own long phrase 2026!"  # noqa: S105 (test fixture)


@pytest.fixture
def pushes(catalog_app: Flask, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def record(title: str, message: str, link: str, expires_at: Any, **_: Any) -> None:
        sent.append({"title": title, "message": message, "link": link})

    monkeypatch.setattr(studio_service, "send_push", record)
    return sent


def _approval_account(app: Flask, name: str = "mrs.adetolu") -> None:
    with app.test_request_context("/"):
        user, _ = service.create_admin(name, f"{name}@example.com", PASSWORD, AdminRole.OWNER)
        service.set_owner_approval(user, True)


def _sign_in(app: Flask, name: str = "mrs.adetolu") -> tuple[FlaskClient, str]:
    browser = app.test_client()
    response = browser.post("/admin/login", data={"username": name, "password": PASSWORD})
    assert response.status_code == 302 and "/admin/waiting/" in response.location
    return browser, response.location


def _approve_path(push: dict[str, Any]) -> str:
    return "/admin/approve/" + push["link"].rsplit("/approve/", 1)[1]


def test_owner_approves_from_the_push_and_she_gets_in(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    owner = admin_client(catalog_app)  # the owner, signed in with an authenticator
    _approval_account(catalog_app)
    browser, waiting = _sign_in(catalog_app)
    page = browser.get(waiting)
    assert page.status_code == 200 and page.headers["Refresh"] == "2"
    with catalog_app.app_context():
        record = db.session.scalar(db.select(AdminLoginRequest))
        assert record is not None
        match = record.match_code
    assert match in page.get_data(as_text=True)
    assert len(pushes) == 1 and match in pushes[0]["message"]
    assert "mrs.adetolu wants to sign in to admin" in pushes[0]["message"]

    review = owner.get(_approve_path(pushes[0]))
    assert review.status_code == 200 and match in review.get_data(as_text=True)
    owner.post(_approve_path(pushes[0]), data={"decision": "approve"})

    assert browser.get(waiting).location == "/admin/"
    assert browser.get("/admin/").status_code == 200  # she is in
    # Signing in clears the pending request from her session: the old waiting page is gone.
    assert browser.get(waiting).status_code == 404


def test_declined_requests_and_other_browsers_get_nothing(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    owner = admin_client(catalog_app)
    _approval_account(catalog_app)
    browser, waiting = _sign_in(catalog_app)
    assert catalog_app.test_client().get(waiting).status_code == 404  # bound to her browser
    owner.post(_approve_path(pushes[0]), data={"decision": "deny"})
    assert "was not approved" in browser.get(waiting).get_data(as_text=True)
    assert browser.get("/admin/").status_code == 302  # still signed out


def test_her_account_cannot_use_codes_and_cannot_approve(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    owner = admin_client(catalog_app)
    _approval_account(catalog_app)
    _approval_account(catalog_app, "mr.helper")
    browser, waiting = _sign_in(catalog_app)
    refused = browser.post("/admin/verify", data={"code": "123456"})
    assert "signs in with the owner" in refused.get_data(as_text=True)
    owner.post(_approve_path(pushes[0]), data={"decision": "approve"})
    browser.get(waiting)  # now signed in

    _sign_in(catalog_app, "mr.helper")
    other_link = _approve_path(pushes[-1])
    assert browser.post(other_link, data={"decision": "approve"}).status_code == 403
    assert "Only the owner" in browser.get(other_link).get_data(as_text=True)
    with catalog_app.app_context():
        admins = {a.username: a for a in db.session.scalars(db.select(AdminUser))}
        assert not service.can_approve_sign_ins(admins["mrs.adetolu"])  # Studio too
        assert service.can_approve_sign_ins(admins["owner"])


def test_expired_requests_cannot_be_approved(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    owner = admin_client(catalog_app)
    _approval_account(catalog_app)
    browser, waiting = _sign_in(catalog_app)
    with catalog_app.app_context():
        record = db.session.scalar(db.select(AdminLoginRequest))
        assert record is not None
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert "expired" in owner.get(_approve_path(pushes[0])).get_data(as_text=True)
    assert "This request expired" in browser.get(waiting).get_data(as_text=True)


def test_requests_are_rate_limited_so_the_owner_is_not_flooded(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    _approval_account(catalog_app)
    for _ in range(service.APPROVAL_REQUESTS):
        _sign_in(catalog_app)
    blocked = catalog_app.test_client().post(
        "/admin/login", data={"username": "mrs.adetolu", "password": PASSWORD}
    )
    assert "Too many sign-in requests" in blocked.get_data(as_text=True)
    assert len(pushes) == service.APPROVAL_REQUESTS


def test_cli_creates_and_switches_approval_accounts(catalog_app: Flask) -> None:
    runner = catalog_app.test_cli_runner()
    args = ["admin", "create", "--username", "mrs.adetolu", "--email", "h@example.com"]
    nobody = runner.invoke(args=[*args, "--owner-approval"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert nobody.exit_code != 0 and "nobody could approve" in nobody.output
    admin_client(catalog_app)  # an authenticator owner now exists
    made = runner.invoke(args=[*args, "--owner-approval"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert made.exit_code == 0 and "approval request" in made.output
    assert "Recovery codes" not in made.output
    with catalog_app.app_context():
        her = db.session.scalar(db.select(AdminUser).where(AdminUser.username == "mrs.adetolu"))
        assert her is not None and her.uses_owner_approval
    off = runner.invoke(args=["admin", "set-approval", "mrs.adetolu", "off"], input="x\nx\nx\n")
    assert off.exit_code != 0 and "Not changed" in off.output
    on_last = runner.invoke(args=["admin", "set-approval", "owner", "on"])
    assert on_last.exit_code != 0 and "only owner with an authenticator" in on_last.output

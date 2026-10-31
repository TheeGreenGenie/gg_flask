"""Failed privileged sign-ins are counted and alerted; network details expire after 90 days."""

# ruff: noqa: S105, S106  (fixture passwords)
from __future__ import annotations

import json
import urllib.request
from datetime import timedelta
from typing import Any

import pytest
from flask import Flask

from app import retention, security_events
from app.accounts.models import MarketingConsent
from app.admin import service as admin_service
from app.admin.models import AdminAudit, AdminRole
from app.catalog.models import utcnow
from app.extensions import db
from app.studio import service as studio_service
from app.studio.models import StudioAudit

ADMIN_PASSWORD = "Long unique phrase 482!"
STUDIO_PASSWORD = "Temporary phrase 8472!"
PREFIX = "/studio-testing-private-path"


@pytest.fixture
def pushes(app: Flask, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    class _Ok:
        def __enter__(self) -> _Ok:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def capture(req: urllib.request.Request, timeout: float) -> _Ok:
        sent.append(json.loads(req.data))  # type: ignore[arg-type]
        return _Ok()

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    app.config.update(
        STUDIO_APPROVAL_BACKEND="ntfy",
        STUDIO_NTFY_SERVER="https://ntfy.example",
        STUDIO_NTFY_TOPIC="owner-topic",
        ORDER_NOTIFICATION_EMAIL="owner@example.com",
    )
    return sent


def test_right_password_then_wrong_codes_alerts_the_owner_once(
    app: Flask, pushes: list[dict[str, Any]]
) -> None:
    with app.test_request_context("/"):
        admin_service.create_admin("owner", "owner@example.com", ADMIN_PASSWORD, AdminRole.OWNER)
    browser = app.test_client()
    browser.post("/admin/login", data={"username": "owner", "password": "wrong password!!"})
    browser.post("/admin/login", data={"username": "owner", "password": ADMIN_PASSWORD})
    for _ in range(admin_service.CODE_ALERT_AFTER - 1):
        browser.post("/admin/verify", data={"code": "000000"})
    assert pushes == []  # a typo or two is not an alarm
    browser.post("/admin/verify", data={"code": "000000"})
    assert len(pushes) == 1
    push = pushes[0]
    assert "correct password for admin owner" in push["message"]
    assert push["priority"] == 5  # a number: ntfy's JSON API rejects "high"
    outbox = app.extensions.setdefault("outbox", [])
    assert any(m.subject == "Admin sign-in warning" for m in outbox)
    browser.post("/admin/verify", data={"code": "000000"})
    assert len(pushes) == 1  # at most once an hour
    with app.app_context():
        counts = security_events.last_day()
        assert counts["admin_password"] == 1
        assert counts["admin_code"] == admin_service.CODE_ALERT_AFTER + 1


def test_studio_failures_and_lockouts_reach_the_daily_summary(app: Flask) -> None:
    app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    with app.test_request_context(PREFIX + "/"):
        studio_service.create_user("ada", STUDIO_PASSWORD, "editor")
    browser = app.test_client()
    for _ in range(5):
        browser.post(PREFIX + "/", data={"username": "ada", "password": "not it at all"})
    with app.app_context():
        counts = security_events.last_day()
        assert counts["studio_password"] == 5 and counts["studio_locked"] == 1
        studio_service.send_daily_summary()
    body = app.extensions["outbox"][-1].body
    assert "Wrong Studio passwords: 5" in body
    assert "Studio accounts locked for 15 minutes: 1" in body


def test_quiet_days_say_none(app: Flask) -> None:
    with app.app_context():
        assert "Failed sign-ins in the last 24 hours: none." in security_events.summary_lines()


def test_counter_outage_never_looks_like_a_quiet_day(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Unavailable:
        def get(self, _name: str) -> None:
            raise ConnectionError("test outage")

    monkeypatch.setattr(security_events, "get_redis", lambda: _Unavailable())
    with app.app_context():
        summary = security_events.summary_lines()
    assert "counters are unavailable" in summary
    assert "none" not in summary


def test_network_details_are_cleared_after_90_days_but_consents_keep_theirs(app: Flask) -> None:
    old = utcnow() - timedelta(days=retention.RETENTION_DAYS + 1)
    recent = utcnow() - timedelta(days=5)
    with app.app_context():
        db.session.add_all(
            [
                AdminAudit(actor_email="a@x.com", action="old", ip_address="203.0.113.9",
                           user_agent="Old browser", created_at=old),
                AdminAudit(actor_email="a@x.com", action="new", ip_address="203.0.113.10",
                           user_agent="New browser", created_at=recent),
                StudioAudit(actor_username="ada", action="old", ip_address="198.51.100.7",
                            user_agent="Phone", created_at=old),
            ]
        )  # fmt: skip
        db.session.commit()
        assert retention.clear_old_network_details() == 2
        rows = {a.action: a for a in db.session.scalars(db.select(AdminAudit))}
        assert rows["old"].ip_address is None and rows["old"].user_agent is None
        assert rows["old"].action == "old"  # the record itself stays
        assert rows["new"].ip_address == "203.0.113.10"
        studio_old = db.session.scalar(db.select(StudioAudit).where(StudioAudit.action == "old"))
        assert studio_old is not None and studio_old.ip_address is None
        assert retention.clear_old_network_details() == 0  # nothing left to clear
    # Consent records keep their IP: it is the proof that someone opted in.
    assert MarketingConsent not in retention._models()
    privacy = app.test_client().get("/privacy-policy/")
    assert privacy.status_code == 200
    assert b"cleared automatically after 90 days" in privacy.data
    assert b"marketing consent keep them as proof" in privacy.data

"""New-order alerts (email list + ntfy phone pushes), the admin order page, weekly backups."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import backup
from app.admin.models import AdminRole
from app.extensions import db
from app.orders import alerts
from app.orders.models import Order, OrderAlertRecipient
from app.orders.payments import send_order_emails
from tests.test_admin import admin_client
from tests.test_order_shipping import _paid_order


@pytest.fixture
def pushes(catalog_app: Flask, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
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
    catalog_app.config.update(
        STUDIO_APPROVAL_BACKEND="ntfy",
        STUDIO_NTFY_SERVER="https://ntfy.example",
        SITE_URL="https://shop.example.com",
        ORDER_NOTIFICATION_EMAIL="owner@example.com",
    )
    return sent


def test_owner_manages_the_alert_list_and_tests_it(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    owner = admin_client(catalog_app)
    owner.post("/admin/alerts/", data={"action": "add", "kind": "ntfy", "value": "gg-phone-k2m9x8",
                                       "label": "Mrs. Adetolu's phone"})  # fmt: skip
    owner.post(
        "/admin/alerts/", data={"action": "add", "kind": "email", "value": "shop@example.com"}
    )
    weak = owner.post(
        "/admin/alerts/",
        data={"action": "add", "kind": "ntfy", "value": "abc"},
        follow_redirects=True,
    )
    assert "hard to guess" in weak.get_data(as_text=True)
    page = owner.get("/admin/alerts/").get_data(as_text=True)
    assert "Mrs. Adetolu" in page and "shop@example.com" in page
    assert "gg-phone-k2m9x8" not in page  # the topic works like a password: only a hint shown
    with catalog_app.app_context():
        phone = db.session.scalar(
            db.select(OrderAlertRecipient).where(OrderAlertRecipient.kind == "ntfy")
        )
        assert phone is not None
        phone_id = phone.id
    owner.post("/admin/alerts/", data={"action": "test", "id": str(phone_id)})
    assert pushes[-1]["topic"] == "gg-phone-k2m9x8" and pushes[-1]["title"].startswith("Test")
    owner.post("/admin/alerts/", data={"action": "pause", "id": str(phone_id)})
    with catalog_app.app_context():
        assert alerts._active("ntfy") == []
    # Only the owner manages who is told about orders.
    support = admin_client(catalog_app, AdminRole.SUPPORT)
    assert support.get("/admin/alerts/").status_code == 403


def test_a_paid_order_alerts_every_phone_and_email(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    with catalog_app.app_context():
        db.session.add_all(
            [
                OrderAlertRecipient(kind="ntfy", value="gg-phone-k2m9x8", active=True),
                OrderAlertRecipient(kind="ntfy", value="gg-paused-topic", active=False),
                OrderAlertRecipient(kind="email", value="shop@example.com", active=True),
                OrderAlertRecipient(kind="email", value="OWNER@example.com", active=True),
            ]
        )
        db.session.commit()
    order_id = _paid_order(catalog_app)
    outbox = catalog_app.extensions.setdefault("outbox", [])
    before = len(outbox)
    with catalog_app.test_request_context("/"):
        order = db.session.get(Order, order_id)
        assert order is not None
        send_order_emails(order)
        public_id = order.public_id
    sent_to = sorted(message.to for message in outbox[before:])
    # The customer, the owner (once, even though listed twice) and the shop address.
    assert sent_to == sorted([order.email, "owner@example.com", "shop@example.com"])
    assert [push["topic"] for push in pushes] == ["gg-phone-k2m9x8"]  # paused one skipped
    push = pushes[0]
    assert public_id in push["title"] and "$" in push["title"]
    assert "Ankara" in push["message"] or "ankara" in push["message"].lower()
    assert push["click"] == f"https://shop.example.com/admin/orders/{order_id}"
    assert push["actions"][0]["url"] == push["click"]


def test_a_failing_alert_never_breaks_the_order(
    catalog_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_app.config.update(STUDIO_APPROVAL_BACKEND="ntfy", STUDIO_NTFY_SERVER="https://x")

    def broken(*_: Any, **__: Any) -> None:
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    with catalog_app.app_context():
        db.session.add(OrderAlertRecipient(kind="ntfy", value="gg-phone-k2m9x8", active=True))
        db.session.commit()
    order_id = _paid_order(catalog_app)
    with catalog_app.test_request_context("/"):
        order = db.session.get(Order, order_id)
        assert order is not None
        send_order_emails(order)  # no exception


def test_resending_the_confirmation_does_not_alert_the_team_again(
    catalog_app: Flask, pushes: list[dict[str, Any]]
) -> None:
    with catalog_app.app_context():
        db.session.add(OrderAlertRecipient(kind="ntfy", value="gg-phone-k2m9x8", active=True))
        db.session.commit()
    order_id = _paid_order(catalog_app)
    owner = admin_client(catalog_app)
    outbox = catalog_app.extensions.setdefault("outbox", [])
    before = len(outbox)
    owner.post(f"/admin/orders/{order_id}/resend")
    assert len(outbox) == before + 1 and pushes == []


def test_order_page_shows_photos_names_and_every_detail(catalog_app: Flask) -> None:
    order_id = _paid_order(catalog_app)
    page = admin_client(catalog_app, AdminRole.SUPPORT).get(f"/admin/orders/{order_id}")
    html = page.get_data(as_text=True)
    with catalog_app.app_context():
        order = db.session.get(Order, order_id)
        assert order is not None
        line = order.lines[0]
        for detail in (
            line.product_name,
            line.sku,
            order.first_name,
            order.email,
            order.address1,
            order.postcode,
            "Philadelphia time",
            "Total paid",
            "Shipping",
            "Tax",
        ):
            assert detail in html, detail
    assert "/email-images/products/ankara-blue/" in html  # the fabric photo
    assert "/product/ankara-blue/" in html and "Edit product" in html


# ---------- weekly database backups ----------


def _fake_tools(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(backup.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "env": kwargs.get("env") or {}})
        if "--file" in args:
            Path(args[args.index("--file") + 1]).write_bytes(b"PGDMP fake dump")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)


def test_backups_keep_the_newest_three_and_never_expose_the_password(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.config.update(
        BACKUP_DIR=str(tmp_path),
        SQLALCHEMY_DATABASE_URI="postgresql+psycopg://shop:s3cret-pw@db.example:5433/gg_site",
    )
    calls: list[dict[str, Any]] = []
    _fake_tools(monkeypatch, calls)
    saturday = datetime(2026, 10, 3, 7, 30, tzinfo=UTC)
    with app.app_context():
        for week in range(5):
            backup.run(saturday + timedelta(weeks=week))
        kept = [path.name for path in backup.existing()]
    assert kept == [
        "gg-db-20261031T073000Z.dump",
        "gg-db-20261024T073000Z.dump",
        "gg-db-20261017T073000Z.dump",
    ]
    dump = calls[0]
    assert "s3cret-pw" not in " ".join(dump["args"])  # never on the command line
    assert dump["env"]["PGPASSWORD"] == "s3cret-pw"
    assert dump["args"][dump["args"].index("--host") :][:2] == ["--host", "db.example"]
    assert "--format=custom" in dump["args"]
    assert calls[1]["args"][1] == "--list"  # each dump is read back before it is trusted


def test_a_failed_dump_is_reported_and_keeps_the_old_backups(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.config["BACKUP_DIR"] = str(tmp_path)
    (tmp_path / "gg-db-20261024T073000Z.dump").write_bytes(b"old")
    monkeypatch.setattr(backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "server version mismatch"),
    )
    result = app.test_cli_runner().invoke(args=["backup", "run"])
    assert result.exit_code != 0 and "server version mismatch" in result.output
    assert [p.name for p in tmp_path.iterdir()] == ["gg-db-20261024T073000Z.dump"]


def test_missing_pg_dump_is_said_plainly(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.config["BACKUP_DIR"] = str(tmp_path)
    monkeypatch.setattr(backup.shutil, "which", lambda name: None)
    result = app.test_cli_runner().invoke(args=["backup", "run"])
    assert result.exit_code != 0 and "pg_dump is not installed" in result.output

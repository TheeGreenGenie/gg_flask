from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from tests.conftest import FakeRedis


def test_live_is_ok(client: FlaskClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_ready_ok_when_dependencies_answer(client: FlaskClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_ready_503_when_redis_down(app: Flask, client: FlaskClient) -> None:
    app.extensions["redis"] = FakeRedis(healthy=False)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.get_json()["checks"] == {"database": "ok", "redis": "fail"}


def test_ready_503_when_redis_not_configured(app: Flask, client: FlaskClient) -> None:
    app.extensions["redis"] = None
    resp = client.get("/health/ready")
    assert resp.status_code == 503


def test_ready_503_when_database_unreachable() -> None:
    from app import create_app
    from app.config import TestingConfig

    cfg = TestingConfig()
    cfg.SQLALCHEMY_DATABASE_URI = "postgresql+psycopg://nobody:x@127.0.0.1:1/none"
    app = create_app(cfg)
    app.extensions["redis"] = FakeRedis()
    resp = app.test_client().get("/health/ready")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["checks"]["database"] == "fail"
    # No internal error details leak to the caller.
    assert set(body) == {"status", "checks"}


def test_health_rejects_post(client: FlaskClient) -> None:
    assert client.post("/health/live").status_code == 405

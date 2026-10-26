from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.catalog import manifest as mf
from app.extensions import db
from tests.catalog_fixtures import manifest, write_assets


class FakeRedis:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.counters: dict[str, int] = {}
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis down")
        return True

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def get(self, name: str) -> bytes | None:
        value = self.values.get(name)
        return value.encode() if value is not None else None

    def setex(self, name: str, seconds: int, value: str) -> None:
        self.values[name] = value

    def incr(self, name: str) -> int:
        self.values[name] = str(int(self.values.get(name, "0")) + 1)
        return int(self.values[name])


class FakePipeline:
    """Just enough of redis-py's pipeline for the rate limiter (no expiry)."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[str] = []

    def incr(self, name: str) -> None:
        self.ops.append(name)

    def expire(self, name: str, seconds: int, nx: bool = False) -> None:
        pass

    def execute(self) -> list[int]:
        if not self.redis.healthy:
            raise ConnectionError("redis down")
        out = []
        for name in self.ops:
            self.redis.counters[name] = self.redis.counters.get(name, 0) + 1
            out.append(self.redis.counters[name])
        return [*out, True]


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Flask]:
    app = create_app("testing")
    app.config["MEDIA_ROOT"] = str(tmp_path / "media")
    if app.extensions.get("redis") is None:
        app.extensions["redis"] = FakeRedis()
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    path = tmp_path / "assets"
    write_assets(path)
    return path


@pytest.fixture
def catalog_app(app: Flask, assets: Path) -> Flask:
    """App with the small test catalog loaded."""
    with app.app_context():
        mf.load(manifest(), assets, Path(app.config["MEDIA_ROOT"]))
    return app


@pytest.fixture
def shop(catalog_app: Flask) -> FlaskClient:
    return catalog_app.test_client()

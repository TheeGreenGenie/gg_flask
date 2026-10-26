from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import create_app


class FakeRedis:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis down")
        return True


@pytest.fixture
def app() -> Iterator[Flask]:
    app = create_app("testing")
    if app.extensions.get("redis") is None:
        app.extensions["redis"] = FakeRedis()
    yield app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()

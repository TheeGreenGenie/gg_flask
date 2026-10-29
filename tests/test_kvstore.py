"""The database stand-in for Redis behaves like the subset of Redis the app relies on."""

from __future__ import annotations

import pytest
from flask import Flask

from app import kvstore, ratelimit
from app.config import ConfigError, ProductionConfig
from app.extensions import db, init_redis
from app.kvstore import DatabaseKV, KVEntry


@pytest.fixture
def kv(app: Flask) -> DatabaseKV:
    store = DatabaseKV()
    app.extensions["redis"] = store
    return store


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 1_800_000_000.0
        monkeypatch.setattr(kvstore.time, "time", lambda: self.now)


def test_get_setex_and_expiry(app: Flask, kv: DatabaseKV, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(monkeypatch)
    with app.app_context():
        assert kv.get("missing") is None
        kv.setex("k", 60, '{"a": 1}')
        assert kv.get("k") == b'{"a": 1}'
        kv.setex("k", 60, b"replaced")
        assert kv.get("k") == b"replaced"
        clock.now += 61
        assert kv.get("k") is None


def test_counter_window_matches_redis(
    app: Flask, kv: DatabaseKV, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    with app.app_context():
        for expected in (1, 2, 3):
            pipe = kv.pipeline()
            pipe.incr("rl:x")
            pipe.expire("rl:x", 100, nx=True)
            assert pipe.execute()[0] == expected
        clock.now += 50  # nx: later hits do not push the window out
        pipe = kv.pipeline()
        pipe.incr("rl:x")
        pipe.expire("rl:x", 100, nx=True)
        assert pipe.execute() == [4, False]
        clock.now += 51  # window over: counting starts again
        pipe = kv.pipeline()
        pipe.incr("rl:x")
        pipe.expire("rl:x", 100, nx=True)
        assert pipe.execute() == [1, True]


def test_plain_incr_for_cache_versions(app: Flask, kv: DatabaseKV) -> None:
    with app.app_context():
        assert kv.incr("cache:catalog:version") == 1
        assert kv.incr("cache:catalog:version") == 2
        assert kv.get("cache:catalog:version") == b"2"
        assert kv.ping() is True


def test_counting_never_commits_the_requests_own_changes(app: Flask, kv: DatabaseKV) -> None:
    with app.test_request_context("/"):
        db.session.add(KVEntry(key="pending-request-change", value="x"))
        db.session.flush()
        assert ratelimit.allow("b", "k", 5, 60) is True
        db.session.rollback()
    with app.app_context():
        assert db.session.get(KVEntry, "pending-request-change") is None


def test_rate_limits_work_and_refuse_when_the_database_is_unreachable(
    app: Flask, kv: DatabaseKV, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app.test_request_context("/"):
        assert [ratelimit.allow("b", "k", 2, 60) for _ in range(3)] == [True, True, False]

        def broken(*args: object, **kwargs: object) -> None:
            raise ConnectionError("database down")

        monkeypatch.setattr(kvstore, "_incr", broken)
        assert ratelimit.allow("b2", "k", 2, 60, fail_closed=True) is False
        assert ratelimit.allow("b2", "k", 2, 60) is True  # fail-open buckets keep shopping alive


def test_configuration_selects_the_store(app: Flask) -> None:
    app.config.update(REDIS_URL=None, KV_BACKEND="database")
    init_redis(app)
    assert isinstance(app.extensions["redis"], DatabaseKV)
    app.config.update(KV_BACKEND=None)
    init_redis(app)
    assert app.extensions["redis"] is None


def test_production_needs_redis_or_the_database_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@/db")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("KV_BACKEND", raising=False)
    with pytest.raises(ConfigError, match="REDIS_URL or KV_BACKEND"):
        ProductionConfig().validate()
    monkeypatch.setenv("KV_BACKEND", "memory")
    with pytest.raises(ConfigError, match="KV_BACKEND must be"):
        ProductionConfig().validate()

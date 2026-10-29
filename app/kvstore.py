"""Database-backed stand-in for the few Redis operations the app uses.

Some hosts (SiteGround) offer no Redis. Rate limits must survive restarts and be shared by every
worker, and some of them fail closed, so an in-memory dict will not do. This keeps the exact
subset of the redis-py interface the app calls (get, setex, incr, expire, ping and a pipeline of
incr/expire) on a small table, using atomic upserts so concurrent requests count correctly.

Each operation runs in its own short transaction on its own connection, so counting a hit never
commits (or rolls back) the request's own pending changes.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import Float, Integer, String, Text, case, cast, delete, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

PURGE_CHANCE = 0.01  # share of counter writes that also delete expired rows


class KVEntry(db.Model):  # type: ignore[name-defined]
    __tablename__ = "kv_entries"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[float | None] = mapped_column(Float)  # Unix time; None = no expiry


TABLE = KVEntry.__table__


def _insert(conn: Connection) -> Any:
    if conn.dialect.name != "postgresql":
        raise RuntimeError("KV_BACKEND=database needs PostgreSQL")
    return postgresql.insert(TABLE)


def _live(now: float) -> Any:
    return (TABLE.c.expires_at.is_(None)) | (TABLE.c.expires_at > now)


def _get(conn: Connection, key: str) -> bytes | None:
    value = conn.scalar(select(TABLE.c.value).where(TABLE.c.key == key, _live(time.time())))
    return value.encode() if value is not None else None


def _setex(conn: Connection, key: str, seconds: int, value: Any) -> bool:
    text = value.decode() if isinstance(value, bytes) else str(value)
    expires = time.time() + seconds
    stmt = _insert(conn).values(key=key, value=text, expires_at=expires)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": text, "expires_at": expires}
        )
    )
    return True


def _incr(conn: Connection, key: str) -> int:
    now = time.time()
    expired = TABLE.c.expires_at.is_not(None) & (TABLE.c.expires_at <= now)
    stmt = _insert(conn).values(key=key, value="1", expires_at=None)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={
            # An expired counter starts again from 1 with no expiry, as in Redis.
            "value": case((expired, "1"), else_=cast(cast(TABLE.c.value, Integer) + 1, Text)),
            "expires_at": case((expired, None), else_=TABLE.c.expires_at),
        },
    ).returning(TABLE.c.value)
    count = int(conn.execute(stmt).scalar_one())
    if random.random() < PURGE_CHANCE:  # noqa: S311 - housekeeping, not security
        conn.execute(delete(TABLE).where(TABLE.c.expires_at <= now))
    return count


def _expire(conn: Connection, key: str, seconds: int, nx: bool = False) -> bool:
    condition = TABLE.c.key == key
    if nx:
        condition &= TABLE.c.expires_at.is_(None)
    result = conn.execute(update(TABLE).where(condition).values(expires_at=time.time() + seconds))
    return bool(result.rowcount)


class DatabaseKV:
    """The redis-py subset used by app.ratelimit, app.cache and app.address_lookup."""

    def _run(self, op: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with db.engine.begin() as conn:
            return op(conn, *args, **kwargs)

    def get(self, name: str) -> bytes | None:
        return self._run(_get, name)

    def setex(self, name: str, seconds: int, value: Any) -> bool:
        return self._run(_setex, name, seconds, value)

    def incr(self, name: str) -> int:
        return self._run(_incr, name)

    def expire(self, name: str, seconds: int, nx: bool = False) -> bool:
        return self._run(_expire, name, seconds, nx=nx)

    def ping(self) -> bool:
        with db.engine.connect() as conn:
            conn.execute(select(TABLE.c.key).limit(1))
        return True

    def pipeline(self) -> _Pipeline:
        return _Pipeline()


class _Pipeline:
    """Queues incr/expire calls and runs them in one transaction, like a Redis MULTI."""

    def __init__(self) -> None:
        self._ops: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []

    def incr(self, name: str) -> _Pipeline:
        self._ops.append((_incr, (name,), {}))
        return self

    def expire(self, name: str, seconds: int, nx: bool = False) -> _Pipeline:
        self._ops.append((_expire, (name, seconds), {"nx": nx}))
        return self

    def execute(self) -> list[Any]:
        with db.engine.begin() as conn:
            return [op(conn, *args, **kwargs) for op, args, kwargs in self._ops]

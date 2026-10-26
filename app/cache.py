"""Small Redis-backed JSON cache for catalog data shared by every visitor.

Keys include a catalog version number; `flask catalog load` bumps it, so a catalog change
is visible immediately instead of waiting for entries to expire. Like the rate limiter this
fails open: if Redis is unavailable the value is simply computed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from flask import g

from app.extensions import get_redis

log = logging.getLogger(__name__)

VERSION_KEY = "cache:catalog:version"
DEFAULT_TTL = 300  # seconds; also bounds staleness of stock-dependent lists


def catalog_version() -> str:
    if "catalog_cache_version" not in g:
        version = "0"
        client = get_redis()
        if client is not None:
            try:
                raw = client.get(VERSION_KEY)
                version = raw.decode() if isinstance(raw, bytes) else str(raw or "0")
            except Exception:  # noqa: BLE001
                log.warning("cache_unavailable")
        g.catalog_cache_version = version
    return str(g.catalog_cache_version)


def bump_catalog_version() -> None:
    """Invalidate every catalog cache entry (call after catalog changes)."""
    client = get_redis()
    if client is None:
        return
    try:
        client.incr(VERSION_KEY)
    except Exception:  # noqa: BLE001
        log.warning("cache_unavailable")
    g.pop("catalog_cache_version", None)


def cached_json[T](name: str, compute: Callable[[], T], ttl: int = DEFAULT_TTL) -> T:
    """Return a JSON-serialisable value from the cache, computing and storing it on a miss."""
    client = get_redis()
    if client is None:
        return compute()
    key = f"cache:catalog:v{catalog_version()}:{name}"
    try:
        raw = client.get(key)
        if raw is not None:
            value: Any = json.loads(raw)
            return value
    except Exception:  # noqa: BLE001
        log.warning("cache_unavailable")
        return compute()
    value = compute()
    try:
        client.setex(key, ttl, json.dumps(value, separators=(",", ":")))
    except Exception:  # noqa: BLE001
        log.warning("cache_unavailable")
    return value

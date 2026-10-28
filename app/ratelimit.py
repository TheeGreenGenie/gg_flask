"""Small Redis fixed-window rate limiter (security doc §3: Redis-backed application limits).

Fails open by default when Redis is unavailable: the limits protect against abuse, while hard
caps (e.g. wishlist size) still apply in the database layer. Sign-in to the private areas and
anything that sends email pass `fail_closed=True` instead (monitoring review): during a Redis
outage those are refused rather than left unthrottled.
"""

from __future__ import annotations

import logging

from flask import request

from app.extensions import get_redis

log = logging.getLogger(__name__)


def client_key() -> str:
    # remote_addr is the trusted client IP: ProxyFix only honours configured proxy hops.
    return request.remote_addr or "unknown"


def allow(
    bucket: str, key: str, limit: int, window_seconds: int, *, fail_closed: bool = False
) -> bool:
    """Count one hit; False once `limit` hits happen within the window."""
    client = get_redis()
    if client is None:
        if fail_closed:
            log.warning("rate_limit_unavailable_refused", extra={"bucket": bucket})
        return not fail_closed
    name = f"rl:{bucket}:{key}"
    try:
        pipe = client.pipeline()
        pipe.incr(name)
        pipe.expire(name, window_seconds, nx=True)
        count = int(pipe.execute()[0])
    except Exception:  # noqa: BLE001 — never break the page because Redis hiccupped
        log.warning("rate_limit_unavailable", extra={"bucket": bucket})
        return not fail_closed
    return count <= limit

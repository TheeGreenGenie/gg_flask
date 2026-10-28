"""Liveness and readiness endpoints for the load balancer and uptime checks.

Responses never include error details; failures are logged server-side. Which dependency failed
is shown only to requests from the server itself (monitoring review: public callers learn only
ok/fail), and the readiness result is reused for a few seconds so probes can't load the database.
"""

from __future__ import annotations

import logging
import time

from flask import Blueprint, Response, current_app, jsonify, request
from sqlalchemy import text

from app.extensions import csrf, db, get_redis

bp = Blueprint("health", __name__, url_prefix="/health")
csrf.exempt(bp)

log = logging.getLogger(__name__)


@bp.get("/live")
def live() -> Response:
    """The process is up. No dependency checks."""
    return jsonify(status="ok")


LOCAL = frozenset({"127.0.0.1", "::1"})


@bp.get("/ready")
def ready() -> tuple[Response, int]:
    """The app can serve traffic: database and Redis both answer."""
    cache_seconds = float(current_app.config.get("HEALTH_CACHE_SECONDS", 5))
    cached = current_app.extensions.get("health_ready")
    if cached and time.monotonic() - cached[0] < cache_seconds:
        checks = cached[1]
    else:
        checks = _check()
        current_app.extensions["health_ready"] = (time.monotonic(), checks)
    healthy = all(v == "ok" for v in checks.values())
    body: dict[str, object] = {"status": "ok" if healthy else "fail"}
    if request.remote_addr in LOCAL:
        body["checks"] = checks
    return jsonify(body), 200 if healthy else 503


def _check() -> dict[str, str]:
    checks: dict[str, str] = {}

    try:
        db.session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        log.exception("readiness_database_failed")
        checks["database"] = "fail"
    finally:
        db.session.rollback()

    redis = get_redis()
    try:
        if redis is None:
            raise RuntimeError("redis not configured")
        redis.ping()
        checks["redis"] = "ok"
    except Exception:
        log.exception("readiness_redis_failed")
        checks["redis"] = "fail"
    return checks

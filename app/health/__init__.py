"""Liveness and readiness endpoints for the load balancer and uptime checks.

Responses never include error details; failures are logged server-side.
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, jsonify
from sqlalchemy import text

from app.extensions import csrf, db, get_redis

bp = Blueprint("health", __name__, url_prefix="/health")
csrf.exempt(bp)

log = logging.getLogger(__name__)


@bp.get("/live")
def live() -> Response:
    """The process is up. No dependency checks."""
    return jsonify(status="ok")


@bp.get("/ready")
def ready() -> tuple[Response, int]:
    """The app can serve traffic: database and Redis both answer."""
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

    healthy = all(v == "ok" for v in checks.values())
    return jsonify(status="ok" if healthy else "fail", checks=checks), 200 if healthy else 503

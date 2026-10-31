"""Failed privileged sign-ins, counted so the owner can see them (security review, 2026-10-30).

Wrong admin/Studio passwords, wrong admin codes and Studio lockouts are counted per hour in the
shared key-value store (Redis or the database table on SiteGround); the daily summary email
reports the last 24 hours. Nothing identifying is stored here: only counts. A counting failure never
rejects a sign-in; if the store is down the event is simply not counted and the summary warns that
the counters are unavailable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.catalog.models import utcnow
from app.ratelimit import get_redis

_log = logging.getLogger(__name__)

KINDS = {
    "admin_password": "Wrong admin passwords",
    "admin_code": "Wrong admin sign-in codes",
    "studio_password": "Wrong Studio passwords",
    "studio_locked": "Studio accounts locked for 15 minutes",
}
KEEP_SECONDS = 2 * 24 * 3600


def _key(kind: str, hour: datetime) -> str:
    return f"sec:fail:{kind}:{hour:%Y%m%d%H}"


def record(kind: str, now: datetime | None = None) -> None:
    client = get_redis()
    if client is None or kind not in KINDS:
        return
    name = _key(kind, now or utcnow())
    try:
        pipe = client.pipeline()
        pipe.incr(name)
        pipe.expire(name, KEEP_SECONDS, nx=True)
        pipe.execute()
    except Exception:  # noqa: BLE001 - never break a sign-in because counting failed
        _log.warning("security_event_not_counted", extra={"kind": kind})


def _last_day(now: datetime | None = None) -> tuple[dict[str, int], bool]:
    client = get_redis()
    now = now or utcnow()
    totals = dict.fromkeys(KINDS, 0)
    if client is None:
        return totals, False
    for kind in KINDS:
        for hours_ago in range(24):
            try:
                raw = client.get(_key(kind, now - timedelta(hours=hours_ago)))
            except Exception:  # noqa: BLE001
                _log.warning("security_summary_unavailable")
                return totals, False
            totals[kind] += int(raw or 0)
    return totals, True


def last_day(now: datetime | None = None) -> dict[str, int]:
    """Return counts for callers that need numbers; availability is shown in the summary."""
    return _last_day(now)[0]


def summary_lines(now: datetime | None = None) -> str:
    totals, available = _last_day(now)
    if not available:
        return (
            "Failed sign-in counters are unavailable. Check the database/KV service and do not "
            "treat this as a quiet day.\n\n"
        )
    if not any(totals.values()):
        return "Failed sign-ins in the last 24 hours: none.\n\n"
    lines = "".join(f"- {KINDS[kind]}: {count}\n" for kind, count in totals.items() if count)
    note = (
        "Bots guessing passwords is normal once a shop is public; rate limits and sign-in codes "
        "stop them. A phone alert is sent after a correct admin password is followed by three "
        "wrong codes in 15 minutes.\n\n"
    )
    return "Failed sign-ins in the last 24 hours:\n" + lines + note

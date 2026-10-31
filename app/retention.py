"""Network details are kept for 90 days, then cleared (security review, 2026-10-30).

Sign-in requests, audit trails and customer posts record the visitor's IP address and browser
("user agent") so abuse and break-ins can be investigated. After RETENTION_DAYS they are set to
empty; the records themselves (who changed what, when) stay. Marketing consents keep theirs:
they are the evidence that someone opted in.

Run daily as part of `flask studio daily-summary` (already scheduled), so there is no extra job.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, update

from app.catalog.models import utcnow
from app.extensions import db

RETENTION_DAYS = 90


def _models() -> list[Any]:
    from app.admin.models import AdminAudit, AdminLoginRequest
    from app.reviews.models import ProductPost
    from app.studio.models import StudioAudit, StudioKeyEnrollment, StudioLoginRequest

    return [
        AdminAudit,
        AdminLoginRequest,
        ProductPost,
        StudioAudit,
        StudioKeyEnrollment,
        StudioLoginRequest,
    ]


def clear_old_network_details(now: datetime | None = None) -> int:
    """Blank IP addresses and user agents older than RETENTION_DAYS; returns rows changed."""
    cutoff = (now or utcnow()) - timedelta(days=RETENTION_DAYS)
    changed = 0
    for model in _models():
        values: dict[str, None] = {"ip_address": None}
        has_agent = hasattr(model, "user_agent")
        if has_agent:
            values["user_agent"] = None
        holding = [model.ip_address.is_not(None)]
        if has_agent:
            holding.append(model.user_agent.is_not(None))
        result = db.session.execute(
            update(model)
            .where(model.created_at < cutoff, or_(*holding))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        changed += int(result.rowcount or 0)  # type: ignore[attr-defined]
    db.session.commit()
    return changed

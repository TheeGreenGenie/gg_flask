"""Mailchimp newsletter adapter: double opt-in unless the shop has already proven the address."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

from flask import current_app

SUBSCRIBED = "subscribed"
PENDING = (
    "pending"  # Mailchimp emails a confirmation link; the address joins only once it is tapped
)


class MarketingError(RuntimeError):
    pass


REFUSED = "We couldn't sign up that email address. Please check it and try again."


class _Refused(MarketingError):
    """Mailchimp answered 400, e.g. it will not re-subscribe someone who unsubscribed."""


def available() -> bool:
    return all(
        current_app.config.get(name)
        for name in ("MAILCHIMP_API_KEY", "MAILCHIMP_SERVER_PREFIX", "MAILCHIMP_LIST_ID")
    )


def subscribe(email: str, *, confirmed: bool = False) -> str:
    """Add `email` to the audience and return SUBSCRIBED or PENDING.

    Everyone gets double opt-in. `confirmed=True` skips the confirmation email and is only for an
    address the shop has already proven belongs to the person asking: a signed-in, verified
    customer with a paid order, subscribing their own account email.
    """
    if not available():
        raise MarketingError("Newsletter signup is not configured yet.")
    if confirmed:
        try:
            _put(email, {"email_address": email, "status_if_new": SUBSCRIBED, "status": SUBSCRIBED})
            return SUBSCRIBED
        except _Refused:
            # Someone who unsubscribed must opt in again by email; Mailchimp sends the link.
            _put(email, {"email_address": email, "status": PENDING})
            return PENDING
    # Existing members keep their status: a resubmission never unsubscribes or re-confirms anyone.
    _put(email, {"email_address": email, "status_if_new": PENDING})
    return PENDING


def _put(email: str, payload: dict[str, str]) -> None:
    digest = hashlib.md5(email.lower().encode(), usedforsecurity=False).hexdigest()
    prefix = current_app.config["MAILCHIMP_SERVER_PREFIX"]
    list_id = current_app.config["MAILCHIMP_LIST_ID"]
    url = f"https://{prefix}.api.mailchimp.com/3.0/lists/{list_id}/members/{digest}"
    headers = {
        "Authorization": f"Bearer {current_app.config['MAILCHIMP_API_KEY']}",
        "Content-Type": "application/json",
    }
    body = json.dumps(payload).encode()
    injected = current_app.extensions.get("marketing_http")
    if injected:
        status = injected("PUT", url, headers, body) or 200  # tests return a status code or None
        if status == 400:
            raise _Refused(REFUSED)
        return
    request = urllib.request.Request(  # noqa: S310
        url, data=body, headers=headers, method="PUT"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            return
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise _Refused(REFUSED) from exc
        raise MarketingError("Newsletter signup is temporarily unavailable.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MarketingError("Newsletter signup is temporarily unavailable.") from exc

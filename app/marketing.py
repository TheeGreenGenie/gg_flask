"""Mailchimp double-opt-in newsletter adapter."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

from flask import current_app


class MarketingError(RuntimeError):
    pass


def available() -> bool:
    return all(
        current_app.config.get(name)
        for name in ("MAILCHIMP_API_KEY", "MAILCHIMP_SERVER_PREFIX", "MAILCHIMP_LIST_ID")
    )


def subscribe(email: str) -> None:
    if not available():
        raise MarketingError("Newsletter signup is not configured yet.")
    digest = hashlib.md5(email.lower().encode(), usedforsecurity=False).hexdigest()
    prefix = current_app.config["MAILCHIMP_SERVER_PREFIX"]
    list_id = current_app.config["MAILCHIMP_LIST_ID"]
    url = f"https://{prefix}.api.mailchimp.com/3.0/lists/{list_id}/members/{digest}"
    headers = {
        "Authorization": f"Bearer {current_app.config['MAILCHIMP_API_KEY']}",
        "Content-Type": "application/json",
    }
    body = json.dumps({"email_address": email, "status_if_new": "pending"}).encode()
    injected = current_app.extensions.get("marketing_http")
    if injected:
        injected("PUT", url, headers, body)
        return
    request = urllib.request.Request(  # noqa: S310
        url, data=body, headers=headers, method="PUT"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            return
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MarketingError("Newsletter signup is temporarily unavailable.") from exc

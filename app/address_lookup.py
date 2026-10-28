"""Address autocomplete for checkout and the address book, through a server-side HERE proxy.

Ported from the old WordPress site (owner, 2026-10-28): HERE Autocomplete, at least 3
characters, 5 suggestions, 30 lookups per 10 minutes per visitor IP. Every lookup costs the
business money, so this is stricter than the old version:

- the IP is the trusted client address (the old code believed any X-Forwarded-For header, so
  one script could pretend to be a new visitor on every request);
- a site-wide daily cap on paid lookups (ADDRESS_LOOKUP_DAILY_CAP) bounds the bill even when
  many IPs are used at once;
- identical searches are answered from a 24-hour Redis cache for free;
- it fails closed: without Redis (no way to count) or without a key it does nothing and the
  shopper simply types the address;
- results only for the countries we ship to; searches are never logged (they are addresses).
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from app import ratelimit
from app.extensions import get_redis

log = logging.getLogger(__name__)

HERE_URL = "https://autocomplete.search.hereapi.com/v1/autocomplete"
MIN_CHARS = 3
MAX_CHARS = 120
RESULTS = 5
IP_LIMIT, IP_WINDOW = 30, 600  # same as the WordPress site: 30 per 10 minutes per IP
CACHE_SECONDS = 24 * 3600
ALPHA3 = {"US": "USA", "CA": "CAN", "MX": "MEX"}
ALPHA2 = {value: key for key, value in ALPHA3.items()}


class Unavailable(Exception):
    """Autocomplete is switched off (no key, no Redis, or the daily cap is used up)."""


class Limited(Exception):
    """This visitor is going too fast."""


def enabled() -> bool:
    return bool(current_app.config.get("HERE_API_KEY"))


def _countries(selected: str) -> list[str]:
    shipped = [c for c in current_app.config.get("SUPPORTED_COUNTRIES", ()) if c in ALPHA3]
    if selected.upper() in shipped:
        return [selected.upper()]
    return shipped or list(ALPHA3)


def _count(client: Any, name: str, limit: int, window: int) -> bool:
    """Fixed-window counter that refuses (instead of allowing) when Redis misbehaves."""
    try:
        pipe = client.pipeline()
        pipe.incr(name)
        pipe.expire(name, window, nx=True)
        return int(pipe.execute()[0]) <= limit
    except Exception:  # noqa: BLE001 - a paid API must not run uncounted
        log.warning("address_lookup_counter_unavailable")
        raise Unavailable from None


def suggest(query_raw: str, country_raw: str = "") -> list[dict[str, str]]:
    query = " ".join((query_raw or "").split())[:MAX_CHARS]
    if len(query) < MIN_CHARS:
        return []
    key = current_app.config.get("HERE_API_KEY")
    client = get_redis()
    if not key or client is None:
        raise Unavailable
    if not _count(client, f"rl:address:{ratelimit.client_key()}", IP_LIMIT, IP_WINDOW):
        raise Limited
    countries = _countries(country_raw or "")
    digest = hashlib.sha256(f"{','.join(countries)}|{query.lower()}".encode()).hexdigest()
    cache_key = f"address:v1:{digest}"
    try:
        cached = client.get(cache_key)
    except Exception:  # noqa: BLE001
        raise Unavailable from None
    if cached:
        return list(json.loads(cached))
    cap = int(current_app.config.get("ADDRESS_LOOKUP_DAILY_CAP", 1000))
    day = datetime.now(UTC).strftime("%Y%m%d")
    if not _count(client, f"address:day:{day}", cap, 2 * 86400):
        log.warning("address_lookup_daily_cap_reached cap=%s", cap)
        raise Unavailable
    items = _here(str(key), query, countries)
    try:
        client.setex(cache_key, CACHE_SECONDS, json.dumps(items))
    except Exception:  # noqa: BLE001 - caching is a saving, not a requirement
        log.warning("address_lookup_cache_unavailable")
    return items


def _here(key: str, query: str, countries: list[str]) -> list[dict[str, str]]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "apiKey": key,
            "limit": RESULTS,
            "types": "houseNumber,street",
            "lang": "en-US",
            "in": "countryCode:" + ",".join(ALPHA3[c] for c in countries),
        }
    )
    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS host
        f"{HERE_URL}?{params}", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:  # noqa: S310 - fixed HTTPS host
            data = json.loads(response.read(200_000))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Never log the URL: it contains the API key and the shopper's address.
        log.warning("address_lookup_failed reason=%s", type(exc).__name__)
        return []
    return [item for item in map(_tidy, data.get("items") or []) if item][:RESULTS]


def _tidy(item: dict[str, Any]) -> dict[str, str] | None:
    """Only the fields the form needs, in our own format (2-letter country, state code)."""
    address = item.get("address") or {}
    country = ALPHA2.get(str(address.get("countryCode", "")).upper())
    line1 = " ".join(
        part for part in (address.get("houseNumber"), address.get("street")) if part
    ).strip()
    if not country or not line1:
        return None
    return {
        "label": str(address.get("label") or item.get("title") or line1)[:200],
        "address1": line1[:200],
        "city": str(address.get("city") or address.get("county") or "")[:100],
        "region": str(address.get("stateCode") or address.get("state") or "")[:100],
        "postcode": str(address.get("postalCode") or "")[:20],
        "country": country,
    }

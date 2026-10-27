"""Jinja filters and globals shared by every template."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flask import Flask

from app import site_content


def money(cents: int, currency: str = "USD") -> str:
    """Format integer minor units for display. Only USD is sold today."""
    if currency != "USD":
        raise ValueError(f"Unsupported currency {currency!r}")
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(int(cents)), 100)
    return f"{sign}${dollars:,}.{rem:02d}"


def long_date(iso: str) -> str:
    """'2024-10-01' -> 'October 1, 2024'."""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d:%B} {d.day}, {d.year}"


def phone_uri(phone: str) -> str:
    return "tel:" + "".join(ch for ch in phone if ch.isdigit() or ch == "+")


def init_templating(app: Flask) -> None:
    app.add_template_filter(money, "money")
    app.add_template_filter(phone_uri, "phone_uri")
    app.add_template_filter(long_date, "long_date")

    @app.context_processor
    def site_globals() -> dict[str, Any]:
        return {
            "site": site_content,
            "current_year": datetime.now(UTC).year,
        }

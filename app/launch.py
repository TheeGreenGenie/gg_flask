"""Read-only launch preflight for dependencies and commerce configuration."""

from __future__ import annotations

from dataclasses import dataclass

import click
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import Flask, current_app
from sqlalchemy import text

from app import mail
from app.extensions import db, get_redis
from app.orders.models import PaymentProvider
from app.orders.payments import provider_configured


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def checks() -> list[Check]:
    results: list[Check] = []
    try:
        db.session.execute(text("SELECT 1"))
        results.append(Check("PASS", "database", "connection succeeded"))
    except Exception:
        results.append(Check("FAIL", "database", "connection failed"))
    finally:
        db.session.rollback()

    redis = get_redis()
    try:
        if redis is None:
            raise RuntimeError
        redis.ping()
        results.append(Check("PASS", "redis", "connection succeeded"))
    except Exception:
        results.append(Check("FAIL", "redis", "not configured or unavailable"))

    try:
        migrate = current_app.extensions["migrate"].migrate
        script = ScriptDirectory.from_config(migrate.get_config())
        expected = set(script.get_heads())
        with db.engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        if current == expected:
            results.append(Check("PASS", "migrations", ", ".join(sorted(current))))
        else:
            results.append(
                Check(
                    "FAIL",
                    "migrations",
                    f"database={','.join(sorted(current)) or 'none'}; "
                    f"expected={','.join(sorted(expected))}",
                )
            )
    except Exception:
        results.append(Check("FAIL", "migrations", "could not read migration state"))

    results.append(
        Check(
            "PASS" if mail.available() else "FAIL",
            "transactional email",
            "configured" if mail.available() else "configure SMTP and run `flask mail test`",
        )
    )
    payments = [provider.value for provider in PaymentProvider if provider_configured(provider)]
    results.append(
        Check(
            "PASS" if payments else "FAIL",
            "payments",
            ", ".join(payments) if payments else "configure Stripe or PayPal",
        )
    )
    countries = tuple(current_app.config.get("SUPPORTED_COUNTRIES") or ())
    results.append(
        Check(
            "PASS" if countries else "FAIL",
            "destinations",
            ", ".join(countries) if countries else "SUPPORTED_COUNTRIES is empty",
        )
    )
    shipping = current_app.config.get("SHIPPING_BACKEND")
    shipping_ready = bool(
        (shipping == "flat" and current_app.config.get("FLAT_SHIPPING_CENTS") is not None)
        or (shipping == "shippo" and current_app.config.get("SHIPPO_API_TOKEN"))
    )
    results.append(
        Check(
            "PASS" if shipping_ready else "FAIL",
            "shipping",
            str(shipping) if shipping_ready else "configure a flat price or Shippo token",
        )
    )
    tax = current_app.config.get("TAX_BACKEND")
    results.append(
        Check(
            "PASS" if tax in {"none", "stripe"} else "FAIL",
            "tax",
            str(tax) if tax else "choose TAX_BACKEND=stripe or explicitly choose none",
        )
    )
    results.extend(
        (
            Check("MANUAL", "policies", "owner/legal review and approval still required"),
            Check("MANUAL", "backups", "restore the latest backup into a scratch database"),
            Check("MANUAL", "checkout", "complete Stripe and PayPal sandbox orders"),
        )
    )
    return results


def init_launch(app: Flask) -> None:
    @app.cli.group("launch")
    def launch_cli() -> None:
        """Launch-readiness diagnostics."""

    @launch_cli.command("check")
    def check() -> None:
        """Check dependencies and configuration without changing data."""
        results = checks()
        for result in results:
            click.echo(f"{result.level:<6} {result.name:<20} {result.detail}")
        failed = sum(result.level == "FAIL" for result in results)
        if failed:
            raise click.ClickException(f"{failed} launch-blocking check(s) failed.")
        click.echo("Automated checks passed; complete the MANUAL checks before launch.")

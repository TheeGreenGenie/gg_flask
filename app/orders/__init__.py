"""Order commands."""

from __future__ import annotations

import click
from flask import Flask

from app.orders import service


def init_orders(app: Flask) -> None:
    @app.cli.group("orders")
    def orders_cli() -> None:
        """Order lifecycle tools."""

    @orders_cli.command("release-expired-holds")
    def release_expired_holds() -> None:
        """Release expired stock holds and cancel their unpaid orders."""
        click.echo(f"Released {service.release_expired_holds()} expired hold(s).")

    @orders_cli.command("sync-refunds")
    def sync_refunds() -> None:
        """Ask Stripe/PayPal about pending refunds and mark the completed ones refunded."""
        from app.orders import payments

        counts = payments.sync_refunds()
        click.echo(", ".join(f"{name}: {number}" for name, number in counts.items()))

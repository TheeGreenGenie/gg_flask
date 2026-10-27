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

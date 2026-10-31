"""Weekly database backups (owner, 2026-10-30): every weekend, keeping three weeks.

`flask backup run` dumps PostgreSQL with pg_dump (compressed custom format, restorable whole or
table by table with pg_restore), checks the file can be read back, then deletes all but the
newest KEEP dumps. Run it from cron on Saturdays (deploy/siteground/SITEGROUND.md section 7); the
heartbeat lets `flask jobs check` and the daily summary warn when a weekend is missed.

The password reaches pg_dump through the environment (PGPASSWORD), never the command line, so it
cannot show up in a process list or a log. Files are readable only by the site's own user.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - fixed pg_dump/pg_restore commands, no shell
from datetime import UTC, datetime
from pathlib import Path

import click
from flask import Flask, current_app
from sqlalchemy.engine import make_url

KEEP = 3  # weekly backups = three weeks
PREFIX = "gg-db-"
TIMEOUT = 30 * 60


class BackupError(RuntimeError):
    pass


def backup_dir() -> Path:
    return Path(current_app.config.get("BACKUP_DIR") or Path(current_app.instance_path) / "backups")


def existing() -> list[Path]:
    """Finished dumps, newest first."""
    folder = backup_dir()
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{PREFIX}*.dump"), key=lambda p: p.name, reverse=True)


def _connection() -> tuple[list[str], dict[str, str]]:
    url = make_url(str(current_app.config["SQLALCHEMY_DATABASE_URI"]))
    if not url.drivername.startswith("postgresql") or not url.database:
        raise BackupError("DATABASE_URL must point at a PostgreSQL database.")
    args = ["--dbname", url.database]
    if url.host:
        args += ["--host", url.host]
    if url.port:
        args += ["--port", str(url.port)]
    if url.username:
        args += ["--username", url.username]
    env = {**os.environ, "PGCONNECT_TIMEOUT": "15"}
    if url.password:
        env["PGPASSWORD"] = str(url.password)
    return args, env


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} is not installed on this server, so no backup was made.")
    return found


def run(now: datetime | None = None) -> Path:
    now = now or datetime.now(UTC)
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    target = folder / f"{PREFIX}{now:%Y%m%dT%H%M%SZ}.dump"
    partial = target.with_suffix(".part")
    args, env = _connection()
    dump = subprocess.run(  # noqa: S603
        [_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges",
         "--file", str(partial), *args],
        env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False,
    )  # fmt: skip
    if dump.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        detail = (dump.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise BackupError(f"pg_dump failed: {detail[0][:300]}")
    os.chmod(partial, 0o600)
    check = subprocess.run(  # noqa: S603 - prove the dump is readable before trusting it
        [_tool("pg_restore"), "--list", str(partial)],
        capture_output=True, text=True, timeout=TIMEOUT, check=False,
    )  # fmt: skip
    if check.returncode != 0:
        partial.unlink(missing_ok=True)
        raise BackupError("The new backup could not be read back, so it was discarded.")
    partial.replace(target)
    prune()
    return target


def prune() -> list[Path]:
    """Delete everything but the newest KEEP dumps (and any half-written leftovers)."""
    removed = []
    for old in existing()[KEEP:]:
        old.unlink(missing_ok=True)
        removed.append(old)
    for leftover in backup_dir().glob(f"{PREFIX}*.part"):
        leftover.unlink(missing_ok=True)
        removed.append(leftover)
    return removed


def init_backup(app: Flask) -> None:
    @app.cli.group("backup")
    def backup_cli() -> None:
        """Weekly database backups."""

    @backup_cli.command("run")
    def run_command() -> None:
        """Back up the database now and keep only the newest three backups."""
        try:
            path = run()
        except (BackupError, subprocess.TimeoutExpired, OSError) as exc:
            raise click.ClickException(str(exc)) from None
        size = path.stat().st_size / (1024 * 1024)
        click.echo(f"Backup saved: {path.name} ({size:.1f} MB). Keeping the newest {KEEP}.")

    @backup_cli.command("list")
    def list_command() -> None:
        """Show the backups kept on this server, newest first."""
        found = existing()
        if not found:
            click.echo(f"No backups yet in {backup_dir()}.")
        for path in found:
            size = path.stat().st_size / (1024 * 1024)
            click.echo(f"{path.name}  {size:.1f} MB")

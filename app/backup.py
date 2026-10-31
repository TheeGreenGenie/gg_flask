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
import re
import shutil
import subprocess  # noqa: S404 - fixed pg_dump/pg_restore commands, no shell
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from flask import Flask, current_app
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

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


# ---------- proving a backup restores (owner, 2026-10-30) ----------
# `flask backup test-restore SPARE_DB` loads a backup into a separate, empty database (created in
# Site Tools) and compares row counts with the live one. It refuses to touch the live database.

CHECK_TABLES = (
    "products",
    "variants",
    "product_images",
    "categories",
    "collections",
    "orders",
    "order_lines",
    "users",
    "blog_posts",
    "admin_users",
    "studio_users",
)
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,63}$")


def _counts(url: Any) -> dict[str, int | None]:
    """Rows per CHECK_TABLES table (None when the table is missing)."""
    from sqlalchemy import create_engine, text

    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 15})
    found: dict[str, int | None] = {}
    try:
        with engine.connect() as conn:
            for table in CHECK_TABLES:
                exists = conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar()
                found[table] = (
                    int(conn.execute(text(f'SELECT count(*) FROM "{table}"')).scalar() or 0)  # noqa: S608 - fixed names
                    if exists
                    else None
                )
    finally:
        engine.dispose()
    return found


RestoreRow = tuple[str, int | None, int | None]  # table, rows in the restored copy, rows live


def restore_check(spare_db: str, source: Path | None = None) -> tuple[Path, list[RestoreRow]]:
    live = make_url(str(current_app.config["SQLALCHEMY_DATABASE_URI"]))
    if not DB_NAME_RE.fullmatch(spare_db):
        raise BackupError("Give the spare database's name (letters, numbers, underscores).")
    if spare_db == live.database:
        raise BackupError("That is the live database. Create a separate, empty one for the test.")
    if source is None:
        kept = existing()
        if not kept:
            raise BackupError("No backups yet. Run `backup run` first.")
        source = kept[0]
    args, env = _connection()
    args[args.index("--dbname") + 1] = spare_db
    restore = subprocess.run(  # noqa: S603
        [_tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges",
         *args, str(source)],
        env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False,
    )  # fmt: skip
    spare = live.set(database=spare_db)
    try:
        restored = _counts(spare)
    except Exception as exc:  # cannot connect: the spare database or its user is not set up
        raise BackupError(f"Could not open {spare_db}: {type(exc).__name__}. Was it created, "
                          "with the site's database user added to it?") from None  # fmt: skip
    if restored.get("products") is None:
        last = (restore.stderr or "").strip().splitlines()[-1:] or ["no output"]
        raise BackupError(f"The restore did not complete: {last[0][:300]}")
    now = _counts(live)
    return source, [(table, restored[table], now[table]) for table in CHECK_TABLES]


# ---------- emailing an encrypted copy (owner, 2026-10-30) ----------
# The dump holds customer details and the admin sign-in secrets, so it never leaves the server
# unencrypted: AES-256-GCM with a key stretched from BACKUP_PASSPHRASE by scrypt. The file can
# only be opened with `flask backup decrypt` and the passphrase; a wrong one is refused.

MAGIC = b"GGBACKUP1\n"
MIN_PASSPHRASE = 16
# Backups stay on the original shop mailbox even though the shop's public address moved to
# ggafrikaneleganz@gmail.com (owner, 2026-10-30). BACKUP_EMAIL overrides it.
BACKUP_DEFAULT_EMAIL = "ggafrikan@gmail.com"
MAX_EMAIL_BYTES = 17 * 1024 * 1024  # Gmail's 25 MB limit includes the ~37% email encoding


def _key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def encrypt(data: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC):
        raise BackupError("This is not an encrypted GG backup file.")
    body = blob[len(MAGIC) :]
    salt, nonce, sealed = body[:16], body[16:28], body[28:]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, sealed, MAGIC)
    except InvalidTag:
        raise BackupError("Wrong passphrase, or the file is damaged.") from None


def email_to() -> str | None:
    """Where backups are emailed, or None when emailing is off (no passphrase set)."""
    if not current_app.config.get("BACKUP_PASSPHRASE"):
        return None
    return current_app.config.get("BACKUP_EMAIL") or BACKUP_DEFAULT_EMAIL


def email_backup(path: Path) -> str:
    """Send the encrypted backup; returns a one-line result for the job log."""
    from app import mail

    to = email_to()
    passphrase = str(current_app.config.get("BACKUP_PASSPHRASE") or "")
    if to is None:
        return "Emailing is off (set BACKUP_PASSPHRASE to turn it on)."
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(
            f"BACKUP_PASSPHRASE must be at least {MIN_PASSPHRASE} characters; nothing was emailed."
        )
    sealed = encrypt(path.read_bytes(), passphrase)
    name = path.name + ".enc"
    if len(sealed) > MAX_EMAIL_BYTES:
        sent = mail.send(
            to,
            f"GG database backup {path.stem[len(PREFIX) :]}: too large to email",
            f"This week's backup ({len(sealed) // (1024 * 1024)} MB) is too large to attach. "
            "It is saved on the server in .ggflask/backups; download it from Site Tools > "
            "File Manager.\n",
        )
        if not sent:
            raise BackupError("The backup was saved, but the email could not be sent.")
        return f"Too large to attach; a notice was emailed to {to}."
    sent = mail.send(
        to,
        f"GG database backup {path.stem[len(PREFIX) :]}",
        "This week's database backup is attached, encrypted with your backup passphrase.\n\n"
        "Keep this email: it is an off-server copy in case the server is ever lost. The server "
        f"keeps only the newest {KEEP} backups; older emails are your history.\n\n"
        "To open it, on a computer with the project:\n"
        f"  flask --app wsgi backup decrypt {name}\n"
        "and type the passphrase. Only restore with Claude's help: it replaces the live data.\n",
        attachments=((name, sealed),),
    )
    if not sent:
        raise BackupError("The backup was saved, but the email could not be sent.")
    return f"Encrypted copy emailed to {to}."


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
        try:
            click.echo(email_backup(path))
        except BackupError as exc:  # the job shows as failed so the daily summary flags it
            raise click.ClickException(str(exc)) from None

    @backup_cli.command("test-restore")
    @click.argument("spare_db")
    def test_restore_command(spare_db: str) -> None:
        """Load the newest backup into SPARE_DB (an empty database) and compare it with live."""
        try:
            source, rows = restore_check(spare_db)
        except (BackupError, subprocess.TimeoutExpired, OSError) as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(f"Restored {source.name} into {spare_db}.\n")
        click.echo(f"{'table':16} {'in backup':>10} {'live now':>10}")
        for table, restored, live in rows:
            note = "" if restored == live else "  (live changed since the backup)"
            shown = "missing" if restored is None else str(restored)
            click.echo(f"{table:16} {shown:>10} {live if live is not None else '-':>10}{note}")
        click.echo(
            "\nThe backup restores. Differences only mean orders or edits happened after it was "
            f"taken. You can now delete {spare_db} in Site Tools."
        )

    @backup_cli.command("decrypt")
    @click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def decrypt_command(source: Path) -> None:
        """Turn an emailed SOURCE.enc back into a .dump file (asks for the passphrase)."""
        passphrase = click.prompt("Backup passphrase", hide_input=True)
        try:
            data = decrypt(source.read_bytes(), passphrase)
        except BackupError as exc:
            raise click.ClickException(str(exc)) from None
        target = source.with_suffix("") if source.suffix == ".enc" else source.with_suffix(".dump")
        if target.exists():
            raise click.ClickException(f"{target.name} already exists; move it first.")
        target.write_bytes(data)
        click.echo(f"Decrypted to {target}. Restore only with Claude's help.")

    @backup_cli.command("list")
    def list_command() -> None:
        """Show the backups kept on this server, newest first."""
        found = existing()
        if not found:
            click.echo(f"No backups yet in {backup_dir()}.")
        for path in found:
            size = path.stat().st_size / (1024 * 1024)
            click.echo(f"{path.name}  {size:.1f} MB")

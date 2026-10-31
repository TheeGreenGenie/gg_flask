"""Run pytest in a disposable PostgreSQL database that no other test run shares.

This runner is deliberately local-only. It refuses remote hosts and refuses a source URL whose
database is not named like a test database. It connects to PostgreSQL's ``postgres`` maintenance
database only to create/drop its own randomly named database; it never opens the shared ``gg_test``
database used to obtain the local connection settings.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess  # noqa: S404 - fixed interpreter/module; pytest args are intentional CLI input
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

DEFAULT_TEST_URL = "postgresql+psycopg://gg:gg@127.0.0.1:55432/gg_test"
DATABASE_PREFIX = "gg_iso_"
DATABASE_RE = re.compile(r"^gg_iso_[a-z0-9_]{1,55}$")
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}

# Test children need operating-system/process basics, not the parent's application/provider secrets.
SAFE_INHERITED_ENV = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "CI",
    "COLORTERM",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NO_COLOR",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TERM",
    "TMP",
    "TMPDIR",
    "TEMP",
    "USERPROFILE",
    "WINDIR",
}


class IsolationError(RuntimeError):
    pass


def database_name(label: str, *, pid: int | None = None, nonce: str | None = None) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "run"
    clean = clean[:24].rstrip("_") or "run"
    value = f"{DATABASE_PREFIX}{clean}_{pid or os.getpid()}_{nonce or secrets.token_hex(4)}"
    value = value[:63].rstrip("_")
    if not DATABASE_RE.fullmatch(value):
        raise IsolationError("could not make a safe isolated database name")
    return value


def validate_base_url(raw: str) -> URL:
    try:
        url = make_url(raw)
    except Exception as exc:
        raise IsolationError("TEST_DATABASE_URL is not a valid SQLAlchemy URL") from exc
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise IsolationError("isolated tests require PostgreSQL with psycopg")
    if url.host not in LOCAL_HOSTS:
        raise IsolationError("isolated tests refuse non-local PostgreSQL hosts")
    if url.query:
        raise IsolationError("isolated tests refuse database URL query parameters")
    if not url.database or (url.database != "gg_test" and not url.database.startswith("gg_test_")):
        raise IsolationError("base database must be named gg_test or start with gg_test_")
    return url


def _connect_kwargs(url: URL, database: str) -> dict[str, object]:
    values: dict[str, object] = {"dbname": database, "connect_timeout": 10}
    if url.host:
        values["host"] = url.host
    if url.port:
        values["port"] = url.port
    if url.username:
        values["user"] = url.username
    if url.password:
        values["password"] = url.password
    return values


def create_database(base: URL, name: str) -> None:
    if not DATABASE_RE.fullmatch(name):
        raise IsolationError("refusing to create an unrecognized database name")
    try:
        with psycopg.connect(
            **_connect_kwargs(base, "postgres"),  # type: ignore[arg-type]
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name))
            )
    except psycopg.Error as exc:
        raise IsolationError(
            "could not create the isolated database; start the local PostgreSQL container and "
            "use a local test role with CREATEDB"
        ) from exc


def drop_database(base: URL, name: str) -> None:
    if not DATABASE_RE.fullmatch(name):
        raise IsolationError("refusing to drop an unrecognized database name")
    try:
        with psycopg.connect(
            **_connect_kwargs(base, "postgres"),  # type: ignore[arg-type]
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
    except psycopg.Error as exc:
        raise IsolationError(f"could not remove isolated database {name}") from exc


def child_environment(
    parent: Mapping[str, str], base: URL, name: str, temp_root: Path
) -> dict[str, str]:
    if not DATABASE_RE.fullmatch(name):
        raise IsolationError("refusing to build settings for an unrecognized database name")
    env = {key: value for key, value in parent.items() if key.upper() in SAFE_INHERITED_ENV}
    isolated = base.set(database=name).render_as_string(hide_password=False)
    env.update(
        {
            "APP_ENV": "testing",
            "TEST_DATABASE_URL": isolated,
            "TEST_KV_BACKEND": "database",
            "TEST_REDIS_URL": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "TMP": str(temp_root),
            "TEMP": str(temp_root),
            "TMPDIR": str(temp_root),
        }
    )
    return env


def run_isolated(
    base: URL,
    name: str,
    pytest_args: list[str],
    *,
    keep_database: bool = False,
) -> int:
    created = False
    with tempfile.TemporaryDirectory(prefix=f"{name}_", ignore_cleanup_errors=True) as raw_temp:
        temp_root = Path(raw_temp).resolve()
        cache = temp_root / "pytest-cache"
        base_temp = temp_root / "pytest-temp"
        try:
            create_database(base, name)
            created = True
            print(f"Isolated test database: {name}", flush=True)
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                f"cache_dir={cache}",
                "--basetemp",
                str(base_temp),
                *pytest_args,
            ]
            completed = subprocess.run(  # noqa: S603 - pytest arguments are caller-selected
                command,
                cwd=Path(__file__).resolve().parent.parent,
                env=child_environment(os.environ, base, name, temp_root),
                check=False,
            )
            return completed.returncode
        finally:
            if created and not keep_database:
                drop_database(base, name)
                print(f"Removed isolated test database: {name}", flush=True)
            elif created:
                print(f"Kept isolated test database for debugging: {name}", flush=True)
            # TemporaryDirectory validates and owns raw_temp; this is only a best-effort retry for
            # Windows tools that release a cache handle just after pytest exits.
            if temp_root.is_dir():
                shutil.rmtree(temp_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run", help="short human label, e.g. claude or codex")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL),
        help="local gg_test URL used only for connection settings (never opened)",
    )
    parser.add_argument(
        "--keep-database",
        action="store_true",
        help="keep this runner's generated database after pytest for debugging",
    )
    parser.add_argument(
        "pytest_args", nargs=argparse.REMAINDER, help="arguments after -- go to pytest"
    )
    args = parser.parse_args(argv)
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args.pop(0)
    try:
        base = validate_base_url(args.base_url)
        name = database_name(args.label)
        return run_isolated(base, name, pytest_args, keep_database=args.keep_database)
    except IsolationError as exc:
        parser.exit(2, f"isolated test error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

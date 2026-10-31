"""Health of the scheduled jobs (monitoring review: jobs had no heartbeat).

deploy/run-job.sh and deploy/backup.sh write one small JSON file per job to instance/jobs/
after every run. This module compares those files with the schedule in deploy/gg-flask.cron:
a job is a problem when its last run failed, or when it has not finished for longer than its
schedule allows (stopped, stuck, or never ran on this server).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import click
from flask import Flask, current_app

# heartbeat name (the run-job.sh arguments with non-alphanumerics as "-") -> longest gap allowed
EXPECTED = {
    "studio-sync-sales": 10 * 60,  # every minute
    "studio-sync-blog": 10 * 60,  # every minute
    "orders-release-expired-holds": 45 * 60,  # every 15 minutes
    "orders-sync-refunds": 45 * 60,  # every 15 minutes
    "wishlist-prune": 26 * 3600,  # daily
    "accounts-purge-unverified": 26 * 3600,  # daily
    "reviews-purge-removed": 26 * 3600,  # daily
    "studio-daily-summary": 26 * 3600,  # daily
    "backup": 26 * 3600,  # nightly, run by deploy/backup.sh (own servers)
    "backup-run": 8 * 24 * 3600,  # weekly database backup on SiteGround (Saturdays)
}


@dataclass
class JobState:
    name: str
    problem: str  # "" when healthy
    detail: str


def heartbeat_dir() -> Path:
    return Path(
        current_app.config.get("JOBS_HEARTBEAT_DIR") or Path(current_app.instance_path) / "jobs"
    )


def check(now: float | None = None) -> list[JobState]:
    now = time.time() if now is None else now
    states = []
    for name, max_age in EXPECTED.items():
        on_siteground = current_app.config.get("GG_PLATFORM") == "siteground"
        if name == ("backup" if on_siteground else "backup-run"):
            continue  # SiteGround runs `flask backup run` weekly; own servers use deploy/backup.sh
        path = heartbeat_dir() / f"{name}.json"
        try:
            beat = json.loads(path.read_text(encoding="utf-8"))
            finished, result = float(beat["finished"]), str(beat["result"])
        except FileNotFoundError:
            states.append(JobState(name, "never ran", "no heartbeat on this server yet"))
            continue
        except (OSError, ValueError, KeyError, TypeError):
            states.append(JobState(name, "unreadable", "heartbeat file is damaged"))
            continue
        age = now - finished
        detail = f"last {result} {_ago(age)}"
        if result == "failed":
            states.append(JobState(name, "failed", f"{detail} (exit {beat.get('exit')})"))
        elif age > max_age:
            states.append(JobState(name, "overdue", detail))
        else:
            states.append(JobState(name, "", detail))
    return states


def problems(now: float | None = None) -> list[JobState]:
    return [state for state in check(now) if state.problem]


def _ago(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"


def init_jobs(app: Flask) -> None:
    @app.cli.group("jobs")
    def jobs_cli() -> None:
        """Scheduled job health."""

    @jobs_cli.command("check")
    def check_command() -> None:
        """List every scheduled job; exits with status 1 if any failed or stopped running."""
        states = check()
        for state in states:
            mark = "PROBLEM" if state.problem else "ok"
            note = f"{state.problem}: {state.detail}" if state.problem else state.detail
            click.echo(f"{mark:8} {state.name:32} {note}")
        if any(state.problem for state in states):
            raise SystemExit(1)

"""Scheduled-job heartbeats: failed, overdue and never-run jobs are reported."""

from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Flask

from app import jobs


def _beat(folder: Path, name: str, result: str = "ok", age: float = 0, code: int = 0) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    beat = {"job": name, "result": result, "exit": code, "finished": time.time() - age}
    (folder / f"{name}.json").write_text(json.dumps(beat), encoding="utf-8")


def _all_healthy(folder: Path) -> None:
    for name in jobs.EXPECTED:
        _beat(folder, name, age=60)


def test_healthy_jobs_pass_and_problems_are_named(app: Flask, tmp_path: Path) -> None:
    app.config["JOBS_HEARTBEAT_DIR"] = str(tmp_path)
    _all_healthy(tmp_path)
    with app.app_context():
        assert jobs.problems() == []
        _beat(tmp_path, "orders-sync-refunds", result="failed", code=2)
        _beat(tmp_path, "studio-sync-sales", age=3600)  # every-minute job silent for an hour
        (tmp_path / "backup.json").unlink()
        found = {state.name: state.problem for state in jobs.problems()}
    assert found == {
        "orders-sync-refunds": "failed",
        "studio-sync-sales": "overdue",
        "backup": "never ran",
    }


def test_a_skipped_run_is_not_a_failure(app: Flask, tmp_path: Path) -> None:
    app.config["JOBS_HEARTBEAT_DIR"] = str(tmp_path)
    _all_healthy(tmp_path)
    _beat(tmp_path, "studio-sync-blog", result="skipped", code=75)
    with app.app_context():
        assert jobs.problems() == []


def test_cli_exits_non_zero_when_something_is_wrong(app: Flask, tmp_path: Path) -> None:
    app.config["JOBS_HEARTBEAT_DIR"] = str(tmp_path)
    _all_healthy(tmp_path)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["jobs", "check"]).exit_code == 0
    _beat(tmp_path, "wishlist-prune", age=3 * 86400)
    result = runner.invoke(args=["jobs", "check"])
    assert result.exit_code == 1 and "overdue" in result.output and "3 days ago" in result.output


def test_daily_summary_warns_about_job_problems(catalog_app: Flask, tmp_path: Path) -> None:
    from app.studio import service

    catalog_app.config.update(ORDER_NOTIFICATION_EMAIL="owner@example.com")
    catalog_app.config["JOBS_HEARTBEAT_DIR"] = str(tmp_path)
    with catalog_app.app_context():
        service.send_daily_summary()
        assert "Scheduled jobs needing attention" in catalog_app.extensions["outbox"][-1].body
        _all_healthy(tmp_path)
        service.send_daily_summary()
        assert "ran normally" in catalog_app.extensions["outbox"][-1].body

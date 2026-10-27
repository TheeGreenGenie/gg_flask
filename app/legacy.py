"""Exact-match, same-origin redirects loaded from a reviewable CSV map."""

from __future__ import annotations

import csv
from pathlib import Path

from flask import Flask, redirect, request
from werkzeug.wrappers import Response


class RedirectMapError(RuntimeError):
    pass


def load(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    redirects: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            old, new = (row.get("old_path") or "").strip(), (row.get("new_path") or "").strip()
            if not old.startswith("/") or not new.startswith("/") or new.startswith("//"):
                raise RedirectMapError("Redirect paths must be same-origin absolute paths.")
            if old in redirects or old == new:
                raise RedirectMapError(f"Duplicate or circular redirect: {old}")
            redirects[old] = new
    for old in redirects:
        seen = {old}
        target = redirects[old]
        while target in redirects:
            if target in seen:
                raise RedirectMapError(f"Redirect loop involving {old}")
            seen.add(target)
            target = redirects[target]
    return redirects


def init_legacy_redirects(app: Flask) -> None:
    mapping = load(Path(app.root_path).parent / "legacy_redirects.csv")

    @app.before_request
    def redirect_legacy_path() -> Response | None:
        target = mapping.get(request.path)
        return redirect(target, code=301) if target else None

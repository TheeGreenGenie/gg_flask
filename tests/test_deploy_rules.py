"""The Apache rules in deploy/htaccess.example must let every real Studio page through.

They return 404 for anything under the private Studio path that is not on the allowlist, so a
new Studio page that is missing from it would silently break on the server.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import Flask

RULES = Path(__file__).resolve().parent.parent / "deploy" / "htaccess.example"
PLACEHOLDER = "REPLACE_WITH_STUDIO_PATH"
SAMPLE_TOKEN = "Ab3_" + "x" * 39  # same shape as secrets.token_urlsafe(32)


def _allowlist() -> list[re.Pattern[str]]:
    patterns = []
    for line in RULES.read_text(encoding="utf-8").splitlines():
        match = re.match(
            r"\s*RewriteCond %\{REQUEST_URI\} !(\^/" + PLACEHOLDER + r".*?) \[NC\]", line
        )
        if match:
            patterns.append(re.compile(match.group(1), re.IGNORECASE))
    return patterns


def _sample(rule: str) -> str:
    def fill(m: re.Match[str]) -> str:
        converter = m.group(1) or ""
        return "123" if converter.startswith("int") else SAMPLE_TOKEN

    return re.sub(r"<(?:(\w+):)?\w+>", fill, rule)


def test_every_studio_page_is_allowed_through(app: Flask) -> None:
    prefix = "/" + app.config["STUDIO_PATH"].strip("/")
    allowed = _allowlist()
    assert allowed, "no allowlist rules found"
    missing = []
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("studio.") or rule.endpoint == "studio.static":
            continue
        path = PLACEHOLDER + _sample(rule.rule).removeprefix(prefix)
        path = "/" + path.lstrip("/")
        if not any(p.match(path) for p in allowed):
            missing.append(f"{rule.endpoint} {path}")
    assert not missing, "Add to deploy/htaccess.example:\n" + "\n".join(missing)


def test_unknown_studio_paths_are_still_refused() -> None:
    allowed = _allowlist()
    for path in ("/.env", "/admin/x", "/products/abc", "/inventory/1/drop", "/../etc/passwd"):
        assert not any(p.match(f"/{PLACEHOLDER}{path}") for p in allowed), path

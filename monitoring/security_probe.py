"""Passive release-boundary checks for the deployed storefront.

The probe deliberately uses no login, provider, or application secrets. It never stores response
bodies. It is suitable for a cron job or an external monitor after the owner has authorized the
target URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

USER_AGENT: Final = "GG-Blue-Team-Security-Probe/1.0"
MAX_CAPTURE: Final = 64 * 1024

SENSITIVE_PATHS: Final = (
    "/.env",
    "/.git/config",
    "/.ggflask/env",
    "/current_security.md",
    "/requirements.lock",
    "/package.json",
    "/deploy/siteground/server.js",
    "/instance/",
    "/logs/python.log",
    "/backup.sql",
)


@dataclass(frozen=True)
class Probe:
    name: str
    method: str
    path: str
    kind: str
    body: bytes | None = None
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Observation:
    name: str
    method: str
    path: str
    status: int
    headers: dict[str, str]
    capture_sha256: str
    capture_bytes: int
    body_truncated: bool


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    detail: str


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep authentication-boundary redirects observable instead of following them."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ) -> None:
        return None


def probes() -> tuple[Probe, ...]:
    fixed = (
        Probe("homepage", "GET", "/", "homepage"),
        Probe("liveness", "GET", "/health/live", "liveness"),
        Probe("readiness", "GET", "/health/ready", "readiness"),
        Probe("admin-boundary", "GET", "/admin/", "admin-boundary"),
        Probe("admin-login", "GET", "/admin/login", "admin-login"),
        Probe(
            "stripe-unsigned",
            "POST",
            "/webhooks/stripe",
            "webhook",
            b"{}",
            (("Content-Type", "application/json"),),
        ),
        Probe(
            "paypal-unsigned",
            "POST",
            "/webhooks/paypal",
            "webhook",
            b"{}",
            (("Content-Type", "application/json"),),
        ),
        Probe("trace-disabled", "TRACE", "/", "trace"),
    )
    exposures = tuple(
        Probe(f"exposure:{path}", "GET", path, "sensitive") for path in SENSITIVE_PATHS
    )
    return fixed + exposures


def _fetch(base_url: str, probe: Probe, timeout: float) -> tuple[Observation, bytes]:
    target = urllib.parse.urljoin(base_url.rstrip("/") + "/", probe.path.lstrip("/"))
    request = urllib.request.Request(  # noqa: S310 - run() accepts HTTPS origins only
        target,
        data=probe.body,
        method=probe.method,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.8,*/*;q=0.1"}
        | dict(probe.headers),
    )
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_CAPTURE + 1)
        headers = {key.lower(): value for key, value in response.headers.items()}
        observation = Observation(
            name=probe.name,
            method=probe.method,
            path=probe.path,
            status=int(response.status),
            headers=headers,
            capture_sha256=hashlib.sha256(body).hexdigest(),
            capture_bytes=min(len(body), MAX_CAPTURE),
            body_truncated=len(body) > MAX_CAPTURE,
        )
        return observation, body[:MAX_CAPTURE]


def _header_findings(observation: Observation) -> list[Finding]:
    findings: list[Finding] = []
    required = {
        "content-security-policy": ("default-src 'self'", "frame-ancestors 'none'"),
        "strict-transport-security": ("max-age=",),
        "x-content-type-options": ("nosniff",),
        "x-frame-options": ("DENY",),
        "referrer-policy": ("strict-origin-when-cross-origin",),
        "permissions-policy": ("camera=()", "microphone=()"),
    }
    for name, fragments in required.items():
        value = observation.headers.get(name, "")
        if not value or any(fragment.lower() not in value.lower() for fragment in fragments):
            findings.append(Finding("FAIL", observation.name, f"missing/unsafe {name}"))
    for name in ("server", "x-powered-by"):
        value = observation.headers.get(name, "").strip()
        if value:
            findings.append(Finding("WARN", observation.name, f"public {name} banner is present"))
    return findings


def _json_body(observation: Observation, captured_body: bytes | None) -> dict[str, object]:
    if captured_body is None:
        return {}
    try:
        value = json.loads(captured_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def evaluate(
    probe: Probe,
    observation: Observation,
    *,
    expect_preview: bool = False,
    captured_body: bytes | None = None,
) -> list[Finding]:
    """Evaluate one observation. ``captured_body`` exists only for local tests/in-memory use."""
    findings = (
        _header_findings(observation)
        if probe.kind
        in {"homepage", "liveness", "readiness", "admin-boundary", "admin-login", "webhook"}
        else []
    )
    status = observation.status
    if probe.kind == "homepage":
        expected = {503} if expect_preview else {200}
        if status not in expected:
            findings.append(Finding("FAIL", probe.name, f"homepage returned {status}"))
    elif probe.kind == "liveness":
        if status != 200:
            findings.append(Finding("FAIL", probe.name, f"liveness returned {status}"))
        body = _json_body(observation, captured_body)
        if body and body.get("status") != "ok":
            findings.append(Finding("FAIL", probe.name, "liveness did not report ok"))
    elif probe.kind == "readiness":
        if status != 200:
            findings.append(Finding("FAIL", probe.name, f"readiness returned {status}"))
        body = _json_body(observation, captured_body)
        if body and body.get("status") != "ok":
            findings.append(Finding("FAIL", probe.name, "readiness did not report ok"))
        if "checks" in body:
            findings.append(
                Finding("FAIL", probe.name, "public readiness leaked dependency detail")
            )
    elif probe.kind == "admin-boundary":
        if status not in {302, 303, 307, 308, 404}:
            findings.append(
                Finding("FAIL", probe.name, f"unauthenticated admin root returned {status}")
            )
        elif status != 404:
            location = observation.headers.get("location", "")
            parsed = urllib.parse.urlsplit(location)
            if parsed.netloc or not parsed.path.startswith("/admin/login"):
                findings.append(Finding("FAIL", probe.name, "admin redirect left its login path"))
    elif probe.kind == "admin-login":
        if status not in {200, 404}:
            findings.append(Finding("FAIL", probe.name, f"admin login returned {status}"))
        if status == 200:
            cache = observation.headers.get("cache-control", "").lower()
            robots = observation.headers.get("x-robots-tag", "").lower()
            if "no-store" not in cache:
                findings.append(Finding("FAIL", probe.name, "admin login may be cached"))
            if "noindex" not in robots:
                findings.append(Finding("FAIL", probe.name, "admin login is indexable"))
    elif probe.kind == "webhook":
        if status != 400:
            findings.append(
                Finding("FAIL", probe.name, f"unsigned payment webhook returned {status}")
            )
    elif probe.kind == "trace":
        allowed = {405, 501} | ({503} if expect_preview else set())
        if status not in allowed:
            findings.append(Finding("FAIL", probe.name, f"TRACE returned {status}"))
    elif probe.kind == "sensitive":
        denied = {401, 403, 404, 410, 421} | ({503} if expect_preview else set())
        if status not in denied:
            findings.append(Finding("FAIL", probe.name, f"sensitive path returned {status}"))
    return findings


def run(base_url: str, *, timeout: float, expect_preview: bool) -> dict[str, object]:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("target must be an HTTPS origin with no credentials in the URL")
    observations: list[Observation] = []
    findings: list[Finding] = []
    for probe in probes():
        try:
            observation, captured_body = _fetch(base_url, probe, timeout)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            findings.append(Finding("FAIL", probe.name, f"request failed: {type(exc).__name__}"))
            continue
        observations.append(observation)
        # The small in-memory capture validates health JSON and is then discarded. The evidence
        # record keeps only its length/hash and whether the response was larger than the cap.
        findings.extend(
            evaluate(
                probe,
                observation,
                expect_preview=expect_preview,
                captured_body=captured_body,
            )
        )
    levels = {level: sum(item.level == level for item in findings) for level in ("FAIL", "WARN")}
    return {
        "schema": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "target": f"{parsed.scheme}://{parsed.netloc}",
        "preview_expected": expect_preview,
        "summary": {"probes": len(probes()), **levels},
        "findings": [asdict(item) for item in findings],
        "observations": [asdict(item) for item in observations],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="HTTPS origin, for example https://example.com")
    parser.add_argument("--expect-preview", action="store_true", help="expect the 503 preview lock")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output", type=Path, help="write JSON evidence atomically to this file")
    args = parser.parse_args(argv)
    try:
        report = run(args.target, timeout=args.timeout, expect_preview=args.expect_preview)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(args.output)
    else:
        sys.stdout.write(rendered)
    summary = report["summary"]
    assert isinstance(summary, dict)
    if summary["FAIL"]:
        return 2
    if summary["WARN"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

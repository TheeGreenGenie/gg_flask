from __future__ import annotations

import hashlib
from dataclasses import replace

from monitoring.security_probe import Observation, Probe, evaluate


def _headers(**extra: str) -> dict[str, str]:
    return {
        "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=(), microphone=()",
    } | extra


def _observation(probe: Probe, status: int, headers: dict[str, str] | None = None) -> Observation:
    return Observation(
        probe.name,
        probe.method,
        probe.path,
        status,
        headers or _headers(),
        hashlib.sha256(b"").hexdigest(),
        0,
        False,
    )


def test_hardened_homepage_passes() -> None:
    probe = Probe("homepage", "GET", "/", "homepage")
    assert evaluate(probe, _observation(probe, 200)) == []


def test_preview_mode_requires_the_intentional_503() -> None:
    probe = Probe("homepage", "GET", "/", "homepage")
    assert evaluate(probe, _observation(probe, 503), expect_preview=True) == []
    findings = evaluate(probe, _observation(probe, 200), expect_preview=True)
    assert any(item.level == "FAIL" and "homepage" in item.detail for item in findings)


def test_missing_headers_and_public_version_banner_are_reported() -> None:
    probe = Probe("homepage", "GET", "/", "homepage")
    observation = _observation(probe, 200, {"server": "gunicorn/26.2.0"})
    findings = evaluate(probe, observation)
    assert sum(item.level == "FAIL" for item in findings) == 6
    assert any(item.level == "WARN" and "banner" in item.detail for item in findings)


def test_sensitive_file_must_not_return_success_or_redirect() -> None:
    probe = Probe("exposure:/.env", "GET", "/.env", "sensitive")
    assert evaluate(probe, _observation(probe, 404)) == []
    for status in (200, 206, 301, 302, 304):
        assert any(item.level == "FAIL" for item in evaluate(probe, _observation(probe, status)))


def test_unsigned_webhook_must_be_rejected() -> None:
    probe = Probe("stripe-unsigned", "POST", "/webhooks/stripe", "webhook", b"{}")
    assert evaluate(probe, _observation(probe, 400)) == []
    assert any(item.level == "FAIL" for item in evaluate(probe, _observation(probe, 200)))


def test_admin_boundary_and_private_headers() -> None:
    root = Probe("admin-boundary", "GET", "/admin/", "admin-boundary")
    redirected = _observation(root, 302, _headers(location="/admin/login?next=/admin/"))
    assert evaluate(root, redirected) == []
    assert any(item.level == "FAIL" for item in evaluate(root, _observation(root, 200)))

    login = Probe("admin-login", "GET", "/admin/login", "admin-login")
    secure = _observation(
        login,
        200,
        _headers(**{"cache-control": "private, no-store", "x-robots-tag": "noindex"}),
    )
    assert evaluate(login, secure) == []
    findings = evaluate(login, replace(secure, headers=_headers()))
    assert len([item for item in findings if item.level == "FAIL"]) == 2


def test_readiness_detail_is_not_public() -> None:
    probe = Probe("readiness", "GET", "/health/ready", "readiness")
    observation = _observation(probe, 200)
    assert evaluate(probe, observation, captured_body=b'{"status":"ok"}') == []
    findings = evaluate(
        probe,
        observation,
        captured_body=b'{"status":"ok","checks":{"database":"ok"}}',
    )
    assert any("leaked dependency" in item.detail for item in findings)

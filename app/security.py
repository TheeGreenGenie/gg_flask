"""HTTP security headers applied to every response."""

from __future__ import annotations

from flask import Flask, Response

# Tight default: same-origin only. Third parties are added explicitly, never as wildcards
# for whole providers. Google Analytics hosts are added only when GA4 is configured (the
# script itself still loads only after visitor consent).
GA_SCRIPT = "https://www.googletagmanager.com"
GA_CONNECT = (
    "https://*.google-analytics.com https://*.analytics.google.com https://www.googletagmanager.com"
)


def build_csp(analytics: bool = False) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'" + (f" {GA_SCRIPT}" if analytics else ""),
            "style-src 'self'",
            "img-src 'self' data:" + (f" {GA_CONNECT}" if analytics else ""),
            "font-src 'self'",
            "connect-src 'self'" + (f" {GA_CONNECT}" if analytics else ""),
            "frame-src https://www.youtube-nocookie.com",  # homepage brand-story video
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


CONTENT_SECURITY_POLICY = build_csp()

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), interest-cohort=()"


PRIVATE = "private, no-store"


def apply_shared_cache_policy(response: Response) -> Response:
    """Keep shared caches (the host's page cache, CDNs) away from anything personal.

    The host caches any response that does not forbid it, and pages carry per-visitor content
    (cart count, CSRF token, flashed messages). So a response may be shared-cached only when its
    route explicitly marked it public, and never when it sets a cookie. Everything else is
    private and never stored.
    """
    if not response.cache_control.public or response.headers.getlist("Set-Cookie"):
        response.headers["Cache-Control"] = PRIVATE
    return response


def init_security_headers(app: Flask) -> None:
    hsts = app.config.get("ENABLE_HSTS", False)
    csp = build_csp(analytics=bool(app.config.get("GA4_MEASUREMENT_ID")))

    @app.after_request
    def set_security_headers(response: Response) -> Response:
        headers = response.headers
        headers.setdefault("Content-Security-Policy", csp)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if hsts:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    # Registered before the blueprints and asset hooks, so it runs after them and sees their
    # final Cache-Control.
    @app.after_request
    def shared_cache_policy(response: Response) -> Response:
        return apply_shared_cache_policy(response)

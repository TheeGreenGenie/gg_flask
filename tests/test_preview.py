"""Preview lock: the shop is live on its domain but only the team can see it (PREVIEW_PASSWORD)."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.preview import COOKIE
from tests.conftest import FakeRedis

PASSWORD = "team-preview-2026"  # noqa: S105 (test fixture)
STUDIO = "/studio-testing-private-path"


@pytest.fixture
def locked(catalog_app: Flask) -> Flask:
    catalog_app.config["PREVIEW_PASSWORD"] = PASSWORD
    return catalog_app


def _unlock(client: FlaskClient, password: str = PASSWORD, next_url: str = "/shop/"):  # type: ignore[no-untyped-def]
    return client.post("/preview", data={"password": password, "next": next_url})


def test_open_by_default(shop: FlaskClient) -> None:
    assert shop.get("/").status_code == 200
    assert shop.get("/preview").status_code == 302  # nothing to unlock
    assert "X-Robots-Tag" not in shop.get("/").headers


def test_public_sees_coming_soon_and_nothing_is_indexed(locked: Flask) -> None:
    client = locked.test_client()
    for path in ("/", "/shop/", "/product/ankara-blue/", "/account/login", "/cart/"):
        page = client.get(path)
        assert page.status_code == 503, path
        assert b"Our new shop is on its way" in page.data
        assert page.mimetype == "text/html"  # otherwise browsers show the raw source
        assert page.headers["Cache-Control"] == "private, no-store"
        assert "noindex" in page.headers["X-Robots-Tag"]
    assert client.post("/newsletter", data={"email": "a@b.com"}).status_code == 503
    assert client.get("/robots.txt").data == b"User-agent: *\nDisallow: /\n"


def test_payments_admin_studio_and_assets_stay_reachable(locked: Flask) -> None:
    client = locked.test_client()
    assert client.get("/health/live").status_code == 200
    assert client.post("/webhooks/stripe", data=b"{}").status_code != 503  # reaches Stripe code
    assert client.post("/webhooks/paypal", data=b"{}").status_code != 503
    assert client.get("/admin/login").status_code == 200
    assert client.get(f"{STUDIO}/").status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200
    # Lookalike paths are not exempt.
    for lookalike in ("/administrator/", "/admin-x", "/previewer", "/healthz", STUDIO + "-fake/"):
        assert client.get(lookalike).status_code == 503, lookalike


def test_password_unlocks_the_whole_site(locked: Flask) -> None:
    client = locked.test_client()
    wrong = _unlock(client, "not-the-password")
    assert wrong.status_code == 200 and b"That password is not right" in wrong.data
    assert client.get("/").status_code == 503
    ok = _unlock(client)
    assert ok.status_code == 302 and ok.headers["Location"] == "/shop/"
    cookie = ok.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert client.get("/").status_code == 200
    assert client.get("/product/ankara-blue/").status_code == 200


def test_unlock_only_redirects_within_the_site(locked: Flask) -> None:
    for target in ("//evil.example/", "https://evil.example/", "/\\evil.example"):
        response = _unlock(locked.test_client(), next_url=target)
        assert response.headers["Location"] == "/", target


def test_forged_or_outdated_cookies_do_not_unlock(locked: Flask) -> None:
    client = locked.test_client()
    client.set_cookie(COOKIE, "forged-value")
    assert client.get("/").status_code == 503
    _unlock(client)
    assert client.get("/").status_code == 200
    locked.config["PREVIEW_PASSWORD"] = "a-brand-new-password"  # noqa: S105 - signs everyone out
    assert client.get("/").status_code == 503


def test_password_guessing_is_limited_and_fails_closed(locked: Flask) -> None:
    client = locked.test_client()
    for _ in range(10):
        _unlock(client, "guess-guess-guess")
    blocked = _unlock(client)  # even the right password, after 10 wrong tries in an hour
    assert b"Too many attempts" in blocked.data and client.get("/").status_code == 503
    locked.extensions["redis"] = FakeRedis(healthy=False)
    fresh = locked.test_client()
    assert b"Too many attempts" in _unlock(fresh).data
    assert fresh.get("/").status_code == 503


def test_short_passwords_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import ConfigError, ProductionConfig

    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gg@localhost/unused")
    monkeypatch.setenv("KV_BACKEND", "database")
    monkeypatch.setenv("PREVIEW_PASSWORD", "short")
    with pytest.raises(ConfigError, match="PREVIEW_PASSWORD"):
        ProductionConfig().validate()

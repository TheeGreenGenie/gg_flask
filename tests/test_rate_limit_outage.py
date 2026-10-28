"""Redis outage: private sign-ins and reset emails are refused; shopping keeps working."""

from __future__ import annotations

from flask import Flask

from app import ratelimit
from app.studio import service as studio_service
from tests.conftest import FakeRedis
from tests.test_accounts import PASSWORD, register

STUDIO = "/studio-testing-private-path"


def test_limiter_fails_open_by_default_and_closed_on_request(app: Flask) -> None:
    for broken in (None, FakeRedis(healthy=False)):
        app.extensions["redis"] = broken
        with app.test_request_context("/"):
            assert ratelimit.allow("x", "k", 1, 60) is True
            assert ratelimit.allow("x", "k", 1, 60, fail_closed=True) is False


def test_studio_sign_in_is_refused_while_limits_cannot_be_counted(app: Flask) -> None:
    with app.test_request_context(STUDIO + "/"):
        studio_service.create_user("ada", "Temporary phrase 8472!", "editor")
    app.extensions["redis"] = FakeRedis(healthy=False)
    client = app.test_client()
    client.post(STUDIO + "/", data={"username": "ada", "password": "Temporary phrase 8472!"})
    from app.extensions import db
    from app.studio.models import StudioLoginRequest

    with app.app_context():
        assert db.session.query(StudioLoginRequest).count() == 0  # no approval request made


def test_customers_can_still_sign_in_during_an_outage(catalog_app: Flask) -> None:
    shop = catalog_app.test_client()
    register(shop)
    shop.post("/account/logout")
    catalog_app.extensions["redis"] = FakeRedis(healthy=False)
    response = shop.post("/account/login", data={"identity": "ada", "password": PASSWORD})
    assert response.status_code == 302 and response.headers["Location"].endswith("/account/")


def test_password_reset_emails_stop_during_an_outage(catalog_app: Flask) -> None:
    shop = catalog_app.test_client()
    register(shop)
    sent = len(catalog_app.extensions["outbox"])
    shop.post("/account/password/forgot", data={"email": "ada@example.com"})
    assert len(catalog_app.extensions["outbox"]) == sent + 1  # works normally
    catalog_app.extensions["redis"] = FakeRedis(healthy=False)
    shop.post("/account/password/forgot", data={"email": "ada@example.com"})
    assert len(catalog_app.extensions["outbox"]) == sent + 1  # refused during the outage

from __future__ import annotations

import re

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import func, select

from app.accounts import service
from app.accounts.models import Channel, MarketingConsent, PasswordReset, User
from app.extensions import db
from app.wishlist.models import Wishlist

PASSWORD = "kente-sunrise-2026"  # noqa: S105 (test fixture)


def register(client: FlaskClient, **overrides: str):  # type: ignore[no-untyped-def]
    data = {
        "first_name": "Ada",
        "email": "Ada@Example.com",
        "phone": "215-555-0123",
        "password": PASSWORD,
    }
    data.update(overrides)
    return client.post("/account/register", data=data)


def user(app: Flask, email: str = "ada@example.com") -> User:
    return db.session.scalar(select(User).where(User.email == email))  # type: ignore[no-any-return]


# ---------- registration ----------


def test_register_normalises_and_hashes(shop: FlaskClient, catalog_app: Flask) -> None:
    resp = register(shop)
    assert resp.status_code == 302 and resp.headers["Location"] == "/account/"
    with catalog_app.app_context():
        u = user(catalog_app)
        assert u.phone == "+12155550123"
        assert u.password_hash.startswith("$argon2id$") and PASSWORD not in u.password_hash
        assert u.consents == []  # nothing ticked -> no consent recorded
    page = shop.get("/account/").get_data(as_text=True)
    assert "Hello, Ada" in page and "ada@example.com" in page


def test_consent_is_opt_in_and_recorded_with_wording(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop, email_opt_in="1", sms_opt_in="1")
    with catalog_app.app_context():
        u = user(catalog_app)
        assert u.consented(Channel.EMAIL) and u.consented(Channel.SMS)
        sms = next(c for c in u.consents if c.channel == Channel.SMS)
        assert sms.wording == service.SMS_WORDING and "STOP" in sms.wording
        assert sms.destination == "+12155550123" and sms.source == "register"
        assert sms.ip_address


def test_register_form_boxes_are_unticked(shop: FlaskClient) -> None:
    html = shop.get("/account/register").get_data(as_text=True)
    for name in ("email_opt_in", "sms_opt_in"):
        tag = re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)  # type: ignore[union-attr]
        assert "checked" not in tag


def test_sms_consent_requires_a_phone(shop: FlaskClient, catalog_app: Flask) -> None:
    resp = register(shop, phone="", sms_opt_in="1")
    assert resp.status_code == 200 and "mobile number" in resp.get_data(as_text=True)
    with catalog_app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("email", "not-an-email", "valid email"),
        ("password", "short", "10 to 128"),
        ("password", "password123", "too easy"),
        ("password", "ada@example.com-secret", "too easy"),
        ("phone", "12", "valid phone"),
    ],
)
def test_register_validation(shop: FlaskClient, field: str, value: str, message: str) -> None:
    resp = register(shop, **{field: value})
    assert resp.status_code == 200 and message in resp.get_data(as_text=True)


def test_duplicate_email_is_rejected(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop)
    other = catalog_app.test_client()
    resp = register(other, email="ADA@example.com")
    assert "already exists" in resp.get_data(as_text=True)


# ---------- login / logout / sessions ----------


def test_login_is_generic_and_rotates_session(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop)
    shop.post("/account/logout")
    with shop.session_transaction() as s:
        s["planted"] = "attacker"  # fixation attempt
    wrong = shop.post(
        "/account/login", data={"email": "ada@example.com", "password": "nope-nope-nope"}
    )
    unknown = shop.post("/account/login", data={"email": "who@example.com", "password": PASSWORD})
    for resp in (wrong, unknown):
        assert "Email or password is incorrect." in resp.get_data(as_text=True)
    ok = shop.post("/account/login", data={"email": "ADA@example.com", "password": PASSWORD})
    assert ok.status_code == 302
    with shop.session_transaction() as s:
        assert "planted" not in s and s["uid"]


def test_logout_ends_session(shop: FlaskClient) -> None:
    register(shop)
    shop.post("/account/logout")
    assert shop.get("/account/").headers["Location"].startswith("/account/login")


def test_account_pages_are_not_cached(shop: FlaskClient) -> None:
    register(shop)
    assert shop.get("/account/").headers["Cache-Control"] == "private, no-store"


def test_login_is_rate_limited(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop)
    shop.post("/account/logout")
    for _ in range(10):
        shop.post("/account/login", data={"email": "ada@example.com", "password": "wrong-wrong"})
    resp = shop.post("/account/login", data={"email": "ada@example.com", "password": PASSWORD})
    assert "Too many attempts" in resp.get_data(as_text=True)


def test_next_param_cannot_redirect_offsite(shop: FlaskClient) -> None:
    register(shop)
    shop.post("/account/logout")
    resp = shop.post(
        "/account/login?next=https://evil.example/",
        data={"email": "ada@example.com", "password": PASSWORD},
    )
    assert resp.headers["Location"] == "/account/"
    shop.post("/account/logout")
    resp = shop.post(
        "/account/login?next=//evil.example/x",
        data={"email": "ada@example.com", "password": PASSWORD},
    )
    assert resp.headers["Location"] == "/account/"


def test_password_change_signs_out_other_devices(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop)
    laptop = catalog_app.test_client()
    laptop.post("/account/login", data={"email": "ada@example.com", "password": PASSWORD})
    assert laptop.get("/account/").status_code == 200
    shop.post(
        "/account/password",
        data={"current_password": PASSWORD, "new_password": "adire-indigo-2027"},
    )
    assert shop.get("/account/").status_code == 200  # this device stays signed in
    assert laptop.get("/account/").status_code == 302  # the other is signed out


# ---------- preferences / profile ----------


def test_preferences_append_to_consent_log(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop, email_opt_in="1")
    shop.post("/account/preferences", data={})  # untick everything
    shop.post("/account/preferences", data={})  # no change -> no new row
    with catalog_app.app_context():
        u = user(catalog_app)
        assert [(c.channel, c.granted) for c in u.consents] == [
            (Channel.EMAIL, True),
            (Channel.EMAIL, False),
        ]
        assert not u.consented(Channel.EMAIL)


def test_changing_phone_withdraws_sms_consent(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop, sms_opt_in="1")
    shop.post("/account/details", data={"first_name": "Ada", "phone": "+44 20 7946 0958"})
    with catalog_app.app_context():
        u = user(catalog_app)
        assert u.phone == "+442079460958"
        assert not u.consented(Channel.SMS)
        assert u.consents[-1].source == "phone-changed"
        assert u.consents[-1].destination == "+12155550123"


# ---------- password reset ----------


def _reset_link(app: Flask) -> str:
    body = app.extensions["outbox"][-1].body
    return re.search(r"http://localhost(/account/password/reset/\S+)", body).group(1)  # type: ignore[union-attr]


def test_password_reset_flow(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop)
    shop.post("/account/logout")
    resp = shop.post("/account/password/forgot", data={"email": "ada@example.com"})
    assert resp.status_code == 302
    link = _reset_link(catalog_app)
    with catalog_app.app_context():
        stored = db.session.scalars(select(PasswordReset.token_hash)).all()
        assert link.rsplit("/", 1)[1] not in stored  # only the hash is stored
    shop.post(link, data={"password": "kente-moonrise-2027"})
    assert shop.post(link, data={"password": "another-pass-2028"}).status_code == 302  # single use
    ok = shop.post(
        "/account/login", data={"email": "ada@example.com", "password": "kente-moonrise-2027"}
    )
    assert ok.headers["Location"] == "/account/"


def test_reset_does_not_reveal_accounts(shop: FlaskClient, catalog_app: Flask) -> None:
    resp = shop.post(
        "/account/password/forgot", data={"email": "nobody@example.com"}, follow_redirects=True
    )
    assert "If an account uses that email" in resp.get_data(as_text=True)
    assert catalog_app.extensions.get("outbox", []) == []


def test_reset_unavailable_without_mail_provider(shop: FlaskClient, catalog_app: Flask) -> None:
    catalog_app.config["MAIL_BACKEND"] = None
    html = shop.get("/account/password/forgot").get_data(as_text=True)
    assert "isn't available yet" in html


# ---------- wishlist + delete + export ----------


def test_wishlist_moves_into_account_and_stays_private(
    shop: FlaskClient, catalog_app: Flask
) -> None:
    shop.post(
        "/wishlist/toggle", data={"product": "ankara-blue"}, headers={"Accept": "application/json"}
    )
    register(shop)
    assert shop.get("/api/v1/wishlist").get_json()["count"] == 1
    shop.post("/account/logout")
    # After sign-out the same browser no longer sees the account's list.
    assert shop.get("/api/v1/wishlist").get_json()["count"] == 0
    shop.post("/account/login", data={"email": "ada@example.com", "password": PASSWORD})
    assert shop.get("/api/v1/wishlist").get_json()["count"] == 1


def test_delete_account_removes_personal_data(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop, email_opt_in="1")
    shop.post(
        "/wishlist/toggle", data={"product": "ankara-blue"}, headers={"Accept": "application/json"}
    )
    bad = shop.post("/account/delete", data={"password": PASSWORD, "confirm": "delete"})
    assert bad.headers["Location"].endswith("#delete")
    shop.post("/account/delete", data={"password": PASSWORD, "confirm": "DELETE"})
    with catalog_app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 0
        assert db.session.scalar(select(func.count(MarketingConsent.id))) == 0
        assert db.session.scalar(select(func.count(Wishlist.id))) == 0


def test_export_lists_only_opted_in(shop: FlaskClient, catalog_app: Flask) -> None:
    register(shop, email_opt_in="1", sms_opt_in="1")
    register(catalog_app.test_client(), email="ben@example.com", phone="")
    runner = catalog_app.test_cli_runner()
    email = runner.invoke(args=["accounts", "export-marketing", "--channel", "email"]).output
    sms = runner.invoke(args=["accounts", "export-marketing", "--channel", "sms"]).output
    assert "ada@example.com" in email and "ben@example.com" not in email
    assert "+12155550123" in sms and "ben" not in sms


def test_header_shows_account_state(shop: FlaskClient) -> None:
    assert 'aria-label="Sign in"' in shop.get("/").get_data(as_text=True)
    register(shop)
    assert 'aria-label="My account"' in shop.get("/").get_data(as_text=True)

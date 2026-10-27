from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

PAGES = (
    "/about-us/",
    "/contact-us/",
    "/faq/",
    "/privacy-policy/",
    "/terms-of-service/",
    "/shipping-information/",
    "/returns-exchanges/",
    "/blog/how-to-make-the-most-of-our-october-sales/",
    "/blog/the-cultural-significance-behind-ankara-prints/",
    "/my-account/",
    "/my-account/orders/",
)


@pytest.mark.parametrize("path", PAGES)
def test_content_pages_render(client: FlaskClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'class="gg-skip-link"' in html and "<footer" in html


def test_legacy_content_routes(client: FlaskClient) -> None:
    response = client.get("/support/")
    assert response.status_code == 301 and response.headers["Location"].endswith("/contact-us/")
    assert client.get("/portfolio/").status_code == 410
    assert client.get("/blog/not-a-post/").status_code == 404


def test_contact_validation_and_delivery(client: FlaskClient, app: Flask) -> None:
    bad = client.post("/contact-us/", data={"name": "", "email": "bad"})
    assert bad.status_code == 200 and b"Enter your name" in bad.data
    sent = client.post(
        "/contact-us/",
        data={
            "name": "Ada Example",
            "email": "ada@example.com",
            "topic": "product",
            "phone": "",
            "message": "Could you help me choose a coordinating fabric?",
        },
    )
    assert sent.status_code == 302 and sent.headers["Location"].endswith("/contact-us/")
    message = app.extensions["outbox"][-1]
    assert message.to == "info@ggafrikaneleganz.com"
    assert "ada@example.com" in message.body and "coordinating fabric" in message.body


def test_contact_does_not_put_user_input_in_mail_headers(client: FlaskClient, app: Flask) -> None:
    client.post(
        "/contact-us/",
        data={
            "name": "Mallory",
            "email": "mallory@example.com",
            "topic": "other",
            "message": "Subject: injected\nBcc: victim@example.com",
        },
    )
    message = app.extensions["outbox"][-1]
    assert message.subject == "Website enquiry: Other"


def test_home_journal_cards_link_to_posts(client: FlaskClient) -> None:
    html = client.get("/").get_data(as_text=True)
    assert "/blog/how-to-make-the-most-of-our-october-sales/" in html
    assert "/blog/the-cultural-significance-behind-ankara-prints/" in html

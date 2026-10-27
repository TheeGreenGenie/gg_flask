from __future__ import annotations

from flask import Flask, request
from flask.testing import FlaskClient

from app.uploads import PHOTO_UPLOAD_MAX_BYTES


def test_photo_upload_endpoints_accept_phone_sized_photos(app: Flask) -> None:
    for path in (
        "/admin/catalog/products/1/images",
        f"/{app.config['STUDIO_PATH']}/products/1/images",
    ):
        with app.test_request_context(path, method="POST"):
            assert request.max_content_length == PHOTO_UPLOAD_MAX_BYTES


def test_every_other_form_keeps_the_small_limit(app: Flask) -> None:
    for path in ("/contact-us/", "/account/login", "/cart/add", "/admin/catalog/products/1"):
        with app.test_request_context(path, method="POST"):
            assert request.max_content_length == app.config["MAX_CONTENT_LENGTH"]


def test_oversized_form_gets_a_plain_message(client: FlaskClient) -> None:
    big = {"name": "Ada", "message": "x" * (3 * 1024 * 1024)}
    resp = client.post("/contact-us/", data=big)
    assert resp.status_code == 413
    assert "more than this form accepts" in resp.get_data(as_text=True)


def test_oversized_form_returns_to_the_same_site_only(client: FlaskClient) -> None:
    big = {"message": "x" * (3 * 1024 * 1024)}
    back = client.post(
        "/contact-us/", data=big, headers={"Referer": "http://localhost/contact-us/"}
    )
    assert back.status_code == 303 and back.headers["Location"].endswith("/contact-us/")
    away = client.post("/contact-us/", data=big, headers={"Referer": "https://evil.example/x"})
    assert away.status_code == 413


def test_limit_applies_before_csrf_reads_the_form(app: Flask) -> None:
    """CSRF protection parses the body first; the photo limit must already apply then."""
    import io

    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    photo = (io.BytesIO(b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)), "phone.jpg")
    resp = client.post(
        "/admin/catalog/products/1/images",
        data={"image": photo, "alt": "Phone photo"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400  # past the size limit; refused only for the missing token
    assert client.post("/contact-us/", data={"m": "x" * (5 * 1024 * 1024)}).status_code == 413

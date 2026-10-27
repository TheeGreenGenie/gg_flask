"""Studio fixes from the staff walkthrough: page-by-page lists, photo uploads in the blog
editor, and draft previews (owner approved, 2026-10-27)."""

# ruff: noqa: S105  (fixture password)
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image
from sqlalchemy import select

import app.studio as studio_module
from app.content.models import BlogPost, BlogStatus
from app.extensions import db
from app.studio import service
from app.studio.models import StudioLoginRequest

PREFIX = "/studio-testing-private-path"
PASSWORD = "Temporary phrase 8472!"


def _editor(app: Flask) -> FlaskClient:
    client = app.test_client()
    with app.test_request_context(PREFIX + "/"):
        user = service.create_user("ada", PASSWORD, "editor")
        user.must_change_password = False
        db.session.commit()
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    with app.app_context():
        record = db.session.scalars(select(StudioLoginRequest)).one()
        service.decide(record, True)
        request_id = record.request_id
    client.get(PREFIX + f"/waiting/{request_id}")
    return client


def _jpeg(color: tuple[int, int, int] = (180, 90, 40)) -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 50), color).save(buffer, "JPEG")
    buffer.seek(0)
    return buffer


ARTICLE = {
    "title": "Wax print care",
    "slug": "wax-print-care",
    "category": "Journal",
    "excerpt": "How to wash and store wax prints.",
    "cover_alt": "Folded wax prints",
    "body": "Wash in cold water.",
}


def test_lists_are_page_by_page_and_keep_your_place(
    catalog_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(studio_module, "PAGE_SIZE", 1)
    client = _editor(catalog_app)
    first = client.get(PREFIX + "/inventory").get_data(as_text=True)
    assert "Showing 1–1 of" in first and "Next →" in first
    second = client.get(PREFIX + "/inventory?page=2").get_data(as_text=True)
    assert "Showing 2–2 of" in second and "← Previous" in second
    assert "Showing 1–1 of" in client.get(PREFIX + "/prices").get_data(as_text=True)
    assert "Showing 1–1 of" in client.get(PREFIX + "/products").get_data(as_text=True)
    # Saving a row returns to the same page.
    variant_id = int(re.search(r"/inventory/(\d+)\"", second).group(1))  # type: ignore[union-attr]
    back = client.post(
        PREFIX + f"/inventory/{variant_id}",
        data={"quantity": "3", "reason": "count", "mode": "set", "return_page": "2"},
    )
    assert back.headers["Location"].endswith("/inventory?page=2")


def test_cover_and_article_photos_upload_instead_of_typed_paths(catalog_app: Flask) -> None:
    client = _editor(catalog_app)
    response = client.post(
        PREFIX + "/blog/new",
        data={
            **ARTICLE,
            "cover_upload": (_jpeg(), "cover.jpg"),
            "body_image": (_jpeg((20, 120, 90)), "inside.jpg"),
            "body_image_alt": "Rinsing fabric",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    with catalog_app.app_context():
        post = db.session.scalar(select(BlogPost).where(BlogPost.slug == "wax-print-care"))
        assert post is not None
        assert post.cover_path.startswith("media:blog/") and post.cover_path.endswith(".webp")
        media = Path(catalog_app.config["MEDIA_ROOT"])
        assert (media / post.cover_path.removeprefix("media:")).is_file()
        assert "Rinsing fabric" in post.body_json and "blog/" in post.body_json


def test_new_article_without_a_cover_gets_a_plain_message(catalog_app: Flask) -> None:
    client = _editor(catalog_app)
    page = client.post(PREFIX + "/blog/new", data=ARTICLE).get_data(as_text=True)
    assert "Choose a cover photo for the article." in page


def test_drafts_can_be_previewed_only_in_the_studio(catalog_app: Flask) -> None:
    client = _editor(catalog_app)
    client.post(
        PREFIX + "/blog/new",
        data={**ARTICLE, "cover_upload": (_jpeg(), "cover.jpg")},
        content_type="multipart/form-data",
    )
    with catalog_app.app_context():
        post = db.session.scalar(select(BlogPost).where(BlogPost.slug == "wax-print-care"))
        assert post is not None and post.status == BlogStatus.DRAFT
        post_id = post.id
    preview = client.get(PREFIX + f"/blog/{post_id}/preview")
    html = preview.get_data(as_text=True)
    assert preview.status_code == 200 and "Wax print care" in html
    assert "Preview only" in html and "not visible on the website" in html
    assert preview.headers["X-Robots-Tag"].startswith("noindex")
    assert catalog_app.test_client().get("/blog/wax-print-care/").status_code == 404
    assert catalog_app.test_client().get(PREFIX + f"/blog/{post_id}/preview").status_code == 302

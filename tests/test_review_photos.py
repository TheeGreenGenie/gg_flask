"""Customer photos on reviews, checked privately for nudity (owner, 2026-10-27).

The flagged/clean decision is tested with a stand-in detector (no explicit images in the repo);
a smoke test runs the real on-server model on harmless images when it is installed.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, template_rendered
from PIL import Image
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app.reviews import images
from app.reviews import service as reviews
from app.reviews.models import ModerationRemoval, PostStatus, ProductPost, ProductPostPhoto
from tests.test_reviews import _customer, _form, _product


class FakeDetector:
    def __init__(self, score: float) -> None:
        self.score = score

    def detect(self, path: str) -> list[dict[str, Any]]:
        return [{"class": "FEMALE_BREAST_EXPOSED", "score": self.score}] if self.score else []


def _photo(name: str = "fabric.jpg") -> FileStorage:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (30, 120, 200)).save(buffer, "JPEG")
    buffer.seek(0)
    return FileStorage(stream=buffer, filename=name, content_type="image/jpeg")


@pytest.fixture
def photos_on(catalog_app: Flask, monkeypatch: pytest.MonkeyPatch) -> Flask:
    catalog_app.config["IMAGE_MODERATION_BACKEND"] = "local"
    catalog_app.config["MODERATION_RETENTION_DAYS"] = 15
    monkeypatch.setattr(images, "_detector", lambda: FakeDetector(0.0))
    return catalog_app


def _media_files(app: Flask) -> list[Path]:
    root = Path(app.config["MEDIA_ROOT"]) / "reviews"
    return list(root.glob("*")) if root.exists() else []


def _photo_field_offered(app: Flask) -> bool:
    seen: list[bool] = []

    def capture(sender: Flask, template: Any, context: dict[str, Any], **extra: Any) -> None:
        if "review_photos" in context:
            seen.append(bool(context["review_photos"]))

    with template_rendered.connected_to(capture, app):
        app.test_client().get("/product/ankara-blue/")
    return seen == [True]


def test_photos_are_off_unless_the_private_check_is_on(catalog_app: Flask) -> None:
    assert not _photo_field_offered(catalog_app)
    with (
        catalog_app.test_request_context("/"),
        pytest.raises(reviews.ReviewError, match="can't be added"),
    ):
        reviews.create_post(_customer(), _product(), _form(), [_photo()])


def test_clean_photo_is_published_with_the_review(photos_on: Flask) -> None:
    with photos_on.test_request_context("/"):
        post = reviews.create_post(_customer(), _product(), _form(), [_photo()])
        assert post.status == PostStatus.VISIBLE
        photo = photos_on.extensions["sqlalchemy"].session.scalar(select(ProductPostPhoto))
        assert photo is not None and photo.path.startswith("reviews/")
    assert len(_media_files(photos_on)) == 1
    assert _photo_field_offered(photos_on)
    html = photos_on.test_client().get("/product/ankara-blue/").get_data(as_text=True)
    assert photo.path in html


def test_inappropriate_photo_removes_the_post_and_is_never_stored(
    photos_on: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images, "_detector", lambda: FakeDetector(0.93))
    with photos_on.test_request_context("/"):
        with pytest.raises(reviews.ReviewError, match="removed"):
            reviews.create_post(_customer(), _product(), _form(), [_photo()])
        session = photos_on.extensions["sqlalchemy"].session
        post = session.scalar(select(ProductPost))
        assert post is not None and post.status == PostStatus.REMOVED
        assert post.moderation_reason == "nude_image" and post.body == ""
        assert session.scalar(select(ProductPostPhoto)) is None
        removal = session.scalar(select(ModerationRemoval))
        assert removal is not None and removal.reason == "nude_image"
        assert "soft" in removal.original_body  # the text is kept privately, the photo is not
    assert _media_files(photos_on) == []


def test_borderline_scores_below_the_threshold_are_allowed(
    photos_on: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images, "_detector", lambda: FakeDetector(0.39))  # a busy wax print
    with photos_on.test_request_context("/"):
        post = reviews.create_post(_customer(), _product(), _form(), [_photo()])
        assert post.status == PostStatus.VISIBLE


def test_photo_limits(photos_on: Flask) -> None:
    with photos_on.test_request_context("/"):
        with pytest.raises(reviews.ReviewError, match="at most 3"):
            reviews.create_post(_customer(), _product(), _form(), [_photo() for _ in range(4)])
        not_image = FileStorage(stream=io.BytesIO(b"<?php"), filename="x.jpg")
        with pytest.raises(reviews.ReviewError, match="could not be read"):
            reviews.create_post(_customer("b@example.com"), _product(), _form(), [not_image])


@pytest.mark.skipif(importlib.util.find_spec("nudenet") is None, reason="optional model")
def test_real_on_server_model_passes_harmless_photos(catalog_app: Flask) -> None:
    catalog_app.config["IMAGE_MODERATION_BACKEND"] = "local"
    images._detector.cache_clear()
    with catalog_app.app_context():
        assert images.photos_enabled()
        for color in ((30, 120, 200), (200, 160, 20)):
            data = images.prepare(_photo())
            assert images.explicit_score(data) < 0.5, color

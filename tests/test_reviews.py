from __future__ import annotations

from datetime import timedelta

import pytest
from flask import Flask
from sqlalchemy import select

from app.accounts.models import User
from app.catalog.models import Product, utcnow
from app.extensions import db
from app.reviews import service as reviews
from app.reviews.models import ModerationRemoval, PostKind, PostStatus, ProductPost
from app.studio import service as studio
from app.studio.models import StudioAudit


def _customer(email: str = "reader@example.com") -> User:
    user = User(
        username=email.split("@", 1)[0],
        email=email,
        password_hash="not-used-in-service-tests",  # noqa: S106 - no login in service tests
        first_name="Reader",
        email_verified_at=utcnow(),
    )
    db.session.add(user)
    db.session.commit()
    return user


def _product() -> Product:
    return db.session.scalar(select(Product).order_by(Product.id))  # type: ignore[return-value]


def _form(**changes: str) -> dict[str, str]:
    values = {
        "kind": "review",
        "rating": "4",
        "title": "Beautiful fabric",
        "body": "The cotton fabric has a beautiful color and feels soft.",
    }
    values.update(changes)
    return values


def test_relevant_review_is_public_and_counted(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/", headers={"User-Agent": "review-test"}):
        product = _product()
        post = reviews.create_post(_customer(), product, _form())

        assert post.status == PostStatus.VISIBLE
        assert reviews.public_posts(product) == [post]
        assert reviews.rating_summary(product) == (4.0, 1)


def test_negative_relevant_review_is_not_hidden(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        post = reviews.create_post(
            _customer(),
            _product(),
            _form(
                rating="1",
                title="Poor fabric quality",
                body="The fabric color and quality were poor for this price.",
            ),
        )

        assert post.status == PostStatus.VISIBLE
        assert post.rating == 1


def test_off_topic_post_is_collapsed_and_excluded_from_rating(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        product = _product()
        post = reviews.create_post(
            _customer(),
            product,
            _form(title="Unrelated thought", body="Tomorrow perhaps everybody visits elsewhere."),
        )

        assert post.status == PostStatus.COLLAPSED
        assert reviews.rating_summary(product) == (0.0, 0)


def test_links_duplicates_and_invalid_rating_are_rejected(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        user = _customer()
        product = _product()
        with pytest.raises(reviews.ReviewError, match="Links are not allowed"):
            reviews.create_post(user, product, _form(body="See https://example.com for fabric."))
        with pytest.raises(reviews.ReviewError, match="star rating"):
            reviews.create_post(user, product, _form(rating="9"))
        reviews.create_post(user, product, _form())
        with pytest.raises(reviews.ReviewError, match="already submitted"):
            reviews.create_post(user, product, _form())


def test_obscenity_is_removed_and_private_copy_expires(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        with pytest.raises(reviews.ReviewError, match="posting rules"):
            reviews.create_post(
                _customer(),
                _product(),
                _form(title="Fabric", body="This fuuuuck fabric message should be removed."),
            )

        post = db.session.scalar(select(ProductPost))
        removal = db.session.scalar(select(ModerationRemoval))
        assert post is not None and post.status == PostStatus.REMOVED
        assert post.title == "" and post.body == ""
        assert removal is not None and "fuuuuck" in removal.original_body
        assert removal.expires_at - removal.created_at >= timedelta(days=14)
        assert reviews.public_posts(_product()) == []


def test_zero_retention_discards_removed_text(catalog_app: Flask) -> None:
    catalog_app.config["MODERATION_RETENTION_DAYS"] = 0
    with catalog_app.test_request_context("/"):
        with pytest.raises(reviews.ReviewError):
            reviews.create_post(
                _customer(),
                _product(),
                _form(title="Fabric", body="This shit fabric message should be removed."),
            )
        assert db.session.scalar(select(ModerationRemoval)) is None


def test_expired_private_text_is_purged(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        with pytest.raises(reviews.ReviewError):
            reviews.create_post(
                _customer(),
                _product(),
                _form(title="Fabric", body="This shit fabric message should be removed."),
            )
        removal = db.session.scalar(select(ModerationRemoval))
        assert removal is not None
        removal.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

        assert reviews.purge_expired() == 1
        assert db.session.scalar(select(ModerationRemoval)) is None


def test_support_can_reply_and_override_relevance(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        post = reviews.create_post(
            _customer(),
            _product(),
            _form(title="Different subject", body="Tomorrow perhaps everybody visits elsewhere."),
        )
        staff = studio.create_user("supporter", "A-strong-test-password!", "support")

        studio.moderate_post(staff, post, "relevant")
        reply = studio.reply_to_post(staff, post, "Thanks for sharing your experience.")

        assert post.status == PostStatus.VISIBLE
        assert post.staff_relevance_override is True
        assert reply.body.startswith("Thanks")
        notice = catalog_app.extensions["outbox"][-1]
        assert notice.to == "reader@example.com"
        assert "replied to your review" in notice.subject
        assert db.session.scalar(
            select(StudioAudit).where(StudioAudit.action == "studio.community.replied")
        )


def test_storefront_shows_collapsed_notice(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        product = _product()
        reviews.create_post(
            _customer(),
            product,
            _form(title="Different subject", body="Tomorrow perhaps everybody visits elsewhere."),
        )
        slug = product.slug

    response = catalog_app.test_client().get(f"/product/{slug}/")
    assert response.status_code == 200
    assert b"These comments may not be relevant to the topic" in response.data


def test_posting_rules_and_privacy_disclose_moderation(client) -> None:
    rules = client.get("/review-rules/")
    privacy = client.get("/privacy-policy/")
    assert rules.status_code == 200
    assert b"Positive and negative opinions are treated the same" in rules.data
    assert privacy.status_code == 200
    assert b"Reviews, comments, and moderation" in privacy.data
    assert b"reviews, comments, and questions" in privacy.data


@pytest.mark.parametrize(
    ("kind", "rating", "success"),
    [
        ("review", "5", "Your review was posted."),
        ("comment", "", "Your comment was posted."),
        ("question", "", "Your question was posted."),
    ],
)
def test_signed_in_customer_can_post_each_kind_through_storefront(
    catalog_app: Flask, kind: str, rating: str, success: str
) -> None:
    with catalog_app.app_context():
        customer = _customer(f"{kind}@example.com")
        product = _product()
        user_id, session_version, slug = customer.id, customer.session_version, product.slug
        catalog_app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    client = catalog_app.test_client()
    with client.session_transaction() as session:
        session["uid"] = user_id
        session["sv"] = session_version

    response = client.post(
        f"/products/{slug}/posts",
        data={
            "kind": kind,
            "rating": rating,
            "title": f"Helpful {kind}",
            "body": "This fabric color and quality information is helpful.",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert success in response.get_data(as_text=True)
    with catalog_app.app_context():
        post = db.session.scalar(select(ProductPost).where(ProductPost.user_id == user_id))
        assert post is not None and post.kind == PostKind(kind)
        assert post.rating == (5 if kind == "review" else None)
        notice = catalog_app.extensions["outbox"][-1]
        assert notice.to == "owner@example.com" and f"product {kind}" in notice.subject


def test_post_requires_sign_in(catalog_app: Flask) -> None:
    with catalog_app.app_context():
        slug = _product().slug
    response = catalog_app.test_client().post(f"/products/{slug}/posts", data=_form())
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/account/login")

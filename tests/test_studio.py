from __future__ import annotations

import json
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from flask import Flask
from PIL import Image

from app.catalog.models import Category, Product, ProductImage, ProductStatus, Variant, utcnow
from app.content import blog as blog_service
from app.content.models import BlogPost, BlogRevision, BlogStatus
from app.extensions import db
from app.studio import service
from app.studio.models import (
    ApprovalStatus,
    InventoryHistory,
    PriceHistory,
    SaleStatus,
    StudioAudit,
    StudioLoginRequest,
    StudioRole,
    StudioSale,
    StudioSession,
    StudioUser,
)

PREFIX = "/studio-testing-private-path"
PASSWORD = "Temporary phrase 8472!"  # noqa: S105 - test fixture only
NEW_PASSWORD = "Permanent phrase 5938!"  # noqa: S105 - test fixture only


def _user(app: Flask, roles: str = "editor") -> StudioUser:
    with app.test_request_context(PREFIX + "/"):
        return service.create_user("ada", PASSWORD, roles)


def _approved_cookie(app: Flask, client, roles: str = "editor", change: bool = False) -> StudioUser:
    user = _user(app, roles)
    response = client.post(
        PREFIX + "/", data={"username": "ada", "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 302
    with app.app_context():
        record = db.session.query(StudioLoginRequest).one()
        service.decide(record, True)
        request_id = record.request_id
    response = client.get(PREFIX + f"/waiting/{request_id}", follow_redirects=False)
    assert response.status_code == 302
    assert "gg_studio=" in response.headers["Set-Cookie"]
    assert "Max-Age" not in response.headers["Set-Cookie"]
    if change:
        response = client.post(
            PREFIX + "/password",
            data={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
    return user


def test_studio_uses_only_configured_hidden_path(app: Flask, client) -> None:
    assert client.get("/studio/").status_code == 404
    response = client.get(PREFIX + "/")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert b"Staff Studio" in response.data


def test_server_only_account_commands(app: Flask) -> None:
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["studio", "create", "--username", "ada", "--roles", "editor,inventory"],
        input=PASSWORD + "\n" + PASSWORD + "\n",
    )
    assert result.exit_code == 0, result.output
    assert "Created ada" in result.output
    listed = runner.invoke(args=["studio", "list"])
    assert "ada\teditor,inventory\tactive" in listed.output
    disabled = runner.invoke(args=["studio", "disable", "ada"])
    assert disabled.exit_code == 0
    with app.app_context():
        user = db.session.query(StudioUser).one()
        assert not user.active
        assert user.must_change_password
        assert user.password_hash != PASSWORD


def test_password_login_needs_single_use_owner_approval(app: Flask, client) -> None:
    _user(app)
    response = client.post(
        PREFIX + "/", data={"username": "ada", "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 302
    with app.app_context():
        record = db.session.query(StudioLoginRequest).one()
        assert record.status == ApprovalStatus.PENDING
        request_id = record.request_id
        service.decide(record, True)
    response = client.get(PREFIX + f"/waiting/{request_id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(PREFIX + "/password")
    with app.app_context():
        assert db.session.query(StudioSession).count() == 1
        assert db.session.query(StudioLoginRequest).one().status == ApprovalStatus.CONSUMED
        assert db.session.query(StudioAudit).filter_by(action="studio.login.completed").count() == 1
    replay = client.get(PREFIX + f"/waiting/{request_id}")
    assert replay.status_code == 200
    assert b"already been used" in replay.data


def test_expired_request_cannot_be_consumed(app: Flask, client) -> None:
    _user(app)
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    with app.app_context():
        record = db.session.query(StudioLoginRequest).one()
        request_id = record.request_id
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    response = client.get(PREFIX + f"/waiting/{request_id}")
    assert b"Request expired" in response.data
    with app.app_context():
        assert db.session.query(StudioLoginRequest).one().status == ApprovalStatus.EXPIRED
        assert db.session.query(StudioSession).count() == 0


def test_first_sign_in_forces_password_change(app: Flask, client) -> None:
    _approved_cookie(app, client)
    response = client.get(PREFIX + "/inventory", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(PREFIX + "/password")
    response = client.post(
        PREFIX + "/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(PREFIX + "/home")
    assert client.get(PREFIX + "/inventory").status_code == 200


def test_role_permissions_and_inventory_history(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    assert client.get(PREFIX + "/inventory").status_code == 200
    assert client.get(PREFIX + "/prices").status_code == 403
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant_id = variant.id
        before = variant.stock_quantity
    response = client.post(
        PREFIX + f"/inventory/{variant_id}",
        data={"quantity": str(before + 3), "reason": "Received shipment"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        history = db.session.query(InventoryHistory).one()
        assert variant is not None and variant.stock_quantity == before + 3
        assert history.before_quantity == before
        assert history.after_quantity == before + 3
        assert history.reason == "Received shipment"


def test_inventory_undo_restores_quantity_and_records_it(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant_id = variant.id
        original = variant.stock_quantity
    client.post(
        PREFIX + f"/inventory/{variant_id}",
        data={"quantity": str(original + 2), "reason": "Received stock"},
    )
    with catalog_app.app_context():
        history_id = db.session.query(InventoryHistory.id).scalar()
    response = client.post(PREFIX + f"/inventory/history/{history_id}/undo", follow_redirects=True)
    assert b"Stock change undone" in response.data
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        original_history = db.session.get(InventoryHistory, history_id)
        assert variant is not None and variant.stock_quantity == original
        assert original_history is not None and original_history.undone_at is not None
        assert db.session.query(InventoryHistory).count() == 2


def test_inventory_undo_refuses_later_stock_change(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant_id = variant.id
        original = variant.stock_quantity
    client.post(
        PREFIX + f"/inventory/{variant_id}",
        data={"quantity": str(original + 2), "reason": "First shipment"},
    )
    with catalog_app.app_context():
        history_id = db.session.query(InventoryHistory.id).scalar()
        variant = db.session.get(Variant, variant_id)
        assert variant is not None
        variant.stock_quantity += 1
        db.session.commit()
    response = client.post(PREFIX + f"/inventory/history/{history_id}/undo", follow_redirects=True)
    assert b"cannot be safely undone" in response.data


def test_editor_can_update_exact_price_with_history(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant.compare_at_cents = None
        db.session.commit()
        variant_id = variant.id
        before = variant.price_cents
    response = client.post(
        PREFIX + f"/prices/{variant_id}",
        data={"price": "42.99", "reason": "Supplier cost changed"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        history = db.session.query(PriceHistory).one()
        assert variant is not None and variant.price_cents == 4299
        assert history.before_cents == before
        assert history.after_cents == 4299


def test_price_undo_refuses_to_overwrite_a_later_change(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant.compare_at_cents = None
        original = variant.price_cents
        db.session.commit()
        variant_id = variant.id
    client.post(
        PREFIX + f"/prices/{variant_id}",
        data={"price": "42.99", "reason": "First change", "expected": str(original)},
    )
    with catalog_app.app_context():
        first = db.session.query(PriceHistory).one()
        first_id = first.id
    client.post(
        PREFIX + f"/prices/{variant_id}",
        data={"price": "44.99", "reason": "Later change", "expected": "4299"},
    )
    response = client.post(PREFIX + f"/prices/history/{first_id}/undo", follow_redirects=True)
    assert b"cannot be safely undone" in response.data
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        assert variant is not None and variant.price_cents == 4499


def test_price_undo_restores_value_and_records_the_undo(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant.compare_at_cents = None
        original = variant.price_cents
        db.session.commit()
        variant_id = variant.id
    client.post(
        PREFIX + f"/prices/{variant_id}",
        data={"price": "42.99", "reason": "Correction", "expected": str(original)},
    )
    with catalog_app.app_context():
        history_id = db.session.query(PriceHistory).one().id
    response = client.post(PREFIX + f"/prices/history/{history_id}/undo", follow_redirects=True)
    assert b"Price change undone" in response.data
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        original_history = db.session.get(PriceHistory, history_id)
        assert variant is not None and variant.price_cents == original
        assert original_history is not None and original_history.undone_at is not None
        assert db.session.query(PriceHistory).count() == 2


def test_stale_inventory_form_does_not_overwrite_newer_stock(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    with catalog_app.app_context():
        variant = db.session.query(Variant).first()
        assert variant is not None
        variant.stock_quantity = 12
        db.session.commit()
        variant_id = variant.id
    response = client.post(
        PREFIX + f"/inventory/{variant_id}",
        data={"quantity": "4", "reason": "Old page", "expected": "10"},
        follow_redirects=True,
    )
    assert b"Stock changed since this page loaded" in response.data
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        assert variant is not None and variant.stock_quantity == 12
        assert db.session.query(InventoryHistory).count() == 0


def test_inventory_csv_is_downloadable(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    response = client.get(PREFIX + "/inventory.csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"] == "attachment; filename=inventory.csv"
    assert b"Product,Option,SKU,Stock,Tracking,Status" in response.data


def test_editor_hides_and_restores_product_without_deleting(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        product = db.session.query(Product).filter_by(status=ProductStatus.ACTIVE).first()
        assert product is not None
        product_id = product.id
        slug = product.slug
    response = client.post(
        PREFIX + f"/products/{product_id}/visibility",
        data={"live": "0", "expected": "active", "reason": "Temporarily unavailable"},
        follow_redirects=True,
    )
    assert b"Product hidden" in response.data
    with catalog_app.app_context():
        product = db.session.get(Product, product_id)
        assert product is not None and product.status == ProductStatus.DRAFT
    assert client.get(f"/product/{slug}/").status_code == 404
    response = client.post(
        PREFIX + f"/products/{product_id}/visibility",
        data={"live": "1", "expected": "draft", "reason": "Back in stock"},
        follow_redirects=True,
    )
    assert b"Product is live again" in response.data
    with catalog_app.app_context():
        product = db.session.get(Product, product_id)
        assert product is not None and product.status == ProductStatus.ACTIVE
        assert db.session.query(Product).filter_by(id=product_id).count() == 1


def test_inventory_role_cannot_edit_products(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    with catalog_app.app_context():
        product_id = db.session.query(Product.id).first()[0]
    assert client.get(PREFIX + "/products").status_code == 403
    response = client.post(
        PREFIX + f"/products/{product_id}/visibility",
        data={"live": "0", "expected": "active", "reason": "Not permitted"},
    )
    assert response.status_code == 403


def test_editor_updates_product_details_and_categories(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        product = db.session.query(Product).first()
        categories = db.session.query(Category).order_by(Category.id).limit(2).all()
        assert product is not None and categories
        product_id = product.id
        category_ids = [str(category.id) for category in categories]
    response = client.post(
        PREFIX + f"/products/{product_id}",
        data={
            "name": "Edited Studio Product",
            "summary": "A clear summary.",
            "description": "A staff-authored product description.",
            "sale_unit": "metre",
            "category_ids": category_ids,
        },
        follow_redirects=True,
    )
    assert b"Product details saved" in response.data
    with catalog_app.app_context():
        product = db.session.get(Product, product_id)
        assert product is not None and product.name == "Edited Studio Product"
        assert product.sale_unit == "metre"
        assert {category.id for category in product.categories} == {
            int(category_id) for category_id in category_ids
        }
        slug = product.slug
    public = client.get(f"/product/{slug}/")
    assert b"Price per metre" in public.data


def test_editor_uploads_sanitized_photo_and_can_remove_it(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        product = db.session.query(Product).first()
        assert product is not None
        product_id = product.id
        original_count = len(product.images)
    source = BytesIO()
    Image.new("RGB", (40, 30), (180, 30, 20)).save(source, "PNG")
    source.seek(0)
    response = client.post(
        PREFIX + f"/products/{product_id}/images",
        data={"image": (source, "untrusted.png"), "alt": "Red patterned fabric"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Photo uploaded and safely resized" in response.data
    with catalog_app.app_context():
        product = db.session.get(Product, product_id)
        assert product is not None and len(product.images) == original_count + 1
        uploaded = db.session.query(ProductImage).filter_by(alt="Red patterned fabric").one()
        image_id = uploaded.id
        assert uploaded.path.endswith(".webp")
    response = client.post(PREFIX + f"/products/images/{image_id}/remove", follow_redirects=True)
    assert b"Photo removed" in response.data
    with catalog_app.app_context():
        assert db.session.get(ProductImage, image_id) is None


def _sale_form(product_id: int, starts: datetime, ends: datetime) -> dict[str, str]:
    return {
        "name": "Weekend fabric sale",
        "starts_at": starts.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": ends.strftime("%Y-%m-%dT%H:%M"),
        "discount_kind": "percent",
        "amount": "10",
        "scope": f"product:{product_id}",
    }


def test_confirmed_sale_activates_and_restores_prices(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        product = db.session.query(Product).filter_by(status=ProductStatus.ACTIVE).first()
        assert product is not None
        for variant in product.variants:
            variant.compare_at_cents = None
        db.session.commit()
        product_id = product.id
        variant_id = product.variants[0].id
        original = product.variants[0].price_cents
    local_now = datetime.now(ZoneInfo("America/New_York"))
    response = client.post(
        PREFIX + "/sales/new",
        data=_sale_form(
            product_id, local_now - timedelta(minutes=1), local_now + timedelta(hours=1)
        ),
        follow_redirects=False,
    )
    assert response.status_code == 302
    with catalog_app.app_context():
        sale = db.session.query(StudioSale).one()
        assert sale.status == SaleStatus.DRAFT
        sale_id = sale.id
        end = sale.ends_at
    response = client.post(PREFIX + f"/sales/{sale_id}/confirm", follow_redirects=True)
    assert b"Sale confirmed and scheduled" in response.data
    with catalog_app.app_context():
        sale = db.session.get(StudioSale, sale_id)
        variant = db.session.get(Variant, variant_id)
        assert sale is not None and sale.status == SaleStatus.ACTIVE
        assert variant is not None and variant.compare_at_cents == original
        assert variant.price_cents < original
        activated_price = variant.price_cents
        activated, ended = service.sync_sales(end + timedelta(seconds=1))
        assert (activated, ended) == (0, 1)
        assert sale.status == SaleStatus.ENDED
        assert variant.price_cents == original
        assert variant.compare_at_cents is None
        assert activated_price != original


def test_overlapping_sale_is_rejected_at_confirmation(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        product = db.session.query(Product).filter_by(status=ProductStatus.ACTIVE).first()
        assert product is not None
        for variant in product.variants:
            variant.compare_at_cents = None
        db.session.commit()
        product_id = product.id
    local_now = datetime.now(ZoneInfo("America/New_York"))
    form = _sale_form(product_id, local_now + timedelta(days=1), local_now + timedelta(days=2))
    client.post(PREFIX + "/sales/new", data=form)
    with catalog_app.app_context():
        first = db.session.query(StudioSale).one()
        first_id = first.id
    client.post(PREFIX + f"/sales/{first_id}/confirm")
    second_form = dict(form)
    second_form["name"] = "Overlapping sale"
    client.post(PREFIX + "/sales/new", data=second_form)
    with catalog_app.app_context():
        second = db.session.query(StudioSale).order_by(StudioSale.id.desc()).first()
        assert second is not None
        second_id = second.id
    response = client.post(PREFIX + f"/sales/{second_id}/confirm", follow_redirects=True)
    assert b"Another scheduled sale overlaps" in response.data
    with catalog_app.app_context():
        assert db.session.get(StudioSale, second_id).status == SaleStatus.DRAFT


def test_sale_activation_conflict_never_overwrites_new_price(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    with catalog_app.app_context():
        product = db.session.query(Product).filter_by(status=ProductStatus.ACTIVE).first()
        assert product is not None
        for variant in product.variants:
            variant.compare_at_cents = None
        db.session.commit()
        product_id = product.id
        variant_id = product.variants[0].id
    local_now = datetime.now(ZoneInfo("America/New_York"))
    starts = local_now + timedelta(hours=2)
    form = _sale_form(product_id, starts, starts + timedelta(hours=2))
    client.post(PREFIX + "/sales/new", data=form)
    with catalog_app.app_context():
        sale = db.session.query(StudioSale).one()
        sale_id = sale.id
        start_utc = sale.starts_at
    client.post(PREFIX + f"/sales/{sale_id}/confirm")
    with catalog_app.app_context():
        variant = db.session.get(Variant, variant_id)
        assert variant is not None
        later_price = variant.price_cents + 123
        variant.price_cents = later_price
        db.session.commit()
        service.sync_sales(start_utc + timedelta(seconds=1))
        sale = db.session.get(StudioSale, sale_id)
        assert sale is not None and sale.status == SaleStatus.CANCELLED
        assert variant.price_cents == later_price


def _blog_form(title: str = "Studio Journal Story") -> dict[str, str]:
    return {
        "title": title,
        "slug": "studio-journal-story",
        "category": "Studio Notes",
        "excerpt": "A useful summary of this new article for readers.",
        "cover_path": "static:img/home/post-promo.webp",
        "cover_alt": "Folded colorful African fabrics",
        "body": (
            "A first paragraph with <script>alert('never')</script>.\n\n"
            "## A helpful heading\n\n- First point\n- Second point\n\n"
            "[Learn more](https://example.com/story)"
        ),
        "seo_title": "",
        "seo_description": "",
    }


def test_editor_creates_and_publishes_safe_database_article(app: Flask) -> None:
    client = app.test_client()
    _approved_cookie(app, client, roles="editor", change=True)
    response = client.post(PREFIX + "/blog/new", data=_blog_form(), follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        post = db.session.query(BlogPost).one()
        assert post.status == BlogStatus.DRAFT
        assert post.seo_title == post.title
        post_id = post.id
    response = client.post(
        PREFIX + f"/blog/{post_id}/publication",
        data={"action": "publish"},
        follow_redirects=True,
    )
    assert b"Article publication updated" in response.data
    public = client.get("/blog/studio-journal-story/")
    assert public.status_code == 200
    assert b"Studio Journal Story" in public.data
    assert b"A helpful heading" in public.data
    assert b"https://example.com/story" in public.data
    assert b"<script>alert" not in public.data
    assert b"&lt;script&gt;" in public.data


def test_blog_edit_creates_revision_that_can_be_restored(app: Flask) -> None:
    client = app.test_client()
    _approved_cookie(app, client, roles="editor", change=True)
    client.post(PREFIX + "/blog/new", data=_blog_form())
    with app.app_context():
        post_id = db.session.query(BlogPost.id).scalar()
    changed = _blog_form("Changed Article Title")
    client.post(PREFIX + f"/blog/{post_id}", data=changed)
    with app.app_context():
        post = db.session.get(BlogPost, post_id)
        revision = db.session.query(BlogRevision).one()
        assert post is not None and post.title == "Changed Article Title"
        revision_id = revision.id
    response = client.post(PREFIX + f"/blog/revisions/{revision_id}/restore", follow_redirects=True)
    assert b"Earlier article version restored" in response.data
    with app.app_context():
        post = db.session.get(BlogPost, post_id)
        assert post is not None and post.title == "Studio Journal Story"
        assert db.session.query(BlogRevision).count() == 2


def test_scheduled_blog_article_publishes_idempotently(app: Flask) -> None:
    client = app.test_client()
    _approved_cookie(app, client, roles="editor", change=True)
    client.post(PREFIX + "/blog/new", data=_blog_form())
    local_now = datetime.now(ZoneInfo("America/New_York"))
    with app.app_context():
        post_id = db.session.query(BlogPost.id).scalar()
    client.post(
        PREFIX + f"/blog/{post_id}/publication",
        data={
            "action": "schedule",
            "publish_at": (local_now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        },
    )
    assert client.get("/blog/studio-journal-story/").status_code == 404
    with app.app_context():
        post = db.session.get(BlogPost, post_id)
        assert post is not None and post.status == BlogStatus.SCHEDULED
        publish_at = post.publish_at
        assert publish_at is not None
        assert blog_service.sync_publication(publish_at + timedelta(seconds=1))[0] == 1
        assert blog_service.sync_publication(publish_at + timedelta(seconds=2))[0] == 0
    assert client.get("/blog/studio-journal-story/").status_code == 200


def test_logout_revokes_database_session(app: Flask, client) -> None:
    _approved_cookie(app, client, change=True)
    assert client.get(PREFIX + "/home").status_code == 200
    response = client.post(PREFIX + "/logout", follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        session_record = db.session.query(StudioSession).one()
        assert session_record.revoked_at is not None
    assert client.get(PREFIX + "/home", follow_redirects=False).status_code == 302


def test_invalid_studio_password_is_rejected(app: Flask) -> None:
    with app.test_request_context(PREFIX + "/"):
        try:
            service.create_user("ada", "short pass", StudioRole.EDITOR.value)
        except service.StudioError as exc:
            assert "12 to 128" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("short Studio password was accepted")


def test_daily_summary_uses_configured_mail_adapter(catalog_app: Flask) -> None:
    catalog_app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    with catalog_app.app_context():
        assert service.send_daily_summary()
        message = catalog_app.extensions["outbox"][-1]
        assert message.to == "owner@example.com"
        assert message.subject == "Daily Staff Studio summary"
        assert "Low-stock or sold-out options:" in message.body


def test_daily_summary_requires_owner_email(app: Flask) -> None:
    with app.app_context(), pytest.raises(service.StudioError, match="ORDER_NOTIFICATION_EMAIL"):
        service.send_daily_summary()


def test_studio_user_manifest_previews_then_applies(app: Flask, tmp_path: Path) -> None:
    manifest = tmp_path / "studio-users.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "users": [{"username": "ada", "roles": ["editor", "support"], "active": True}],
            }
        ),
        encoding="utf-8",
    )
    runner = app.test_cli_runner()
    preview = runner.invoke(args=["studio", "sync-users", "--file", str(manifest)])
    assert preview.exit_code == 0
    assert "Preview only" in preview.output
    with app.app_context():
        assert db.session.query(StudioUser).count() == 0

    applied = runner.invoke(
        args=["studio", "sync-users", "--file", str(manifest), "--apply"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert applied.exit_code == 0
    with app.app_context():
        user = db.session.query(StudioUser).one()
        assert user.username == "ada"
        assert user.roles == "editor,support"
        assert user.must_change_password


def test_studio_user_removed_from_manifest_is_disabled(app: Flask, tmp_path: Path) -> None:
    with app.app_context():
        service.create_user("ada", PASSWORD, "editor")
    manifest = tmp_path / "studio-users.json"
    manifest.write_text('{"version": 1, "users": []}', encoding="utf-8")
    result = app.test_cli_runner().invoke(
        args=["studio", "sync-users", "--file", str(manifest), "--apply"]
    )
    assert result.exit_code == 0
    assert "disabled, never deleted" in result.output
    with app.app_context():
        user = db.session.query(StudioUser).one()
        assert user.active is False

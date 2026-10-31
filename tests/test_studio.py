from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from flask import Flask
from PIL import Image
from sqlalchemy.orm.attributes import set_committed_value

from app.catalog.models import Category, Product, ProductImage, ProductStatus, Variant, utcnow
from app.content import blog as blog_service
from app.content.models import BlogPost, BlogRevision, BlogStatus
from app.extensions import db
from app.studio import service
from app.studio.models import (
    ApprovalStatus,
    InventoryHistory,
    PriceHistory,
    ProductHistory,
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
    assert b"data-gg-newsletter" not in response.data


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


def test_ntfy_approval_payload_is_valid_for_ntfy(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    sent: list[urllib.request.Request] = []

    class _Ok:
        def __enter__(self) -> _Ok:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req) or _Ok())
    app.config.update(
        STUDIO_APPROVAL_BACKEND="ntfy",
        STUDIO_NTFY_SERVER="https://ntfy.example",
        STUDIO_NTFY_TOPIC="private-topic",
    )
    _user(app)
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    assert len(sent) == 1 and sent[0].full_url == "https://ntfy.example/"
    payload = json.loads(sent[0].data)  # type: ignore[arg-type]
    assert payload["topic"] == "private-topic"
    # ntfy's JSON API rejects the whole message (HTTP 400) unless priority is a number 1-5.
    assert isinstance(payload["priority"], int) and 1 <= payload["priority"] <= 5
    assert payload["click"].startswith("http") and payload["actions"][0]["url"] == payload["click"]


def test_ntfy_refusals_are_logged_with_ntfys_reason(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error
    import urllib.request

    def refuse(req: urllib.request.Request, timeout: float) -> None:
        body = BytesIO(b'{"code":42908,"http":429,"error":"limit reached: too many requests"}')
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, body)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    logged_lines: list[str] = []  # the app's log setup may stop propagation, so record directly
    monkeypatch.setattr(service._log, "error", lambda msg, *a: logged_lines.append(msg % a))
    app.config.update(
        STUDIO_APPROVAL_BACKEND="ntfy",
        STUDIO_NTFY_SERVER="https://ntfy.example",
        STUDIO_NTFY_TOPIC="private-topic",
    )
    _user(app)
    response = client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    assert response.status_code == 302  # the sign-in still waits; the owner can retry
    logged = " ".join(logged_lines)
    assert "status=429" in logged and "limit reached" in logged
    assert "private-topic" not in logged


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
    assert client.get(PREFIX + "/prices").status_code == 200  # every account (owner, 2026-10-30)
    assert client.get(PREFIX + "/community").status_code == 403  # not theirs
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


def test_every_role_may_edit_products_but_not_moderate(catalog_app: Flask) -> None:
    """Owner, 2026-10-30: all Studio accounts get the product tools; community stays scoped."""
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="inventory", change=True)
    with catalog_app.app_context():
        product_id = db.session.query(Product.id).first()[0]
    assert client.get(PREFIX + "/products").status_code == 200
    assert client.get(PREFIX + f"/products/{product_id}").status_code == 200
    assert client.get(PREFIX + "/community").status_code == 403


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


def _bulk_form(category_id: int, **extra: str) -> dict[str, str]:
    form = {"category": str(category_id), "kind": "raise_percent", "amount": "10"}
    form.update(reason="Supplier increase", **extra)
    return form


def _first_category(app: Flask) -> tuple[int, list[tuple[int, int]]]:
    """A category with priced options, and its (variant id, price) pairs after clearing sales."""
    with app.app_context():
        variant = db.session.query(Variant).filter(Variant.active.is_(True)).first()
        assert variant is not None
        category = variant.product.categories[0]
        ids = [category.id, *(child.id for child in category.children)]
        variants = (
            db.session.query(Variant)
            .join(Variant.product)
            .filter(Variant.active.is_(True), Product.categories.any(Category.id.in_(ids)))
            .all()
        )
        for item in variants:
            item.compare_at_cents = None
        db.session.commit()
        return category.id, [(item.id, item.price_cents) for item in variants]


def test_bulk_price_preview_saves_nothing_then_applies_with_history(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    category_id, before = _first_category(catalog_app)
    page = client.get(
        PREFIX + "/prices/bulk", query_string={**_bulk_form(category_id), "preview": 1}
    )
    text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Raise by 10%" in text and "Save these" in text
    fingerprint = re.search(r'name="fingerprint" value="([0-9a-f]{64})"', text)
    assert fingerprint
    with catalog_app.app_context():
        assert [(v, db.session.get(Variant, v).price_cents) for v, _ in before] == before
        assert db.session.query(PriceHistory).count() == 0

    response = client.post(
        PREFIX + "/prices/bulk",
        data={
            **_bulk_form(category_id),
            "fingerprint": fingerprint[1],
            "step_up_password": NEW_PASSWORD,
        },
    )
    assert response.status_code == 302 and response.headers["Location"].endswith("/prices/history")
    with catalog_app.app_context():
        for variant_id, cents in before:
            expected = int((Decimal(cents) * Decimal("1.1")).quantize(Decimal("1"), ROUND_HALF_UP))
            assert db.session.get(Variant, variant_id).price_cents == expected
        history = db.session.query(PriceHistory).all()
        assert len(history) == len(before)
        assert all(item.reason == "Bulk: Supplier increase" for item in history)
        assert db.session.query(StudioAudit).filter_by(action="studio.price.bulk_updated").count()


def test_bulk_price_refuses_a_stale_preview(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    category_id, before = _first_category(catalog_app)
    text = client.get(
        PREFIX + "/prices/bulk", query_string={**_bulk_form(category_id), "preview": 1}
    ).get_data(as_text=True)
    fingerprint = re.search(r'name="fingerprint" value="([0-9a-f]{64})"', text)
    assert fingerprint
    with catalog_app.app_context():
        db.session.get(Variant, before[0][0]).price_cents += 100  # someone changed one price
        db.session.commit()
    response = client.post(
        PREFIX + "/prices/bulk",
        data={
            **_bulk_form(category_id),
            "fingerprint": fingerprint[1],
            "step_up_password": NEW_PASSWORD,
        },
        follow_redirects=True,
    )
    assert b"changed since the preview" in response.data
    with catalog_app.app_context():
        assert db.session.query(PriceHistory).count() == 0


def test_bulk_price_needs_a_selection_and_skips_sale_conflicts(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    category_id, before = _first_category(catalog_app)
    everything = client.get(
        PREFIX + "/prices/bulk",
        query_string={"kind": "lower_percent", "amount": "5", "reason": "x y", "preview": 1},
    )
    assert b"can&#39;t hit everything" in everything.data
    with catalog_app.app_context():
        variant = db.session.get(Variant, before[0][0])
        variant.compare_at_cents = variant.price_cents + 50
        db.session.commit()
        plan = service.plan_bulk_price(_bulk_form(category_id))
        row = next(r for r in plan.rows if r.variant.id == before[0][0])
        assert row.skip == "Would reach its compare-at (was) price" and row.after == row.before


def test_bulk_price_rounding_to_99() -> None:
    assert service._bulk_new_price(4000, "raise_percent", Decimal("10"), True) == 4399
    assert service._bulk_new_price(4599, "set", Decimal("45.99"), True) == 4599
    assert service._bulk_new_price(4500, "lower_amount", Decimal("5"), True) == 3999
    assert service._bulk_new_price(4000, "raise_amount", Decimal("2.50"), False) == 4250


def _preview_fingerprint(client, path: str, form: dict[str, str]) -> str:  # type: ignore[no-untyped-def]
    text = client.get(PREFIX + path, query_string={**form, "preview": 1}).get_data(as_text=True)
    found = re.search(r'name="fingerprint" value="([0-9a-f]{64})"', text)
    assert found, text[-2000:]
    return found[1]


def test_bulk_stock_previews_then_applies_with_history(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    category_id, before = _first_category(catalog_app)
    with catalog_app.app_context():
        stock = {v: db.session.get(Variant, v).stock_quantity for v, _ in before}
    form = {"category": str(category_id), "kind": "add", "amount": "3", "reason": "Delivery"}
    fingerprint = _preview_fingerprint(client, "/inventory/bulk", form)
    with catalog_app.app_context():
        assert db.session.query(InventoryHistory).count() == 0  # preview saves nothing
    response = client.post(
        PREFIX + "/inventory/bulk",
        data={**form, "fingerprint": fingerprint, "step_up_password": NEW_PASSWORD},
    )
    assert response.headers["Location"].endswith("/inventory/history")
    with catalog_app.app_context():
        for variant_id, quantity in stock.items():
            variant = db.session.get(Variant, variant_id)
            expected = quantity + 3 if variant.track_inventory else quantity
            assert variant.stock_quantity == expected
        assert all(h.reason == "Bulk: Delivery" for h in db.session.query(InventoryHistory))


def test_bulk_stock_skips_going_below_zero(catalog_app: Flask) -> None:
    category_id, before = _first_category(catalog_app)
    with catalog_app.app_context():
        db.session.get(Variant, before[0][0]).stock_quantity = 1
        db.session.commit()
        form = {"category": str(category_id), "kind": "remove", "amount": "2", "reason": "Damaged"}
        row = next(r for r in service.plan_bulk_stock(form).rows if r.variant.id == before[0][0])
        assert row.skip == "Would go below zero" and row.after == 1


def test_risky_actions_need_the_password_again(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    category_id, before = _first_category(catalog_app)
    form = {"category": str(category_id), "kind": "set", "amount": "50", "reason": "Recount"}
    fingerprint = _preview_fingerprint(client, "/inventory/bulk", form)
    for password in ("", "wrong password here"):
        page = client.post(
            PREFIX + "/inventory/bulk",
            data={**form, "fingerprint": fingerprint, "step_up_password": password},
            follow_redirects=True,
        )
        assert b"password is not right" in page.data
    with catalog_app.app_context():
        assert db.session.query(InventoryHistory).count() == 0
        failures = db.session.query(StudioAudit).filter_by(action="studio.step_up.failed").count()
        assert failures == 2
    for _ in range(service.STEP_UP_TRIES):
        client.post(
            PREFIX + "/inventory/bulk",
            data={**form, "fingerprint": fingerprint, "step_up_password": "nope nope nope"},
        )
    locked = client.post(
        PREFIX + "/inventory/bulk",
        data={**form, "fingerprint": fingerprint, "step_up_password": NEW_PASSWORD},
        follow_redirects=True,
    )
    assert b"Too many wrong passwords" in locked.data


def test_unpublishing_asks_for_the_password_but_publishing_does_not(app: Flask) -> None:
    client = app.test_client()
    _approved_cookie(app, client, roles="editor", change=True)
    client.post(PREFIX + "/blog/new", data=_blog_form())
    with app.app_context():
        post_id = db.session.query(BlogPost.id).scalar()
    client.post(PREFIX + f"/blog/{post_id}/publication", data={"action": "publish"})
    assert client.get("/blog/studio-journal-story/").status_code == 200
    page = client.post(
        PREFIX + f"/blog/{post_id}/publication", data={"action": "unpublish"}, follow_redirects=True
    )
    assert b"password is not right" in page.data
    assert client.get("/blog/studio-journal-story/").status_code == 200
    client.post(
        PREFIX + f"/blog/{post_id}/publication",
        data={"action": "unpublish", "step_up_password": NEW_PASSWORD},
    )
    assert client.get("/blog/studio-journal-story/").status_code == 404


def _active_product(app: Flask) -> tuple[int, str, list[str]]:
    with app.app_context():
        product = db.session.query(Product).filter_by(status=ProductStatus.ACTIVE).first()
        assert product is not None
        return product.id, product.name, [str(c.id) for c in product.categories]


def test_hiding_a_product_is_recorded_and_can_be_undone(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    product_id, _, _ = _active_product(catalog_app)
    client.post(
        PREFIX + f"/products/{product_id}/visibility",
        data={"live": "0", "reason": "Sold out", "expected": "active"},
    )
    with catalog_app.app_context():
        assert db.session.get(Product, product_id).status == ProductStatus.DRAFT
        history_id = db.session.query(ProductHistory).one().id
    page = client.get(PREFIX + "/products/history").get_data(as_text=True)
    assert "Live → Hidden" in page and "Sold out" in page
    done = client.post(PREFIX + f"/products/history/{history_id}/undo", follow_redirects=True)
    assert b"Product change undone" in done.data
    with catalog_app.app_context():
        assert db.session.get(Product, product_id).status == ProductStatus.ACTIVE
        assert db.session.get(ProductHistory, history_id).undone_at is not None


def test_details_edit_can_be_undone_but_not_after_a_later_change(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    product_id, name, category_ids = _active_product(catalog_app)

    def save(new_name: str) -> None:
        client.post(
            PREFIX + f"/products/{product_id}",
            data={"name": new_name, "summary": "", "description": "", "category_ids": category_ids},
        )

    save("First New Name")
    save("Second New Name")
    with catalog_app.app_context():
        first, second = db.session.query(ProductHistory).order_by(ProductHistory.id).all()
        first_id, second_id = first.id, second.id
    refused = client.post(PREFIX + f"/products/history/{first_id}/undo", follow_redirects=True)
    assert b"cannot be safely undone" in refused.data
    client.post(PREFIX + f"/products/history/{second_id}/undo")
    with catalog_app.app_context():
        assert db.session.get(Product, product_id).name == "First New Name"
    client.post(PREFIX + f"/products/history/{first_id}/undo")
    with catalog_app.app_context():
        assert db.session.get(Product, product_id).name == name


def test_bulk_hide_previews_needs_password_and_applies(catalog_app: Flask) -> None:
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="editor", change=True)
    category_id, _ = _first_category(catalog_app)
    form = {"category": str(category_id), "kind": "hide", "reason": "End of season"}
    fingerprint = _preview_fingerprint(client, "/products/bulk", form)
    with catalog_app.app_context():
        live = [
            p.id
            for p in db.session.query(Product).filter_by(status=ProductStatus.ACTIVE)
            if any(c.id == category_id or c.parent_id == category_id for c in p.categories)
        ]
        assert live
    refused = client.post(
        PREFIX + "/products/bulk", data={**form, "fingerprint": fingerprint}, follow_redirects=True
    )
    assert b"password is not right" in refused.data
    client.post(
        PREFIX + "/products/bulk",
        data={**form, "fingerprint": fingerprint, "step_up_password": NEW_PASSWORD},
    )
    with catalog_app.app_context():
        assert all(db.session.get(Product, i).status == ProductStatus.DRAFT for i in live)
        reasons = {h.reason for h in db.session.query(ProductHistory)}
        assert reasons == {"Bulk: End of season"}


def test_every_role_undoes_both_kinds_of_product_change(catalog_app: Flask) -> None:
    with catalog_app.app_context():
        product = db.session.query(Product).filter_by(status=ProductStatus.ACTIVE).first()
        details = ProductHistory(
            product_id=product.id, kind="details", before="{}", after="{}", reason=""
        )
        hidden = ProductHistory(
            product_id=product.id, kind="visibility", before="{}", after="{}", reason=""
        )
        db.session.add_all([details, hidden])
        db.session.commit()
        details_id, hidden_id = details.id, hidden.id
    client = catalog_app.test_client()
    _approved_cookie(catalog_app, client, roles="maintenance", change=True)
    assert client.post(PREFIX + f"/products/history/{details_id}/undo").status_code == 302
    assert client.post(PREFIX + f"/products/history/{hidden_id}/undo").status_code == 302


def test_one_approval_can_never_become_two_sessions(app: Flask, client) -> None:
    _user(app)
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    with app.test_request_context(PREFIX + "/"):
        record = db.session.query(StudioLoginRequest).one()
        service.decide(record, True)
        service.consume_approval(record)
        # A second request racing the first still sees "approved" in its own (stale) copy.
        set_committed_value(record, "status", ApprovalStatus.APPROVED)
        with pytest.raises(service.StudioError, match="already been used"):
            service.consume_approval(record)
        assert db.session.query(StudioSession).count() == 1


def test_approve_cannot_override_a_denial(app: Flask, client) -> None:
    _user(app)
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    with app.test_request_context(PREFIX + "/"):
        record = db.session.query(StudioLoginRequest).one()
        service.decide(record, False)
        set_committed_value(record, "status", ApprovalStatus.PENDING)  # a racing request's view
        with pytest.raises(service.StudioError, match="already decided"):
            service.decide(record, True)
        assert db.session.get(StudioLoginRequest, record.id).status == ApprovalStatus.DENIED


def test_owner_approves_staff_from_the_push_with_an_authenticator_code(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.admin import service as admin_service
    from app.admin.models import AdminRole, AdminUser
    from tests.test_admin import admin_client

    links: list[str] = []
    monkeypatch.setattr(
        service, "send_push", lambda title, message, link, *a, **k: links.append(link)
    )
    admin_client(app, AdminRole.OWNER)  # the owner exists; the phone below is signed out
    _user(app)
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    link = PREFIX + "/approve/" + links[0].rsplit("/approve/", 1)[1]
    phone = app.test_client()
    assert b"Code from your authenticator app" in phone.get(link).data
    assert b"That code" in phone.post(link, data={"decision": "approve", "code": "000000"}).data
    with app.app_context():
        assert db.session.query(StudioLoginRequest).one().status == ApprovalStatus.PENDING
        owner = db.session.query(AdminUser).one()
        code = admin_service.totp(owner.totp_secret)
    phone.post(link, data={"decision": "approve", "code": code})
    with app.app_context():
        assert db.session.query(StudioLoginRequest).one().status == ApprovalStatus.APPROVED
    assert phone.get("/admin/").status_code == 302  # approving never signed the phone in


def test_staff_requests_decline_without_a_code_and_lock_after_wrong_codes(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.admin import service as admin_service
    from app.admin.models import AdminRole, AdminUser
    from tests.test_admin import admin_client

    links: list[str] = []
    monkeypatch.setattr(
        service, "send_push", lambda title, message, link, *a, **k: links.append(link)
    )
    admin_client(app, AdminRole.OWNER)
    _user(app)
    client.post(PREFIX + "/", data={"username": "ada", "password": PASSWORD})
    link = PREFIX + "/approve/" + links[0].rsplit("/approve/", 1)[1]
    phone = app.test_client()
    for _ in range(admin_service.APPROVE_CODE_TRIES):
        phone.post(link, data={"decision": "approve", "code": "000000"})
    with app.app_context():
        owner = db.session.query(AdminUser).one()
        code = admin_service.totp(owner.totp_secret)
    blocked = phone.post(link, data={"decision": "approve", "code": code})
    assert b"Too many wrong codes" in blocked.data
    phone.post(link, data={"decision": "deny"})
    with app.app_context():
        assert db.session.query(StudioLoginRequest).one().status == ApprovalStatus.DENIED

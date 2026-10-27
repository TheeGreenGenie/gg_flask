from __future__ import annotations

import time
from io import BytesIO

from flask import Flask
from flask.testing import FlaskClient
from PIL import Image
from sqlalchemy import select

from app.admin import service
from app.admin.models import AdminAudit, AdminRecoveryCode, AdminRole, AdminUser
from app.catalog.models import Category, Product, ProductImage, Variant
from app.extensions import db
from app.orders.models import Coupon


def make_admin(app: Flask) -> tuple[str, list[str]]:
    with app.test_request_context("/"):
        admin, codes = service.create_admin(
            "owner", "owner@example.com", "Long unique phrase 482!", AdminRole.OWNER
        )
        return admin.totp_secret, codes


def password_step(client: FlaskClient) -> None:
    response = client.post(
        "/admin/login",
        data={"username": "owner", "password": "Long unique phrase 482!"},
    )
    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/verify")


def test_admin_requires_password_then_totp(client: FlaskClient, app: Flask) -> None:
    secret, _ = make_admin(app)
    assert client.get("/admin/").status_code == 302
    password_step(client)
    response = client.post("/admin/verify", data={"code": service.totp(secret)})
    assert response.status_code == 302 and response.headers["Location"].endswith("/admin/")
    page = client.get("/admin/")
    assert page.status_code == 200 and b"Signed in as owner" in page.data
    assert page.headers["Cache-Control"] == "private, no-store"
    assert page.headers["X-Robots-Tag"] == "noindex, nofollow"
    with app.app_context():
        actions = db.session.scalars(select(AdminAudit.action).order_by(AdminAudit.id)).all()
        assert actions == ["admin.created", "admin.login"]


def test_bad_password_is_generic(client: FlaskClient, app: Flask) -> None:
    make_admin(app)
    response = client.post("/admin/login", data={"username": "nope", "password": "bad"})
    assert b"Username or password is incorrect" in response.data


def test_recovery_code_works_once(client: FlaskClient, app: Flask) -> None:
    _, codes = make_admin(app)
    password_step(client)
    assert client.post("/admin/verify", data={"code": codes[0]}).status_code == 302
    client.post("/admin/logout")
    password_step(client)
    assert b"not valid" in client.post("/admin/verify", data={"code": codes[0]}).data
    with app.app_context():
        used = db.session.scalar(
            select(AdminRecoveryCode).where(AdminRecoveryCode.used_at.is_not(None))
        )
        assert used is not None


def test_admin_session_idle_timeout(client: FlaskClient, app: Flask) -> None:
    secret, _ = make_admin(app)
    password_step(client)
    client.post("/admin/verify", data={"code": service.totp(secret)})
    with client.session_transaction() as sess:
        sess["admin_seen_at"] = int(time.time()) - service.IDLE_SECONDS - 1
    assert client.get("/admin/").status_code == 302


def test_admin_changes_password_with_current_password(client: FlaskClient, app: Flask) -> None:
    app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    secret, _ = make_admin(app)
    password_step(client)
    client.post("/admin/verify", data={"code": service.totp(secret)})

    wrong = client.post(
        "/admin/password",
        data={
            "current_password": "incorrect password",
            "new_password": "Different secure phrase 936!",
            "confirm_password": "Different secure phrase 936!",
        },
    )
    assert b"current password is incorrect" in wrong.data

    changed = client.post(
        "/admin/password",
        data={
            "current_password": "Long unique phrase 482!",
            "new_password": "Different secure phrase 936!",
            "confirm_password": "Different secure phrase 936!",
        },
        follow_redirects=True,
    )
    assert b"Other administrator sessions were signed out" in changed.data
    with app.app_context():
        admin = db.session.scalar(select(AdminUser).where(AdminUser.username == "owner"))
        assert admin is not None
        assert "Different secure phrase" not in admin.password_hash
        assert "admin.password.changed" in db.session.scalars(select(AdminAudit.action)).all()
    notice = app.extensions["outbox"][-1]
    assert "Different secure phrase" not in notice.body
    assert "password itself is not stored or sent" in notice.body

    client.post("/admin/logout")
    old = client.post(
        "/admin/login",
        data={"username": "owner", "password": "Long unique phrase 482!"},
    )
    assert b"Username or password is incorrect" in old.data
    new = client.post(
        "/admin/login",
        data={"username": "owner", "password": "Different secure phrase 936!"},
    )
    assert new.status_code == 302 and new.headers["Location"].endswith("/admin/verify")


def test_admin_password_change_reports_password_policy_error(
    client: FlaskClient, app: Flask
) -> None:
    secret, _ = make_admin(app)
    password_step(client)
    client.post("/admin/verify", data={"code": service.totp(secret)})
    response = client.post(
        "/admin/password",
        data={
            "current_password": "Long unique phrase 482!",
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert response.status_code == 200
    assert b"Use a password of" in response.data


def test_admin_password_reset_cli_uses_username_and_revokes_sessions(app: Flask) -> None:
    with app.app_context():
        admin, _ = service.create_admin(
            "owner", "owner@example.com", "Long unique phrase 482!", AdminRole.OWNER
        )
        before_version = admin.session_version
    result = app.test_cli_runner().invoke(
        args=["admin", "reset-password", "owner"],
        input="Replacement secure phrase 741!\nReplacement secure phrase 741!\n",
    )
    assert result.exit_code == 0
    assert "all existing sessions were revoked" in result.output
    with app.app_context():
        admin = db.session.scalar(select(AdminUser).where(AdminUser.username == "owner"))
        assert admin is not None and admin.session_version == before_version + 1


def test_totp_accepts_small_clock_drift() -> None:
    secret = service.generate_totp_secret()
    now = 2_000_000_000
    assert service.verify_totp(secret, service.totp(secret, now - 30), now)
    assert not service.verify_totp(secret, "12345", now)


def test_admin_create_cli(app: Flask) -> None:
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "admin",
            "create",
            "--username",
            "catalog-admin",
            "--email",
            "admin@example.com",
            "--role",
            "catalog",
        ],
        input="Another unique phrase 782!\nAnother unique phrase 782!\n",
    )
    assert result.exit_code == 0
    assert "otpauth://totp/" in result.output and "Recovery codes" in result.output


def admin_client(catalog_app: Flask, role: AdminRole = AdminRole.OWNER) -> FlaskClient:
    with catalog_app.test_request_context("/"):
        admin, _ = service.create_admin(
            role.value,
            f"{role.value}@example.com",
            "Long unique phrase 482!",
            role,
        )
        code = service.totp(admin.totp_secret)
    client = catalog_app.test_client()
    client.post(
        "/admin/login",
        data={"username": role.value, "password": "Long unique phrase 482!"},
    )
    client.post("/admin/verify", data={"code": code})
    return client


def test_catalog_product_variant_and_category_edits_are_audited(catalog_app: Flask) -> None:
    client = admin_client(catalog_app)
    with catalog_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        variant = product.variants[0]
        category = product.categories[0]
        product_id, variant_id, category_id = product.id, variant.id, category.id
    product_response = client.post(
        f"/admin/catalog/products/{product_id}",
        data={
            "name": "Updated Ankara",
            "summary": "Updated summary",
            "description": "Updated description",
            "product_type": "fabric",
            "status": "active",
            "featured": "1",
            "category_ids": str(category_id),
        },
    )
    assert product_response.status_code == 302
    variant_response = client.post(
        f"/admin/catalog/variants/{variant_id}",
        data={
            "sku": "UPDATED-1",
            "price": "12.34",
            "compare_at": "15.00",
            "stock": "7",
            "weight_grams": "500",
            "track_inventory": "1",
            "active": "1",
        },
    )
    assert variant_response.status_code == 302
    assert (
        client.post(
            f"/admin/catalog/categories/{category_id}",
            data={"name": "Updated Category", "description": "New description", "position": "3"},
        ).status_code
        == 302
    )
    with catalog_app.app_context():
        assert db.session.get(Product, product_id).name == "Updated Ankara"
        saved_variant = db.session.get(Variant, variant_id)
        assert (saved_variant.price_cents, saved_variant.stock_quantity) == (1234, 7)
        assert db.session.get(Category, category_id).name == "Updated Category"
        actions = set(db.session.scalars(select(AdminAudit.action)).all())
        assert {
            "catalog.product.updated",
            "catalog.variant.updated",
            "catalog.category.updated",
        } <= actions


def test_safe_image_upload_reencodes_and_can_delete(catalog_app: Flask) -> None:
    client = admin_client(catalog_app)
    with catalog_app.app_context():
        product_id = db.session.scalar(select(Product.id).where(Product.slug == "ankara-blue"))
    source = BytesIO()
    Image.new("RGB", (40, 30), "red").save(source, "PNG")
    source.seek(0)
    response = client.post(
        f"/admin/catalog/products/{product_id}/images",
        data={"alt": "A red test fabric", "image": (source, "tricky.php.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with catalog_app.app_context():
        image = db.session.scalar(
            select(ProductImage).where(ProductImage.alt == "A red test fabric")
        )
        assert image.path.endswith(".webp") and "/uploads/" in image.path
        path = catalog_app.config["MEDIA_ROOT"] + "/" + image.path
        with Image.open(path) as saved:
            assert saved.format == "WEBP" and saved.size == (40, 30)
        image_id = image.id
    assert client.post(f"/admin/catalog/images/{image_id}/delete").status_code == 302
    with catalog_app.app_context():
        assert db.session.get(ProductImage, image_id) is None


def test_non_image_upload_is_rejected(catalog_app: Flask) -> None:
    client = admin_client(catalog_app)
    with catalog_app.app_context():
        product_id = db.session.scalar(select(Product.id).where(Product.slug == "ankara-blue"))
    response = client.post(
        f"/admin/catalog/products/{product_id}/images",
        data={"alt": "Not really an image", "image": (BytesIO(b"<?php evil();"), "evil.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"not a safe, readable image" in response.data


def test_support_role_cannot_edit_catalog(catalog_app: Flask) -> None:
    client = admin_client(catalog_app, AdminRole.SUPPORT)
    assert client.get("/admin/catalog/").status_code == 403
    assert client.get("/admin/orders/").status_code == 200
    assert client.get("/admin/coupons/").status_code == 403


def test_coupon_manager_validates_saves_and_audits(catalog_app: Flask) -> None:
    client = admin_client(catalog_app)
    response = client.post(
        "/admin/coupons/new",
        data={
            "code": "welcome-10",
            "discount_type": "percent",
            "amount": "10",
            "minimum_subtotal_cents": "2500",
            "max_uses": "100",
            "active": "1",
        },
    )
    assert response.status_code == 302
    with catalog_app.app_context():
        coupon = db.session.scalar(select(Coupon).where(Coupon.code == "WELCOME-10"))
        assert coupon and coupon.amount == 10 and coupon.minimum_subtotal_cents == 2500
        assert "coupon.saved" in db.session.scalars(select(AdminAudit.action)).all()

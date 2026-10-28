"""Precut pieces / bundle lengths, the 16 oz/sq yd weight estimate and order parcel weights."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

from app.admin import service as admin_service
from app.admin.catalog import CatalogError, update_product
from app.admin.models import AdminRole
from app.cart import service as cart_service
from app.catalog import manifest as mf
from app.catalog import units
from app.catalog.models import Product, ProductType
from app.extensions import db
from app.orders import providers, service
from app.orders.models import PaymentProvider
from app.studio import service as studio_service
from tests.catalog_fixtures import manifest
from tests.test_orders import _variant_id, address


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", "1.00"), ("4.5", "4.50"), ("4 1/2", "4.50"), ("6 yards", "6.00"), ("", None)],
)
def test_piece_length_parsing(raw: str, expected: str | None) -> None:
    value = units.parse_piece_length(raw)
    assert (None if value is None else str(value)) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "1.234", "4 1/0", "2000"])
def test_piece_length_rejects_nonsense(raw: str) -> None:
    with pytest.raises(ValueError, match="piece length|yards"):
        units.parse_piece_length(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("4-5", ("4.00", "5.00")), ("4 – 5 yards", ("4.00", "5.00")), ("4.5 to 6", ("4.50", "6.00")),
     ("4.5", ("4.50", None)), ("", (None, None))],
)  # fmt: skip
def test_piece_length_range_parsing(raw: str, expected: tuple[str | None, str | None]) -> None:
    low, high = units.parse_piece_range(raw)
    assert (str(low) if low else None, str(high) if high else None) == expected
    assert units.format_piece_length(low, high) == {"4.5": "4.5", "": ""}.get(
        raw, f"{low.normalize():f}-{high.normalize():f}" if low and high else ""
    )


@pytest.mark.parametrize("raw", ["5-4", "4-4", "4-", "-5", "4-abc"])
def test_piece_length_range_rejects_backwards_or_broken(raw: str) -> None:
    with pytest.raises(ValueError):
        units.parse_piece_range(raw)


def test_range_label_and_weight_use_the_longest_piece() -> None:
    bundle = Product(
        product_type=ProductType.FABRIC,
        piece_length_yards=Decimal(4),
        piece_length_max_yards=Decimal(5),
        width_inches=36,
        sale_unit="fixed_bundle",
    )
    assert bundle.piece_label == "4–5-yard bundle"
    assert bundle.piece_length_text == "4-5"
    assert bundle.estimated_weight_grams == 2268  # 5 sq yd x 16 oz, never under-estimated


def test_width_parsing() -> None:
    assert units.parse_width('45"') == 45 and units.parse_width("") is None
    for bad in ("0", "4.5", "wide", "300"):
        with pytest.raises(ValueError, match="width"):
            units.parse_width(bad)


@pytest.mark.parametrize(
    ("grams", "step"),
    [(100, "up to 4 oz"), (226, "up to 8 oz"), (453, "up to 15.99 oz"), (454, "up to 2 lb"),
     (908, "up to 3 lb")],
)  # fmt: skip
def test_usps_price_steps(grams: int, step: str) -> None:
    assert units.usps_weight_step(grams) == step


def test_weight_formatting_never_shows_sixteen_ounces() -> None:
    assert units.format_weight(567) == "1 lb 4 oz (567 g)"
    assert units.format_weight(906) == "2 lb 0 oz (906 g)"
    assert units.format_weight(278) == "9.8 oz (278 g)"


def test_fabric_piece_estimate_uses_16_oz_per_square_yard() -> None:
    piece = Product(
        product_type=ProductType.FABRIC, piece_length_yards=Decimal(1), sale_unit="fixed_bundle"
    )
    # 1 yd x 45 in (default width) = 1.25 sq yd x 16 oz = 20 oz = 567 g
    assert piece.estimated_weight_grams == 567
    assert piece.piece_label == "1-yard precut piece"
    bundle = Product(
        product_type=ProductType.FABRIC,
        piece_length_yards=Decimal("4.5"),
        width_inches=36,
        sale_unit="fixed_bundle",
    )
    assert bundle.piece_label == "4.5-yard bundle"
    assert bundle.estimated_weight_grams == 2042  # 4.5 sq yd x 16 oz
    cushion = Product(product_type=ProductType.HOME_ACCESSORIES, piece_length_yards=Decimal(1))
    assert cushion.estimated_weight_grams is None


def test_manifest_accepts_and_validates_piece_size(app: Flask, assets: Path) -> None:
    data = manifest()
    data["products"][0].update(piece_length_yards=1, width_inches=45)
    with app.app_context():
        mf.load(data, assets, Path(app.config["MEDIA_ROOT"]))
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        assert (product.piece_length_yards, product.width_inches) == (Decimal("1.00"), 45)
    bad = manifest()
    bad["products"][0].update(piece_length_yards=0, width_inches=4.5)
    errors = mf.validate(bad, assets)
    assert any("piece_length_yards" in e for e in errors)
    assert any("width_inches" in e for e in errors)
    ranged = manifest()
    ranged["products"][0].update(piece_length_yards=4, piece_length_max_yards=5)
    assert mf.validate(ranged, assets) == []
    ranged["products"][0].update(piece_length_max_yards=3)
    assert any("piece_length_max_yards" in e for e in mf.validate(ranged, assets))


def test_product_page_shows_the_piece(catalog_app: Flask, shop: FlaskClient) -> None:
    with catalog_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        product.piece_length_yards = Decimal(1)
        db.session.commit()
    html = shop.get("/product/ankara-blue/").get_data(as_text=True)
    assert "Price per 1-yard precut piece." in html


def test_admin_and_studio_edit_piece_size(catalog_app: Flask) -> None:
    with catalog_app.test_request_context("/"):
        admin, _ = admin_service.create_admin(
            "owner", "owner@example.com", "Long unique phrase 482!", AdminRole.OWNER
        )
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        form = {
            "name": product.name,
            "status": product.status.value,
            "product_type": product.product_type.value,
            "sale_unit": "fixed_bundle",
            "piece_length_yards": "6",
            "width_inches": "46",
            "category_ids": ",".join(str(c.id) for c in product.categories),
        }
        update_product(admin, product, form)
        assert (product.piece_length_yards, product.width_inches) == (Decimal("6.00"), 46)
        update_product(admin, product, {**form, "piece_length_yards": "4-5"})
        assert (product.piece_length_yards, product.piece_length_max_yards) == (4, 5)
        update_product(admin, product, {**form, "piece_length_yards": "6"})
        assert product.piece_length_max_yards is None  # back to a single length
        with pytest.raises(CatalogError, match="piece length"):
            update_product(admin, product, {**form, "piece_length_yards": "0"})
        with pytest.raises(CatalogError, match="smaller length first"):
            update_product(admin, product, {**form, "piece_length_yards": "5-4"})
        db.session.rollback()

    with catalog_app.test_request_context("/studio-testing-private-path/"):
        staff = studio_service.create_user("ada", "Temporary phrase 8472!", "editor")
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        cats = [str(c.id) for c in product.categories]
        studio_service.update_product_details(
            staff, product, product.name, "", "", cats, "fixed_bundle", "1", ""
        )
        assert (product.piece_length_yards, product.width_inches) == (Decimal("1.00"), None)
        # Forms without the fields leave them alone.
        studio_service.update_product_details(staff, product, product.name, "", "", cats)
        assert product.piece_length_yards == Decimal("1.00")


def test_orders_record_parcel_weight(catalog_app: Flask, shop: FlaskClient) -> None:
    with catalog_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert product is not None
        product.piece_length_yards = Decimal(1)
        db.session.commit()
    shop.post("/cart/add", data={"variant": _variant_id(catalog_app, "101001"), "quantity": 2})
    cookie = shop.get_cookie(cart_service.COOKIE)
    assert cookie is not None
    with catalog_app.test_request_context(
        "/checkout/", headers={"Cookie": f"{cookie.key}={cookie.value}"}
    ):
        order = service.create_order(
            cart_service.current_cart(), address(), PaymentProvider.STRIPE, "w" * 32
        )
        assert order.parcel_weight_grams == 2 * 567 + providers.PACKAGING_WEIGHT_GRAMS
        db.session.rollback()

"""Customers can find prints by colour (owner, 2026-10-30)."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app.catalog.models import Product
from app.extensions import db

BLUE = "African Ankara Print - 101001"
CUSHION = "Cushion Cover G-1"
DRAFT = "Unreleased Print"


def _paint(app: Flask, colors: dict[str, str]) -> None:
    with app.app_context():
        for slug, value in colors.items():
            product = db.session.scalar(db.select(Product).where(Product.slug == slug))
            assert product is not None
            product.colors = value
        db.session.commit()


def _shown(html: str) -> set[str]:
    return {name for name in (BLUE, CUSHION, DRAFT) if name in html}


def test_swatches_show_counts_and_filter_the_shop(catalog_app: Flask, shop: FlaskClient) -> None:
    _paint(
        catalog_app,
        {"ankara-blue": "Navy,Cream", "cushion-g1": "Teal,Navy", "draft-thing": "Red"},
    )
    page = shop.get("/shop/").get_data(as_text=True)
    assert "gg-swatch--navy" in page
    assert 'Navy <span class="gg-filters__count">(2)</span>' in page
    assert "gg-swatch--red" not in page  # hidden products never count
    assert _shown(shop.get("/shop/?color=Teal").get_data(as_text=True)) == {CUSHION}
    either = shop.get("/shop/?color=Teal&color=Cream").get_data(as_text=True)
    assert _shown(either) == {BLUE, CUSHION}  # any of the ticked colours
    assert "Clear colours" in either


def test_whole_colour_names_only_and_unknown_colours_are_ignored(
    catalog_app: Flask, shop: FlaskClient
) -> None:
    _paint(catalog_app, {"ankara-blue": "Navy", "cushion-g1": "Tan"})
    assert _shown(shop.get("/shop/?color=Tan").get_data(as_text=True)) == {CUSHION}
    unknown = shop.get("/shop/?color=Sparkly").get_data(as_text=True)
    assert _shown(unknown) == {BLUE, CUSHION}  # dropped, not an empty page


def test_typing_a_colour_in_search_finds_prints_of_that_colour(
    catalog_app: Flask, shop: FlaskClient
) -> None:
    _paint(catalog_app, {"ankara-blue": "Teal,Gold", "cushion-g1": "Black"})
    assert _shown(shop.get("/shop/?q=teal").get_data(as_text=True)) == {BLUE}


def test_colours_work_inside_categories_and_are_kept_when_sorting(
    catalog_app: Flask, shop: FlaskClient
) -> None:
    _paint(catalog_app, {"ankara-blue": "Navy", "cushion-g1": "Navy"})
    fabrics = shop.get("/shop/fabrics/?color=Navy").get_data(as_text=True)
    assert _shown(fabrics) == {BLUE}  # only that category
    assert '<input type="hidden" name="color" value="Navy">' in fabrics

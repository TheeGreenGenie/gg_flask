"""Three-level categories: Fabrics → Wax Prints → Grand Super Wax (owner, 2026-10-29)."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.catalog import manifest as mf
from app.catalog import queries
from app.extensions import db
from app.studio import service as studio_service
from tests.catalog_fixtures import manifest


@pytest.fixture
def wax_shop(app: Flask, assets: Path) -> FlaskClient:
    data = manifest()
    fabrics = data["categories"][0]
    fabrics["children"].append(
        {
            "slug": "wax-prints",
            "name": "Wax Prints",
            "children": [
                {"slug": "grand-super-wax", "name": "Grand Super Wax"},
                {"slug": "regular-wax", "name": "Regular Wax"},
            ],
        }
    )
    # A wax print stays in Ankara Prints and is also a Grand Super Wax.
    data["products"][0]["categories"] = ["ankara", "grand-super-wax"]
    assert not mf.validate(data, assets), mf.validate(data, assets)
    with app.app_context():
        mf.load(data, assets, Path(app.config["MEDIA_ROOT"]))
    return app.test_client()


def test_parent_pages_show_products_from_every_level(wax_shop: FlaskClient) -> None:
    for path in ("/shop/grand-super-wax/", "/shop/wax-prints/", "/shop/fabrics/", "/shop/ankara/"):
        page = wax_shop.get(path)
        assert page.status_code == 200, path
        assert "/product/ankara-blue/" in page.get_data(as_text=True), path
    assert "/product/ankara-blue/" not in wax_shop.get("/shop/regular-wax/").get_data(as_text=True)


def test_breadcrumbs_and_sidebar_show_the_whole_path(wax_shop: FlaskClient) -> None:
    html = wax_shop.get("/shop/grand-super-wax/").get_data(as_text=True)
    start = html.index('class="gg-breadcrumbs"')
    crumbs = html[start : html.index("</nav>", start)]
    assert crumbs.index("/shop/fabrics/") < crumbs.index("/shop/wax-prints/")
    assert 'aria-current="page">Grand Super Wax' in crumbs
    assert 'href="/shop/regular-wax/"' in html  # the third level is listed in the sidebar


def test_counts_count_each_product_once(wax_shop: FlaskClient, app: Flask) -> None:
    with app.app_context():
        counts = queries.category_counts()
        fabrics = queries.category_by_slug("fabrics")
        wax = queries.category_by_slug("wax-prints")
        ankara = queries.category_by_slug("ankara")
        assert fabrics and wax and ankara
        assert counts[wax.id] == 1
        # In Ankara and in Grand Super Wax, but one product: Fabrics is not inflated.
        assert counts[fabrics.id] == counts[ankara.id]


def test_tree_helpers_and_find_fabrics(wax_shop: FlaskClient, app: Flask) -> None:
    with app.app_context():
        grand = queries.category_by_slug("grand-super-wax")
        wax = queries.category_by_slug("wax-prints")
        assert grand and wax
        assert [c.slug for c in grand.ancestors] == ["fabrics", "wax-prints"]
        assert grand.id in wax.subtree_ids()
        assert set(wax.subtree_ids()) <= set(queries.discovery_category_ids("fabrics"))
        assert grand.id in queries.discovery_category_ids("wax-prints")


def test_studio_bulk_tools_include_every_level(wax_shop: FlaskClient, app: Flask) -> None:
    with app.test_request_context("/"):
        wax = queries.category_by_slug("wax-prints")
        assert wax
        variants, label = studio_service._bulk_selection({"category": str(wax.id), "q": ""})
        assert [v.product.slug for v in variants] == ["ankara-blue"]
        assert "Wax Prints" in label
        db.session.rollback()

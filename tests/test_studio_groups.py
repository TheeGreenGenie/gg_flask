"""Studio groups, new products, blog product previews and list photos (owner, 2026-10-30)."""

# ruff: noqa: S105, S106  (fixture passwords)
from __future__ import annotations

import json

import pytest
from flask import Flask
from sqlalchemy import select

from app.catalog.models import Category, Collection, Product, ProductStatus, Variant
from app.content import blog
from app.content.models import BlogPost, BlogStatus
from app.extensions import db
from app.studio.models import StudioChange
from tests.test_studio_review import PREFIX, _staff_client


@pytest.fixture
def staff_app(catalog_app: Flask) -> Flask:
    catalog_app.config["ORDER_NOTIFICATION_EMAIL"] = "owner@example.com"
    return catalog_app


def _category(app: Flask, slug: str) -> Category:
    with app.app_context():
        category = db.session.scalar(select(Category).where(Category.slug == slug))
        assert category is not None
        db.session.expunge(category)
        return category


def test_reviewed_staff_create_and_rearrange_categories_live(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "branding")  # review mode: groups still go live
    fabrics = _category(staff_app, "fabrics")
    response = staff.post(
        PREFIX + "/groups/categories/new",
        data={"name": "Kente Cloth", "parent_id": str(fabrics.id), "position": "3"},
    )
    assert response.status_code == 302
    with staff_app.app_context():
        kente = db.session.scalar(select(Category).where(Category.slug == "kente-cloth"))
        assert kente is not None and kente.parent_id == fabrics.id
        assert db.session.scalar(select(StudioChange)) is None
        kente_id = kente.id
    # Renaming keeps the web address, so old links keep working.
    staff.post(PREFIX + f"/groups/categories/{kente_id}", data={"name": "Kente", "parent_id": ""})
    with staff_app.app_context():
        kente = db.session.get(Category, kente_id)
        assert kente is not None and kente.name == "Kente" and kente.slug == "kente-cloth"
        assert kente.parent_id is None
    page = staff.get(PREFIX + "/groups").get_data(as_text=True)
    assert "Kente" in page and "Ankara Prints" in page


def test_category_rules_cycles_depth_and_safe_delete(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "maintenance")
    fabrics = _category(staff_app, "fabrics")
    ankara = _category(staff_app, "ankara")
    inside_itself = staff.post(
        PREFIX + f"/groups/categories/{fabrics.id}",
        data={"name": "Fabrics", "parent_id": str(ankara.id)},
    )
    assert "cannot go inside itself" in inside_itself.get_data(as_text=True)
    staff.post(
        PREFIX + "/groups/categories/new", data={"name": "Deep", "parent_id": str(ankara.id)}
    )
    deep = _category(staff_app, "deep")
    too_deep = staff.post(
        PREFIX + "/groups/categories/new", data={"name": "Deeper", "parent_id": str(deep.id)}
    )
    assert "at most 3 levels" in too_deep.get_data(as_text=True)
    staff.post(PREFIX + f"/groups/categories/{ankara.id}/delete")
    assert _category(staff_app, "ankara")  # has products: kept
    staff.post(PREFIX + f"/groups/categories/{deep.id}/delete")
    with staff_app.app_context():
        assert db.session.scalar(select(Category).where(Category.slug == "deep")) is None


def test_collections_pick_products_with_photos_and_dates(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "sales")
    with staff_app.app_context():
        ids = {p.slug: p.id for p in db.session.scalars(select(Product))}
    search = staff.get(PREFIX + "/groups/collections/new?q=ankara").get_data(as_text=True)
    assert "Ankara" in search and "/email-images/" in search  # photo while picking
    staff.post(
        PREFIX + "/groups/collections/new",
        data={
            "name": "Holiday Edit",
            "product_ids": [str(ids["ankara-blue"]), str(ids["cushion-g1"])],
            "starts_at": "2026-12-01T09:00",
            "ends_at": "2026-12-31T23:00",
        },
    )
    with staff_app.app_context():
        holiday = db.session.scalar(select(Collection).where(Collection.slug == "holiday-edit"))
        assert holiday is not None and holiday.starts_at is not None
        collection_id = holiday.id
        assert {
            p.slug for p in db.session.scalars(select(Product)) if holiday in p.collections
        } == {
            "ankara-blue",
            "cushion-g1",
        }
    staff.post(  # untick one; dates cleared
        PREFIX + f"/groups/collections/{collection_id}",
        data={"name": "Holiday Edit", "product_ids": [str(ids["cushion-g1"])]},
    )
    with staff_app.app_context():
        holiday = db.session.get(Collection, collection_id)
        assert holiday is not None and holiday.starts_at is None
        members = [p.slug for p in db.session.scalars(select(Product)) if holiday in p.collections]
        assert members == ["cushion-g1"]
    staff.post(PREFIX + f"/groups/collections/{collection_id}/delete")
    with staff_app.app_context():
        assert db.session.get(Collection, collection_id) is None
        assert db.session.get(Product, ids["cushion-g1"]) is not None


def test_new_products_start_hidden_with_one_option_per_choice(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "branding")
    ankara = _category(staff_app, "ankara")
    form = {
        "name": "Gold Kente Bundle",
        "product_type": "fabric",
        "sale_unit": "fixed_bundle",
        "price": "45.50",
        "stock": "4",
        "piece_length_yards": "6",
        "option_name": "Colour",
        "option_values": "Gold, Green, gold",
        "category_ids": [str(ankara.id)],
    }
    response = staff.post(PREFIX + "/products/new", data=form)
    assert response.status_code == 302 and "#photos" in response.location
    with staff_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "gold-kente-bundle"))
        assert product is not None and product.status == ProductStatus.DRAFT
        assert [v.label for v in product.variants] == ["Gold", "Green"]
        assert {v.price_cents for v in product.variants} == {4550}
        assert all(v.stock_quantity == 4 for v in product.variants)
        assert db.session.scalar(select(StudioChange)) is None  # hidden: nothing to review
    again = staff.post(PREFIX + "/products/new", data={**form, "option_values": ""})
    assert "List the choices" in again.get_data(as_text=True)
    plain = {**form, "option_name": "", "option_values": "", "price": "0"}
    assert "more than $0" in staff.post(PREFIX + "/products/new", data=plain).get_data(as_text=True)
    single = {**form, "option_name": "", "option_values": ""}
    staff.post(PREFIX + "/products/new", data=single)
    with staff_app.app_context():
        second = db.session.scalar(select(Product).where(Product.slug == "gold-kente-bundle-2"))
        assert second is not None and [v.sku for v in second.variants] == [f"GG-{second.id}"]


def test_staff_lists_show_product_photos(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "maintenance")
    for path in ("/products", "/prices", "/inventory"):
        page = staff.get(PREFIX + path).get_data(as_text=True)
        assert 'loading="lazy"' in page and "/email-images/products/" in page, path


def test_blog_previews_show_live_products_and_skip_hidden_ones(staff_app: Flask) -> None:
    with staff_app.app_context():
        blocks = blog.parse_body(
            "Look at these.\n[product: ankara-blue]\n[category: fabrics]\n"
            "[collection: new-arrivals]\n[product: draft-thing]"
        )
        assert [b.get("slug") for b in blocks if b["type"] == "embed"] == [
            "ankara-blue",
            "fabrics",
            "new-arrivals",
            "draft-thing",
        ]
        assert "[product: ankara-blue]" in blog.body_to_editor(blocks)
        with pytest.raises(blog.BlogError, match="no product"):
            blog.parse_body("[product: not-a-thing]")
    staff = _staff_client(staff_app, "branding")
    form = {
        "title": "Shop the look",
        "slug": "shop-the-look",
        "category": "Journal",
        "excerpt": "Our favourite prints.",
        "cover_path": "static:img/home/post-ankara.webp",
        "cover_alt": "Prints",
        "body": "Our picks.\n\n[product: ankara-blue]\n\n[category: fabrics]\n\n"
        "[product: draft-thing]",
    }
    assert staff.post(PREFIX + "/blog/new", data=form).status_code == 302
    with staff_app.app_context():
        post = db.session.scalar(select(BlogPost).where(BlogPost.slug == "shop-the-look"))
        assert post is not None
        post_id = post.id
    staff.post(PREFIX + f"/blog/{post_id}/publication", data={"action": "publish"})
    with staff_app.app_context():
        assert db.session.get(BlogPost, post_id).status == BlogStatus.PUBLISHED
    page = staff_app.test_client().get("/blog/shop-the-look/").get_data(as_text=True)
    assert page.count("gg-post-shop") >= 2 and "/product/ankara-blue/" in page
    assert "/product/draft-thing/" not in page  # hidden products never show
    assert "Shop all" in page and "/shop/fabrics/" in page


def test_blog_picker_finds_products_and_groups(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "sales")
    found = json.loads(staff.get(PREFIX + "/blog/shop-items?q=ank").data)["items"]
    kinds = {(item["kind"], item["slug"]) for item in found}
    assert ("category", "ankara") in kinds and ("product", "ankara-blue") in kinds
    product = next(item for item in found if item["kind"] == "product")
    assert product["image"].startswith("/email-images/")
    assert json.loads(staff.get(PREFIX + "/blog/shop-items?q=a").data)["items"] == []


def test_prices_stay_held_for_reviewed_staff(staff_app: Flask) -> None:
    staff = _staff_client(staff_app, "branding")
    with staff_app.app_context():
        variant = db.session.scalar(select(Variant).where(Variant.sku == "101001"))
        assert variant is not None
        variant_id, before = variant.id, variant.price_cents
    staff.post(PREFIX + f"/prices/{variant_id}", data={"price": "12.34", "reason": "Test"})
    with staff_app.app_context():
        assert db.session.get(Variant, variant_id).price_cents == before
        assert db.session.scalar(select(StudioChange)) is not None

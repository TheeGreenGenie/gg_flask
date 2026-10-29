from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.catalog import manifest as mf
from app.catalog import naming
from app.catalog.models import Product, ProductStatus, Variant, combination_key
from app.extensions import db
from tests.catalog_fixtures import make_image, manifest


def errors_for(data: dict, assets: Path) -> list[str]:
    return mf.validate(data, assets)


def test_valid_manifest_has_no_errors(assets: Path) -> None:
    assert errors_for(manifest(), assets) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["products"][0].update(slug="Bad Slug"), "slug must be lowercase"),
        (lambda d: d["products"][1].update(slug="ankara-blue"), "duplicate product slug"),
        (lambda d: d["products"][1]["variants"][0].update(sku="101001"), "duplicate sku"),
        (
            lambda d: d["products"][0]["variants"][0].update(price_cents=8.99),
            "whole number of cents",
        ),
        (lambda d: d["products"][0]["variants"][0].update(stock=-1), "stock"),
        (lambda d: d["products"][0].update(categories=["nope"]), "unknown category"),
        (lambda d: d["products"][0].update(collections=["nope"]), "unknown collection"),
        (lambda d: d["products"][0].update(colour="blue"), "unknown field"),
        (lambda d: d["products"][0].update(colors=["Sky"]), "colors"),
        (
            lambda d: d["products"][0]["images"][0].update(file="../../etc/passwd.png"),
            "unsafe path",
        ),
        (lambda d: d["products"][0]["images"][0].update(file="products/missing.png"), "not found"),
        (lambda d: d["products"][0]["images"][0].update(alt=""), "alt text"),
        (lambda d: d["products"][0].update(images=[]), "at least one image"),
        (
            lambda d: d["products"][1]["variants"][1].update(options={"Style": "Style 1"}),
            "duplicate option combination",
        ),
        (lambda d: d["products"][1]["variants"][0].update(options={}), "exactly one value"),
        (
            lambda d: d["products"][1]["variants"][0].update(compare_at_cents=700),
            "compare_at_cents must be above",
        ),
        (
            lambda d: d["products"][0]["variants"].append({"sku": "X2", "price_cents": 1}),
            "exactly one variant",
        ),
        (
            lambda d: d["categories"][0]["children"][0].update(
                children=[
                    {
                        "slug": "third",
                        "name": "Third level",
                        "children": [{"slug": "deep", "name": "Too deep"}],
                    }
                ]
            ),
            "3 levels deep",
        ),
    ],
)
def test_validation_rejects(assets: Path, mutate, expected: str) -> None:  # type: ignore[no-untyped-def]
    data = manifest()
    mutate(data)
    errors = errors_for(data, assets)
    assert any(expected in e for e in errors), errors


def test_load_is_idempotent_and_archives_missing(catalog_app: Flask, assets: Path) -> None:
    media = Path(catalog_app.config["MEDIA_ROOT"])
    with catalog_app.app_context():
        assert db.session.scalar(select(db.func.count(Variant.id))) == 5
        report = mf.load(manifest(), assets, media)  # second load
        assert report.products == 3 and not report.archived
        assert db.session.scalar(select(db.func.count(Variant.id))) == 5

        data = manifest()
        data["products"] = data["products"][:1]
        report = mf.load(data, assets, media)
        assert sorted(report.archived) == ["cushion-g1", "draft-thing"]
        cushion = db.session.scalar(select(Product).where(Product.slug == "cushion-g1"))
        assert cushion.status == ProductStatus.ARCHIVED
        assert (media / "products/ankara-blue/01.png").is_file()


def test_removed_variant_is_deactivated_not_deleted(catalog_app: Flask, assets: Path) -> None:
    media = Path(catalog_app.config["MEDIA_ROOT"])
    with catalog_app.app_context():
        data = manifest()
        data["products"][1]["variants"].pop()  # drop G1-3
        data["products"][1]["options"][0]["values"].pop()
        mf.load(data, assets, media)
        v = db.session.scalar(select(Variant).where(Variant.sku == "G1-3"))
        assert v is not None and v.active is False


def test_invalid_manifest_changes_nothing(catalog_app: Flask, assets: Path) -> None:
    with catalog_app.app_context():
        data = manifest()
        data["products"][0]["name"] = "Renamed"
        data["products"][1]["variants"][0]["sku"] = "101001"  # duplicate -> invalid
        with pytest.raises(mf.ManifestError):
            mf.load(data, assets, Path(catalog_app.config["MEDIA_ROOT"]))
        p = db.session.scalar(select(Product).where(Product.slug == "ankara-blue"))
        assert p.name == "African Ankara Print - 101001"


def test_database_rejects_duplicate_combination(catalog_app: Flask) -> None:
    with catalog_app.app_context():
        product = db.session.scalar(select(Product).where(Product.slug == "cushion-g1"))
        db.session.add(
            Variant(
                product=product,
                sku="DUP",
                combination_key=combination_key({"Style": "Style 1"}),
                price_cents=1,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_combination_key_is_order_and_case_insensitive() -> None:
    assert combination_key({"Size": "M", "Color": "Blue"}) == combination_key(
        {"color": "blue ", "size": "m"}
    )


# ---------- Naming ----------


@pytest.mark.parametrize(
    ("name", "family", "code"),
    [
        ("African Ankara Print - 101025", "African Ankara Print", "101025"),
        ("African Ankara Print - Purple & Blue - 101025", "African Ankara Print", "101025"),
        ("Cushion Cover G-3", "Cushion Cover", "G-3"),
        ("Wrap Skirt WR 2002 - 101196", "Wrap Skirt WR 2002", "101196"),
        ("Reversible Jacket TMB-1 -1016", "Reversible Jacket TMB-1", "1016"),
    ],
)
def test_split_name_keeps_family_as_on_site(name: str, family: str, code: str) -> None:
    parts = naming.split_name(name)
    assert (parts.family, parts.code) == (family, code)


def test_compose_name() -> None:
    assert (
        naming.compose("African Ankara Print", ["Blue", "Gold", "Red"], "101025")
        == "African Ankara Print - Blue & Gold - 101025"
    )


def test_colors_measured_on_fabric_not_backdrop(tmp_path: Path) -> None:
    photo = tmp_path / "on-white.png"
    make_image(photo, (200, 30, 35), backdrop=True)  # red square on a white backdrop
    analysis = naming.analyze(photo)
    assert analysis.names() == ["Red"]
    assert analysis.shares[0] == ("Red", 1.0)  # percentages are of fabric pixels only
    assert 0.7 < analysis.background_fraction < 0.8  # 75% of the photo was backdrop


def test_full_frame_fabric_keeps_every_pixel(tmp_path: Path) -> None:
    photo = tmp_path / "black.png"
    make_image(photo, (15, 15, 15))  # dark fabric filling the frame is not a backdrop
    analysis = naming.analyze(photo)
    assert analysis.background_fraction == 0
    assert analysis.names() == ["Black"]


def test_two_color_print_shares(tmp_path: Path) -> None:
    from PIL import Image

    photo = tmp_path / "split.png"
    im = Image.new("RGB", (100, 100), (30, 90, 200))
    im.paste(Image.new("RGB", (30, 100), (250, 210, 40)), (0, 0))
    im.save(photo)
    analysis = naming.analyze(photo)
    shares = dict(analysis.shares)
    assert shares["Blue"] == pytest.approx(0.7, abs=0.02)
    assert shares["Yellow"] == pytest.approx(0.3, abs=0.02)
    assert analysis.names() == ["Blue", "Yellow"]

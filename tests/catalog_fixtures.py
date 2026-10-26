"""A tiny but complete manifest (with generated images) for catalog, store and cart tests."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image


def make_image(path: Path, color: tuple[int, int, int], backdrop: bool = False) -> None:
    """Solid color photo; with `backdrop`, a colored square on a white studio background."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backdrop:
        im = Image.new("RGB", (100, 100), (250, 250, 250))
        im.paste(Image.new("RGB", (50, 50), color), (25, 25))
    else:
        im = Image.new("RGB", (100, 100), color)
    im.save(path)


BASE: dict[str, Any] = {
    "schema_version": 1,
    "categories": [
        {
            "slug": "fabrics",
            "name": "Fabrics",
            "children": [{"slug": "ankara", "name": "Ankara Prints"}],
        },
        {"slug": "home-accessories", "name": "Home & Accessories"},
    ],
    "collections": [{"slug": "bhm-collection", "name": "BHM Collection"}],
    "products": [
        {
            "slug": "ankara-blue",
            "name": "African Ankara Print - 101001",
            "type": "fabric",
            "status": "active",
            "published": date(2026, 10, 1),
            "summary": "Blue wax print.",
            "categories": ["ankara"],
            "collections": ["bhm-collection"],
            "images": [{"file": "products/ankara-blue/01.png", "alt": "Blue ankara"}],
            "variants": [{"sku": "101001", "price_cents": 899, "stock": 5}],
        },
        {
            "slug": "cushion-g1",
            "name": "Cushion Cover G-1",
            "type": "home_accessories",
            "status": "active",
            "published": date(2024, 1, 1),
            "categories": ["home-accessories"],
            "images": [
                {"file": "products/cushion-g1/01.png", "alt": "Cushion"},
                {"file": "products/cushion-g1/02.png", "alt": "Cushion style 2"},
            ],
            "options": [{"name": "Style", "values": ["Style 1", "Style 2", "Style 3"]}],
            "variants": [
                {
                    "sku": "G1-1",
                    "options": {"Style": "Style 1"},
                    "price_cents": 750,
                    "compare_at_cents": 950,
                    "stock": 2,
                },
                {
                    "sku": "G1-2",
                    "options": {"Style": "Style 2"},
                    "price_cents": 800,
                    "stock": 0,
                    "image": "products/cushion-g1/02.png",
                },
                {"sku": "G1-3", "options": {"Style": "Style 3"}, "price_cents": 750, "stock": 1},
            ],
        },  # fmt: skip
        {
            "slug": "draft-thing",
            "name": "Unreleased Print",
            "type": "fabric",
            "status": "draft",
            "categories": ["ankara"],
            "variants": [{"sku": "DRAFT-1", "price_cents": 100, "stock": 9}],
        },
    ],
}


def manifest() -> dict[str, Any]:
    return copy.deepcopy(BASE)


def write_assets(assets: Path) -> None:
    make_image(assets / "products/ankara-blue/01.png", (30, 90, 200))
    make_image(assets / "products/cushion-g1/01.png", (200, 30, 35), backdrop=True)
    make_image(assets / "products/cushion-g1/02.png", (40, 150, 60), backdrop=True)

"""Add products that exist only on the live site to catalog/manifest.yaml (dev only).

Inputs: instance/live_products.json from fetch_live_products.py (name, SKU, price, photo file
name as published on the live product page) and the owner's photo zips. Each live photo name
("WhatsApp-Image-2026-05-03-at-9.55.59-PM-1.jpeg") is matched to its zip entry
("WhatsApp Image 2026-05-03 at 9.55.59 PM (1).jpeg"), converted to WebP like the other catalog
photos, and the product is written to the manifest. Re-running replaces the same products, so
it can follow every `export_manifest_draft.py` run:

    python scripts/export_manifest_draft.py <uploads>
    python scripts/import_live_products.py ZIP [ZIP ...]
    flask --app wsgi catalog autoname --write && flask --app wsgi catalog load

The live pages publish no description or piece length for these products; both are left for
the owner (admin or Studio product page).
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
MAX_WIDTH = 1600
INITIAL_STOCK = 1  # owner, 2026-10-27
# Old WordPress category -> (new collection slug, name). The products go in Ankara Prints too.
COLLECTIONS = {
    "grand-super-wax": ("grand-super-wax", "Grand Super Wax"),
    "super-wax-fabric": ("super-wax", "Super Wax"),
    "regular-wax-fabric": ("regular-wax", "Regular Wax"),
}
CATEGORY = "ankara"


def live_key(name: str) -> str:
    """Normalise both naming styles to one key: lower case, spaces/brackets -> dashes."""
    stem = PurePosixPath(name).name.lower()
    stem = re.sub(r"\s*\((\d+)\)", r"-\1", stem)
    return re.sub(r"[\s_]+", "-", stem)


def zip_photos(paths: list[Path]) -> dict[str, tuple[Path, str]]:
    photos: dict[str, tuple[Path, str]] = {}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.namelist():
                if entry.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    photos[live_key(entry)] = (path, entry)
    return photos


def convert(archive: Path, entry: str, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        data = zf.read(entry)
    with Image.open(io.BytesIO(data)) as im:  # Pillow's decompression-bomb guard applies
        im = im.convert("RGB")
        if im.width > MAX_WIDTH:
            im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "WEBP", quality=82, method=6)


def published_dates() -> dict[str, date]:
    with (ROOT / "live_url_inventory.csv").open(encoding="utf-8") as fh:
        return {
            r["path"]: date.fromisoformat(r["last_modified"][:10])
            for r in csv.DictReader(fh)
            if r.get("last_modified")
        }


def main() -> int:
    zips = [Path(p) for p in sys.argv[1:]]
    if not zips:
        print(__doc__)
        return 1
    live = json.loads((ROOT / "instance" / "live_products.json").read_text(encoding="utf-8"))
    photos = zip_photos(zips)
    dates = published_dates()
    manifest_path = CATALOG / "manifest.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    data = yaml.safe_load(text)

    new: list[dict[str, Any]] = []
    problems: list[str] = []
    for item in live:
        if item.get("error"):
            problems.append(f"{item['path']}: {item['error']}")
            continue
        slug = item["path"].strip("/").rsplit("/", 1)[-1]
        found = [photos.get(live_key(name)) for name in item["images"]]
        if not found or None in found:
            problems.append(f"{slug}: photo {item['images']} not in the zips")
            continue
        images = []
        for n, (archive, entry) in enumerate(found, 1):  # type: ignore[misc]
            rel = f"products/{slug}/{n:02d}.webp"
            convert(archive, entry, CATALOG / "assets" / rel)
            images.append({"file": rel, "alt": item["name"]})
        cat = next((c for c in item["categories"] if c in COLLECTIONS), None)
        new.append(
            {
                "slug": slug,
                "name": item["name"],
                "type": "fabric",
                "status": "active" if item.get("availability") == "InStock" else "draft",
                "featured": False,
                "published": dates.get(item["path"], date.today()),
                "summary": "",
                "description": "",
                "categories": [CATEGORY],
                "collections": [COLLECTIONS[cat][0]] if cat else [],
                "images": images,
                "options": [],
                "variants": [
                    {
                        "sku": item["sku"] or f"LIVE-{slug}",
                        "price_cents": round(float(item["price"]) * 100),
                        "stock": INITIAL_STOCK,
                    }
                ],
            }
        )

    if problems:
        print("Nothing was changed:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    slugs = {p["slug"] for p in new}
    data["products"] = [p for p in data["products"] if p["slug"] not in slugs] + new
    have = {c["slug"] for c in data.get("collections", [])}
    for slug, name in COLLECTIONS.values():
        if slug not in have:
            data.setdefault("collections", []).append(
                {"slug": slug, "name": name, "description": ""}
            )
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    manifest_path.write_text(header + body, encoding="utf-8")
    print(f"Added/updated {len(new)} live-only products in {manifest_path.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read product facts from the live WordPress product pages (dev only, read-only).

For products that exist on the live site but not in the local WordPress copy, fetch each public
product page once (politely, one request per second) and keep what the page itself publishes:
name, SKU, price, availability, category, photo file names, short and long description.

    python scripts/fetch_live_products.py            # products missing from catalog/manifest.yaml
    -> instance/live_products.json

Uses the same plain HTTP client as crawl_sitemap.py. If the site ever answers with a bot
challenge, the script stops instead of trying to get around it.
"""

from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://ggafrikaneleganz.com"
USER_AGENT = "GG-Rebuild-Inventory/1.0"
OUT = ROOT / "instance" / "live_products.json"


def fetch(path: str) -> str:
    request = urllib.request.Request(SITE + path, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        page: str = response.read().decode("utf-8", "replace")
    if any(k in page.lower() for k in ("cf-chl", "captcha", "just a moment")):
        raise SystemExit(f"Bot challenge at {path}; stopping without retrying.")
    return page


def text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>|</p>\s*<p[^>]*>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse(path: str, page: str) -> dict[str, Any]:
    product: dict[str, Any] = {}
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else data
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Product":
                product = item
    offers = product.get("offers") or [{}]
    offer = offers[0] if isinstance(offers, list) else offers
    short = re.search(
        r'woocommerce-product-details__short-description">(.*?)</div>', page, re.S
    )
    long = re.search(r'id="tab-description"[^>]*>(.*?)</div>', page, re.S)
    gallery = re.findall(r'data-large_image="([^"]+)"', page) or [product.get("image", "")]
    return {
        "path": path,
        "name": html.unescape(product.get("name", "")).strip(),
        "sku": product.get("sku", ""),
        "price": offer.get("price"),
        "availability": str(offer.get("availability", "")).rsplit("/", 1)[-1],
        "categories": sorted(set(re.findall(r"product_cat-([a-z0-9-]+)", page))),
        "images": [u.rsplit("/", 1)[-1] for u in dict.fromkeys(gallery) if u],
        "short_description": text(short.group(1)) if short else "",
        "description": text(long.group(1)) if long else text(product.get("description", "")),
    }


def missing_paths() -> list[str]:
    data = yaml.safe_load((ROOT / "catalog" / "manifest.yaml").read_text(encoding="utf-8"))
    have = {f"/product/{p['slug']}/" for p in data["products"]}
    with (ROOT / "live_url_inventory.csv").open(encoding="utf-8") as fh:
        live = [r["path"] for r in csv.DictReader(fh) if r["type"] == "posts-product-1"]
    return [p for p in live if p not in have]


def main() -> int:
    paths = sys.argv[1:] or missing_paths()
    results = []
    for n, path in enumerate(paths, 1):
        try:
            results.append(parse(path, fetch(path)))
        except urllib.error.HTTPError as exc:
            results.append({"path": path, "error": f"HTTP {exc.code}"})
        print(f"{n}/{len(paths)} {path} {results[-1].get('price') or results[-1].get('error')}")
        time.sleep(1)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

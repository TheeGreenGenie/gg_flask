"""Owner review sheet for the launch catalog (dev only).

Reads catalog/manifest.yaml and writes instance/catalog_review.html and .csv: every product with
its photo, price, stock and the things that need an owner decision, so the catalog review is a
scan of flagged rows instead of reading 230 YAML entries.

    python scripts/catalog_review.py [--wp-db new_wordpress_local]

With --wp-db, each product also shows how the old WordPress store sold it (By The Yard / 3 Yards /
6 Yards), which explains most prices. Read-only on both sources.
"""

from __future__ import annotations

import argparse
import collections
import csv
import html
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
OUT = ROOT / "instance"
CUT_TERMS = {"by-the-yard": "By The Yard", "3-yards": "3 Yards", "6-yards": "6 Yards"}
PER_YARD_TEXT = re.compile(r"sold by (the )?yard", re.I)
PLACEHOLDER_STOCK = 10  # export_manifest_draft.py sets this for items WordPress did not track


def old_cuts(db: str) -> dict[int, str]:
    import pymysql  # tools extra

    conn = pymysql.connect(host="localhost", user="root", database=db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.object_id, t.slug FROM wp_term_relationships r
                JOIN wp_term_taxonomy tt ON tt.term_taxonomy_id = r.term_taxonomy_id
                 AND tt.taxonomy = 'product_cat'
                JOIN wp_terms t ON t.term_id = tt.term_id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    cuts: dict[int, str] = {}
    for object_id, slug in rows:
        if slug in CUT_TERMS:
            cuts[int(object_id)] = CUT_TERMS[slug]
    return cuts


def flags_for(p: dict[str, Any], dup_names: set[str], cut: str) -> list[str]:
    text = f"{p.get('summary', '')}\n{p.get('description', '')}"
    variants = p.get("variants") or []
    out: list[str] = []
    unit = p.get("sale_unit", "fixed_bundle")
    if unit == "fixed_bundle" and (cut == "By The Yard" or PER_YARD_TEXT.search(text)):
        why = "old store sold it per yard" if cut == "By The Yard" else "text says sold by the yard"
        out.append(f"PRICE UNIT: {why}, but it is now sold as a bundle at that price")
    if any(v.get("stock") == PLACEHOLDER_STOCK for v in variants):
        out.append("STOCK: placeholder 10 (old store did not track stock)")
    if any(str(v.get("sku", "")).startswith("GG-") for v in variants):
        out.append("SKU: placeholder code (old store reused this code)")
    if not any(v.get("weight_grams") for v in variants):
        out.append("WEIGHT: missing (shipping estimates 450 g)")
    if p["name"] in dup_names:
        out.append("NAME: same as another product")
    if len(p.get("images") or []) < 2:
        out.append("PHOTOS: only one photo")
    if not (p.get("description") or "").strip():
        out.append("COPY: no description")
    if any((v.get("price_cents") or 0) <= 0 for v in variants):
        out.append("PRICE: zero")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wp-db", help="Old WordPress database name (read-only), for cut categories")
    args = ap.parse_args()

    data = yaml.safe_load((CATALOG / "manifest.yaml").read_text(encoding="utf-8"))
    products: list[dict[str, Any]] = data["products"]
    cuts = old_cuts(args.wp_db) if args.wp_db else {}
    names = collections.Counter(p["name"] for p in products)
    dup_names = {n for n, c in names.items() if c > 1}

    rows = []
    for p in products:
        cut = cuts.get(int(p.get("legacy_wp_id") or 0), "")
        variants = p.get("variants") or []
        prices = sorted({v.get("price_cents", 0) for v in variants})
        rows.append(
            {
                "slug": p["slug"],
                "name": p["name"],
                "category": ", ".join(p.get("categories") or []),
                "old_store_sold_as": cut,
                "sale_unit": p.get("sale_unit", "fixed_bundle"),
                "unit_label": p.get("sale_unit", "bundle").replace("fixed_bundle", "bundle"),
                "price": " / ".join(f"${c / 100:.2f}" for c in prices),
                "stock": " / ".join(str(v.get("stock")) for v in variants),
                "skus": " / ".join(str(v.get("sku")) for v in variants),
                "photo": (p.get("images") or [{}])[0].get("file", ""),
                "flags": flags_for(p, dup_names, cut),
            }
        )

    OUT.mkdir(exist_ok=True)
    with (OUT / "catalog_review.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        cols = ["slug", "name", "category", "old_store_sold_as"]
        cols += ["sale_unit", "price", "stock", "skus"]
        writer.writerow([*cols, "flags"])
        for r in rows:
            # Spreadsheet-safe: never start a cell with a formula character.
            cells = [str(r[c]) for c in cols] + ["; ".join(r["flags"])]
            writer.writerow(["'" + c if c and c[0] in "=+-@" else c for c in cells])

    totals = collections.Counter(k for r in rows for k in {f.split(":")[0] for f in r["flags"]})
    summary = "".join(
        f"<li><b>{html.escape(k)}</b>: {v} products</li>" for k, v in totals.most_common()
    )
    body = []
    for r in sorted(rows, key=lambda r: (-len(r["flags"]), r["category"], r["name"])):
        img = f"../catalog/assets/{r['photo']}" if r["photo"] else ""
        flags = "".join(f"<li>{html.escape(f)}</li>" for f in r["flags"]) or "<li class=ok>OK</li>"
        body.append(
            "<tr>"
            f"<td><img src='{html.escape(img)}' alt='' loading='lazy'></td>"
            f"<td><b>{html.escape(r['name'])}</b><br><small>{html.escape(r['slug'])}"
            f" · {html.escape(r['skus'])}</small></td>"
            f"<td>{html.escape(r['category'])}</td>"
            f"<td>{html.escape(r['old_store_sold_as'] or '—')}</td>"
            f"<td>{html.escape(r['price'])}<br>"
            f"<small>per {html.escape(r['unit_label'])}</small></td>"
            f"<td>{html.escape(r['stock'])}</td>"
            f"<td><ul>{flags}</ul></td>"
            "</tr>"
        )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalog review</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;margin:1rem;color:#1a1814;background:#faf7f1}}
table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid #e3dccf;padding:.4rem;vertical-align:top;text-align:left}}
th{{position:sticky;top:0;background:#f1e9da}}
img{{width:72px;height:72px;object-fit:cover;border-radius:6px}}
ul{{margin:0;padding-left:1rem}} li{{color:#8a2d0b}} li.ok{{color:#2c6b2f;list-style:none}}
small{{color:#6b645a}}
</style></head><body>
<h1>Catalog review — {len(rows)} products</h1>
<p>Most-flagged first. Edit <code>catalog/manifest.yaml</code> (or the Staff Studio once
it is live), then run <code>flask --app wsgi catalog load</code>.</p>
<ul>{summary}</ul>
<table><thead><tr><th>Photo</th><th>Product</th><th>Category</th><th>Old store sold as</th>
<th>Price</th><th>Stock</th><th>Needs a decision</th></tr></thead>
<tbody>{"".join(body)}</tbody></table></body></html>"""
    (OUT / "catalog_review.html").write_text(page, encoding="utf-8")
    print(f"wrote {OUT / 'catalog_review.html'} and .csv ({len(rows)} products)")
    for k, v in totals.most_common():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

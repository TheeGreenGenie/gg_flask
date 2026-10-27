"""Draft catalog/manifest.yaml from the old WordPress database (read-only).

This is a starting point for curation, not an import: it picks a representative set of
published products, maps them onto the new category plan, converts photos to WebP and
writes YAML for the owner to review and edit. Old users, orders, tags and the old
category tree are never read.

Usage:
  python scripts/export_manifest_draft.py UPLOADS_DIR [--per-category N] [--db NAME]

Dev-only tool (needs `pip install -e ".[tools]"`). Connects to MySQL on localhost as root.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pymysql
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "catalog"

# New category plan (worksheet D-002 recommendation, working default).
# Old WordPress category slug -> (new parent slug, new child slug)
CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "ankara-prints": ("fabrics", "ankara"),
    "kente-prints": ("fabrics", "kente"),
    "adire": ("fabrics", "adire"),
    "satin-chiffon": ("fabrics", "satin-chiffon"),
    "ethnic-prints": ("fabrics", "ethnic-prints"),
    "skirts": ("clothing", "skirts"),
    "reversible-jacket": ("clothing", "jackets"),
    "cushion-cover": ("home-accessories", "cushion-covers"),
    "sarong-wrap": ("home-accessories", "sarong-wraps"),
    # Old ankara-only groupings with no category of their own; also kept as collections.
    "ankara-plus": ("fabrics", "ankara"),
    "final-cut": ("fabrics", "ankara"),
}
# Product code at the end of an old title: "… - 101219", "… - 101068-OG", "… - G-6".
TITLE_CODE = re.compile(r"(?:^|\s)([A-Z]{0,3}-?\d{1,6}(?:-[A-Z0-9]{1,4})?)\s*$")
# Checked in this order so the most specific old category wins (Adire is under Ethnic).
CATEGORY_PRIORITY = list(CATEGORY_MAP)

CATEGORIES = [
    {
        "slug": "fabrics",
        "name": "Fabrics",
        "image_src": "2024/09/Ankara-By-The-Yard-Category.jpg",
        "children": [
            ("ankara", "Ankara Prints", "2024/09/Ankara-Category.jpg"),
            ("kente", "Kente Prints", "2024/09/Kente-Print-Category.jpg"),
            ("adire", "Adire (Tie-Dye)", "2025/01/Adire-Category.jpg"),
            ("satin-chiffon", "Satin & Chiffon", "2024/09/Satin-category.jpg"),
            ("ethnic-prints", "Ethnic Prints", "2024/09/Ethnic-Category.jpg"),
        ],
    },
    {
        "slug": "clothing",
        "name": "Clothing",
        "image_src": "2024/10/Reversible-Jackets.jpg",
        "children": [
            ("skirts", "Skirts", "2024/10/Wrap-Skirt-WR-2002-101196.jpg"),
            ("jackets", "Jackets", "2024/10/Reversible-Jackets.jpg"),
        ],
    },
    {
        "slug": "home-accessories",
        "name": "Home & Accessories",
        "image_src": "2023/01/Cushion-Covers-Group-3.jpg",
        "children": [
            ("cushion-covers", "Cushion Covers", "2023/01/Cushion-Covers-Group-3.jpg"),
            ("sarong-wraps", "Sarong Wraps", "2024/10/Sarong-Wrap.jpg"),
        ],
    },
]

# Old categories that become hand-picked collections (D-004 recommendation).
COLLECTION_MAP = {
    "bhm-collection": ("bhm-collection", "BHM Collection", "Celebrating Black History Month."),
    "final-cut": ("final-cut", "Final Cut", "Last pieces of favourite prints."),
    "ankara-plus": ("ankara-plus", "Ankara Plus", ""),
    "classic-designs": ("classic-designs", "Classic Designs", ""),
    "floral-designs": ("floral-designs", "Floral Designs", ""),
}

PRODUCT_TYPE = {"fabrics": "fabric", "clothing": "clothing", "home-accessories": "home_accessories"}
AXIS_NAMES = {"color": "Colorway", "pa_color": "Colorway", "style": "Style", "pa_size": "Size"}


INITIAL_STOCK = 1
# Old cut-length categories -> length of one piece in yards (owner, 2026-10-27: "By The Yard"
# items are precut 1-yard pieces at that price).
CUT_LENGTH = {"by-the-yard": 1, "3-yards": 3, "6-yards": 6}
PER_YARD_TEXT = re.compile(r"sold by (?:the )?yard\.?", re.I)
LENGTH_TEXT = re.compile(
    r"(?:comes in|measures|totaling)\s+(\d+(?:\.\d+)?)(?:\s+(\d)/(\d))?\s+yards?", re.I
)
SIZE_TEXT = re.compile(r'(\d{2,3})\s*(?:"|”|in)\s*(?:by|x)\s*(\d{2,3})\s*(?:"|”|in)', re.I)


def piece_size(terms: set[str], text: str) -> tuple[float | None, int | None]:
    """(length in yards, width in inches) from the old category or the description."""
    length: float | None = next((CUT_LENGTH[t] for t in CUT_LENGTH if t in terms), None)
    if length is None and (m := LENGTH_TEXT.search(text)):
        length = float(m[1]) + (int(m[2]) / int(m[3]) if m[2] else 0)
    if length is None and PER_YARD_TEXT.search(text):
        length = 1  # priced per yard on the old site = a precut 1-yard piece
    width = None
    if m := SIZE_TEXT.search(text):
        a, b = int(m[1]), int(m[2])
        along = round((length or 0) * 36)
        candidates = [n for n in (a, b) if n != along and 30 <= n <= 80]
        width = candidates[0] if candidates else None
    return (round(length, 2) if length else None), width


def q(cur: Any, sql: str, args: Any = None) -> list[dict[str, Any]]:
    cur.execute(sql, args)
    return list(cur.fetchall())


def plain_text(raw: str) -> str:
    """Strip HTML and WordPress shortcodes; keep paragraph breaks."""
    text = re.sub(r"\[/?[a-zA-Z_][^\]]*\]", "", raw or "")
    text = re.sub(r"<\s*(br|/p|/h\d|/li)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def slugify(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def convert(uploads: Path, rel: str, dest: Path, max_width: int = 1600) -> bool:
    src = uploads / rel
    if not src.is_file():
        print(f"  ! missing image {rel}", file=sys.stderr)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
        im.save(dest, "WEBP", quality=82, method=6)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("uploads", type=Path)
    ap.add_argument("--per-category", type=int, default=100_000, help="Cap per category.")
    ap.add_argument("--db", default="new_wordpress_local")
    args = ap.parse_args()

    conn = pymysql.connect(
        host="localhost", user="root", database=args.db, cursorclass=pymysql.cursors.DictCursor
    )
    cur = conn.cursor()
    assets = ROOT / "assets"

    # Published products with a featured image, their categories and visibility terms.
    rows = q(
        cur,
        """
        SELECT p.ID, p.post_title, p.post_name, p.post_content, p.post_excerpt, p.post_date
        FROM wp_posts p
        JOIN wp_postmeta t ON t.post_id = p.ID AND t.meta_key = '_thumbnail_id'
        WHERE p.post_type = 'product' AND p.post_status = 'publish'
        ORDER BY p.post_date DESC
        """,
    )
    terms = defaultdict(set)
    for r in q(
        cur,
        """
        SELECT tr.object_id, t.slug, tt.taxonomy FROM wp_term_relationships tr
        JOIN wp_term_taxonomy tt USING (term_taxonomy_id) JOIN wp_terms t USING (term_id)
        WHERE tt.taxonomy IN ('product_cat', 'product_visibility')
        """,
    ):
        terms[r["object_id"]].add(r["slug"])

    def meta(post_id: int) -> dict[str, str]:
        return {
            m["meta_key"]: m["meta_value"]
            for m in q(
                cur, "SELECT meta_key, meta_value FROM wp_postmeta WHERE post_id=%s", post_id
            )
        }

    def attachment(att_id: str | int | None) -> str | None:
        if not att_id:
            return None
        found = q(
            cur,
            "SELECT meta_value FROM wp_postmeta WHERE post_id=%s AND meta_key='_wp_attached_file'",
            int(att_id),
        )
        return found[0]["meta_value"] if found else None

    picked: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        old = terms[row["ID"]]
        child = next((CATEGORY_MAP[c] for c in CATEGORY_PRIORITY if c in old), None)
        if child is None or len(picked[child[1]]) >= args.per_category:
            continue
        # Prefer products with variants so the variant model is exercised.
        picked[child[1]].append({**row, "_cat": child, "_terms": old})

    products: list[dict[str, Any]] = []
    used_skus: set[str] = set()
    used_slugs: set[str] = set()

    for _, items in sorted(picked.items()):
        for row in items:
            m = meta(row["ID"])
            parent_slug, child_slug = row["_cat"]
            slug = slugify(row["post_name"]) or slugify(row["post_title"])
            slug = re.sub(r"-\d$", "", slug) if slug[:-2] in used_slugs else slug
            if slug in used_slugs:
                slug = f"{slug}-{row['ID']}"
            used_slugs.add(slug)

            # Images: featured + gallery, numbered in order.
            img_rels = [attachment(m.get("_thumbnail_id"))]
            img_rels += [attachment(i) for i in (m.get("_product_image_gallery") or "").split(",")]
            images, rel_to_file = [], {}
            for rel in [r for r in dict.fromkeys(img_rels) if r][:8]:
                name = f"products/{slug}/{len(images) + 1:02d}.webp"
                if convert(args.uploads, rel, assets / name):
                    images.append({"file": name, "alt": row["post_title"].strip()})
                    rel_to_file[rel] = name
            if not images:
                continue

            base_sku = (m.get("_sku") or "").strip()
            if not base_sku:  # most old products only carry their code in the title
                code = TITLE_CODE.search(row["post_title"])
                base_sku = code.group(1).lstrip("-") if code else ""
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}", base_sku)
                or base_sku in used_skus
            ):
                # The old store reused some codes across categories (owner, 2026-10-27): keep
                # the code and add the category's initial, e.g. Ethnic Print 101135 -> 101135-E.
                suffixed = f"{base_sku}-{row['_cat'][1][0].upper()}" if base_sku else ""
                base_sku = suffixed if suffixed and suffixed not in used_skus else f"GG-{row['ID']}"
            used_skus.add(base_sku)

            def stock_fields(vm: dict[str, str]) -> dict[str, Any]:
                # Owner, 2026-10-27: stock is tracked; every item starts at 1 and real counts
                # are entered later (hidden products count as unavailable).
                return {"stock": INITIAL_STOCK}

            def money(vm: dict[str, str]) -> dict[str, Any]:
                price = round(float(vm.get("_price") or vm.get("_regular_price") or 0) * 100)
                regular = vm.get("_regular_price")
                out: dict[str, Any] = {"price_cents": price}
                if regular and round(float(regular) * 100) > price:
                    out["compare_at_cents"] = round(float(regular) * 100)
                return out

            options, variants = [], []
            children = q(
                cur,
                "SELECT ID FROM wp_posts WHERE post_parent=%s AND post_type='product_variation' "
                "AND post_status='publish' ORDER BY menu_order, ID",
                row["ID"],
            )
            if children:
                axis_values: dict[str, list[str]] = {}
                for child in children:
                    vm = meta(child["ID"])
                    chosen = {}
                    for key, value in vm.items():
                        if key.startswith("attribute_") and value:
                            axis = AXIS_NAMES.get(key.removeprefix("attribute_"), "Option")
                            label = f"{axis} {value}" if value.isdigit() else value.title()
                            chosen[axis] = label
                            axis_values.setdefault(axis, [])
                            if label not in axis_values[axis]:
                                axis_values[axis].append(label)
                    if not chosen:
                        continue
                    sku = f"{base_sku}-{len(variants) + 1}"
                    variant = {"sku": sku, "options": chosen, **money(vm), **stock_fields(vm)}
                    # Match a colorway number to a gallery image named ...-N.jpg.
                    number = next(
                        (v.split()[-1] for v in chosen.values() if v.split()[-1].isdigit()), None
                    )
                    img = attachment(vm.get("_thumbnail_id"))
                    if img in rel_to_file:
                        variant["image"] = rel_to_file[img]
                    elif number:
                        match = next(
                            (
                                f
                                for r, f in rel_to_file.items()
                                if re.search(rf"-{number}\.\w+$", r)
                            ),
                            None,
                        )
                        if match:
                            variant["image"] = match
                    variants.append(variant)
                options = [{"name": k, "values": v} for k, v in axis_values.items()]
            if not variants:
                options = []
                variants = [{"sku": base_sku, **money(m), **stock_fields(m)}]

            product_type = PRODUCT_TYPE[parent_slug]
            description = plain_text(row["post_content"]) or plain_text(row["post_excerpt"])
            length, width = (
                piece_size(row["_terms"], description) if product_type == "fabric" else (None, None)
            )
            if length == 1:
                description = PER_YARD_TEXT.sub("Precut 1-yard piece.", description)
            summary = (
                plain_text(row["post_excerpt"]).split("\n")[0][:300]
                or description.split("\n")[0][:300]
            )
            products.append(
                {
                    "slug": slug,
                    "name": row["post_title"].strip(),
                    "type": product_type,
                    "status": "active",
                    "featured": "featured" in row["_terms"],
                    "published": row["post_date"].date(),
                    "legacy_wp_id": row["ID"],
                    "summary": summary,
                    "description": description,
                    "categories": [child_slug],
                    "collections": [
                        COLLECTION_MAP[c][0] for c in COLLECTION_MAP if c in row["_terms"]
                    ],
                    "images": images,
                    "options": options,
                    "variants": variants,
                    **({"piece_length_yards": length} if length else {}),
                    **({"width_inches": width} if width else {}),
                }
            )
            print(f"  {slug}: {len(variants)} variant(s), {len(images)} image(s)")

    categories = []
    for cat in CATEGORIES:
        img = f"categories/{cat['slug']}.webp"
        node: dict[str, Any] = {"slug": cat["slug"], "name": cat["name"]}
        if convert(args.uploads, cat["image_src"], assets / img, 1200):
            node["image"] = img
        node["children"] = []
        for slug, name, src in cat["children"]:
            child: dict[str, Any] = {"slug": slug, "name": name}
            cimg = f"categories/{slug}.webp"
            if convert(args.uploads, src, assets / cimg, 1200):
                child["image"] = cimg
            node["children"].append(child)
        categories.append(node)

    used_cols = {c for p in products for c in p["collections"]}
    collections = [
        {"slug": s, "name": n, "description": d}
        for s, n, d in COLLECTION_MAP.values()
        if s in used_cols
    ]

    manifest = {
        "schema_version": 1,
        "categories": categories,
        "collections": collections,
        "products": products,
    }
    header = (
        "# GG Afrikan Eleganz launch manifest — DRAFT generated from the old WordPress catalog.\n"
        "# Review every product: names, copy, prices, stock (untracked items were set to 10),\n"
        "# alt text and categories. Then run `flask catalog validate` and `flask catalog load`.\n"
    )
    (ROOT / "manifest.yaml").write_text(
        header + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    print(f"wrote {ROOT / 'manifest.yaml'} with {len(products)} products")
    return 0


if __name__ == "__main__":
    sys.exit(main())

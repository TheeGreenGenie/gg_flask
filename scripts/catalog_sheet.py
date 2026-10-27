"""Edit stock, weights, prices and selling units in a spreadsheet instead of YAML (dev only).

    python scripts/catalog_sheet.py export            # writes instance/catalog_sheet.csv
    python scripts/catalog_sheet.py apply FILE.csv    # shows what would change
    python scripts/catalog_sheet.py apply FILE.csv --write
    flask --app wsgi catalog load                      # then load the catalog as usual

One row per product option (SKU). Editable columns: price, was_price (crossed-out price, blank
for none), stock, weight_grams, sale_unit (bundle / yard / metre — one value per product).
Everything else is for reference and is ignored on apply. Rows are matched by SKU; unknown SKUs
and invalid numbers stop the whole apply, so a half-edited sheet never lands.

Use this before launch, while catalog/manifest.yaml is the source of the catalog. Once staff
edit products in the Studio, make changes there instead.
"""

from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "catalog" / "manifest.yaml"
DEFAULT_OUT = ROOT / "instance" / "catalog_sheet.csv"
COLUMNS = [
    "sku", "product", "option", "category",
    "sale_unit", "price", "was_price", "stock", "weight_grams",
]  # fmt: skip
UNITS = {"bundle": "fixed_bundle", "fixed_bundle": "fixed_bundle", "yard": "yard", "metre": "metre"}


def _read_manifest() -> tuple[str, dict[str, Any]]:
    text = MANIFEST.read_text(encoding="utf-8")
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    return header, yaml.safe_load(text)


def _dollars(cents: int | None) -> str:
    return "" if cents is None else f"{Decimal(cents) / 100:.2f}"


def _cents(raw: str, where: str, errors: list[str], required: bool) -> int | None:
    raw = raw.strip().lstrip("'").replace("$", "").replace(",", "")
    if not raw:
        if required:
            errors.append(f"{where}: price is required")
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{where}: {raw!r} is not a price")
        return None
    if value < 0 or value != value.quantize(Decimal("0.01")):
        errors.append(f"{where}: {raw!r} must be a positive amount with at most 2 decimals")
        return None
    return int(value * 100)


def _whole(raw: str, where: str, errors: list[str]) -> int | None:
    raw = raw.strip().lstrip("'")
    if not raw:
        return None
    if not raw.isdigit():
        errors.append(f"{where}: {raw!r} must be a whole number (0 or more)")
        return None
    return int(raw)


def _safe(cell: str) -> str:
    # Spreadsheet-safe: a leading = + - @ would be treated as a formula by Excel.
    return "'" + cell if cell and cell[0] in "=+-@" else cell


def export(out: Path) -> int:
    _, data = _read_manifest()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    # utf-8-sig so Excel shows accents and "&" correctly when the file is double-clicked.
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for p in data["products"]:
            unit = p.get("sale_unit", "fixed_bundle").replace("fixed_bundle", "bundle")
            for v in p["variants"]:
                option = ", ".join(f"{k}: {val}" for k, val in (v.get("options") or {}).items())
                cells = [
                    str(v["sku"]),
                    p["name"],
                    option,
                    ", ".join(p.get("categories") or []),
                    unit,
                    _dollars(v.get("price_cents")),
                    _dollars(v.get("compare_at_cents")),
                    "" if v.get("stock") is None else str(v["stock"]),
                    "" if v.get("weight_grams") is None else str(v["weight_grams"]),
                ]
                writer.writerow([_safe(c) for c in cells])
                rows += 1
    print(f"wrote {out} ({rows} rows). Edit price, was_price, stock, weight_grams, sale_unit.")
    return 0


def apply(sheet: Path, write: bool) -> int:
    header, data = _read_manifest()
    by_sku: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
        str(v["sku"]): (p, v) for p in data["products"] for v in p["variants"]
    }
    errors: list[str] = []
    changes: list[str] = []
    units: dict[str, set[str]] = {}
    seen: set[str] = set()
    with sheet.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in ("sku", "price", "stock") if c not in (reader.fieldnames or [])]
        if missing:
            print(f"The sheet is missing column(s): {', '.join(missing)}", file=sys.stderr)
            return 1
        for n, row in enumerate(reader, start=2):
            sku = (row.get("sku") or "").strip().lstrip("'")
            where = f"row {n} ({sku or 'no SKU'})"
            if not sku:
                continue
            if sku not in by_sku:
                errors.append(f"{where}: unknown SKU")
                continue
            if sku in seen:
                errors.append(f"{where}: SKU appears twice")
                continue
            seen.add(sku)
            product, variant = by_sku[sku]
            new = {
                "price_cents": _cents(row.get("price", ""), where, errors, required=True),
                "compare_at_cents": _cents(row.get("was_price", ""), where, errors, False),
                "stock": _whole(row.get("stock", ""), where, errors),
                "weight_grams": _whole(row.get("weight_grams", ""), where, errors),
            }
            if new["stock"] is None:
                errors.append(f"{where}: stock is required")
            price, was = new["price_cents"], new["compare_at_cents"]
            if price is not None and was is not None and was <= price:
                errors.append(f"{where}: was_price must be higher than price (or left blank)")
            unit_raw = (row.get("sale_unit") or "bundle").strip().lower()
            if unit_raw not in UNITS:
                errors.append(f"{where}: sale_unit must be bundle, yard or metre")
            else:
                units.setdefault(product["slug"], set()).add(UNITS[unit_raw])
            for key, value in new.items():
                if variant.get(key) != value and not (value is None and key not in variant):
                    old = variant.get(key)
                    if key.endswith("_cents"):
                        old_s, new_s = _dollars(old) or "none", _dollars(value) or "none"
                    else:
                        old_s = "none" if old is None else str(old)
                        new_s = "none" if value is None else str(value)
                    changes.append(f"{sku}: {key.replace('_cents', '')} {old_s} -> {new_s}")
                    if value is None:
                        variant.pop(key, None)
                    else:
                        variant[key] = value
    for slug, found in units.items():
        if len(found) > 1:
            errors.append(f"{slug}: all options of one product need the same sale_unit")
            continue
        unit = found.pop()
        product = next(p for p in data["products"] if p["slug"] == slug)
        current = product.get("sale_unit", "fixed_bundle")
        if current != unit:
            changes.append(f"{slug}: sale_unit {current} -> {unit}")
            if unit == "fixed_bundle":
                product.pop("sale_unit", None)
            else:
                product["sale_unit"] = unit

    if errors:
        print(f"Nothing was changed: {len(errors)} problem(s):", file=sys.stderr)
        for e in errors[:50]:
            print(f"  - {e}", file=sys.stderr)
        return 1
    for c in changes[:200]:
        print(f"  {c}")
    if len(changes) > 200:
        print(f"  … and {len(changes) - 200} more")
    if not write:
        print(f"{len(changes)} change(s) would be made. Re-run with --write to save them.")
        return 0
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    MANIFEST.write_text(header + body, encoding="utf-8")
    print(f"Saved {len(changes)} change(s) to {MANIFEST.name}. Now run:")
    print("  flask --app wsgi catalog validate && flask --app wsgi catalog load")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write the sheet")
    ex.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap_apply = sub.add_parser("apply", help="read an edited sheet back into the manifest")
    ap_apply.add_argument("sheet", type=Path)
    ap_apply.add_argument("--write", action="store_true", help="save (default: preview only)")
    args = ap.parse_args()
    if args.cmd == "export":
        return export(args.out)
    return apply(args.sheet, args.write)


if __name__ == "__main__":
    raise SystemExit(main())

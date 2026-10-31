"""Catalog: models, launch-manifest loader, CLI commands and local media serving."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml
from flask import Blueprint, Flask, current_app, send_from_directory
from werkzeug.wrappers import Response

from app import cache
from app.catalog import manifest, naming
from app.catalog.models import utcnow

media_bp = Blueprint("media", __name__)
MEDIA_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
TEST_ITEM_SLUG = "test-item-not-for-sale"
TEST_ITEM_CENTS = 50  # Stripe's minimum charge


@media_bp.get("/media/<path:path>")
def media_file(path: str) -> Response:
    """Serve uploaded/seeded media locally. Production serves this path from object storage/CDN."""
    resp = send_from_directory(current_app.config["MEDIA_ROOT"], path, max_age=7 * 86400)
    # Media is inert: even an SVG opened directly may not run script or load anything.
    resp.headers["Content-Security-Policy"] = MEDIA_CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def init_catalog(app: Flask) -> None:
    app.config.setdefault("MEDIA_ROOT", str(Path(app.instance_path) / "media"))
    app.config.setdefault("CATALOG_DIR", str(Path(app.root_path).parent / "catalog"))
    if app.config.get("SERVE_MEDIA", True):
        app.register_blueprint(media_bp)

    @app.cli.group("catalog")
    def catalog_cli() -> None:
        """Launch-manifest commands."""

    def _paths() -> tuple[Path, Path, Path]:
        base = Path(current_app.config["CATALOG_DIR"])
        return base / "manifest.yaml", base / "assets", Path(current_app.config["MEDIA_ROOT"])

    @catalog_cli.command("validate")
    def validate_cmd() -> None:
        """Check catalog/manifest.yaml without changing the database."""
        path, assets, _ = _paths()
        data = manifest.read(path)
        errors = manifest.validate(data, assets)
        if errors:
            for e in errors:
                click.echo(f"  - {e}", err=True)
            raise SystemExit(f"{len(errors)} problem(s) in {path}")
        click.echo(f"OK: {path.name}: {len(data['products'])} products, no problems")

    @catalog_cli.command("load")
    def load_cmd() -> None:
        """Validate, then add/update the catalog from catalog/manifest.yaml."""
        path, assets, media = _paths()
        try:
            report = manifest.load(manifest.read(path), assets, media)
        except manifest.ManifestError as exc:
            for e in exc.errors:
                click.echo(f"  - {e}", err=True)
            raise SystemExit(f"{len(exc.errors)} problem(s); nothing was changed") from None
        cache.bump_catalog_version()
        click.echo(
            f"OK: loaded {report.products} products, {report.variants} variants, "
            f"{report.images} images, {report.categories} categories, "
            f"{report.collections} collections"
        )
        if report.archived:
            click.echo(f"  archived (not in manifest): {', '.join(report.archived)}")

    @catalog_cli.command("add-test-item")
    def add_test_item_cmd() -> None:
        """Create (or reset) the $0.50 launch test item: no photo, no description, ships free.

        Used to place one real order per payment provider before launch, then refunded. Hide it
        in the Studio afterwards, before removing PREVIEW_PASSWORD."""
        from app.catalog.models import Product, ProductStatus, ProductType, Variant
        from app.extensions import db

        product = db.session.scalar(db.select(Product).where(Product.slug == TEST_ITEM_SLUG))
        if product is None:
            product = Product(slug=TEST_ITEM_SLUG, product_type=ProductType.HOME_ACCESSORIES)
            db.session.add(product)
        product.name = "Test item — not for sale"
        product.summary = ""
        product.description = ""
        product.status = ProductStatus.ACTIVE
        product.free_shipping = True
        product.published_at = product.published_at or utcnow()
        if not product.variants:
            product.variants.append(Variant(sku="TEST-ITEM", price_cents=TEST_ITEM_CENTS))
        for variant in product.variants:
            variant.price_cents = TEST_ITEM_CENTS
            variant.compare_at_cents = None
            variant.track_inventory = False  # never runs out
            variant.active = True
        db.session.commit()
        cache.bump_catalog_version()
        click.echo(
            f"Test item ready at /product/{TEST_ITEM_SLUG}/ ($0.50, free shipping). "
            "Refund each test order in /admin, then hide the item in Studio > Products."
        )

    @catalog_cli.command("autoname")
    @click.option("--write", is_flag=True, help="Save names and colors into manifest.yaml.")
    @click.option("--redetect", is_flag=True, help="Ignore colors already in the manifest.")
    @click.option(
        "--sheet",
        type=click.Path(dir_okay=False),
        help="Also save a review image: each photo with background tinted pink, plus shares.",
    )
    @click.argument("slugs", nargs=-1)
    def autoname_cmd(
        write: bool, redetect: bool, sheet: str | None, slugs: tuple[str, ...]
    ) -> None:
        """Name products '<family> - <colors> - <code>' from their photos.

        Products with `autoname: false` are skipped. Colors listed in the manifest are kept
        (they override detection) unless --redetect is given.
        """
        path, assets, _ = _paths()
        text = path.read_text(encoding="utf-8")
        header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
        data = manifest.read(path)
        changed = 0
        reviewed: list[tuple[Path, dict[str, Any], naming.ColorAnalysis]] = []
        for p in data.get("products", []):
            if (slugs and p.get("slug") not in slugs) or p.get("autoname") is False:
                continue
            images = p.get("images") or []
            colors = [] if redetect else list(p.get("colors") or [])
            analysis = naming.analyze(assets / images[0]["file"]) if images else None
            if analysis:
                click.echo(f"{p['slug']}: {analysis.describe()}")
                reviewed.append((assets / images[0]["file"], p, analysis))
            if not colors and analysis:
                colors = analysis.names()
            parts = naming.split_name(p["name"], (p.get("categories") or [None])[0])
            new_name = naming.compose(parts.family, colors, parts.code)
            if new_name != p["name"] or colors != p.get("colors"):
                click.echo(f"    {p['name']}  ->  {new_name}")
                p["name"], p["colors"] = new_name, colors
                for img in images:  # keep alt text in step with the visible name
                    img["alt"] = new_name
                changed += 1
        if sheet and reviewed:
            naming.review_sheet(reviewed).save(sheet)
            click.echo(f"Review image saved to {sheet}")
        if write and changed:
            body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
            path.write_text(header + body, encoding="utf-8")
            click.echo(
                f"OK: updated {changed} product(s) in {path.name}. Run `flask catalog load`."
            )
        else:
            click.echo(f"{changed} product(s) would change. Re-run with --write to save.")

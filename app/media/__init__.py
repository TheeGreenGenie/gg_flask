"""Media library commands: import from the old WordPress site, and verify files on disk."""

from __future__ import annotations

from pathlib import Path

import click
from flask import Flask, current_app

from app.media import importer


def init_media(app: Flask) -> None:
    @app.cli.group("media")
    def media_cli() -> None:
        """Media library tools."""

    @media_cli.command("import-wordpress")
    @click.option(
        "--uploads",
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        required=True,
        help="WordPress wp-content/uploads folder.",
    )
    @click.option(
        "--theme-assets",
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        help="Optional extra folder (e.g. the child theme's 3d-assets).",
    )
    @click.option(
        "--wp-db",
        envvar="WORDPRESS_DB_URL",
        help="Read-only WordPress database URL for titles/alt text, "
        "e.g. mysql://root@localhost/new_wordpress_local",
    )
    @click.option("--dry-run", is_flag=True, help="Report what would happen; copy nothing.")
    def import_wordpress(
        uploads: Path, theme_assets: Path | None, wp_db: str | None, dry_run: bool
    ) -> None:
        """Copy real media from the old site into MEDIA_ROOT and record each file."""
        media_root = Path(current_app.config["MEDIA_ROOT"])
        meta = importer.wordpress_metadata(wp_db) if wp_db else None
        if meta is not None:
            click.echo(f"Read metadata for {len(meta)} WordPress attachments.")
        report = importer.import_tree(
            uploads,
            media_root,
            source="wordpress-uploads",
            dest_prefix="library",
            wp_meta=meta,
            dry_run=dry_run,
        )
        if theme_assets:
            importer.import_tree(
                theme_assets,
                media_root,
                source="wordpress-theme",
                dest_prefix="library/theme-assets",
                dry_run=dry_run,
                report=report,
            )
        verb = "Would import" if dry_run else "Imported"
        result = importer.summary(report)
        click.echo(f"{verb}: {result['imported']} ({result['megabytes']} MB) into {media_root}")
        click.echo("Skipped:")
        for reason, count in sorted(report.skipped.items(), key=lambda kv: -kv[1]):
            examples = ", ".join(report.examples.get(reason, []))
            click.echo(f"  {count:>5}  {reason}  (e.g. {examples})")

    @media_cli.command("verify")
    def verify_cmd() -> None:
        """Check every recorded file is on disk and unchanged (run after copying to a server)."""
        problems = importer.verify(Path(current_app.config["MEDIA_ROOT"]))
        total = sum(len(v) for v in problems.values())
        for kind, paths in problems.items():
            for path in paths[:20]:
                click.echo(f"  {kind}: {path}")
        if total:
            raise SystemExit(f"{total} problem(s) found")
        click.echo("OK: every recorded file is present and unchanged.")

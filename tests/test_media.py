from __future__ import annotations

import io
from pathlib import Path

from flask import Flask
from PIL import Image
from sqlalchemy import select

from app.extensions import db
from app.media import importer
from app.media.models import MediaAsset


def _jpeg(color: str = "red", size: tuple[int, int] = (40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def uploads(tmp_path: Path) -> Path:
    root = tmp_path / "uploads"
    _write(root, "2024/05/ankara.jpg", _jpeg())
    _write(root, "2024/05/ankara-300x200.jpg", _jpeg("blue", (30, 20)))  # WP size copy
    _write(root, "2024/05/big-scaled.jpg", _jpeg("green"))  # WP scaled copy of big.jpg
    _write(root, "2024/05/big.jpg", _jpeg("yellow", (80, 60)))
    _write(root, "2024/05/dupe.jpg", _jpeg())  # same bytes as ankara.jpg
    _write(root, "2024/05/logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    _write(root, "2024/05/evil.svg", b'<svg onload="alert(1)"></svg>')
    _write(root, "2024/05/fake.jpg", b"<?php echo 'not an image'; ?>")
    _write(root, "2024/05/broken.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    _write(root, "2026/02/clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    _write(root, "wc-logs/fatal-errors.log", b"order 123 customer@example.com")
    _write(root, "wpcf7_uploads/resume.jpg", _jpeg("purple"))
    _write(root, "woocommerce_uploads/invoice.png", _jpeg("pink"))
    _write(root, "index.php", b"<?php // Silence is golden.")
    return root


def test_import_copies_only_real_media_and_records_it(app: Flask, tmp_path: Path) -> None:
    root = uploads(tmp_path)
    media_root = Path(app.config["MEDIA_ROOT"])
    meta = {
        "2024/05/ankara.jpg": importer.WPMeta(
            7, "Ankara Blue", "", "Blue ankara fabric", "product", 42, "ankara-blue", None
        )
    }
    with app.app_context():
        report = importer.import_tree(
            root, media_root, source="wordpress-uploads", dest_prefix="library", wp_meta=meta
        )
        assets = {a.source_path: a for a in db.session.scalars(select(MediaAsset))}

    assert set(assets) == {
        "2024/05/ankara.jpg",
        "2024/05/big.jpg",
        "2024/05/logo.svg",
        "2026/02/clip.mp4",
    }
    assert report.imported == {"image": 2, "svg": 1, "video": 1}
    ankara = assets["2024/05/ankara.jpg"]
    assert (ankara.path, ankara.mime, ankara.width, ankara.height) == (
        "library/2024/05/ankara.jpg",
        "image/jpeg",
        40,
        30,
    )
    assert (ankara.alt, ankara.wp_parent_type, ankara.wp_parent_slug) == (
        "Blue ankara fabric",
        "product",
        "ankara-blue",
    )
    assert (media_root / "library/2024/05/ankara.jpg").read_bytes() == (
        root / "2024/05/ankara.jpg"
    ).read_bytes()
    assert report.skipped["WordPress size copy"] == 1
    assert report.skipped["WordPress scaled copy (original kept)"] == 1
    assert report.skipped["duplicate (already imported)"] == 1
    assert report.skipped["unsafe SVG (scripts or handlers)"] == 1
    assert report.skipped["unrecognised file type"] == 1
    assert report.skipped["corrupt image"] == 1
    assert report.skipped["private or generated folder"] == 3
    # Nothing from private folders reached the media root.
    copied = {p.relative_to(media_root).as_posix() for p in media_root.rglob("*") if p.is_file()}
    assert not any(
        part in c for c in copied for part in ("wc-logs", "wpcf7", "woocommerce", "evil", "php")
    )


def test_import_is_idempotent(app: Flask, tmp_path: Path) -> None:
    root = uploads(tmp_path)
    media_root = Path(app.config["MEDIA_ROOT"])
    with app.app_context():
        importer.import_tree(root, media_root, source="wordpress-uploads", dest_prefix="library")
        again = importer.import_tree(
            root, media_root, source="wordpress-uploads", dest_prefix="library"
        )
        count = len(db.session.scalars(select(MediaAsset)).all())
    assert sum(again.imported.values()) == 0 and count == 4


def test_dry_run_copies_and_records_nothing(app: Flask, tmp_path: Path) -> None:
    root = uploads(tmp_path)
    media_root = Path(app.config["MEDIA_ROOT"])
    with app.app_context():
        report = importer.import_tree(
            root, media_root, source="wordpress-uploads", dest_prefix="library", dry_run=True
        )
        assert db.session.scalars(select(MediaAsset)).first() is None
    assert sum(report.imported.values()) == 4
    assert not (media_root / "library").exists()


def test_verify_finds_missing_and_changed_files(app: Flask, tmp_path: Path) -> None:
    root = uploads(tmp_path)
    media_root = Path(app.config["MEDIA_ROOT"])
    with app.app_context():
        importer.import_tree(root, media_root, source="wordpress-uploads", dest_prefix="library")
        assert importer.verify(media_root) == {"missing": [], "changed": []}
        (media_root / "library/2024/05/big.jpg").unlink()
        (media_root / "library/2024/05/logo.svg").write_bytes(b"<svg>changed</svg>")
        problems = importer.verify(media_root)
    assert problems == {
        "missing": ["library/2024/05/big.jpg"],
        "changed": ["library/2024/05/logo.svg"],
    }


def test_unsafe_names_are_normalised_and_collisions_numbered() -> None:
    assert importer._safe_rel("2024/05/Ankara Print (1).JPG") == "2024/05/Ankara-Print-1.jpg"
    assert importer._safe_rel("../evil/..hidden") == "dir/evil/file.hidden"
    taken = {"library/a.jpg", "library/a-2.jpg"}
    assert importer._unique("library/a.jpg", taken) == "library/a-3.jpg"


def test_cli_import_reports_counts(app: Flask, tmp_path: Path) -> None:
    root = uploads(tmp_path)
    result = app.test_cli_runner().invoke(
        args=["media", "import-wordpress", "--uploads", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert "Imported: {'image': 2, 'svg': 1, 'video': 1}" in result.output
    verify = app.test_cli_runner().invoke(args=["media", "verify"])
    assert "OK" in verify.output


def test_media_responses_are_sandboxed(app: Flask) -> None:
    media_root = Path(app.config["MEDIA_ROOT"])
    _write(media_root, "library/logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    resp = app.test_client().get("/media/library/logo.svg")
    assert resp.status_code == 200
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    assert "default-src 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"

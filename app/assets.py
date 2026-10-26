"""Cache-friendly asset URLs.

Every `url_for('static', ...)` and product-image URL gets a `?v=` fingerprint that changes
whenever the file does, so browsers and CDNs can keep it for a year ("immutable") and
still pick up a new version immediately after a deploy or a new photo.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from flask import Flask, current_app, request
from werkzeug.wrappers import Response

YEAR = 365 * 24 * 3600
UNVERSIONED_MAX_AGE = 3600  # e.g. fonts referenced from CSS

_fingerprints: dict[tuple[str, int, int], str] = {}


def fingerprint(path: Path, content: bool) -> str:
    """Short version tag for a file, memoised on (path, mtime, size)."""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = (str(path), st.st_mtime_ns, st.st_size)
    tag = _fingerprints.get(key)
    if tag is None:
        h = hashlib.sha256()
        if content:  # small static files: hash the bytes so identical deploys keep URLs
            h.update(path.read_bytes())
        else:  # photos: mtime + size is enough and avoids reading megabytes
            h.update(f"{st.st_mtime_ns}:{st.st_size}".encode())
        tag = _fingerprints[key] = h.hexdigest()[:12]
    return tag


def media_version(rel_path: str) -> str:
    root = Path(current_app.config["MEDIA_ROOT"])
    target = (root / rel_path).resolve()
    if root.resolve() not in target.parents:
        return ""
    return fingerprint(target, content=False)


def init_assets(app: Flask) -> None:
    static_root = Path(app.static_folder or "static")

    @app.url_defaults
    def _version_static(endpoint: str, values: dict[str, str]) -> None:
        if endpoint == "static" and "filename" in values and "v" not in values:
            tag = fingerprint(static_root / values["filename"], content=True)
            if tag:
                values["v"] = tag

    @app.after_request
    def _cache_headers(response: Response) -> Response:
        if request.endpoint in ("static", "media.media_file") and response.status_code in (
            200,
            304,
        ):
            response.cache_control.no_cache = None  # Flask's default for send_file
            if request.args.get("v"):
                response.cache_control.public = True
                response.cache_control.max_age = YEAR
                response.cache_control.immutable = True
            else:
                response.cache_control.public = True
                response.cache_control.max_age = UNVERSIONED_MAX_AGE
        return response

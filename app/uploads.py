"""Request-size limits: small everywhere, larger only where staff upload photos.

MAX_CONTENT_LENGTH (2 MB) protects every form. Phone photos are usually 3–8 MB, so the photo
upload endpoints accept up to PHOTO_UPLOAD_MAX_BYTES; the image is then decoded with pixel limits
and re-encoded (app/catalog/images.py), so the larger body never reaches storage as-is.

The limit has to be decided by the request object itself: CSRF protection reads the form in an
earlier before_request hook, so changing it inside a view or later hook would be too late.
"""

from __future__ import annotations

from flask import Flask, Request, flash, redirect, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wrappers import Response

PHOTO_UPLOAD_MAX_BYTES = 15 * 1024 * 1024
PHOTO_UPLOAD_ENDPOINTS = frozenset(
    {
        "admin.image_upload",
        "studio.product_image_upload",
    }
)


class UploadAwareRequest(Request):
    @property
    def max_content_length(self) -> int | None:
        if self._max_content_length is None and self.endpoint in PHOTO_UPLOAD_ENDPOINTS:
            return PHOTO_UPLOAD_MAX_BYTES
        return super().max_content_length

    @max_content_length.setter
    def max_content_length(self, value: int | None) -> None:
        self._max_content_length = value


def init_uploads(app: Flask) -> None:
    app.request_class = UploadAwareRequest

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc: RequestEntityTooLarge) -> tuple[str, int] | Response:
        if request.endpoint in PHOTO_UPLOAD_ENDPOINTS:
            message = "That photo is larger than 15 MB. Please choose a smaller photo."
        else:
            message = "That was more than this form accepts. Please shorten it and try again."
        referrer = request.referrer or ""
        if referrer.startswith(request.host_url):  # back to the same site only
            flash(message, "error")
            return redirect(referrer, code=303)
        return render_template("errors/too_large.html", message=message), 413

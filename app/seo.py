"""Search-engine endpoints and deployment-owned verification files."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from flask import Blueprint, Flask, Response, abort, current_app, send_file, url_for
from sqlalchemy import select

from app.catalog.models import Category, Collection, Product, ProductStatus
from app.extensions import db

bp = Blueprint("seo", __name__)


@bp.get("/robots.txt")
def robots() -> Response:
    body = f"User-agent: *\nDisallow: /admin/\nSitemap: {url_for('seo.sitemap', _external=True)}\n"
    return Response(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap() -> Response:
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    paths = [
        url_for("store.home"),
        url_for("store.shop"),
        url_for("store.find_fabrics"),
        url_for("content.about"),
        url_for("content.contact"),
        url_for("content.faq"),
        url_for("content.privacy"),
        url_for("content.terms"),
        url_for("content.shipping"),
        url_for("content.returns"),
    ]
    paths.extend(
        url_for("store.category", slug=slug)
        for slug in db.session.scalars(select(Category.slug)).all()
    )
    paths.extend(
        url_for("store.collection", slug=slug)
        for slug in db.session.scalars(select(Collection.slug)).all()
    )
    paths.extend(
        url_for("store.product", slug=slug)
        for slug in db.session.scalars(
            select(Product.slug).where(Product.status == ProductStatus.ACTIVE)
        ).all()
    )
    for path in dict.fromkeys(paths):
        node = SubElement(root, "url")
        SubElement(node, "loc").text = url_for("store.home", _external=True).rstrip("/") + path
    return Response(
        tostring(root, encoding="utf-8", xml_declaration=True), mimetype="application/xml"
    )


@bp.get("/.well-known/apple-developer-merchantid-domain-association")
def apple_pay_verification() -> Response:
    configured = current_app.config.get("APPLE_PAY_DOMAIN_ASSOCIATION")
    if not configured:
        abort(404)
    path = Path(configured).resolve()
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="text/plain", conditional=True)


def init_seo(app: Flask) -> None:
    app.register_blueprint(bp)

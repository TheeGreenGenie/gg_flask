from __future__ import annotations

import json
from pathlib import Path

from flask import Flask


def test_robots_sitemap_metadata_and_legacy_redirect(shop) -> None:
    robots = shop.get("/robots.txt")
    assert robots.status_code == 200 and b"Disallow: /admin/" in robots.data
    sitemap = shop.get("/sitemap.xml")
    assert sitemap.status_code == 200 and b"/product/ankara-blue/" in sitemap.data
    assert b"/product/draft-hidden/" not in sitemap.data
    product = shop.get("/product/ankara-blue/").get_data(as_text=True)
    assert 'rel="canonical"' in product and 'itemtype="https://schema.org/Product"' in product
    redirect = shop.get("/support/")
    assert redirect.status_code == 301 and redirect.location == "/contact-us/"


def test_mailchimp_signup_uses_double_opt_in(shop, catalog_app: Flask) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def fake(method: str, url: str, headers: dict[str, str], body: bytes) -> None:
        calls.append((method, url, headers, body))

    catalog_app.config.update(
        MAILCHIMP_API_KEY="test-key",
        MAILCHIMP_SERVER_PREFIX="us1",
        MAILCHIMP_LIST_ID="audience",
    )
    catalog_app.extensions["marketing_http"] = fake
    response = shop.post("/newsletter", data={"email": "Reader@Example.com"})
    assert response.status_code == 302 and len(calls) == 1
    assert calls[0][0] == "PUT" and "/lists/audience/members/" in calls[0][1]
    assert json.loads(calls[0][3]) == {
        "email_address": "reader@example.com",
        "status_if_new": "pending",
    }


def test_apple_pay_verification_is_fail_closed(
    shop, catalog_app: Flask, tmp_path: Path
) -> None:
    path = "/.well-known/apple-developer-merchantid-domain-association"
    assert shop.get(path).status_code == 404
    association = tmp_path / "apple-pay.txt"
    association.write_text("verified", encoding="utf-8")
    catalog_app.config["APPLE_PAY_DOMAIN_ASSOCIATION"] = str(association)
    response = shop.get(path)
    assert response.status_code == 200 and response.data == b"verified"


def test_launch_check_reports_blockers(app: Flask) -> None:
    result = app.test_cli_runner().invoke(args=["launch", "check"])

    assert result.exit_code != 0
    assert "FAIL" in result.output
    assert "payments" in result.output
    assert "MANUAL policies" in result.output


def test_launch_check_passes_automated_configuration(app: Flask) -> None:
    runner = app.test_cli_runner()
    assert runner.invoke(args=["db", "stamp", "head"]).exit_code == 0
    app.config.update(
        STRIPE_SECRET_KEY="sk_test_example",  # noqa: S106 - fake provider test value
        STRIPE_WEBHOOK_SECRET="whsec_example",  # noqa: S106 - fake provider test value
    )

    result = runner.invoke(args=["launch", "check"])

    assert result.exit_code == 0, result.output
    assert "PASS   migrations" in result.output
    assert "Automated checks passed" in result.output

from flask import Flask

from app.studio import service


def test_daily_summary_uses_dedicated_monitoring_recipient(catalog_app: Flask) -> None:
    catalog_app.config.update(
        MONITORING_SUMMARY_EMAIL="solomonshasanmi@gmail.com",
        ORDER_NOTIFICATION_EMAIL="orders@example.com",
    )

    with catalog_app.app_context():
        assert service.send_daily_summary()

    message = catalog_app.extensions["outbox"][-1]
    assert message.to == "solomonshasanmi@gmail.com"
    assert message.subject == "Daily Staff Studio summary"
    assert "Failed sign-ins" in message.body
    assert "Scheduled jobs" in message.body


def test_daily_summary_keeps_order_email_as_deployment_fallback(catalog_app: Flask) -> None:
    catalog_app.config.update(
        MONITORING_SUMMARY_EMAIL=None,
        ORDER_NOTIFICATION_EMAIL="owner@example.com",
    )

    with catalog_app.app_context():
        assert service.send_daily_summary()

    assert catalog_app.extensions["outbox"][-1].to == "owner@example.com"

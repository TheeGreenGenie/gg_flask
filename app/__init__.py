"""Application factory."""

from __future__ import annotations

import logging
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.assets import init_assets
from app.catalog import init_catalog
from app.config import BaseConfig, load_config
from app.extensions import csrf, db, init_redis
from app.legacy import init_legacy_redirects
from app.log_config import configure_logging
from app.security import init_security_headers
from app.templating import init_templating
from app.uploads import init_uploads


def create_app(config: BaseConfig | str | None = None) -> Flask:
    app = Flask(__name__)

    cfg = config if isinstance(config, BaseConfig) else load_config(config)
    app.config.from_object(cfg)

    configure_logging(app)

    if str(app.config.get("SQLALCHEMY_DATABASE_URI") or "").startswith("postgresql"):
        # Fail fast instead of hanging when the database is unreachable.
        engine_opts = dict(app.config["SQLALCHEMY_ENGINE_OPTIONS"])
        engine_opts["connect_args"] = {"connect_timeout": 3, **engine_opts.get("connect_args", {})}
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_opts

    hops = app.config["TRUSTED_PROXY_HOPS"]
    if hops > 0:
        # Trust X-Forwarded-* only from the configured number of proxies.
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_prefix=0
        )

    from app import models  # noqa: F401  (register all tables)

    db.init_app(app)
    if os.environ.get("GG_WEB_ONLY") != "1":
        # `flask db ...` and `launch check` need Flask-Migrate. The web server never does, and
        # importing it (Alembic) costs ~0.2 s of every wake-up on hosts that idle the app.
        from flask_migrate import Migrate

        Migrate(app, db)
    csrf.init_app(app)
    init_redis(app)
    init_security_headers(app)
    from app.launch import init_launch
    from app.mail import init_mail

    init_mail(app)
    init_launch(app)
    init_uploads(app)
    init_templating(app)
    init_catalog(app)
    init_assets(app)
    init_legacy_redirects(app)

    from app.health import bp as health_bp
    from app.main import bp as main_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(main_bp)

    from app.store import bp as store_bp

    app.register_blueprint(store_bp)

    from app.content import bp as content_bp

    app.register_blueprint(content_bp)

    from app.accounts import init_accounts
    from app.admin import init_admin
    from app.jobs import init_jobs
    from app.media import init_media
    from app.orders import init_orders
    from app.reviews import init_reviews
    from app.seo import init_seo
    from app.studio import init_studio
    from app.wishlist import init_wishlist

    init_accounts(app)
    init_admin(app)
    init_jobs(app)
    init_media(app)
    init_orders(app)
    init_seo(app)
    init_studio(app)
    init_reviews(app)

    init_wishlist(app)

    logging.getLogger(__name__).info("app_started", extra={"app_env": app.config["APP_ENV"]})
    return app

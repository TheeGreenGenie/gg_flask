"""Application factory."""

from __future__ import annotations

import logging

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import BaseConfig, load_config
from app.extensions import csrf, db, init_redis, migrate
from app.log_config import configure_logging
from app.security import init_security_headers


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

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    init_redis(app)
    init_security_headers(app)

    from app.health import bp as health_bp
    from app.main import bp as main_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(main_bp)

    logging.getLogger(__name__).info("app_started", extra={"app_env": app.config["APP_ENV"]})
    return app

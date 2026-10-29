"""Gunicorn settings used by the SiteGround Node wrapper (deploy/siteground/server.js)."""

import os

bind = f"unix:{os.environ['GG_SOCKET']}"
umask = 0o077  # the socket is reachable by this hosting account only
# Recorded so the next wrapper start can stop this Gunicorn if SiteGround killed the wrapper
# without letting it shut Gunicorn down (see stopLeftovers in server.js).
pidfile = os.environ.get("GG_PIDFILE")
workers = int(os.environ.get("GG_WORKERS", "2"))  # the host caps processes; keep this small
preload_app = True  # import the app once, then fork: faster wake-up after idle, less memory
timeout = 30
graceful_timeout = 10
max_requests = 2000
max_requests_jitter = 200
# No access log: URLs can carry secrets (password-reset links, the private Studio path).
accesslog = None
errorlog = "-"
loglevel = "info"


def post_fork(server, worker):  # noqa: ARG001 - Gunicorn's hook signature
    """Each worker opens its own database connections instead of inheriting the master's."""
    from app.extensions import db
    from wsgi import app

    with app.app_context():
        for engine in db.engines.values():
            engine.dispose(close=False)

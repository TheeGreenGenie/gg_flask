"""Shared extension instances, initialised in the application factory."""

from __future__ import annotations

from flask import Flask, current_app
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from redis import Redis
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
csrf = CSRFProtect()


def init_redis(app: Flask) -> None:
    url = app.config.get("REDIS_URL")
    app.extensions["redis"] = (
        Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2) if url else None
    )


def get_redis() -> Redis | None:
    client: Redis | None = current_app.extensions.get("redis")
    return client

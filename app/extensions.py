"""Shared extension instances, initialised in the application factory."""

from __future__ import annotations

from typing import Any

from flask import Flask, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from redis import Redis
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)
csrf = CSRFProtect()


def init_redis(app: Flask) -> None:
    """Redis when REDIS_URL is set; otherwise the database stand-in if KV_BACKEND=database."""
    url = app.config.get("REDIS_URL")
    if url:
        app.extensions["redis"] = Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    elif app.config.get("KV_BACKEND") == "database":
        from app.kvstore import DatabaseKV

        app.extensions["redis"] = DatabaseKV()
    else:
        app.extensions["redis"] = None


def get_redis() -> Any:
    """The rate-limit/cache store: a redis-py client, the database stand-in, or None."""
    return current_app.extensions.get("redis")

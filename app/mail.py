"""Outgoing transactional email.

No provider is connected yet (I-001, chosen later). Backends:
- "console": development — prints the message to the terminal so links can be tested.
- "memory":  tests — collects messages in app.extensions["outbox"].
- unset:     production until a provider is configured — sending is unavailable, and
             features that need email say so instead of silently failing.
Messages are never written to the structured application log (they contain secrets).
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import current_app


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


def available() -> bool:
    return current_app.config.get("MAIL_BACKEND") in ("console", "memory")


def send(to: str, subject: str, body: str) -> bool:
    backend = current_app.config.get("MAIL_BACKEND")
    msg = Message(to, subject, body)
    if backend == "memory":
        current_app.extensions.setdefault("outbox", []).append(msg)
        return True
    if backend == "console":
        print(f"\n--- email to {to}: {subject} ---\n{body}\n--- end email ---\n", flush=True)  # noqa: T201
        return True
    return False

"""Outgoing transactional email.

Backends (MAIL_BACKEND):
- "smtp":    production — any transactional provider's SMTP relay (Postmark, Amazon SES,
             SendGrid, Google Workspace, or the server's own mail service). Needs SMTP_HOST and
             MAIL_FROM; SMTP_SECURITY is starttls (587) or ssl (465). Encryption is always on.
- "console": development — prints the message to the terminal so links can be tested.
- "memory":  tests — collects messages in app.extensions["outbox"].
- unset:     production until a provider is configured — sending is unavailable, and
             features that need email say so instead of silently failing.
Messages are never written to the structured application log (they contain secrets); a failed
send logs only the recipient's domain and the error type.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from flask import current_app

log = logging.getLogger(__name__)
SMTP_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


def available() -> bool:
    cfg = current_app.config
    backend = cfg.get("MAIL_BACKEND")
    if backend == "smtp":
        return bool(cfg.get("SMTP_HOST") and cfg.get("MAIL_FROM"))
    return backend in ("console", "memory")


def send(to: str, subject: str, body: str) -> bool:
    backend = current_app.config.get("MAIL_BACKEND")
    msg = Message(to, subject, body)
    if backend == "memory":
        current_app.extensions.setdefault("outbox", []).append(msg)
        return True
    if backend == "console":
        print(f"\n--- email to {to}: {subject} ---\n{body}\n--- end email ---\n", flush=True)  # noqa: T201
        return True
    if backend == "smtp" and available():
        return _send_smtp(msg)
    return False


def build(msg: Message) -> EmailMessage:
    """The MIME message; refuses header injection (CR/LF in any header value)."""
    cfg = current_app.config
    if any(c in value for value in (msg.to, msg.subject) for c in "\r\n"):
        raise ValueError("header values may not contain line breaks")
    if "@" not in parseaddr(msg.to)[1]:
        raise ValueError("invalid recipient")
    email = EmailMessage()
    email["From"] = cfg["MAIL_FROM"]
    email["To"] = msg.to
    email["Subject"] = msg.subject
    email["Date"] = formatdate(localtime=False)
    sender_domain = parseaddr(cfg["MAIL_FROM"])[1].rpartition("@")[2] or None
    email["Message-ID"] = make_msgid(domain=sender_domain)
    if cfg.get("MAIL_REPLY_TO"):
        email["Reply-To"] = cfg["MAIL_REPLY_TO"]
    email.set_content(msg.body)
    return email


def _send_smtp(msg: Message) -> bool:
    cfg = current_app.config
    domain = msg.to.rpartition("@")[2][:80]
    try:
        email = build(msg)
        context = ssl.create_default_context()
        host, port = cfg["SMTP_HOST"], int(cfg.get("SMTP_PORT") or 587)
        smtp: smtplib.SMTP
        if cfg.get("SMTP_SECURITY") == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
        else:
            smtp = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
        with smtp:
            if cfg.get("SMTP_SECURITY") != "ssl":
                smtp.starttls(context=context)  # never send credentials or mail in clear text
            if cfg.get("SMTP_USERNAME"):
                smtp.login(cfg["SMTP_USERNAME"], cfg.get("SMTP_PASSWORD") or "")
            smtp.send_message(email)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        log.warning("mail_send_failed", extra={"to_domain": domain, "error": type(exc).__name__})
        return False
    return True

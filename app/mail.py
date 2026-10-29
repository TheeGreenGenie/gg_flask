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
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

import click
from flask import Flask, current_app

log = logging.getLogger(__name__)
SMTP_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str  # plain text: always sent, for text-only mail apps and when images are blocked
    html: str | None = None  # optional designed version, sent alongside the text


def available() -> bool:
    cfg = current_app.config
    backend = cfg.get("MAIL_BACKEND")
    if backend == "smtp":
        return bool(cfg.get("SMTP_HOST") and cfg.get("MAIL_FROM"))
    return backend in ("console", "memory")


def send(to: str, subject: str, body: str, html: str | None = None) -> bool:
    backend = current_app.config.get("MAIL_BACKEND")
    msg = Message(to, subject, body, html)
    if backend == "memory":
        current_app.extensions.setdefault("outbox", []).append(msg)
        return True
    if backend == "console":
        extra = f"(+ HTML version, {len(html)} characters)\n" if html else ""
        text = f"\n--- email to {to}: {subject} ---\n{body}\n{extra}--- end email ---\n"
        # Windows consoles may not be UTF-8; a character like "→" must never break a request.
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding))
        sys.stdout.flush()
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
    if msg.html:
        email.add_alternative(msg.html, subtype="html")  # mail apps show the best they support
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


def init_mail(app: Flask) -> None:
    """Register commands that verify transactional mail without exposing message content."""

    @app.cli.group("mail")
    def mail_cli() -> None:
        """Transactional email diagnostics."""

    @mail_cli.command("test")
    @click.argument("recipient")
    def test_mail(recipient: str) -> None:
        """Send one harmless delivery test to RECIPIENT."""
        if not available():
            raise click.ClickException(
                "Email is not configured. Set MAIL_BACKEND=smtp, SMTP_HOST, and MAIL_FROM."
            )
        if not send(
            recipient,
            "GG Afrikan Eleganz email test",
            "Transactional email is configured and this test message was delivered.\n",
        ):
            raise click.ClickException("The mail provider rejected or could not deliver the test.")
        click.echo(f"Test message accepted for delivery to {recipient}.")

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr

log = logging.getLogger("mifp.mailer")


def _clean_header(value: str) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def _valid_email(value: str) -> str:
    name, addr = parseaddr(value or "")
    if not addr or "@" not in addr or any(ch in addr for ch in "\r\n"):
        raise ValueError("Invalid email address")
    return addr


def send_mail(app, *, to: str, subject: str, body: str, reply_to: str | None = None) -> bool:
    provider = str(app.config.get("MAIL_PROVIDER", "disabled") or "disabled").lower()
    if provider == "disabled":
        log.info("mail disabled subject=%s to=%s", _clean_header(subject), to)
        return False
    msg = EmailMessage()
    msg["From"] = _valid_email(app.config.get("MAIL_FROM", "no-reply@mifp.eu"))
    msg["To"] = _valid_email(to)
    msg["Subject"] = _clean_header(subject)[:180]
    if reply_to:
        msg["Reply-To"] = _valid_email(reply_to)
    msg.set_content(body or "", subtype="plain", charset="utf-8")
    if provider == "console":
        log.info("console mail\n%s", msg.as_string())
        return True
    if provider == "smtp":
        host = app.config.get("SMTP_HOST")
        if not host:
            raise RuntimeError("SMTP_HOST is not configured")
        port = int(app.config.get("SMTP_PORT", 587))
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if app.config.get("SMTP_USE_TLS", True):
                smtp.starttls()
            username = app.config.get("SMTP_USERNAME")
            password = app.config.get("SMTP_PASSWORD")
            if username:
                smtp.login(username, password or "")
            smtp.send_message(msg)
        return True
    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider}")

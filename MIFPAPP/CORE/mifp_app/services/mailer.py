from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

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
    from_address = _valid_email(app.config.get("MAIL_FROM", "no-reply@mifp.eu"))
    from_name = _clean_header(str(app.config.get("MAIL_FROM_NAME", "")))
    msg["From"] = formataddr((from_name, from_address)) if from_name else from_address
    msg["To"] = _valid_email(to)
    msg["Subject"] = _clean_header(subject)[:180]
    if reply_to:
        msg["Reply-To"] = _valid_email(reply_to)
    msg.set_content(body or "", subtype="plain", charset="utf-8")
    if provider == "console":
        # Join-request notifications carry personal data (name, affiliation,
        # motivation). Ordinary logs must not retain it, so only metadata is
        # logged at INFO and the full message is restricted to DEBUG; the
        # provider is refused outright in production.
        if str(app.config.get("ENV", "")).lower() == "production":
            raise RuntimeError("MAIL_PROVIDER=console is not allowed in production")
        log.info(
            "console mail subject=%s to=%s body_bytes=%d",
            _clean_header(subject)[:180],
            to,
            len(body or ""),
        )
        log.debug("console mail body\n%s", msg.as_string())
        return True
    if provider == "smtp":
        host = app.config.get("SMTP_HOST")
        if not host:
            raise RuntimeError("SMTP_HOST is not configured")
        port = int(app.config.get("SMTP_PORT", 587))
        security = str(app.config.get("SMTP_SECURITY", "starttls") or "starttls").lower()
        smtp_class = smtplib.SMTP_SSL if security == "tls" else smtplib.SMTP
        with smtp_class(host, port, timeout=20) as smtp:
            if security == "starttls":
                smtp.starttls()
            username = app.config.get("SMTP_USERNAME")
            password = app.config.get("SMTP_PASSWORD")
            if username:
                smtp.login(username, password or "")
            smtp.send_message(msg)
        return True
    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider}")

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

log = logging.getLogger("mifp.mailer")


def _clean_header(value: str) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def _valid_email(value: str) -> str:
    _name, addr = parseaddr(value or "")
    if not addr or "@" not in addr or any(ch in addr for ch in "\r\n"):
        raise ValueError("Invalid email address")
    return addr


def send_mail(
    app,
    *,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    reply_to: str | None = None,
) -> bool:
    """Send one message through the configured transport.

    The plain-text part is always present. ``html_body`` is an optional
    alternative used by MIFP notifications; callers never need HTML for mail
    delivery to work. SMTP credentials remain application configuration and are
    never copied into the message or log fields.
    """
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
    msg["Auto-Submitted"] = "auto-generated"
    if reply_to:
        msg["Reply-To"] = _valid_email(reply_to)
    msg.set_content(body or "", subtype="plain", charset="utf-8")
    if html_body:
        msg.add_alternative(str(html_body), subtype="html", charset="utf-8")

    if provider == "console":
        # Notifications can contain operational or membership information.
        # Ordinary logs retain metadata only; complete MIME content is DEBUG
        # only, and the console provider is forbidden in production.
        if str(app.config.get("ENV", "")).lower() == "production":
            raise RuntimeError("MAIL_PROVIDER=console is not allowed in production")
        log.info(
            "console mail subject=%s to=%s body_bytes=%d html=%s",
            _clean_header(subject)[:180],
            to,
            len(body or ""),
            bool(html_body),
        )
        log.debug("console mail body\n%s", msg.as_string())
        return True

    if provider == "smtp":
        host = str(app.config.get("SMTP_HOST") or "").strip()
        if not host:
            raise RuntimeError("SMTP_HOST is not configured")
        port = int(app.config.get("SMTP_PORT", 587))
        security = str(app.config.get("SMTP_SECURITY", "starttls") or "starttls").lower()
        if security not in {"tls", "starttls", "none"}:
            raise RuntimeError("SMTP_SECURITY must be tls, starttls or none")
        username = str(app.config.get("SMTP_USERNAME") or "")
        password = str(app.config.get("SMTP_PASSWORD") or "")
        if username and not password:
            raise RuntimeError("SMTP credentials are incomplete")
        if username and security == "none":
            raise RuntimeError("Authenticated SMTP requires TLS or STARTTLS")

        tls_context = ssl.create_default_context()
        smtp_class = smtplib.SMTP_SSL if security == "tls" else smtplib.SMTP
        smtp_kwargs = {"timeout": 20}
        if security == "tls":
            smtp_kwargs["context"] = tls_context
        with smtp_class(host, port, **smtp_kwargs) as smtp:
            if security == "starttls":
                smtp.ehlo()
                smtp.starttls(context=tls_context)
                smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
        return True

    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider}")

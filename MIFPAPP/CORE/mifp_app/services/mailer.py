from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

log = logging.getLogger("mifp.mailer")


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content_type: str
    data: bytes


def _clean_header(value: str) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def _valid_email(value: str) -> str:
    _name, addr = parseaddr(value or "")
    if not addr or "@" not in addr or any(ch in addr for ch in "\r\n"):
        raise ValueError("Invalid email address")
    return addr


def _valid_emails(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else list(value)
    return [_valid_email(str(item)) for item in values]


def send_mail(
    app,
    *,
    to: str | Sequence[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    reply_to: str | None = None,
    cc: Sequence[str] | None = None,
    bcc: Sequence[str] | None = None,
    attachments: Sequence[MailAttachment] | None = None,
    automated: bool = True,
) -> bool:
    """Send one message through the configured transport.

    The plain-text part is always present. ``html_body`` is an optional
    alternative used by MIFP notifications and administrator-authored mail.
    ``automated=False`` deliberately omits the Auto-Submitted header for a
    human-authored dashboard message. SMTP credentials remain application
    configuration and are never copied into the message or log fields.
    """
    to_addresses = _valid_emails(to)
    cc_addresses = _valid_emails(cc)
    bcc_addresses = _valid_emails(bcc)
    if not to_addresses:
        raise ValueError("At least one recipient is required")
    envelope_recipients = list(dict.fromkeys([*to_addresses, *cc_addresses, *bcc_addresses]))

    provider = str(app.config.get("MAIL_PROVIDER", "disabled") or "disabled").lower()
    if provider == "disabled":
        log.info("mail disabled subject=%s recipients=%d", _clean_header(subject), len(envelope_recipients))
        return False

    msg = EmailMessage()
    from_address = _valid_email(app.config.get("MAIL_FROM", "no-reply@mifp.eu"))
    from_name = _clean_header(str(app.config.get("MAIL_FROM_NAME", "")))
    msg["From"] = formataddr((from_name, from_address)) if from_name else from_address
    msg["To"] = ", ".join(to_addresses)
    if cc_addresses:
        msg["Cc"] = ", ".join(cc_addresses)
    msg["Subject"] = _clean_header(subject)[:180]
    if automated:
        msg["Auto-Submitted"] = "auto-generated"
    if reply_to:
        msg["Reply-To"] = _valid_email(reply_to)
    msg.set_content(body or "", subtype="plain", charset="utf-8")
    if html_body:
        msg.add_alternative(str(html_body), subtype="html", charset="utf-8")
    for attachment in attachments or ():
        maintype, subtype = str(attachment.content_type).split("/", 1)
        msg.add_attachment(
            bytes(attachment.data),
            maintype=maintype,
            subtype=subtype,
            filename=_clean_header(attachment.filename)[:120],
        )

    if provider == "console":
        # Notifications can contain operational or membership information.
        # Ordinary logs retain metadata only; complete MIME content is DEBUG
        # only, and the console provider is forbidden in production.
        if str(app.config.get("ENV", "")).lower() == "production":
            raise RuntimeError("MAIL_PROVIDER=console is not allowed in production")
        log.info(
            "console mail subject=%s recipients=%d body_bytes=%d html=%s attachments=%d",
            _clean_header(subject)[:180],
            len(envelope_recipients),
            len(body or ""),
            bool(html_body),
            len(attachments or ()),
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
            if len(envelope_recipients) == 1 and not cc_addresses and not bcc_addresses:
                smtp.send_message(msg)
            else:
                # BCC recipients are envelope-only and never appear in MIME headers.
                smtp.send_message(msg, to_addrs=envelope_recipients)
        return True

    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider}")

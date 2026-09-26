from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

import bleach

from ..db.connection import connect_readonly
from ..utils.logger import get_logger, log_event
from .mailer import send_mail

log = get_logger("notification")

DEFAULT_NOTIFICATION_SETTINGS: dict[str, str] = {
    "notification_enabled": "1",
    "notification_errors_enabled": "1",
    "notification_problems_enabled": "1",
    "notification_down_enabled": "1",
    "notification_recovery_enabled": "1",
    "notification_join_enabled": "1",
    "notification_auth_failed_enabled": "1",
    "notification_auth_success_enabled": "1",
    "notification_error_threshold": "3",
    "notification_problem_threshold": "3",
    "notification_down_threshold": "3",
    "notification_auth_failed_threshold": "3",
    "notification_window_minutes": "5",
    "notification_cooldown_minutes": "30",
}
_NOTIFICATION_BOOL_KEYS = frozenset(
    key for key in DEFAULT_NOTIFICATION_SETTINGS if key.endswith("_enabled")
)
_NOTIFICATION_INT_RANGES = {
    "notification_error_threshold": (1, 20),
    "notification_problem_threshold": (1, 20),
    "notification_down_threshold": (1, 20),
    "notification_auth_failed_threshold": (1, 20),
    "notification_window_minutes": (1, 1440),
    "notification_cooldown_minutes": (1, 1440),
}
_CATEGORY_SETTING = {
    "error": "notification_errors_enabled",
    "problem": "notification_problems_enabled",
    "down": "notification_down_enabled",
    "recovery": "notification_recovery_enabled",
    "join": "notification_join_enabled",
    "auth_failed": "notification_auth_failed_enabled",
    "auth_success": "notification_auth_success_enabled",
}
_THRESHOLD_SETTING = {
    "error": "notification_error_threshold",
    "problem": "notification_problem_threshold",
    "down": "notification_down_threshold",
    "auth_failed": "notification_auth_failed_threshold",
}

# Email-safe equivalents of the dashboard's canonical design tokens.
_EMAIL_THEME = {
    "page": "#eef1f4",
    "surface": "#ffffff",
    "shell": "#181b20",
    "shell_muted": "#aeb5bf",
    "text": "#2c3036",
    "muted": "#5f6670",
    "border": "#cbd0d6",
    "accent": "#a72b31",
    "accent_subtle": "#faeeee",
}
DEFAULT_EMAIL_TITLE = "MIFP VPS Notification"
MAX_MANUAL_MAIL_SUBJECT = 160
MAX_MANUAL_MAIL_TITLE = 80
MAX_MANUAL_MAIL_BODY = 10000

MAX_MANUAL_MAIL_RECIPIENTS = 30

_EMAIL_RICH_TAGS = frozenset({
    "p", "div", "br", "strong", "b", "em", "i", "u", "a", "ul", "ol", "li", "h2",
    "blockquote", "hr", "img", "table", "thead", "tbody", "tr", "th", "td",
})
MAX_MANUAL_MAIL_HTML = 40000

_SEVERITY_THEME = {
    "critical": ("#a72b31", "#faeeee", "CRITICAL"),
    "error": ("#a72b31", "#faeeee", "ERROR"),
    "warning": ("#9a5b08", "#fff7e6", "WARNING"),
    "recovery": ("#197149", "#edf8f2", "RECOVERED"),
    "success": ("#197149", "#edf8f2", "SUCCESS"),
    "security": ("#68519a", "#f4f1fa", "SECURITY"),
    "info": ("#315f9b", "#eef5fc", "INFO"),
}


@dataclass(frozen=True)
class NotificationResult:
    status: str
    sent: bool
    reason: str
    count: int = 0


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, *, low: int, high: int, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(parsed, high))


def _recipient(value: Any) -> str:
    raw = str(value or "").strip()
    _name, address = parseaddr(raw)
    if not address or "@" not in address or address != raw or any(ch in raw for ch in "\r\n"):
        return ""
    return address


def normalize_email_address(value: Any) -> str:
    """Return one canonical mailbox or an empty string.

    Dashboard mail intentionally supports exactly one recipient per send. This
    rejects display-name forms, comma-separated lists and header injection.
    """
    return _recipient(value)


def normalize_email_addresses(value: Any, *, limit: int = MAX_MANUAL_MAIL_RECIPIENTS) -> list[str]:
    """Return a de-duplicated list of bare mailboxes from a UI recipient field."""
    parts = re.split(r"[,;\n]+", str(value or ""))
    addresses: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part.strip():
            continue
        address = _recipient(part.strip())
        if not address:
            return []
        key = address.casefold()
        if key not in seen:
            seen.add(key)
            addresses.append(address)
        if len(addresses) > limit:
            return []
    return addresses


def mask_email(value: str) -> str:
    address = _recipient(value)
    if not address:
        return "Not configured"
    local, domain = address.rsplit("@", 1)
    visible = local[:1] if local else ""
    return f"{visible}{'*' * max(3, min(8, len(local) - 1))}@{domain}"


def notification_settings(app) -> dict[str, str]:
    settings = dict(DEFAULT_NOTIFICATION_SETTINGS)
    try:
        with connect_readonly(app.config["DATABASE_PATH"], timeout=0.35) as conn:
            rows = conn.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'notification_%'"
            ).fetchall()
        for row in rows:
            key = str(row["key"])
            if key in settings:
                settings[key] = str(row["value"] or "")
    except Exception as exc:
        # Notification delivery must still work while the database is degraded.
        # Keep this below ERROR to avoid an error->notification recursion loop.
        log_event(
            log,
            "notification.settings_fallback",
            "Notification settings unavailable; safe defaults are in use",
            level="DEBUG",
            error_type=type(exc).__name__,
        )
    return settings


def validate_notification_settings(values: Mapping[str, Any]) -> dict[str, str]:
    normalized = dict(DEFAULT_NOTIFICATION_SETTINGS)
    for key in _NOTIFICATION_BOOL_KEYS:
        normalized[key] = "1" if _truthy(values.get(key)) else "0"
    for key, (low, high) in _NOTIFICATION_INT_RANGES.items():
        raw = values.get(key, DEFAULT_NOTIFICATION_SETTINGS[key])
        try:
            parsed = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if not low <= parsed <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        normalized[key] = str(parsed)
    return normalized


def smtp_status(app) -> dict[str, Any]:
    provider = str(app.config.get("MAIL_PROVIDER", "disabled") or "disabled").strip().lower()
    host = str(app.config.get("SMTP_HOST") or "").strip()
    username = str(app.config.get("SMTP_USERNAME") or "").strip()
    password = str(app.config.get("SMTP_PASSWORD") or "")
    from_address = _recipient(app.config.get("MAIL_FROM", ""))
    recipient = _recipient(app.config.get("MAIL_TO", ""))
    security = str(app.config.get("SMTP_SECURITY", "starttls") or "starttls").strip().lower()
    try:
        port = int(app.config.get("SMTP_PORT", 587))
    except (TypeError, ValueError):
        port = 0
    ready = (
        provider in {"smtp", "console"}
        and bool(recipient)
        and (
            provider == "console"
            or (bool(host) and bool(from_address) and (not username or bool(password)))
        )
    )
    return {
        "provider": provider,
        "host": host,
        "port": port,
        "security": security,
        "from_address": from_address,
        "recipient": recipient,
        "recipient_masked": mask_email(recipient),
        "username_configured": bool(username),
        "password_configured": bool(password),
        "ready": ready,
    }


def _display_subject(subject: str) -> str:
    """Remove machine-oriented leading [tags] from the in-message heading.

    The SMTP Subject header can retain operational prefixes for filtering, while
    the visible HTML card stays readable and institutional.
    """
    value = " ".join(str(subject or "").replace("\r", " ").replace("\n", " ").split())
    while value.startswith("[") and "]" in value:
        _tag, remainder = value.split("]", 1)
        value = remainder.lstrip()
    return value or DEFAULT_EMAIL_TITLE


def _html_body(body: str) -> str:
    paragraphs: list[str] = []
    for paragraph in str(body or "").strip().split("\n\n"):
        if not paragraph.strip():
            continue
        lines = "<br>".join(escape(line) for line in paragraph.splitlines())
        paragraphs.append(
            f'<p style="margin:0 0 14px;color:{_EMAIL_THEME["text"]};'
            'font-size:14px;line-height:1.6;">'
            f"{lines}</p>"
        )
    if not paragraphs:
        paragraphs.append(
            f'<p style="margin:0;color:{_EMAIL_THEME["muted"]};font-size:14px;line-height:1.6;">'
            "No additional details were provided.</p>"
        )
    return "".join(paragraphs)


def sanitize_manual_email_html(raw_html: str) -> str:
    """Return the tiny HTML subset accepted from the dashboard visual editor."""
    def allowed_attribute(tag: str, name: str, value: str) -> bool:
        if tag == "a" and name == "href":
            return value.lower().startswith(("https://", "http://", "mailto:"))
        if tag == "img" and name in {"src", "alt", "width", "height"}:
            return name != "src" or value.lower().startswith("https://")
        if tag in {"table", "th", "td"} and name in {"width", "colspan", "rowspan"}:
            return value.isdigit() and 1 <= int(value) <= 1000
        return False

    cleaned = bleach.clean(
        str(raw_html or ""),
        tags=_EMAIL_RICH_TAGS,
        attributes=allowed_attribute,
        protocols={"http", "https", "mailto"},
        strip=True,
        strip_comments=True,
    )
    # Bleach removes unsafe attributes but intentionally preserves the tag.
    # An image without its validated HTTPS source has no useful mail behavior.
    return re.sub(r'<img(?![^>]*\ssrc="https://)[^>]*>', "", cleaned, flags=re.IGNORECASE)


class _ManualEmailTextExtractor(HTMLParser):
    """Create a readable plain-text alternative from sanitized rich mail HTML."""

    _BLOCKS = {"p", "div", "h2", "blockquote", "ul", "ol", "table", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.list_stack: list[str] = []
        self.ordered_index: list[int] = []

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._BLOCKS:
            self._newline()
        if tag == "br":
            self._newline()
        elif tag == "ul":
            self.list_stack.append("ul")
            self.ordered_index.append(0)
        elif tag == "ol":
            self.list_stack.append("ol")
            self.ordered_index.append(0)
        elif tag == "li":
            self._newline()
            if self.list_stack and self.list_stack[-1] == "ol":
                self.ordered_index[-1] += 1
                self.parts.append(f"{self.ordered_index[-1]}. ")
            else:
                self.parts.append("- ")
        elif tag in {"td", "th"} and self.parts and not self.parts[-1].endswith(("\n", "\t")):
            self.parts.append("\t")
        elif tag == "img":
            alt = dict(attrs).get("alt", "")
            if alt:
                self.parts.append(f"[{alt}]")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "div", "h2", "blockquote", "li", "tr"}:
            self._newline()
        if tag in {"ul", "ol"} and self.list_stack:
            self.list_stack.pop()
            self.ordered_index.pop()
            self._newline()

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        lines = [line.rstrip() for line in "".join(self.parts).replace("\r", "").split("\n")]
        compact: list[str] = []
        for line in lines:
            if not line and compact and not compact[-1]:
                continue
            compact.append(line)
        return "\n".join(compact).strip()


def manual_email_content(*, text: str, rich_html: str | None = None) -> tuple[str, str]:
    """Normalize a manual message and return its plain and safe-HTML forms."""
    fallback = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not rich_html:
        return fallback, _html_body(fallback)
    safe_html = sanitize_manual_email_html(rich_html)
    extractor = _ManualEmailTextExtractor()
    extractor.feed(safe_html)
    plain = extractor.text() or fallback
    return plain, safe_html


def _style_manual_email_content(safe_html: str) -> str:
    """Add fixed email-client-friendly styles after untrusted markup is sanitized."""
    styled = re.sub(
        r"<table(?P<attrs>[^>]*)>",
        r'<table\g<attrs> cellspacing="0" cellpadding="0" '
        r'style="width:100%;margin:16px 0;border-collapse:collapse;">',
        safe_html,
        flags=re.IGNORECASE,
    )
    styled = re.sub(
        r"<th(?P<attrs>[^>]*)>",
        r'<th\g<attrs> style="padding:8px 10px;background:#f1f3f5;border:1px solid #cbd0d6;text-align:left;">',
        styled,
        flags=re.IGNORECASE,
    )
    styled = re.sub(
        r"<td(?P<attrs>[^>]*)>",
        r'<td\g<attrs> style="padding:8px 10px;border:1px solid #cbd0d6;text-align:left;">',
        styled,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"<img(?P<attrs>[^>]*)>",
        r'<img\g<attrs> style="display:block;max-width:100%;height:auto;margin:16px 0;">',
        styled,
        flags=re.IGNORECASE,
    )


def _render_mifp_html(
    *,
    title: str,
    subject: str,
    body: str,
    severity: str = "info",
    event: str | None = None,
    badge_label: str | None = None,
    footer: str,
    body_html: str | None = None,
    strip_subject_tags: bool = True,
) -> str:
    """Render a self-contained email-safe MIFP card.

    All caller-controlled text is escaped here. The dashboard never accepts
    arbitrary HTML for outgoing mail.
    """
    level = str(severity or "info").strip().lower()
    color, subtle, default_label = _SEVERITY_THEME.get(level, _SEVERITY_THEME["info"])
    label = escape(str(badge_label or default_label)[:24])
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    safe_title = escape(str(title or DEFAULT_EMAIL_TITLE)[:MAX_MANUAL_MAIL_TITLE])
    display_subject = _display_subject(subject) if strip_subject_tags else str(subject or DEFAULT_EMAIL_TITLE)
    safe_subject = escape(display_subject[:180])
    safe_event = escape(str(event or ""))
    safe_footer = escape(str(footer or ""))
    rendered_body = (
        _style_manual_email_content(sanitize_manual_email_html(body_html))
        if body_html is not None
        else _html_body(body)
    )
    event_line = (
        f'<b style="color:{_EMAIL_THEME["text"]};">Event</b> &nbsp; {safe_event}<br>'
        if safe_event else ""
    )
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:{_EMAIL_THEME['page']};font-family:Inter,Segoe UI,Arial,sans-serif;color:{_EMAIL_THEME['text']};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{_EMAIL_THEME['page']};padding:28px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:{_EMAIL_THEME['surface']};border:1px solid {_EMAIL_THEME['border']};border-radius:6px;overflow:hidden;">
<tr><td style="background:{_EMAIL_THEME['shell']};padding:18px 22px;border-left:5px solid {_EMAIL_THEME['accent']};">
  <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:{_EMAIL_THEME['shell_muted']};font-weight:700;">Mediterranean Institute of Fundamental Physics</div>
  <div style="margin-top:5px;color:#ffffff;font-size:20px;line-height:1.3;font-weight:750;">{safe_title}</div>
</td></tr>
<tr><td style="padding:22px;">
  <div style="display:inline-block;padding:5px 8px;border-radius:999px;background:{subtle};color:{color};font-size:11px;font-weight:800;letter-spacing:.08em;">{label}</div>
  <h1 style="margin:13px 0 16px;font-size:21px;line-height:1.35;color:{_EMAIL_THEME['text']};font-weight:750;">{safe_subject}</h1>
  <div style="border-left:3px solid {color};padding:2px 0 2px 15px;">{rendered_body}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:20px;background:#f8f9fa;border:1px solid #e2e5e9;border-radius:4px;">
    <tr><td style="padding:10px 12px;color:{_EMAIL_THEME['muted']};font-size:12px;line-height:1.5;">{event_line}<b style="color:{_EMAIL_THEME['text']};">Generated</b> &nbsp; {generated}</td></tr>
  </table>
</td></tr>
<tr><td style="padding:15px 22px;background:#f8f9fa;border-top:1px solid #e2e5e9;color:{_EMAIL_THEME['muted']};font-size:11px;line-height:1.5;">
{safe_footer}
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def render_notification_html(*, subject: str, body: str, severity: str, event: str) -> str:
    """Render the operational notification template."""
    return _render_mifp_html(
        title=DEFAULT_EMAIL_TITLE,
        subject=subject,
        body=body,
        severity=severity,
        event=event,
        footer=(
            "Automated operational message from MIFP. Passwords, tokens and SMTP secrets "
            "are never included. Review Dashboard → Notifications and the server logs for "
            "additional details."
        ),
    )


def render_manual_email_html(*, title: str, subject: str, body: str, body_html: str | None = None) -> str:
    """Render an administrator-authored message with a calm institutional layout."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    safe_title = escape(str(title or "MIFP message")[:MAX_MANUAL_MAIL_TITLE])
    rendered_body = (
        _style_manual_email_content(sanitize_manual_email_html(body_html))
        if body_html is not None
        else _html_body(body)
    )
    # `subject` intentionally stays in the SMTP header only: repeating it inside
    # the card made manual messages look like operational alerts.
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:{_EMAIL_THEME['page']};font-family:Inter,Segoe UI,Arial,sans-serif;color:{_EMAIL_THEME['text']};">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{_EMAIL_THEME['page']};padding:28px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:{_EMAIL_THEME['surface']};border:1px solid {_EMAIL_THEME['border']};border-radius:7px;overflow:hidden;">
<tr><td style="background:{_EMAIL_THEME['shell']};padding:14px 24px;border-left:5px solid {_EMAIL_THEME['accent']};">
  <div style="color:#ffffff;font-size:14px;line-height:1.3;font-weight:800;letter-spacing:.02em;">MIFP</div>
  <div style="margin-top:2px;color:{_EMAIL_THEME['shell_muted']};font-size:10px;line-height:1.4;letter-spacing:.09em;text-transform:uppercase;">Mediterranean Institute of Fundamental Physics</div>
</td></tr>
<tr><td style="padding:30px 34px 32px;">
  <h1 style="margin:0 0 24px;font-size:25px;line-height:1.28;color:{_EMAIL_THEME['text']};font-weight:750;">{safe_title}</h1>
  <div style="color:{_EMAIL_THEME['text']};font-size:15px;line-height:1.7;">{rendered_body}</div>
</td></tr>
<tr><td style="padding:13px 24px;background:#f8f9fa;border-top:1px solid #e2e5e9;color:{_EMAIL_THEME['muted']};font-size:10px;line-height:1.5;">
Sent from the MIFP administrative dashboard &middot; {generated}
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def _state_paths(app) -> tuple[Path, Path]:
    directory = Path(app.config["RUNTIME_CONFIG_DIR"])
    return directory / "notification_state.json", directory / ".notification_state.lock"


def _manual_history_paths(app) -> tuple[Path, Path]:
    directory = Path(app.config["RUNTIME_CONFIG_DIR"])
    return directory / "manual_email_history.json", directory / ".manual_email_history.lock"


def _read_manual_history(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    deliveries = payload.get("deliveries") if isinstance(payload, dict) else None
    if not isinstance(deliveries, list):
        return []
    return [row for row in deliveries if isinstance(row, dict)]


def manual_email_history(app, *, limit: int = 40) -> list[dict[str, Any]]:
    """Return the bounded, privacy-safe manual delivery history."""
    history_path, lock_path = _manual_history_paths(app)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
        return _read_manual_history(history_path)[:max(0, limit)]


def record_manual_email_delivery(app, *, delivery_id: str, details: Mapping[str, Any]) -> None:
    """Persist one successful manual delivery without message bodies or raw addresses."""
    history_path, lock_path = _manual_history_paths(app)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "notification.delivered",
        "message": "Manual dashboard email delivered",
        "details": {"delivery_id": str(delivery_id), **dict(details)},
    }
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        deliveries = _read_manual_history(history_path)
        deliveries.insert(0, row)
        _write_state(history_path, {"deliveries": deliveries[:100]})


def _read_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"keys": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), dict):
        return {"keys": {}}
    return payload


def _write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".notification-state-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _reserve_delivery(
    app,
    *,
    key: str,
    threshold: int,
    window_seconds: int,
    cooldown_seconds: int,
    now: float,
) -> tuple[bool, str, int]:
    state_path, lock_path = _state_paths(app)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        state = _read_state(state_path)
        keys = state.setdefault("keys", {})
        # Bound state growth from one-shot events (for example membership
        # requests) and old source-IP hashes. Anything older than the larger
        # policy horizon is irrelevant to future threshold/cooldown decisions.
        retention_seconds = max(window_seconds, cooldown_seconds, 24 * 60 * 60) * 2
        stale = [
            existing_key
            for existing_key, existing in keys.items()
            if not isinstance(existing, dict)
            or now - float(existing.get("last_seen") or existing.get("last_sent") or 0) > retention_seconds
        ]
        for existing_key in stale:
            keys.pop(existing_key, None)
        record = keys.get(key)
        if not isinstance(record, dict):
            record = {}
        window_start = float(record.get("window_start") or 0)
        count = int(record.get("count") or 0)
        last_sent = float(record.get("last_sent") or 0)
        if not window_start or now - window_start > window_seconds:
            window_start = now
            count = 0
        count += 1
        record.update(window_start=window_start, count=count, last_seen=now)
        if last_sent and now - last_sent < cooldown_seconds:
            keys[key] = record
            _write_state(state_path, state)
            return False, "cooldown", count
        if count < threshold:
            keys[key] = record
            _write_state(state_path, state)
            return False, "threshold", count
        # Reserve before the network call so concurrent Gunicorn workers cannot
        # emit the same alert. A failed delivery clears this reservation below.
        record["last_sent"] = now
        record["count"] = 0
        record["window_start"] = now
        keys[key] = record
        _write_state(state_path, state)
        return True, "ready", count


def _clear_failed_reservation(app, key: str) -> None:
    state_path, lock_path = _state_paths(app)
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            state = _read_state(state_path)
            record = state.get("keys", {}).get(key)
            if isinstance(record, dict):
                record["last_sent"] = 0
                _write_state(state_path, state)
    except OSError:
        return


def _notify_impl(
    app,
    *,
    event: str,
    category: str,
    severity: str,
    subject: str,
    body: str,
    dedup_key: str | None = None,
    force: bool = False,
    threshold: int | None = None,
    reply_to: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> NotificationResult:
    settings = notification_settings(app)
    status = smtp_status(app)
    fields = {
        "notification_event": event,
        "category": category,
        "severity": severity,
        **dict(metadata or {}),
    }
    if not force:
        if not _truthy(settings["notification_enabled"]):
            log_event(log, "notification.suppressed", "Notification disabled", reason="disabled", **fields)
            return NotificationResult("suppressed", False, "disabled")
        category_key = _CATEGORY_SETTING.get(category)
        if category_key and not _truthy(settings.get(category_key)):
            log_event(log, "notification.suppressed", "Notification category disabled", reason="category_disabled", **fields)
            return NotificationResult("suppressed", False, "category_disabled")
    if not status["ready"]:
        log_event(log, "notification.skipped", "Mail transport is not configured", reason="transport_unavailable", **fields)
        return NotificationResult("skipped", False, "transport_unavailable")

    current = float(time.time() if now is None else now)
    key = dedup_key or f"{category}:{event}"
    count = 1
    if not force:
        threshold_key = _THRESHOLD_SETTING.get(category)
        threshold_default = int(DEFAULT_NOTIFICATION_SETTINGS.get(threshold_key or "", "1"))
        effective_threshold = (
            _bounded_int(threshold, low=1, high=20, default=threshold_default)
            if threshold is not None
            else (
                1
                if not threshold_key
                else _bounded_int(settings.get(threshold_key), low=1, high=20, default=threshold_default)
            )
        )
        window_minutes = _bounded_int(settings.get("notification_window_minutes"), low=1, high=1440, default=5)
        cooldown_minutes = _bounded_int(settings.get("notification_cooldown_minutes"), low=1, high=1440, default=30)
        try:
            reserved, reason, count = _reserve_delivery(
                app,
                key=key,
                threshold=effective_threshold,
                window_seconds=window_minutes * 60,
                cooldown_seconds=cooldown_minutes * 60,
                now=current,
            )
        except OSError as exc:
            # A broken state directory must not disable critical delivery. SMTP
            # still remains bounded by the provider, while the failure is visible.
            log_event(
                log,
                "notification.state_unavailable",
                "Notification deduplication state unavailable",
                level="WARNING",
                error_type=type(exc).__name__,
                **fields,
            )
            reserved, reason = True, "state_unavailable"
        if not reserved:
            log_event(
                log,
                "notification.suppressed",
                "Notification suppressed by delivery policy",
                reason=reason,
                count=count,
                **fields,
            )
            return NotificationResult("suppressed", False, reason, count)

    delivery_body = str(body or "")
    if count > 1:
        delivery_body = f"{delivery_body.rstrip()}\n\nAggregated occurrences: {count}\n"
    html_body = render_notification_html(
        subject=subject,
        body=delivery_body,
        severity=severity,
        event=event,
    )
    try:
        delivered = send_mail(
            app,
            to=status["recipient"],
            subject=subject,
            body=delivery_body,
            html_body=html_body,
            reply_to=reply_to,
        )
        if not delivered:
            if not force:
                _clear_failed_reservation(app, key)
            log_event(log, "notification.skipped", "Mail provider declined notification", reason="provider_disabled", **fields)
            return NotificationResult("skipped", False, "provider_disabled", count)
    except Exception as exc:
        if not force:
            _clear_failed_reservation(app, key)
        log_event(
            log,
            "notification.failed",
            "Notification delivery failed",
            level="ERROR",
            error_type=type(exc).__name__,
            **fields,
        )
        return NotificationResult("failed", False, type(exc).__name__, count)

    log_event(log, "notification.delivered", "Notification delivered", count=count, **fields)
    return NotificationResult("delivered", True, "sent", count)


def notify(
    app,
    *,
    event: str,
    category: str,
    severity: str,
    subject: str,
    body: str,
    dedup_key: str | None = None,
    force: bool = False,
    threshold: int | None = None,
    reply_to: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> NotificationResult:
    """Deliver one bounded notification without making the caller depend on SMTP.

    This is deliberately fail-safe: notification infrastructure must never turn
    a successful login, membership request or normal application error response
    into a second application failure. Secrets are never persisted in SQLite or
    attached to structured notification log records.
    """
    try:
        return _notify_impl(
            app,
            event=event,
            category=category,
            severity=severity,
            subject=subject,
            body=body,
            dedup_key=dedup_key,
            force=force,
            threshold=threshold,
            reply_to=reply_to,
            metadata=metadata,
            now=now,
        )
    except Exception as exc:
        try:
            log_event(
                log,
                "notification.internal_failure",
                "Notification subsystem failed safely",
                level="ERROR",
                notification_event=event,
                category=category,
                severity=severity,
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        return NotificationResult("failed", False, type(exc).__name__)

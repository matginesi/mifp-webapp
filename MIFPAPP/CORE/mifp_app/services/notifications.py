from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from html import escape
from pathlib import Path
from typing import Any, Mapping

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


def _render_mifp_html(
    *,
    title: str,
    subject: str,
    body: str,
    severity: str = "info",
    event: str | None = None,
    badge_label: str | None = None,
    footer: str,
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
    safe_subject = escape(str(subject or DEFAULT_EMAIL_TITLE)[:180])
    safe_event = escape(str(event or ""))
    safe_footer = escape(str(footer or ""))
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
  <div style="border-left:3px solid {color};padding:2px 0 2px 15px;">{_html_body(body)}</div>
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


def render_manual_email_html(*, title: str, subject: str, body: str) -> str:
    """Render one administrator-authored message in the MIFP visual language."""
    return _render_mifp_html(
        title=title,
        subject=subject,
        body=body,
        severity="info",
        badge_label="MIFP",
        event=None,
        footer=(
            "Message sent by an authenticated MIFP administrator from the MIFP dashboard. "
            "No SMTP credentials or session data are included."
        ),
    )


def _state_paths(app) -> tuple[Path, Path]:
    directory = Path(app.config["RUNTIME_CONFIG_DIR"])
    return directory / "notification_state.json", directory / ".notification_state.lock"


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

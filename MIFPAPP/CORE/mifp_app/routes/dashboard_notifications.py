from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, session, url_for

from ..db.connection import connect
from ..services.dashboard_repository import search_logs
from ..services.mailer import send_mail
from ..services.notifications import (
    DEFAULT_NOTIFICATION_SETTINGS,
    MAX_MANUAL_MAIL_BODY,
    MAX_MANUAL_MAIL_SUBJECT,
    MAX_MANUAL_MAIL_TITLE,
    mask_email,
    normalize_email_address,
    notification_settings,
    notify,
    render_manual_email_html,
    smtp_status,
    validate_notification_settings,
)
from ..utils.logger import audit_log, get_logger, log_event
from ..utils.security import get_client_ip, ip_rate_allowed
from .auth import login_required
from .dashboard import bp


log = get_logger("notification")


_NOTIFICATION_TEST_TYPES = {
    "info": {
        "label": "Information",
        "severity": "info",
        "subject": "[MIFP][TEST] Information notification",
        "event": "test_info",
        "body": (
            "This is an informational MIFP VPS notification preview.\n\n"
            "Use it to verify the standard HTML layout and plain-text fallback."
        ),
    },
    "warning": {
        "label": "Operational warning",
        "severity": "warning",
        "subject": "[MIFP][TEST][WARNING] Operational problem",
        "event": "test_warning",
        "body": (
            "This is a simulated operational warning.\n\n"
            "No real service degradation or storage problem has been detected."
        ),
    },
    "critical": {
        "label": "Critical / down",
        "severity": "critical",
        "subject": "[MIFP][TEST][CRITICAL] Production unavailable",
        "event": "test_critical",
        "body": (
            "This is a simulated production-down notification.\n\n"
            "No real health check has failed and no action is required."
        ),
    },
    "recovery": {
        "label": "Recovery",
        "severity": "recovery",
        "subject": "[MIFP][TEST][RECOVERED] Production recovered",
        "event": "test_recovery",
        "body": (
            "This is a simulated recovery message.\n\n"
            "It previews the email sent when a monitored incident clears."
        ),
    },
    "security": {
        "label": "Security / login",
        "severity": "security",
        "subject": "[MIFP][TEST][SECURITY] Authentication activity",
        "event": "test_security",
        "body": (
            "This is a simulated authentication security notification.\n\n"
            "Passwords, tokens, cookies and session identifiers are never included."
        ),
    },
    "join": {
        "label": "Join member request",
        "severity": "info",
        "subject": "[MIFP][TEST] New member request",
        "event": "test_join",
        "body": (
            "This is a simulated MIFP membership request notification.\n\n"
            "Request ID: TEST-001\nName: Example Researcher\nAffiliation: Example Institute\n\n"
            "Review the complete request in Dashboard → Join requests."
        ),
    },
    "error": {
        "label": "Application error",
        "severity": "error",
        "subject": "[MIFP][TEST][ERROR] Application error",
        "event": "test_error",
        "body": (
            "This is a simulated application error notification.\n\n"
            "No real exception was raised. Detailed stack traces remain in server logs."
        ),
    },
}


def _clean_single_line(value: str, *, max_length: int) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:max_length]


@bp.get("/notifications")
@login_required
def notifications():
    settings = notification_settings(current_app)
    transport = smtp_status(current_app)
    history = search_logs(
        current_app.config["LOG_DIR"],
        event="notification.",
        level="ALL",
        limit=60,
    )
    return render_template(
        "dashboard/notifications.html",
        notification_settings=settings,
        defaults=DEFAULT_NOTIFICATION_SETTINGS,
        transport=transport,
        history=history,
        test_types=_NOTIFICATION_TEST_TYPES,
        max_mail_subject=MAX_MANUAL_MAIL_SUBJECT,
        max_mail_title=MAX_MANUAL_MAIL_TITLE,
        max_mail_body=MAX_MANUAL_MAIL_BODY,
    )


@bp.post("/notifications")
@login_required
def notifications_save():
    submitted = request.form.to_dict(flat=True)
    try:
        normalized = validate_notification_settings(submitted)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard.notifications"))

    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            for key, value in normalized.items():
                conn.execute(
                    """
                    INSERT INTO settings(key,value,updated_at)
                    VALUES(?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                      value=excluded.value,
                      updated_at=CURRENT_TIMESTAMP
                    """,
                    (key, value),
                )
            conn.commit()
    except Exception:
        current_app.logger.exception("notification settings save failed")
        flash("Notification settings could not be saved. Check the server log.", "error")
        return redirect(url_for("dashboard.notifications"))

    audit_log(
        "notifications.settings_update",
        "notification delivery policy updated",
        category="system",
        keys=sorted(normalized),
    )
    flash("Notification settings saved.", "success")
    return redirect(url_for("dashboard.notifications"))


@bp.post("/notifications/test")
@login_required
def notifications_test():
    test_type = str(request.form.get("test_type") or "info").strip().lower()
    sample = _NOTIFICATION_TEST_TYPES.get(test_type)
    if sample is None:
        flash("Unknown notification test type.", "error")
        return redirect(url_for("dashboard.notifications"))

    result = notify(
        current_app,
        event=sample["event"],
        category="test",
        severity=sample["severity"],
        subject=sample["subject"],
        body=sample["body"],
        dedup_key=f"dashboard:test:{test_type}",
        force=True,
        metadata={"source": "dashboard", "test_type": test_type},
    )
    if result.sent:
        audit_log(
            "notifications.test",
            "test notification sent",
            category="system",
            test_type=test_type,
        )
        flash(f"{sample['label']} test email sent.", "success")
    elif result.reason == "transport_unavailable":
        flash(
            "SMTP is not configured yet. Configure it on the VPS with "
            "sudo mifpctl configure --section mail, then retry.",
            "warning",
        )
    else:
        flash("Test email could not be sent. Check the notification history and server log.", "error")
    return redirect(url_for("dashboard.notifications"))


@bp.post("/notifications/send")
@login_required
def notifications_send():
    """Send one bounded administrator-authored email through the existing relay."""
    transport = smtp_status(current_app)
    if not transport["ready"]:
        log_event(
            log,
            "notification.skipped",
            "Manual email skipped because mail transport is unavailable",
            reason="transport_unavailable",
            notification_event="manual_email",
            category="manual",
            severity="info",
        )
        flash("SMTP is not configured. Manual email was not sent.", "warning")
        return redirect(url_for("dashboard.notifications"))

    recipient = normalize_email_address(request.form.get("recipient"))
    subject = _clean_single_line(
        request.form.get("subject", ""), max_length=MAX_MANUAL_MAIL_SUBJECT
    )
    title = _clean_single_line(
        request.form.get("mail_title", ""), max_length=MAX_MANUAL_MAIL_TITLE
    )
    body = str(request.form.get("message") or "").strip()
    plain_text_only = str(request.form.get("plain_text_only") or "").lower() in {
        "1", "true", "yes", "on"
    }

    errors: list[str] = []
    if not recipient:
        errors.append("Enter one valid recipient email address.")
    if not subject:
        errors.append("Subject is required.")
    if not body:
        errors.append("Message is required.")
    if len(body) > MAX_MANUAL_MAIL_BODY:
        errors.append(f"Message must be at most {MAX_MANUAL_MAIL_BODY} characters.")
    if not plain_text_only and not title:
        errors.append("Email title is required for the MIFP HTML layout.")
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("dashboard.notifications") + "#compose-email")

    rate_key = f"{session.get('admin_username') or 'admin'}:{get_client_ip()}"
    if not ip_rate_allowed(
        "dashboard_manual_email",
        rate_key,
        limit=12,
        window_seconds=10 * 60,
        db_path=str(current_app.config["DATABASE_PATH"]),
    ):
        audit_log(
            "notifications.manual_email_rate_limited",
            "manual email rate limit reached",
            category="security",
            recipient=mask_email(recipient),
        )
        log_event(
            log,
            "notification.suppressed",
            "Manual email suppressed by rate limit",
            reason="rate_limited",
            notification_event="manual_email",
            category="manual",
            severity="security",
            recipient=mask_email(recipient),
        )
        flash("Manual email rate limit reached. Try again later.", "error")
        return redirect(url_for("dashboard.notifications") + "#compose-email")

    html_body = None
    if not plain_text_only:
        html_body = render_manual_email_html(title=title, subject=subject, body=body)

    try:
        delivered = send_mail(
            current_app,
            to=recipient,
            subject=subject,
            body=body,
            html_body=html_body,
            automated=False,
        )
    except Exception as exc:
        current_app.logger.exception("manual dashboard email delivery failed")
        audit_log(
            "notifications.manual_email_failed",
            "manual dashboard email failed",
            category="system",
            recipient=mask_email(recipient),
            error_type=type(exc).__name__,
            plain_text_only=plain_text_only,
        )
        log_event(
            log,
            "notification.failed",
            "Manual dashboard email failed",
            level="ERROR",
            reason=type(exc).__name__,
            notification_event="manual_email",
            category="manual",
            severity="error",
            recipient=mask_email(recipient),
            mail_format="plain" if plain_text_only else "html",
        )
        flash("Email could not be sent. Check the server log.", "error")
        return redirect(url_for("dashboard.notifications") + "#compose-email")

    if not delivered:
        log_event(
            log,
            "notification.skipped",
            "Manual email provider declined delivery",
            reason="provider_disabled",
            notification_event="manual_email",
            category="manual",
            severity="info",
            recipient=mask_email(recipient),
        )
        flash("Mail transport is disabled. Email was not sent.", "warning")
        return redirect(url_for("dashboard.notifications") + "#compose-email")

    audit_log(
        "notifications.manual_email_sent",
        "manual dashboard email sent",
        category="system",
        recipient=mask_email(recipient),
        subject_length=len(subject),
        message_length=len(body),
        plain_text_only=plain_text_only,
    )
    log_event(
        log,
        "notification.delivered",
        "Manual dashboard email delivered",
        notification_event="manual_email",
        category="manual",
        severity="info",
        recipient=mask_email(recipient),
        mail_format="plain" if plain_text_only else "html",
    )
    flash(f"Email sent to {mask_email(recipient)}.", "success")
    return redirect(url_for("dashboard.notifications") + "#compose-email")

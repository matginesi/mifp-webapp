from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from ..db.connection import connect
from ..services.dashboard_repository import search_logs
from ..services.mailer import MailAttachment, send_mail
from ..services.notifications import (
    DEFAULT_NOTIFICATION_SETTINGS,
    MAX_MANUAL_MAIL_BODY,
    MAX_MANUAL_MAIL_HTML,
    MAX_MANUAL_MAIL_RECIPIENTS,
    MAX_MANUAL_MAIL_SUBJECT,
    MAX_MANUAL_MAIL_TITLE,
    manual_email_content,
    manual_email_history,
    mask_email,
    normalize_email_addresses,
    notification_settings,
    notify,
    render_manual_email_html,
    record_manual_email_delivery,
    smtp_status,
    validate_notification_settings,
)
from ..utils.logger import audit_log, get_logger, log_event
from ..utils.security import get_client_ip, ip_rate_allowed
from .auth import login_required
from .dashboard import bp


log = get_logger("notification")

MAX_MANUAL_MAIL_ATTACHMENTS = 5
MAX_MANUAL_MAIL_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_MANUAL_MAIL_ATTACHMENTS_TOTAL_BYTES = 10 * 1024 * 1024
MAX_MANUAL_MAIL_REQUEST_BYTES = MAX_MANUAL_MAIL_ATTACHMENTS_TOTAL_BYTES + 512 * 1024
_ATTACHMENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


_NOTIFICATION_TEST_TYPES = {
    "info": {
        "label": "Information",
        "severity": "info",
        "subject": "Test — Information notification",
        "event": "test_info",
        "body": (
            "This is an informational MIFP VPS notification preview.\n\n"
            "Use it to verify the standard HTML layout and plain-text fallback."
        ),
    },
    "warning": {
        "label": "Operational warning",
        "severity": "warning",
        "subject": "Test — Operational warning",
        "event": "test_warning",
        "body": (
            "This is a simulated operational warning.\n\n"
            "No real service degradation or storage problem has been detected."
        ),
    },
    "critical": {
        "label": "Critical / down",
        "severity": "critical",
        "subject": "Test — Production unavailable",
        "event": "test_critical",
        "body": (
            "This is a simulated production-down notification.\n\n"
            "No real health check has failed and no action is required."
        ),
    },
    "recovery": {
        "label": "Recovery",
        "severity": "recovery",
        "subject": "Test — Production recovered",
        "event": "test_recovery",
        "body": (
            "This is a simulated recovery message.\n\n"
            "It previews the email sent when a monitored incident clears."
        ),
    },
    "security": {
        "label": "Security / login",
        "severity": "security",
        "subject": "Test — Authentication activity",
        "event": "test_security",
        "body": (
            "This is a simulated authentication security notification.\n\n"
            "Passwords, tokens, cookies and session identifiers are never included."
        ),
    },
    "join": {
        "label": "Join member request",
        "severity": "info",
        "subject": "Test — New member request",
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
        "subject": "Test — Application error",
        "event": "test_error",
        "body": (
            "This is a simulated application error notification.\n\n"
            "No real exception was raised. Detailed stack traces remain in server logs."
        ),
    },
}


def _clean_single_line(value: str, *, max_length: int) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:max_length]


def _valid_office_archive(data: bytes, extension: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if len(members) > 1000 or "[Content_Types].xml" not in names:
                return False
            if sum(member.file_size for member in members) > 50 * 1024 * 1024:
                return False
            root = "word/" if extension == ".docx" else "xl/"
            return any(name.startswith(root) for name in names)
    except (OSError, zipfile.BadZipFile):
        return False


def _attachment_matches_type(data: bytes, extension: str) -> bool:
    if extension == ".pdf":
        return data.startswith(b"%PDF-")
    if extension == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if extension in {".txt", ".csv"}:
        if b"\x00" in data:
            return False
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    if extension in {".docx", ".xlsx"}:
        return data.startswith(b"PK\x03\x04") and _valid_office_archive(data, extension)
    return False


def _manual_mail_attachments() -> tuple[list[MailAttachment], list[str]]:
    uploads = [item for item in request.files.getlist("attachments") if item and item.filename]
    if len(uploads) > MAX_MANUAL_MAIL_ATTACHMENTS:
        return [], [f"Add at most {MAX_MANUAL_MAIL_ATTACHMENTS} attachments."]

    attachments: list[MailAttachment] = []
    errors: list[str] = []
    total_bytes = 0
    seen_names: set[str] = set()
    for upload in uploads:
        filename = secure_filename(Path(upload.filename or "").name)[:120]
        extension = Path(filename).suffix.lower()
        if not filename or extension not in _ATTACHMENT_TYPES:
            errors.append(f"{upload.filename or 'Attachment'}: file type is not allowed.")
            continue
        if filename.casefold() in seen_names:
            errors.append(f"{filename}: duplicate attachment name.")
            continue
        data = upload.stream.read(MAX_MANUAL_MAIL_ATTACHMENT_BYTES + 1)
        if not data:
            errors.append(f"{filename}: file is empty.")
            continue
        if len(data) > MAX_MANUAL_MAIL_ATTACHMENT_BYTES:
            errors.append(f"{filename}: file exceeds the 5 MB limit.")
            continue
        total_bytes += len(data)
        if total_bytes > MAX_MANUAL_MAIL_ATTACHMENTS_TOTAL_BYTES:
            errors.append("Attachments exceed the 10 MB total limit.")
            break
        if not _attachment_matches_type(data, extension):
            errors.append(f"{filename}: content does not match the file type.")
            continue
        seen_names.add(filename.casefold())
        attachments.append(MailAttachment(filename, _ATTACHMENT_TYPES[extension], data))
    return attachments, errors


def _masked_recipient_summary(to: list[str], cc: list[str], bcc: list[str]) -> str:
    visible = [mask_email(address) for address in [*to, *cc]]
    if bcc:
        visible.append(f"{len(bcc)} BCC")
    return ", ".join(visible)


@bp.get("/notifications")
@login_required
def notifications():
    settings = notification_settings(current_app)
    transport = smtp_status(current_app)
    history = search_logs(
        current_app.config["LOG_DIR"],
        event="notification.",
        level="ALL",
        limit=120,
    )
    stored_sent_history = manual_email_history(current_app, limit=40)
    stored_delivery_ids = {
        str(row.get("details", {}).get("delivery_id") or "")
        for row in stored_sent_history
    }
    logged_sent_history = [
        row for row in history
        if row.get("event") == "notification.delivered"
        and str(row.get("details", {}).get("delivery_id") or "") not in stored_delivery_ids
    ]
    sent_history = sorted(
        [*stored_sent_history, *logged_sent_history],
        key=lambda row: str(row.get("when") or ""),
        reverse=True,
    )[:40]
    return render_template(
        "dashboard/notifications.html",
        notification_settings=settings,
        defaults=DEFAULT_NOTIFICATION_SETTINGS,
        transport=transport,
        history=history,
        sent_history=sent_history,
        test_types=_NOTIFICATION_TEST_TYPES,
        max_mail_subject=MAX_MANUAL_MAIL_SUBJECT,
        max_mail_title=MAX_MANUAL_MAIL_TITLE,
        max_mail_body=MAX_MANUAL_MAIL_BODY,
        max_mail_recipients=MAX_MANUAL_MAIL_RECIPIENTS,
        max_mail_attachments=MAX_MANUAL_MAIL_ATTACHMENTS,
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
            "SMTP is not configured yet. Configure the server mail transport, then retry.",
            "warning",
        )
    else:
        flash("Test email could not be sent. Check the notification history and server log.", "error")
    return redirect(url_for("dashboard.notifications"))


@bp.post("/notifications/send")
@login_required
def notifications_send():
    """Send one bounded administrator-authored email through the existing relay."""
    request.max_content_length = MAX_MANUAL_MAIL_REQUEST_BYTES
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

    recipients = normalize_email_addresses(request.form.get("recipients") or request.form.get("recipient"))
    cc = normalize_email_addresses(request.form.get("cc"))
    bcc = normalize_email_addresses(request.form.get("bcc"))
    subject = _clean_single_line(
        request.form.get("subject", ""), max_length=MAX_MANUAL_MAIL_SUBJECT
    )
    title = _clean_single_line(
        request.form.get("mail_title", ""), max_length=MAX_MANUAL_MAIL_TITLE
    )
    submitted_body = str(request.form.get("message") or "").strip()
    submitted_html = str(request.form.get("message_html") or "")
    plain_text_only = str(request.form.get("plain_text_only") or "").lower() in {
        "1", "true", "yes", "on"
    }

    if plain_text_only:
        body = submitted_body.replace("\r\n", "\n").replace("\r", "\n").strip()
        safe_body_html = None
    else:
        body, safe_body_html = manual_email_content(text=submitted_body, rich_html=submitted_html)

    attachments, attachment_errors = _manual_mail_attachments()
    errors: list[str] = list(attachment_errors)
    if not recipients:
        errors.append("Enter at least one valid recipient email address.")
    if request.form.get("cc", "").strip() and not cc:
        errors.append("Check the CC recipient list.")
    if request.form.get("bcc", "").strip() and not bcc:
        errors.append("Check the BCC recipient list.")
    all_addresses = [*recipients, *cc, *bcc]
    if len({address.casefold() for address in all_addresses}) != len(all_addresses):
        errors.append("Each recipient can appear only once across To, CC and BCC.")
    if len(all_addresses) > MAX_MANUAL_MAIL_RECIPIENTS:
        errors.append(f"Use at most {MAX_MANUAL_MAIL_RECIPIENTS} recipients per message.")
    if not subject:
        errors.append("Subject is required.")
    if not body:
        errors.append("Message is required.")
    if len(body) > MAX_MANUAL_MAIL_BODY:
        errors.append(f"Message must be at most {MAX_MANUAL_MAIL_BODY} characters.")
    if len(submitted_html) > MAX_MANUAL_MAIL_HTML:
        errors.append("Formatted message payload is too large.")
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
            recipients=len(all_addresses),
        )
        log_event(
            log,
            "notification.suppressed",
            "Manual email suppressed by rate limit",
            reason="rate_limited",
            notification_event="manual_email",
            category="manual",
            severity="security",
            recipients=len(all_addresses),
        )
        flash("Manual email rate limit reached. Try again later.", "error")
        return redirect(url_for("dashboard.notifications") + "#compose-email")

    html_body = None
    if not plain_text_only:
        html_body = render_manual_email_html(
            title=title, subject=subject, body=body, body_html=safe_body_html
        )

    try:
        delivered = send_mail(
            current_app,
            to=recipients[0] if len(recipients) == 1 else recipients,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            html_body=html_body,
            attachments=attachments,
            automated=False,
        )
    except Exception as exc:
        current_app.logger.exception("manual dashboard email delivery failed")
        audit_log(
            "notifications.manual_email_failed",
            "manual dashboard email failed",
            category="system",
            recipients=len(all_addresses),
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
            recipients=len(all_addresses),
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
            recipients=len(all_addresses),
        )
        flash("Mail transport is disabled. Email was not sent.", "warning")
        return redirect(url_for("dashboard.notifications") + "#compose-email")

    audit_log(
        "notifications.manual_email_sent",
        "manual dashboard email sent",
        category="system",
        recipients=len(all_addresses),
        subject_length=len(subject),
        message_length=len(body),
        plain_text_only=plain_text_only,
    )
    delivery_id = uuid.uuid4().hex
    delivery_details = {
        "notification_event": "manual_email",
        "category": "manual",
        "severity": "info",
        "recipients_masked": _masked_recipient_summary(recipients, cc, bcc),
        "to_count": len(recipients),
        "cc_count": len(cc),
        "bcc_count": len(bcc),
        "subject": subject,
        "attachment_count": len(attachments),
        "attachment_names": [attachment.filename for attachment in attachments],
        "mail_format": "plain" if plain_text_only else "html",
    }
    history_recorded = True
    try:
        record_manual_email_delivery(
            current_app,
            delivery_id=delivery_id,
            details=delivery_details,
        )
    except OSError as exc:
        history_recorded = False
        log_event(
            log,
            "notification.history_failed",
            "manual email history could not be persisted",
            level="WARNING",
            error_type=type(exc).__name__,
        )

    log_event(
        log,
        "notification.delivered",
        "Manual dashboard email delivered",
        delivery_id=delivery_id,
        **delivery_details,
    )
    if history_recorded:
        flash(f"Email sent to {len(all_addresses)} recipient{'s' if len(all_addresses) != 1 else ''}.", "success")
    else:
        flash("Email sent, but its delivery history could not be saved.", "warning")
    return redirect(url_for("dashboard.notifications") + "#compose-email")

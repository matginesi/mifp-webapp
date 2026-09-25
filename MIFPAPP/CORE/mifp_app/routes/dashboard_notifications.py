from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for

from ..db.connection import connect
from ..services.dashboard_repository import search_logs
from ..services.notifications import (
    DEFAULT_NOTIFICATION_SETTINGS,
    notification_settings,
    notify,
    smtp_status,
    validate_notification_settings,
)
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import bp


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
    result = notify(
        current_app,
        event="test",
        category="test",
        severity="info",
        subject="[MIFP] Notification test",
        body=(
            "MIFP notification test\n\n"
            "The application can reach the configured mail transport.\n"
            "No application error or incident triggered this message.\n"
        ),
        dedup_key="dashboard:test",
        force=True,
        metadata={"source": "dashboard"},
    )
    if result.sent:
        audit_log("notifications.test", "test notification sent", category="system")
        flash("Test email sent.", "success")
    elif result.reason == "transport_unavailable":
        flash(
            "SMTP is not configured yet. Configure it on the VPS with "
            "sudo mifpctl configure --section mail, then retry.",
            "warning",
        )
    else:
        flash("Test email could not be sent. Check the notification history and server log.", "error")
    return redirect(url_for("dashboard.notifications"))

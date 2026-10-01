from __future__ import annotations

import re
import time

from flask import Response, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ..services.mailer import send_mail
from ..services.notifications import (
    manual_email_content,
    normalize_email_address,
    render_poll_invitation_html,
    smtp_status,
)
from ..services.polls import (
    PollError,
    PollNotFound,
    anonymize_poll,
    clean_poll,
    cleanup_retention,
    create_invitation,
    create_poll,
    delete_poll,
    export_csv,
    export_pdf,
    export_xlsx,
    get_poll,
    list_polls,
    poll_analysis,
    reset_poll,
    response_history,
    response_view,
    save_poll,
    set_invitation_status,
)
from ..utils.logger import audit_log, get_logger, log_event
from ..utils.security import get_client_ip, ip_rate_allowed
from .auth import login_required
from .dashboard import bp

log = get_logger("polls")


def _runtime_dir():
    return current_app.config["RUNTIME_CONFIG_DIR"]


def _json_payload() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise PollError("Expected a JSON object")
    return payload


def _poll_error(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), 404 if isinstance(exc, PollNotFound) else status


def _personalize(value: str, first_name: str, last_name: str) -> str:
    rendered = (
        str(value or "").replace("{{first_name}}", str(first_name or "")).replace("{{last_name}}", str(last_name or ""))
    )
    rendered = re.sub(r"[ \t]+([,.;:!?])", r"\1", rendered)
    rendered = re.sub(r"(?im)^\s*(hello|dear|hi)\s*[,!]\s*$", "Hello,", rendered)
    return rendered


def _invitation_content(poll: dict, *, first_name: str, last_name: str, link: str) -> tuple[str, str, str]:
    subject = _personalize(str(poll.get("invitation_subject") or poll["title"]), first_name, last_name)
    message = _personalize(str(poll.get("invitation_message") or ""), first_name, last_name)
    body, safe_body = manual_email_content(text=message)
    plain = f"{body.rstrip()}\n\n{poll.get('invitation_cta') or 'Open poll'}: {link}\n"
    html = render_poll_invitation_html(
        title=poll["title"],
        body=plain,
        body_html=safe_body,
        cta_url=link,
        cta_label=str(poll.get("invitation_cta") or "Open poll"),
        deadline=str(poll.get("deadline") or ""),
        question_count=len(poll.get("questions") or []),
        allow_changes=bool(poll.get("allow_changes", True)),
    )
    return subject, plain, html


@bp.get("/notifications/polls")
@login_required
def polls():
    rows = list_polls(_runtime_dir())
    return render_template(
        "dashboard/polls.html",
        polls=rows,
        poll=None,
        responses=[],
        analysis=None,
        transport=smtp_status(current_app),
    )


@bp.post("/notifications/polls/new")
@login_required
def polls_new():
    try:
        poll = create_poll(_runtime_dir(), str(request.form.get("template") or "blank"))
    except PollError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard.polls"))
    audit_log(
        "poll.created",
        "poll created",
        category="poll",
        poll_id=poll["id"],
        question_count=len(poll["questions"]),
        response_mode=poll["response_mode"],
    )
    return redirect(url_for("dashboard.poll_detail", poll_id=poll["id"]))


@bp.get("/notifications/polls/<poll_id>")
@login_required
def poll_detail(poll_id: str):
    try:
        cleanup_retention(_runtime_dir())
        poll = get_poll(_runtime_dir(), poll_id)
        rows = list_polls(_runtime_dir())
        responses = response_view(_runtime_dir(), poll_id)
        analysis = poll_analysis(_runtime_dir(), poll_id)
    except PollError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard.polls"))
    return render_template(
        "dashboard/polls.html",
        polls=rows,
        poll=poll,
        responses=responses,
        analysis=analysis,
        transport=smtp_status(current_app),
    )


@bp.post("/notifications/polls/<poll_id>")
@login_required
def poll_update(poll_id: str):
    request.max_content_length = 256 * 1024
    try:
        before = get_poll(_runtime_dir(), poll_id)
        poll = save_poll(_runtime_dir(), poll_id, _json_payload())
    except PollError as exc:
        return _poll_error(exc)
    event = "poll.updated"
    if before.get("status") != poll.get("status"):
        event = "poll.opened" if poll["status"] == "open" else "poll.closed" if poll["status"] == "closed" else event
    audit_log(
        event,
        "poll updated",
        category="poll",
        poll_id=poll_id,
        status=poll["status"],
        question_count=len(poll["questions"]),
        response_mode=poll["response_mode"],
    )
    return jsonify({"ok": True, "poll": poll})


@bp.post("/notifications/polls/<poll_id>/invite")
@login_required
def poll_invite(poll_id: str):
    request.max_content_length = 32 * 1024
    started = time.monotonic()
    try:
        payload = _json_payload()
        recipient = normalize_email_address(payload.get("email"))
        if not recipient:
            raise PollError("Enter a valid recipient email address")
        poll = get_poll(_runtime_dir(), poll_id)
        if not smtp_status(current_app)["ready"]:
            raise PollError("SMTP is not configured")
        rate_key = f"{session.get('admin_username') or 'admin'}:{get_client_ip()}"
        if not ip_rate_allowed(
            "poll_invitation",
            rate_key,
            limit=300,
            window_seconds=60 * 60,
            db_path=str(current_app.config["DATABASE_PATH"]),
        ):
            return jsonify({"error": "Invitation rate limit reached"}), 429
        invitation, token = create_invitation(
            _runtime_dir(),
            poll_id,
            first_name=str(payload.get("first_name") or ""),
            last_name=str(payload.get("last_name") or ""),
        )
        link = url_for("public.respond", _external=True) + f"#p={poll_id}&t={token}"
        subject, plain, html = _invitation_content(
            poll,
            first_name=str(payload.get("first_name") or ""),
            last_name=str(payload.get("last_name") or ""),
            link=link,
        )
        # Arm the opaque link before handing the message to SMTP. If the process
        # dies after SMTP acceptance, the delivered link remains usable. A normal
        # send failure immediately revokes and destroys the stored token hash.
        set_invitation_status(_runtime_dir(), poll_id, invitation["invitation_id"], "sent")
        try:
            delivered = send_mail(
                current_app,
                to=recipient,
                subject=subject,
                body=plain,
                html_body=html,
                automated=False,
                privacy_safe_log=True,
            )
        except Exception:
            set_invitation_status(_runtime_dir(), poll_id, invitation["invitation_id"], "revoked")
            raise
        if not delivered:
            set_invitation_status(_runtime_dir(), poll_id, invitation["invitation_id"], "revoked")
            raise PollError("Mail transport declined the invitation")
    except PollError as exc:
        log_event(
            log,
            "poll.invitation_failed",
            "poll invitation failed",
            level="WARNING",
            poll_id=poll_id,
            error_type=type(exc).__name__,
        )
        return _poll_error(exc)
    except Exception as exc:
        current_app.logger.error("poll invitation delivery failed error_type=%s", type(exc).__name__)
        log_event(
            log,
            "poll.invitation_failed",
            "poll invitation failed",
            level="ERROR",
            poll_id=poll_id,
            error_type=type(exc).__name__,
        )
        return jsonify({"error": "Invitation could not be sent"}), 502
    audit_log(
        "poll.invitation_sent",
        "poll invitation accepted by SMTP",
        category="poll",
        poll_id=poll_id,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return jsonify({"ok": True, "accepted": True})



@bp.get("/notifications/polls/<poll_id>/responses")
@login_required
def poll_responses(poll_id: str):
    try:
        return jsonify({"responses": response_view(_runtime_dir(), poll_id)})
    except PollError as exc:
        return _poll_error(exc)


@bp.get("/notifications/polls/<poll_id>/responses/<response_id>/history")
@login_required
def poll_response_history(poll_id: str, response_id: str):
    try:
        return jsonify(response_history(_runtime_dir(), poll_id, response_id))
    except PollError as exc:
        return _poll_error(exc)


@bp.get("/notifications/polls/<poll_id>/analysis")
@login_required
def poll_analysis_data(poll_id: str):
    try:
        return jsonify(poll_analysis(_runtime_dir(), poll_id))
    except PollError as exc:
        return _poll_error(exc)


@bp.get("/notifications/polls/<poll_id>/export.<fmt>")
@login_required
def poll_export(poll_id: str, fmt: str):
    try:
        poll = get_poll(_runtime_dir(), poll_id)
        if fmt == "csv":
            payload, mimetype, extension = export_csv(_runtime_dir(), poll_id), "text/csv; charset=utf-8", "csv"
        elif fmt == "xlsx":
            payload, mimetype, extension = (
                export_xlsx(_runtime_dir(), poll_id),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xlsx",
            )
        elif fmt == "pdf":
            payload, mimetype, extension = export_pdf(_runtime_dir(), poll_id), "application/pdf", "pdf"
        else:
            raise PollError("Unsupported export format")
    except PollError as exc:
        return _poll_error(exc)
    filename = re.sub(r"[^a-z0-9]+", "-", poll["title"].lower()).strip("-")[:60] or "poll"
    return Response(
        payload,
        mimetype=mimetype,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.{extension}"',
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@bp.post("/notifications/polls/<poll_id>/clean")
@login_required
def poll_clean(poll_id: str):
    try:
        result = clean_poll(_runtime_dir(), poll_id)
    except PollError as exc:
        return _poll_error(exc)
    audit_log("poll.cleaned", "poll technical data cleaned", category="poll", poll_id=poll_id, **result)
    return jsonify({"ok": True, **result})


@bp.post("/notifications/polls/<poll_id>/anonymize")
@login_required
def poll_anonymize(poll_id: str):
    try:
        anonymize_poll(_runtime_dir(), poll_id)
    except PollError as exc:
        return _poll_error(exc)
    audit_log("poll.anonymized", "poll identity removed", category="poll", poll_id=poll_id)
    return jsonify({"ok": True})


@bp.post("/notifications/polls/<poll_id>/reset")
@login_required
def poll_reset(poll_id: str):
    try:
        reset_poll(_runtime_dir(), poll_id)
    except PollError as exc:
        return _poll_error(exc)
    audit_log("poll.reset", "poll responses and invitations reset", category="poll", poll_id=poll_id)
    return jsonify({"ok": True})


@bp.post("/notifications/polls/<poll_id>/delete")
@login_required
def poll_delete(poll_id: str):
    try:
        delete_poll(_runtime_dir(), poll_id)
    except PollError as exc:
        return _poll_error(exc)
    audit_log("poll.deleted", "poll deleted", category="poll", poll_id=poll_id)
    return jsonify({"ok": True, "redirect": url_for("dashboard.polls")})

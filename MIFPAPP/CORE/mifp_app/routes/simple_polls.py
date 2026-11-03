"""Shared-link polls: Aruba sends the invitation; MIFP only records answers."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import deque

from flask import Response, abort, current_app, flash, make_response, redirect, render_template, request, session, url_for

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..services import simple_polls as store
from ..services import poll_yaml
from ..utils.security import admin_password_matches, get_client_ip
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import bp as dashboard_bp
from .public import bp as public_bp


# Volatile, per-web-worker anti-abuse throttle. IPs are not written to
# the survey folder; university NATs are allowed generous legitimate bursts.
_survey_rate_lock = threading.Lock()
_survey_rate: dict[str, deque[float]] = {}


def _submission_rate_allowed() -> bool:
    now = time.monotonic()
    ip = get_client_ip()
    with _survey_rate_lock:
        if len(_survey_rate) > 10000:
            _survey_rate.clear()  # bounded memory, never persist client IPs
        seen = _survey_rate.setdefault(ip, deque())
        while seen and seen[0] <= now - 600:
            seen.popleft()
        if len(seen) >= 10000:
            return False
        seen.append(now)
        return True


def _base():
    return current_app.config["RUNTIME_CONFIG_DIR"]


def _questions_from_form():
    values = []
    for index in range(30):
        label = request.form.get(f"q_{index}_question")
        if label is None:
            continue
        options = request.form.get(f"q_{index}_options", "").splitlines()
        values.append({"question": label, "type": request.form.get(f"q_{index}_type"),
                       "required": bool(request.form.get(f"q_{index}_required")),
                       "options": [x.strip() for x in options if x.strip()],
                       "dates": request.form.get(f"q_{index}_dates", "").splitlines(),
                       "style": request.form.get(f"q_{index}_style", "single_line"),
                       "yes_label": request.form.get(f"q_{index}_yes_label", "Yes"),
                       "no_label": request.form.get(f"q_{index}_no_label", "No")})
    return values


@dashboard_bp.get("/polls-simple")
@login_required
def simple_polls():
    return render_template("dashboard/polls_simple.html", polls=store.list_all(_base()), poll=None, analysis=None)


@dashboard_bp.post("/polls-simple/create")
@login_required
def simple_poll_create():
    try:
        template = request.form.get("template", "blank")
        if template == "yaml":
            return simple_poll_import_yaml()
        if template.startswith("poll:"):
            definition = poll_yaml.load_template(poll_yaml.export_template(_base(), template[5:]))
            poll = poll_yaml.create_draft(_base(), definition)
        else:
            poll = store.create(_base(), template)
        audit_log("poll.simple.created", "shared-link poll created", category="poll", poll_id=poll["id"])
        return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll["id"]))
    except store.SurveyError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard.simple_polls"))


def _yaml_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="mifp-poll-yaml-import-v1")


def _import_session_binding():
    return hashlib.sha256(session["_csrf_token"].encode()).hexdigest()


@dashboard_bp.post("/polls-simple/import-yaml")
@login_required
def simple_poll_import_yaml():
    try:
        upload = request.files.get("yaml_file")
        if not upload or not upload.filename or not upload.filename.lower().endswith((".yaml", ".yml")):
            raise store.SurveyError("Choose a .yaml or .yml file")
        template = poll_yaml.load_template(upload.stream.read(poll_yaml.MAX_YAML_BYTES + 1))
        confirmation = _yaml_signer().dumps({"template": template, "session": _import_session_binding()})
        return render_template("dashboard/polls_yaml_import.html", template=template["poll"], confirmation=confirmation)
    except store.SurveyError as exc:
        return render_template("dashboard/polls_yaml_import.html", template=None, confirmation=None, import_error=str(exc)), 400


@dashboard_bp.post("/polls-simple/import-yaml/confirm")
@login_required
def simple_poll_import_confirm():
    try:
        token = request.form.get("confirmation", "")
        if len(token) > poll_yaml.MAX_YAML_BYTES * 2:
            raise store.SurveyError("Invalid import confirmation")
        pending = _yaml_signer().loads(token, max_age=1800)
        if not secrets.compare_digest(pending["session"], _import_session_binding()):
            raise store.SurveyError("Import preview belongs to another administrator session")
        if request.form.get("confirm_import") != "yes":
            raise store.SurveyError("Confirm creation of a new draft after reviewing the preview")
        poll = poll_yaml.create_draft(_base(), pending["template"])
    except (BadSignature, store.SurveyError) as exc:
        message = str(exc) if isinstance(exc, store.SurveyError) else "Import preview expired or invalid; upload the YAML again."
        return render_template("dashboard/polls_yaml_import.html", template=None, confirmation=None, import_error=message), 400
    audit_log("poll.simple.imported", "new draft created from YAML", category="poll", poll_id=poll["id"])
    flash("YAML imported as a new draft. Review and confirm privacy before publishing.", "success")
    return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll["id"]))


@dashboard_bp.get("/polls-simple/<poll_id>/export.yaml")
@login_required
def simple_poll_export_yaml(poll_id):
    try:
        return Response(poll_yaml.export_template(_base(), poll_id), mimetype="application/yaml", headers={
            "Content-Disposition": 'attachment; filename="mifp-poll-template.yaml"', "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"})
    except store.SurveyError:
        abort(404)


@dashboard_bp.get("/polls-simple/<poll_id>")
@login_required
def simple_poll_detail(poll_id):
    try:
        poll = store.get(_base(), poll_id)
        analysis = store.summary(_base(), poll_id)
        return render_template("dashboard/polls_simple.html", polls=store.list_all(_base()), poll=poll, analysis=analysis)
    except store.SurveyError:
        abort(404)


def _editor_data():
    try:
        saved = store.get(_base(), request.view_args["poll_id"])
    except store.SurveyError:
        abort(404)
    privacy = {key: request.form.get(f"privacy_{key}", saved["privacy"].get(key, "")) for key in store.PRIVACY_LIMITS}
    if request.form.get("action") == "adopt_privacy":
        privacy = store.adopt_previous_privacy(_base(), saved, privacy)
    data = {key: request.form.get(key, "") for key in ("title", "description", "response_mode", *store.SCHEDULE_FIELDS)}
    data.update(questions=_questions_from_form(), privacy=privacy,
                status="closed" if request.form.get("action") == "close" else saved["status"])
    return data


def _publication_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="mifp-poll-publication-v1")


def _poll_fingerprint(poll):
    return hashlib.sha256(json.dumps(poll, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dashboard_bp.post("/polls-simple/<poll_id>/publish")
@login_required
def simple_poll_publish(poll_id):
    try:
        pending = _publication_signer().loads(request.form.get("review", ""), max_age=1800)
        if pending["poll_id"] != poll_id or not secrets.compare_digest(pending["session"], _import_session_binding()):
            raise store.SurveyError("Publication review belongs to another poll or administrator session")
        saved = store.get(_base(), poll_id)
        if saved["status"] != "draft" or pending["fingerprint"] != _poll_fingerprint(saved):
            raise store.SurveyError("The draft or privacy notice changed. Preview and review it again")
        store.update(_base(), poll_id, pending["data"], expected_revision=pending["revision"])
    except (BadSignature, store.SurveyError, KeyError, TypeError) as exc:
        error = str(exc) if isinstance(exc, store.SurveyError) else "Publication review expired or invalid. Preview and review it again"
        return render_template("dashboard/polls_publish_review.html", poll=None, review_error=error), 400
    audit_log("poll.simple.published", "reviewed notice published", category="poll", poll_id=poll_id)
    flash("Survey published. Your public link is ready.", "success")
    return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll_id))


@dashboard_bp.post("/polls-simple/<poll_id>/preview")
@login_required
def simple_poll_preview(poll_id):
    try:
        data = _editor_data()
        poll = store.update(_base(), poll_id, data, validate_only=True)
    except store.SurveyError:
        abort(400)
    return _no_cache(make_response(render_template("public/survey.html", poll=poll,
                                                   preview=True, success=False, submission_id="")))


@dashboard_bp.post("/polls-simple/<poll_id>/save")
@login_required
def simple_poll_save(poll_id):
    publishing = request.form.get("action") == "publish"
    draft_saved = False
    data = None
    try:
        data = _editor_data()
        saved = store.get(_base(), poll_id)
        if publishing and saved["status"] != "draft":
            raise store.SurveyError("Only drafts can be published")
        updated = store.update(_base(), poll_id, data)
        draft_saved = updated["status"] == "draft"
        if publishing:
            data.update(status="open", notice_reviewed=True)
            saved = store.get(_base(), poll_id)
            candidate = store.update(_base(), poll_id, data, validate_only=True)
            review = _publication_signer().dumps({"poll_id": poll_id, "data": data,
                "session": _import_session_binding(), "fingerprint": _poll_fingerprint(saved),
                "revision": saved.get("updated_at", saved["created_at"])})
            return render_template("dashboard/polls_publish_review.html", poll=candidate, review=review)
    except (store.SurveyError, OSError) as exc:
        try:
            saved = store.get(_base(), poll_id)
        except store.SurveyError:
            abort(404)
        # Preserve all posted legal facts on validation failure, never in cookies.
        if data is None:
            data = {key: request.form.get(key, saved.get(key, "")) for key in ("title", "description", "response_mode", *store.SCHEDULE_FIELDS)}
            data.update(questions=_questions_from_form(),privacy={key:request.form.get(f"privacy_{key}",saved["privacy"].get(key,"")) for key in store.PRIVACY_LIMITS})
        questions = []
        for index, question in enumerate(data["questions"]):
            old = saved["questions"][index] if index < len(saved["questions"]) else {}
            q = {**question, "id": old.get("id", secrets.token_hex(8))}
            if q["type"] in {"single_choice", "multiple_choice"}:
                q["options"] = [{"id": secrets.token_hex(8), "label": label} for label in q["options"]]
            questions.append(q)
        draft_view = {**saved, **data, "questions": questions, "status": saved["status"]}
        message = str(exc) if isinstance(exc, store.SurveyError) else "Unable to save to private storage"
        return render_template("dashboard/polls_simple.html", polls=store.list_all(_base()),
            poll=draft_view, analysis=store.summary(_base(), poll_id),
            save_error=None if draft_saved else message,
            publish_error=message if draft_saved else None, field_errors=message), 400
    audit_log("poll.simple.updated", "shared-link poll updated", category="poll", poll_id=poll_id,
              status=updated["status"], mode=updated["response_mode"])
    flash("Survey saved.", "success")
    return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll_id))


@dashboard_bp.get("/polls-simple/<poll_id>/export.csv")
@login_required
def simple_poll_export(poll_id):
    try:
        return Response(store.export_csv(_base(), poll_id), mimetype="text/csv; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="mifp-survey.csv"', "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"})
    except store.SurveyError:
        abort(404)


@dashboard_bp.get("/polls-simple/<poll_id>/export.<fmt>")
@login_required
def simple_poll_report_export(poll_id, fmt):
    from ..services import simple_poll_exports
    if fmt not in {"xlsx", "pdf"}:
        abort(404)
    try:
        payload = getattr(simple_poll_exports, "export_" + fmt)(_base(), poll_id)
    except store.SurveyError:
        abort(404)
    mimetype = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(payload, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="mifp-poll-report.{fmt}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@dashboard_bp.post("/polls-simple/<poll_id>/delete")
@login_required
def simple_poll_delete(poll_id):
    if not admin_password_matches(request.form.get("admin_password", "")):
        flash("Administrator password incorrect; no data deleted.", "error")
        return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll_id))
    try:
        store.delete(_base(), poll_id)
        audit_log("poll.simple.deleted", "shared-link poll deleted", category="poll", poll_id=poll_id)
        flash("Survey and responses deleted.", "success")
    except store.SurveyError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard.simple_polls"))


def _no_cache(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _unavailable():
    return _no_cache(make_response(render_template("public/survey.html", poll=None,
                                                   unavailable=True, success=False), 404))


@public_bp.get("/survey/<poll_id>/<key>")
def simple_survey_page(poll_id, key):
    try:
        poll = store.public(_base(), poll_id, key)
    except store.SurveyError:
        return _unavailable()
    return _no_cache(make_response(render_template("public/survey.html", poll=poll, success=False,
                                                   submission_id=secrets.token_hex(16))))


@public_bp.post("/survey/<poll_id>/<key>")
def simple_survey_submit(poll_id, key):
    if not _submission_rate_allowed():
        response = _no_cache(make_response("Too many submissions from this network; try again in 10 minutes.", 429))
        response.headers["Retry-After"] = "600"
        return response
    try:
        poll = store.public(_base(), poll_id, key)
    except store.SurveyError:
        return _unavailable()
    answers = {}
    for question in poll["questions"]:
        field = "q_" + question["id"]
        if question["type"] == "date_availability":
            answers[question["id"]] = {
                name[len(field) + 1:]: request.form.get(name) if len(request.form.getlist(name)) == 1 else request.form.getlist(name)
                for name in request.form if name.startswith(field + "_")}
        else:
            answers[question["id"]] = request.form.getlist(field) if question["type"] == "multiple_choice" else request.form.get(field, "")
    identity = {name: request.form.get(name, "") for name in ("first_name", "last_name", "email")}
    try:
        store.submit(_base(), poll_id, key, request_id=request.form.get("submission_id", ""),
                     answers=answers, identity=identity, consent=request.form.get("processing_consent") == "yes")
        return _no_cache(make_response(render_template("public/survey.html", poll=poll, success=True,
                                                       submission_id="")))
    except store.SurveyError as exc:
        # Error re-render keeps submitted answers; no visitor identity in logs.
        return _no_cache(make_response(render_template("public/survey.html", poll=poll, success=False,
                                                       submission_id=request.form.get("submission_id", ""),
                                                       error=str(exc), values=request.form), 400))


@dashboard_bp.post("/polls-simple/<poll_id>/erase-email")
@login_required
def simple_poll_erase_email(poll_id):
    if not admin_password_matches(request.form.get("admin_password", "")):
        flash("Administrator password incorrect; no data deleted.", "error")
        return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll_id))
    try:
        removed = store.erase_identified_email(_base(), poll_id, request.form.get("email", ""))
        audit_log("poll.simple.identity_erased", "identified survey response erased", category="poll", poll_id=poll_id, deleted=removed)
        flash(f"Removed {removed} response(s). Check backups separately.", "success")
    except store.SurveyError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard.simple_poll_detail", poll_id=poll_id))


@public_bp.get("/respond")
@public_bp.get("/respond/poll")
@public_bp.post("/respond/exchange")
@public_bp.post("/respond/submit")
def respond():
    response = make_response("This personal invitation is retired. Ask the organiser for the current shared survey link.", 410)
    response.headers.update({"Cache-Control": "no-store, max-age=0", "Referrer-Policy": "no-referrer",
                             "X-Robots-Tag": "noindex, nofollow"})
    return response

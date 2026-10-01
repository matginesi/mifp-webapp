from __future__ import annotations

from flask import current_app, jsonify, make_response, render_template, request, session

from ..services.polls import PollError, PollSessionExpired, exchange_token, respondent_poll, submit_response
from ..utils.logger import get_logger, log_event
from ..utils.security import get_client_ip, ip_rate_allowed
from .public import bp

log = get_logger("polls")


def _runtime_dir():
    return current_app.config["RUNTIME_CONFIG_DIR"]


def _private_response(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.get("/respond")
def respond():
    return _private_response(make_response(render_template("public/respond.html")))


@bp.post("/respond/exchange")
def respond_exchange():
    request.max_content_length = 4096
    if not ip_rate_allowed(
        "poll_token_exchange",
        get_client_ip(),
        limit=30,
        window_seconds=10 * 60,
        db_path=str(current_app.config["DATABASE_PATH"]),
    ):
        return _private_response(jsonify({"error": "Too many attempts. Try again later."})), 429
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _private_response(jsonify({"error": "Invalid invitation request"})), 400
    try:
        access = exchange_token(_runtime_dir(), str(payload.get("poll_id") or ""), str(payload.get("token") or ""))
    except PollError as exc:
        return _private_response(jsonify({"error": str(exc)})), 403
    session["poll_access"] = access
    return _private_response(jsonify({"ok": True}))


@bp.get("/respond/poll")
def respond_poll_data():
    access = session.get("poll_access")
    if not isinstance(access, dict):
        return _private_response(jsonify({"error": "Open the personal invitation link to continue."})), 403
    try:
        poll = respondent_poll(_runtime_dir(), access)
    except PollError as exc:
        session.pop("poll_access", None)
        return _private_response(jsonify({"error": str(exc)})), 403
    public_poll = {
        key: poll[key]
        for key in ("id", "title", "description", "deadline", "allow_changes", "questions", "current_answers", "locked")
    }
    return _private_response(jsonify({"poll": public_poll}))


@bp.post("/respond/submit")
def respond_submit():
    request.max_content_length = 512 * 1024
    access = session.get("poll_access")
    payload = request.get_json(silent=True)
    if not isinstance(access, dict) or not isinstance(payload, dict):
        return _private_response(jsonify({"error": "Poll session is unavailable"})), 403
    if not ip_rate_allowed(
        "poll_response_submit",
        str(access.get("invitation_id") or get_client_ip()),
        limit=20,
        window_seconds=60 * 60,
        db_path=str(current_app.config["DATABASE_PATH"]),
    ):
        return _private_response(jsonify({"error": "Too many submissions. Try again later."})), 429
    try:
        record = submit_response(_runtime_dir(), access, payload.get("answers"))
    except PollSessionExpired as exc:
        session.pop("poll_access", None)
        return _private_response(jsonify({"error": str(exc)})), 403
    except PollError as exc:
        return _private_response(jsonify({"error": str(exc)})), 400
    log_event(
        log,
        "poll.response_submitted",
        "poll response submitted",
        poll_id=str(access.get("poll_id") or ""),
        revision=record["revision"],
    )
    return _private_response(
        jsonify({"ok": True, "submitted_at": record["submitted_at"], "revision": record["revision"]})
    )

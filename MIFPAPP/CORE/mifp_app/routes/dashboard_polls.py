"""Read-only access to pre-shared-link polls; no invitation or mutation flow.

Keep existing JSON/JSONL data accessible for administrator review/export.
New questionnaires use the restored visual editor in simple_polls.
"""
from __future__ import annotations

import re

from flask import Response, abort, current_app, jsonify, redirect, render_template, request, url_for

from ..services.poll_archive import (
    PollError, export_csv, export_pdf, export_xlsx, get_poll, list_polls,
    poll_analysis, response_history, response_view,
)
from .auth import login_required
from .dashboard import bp


def _runtime_dir():
    return current_app.config["RUNTIME_CONFIG_DIR"]


@bp.get("/notifications/polls")
@login_required
def polls():
    if request.args.get("archive") != "1":
        return redirect(url_for("dashboard.simple_polls"))
    return render_template("dashboard/polls_archive.html", polls=list_polls(_runtime_dir()), poll=None)


@bp.get("/notifications/polls/<poll_id>")
@login_required
def poll_detail(poll_id):
    try:
        poll = get_poll(_runtime_dir(), poll_id)
        return render_template("dashboard/polls_archive.html", polls=list_polls(_runtime_dir()), poll=poll)
    except PollError:
        abort(404)


@bp.get("/notifications/polls/<poll_id>/responses")
@login_required
def poll_responses(poll_id):
    try:
        return jsonify({"responses": response_view(_runtime_dir(), poll_id)})
    except PollError:
        abort(404)


@bp.get("/notifications/polls/<poll_id>/responses/<response_id>/history")
@login_required
def poll_response_history(poll_id, response_id):
    try:
        return jsonify(response_history(_runtime_dir(), poll_id, response_id))
    except PollError:
        abort(404)


@bp.get("/notifications/polls/<poll_id>/analysis")
@login_required
def poll_analysis_data(poll_id):
    try:
        return jsonify(poll_analysis(_runtime_dir(), poll_id))
    except PollError:
        abort(404)


@bp.get("/notifications/polls/<poll_id>/export.<fmt>")
@login_required
def poll_export(poll_id, fmt):
    try:
        poll = get_poll(_runtime_dir(), poll_id)
        if fmt == "csv":
            payload, mimetype = export_csv(_runtime_dir(), poll_id), "text/csv; charset=utf-8"
        elif fmt == "xlsx":
            payload, mimetype = export_xlsx(_runtime_dir(), poll_id), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "pdf":
            payload, mimetype = export_pdf(_runtime_dir(), poll_id), "application/pdf"
        else:
            abort(404)
    except PollError:
        abort(404)
    filename = re.sub(r"[^a-z0-9]+", "-", poll["title"].lower()).strip("-")[:60] or "poll"
    return Response(payload, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{filename}.{fmt}"',
        "Cache-Control": "no-store, max-age=0", "X-Content-Type-Options": "nosniff",
    })


@bp.post("/notifications/polls/new")
@bp.post("/notifications/polls/<poll_id>")
@bp.post("/notifications/polls/<poll_id>/<operation>")
@login_required
def poll_retired(poll_id=None, operation=None):
    return jsonify({"error": "Legacy polls are read only. Use the shared-link Polls editor."}), 410

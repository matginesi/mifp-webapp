from __future__ import annotations

import re
from datetime import datetime

from flask import Response, current_app, flash, redirect, render_template, request, url_for

from ..services.dashboard_repository import delete_old_logs, search_logs_paginated
from ..services.operation_maintenance import maintenance_guarded
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import bp


def _base_log_name(filename: str) -> str:
    return re.sub(r"\.\d+$", "", filename)


def _log_file_options(log_dir):
    groups: dict[str, dict] = {}
    for pattern in ("*.log*", "*.jsonl*"):
        for path in log_dir.glob(pattern):
            if not path.is_file():
                continue
            name = _base_log_name(path.name)
            stat = path.stat()
            row = groups.setdefault(name, {"name": name, "files": 0, "bytes": 0, "mtime": 0.0})
            row["files"] += 1
            row["bytes"] += stat.st_size
            row["mtime"] = max(row["mtime"], stat.st_mtime)
    options = sorted(groups.values(), key=lambda r: (r["name"] not in {"mifp_app.log", "mifp_app.jsonl"}, r["name"]))
    for row in options:
        row["size_mb"] = round(row["bytes"] / (1024 * 1024), 2)
        row["updated"] = datetime.fromtimestamp(row["mtime"]).strftime("%Y-%m-%d %H:%M") if row["mtime"] else ""
    return options


@bp.get("/logs")
@login_required
def logs():
    q = request.args.get("q", "").strip() or None
    raw_level = request.args.get("level", "")
    show_all = request.args.get("show_all") == "1"
    # Default to INFO on first visit (no filters at all), respect explicit level otherwise
    if not raw_level and not q and not request.args.get("file") and not show_all:
        level = "INFO"
    elif raw_level:
        level = raw_level
    else:
        level = "ALL"
    log_file = request.args.get("file", "").strip() or None
    event = request.args.get("event", "").strip() or None
    request_id = request.args.get("request_id", "").strip()[:64] or None
    since = request.args.get("since", "").strip()[:16] or None
    until = request.args.get("until", "").strip()[:16] or None
    page = request.args.get("page", 1, type=int)
    refresh = request.args.get("refresh", type=int) or 0
    result = search_logs_paginated(
        current_app.config["LOG_DIR"], q=q, level=level, log_file=log_file,
        event=event, request_id=request_id, since=since, until=until,
        page=page, per_page=50,
    )
    log_dir = current_app.config["LOG_DIR"]
    log_files = _log_file_options(log_dir)
    available_files = [row["name"] for row in log_files]
    selected_file = next((row for row in log_files if row["name"] == log_file), None)
    return render_template(
        "dashboard/logs.html", q=q, level=level, event=event,
        request_id=request_id, since=since, until=until, log_file=log_file,
        log_files=log_files, selected_file=selected_file,
        available_files=available_files, refresh=refresh, show_all=show_all,
        **result,
    )


@bp.get("/logs/export/<fmt>")
@login_required
def logs_export(fmt: str):
    if fmt not in {"csv", "json", "txt"}:
        return Response("Invalid export format", status=400)
    q = request.args.get("q", "").strip() or None
    level = request.args.get("level", "ALL")
    log_file = request.args.get("file", "").strip() or None
    event = request.args.get("event", "").strip() or None
    request_id = request.args.get("request_id", "").strip()[:64] or None
    since = request.args.get("since", "").strip()[:16] or None
    until = request.args.get("until", "").strip()[:16] or None
    log_dir = current_app.config["LOG_DIR"]
    result = search_logs_paginated(
        log_dir, q=q, level=level, log_file=log_file, event=event,
        request_id=request_id, since=since, until=until,
        page=1, per_page=10000,
    )
    rows = result["rows"]
    export_stem = (log_file or "mifp_logs").replace(".", "_")

    if fmt == "json":
        import json
        return Response(
            json.dumps(rows, ensure_ascii=False, default=str, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={export_stem}.json"},
        )

    if fmt == "txt":
        lines = []
        for row in rows:
            parts = [
                str(row.get("when") or ""),
                str(row.get("level") or ""),
                str(row.get("logger") or ""),
                str(row.get("message") or ""),
            ]
            lines.append(" | ".join(parts).strip())
        output = "\n".join(lines)
        if output:
            output += "\n"
        return Response(
            output,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={export_stem}.txt"},
        )

    import csv
    import io
    si = io.StringIO()
    fieldnames = ["when", "level", "stream", "event", "logger", "file", "location", "message", "request_id"]
    writer = csv.DictWriter(si, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (r.get(k) or "") for k in fieldnames})
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={export_stem}.csv"},
    )


@bp.post("/logs/cleanup")
@login_required
@maintenance_guarded("log cleanup")
def logs_cleanup():
    days = max(1, min(request.form.get("days", 30, type=int) or 30, 3650))
    log_dir = current_app.config["LOG_DIR"].resolve()
    try:
        deleted = delete_old_logs(log_dir, days=days)
        audit_log("log.cleanup", f"log cleanup: deleted {deleted} files older than {days} days")
        flash(f"Deleted {deleted} log file(s) older than {days} days.", "success")
    except OSError:
        flash("Log cleanup failed. Check the application error log.", "error")
    return redirect(url_for("dashboard.logs"))

from __future__ import annotations

import csv
import io
from pathlib import Path

from flask import Response, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for

from ..db.connection import connect
from ..services.exporters import rows_to_xlsx
from ..services.registrations import RegistrationAdmin, STATUSES, event_key, filtered, notification_state, private_file, read_records, spreadsheet_safe
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import bp


def _context(site_id):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = conn.execute("SELECT * FROM conference_sites WHERE id=?", (site_id,)).fetchone()
        other_sites = conn.execute("SELECT id,public_path,slug FROM conference_sites WHERE id<>?", (site_id,)).fetchall()
    if site is None:
        abort(404)
    site = dict(site)
    configured = current_app.config.get("REGISTRATIONS_PRIVATE_ROOT", "")
    if not configured:
        abort(503, description="Registration storage is not connected. Ask the host operator to enable read-only access.")
    root = Path(configured)
    admin = RegistrationAdmin(Path(current_app.config["CONFERENCES_DIR"]) / ".registrations-admin")
    try:
        event = event_key(site["public_path"] or site["slug"])
        if any(event_key(other["public_path"] or other["slug"]) == event for other in other_sites):
            abort(409, description="Multiple conference sites map to this private storage key. Host review is required.")
        rows = admin.enrich(event, read_records(root, event))
    except (ValueError, OSError):
        abort(503, description="Registration storage is unavailable or invalid; no data was changed.")
    return site, root, event, admin, rows


def _record(rows, receipt):
    return next((row for row in rows if row["receipt_id"] == receipt), None) or abort(404)


@bp.get("/conference-sites/<int:site_id>/registrations")
@login_required
def registrations(site_id):
    site, root, event, admin, rows = _context(site_id)
    matches = filtered(rows, request.args)
    page = max(1, request.args.get("page", 1, type=int))
    page_size = 25
    pages = max(1, (len(matches) + page_size - 1) // page_size)
    page = min(page, pages)
    selected = matches[(page - 1) * page_size:page * page_size]
    for row in selected:
        row["notifications"] = notification_state(root, event, row["receipt_id"])
    return render_template("dashboard/registrations.html", site=site, rows=selected,
                           total=len(rows), count=len(matches), page=page, pages=pages,
                           statuses=STATUSES, types=sorted({r.get('registration_type', '') for r in rows}),
                           payments=sorted({r.get('payment_method', '') for r in rows}),
                           counters={status: sum(r['admin_status'] == status for r in rows) for status in STATUSES})


@bp.route("/conference-sites/<int:site_id>/registrations/<receipt>", methods=["GET", "POST"])
@login_required
def registration_detail(site_id, receipt):
    site, root, event, admin, rows = _context(site_id)
    row = _record(rows, receipt)
    if request.method == "POST":
        try:
            admin.update(event, receipt, request.form.get("status", ""), request.form.get("note", ""),
                         request.form.get("archived") == "1", session.get("admin_username", "operator"))
        except ValueError as exc:
            abort(400, description=str(exc))
        audit_log("registrations.metadata", "Registration metadata updated", category="conference", site_id=site_id)
        return redirect(url_for("dashboard.registration_detail", site_id=site_id, receipt=receipt))
    return render_template("dashboard/registration_detail.html", site=site, row=row, statuses=STATUSES,
                           history=admin.history(event, receipt), notifications=notification_state(root, event, receipt))


@bp.get("/conference-sites/<int:site_id>/registrations-export/<fmt>")
@login_required
def registrations_export(site_id, fmt):
    if fmt not in {"csv", "xlsx"}:
        abort(404)
    site, root, event, admin, rows = _context(site_id)
    fields = [key for key in (rows[0] if rows else {"receipt_id": ""})]
    records = [{key: spreadsheet_safe(row.get(key, "")) for key in fields} for row in filtered(rows, request.args)]
    if fmt == "xlsx":
        payload = rows_to_xlsx(records, "Registrations")
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
        payload = stream.getvalue().encode("utf-8-sig")
        mime = "text/csv; charset=utf-8"
    audit_log("registrations.export", "Registration export", category="conference", site_id=site_id, count=len(records), format=fmt)
    return Response(payload, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="registrations.{fmt}"', "Cache-Control": "no-store"})


@bp.get("/conference-sites/<int:site_id>/registrations/<receipt>/proof")
@login_required
def registration_proof(site_id, receipt):
    site, root, event, admin, rows = _context(site_id)
    row = _record(rows, receipt)
    filename = row.get("proof_file", "")
    # Only a filename recorded by PHP, tied to this receipt. No client filenames.
    if filename.rsplit(".", 1)[-1].lower() not in {"pdf", "jpg", "jpeg", "png"} or filename != "proof-" + receipt + "." + filename.rsplit(".", 1)[-1]:
        abort(404)
    try:
        with private_file(root, "uploads", event, filename) as stream:
            payload = stream.read(10 * 1024 * 1024 + 1)
        if len(payload) > 10 * 1024 * 1024:
            abort(413)
    except (ValueError, OSError):
        abort(404)
    audit_log("registrations.proof", "Payment proof downloaded", category="conference", site_id=site_id)
    response = send_file(io.BytesIO(payload), mimetype="application/octet-stream", as_attachment=True, download_name=filename)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/conference-sites/<int:site_id>/registrations-request")
@login_required
def registrations_request(site_id):
    site, root, event, admin, rows = _context(site_id)
    if request.form.get("confirm") != event:
        abort(400, description="Type the exact conference storage key to confirm.")
    action = request.form.get("action", "")
    receipts = request.form.getlist("receipt")
    if request.form.get("all") == "1" and action == "delete":
        receipts = [row['receipt_id'] for row in rows]
    known = {row['receipt_id'] for row in rows}
    if any(receipt not in known for receipt in receipts):
        abort(400)
    channel = request.form.get("channel", "")
    if action == "retry" and any(notification_state(root, event, receipt).get(channel) not in {"failed", "pending"} for receipt in receipts):
        abort(409, description="Retry requires a known failed or pending notification; accepted, sending and unknown outcomes cannot be resent automatically.")
    try:
        request_id = admin.queue(event, receipts, action, session.get('admin_username', 'operator'), channel)
    except ValueError as exc:
        abort(400, description=str(exc))
    audit_log("registrations.request", "Host registration operation requested", category="conference", site_id=site_id, operation=action, count=len(receipts), request_id=request_id)
    flash(f"Request {request_id} queued. A host operator must review and apply it explicitly; no submission was deleted or email sent.", "info")
    return redirect(url_for("dashboard.registrations", site_id=site_id))

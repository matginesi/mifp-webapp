from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from flask import Response, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for

from ..db.connection import connect
from ..services.exporters import rows_to_xlsx
from ..services.registration_exports import registration_document, payment_proofs
from ..services.registration_transfer import recovery_package, read_import, import_records, MAX_PACKAGE_BYTES
from ..services.registration_deletion import delete_registrations, DeletionRecoveryRequired
from ..services.notifications import notify, smtp_status, NotificationResult
from ..utils.security import admin_password_matches, get_client_ip, ip_rate_allowed
from ..services.registrations import RegistrationAdmin, STATUSES, event_key, filtered, notification_state, private_file, read_records, spreadsheet_safe, registration_summary, mark_potential_duplicates
from ..utils.logger import audit_log
from ..services.registrations import prepare_local_archive
from .auth import login_required
from .dashboard import bp


def _storage_error(site, reason):
    # Stable diagnostics without exception text, paths, CSV contents or PII.
    informational = (reason == "not_configured" and request.endpoint == "dashboard.registrations"
                     and request.method == "GET")
    status = 200 if informational else 503
    audit_log("registrations.storage_unavailable", "Registration storage unavailable",
              category="conference", outcome="unconfigured" if informational else "failure",
              site_id=site["id"], reason=reason)
    response = Response(render_template("dashboard/registrations_unavailable.html", site=site,
                                       reason=reason, status=status), status=status)
    if reason == "busy":
        response.headers["Retry-After"] = "5"
    abort(response)


def _context(site_id):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = conn.execute("SELECT * FROM conference_sites WHERE id=?", (site_id,)).fetchone()
        other_sites = conn.execute("SELECT id,public_path,slug FROM conference_sites WHERE id<>?", (site_id,)).fetchall()
    if site is None:
        abort(404)
    site = dict(site)
    configured = current_app.config.get("REGISTRATIONS_PRIVATE_ROOT", "")
    if not configured and current_app.config.get('ENV') != 'development':
        _storage_error(site, "not_configured")
    try:
        root = Path(configured) if configured else prepare_local_archive(Path(current_app.config['CONFERENCES_DIR']))
        event = event_key(site["public_path"] or site["slug"])
        if any(event_key(other["public_path"] or other["slug"]) == event for other in other_sites):
            abort(409, description="Multiple conference sites map to this private storage key. Host review is required.")
        records = read_records(root, event)
        admin = RegistrationAdmin(Path(current_app.config["CONFERENCES_DIR"]) / ".registrations-admin")
        rows = mark_potential_duplicates(admin.enrich(event, records))
    except PermissionError:
        _storage_error(site, "permission_denied")
    except BlockingIOError:
        _storage_error(site, "busy")
    except FileNotFoundError:
        _storage_error(site, "not_connected")
    except ValueError:
        _storage_error(site, "invalid_data")
    except (OSError, sqlite3.Error):
        _storage_error(site, "storage_error")
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
                           countries=sorted({r.get('country', '') for r in rows if r.get('country')}),
                           affiliations=sorted({r.get('affiliation', '') for r in rows if r.get('affiliation')}),
                           summary=registration_summary(matches),
                           local_archive=not bool(current_app.config.get('REGISTRATIONS_PRIVATE_ROOT')),
                           write_enabled=current_app.config.get("REGISTRATIONS_WRITE_ENABLED", False),
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
                           history=admin.history(event, receipt), notifications=notification_state(root, event, receipt),
                           event=event, write_enabled=current_app.config.get("REGISTRATIONS_WRITE_ENABLED", False))


def _authorize_registration_action(operation, site_id):
    if not ip_rate_allowed("registrations_sensitive", f"{get_client_ip()}:{session.get('admin_username', '')}",
                           limit=10, window_seconds=300):
        abort(429, description="Too many sensitive operations. Try again in a few minutes.")
    if not admin_password_matches(request.form.get("password", "")):
        audit_log("registrations.authorization_denied", "Registration operation password rejected",
                  category="security", outcome="denied", site_id=site_id, operation=operation)
        abort(403, description="Password verification failed. No data was changed or exported.")


@bp.post("/conference-sites/<int:site_id>/registrations-export/<fmt>")
@login_required
def registrations_export(site_id, fmt):
    if fmt not in {"csv", "xlsx", "docx", "pdf", "zip"}:
        abort(404)
    _authorize_registration_action("export", site_id)
    site, root, event, admin, rows = _context(site_id)
    selected = filtered(rows, request.args)
    if len(selected) > (2000 if fmt in {"docx", "pdf"} else 50000):
        abort(413, description="Too many registrations. Apply narrower filters before exporting.")
    fields = [key for key in (rows[0] if rows else {"receipt_id": ""})]
    records = [{key: spreadsheet_safe(row.get(key, "")) for key in fields} for row in selected]
    try:
        if fmt == "zip":
            payload = recovery_package(root, event, [row["receipt_id"] for row in selected])
            mime = "application/zip"
        elif fmt in {"docx", "pdf"}:
            proofs = payment_proofs(root, event, selected)
            payload = registration_document(selected, "Registrations · " + event, fmt, proofs)
            mime = ("application/pdf" if fmt == "pdf" else
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        elif fmt == "xlsx":
            payload = rows_to_xlsx(records, "Registrations")
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
            payload = stream.getvalue().encode("utf-8-sig")
            mime = "text/csv; charset=utf-8"
    except OverflowError:
        abort(413, description="Export exceeds its limits: 10 MiB/receipt, 64 MiB/100 pages for documents, 96 MiB for recovery ZIP. Apply narrower filters.")
    except (ValueError, OSError):
        audit_log("registrations.export_failed", "Registration document export failed", category="conference",
                  outcome="failure", site_id=site_id, format=fmt)
        abort(503, description="Registration export unavailable. Check access to the private payment proofs.")
    audit_log("registrations.export", "Registration export", category="conference", site_id=site_id, count=len(records), format=fmt)
    return Response(payload, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="registrations-{event}.{fmt}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _deletion_alert(site_id, event, count, operation, stage, *, action="deletion"):
    if smtp_status(current_app)["provider"] != "smtp":
        return NotificationResult("skipped", False, "smtp_required")
    return notify(current_app._get_current_object(), event="registrations." + action + "_" + stage,
                  category="security", severity="security", force=True,
                  subject="[MIFP][SECURITY] Registration " + action + " — " + stage,
                  body=(f"Conference: {event}\nAccount: {session.get('admin_username', 'operator')}\n"
                        f"Operation: {operation}\nRegistrations: {count}\nStatus: {stage}\n"
                        "No participant details or payment proofs are included in this alert."),
                  metadata=dict(site_id=site_id, registration_count=count, operation_id=operation))


@bp.post("/conference-sites/<int:site_id>/registrations-delete")
@login_required
def registrations_delete(site_id):
    from uuid import uuid4
    _authorize_registration_action("delete", site_id)
    if not current_app.config.get('REGISTRATIONS_WRITE_ENABLED', False):
        abort(409, description="Registration deletion requires explicit host write authorization.")
    site, root, event, admin, rows = _context(site_id)
    if request.form.get('confirm') != event:
        abort(400, description="Type the exact conference key to confirm deletion.")
    all_records = request.form.get('all') == '1'
    receipts = sorted(set(request.form.getlist('receipt')))
    known = {row['receipt_id'] for row in rows}
    if not all_records and (not receipts or not set(receipts).issubset(known)):
        abort(400, description="Select existing registrations before deleting.")
    count = len(rows) if all_records else len(receipts)
    if not 0 < count <= 5000:
        abort(400, description="Delete between 1 and 5000 registrations per operation.")
    operation = uuid4().hex
    # Mandatory security alert: no destructive action if transport is unavailable.
    if not _deletion_alert(site_id, event, count, operation, 'authorized').sent:
        abort(503, description="Deletion blocked: the administrator alert could not be accepted by the mail transport. No data was deleted.")
    try:
        result = delete_registrations(root, event, receipts, admin.directory / 'deletion-operations',
                                      all_records=all_records, operation=operation)
    except (ValueError, OSError) as exc:
        reason = ('recovery_required' if isinstance(exc, DeletionRecoveryRequired) else
                  'busy' if isinstance(exc, BlockingIOError) else
                  'permission_denied' if isinstance(exc, PermissionError) else 'operation_failed')
        audit_log('registrations.delete_failed', 'Registration deletion failed', category='conference',
                  outcome='failure', site_id=site_id, operation_id=operation, reason=reason)
        _deletion_alert(site_id, event, count, operation, reason)
        abort(409 if isinstance(exc, ValueError) else 503,
              description="Deletion could not complete (" + reason + "). Check the operation journal and current data before retrying.")
    audit_log('registrations.deleted', 'Registration records and payment proofs deleted', category='conference',
              site_id=site_id, count=result['count'], operation_id=result['operation'])
    alert = _deletion_alert(site_id, event, result['count'], operation, 'completed')
    flash(f"Deleted {result['count']} registrations and their payment proofs. No automatic recovery copy was created.", 'success')
    if not alert.sent:
        flash('Deletion completed; the initial security alert was accepted, but the completion alert failed. Check notification logs.', 'warning')
    return redirect(url_for('dashboard.registrations', site_id=site_id))


@bp.post("/conference-sites/<int:site_id>/registrations-import")
@login_required
def registrations_import(site_id):
    from uuid import uuid4
    _authorize_registration_action("import", site_id)
    if not current_app.config.get('REGISTRATIONS_WRITE_ENABLED', False):
        abort(409, description="Registration import requires explicit host write authorization.")
    site, root, event, admin, rows = _context(site_id)
    if request.form.get('confirm') != event:
        abort(400, description="Type the exact conference key to confirm import.")
    upload = request.files.get('registrations_file')
    if upload is None or not upload.filename:
        abort(400, description="Choose a CSV or a registration recovery ZIP.")
    try:
        package = read_import(upload.read(MAX_PACKAGE_BYTES + 1), upload.filename, event)
    except OverflowError:
        abort(413, description="Registration import exceeds its size limits.")
    except ValueError as exc:
        abort(400, description=str(exc))
    operation = uuid4().hex
    if not _deletion_alert(site_id, event, len(package[1]), operation, 'authorized', action='import').sent:
        abort(503, description="Import blocked: the administrator alert could not be accepted. No data was changed.")
    try:
        result = import_records(root, event, package, admin.directory / 'deletion-operations', operation)
    except (ValueError, OSError, OverflowError) as exc:
        reason = 'recovery_required' if isinstance(exc, DeletionRecoveryRequired) else 'operation_failed'
        audit_log('registrations.import_failed', 'Registration import failed', category='conference',
                  outcome='failure', site_id=site_id, operation_id=operation, reason=reason)
        _deletion_alert(site_id, event, len(package[1]), operation, reason, action='import')
        if isinstance(exc, ValueError):
            abort(409, description=str(exc))
        abort(413 if isinstance(exc, OverflowError) else 503,
              description="Import could not complete. Check the operation journal and current data before retrying.")
    audit_log('registrations.imported', 'Registration import completed', category='conference',
              site_id=site_id, operation_id=operation, **result)
    alert = _deletion_alert(site_id, event, result['added'], operation, 'completed', action='import')
    flash(f"Imported {result['added']} registrations; skipped {result['skipped']} identical records; restored {result['restored_proofs']} receipts. No participant emails were sent.", 'success')
    if not alert.sent:
        flash('Import completed; the completion alert failed. Check notification logs before retrying.', 'warning')
    return redirect(url_for('dashboard.registrations', site_id=site_id))


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
    preview = request.args.get("preview") == "1"
    mime = "application/octet-stream"
    if preview:
        if filename.lower().endswith(".png") and payload.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif filename.lower().endswith((".jpg", ".jpeg")) and payload.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        else:
            abort(404)
    response = send_file(io.BytesIO(payload), mimetype=mime, as_attachment=not preview, download_name=filename)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/conference-sites/<int:site_id>/registrations-request")
@login_required
def registrations_request(site_id):
    _authorize_registration_action("host_request", site_id)
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

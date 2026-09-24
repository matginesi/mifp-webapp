from __future__ import annotations

import json
import queue
import tempfile
from collections.abc import Callable, Generator
from datetime import datetime
from pathlib import Path

from flask import Response, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ..db.connection import connect
from ..services.historical_archive import (
    HistoricalArchiveError,
    build_historical_archive_guide,
    import_historical_archive,
)
from ..services.job_manager import JobQueueFull, get_job_manager
from ..services.operation_maintenance import maintenance_guarded, operation_maintenance
from ..utils.logger import audit_log
from ..utils.security import admin_password_matches, get_client_ip, ip_rate_allowed
from .auth import login_required
from .dashboard import bp


def _archive_rows(conn, q: str | None, category: str | None, year: str | None):
    clauses = ["1=1"]; params: list[object] = []
    if q:
        clauses.append("(e.title LIKE ? OR e.slug LIKE ? OR COALESCE(a.acronym,'') LIKE ?)")
        term = f"%{q}%"; params.extend((term, term, term))
    if category: clauses.append("a.category=?"); params.append(category)
    if year: clauses.append("a.archive_year=?"); params.append(year)
    return [dict(row) for row in conn.execute(
        "SELECT a.*,e.slug,e.title,e.review_status,"
        "(SELECT COUNT(*) FROM asset_links al JOIN assets x ON x.id=al.asset_id WHERE al.entity_type='event' AND al.entity_id=e.id AND (al.role='document' OR x.kind IN ('pdf','document'))) document_count,"
        "(SELECT COUNT(*) FROM asset_links al JOIN assets x ON x.id=al.asset_id WHERE al.entity_type='event' AND al.entity_id=e.id AND x.kind='image') image_count "
        "FROM event_archive_entries a JOIN events e ON e.id=a.event_id WHERE " + " AND ".join(clauses) +
        " ORDER BY a.archive_year DESC,e.title", params).fetchall()]


@bp.get("/archive")
@login_required
def archive_page():
    q = request.args.get("q", "").strip() or None
    category = request.args.get("category", "").strip() or None
    year = request.args.get("year", "").strip() or None
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        rows = _archive_rows(conn, q, category, year)
        stats = conn.execute("SELECT COUNT(*),COUNT(DISTINCT category),MIN(archive_year),MAX(archive_year) FROM event_archive_entries").fetchone()
        categories = [r[0] for r in conn.execute("SELECT DISTINCT category FROM event_archive_entries ORDER BY category")]
        years = [r[0] for r in conn.execute("SELECT DISTINCT archive_year FROM event_archive_entries ORDER BY archive_year DESC")]
        runs = [dict(r) for r in conn.execute("SELECT id,name,status,stats_json,started_at,completed_at,notes FROM import_runs WHERE source_kind='historical-archive' ORDER BY id DESC LIMIT 8")]
    for run in runs:
        try: run["stats"] = json.loads(run.pop("stats_json") or "{}")
        except (TypeError, ValueError): run["stats"] = {}
    manager = get_job_manager(
        current_app.config.get("JOB_MAX_WORKERS", 2), current_app.config.get("JOB_MAX_PENDING", 4),
        db_path=str(current_app.config["DATABASE_PATH"]),
    )
    jobs = [job for job in manager.snapshot()["jobs"] if str(job.get("name") or "").startswith("Historical archive")]
    for job in jobs:
        submitted = job.get("submitted_at")
        if isinstance(submitted, (int, float)):
            job["submitted_at"] = datetime.fromtimestamp(submitted).strftime("%Y-%m-%d %H:%M")
    return render_template("dashboard/archive.html", entries=rows, categories=categories, years=years,
                           q=q, current_category=category, current_year=year, runs=runs,
                           stats={"events": stats[0], "categories": stats[1], "year_min": stats[2], "year_max": stats[3]}, jobs=jobs,
                           import_result=session.pop("archive_import_result", None))


@bp.get("/archive/import-guide.md")
@login_required
def archive_import_guide():
    guide = build_historical_archive_guide()
    audit_log(
        "archive.guide_downloaded",
        "Historical Archive LLM import guide downloaded",
        category="admin",
        outcome="success",
        bytes=len(guide.encode("utf-8")),
    )
    return Response(
        guide,
        mimetype="text/markdown",
        headers={
            "Content-Disposition": 'attachment; filename="MIFP_LLM_HISTORICAL_ARCHIVE_GUIDE.md"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@bp.post("/archive/import")
@login_required
@maintenance_guarded("historical archive upload and import")
def archive_import():
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    password = request.form.get("password", "")
    expected_hash = str(current_app.config.get("ADMIN_PASSWORD_HASH") or "")
    identity = {
        "username": session.get("admin_username"),
        "ip": get_client_ip(),
    }
    if not admin_password_matches(password, expected_hash):
        within_limit = ip_rate_allowed(
            "historical_archive_password_failure",
            f"{get_client_ip()}:{session.get('admin_username') or '-'}",
            limit=5,
            window_seconds=300,
        )
        message = (
            "Too many failed attempts. Try again in a few minutes."
            if not within_limit
            else "Password verification failed. No archive file was processed."
        )
        audit_log(
            "archive.authorization_denied",
            "historical archive import password verification failed",
            category="security",
            outcome="denied",
            rate_limited=not within_limit,
            **identity,
        )
        if is_xhr:
            return jsonify({"ok": False, "message": message}), 429 if not within_limit else 403
        flash(message, "error")
        return redirect(url_for("dashboard.archive_page"))
    audit_log(
        "archive.authorization_success",
        "historical archive import password verified",
        category="security",
        outcome="success",
        **identity,
    )
    upload = request.files.get("archive_zip")
    if not upload or not str(upload.filename or "").lower().endswith(".zip"):
        flash("Choose a historical archive ZIP package.", "error")
        return redirect(url_for("dashboard.archive_page"))
    dry_run = request.form.get("dry_run", "1") == "1"
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="mifp-historical-", suffix=".zip", delete=False) as target:
            staged = Path(target.name)
            upload.save(target)
        if staged.stat().st_size > current_app.config["IMPORT_MAX_ZIP_BYTES"]:
            raise HistoricalArchiveError("Archive exceeds the configured upload limit")
        if is_xhr:
            database_path = str(current_app.config["DATABASE_PATH"])
            assets_dir = Path(current_app.config["ASSETS_DIR"])
            package_name = Path(upload.filename).name
            queued_path = staged
            events: queue.Queue[dict | None] = queue.Queue()
            app = current_app._get_current_object()

            def emit_progress(message: str, percent: int) -> None:
                events.put({"event": "progress", "message": message, "percent": percent})

            def run(cancelled: Callable[[], bool]) -> dict:
                try:
                    with app.app_context(), operation_maintenance(
                        database_path,
                        "historical archive background import",
                        logger=app.logger,
                    ), connect(database_path) as conn:
                        result = import_historical_archive(
                            conn,
                            queued_path,
                            assets_dir,
                            dry_run=dry_run,
                            source_name=package_name,
                            cancel_check=cancelled,
                            progress=emit_progress,
                        )
                        events.put({"event": "result", "ok": True, "result": result})
                        audit_log(
                            "archive.validate" if dry_run else "archive.import",
                            "historical archive package processed",
                            events=result.get("events"),
                            package_sha256=result.get("package_sha256"),
                        )
                        return result
                except Exception as exc:
                    app.logger.exception("historical archive import failed")
                    events.put({"event": "result", "ok": False, "message": str(exc)[:500]})
                    raise
                finally:
                    queued_path.unlink(missing_ok=True)
                    events.put(None)

            manager = get_job_manager(
                current_app.config.get("JOB_MAX_WORKERS", 2),
                current_app.config.get("JOB_MAX_PENDING", 4),
                db_path=database_path,
            )
            job_id, _ = manager.submit_cancellable(
                f"Historical archive: {package_name}", run
            )
            staged = None
            cancel_url = url_for("dashboard.archive_cancel_job", job_id=job_id)

            def generate() -> Generator[str, None, None]:
                yield json.dumps({
                    "event": "queued",
                    "job_id": job_id,
                    "cancel_url": cancel_url,
                }) + "\n"
                while True:
                    item = events.get()
                    if item is None:
                        break
                    yield json.dumps(item, ensure_ascii=False, default=str) + "\n"

            return Response(
                generate(),
                mimetype="application/x-ndjson",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                },
            )
        if not dry_run and not current_app.config.get("TESTING"):
            database_path = str(current_app.config["DATABASE_PATH"])
            assets_dir = Path(current_app.config["ASSETS_DIR"])
            package_name = Path(upload.filename).name
            queued_path = staged
            logger = current_app.logger
            def run(cancelled):
                try:
                    with operation_maintenance(
                        database_path,
                        "historical archive background import",
                        logger=logger,
                    ), connect(database_path) as conn:
                        return import_historical_archive(
                            conn, queued_path, assets_dir, source_name=package_name,
                            cancel_check=cancelled,
                        )
                finally:
                    queued_path.unlink(missing_ok=True)
            manager = get_job_manager(
                current_app.config.get("JOB_MAX_WORKERS", 2), current_app.config.get("JOB_MAX_PENDING", 4),
                db_path=database_path,
            )
            job_id, _ = manager.submit_cancellable(f"Historical archive: {package_name}", run)
            staged = None
            session["archive_job_id"] = job_id
            flash("Historical archive import queued. Progress is shown on this page.", "success")
            return redirect(url_for("dashboard.archive_page"))
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            result = import_historical_archive(conn, staged, Path(current_app.config["ASSETS_DIR"]),
                                                dry_run=dry_run, source_name=Path(upload.filename).name)
        session["archive_import_result"] = result
        audit_log("archive.validate" if dry_run else "archive.import", "historical archive package processed",
                  events=result.get("events"), package_sha256=result.get("package_sha256"))
        flash("Archive package validated. Review the action summary before importing." if dry_run else "Historical archive imported.", "success")
    except (HistoricalArchiveError, JobQueueFull, ValueError) as exc:
        if is_xhr:
            return jsonify({"ok": False, "message": str(exc)[:500]}), 400
        flash(str(exc), "error")
    except Exception:
        current_app.logger.exception("historical archive import failed")
        if is_xhr:
            return jsonify({"ok": False, "message": "Historical archive import failed. Check the server log."}), 500
        flash("Historical archive import failed. Check the server log.", "error")
    finally:
        if staged: staged.unlink(missing_ok=True)
    return redirect(url_for("dashboard.archive_page"))


@bp.post("/archive/jobs/<job_id>/cancel")
@login_required
def archive_cancel_job(job_id: str):
    manager = get_job_manager(
        current_app.config.get("JOB_MAX_WORKERS", 2), current_app.config.get("JOB_MAX_PENDING", 4),
        db_path=str(current_app.config["DATABASE_PATH"]),
    )
    accepted = manager.request_cancel(job_id)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if accepted:
            return jsonify({"ok": True, "job_id": job_id}), 202
        return jsonify({"ok": False, "message": "Archive import job is no longer active."}), 409
    flash("Cancellation requested." if accepted else "Archive import job was not active.", "warning")
    return redirect(url_for("dashboard.archive_page"))


@bp.post("/archive/<int:archive_id>/detach")
@login_required
def archive_detach(archive_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        row = conn.execute("SELECT event_id FROM event_archive_entries WHERE id=?", (archive_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM event_archive_entries WHERE id=?", (archive_id,)); conn.commit()
    audit_log("archive.detach", "archive extension detached", archive_id=archive_id,
              canonical_event_preserved=bool(row))
    flash("Archive metadata detached. The canonical event and its assets were preserved.", "warning")
    return redirect(url_for("dashboard.archive_page"))

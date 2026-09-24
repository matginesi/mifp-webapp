from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from ..db.connection import connect
from ..services.admin_safety import backup_sqlite_database
from ..services.asset_cleanup import build_asset_cleanup_plan
from ..services.assets import recover_missing_assets
from ..services.dashboard_repository import (
    PUBLIC_TABLES,
    assets_summary,
    dashboard_alerts,
    dashboard_counts,
    database_model_health,
    list_assets,
    list_records,
    page_type_summary,
    privacy_safe_visit_stats,
    recent_rows,
    search_logs,
)
from ..services.database_restore import DatabaseRestoreError, restore_sqlite_database
from ..services.exporters import export_response_payload, rows_to_json
from ..services.metrics_service import (
    get_content_quality_summary,
    get_import_export_summary,
)
from ..services.operation_maintenance import force_clear_maintenance, operation_maintenance
from ..services.versioning import contract_versions, release_info
from ..utils.http import is_safe_relative_url
from ..utils.logger import audit_log
from ..utils.security import admin_password_matches, get_client_ip
from ._shared import (
    admin_error_text,
)
from .auth import login_required

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.get("/")
@login_required
def index():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        counts = dashboard_counts(conn)
        recent = {t: recent_rows(conn, t, 6) for t in ["members", "events", "news", "publications"]}
        asset_kinds = assets_summary(conn)
        page_types = page_type_summary(conn)
        content_status = [
            {
                "label": "News",
                "section": "news",
                "published": _scalar(conn, "SELECT COUNT(*) FROM news WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM news WHERE COALESCE(review_status,'draft')!='published'"),
            },
            {
                "label": "Events",
                "section": "events",
                "published": _scalar(conn, "SELECT COUNT(*) FROM events WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(review_status,'draft')!='published'"),
            },
            {
                "label": "Publications",
                "section": "publications",
                "published": _scalar(conn, "SELECT COUNT(*) FROM publications WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM publications WHERE COALESCE(review_status,'draft')!='published'"),
            },
            {
                "label": "Research",
                "section": "research",
                "published": _scalar(conn, "SELECT COUNT(*) FROM research_areas WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM research_areas WHERE COALESCE(review_status,'draft')!='published'"),
            },
            {
                "label": "Pages",
                "section": "pages",
                "published": _scalar(conn, "SELECT COUNT(*) FROM pages WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM pages WHERE COALESCE(review_status,'draft')!='published'"),
            },
        ]
        model_health = database_model_health(conn)
        event_status = [
            {"status": "forthcoming", "total": _scalar(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(end_date,start_date) >= date('now')")},
            {"status": "past", "total": _scalar(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(end_date,start_date) < date('now')")},
        ]
        asset_total_mb = round(_scalar(conn, "SELECT COALESCE(SUM(size),0) FROM assets") / 1024 / 1024, 2)
        cleanup_plan = build_asset_cleanup_plan(conn, current_app.config["ASSETS_DIR"])
        unused_count = len(cleanup_plan.unused_db_assets)
        recent_updates = [dict(r) for r in conn.execute(
            """
            SELECT 'news' AS type, id, title AS label, updated_at FROM news
            UNION ALL SELECT 'events', id, title, updated_at FROM events
            UNION ALL SELECT 'members', id, display_name, updated_at FROM members
            UNION ALL SELECT 'publications', id, title, updated_at FROM publications
            ORDER BY updated_at DESC
            LIMIT 8
            """
        ).fetchall()]
        alerts = dashboard_alerts(conn, current_app.config["LOG_DIR"], Path(current_app.config["ASSETS_DIR"]))
        try:
            log_entries = search_logs(current_app.config["LOG_DIR"], q=None, level="ALL", limit=6)
        except Exception:
            current_app.logger.exception("dashboard recent log preview failed")
            log_entries = []
    return render_template(
        "dashboard/index.html",
        counts=counts,
        recent=recent,
        asset_kinds=asset_kinds,
        page_types=page_types,
        content_status=content_status,
        event_status=event_status,
        asset_total_mb=asset_total_mb,
        unused_count=unused_count,
        recent_updates=recent_updates,
        model_health=model_health,
        alerts=alerts,
        log_entries=log_entries,
    )


def _scalar(conn, sql: str) -> int:
    return _sql(conn, sql)


def _sql(conn, sql: str, default=0):
    try:
        r = conn.execute(sql).fetchone()
        return int(r[0]) if r and r[0] is not None else default
    except Exception:
        current_app.logger.exception("SQL query failed in _sql")
        return default


@bp.get("/version")
@login_required
def version_release():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        database_schema = int(row["version"] or 0) if row else 0
        conference_rows = [dict(item) for item in conn.execute(
            """SELECT id,title,acronym,source_version,package_sha256,package_schema_version,
                      source_format,imported_at,public_path
               FROM conference_sites
               ORDER BY COALESCE(imported_at,updated_at) DESC,id DESC
               LIMIT 12"""
        ).fetchall()]
    release = release_info()
    return render_template(
        "dashboard/version.html",
        release=release,
        database_schema=database_schema,
        contracts=contract_versions(),
        conference_rows=conference_rows,
    )


@bp.get("/stats")
@login_required
def stats():
    try:
        selected_days = int(request.args.get("days", "30"))
    except (TypeError, ValueError):
        selected_days = 30
    if selected_days not in {7, 30, 90}:
        selected_days = 30

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        counts = dashboard_counts(conn)
        sections = ["News", "Events", "Publications", "Research", "Pages"]
        tables = ["news", "events", "publications", "research_areas", "pages"]
        content_status = []
        for label, tbl in zip(sections, tables):
            content_status.append({
                "label": label,
                "published": _sql(conn, f"SELECT COUNT(*) FROM {tbl} WHERE review_status='published'"),
                "draft": _sql(conn, f"SELECT COUNT(*) FROM {tbl} WHERE COALESCE(review_status,'draft')!='published'"),
            })
        asset_total_mb = round(_sql(conn, "SELECT COALESCE(SUM(size),0) FROM assets") / 1024 / 1024, 2)
        cleanup_plan = build_asset_cleanup_plan(conn, current_app.config["ASSETS_DIR"])
        recent_updates = [dict(r) for r in conn.execute(
            """
            SELECT 'news' AS type, id, title AS label, updated_at FROM news
            UNION ALL SELECT 'events', id, title, updated_at FROM events
            UNION ALL SELECT 'members', id, display_name, updated_at FROM members
            UNION ALL SELECT 'sponsors', id, name, updated_at FROM sponsors
            ORDER BY updated_at DESC
            LIMIT 10
            """
        ).fetchall()]
        latest_imports = [dict(r) for r in conn.execute("SELECT id, name, source_kind, status, started_at, completed_at FROM import_runs ORDER BY id DESC LIMIT 5").fetchall()]
        visits = privacy_safe_visit_stats(conn, days=selected_days)
        content_quality = get_content_quality_summary(conn)
        import_export_summary = get_import_export_summary(conn)

    published_total = sum(item["published"] for item in content_status)
    draft_total = sum(item["draft"] for item in content_status)
    download_total = sum(int(row.get("total") or 0) for row in visits["downloads"])
    not_found_total = int(visits["errors_404"] or 0)
    quality_checks = [
        ("Members without affiliation", content_quality["members_without_affiliation"], url_for("dashboard.content", section="members")),
        ("Members without country", content_quality["members_without_country"], url_for("dashboard.content", section="members")),
        ("Events without a date", content_quality["events_without_date"], url_for("dashboard.events")),
        ("Events without an image", content_quality["events_without_image"], url_for("dashboard.events")),
        ("News without an image", content_quality["news_without_image"], url_for("dashboard.content", section="news")),
        ("Publications without a link", content_quality["publications_without_link"], url_for("dashboard.content", section="publications")),
        ("Sponsors without a logo", content_quality["sponsors_without_logo"], url_for("dashboard.content", section="sponsors")),
        ("Missing asset files", len(cleanup_plan.missing_file_assets), url_for("dashboard.assets_page", status="missing")),
    ]
    quality_checks = [
        {"label": label, "count": int(count or 0), "url": action_url}
        for label, count, action_url in quality_checks
        if int(count or 0) > 0
    ]
    quality_checks.sort(key=lambda item: item["count"], reverse=True)
    operational_issues = not_found_total + int(visits["errors_5xx"] or 0)
    return render_template(
        "dashboard/stats.html",
        counts=counts,
        selected_days=selected_days,
        content_status=content_status,
        asset_total_mb=asset_total_mb,
        missing_file_count=len(cleanup_plan.missing_file_assets),
        recent_updates=recent_updates,
        latest_imports=latest_imports,
        visits=visits,
        published_total=published_total,
        draft_total=draft_total,
        download_total=download_total,
        not_found_total=not_found_total,
        operational_issues=operational_issues,
        quality_checks=quality_checks,
        import_export_summary=import_export_summary,
    )


def _table_info(conn) -> list[dict]:
    q = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    result = []
    for (name,) in q:
        cnt = _sql(conn, f"SELECT COUNT(*) FROM \"{name}\"")
        result.append({"name": name, "rows": cnt})
    return result


@bp.get("/server")
@login_required
def server():
    cfg = current_app.config
    db_path = Path(cfg["DATABASE_PATH"])
    info = {
        "Database file": str(db_path),
        "Database size": f"{round(db_path.stat().st_size / 1024 / 1024, 2)} MB",
        "Assets directory": str(cfg["ASSETS_DIR"]),
        "Logs directory": str(cfg["LOG_DIR"]),
        "Debug": str(current_app.debug),
        "Environment": cfg.get("ENV", "development"),
        "Max upload": f"{cfg.get('MAX_UPLOAD_FILE_BYTES', cfg.get('MAX_CONTENT_LENGTH', 0)) // (1024 * 1024)} MB",
    }
    with connect(cfg["DATABASE_PATH"]) as conn:
        tables = _table_info(conn)
        db_ok = "OK"
        counts = dashboard_counts(conn)
        model_health = database_model_health(conn)
        asset_status = [dict(r) for r in conn.execute(
            "SELECT COALESCE(storage_status,'unknown') AS status, COUNT(*) AS total FROM assets GROUP BY COALESCE(storage_status,'unknown') ORDER BY total DESC"
        ).fetchall()]
        content_status = {
            "news": {
                "published": _scalar(conn, "SELECT COUNT(*) FROM news WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM news WHERE COALESCE(review_status,'draft')!='published'"),
            },
            "events": {
                "published": _scalar(conn, "SELECT COUNT(*) FROM events WHERE review_status='published'"),
                "draft": _scalar(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(review_status,'draft')!='published'"),
            },
            "sponsors": {
                "active": _scalar(conn, "SELECT COUNT(*) FROM sponsors WHERE is_active=1"),
                "inactive": _scalar(conn, "SELECT COUNT(*) FROM sponsors WHERE COALESCE(is_active,0)=0"),
            },
        }
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        settings = {r["key"]: r["value"] for r in rows}
    for k, v in cfg.get("SITE_DEFAULTS", {}).items():
        settings.setdefault(k, v)

    # Security info + warnings
    admin_user = cfg.get("ADMIN_USERNAME") or "admin"
    admin_masked = (admin_user[0] + "***") if admin_user else "***"
    secret_key = cfg.get("SECRET_KEY", "")
    security_info = {
        "csrf_enabled": bool(cfg.get("WTF_CSRF_ENABLED", True)),
        "session_httponly": bool(cfg.get("SESSION_COOKIE_HTTPONLY", True)),
        "session_samesite": cfg.get("SESSION_COOKIE_SAMESITE", "Lax"),
        "session_secure": bool(cfg.get("SESSION_COOKIE_SECURE", False)),
        "csp_enabled": True,
        "rate_limit_window": "5 req / 60 s (login)",
        "admin_user_masked": admin_masked,
        "secret_key_default": secret_key == "dev-change-me" or not secret_key,
    }
    security_warnings = []
    if secret_key == "dev-change-me" or not secret_key:
        security_warnings.append("SECRET_KEY is the default value 'dev-change-me' — set a unique value in .env for production.")
    if not cfg.get("SESSION_COOKIE_SECURE"):
        security_warnings.append("SESSION_COOKIE_SECURE is disabled — session cookies transmitted over HTTP.")
    admin_name = cfg.get("ADMIN_USERNAME")
    if not admin_name:
        security_warnings.append("ADMIN_USERNAME is not set in .env — admin login will be unavailable.")
    elif admin_name == "admin":
        security_warnings.append("Admin username is the default 'admin' — consider changing it.")

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    app_env = cfg.get("ENV", "development")
    if app_env == "production" and not cfg.get("ADMIN_PASSWORD_HASH"):
        security_warnings.append("ADMIN_PASSWORD_HASH is not set — admin login will always fail.")

    # Recent audit log entries (last 10)
    audit_entries = search_logs(cfg["LOG_DIR"], q=None, level="ALL", limit=10)
    # Keep only audit log entries
    audit_entries = [e for e in audit_entries if e.get("logger") == "mifp.audit"][:10]

    return render_template(
        "dashboard/server.html",
        info=info,
        tables=tables,
        db_ok=db_ok,
        security_info=security_info,
        security_warnings=security_warnings,
        audit_entries=audit_entries,
        python_version=python_version,
        app_env=app_env,
        counts=counts,
        asset_status=asset_status,
        content_status=content_status,
        settings=settings,
        model_health=model_health,
        allow_db_restore=bool(cfg.get("ALLOW_DB_RESTORE", False)),
    )


@bp.post("/server/integrity-check")
@login_required
def server_integrity_check():
    errors = []
    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            for (msg,) in rows:
                if msg != "ok":
                    errors.append(msg)
    except Exception as e:
        current_app.logger.exception("integrity check failed")
        errors.append(str(e))
    if errors:
        flash(f"Integrity errors: {'; '.join(errors)}", "error")
    else:
        flash("Integrity check passed.", "success")
    return redirect(url_for("dashboard.server"))


@bp.post("/server/db-dump")
@login_required
def server_db_dump():
    if not current_app.config.get("ALLOW_DB_DUMP", False):
        audit_log("admin.db_download_denied", "db download blocked (ALLOW_DB_DUMP=0)", category="admin", outcome="denied",
                  ip=get_client_ip(), username=session.get("admin_username"))
        flash("Database dump is disabled in the current configuration.", "error")
        return redirect(url_for("dashboard.server"))
    password = request.form.get("password", "")
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")
    if not admin_password_matches(password, expected_hash):
        audit_log("admin.db_download_denied", "db download denied", category="admin", outcome="denied",
                  ip=get_client_ip(), username=session.get("admin_username"))
        flash("Invalid password.", "error")
        return redirect(url_for("dashboard.server"))
    db_path = Path(current_app.config["DATABASE_PATH"])
    with operation_maintenance(db_path, "consistent database download", logger=current_app.logger):
        snapshot = backup_sqlite_database(
            db_path, label="download", _maintenance_guard=False
        )
    if snapshot is None:
        flash("Database file is unavailable.", "error")
        return redirect(url_for("dashboard.server"))
    audit_log("admin.db_download", "verified database snapshot downloaded", category="admin", outcome="success",
              ip=get_client_ip(), username=session.get("admin_username"),
              db_size=snapshot.stat().st_size)
    response = send_from_directory(str(snapshot.parent), snapshot.name, as_attachment=True,
                                   download_name=f"mifp_full_database_{date.today().isoformat()}.sqlite")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-MIFP-Backup-Type"] = "full-sqlite-snapshot"
    return response


@bp.post("/server/db-restore")
@login_required
def server_db_restore():
    if not current_app.config.get("ALLOW_DB_RESTORE", False):
        audit_log(
            "admin.db_restore_denied",
            "database restore blocked by configuration",
            category="security",
            outcome="denied",
            ip=get_client_ip(),
            username=session.get("admin_username"),
        )
        flash("Database restore is disabled in the current configuration.", "error")
        return redirect(url_for("dashboard.server"))

    password = request.form.get("password", "")
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")
    confirmation = request.form.get("confirmation", "").strip()
    database_file = request.files.get("database_file")
    if not admin_password_matches(password, expected_hash):
        audit_log(
            "admin.db_restore_denied", "database restore denied: invalid password",
            category="security", outcome="denied",
            ip=get_client_ip(), username=session.get("admin_username"),
        )
        flash("Invalid password.", "error")
        return redirect(url_for("dashboard.server"))
    if confirmation != "RESTORE DATABASE":
        flash('Type "RESTORE DATABASE" to confirm the replacement.', "error")
        return redirect(url_for("dashboard.server"))
    if not database_file or not database_file.filename:
        flash("Choose a full SQLite database snapshot.", "error")
        return redirect(url_for("dashboard.server"))
    filename = Path(database_file.filename).name
    if Path(filename).suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        flash("Choose a .sqlite, .sqlite3 or .db full database snapshot.", "error")
        return redirect(url_for("dashboard.server"))

    db_path = Path(current_app.config["DATABASE_PATH"])
    current_app.logger.warning(
        "Full database restore requested filename=%s", filename
    )
    try:
        with operation_maintenance(
            db_path, "full database restore", logger=current_app.logger
        ):
            report = restore_sqlite_database(db_path, database_file.stream)
    except DatabaseRestoreError as exc:
        current_app.logger.warning(
            "Full database restore rejected filename=%s reason=%s", filename, exc
        )
        audit_log(
            "admin.db_restore_rejected", "database restore validation failed",
            category="security", outcome="denied", filename=filename,
            error=str(exc)[:300], ip=get_client_ip(),
            username=session.get("admin_username"),
        )
        flash(str(exc), "error")
        return redirect(url_for("dashboard.server"))
    except Exception:
        current_app.logger.exception("Full database restore failed and rollback was attempted")
        audit_log(
            "admin.db_restore_failed", "database restore failed",
            category="admin", outcome="failure", filename=filename,
            ip=get_client_ip(), username=session.get("admin_username"),
        )
        flash(admin_error_text("Database restore failed; the previous database was preserved."), "error")
        return redirect(url_for("dashboard.server"))

    audit_log(
        "admin.db_restore", "full database restored from verified snapshot",
        category="admin", outcome="success", filename=filename,
        restored_bytes=report["bytes"], backup_path=report["backup_path"],
        ip=get_client_ip(), username=session.get("admin_username"),
    )
    current_app.logger.warning(
        "Full database restore completed filename=%s safety_backup=%s",
        filename, report["backup_path"],
    )
    flash(
        "Full database restored successfully. Integrity and schema checks passed; "
        "the previous database was saved as a safety backup.",
        "success",
    )
    return redirect(url_for("dashboard.server"))


EXPORT_FORMATS = {"csv", "xlsx", "docx", "pdf", "json", "jsonl"}


def _download_response(rows: list[dict[str, Any]], fmt: str, filename: str, title: str) -> Response:
    if fmt not in EXPORT_FORMATS:
        return Response("Invalid export format", status=400)
    if request.args.get("format") == "json":
        return jsonify(rows_to_json(rows))
    payload, mimetype, ext = export_response_payload(rows, fmt, title)
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return Response(payload, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={filename}.{ext}"})


@bp.get("/export/<table>.<fmt>")
@login_required
def export_table(table: str, fmt: str):
    if table not in PUBLIC_TABLES and table != "assets":
        return Response("Invalid table", status=400)
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        rows = list_assets(conn, limit=10000) if table == "assets" else list_records(conn, table, limit=10000)
    title = "Assets" if table == "assets" else str(PUBLIC_TABLES[table]["title"])
    audit_log("export.table", "table export", category="admin", outcome="success",
              table=table, format=fmt, count=len(rows))
    return _download_response(rows, fmt, table, title)


@bp.get("/export/stats.<fmt>")
@login_required
def export_stats(fmt: str):
    rows: list[dict[str, Any]] = []
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        for key, value in dashboard_counts(conn).items():
            rows.append({"section": "counts", "label": key, "value": value})
        for row in assets_summary(conn):
            rows.append({"section": "assets", "label": row["kind"], "value": row["total"], "bytes": row["bytes"]})
        for row in page_type_summary(conn):
            rows.append({"section": "pages", "label": row["type"], "value": row["total"]})
        for row in conn.execute("SELECT substr(date,1,4) AS year, COUNT(*) AS total FROM news WHERE date IS NOT NULL GROUP BY substr(date,1,4) ORDER BY year").fetchall():
            rows.append({"section": "news_by_year", "label": row["year"], "value": row["total"]})
        rows.append({"section": "events_by_status", "label": "forthcoming", "value": _sql(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(end_date,start_date) >= date('now')")})
        rows.append({"section": "events_by_status", "label": "past", "value": _sql(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(end_date,start_date) < date('now')")})
        for row in conn.execute("SELECT COALESCE(country,'Unknown') AS country, COUNT(*) AS total FROM members GROUP BY COALESCE(country,'Unknown') ORDER BY total DESC LIMIT 1000").fetchall():
            rows.append({"section": "members_by_country", "label": row["country"], "value": row["total"]})
    return _download_response(rows, fmt, "mifp_stats", "MIFP statistics")


@bp.get("/settings")
@login_required
def settings():
    return redirect(url_for("dashboard.server"))


@bp.post("/settings")
@login_required
def settings_save():
    from ..services.site_copy import copy_setting_keys, validate_copy_value

    submitted = request.form.to_dict(flat=False)
    data = {key: values[-1] if values else "" for key, values in submitted.items()}
    redirect_url = data.pop("_redirect", None)
    data.pop("_csrf_token", None)
    allowed = copy_setting_keys()
    data = {k: v for k, v in data.items() if k in allowed}
    validation_errors = []
    for key in set(data) & copy_setting_keys():
        clean_value, error = validate_copy_value(key, data[key])
        if error:
            validation_errors.append(error)
        else:
            data[key] = clean_value
    if validation_errors:
        for error in validation_errors[:5]:
            flash(error, "error")
        return redirect(url_for("dashboard.site_texts"))
    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            for key, value in data.items():
                conn.execute(
                    "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                    (key, value),
                )
            conn.commit()
    except Exception:
        current_app.logger.exception("settings save failed")
        flash(admin_error_text("Settings could not be saved. Check the server log."), "error")
        return redirect(url_for("dashboard.server"))
    audit_log("settings.update", "settings updated", keys=list(data.keys()))
    flash("Settings saved.", "success")
    if redirect_url and is_safe_relative_url(redirect_url):
        return redirect(redirect_url)
    return redirect(url_for("dashboard.server"))




@bp.post("/control/site/force-clear-maintenance")
@login_required
def control_site_force_clear_maintenance():
    """Recover the public site after a stuck maintenance operation.

    Requires an admin password re-entry, mirroring the database dump/restore
    routes, so a session alone is never enough to lift the work-in-progress
    gate.
    """
    password = request.form.get("password", "")
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")
    if not admin_password_matches(password, expected_hash):
        audit_log(
            "admin.force_clear_maintenance_denied",
            "force clear maintenance denied: invalid password",
            category="security",
            outcome="denied",
            ip=get_client_ip(),
            username=session.get("admin_username"),
        )
        flash("Invalid password.", "error")
        return redirect(url_for("dashboard.server"))
    if force_clear_maintenance(
        current_app.config["DATABASE_PATH"], logger=current_app.logger
    ):
        flash("Maintenance mode cleared. The public site is available again.", "success")
    else:
        flash("No active maintenance operation was found to clear.", "warning")
    return redirect(url_for("dashboard.server"))


# ---------------------------------------------------------------------------
# Asset maintenance
# ---------------------------------------------------------------------------

@bp.post("/assets/retry-external")
@login_required
def assets_retry_external():
    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            result = recover_missing_assets(conn, Path(current_app.config["ASSETS_DIR"]))
        flash(
            f"Recovery batch: {result['attempted']} attempted, {result['recovered']} recovered, "
            f"{len(result['failed'])} failed, {result['deferred']} cooling down, "
            f"{result['terminal']} retry limit reached.",
            "success" if result["recovered"] > 0 else "warning",
        )
        for fail in result["failed"]:
            current_app.logger.warning("Asset recovery failed: id=%d url=%s error=%s", fail["id"], fail["url"], fail["error"])
    except Exception:
        current_app.logger.exception("retry external assets failed")
        flash("Asset recovery failed. Check the server log for details.", "error")
    # Referer is untrusted input and may be an external URL. This maintenance
    # action has one stable, same-site destination.
    return redirect(url_for("dashboard.server"))




from . import (  # noqa: E402,F401
    dashboard_archive,
    dashboard_assets,
    dashboard_conferences,
    dashboard_content,
    dashboard_control,
    dashboard_data_quality,
    dashboard_event_import,
    dashboard_logs,
    dashboard_portability,
    dashboard_security,
    dashboard_join,
)

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Generator
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
from werkzeug.security import check_password_hash

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
from ..services.data_portability import (
    build_export_bundle,
    canonical_bundle_to_zip,
    import_zip_payload,
    scope_options,
    table_counts,
)
from ..services.database_restore import DatabaseRestoreError, restore_sqlite_database
from ..services.exporters import export_response_payload, rows_to_json
from ..services.importers import import_jsonl
from ..services.metrics_service import (
    get_content_quality_summary,
    get_import_export_summary,
)
from ..services.operation_maintenance import force_clear_maintenance, operation_maintenance
from ..utils.logger import audit_log
from ..utils.security import get_client_ip
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
        alerts = dashboard_alerts(conn)
        try:
            log_entries = search_logs(current_app.config["LOG_DIR"], q=None, level="ALL", limit=6)
        except Exception:
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
        ("Missing asset files", len(cleanup_plan.missing_file_assets), url_for("dashboard.control_assets")),
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
        "Max upload": f"{cfg.get('MAX_CONTENT_LENGTH', 0) // (1024 * 1024)} MB",
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


@bp.post("/server/vacuum")
@login_required
def server_vacuum():
    audit_log(
        "server.vacuum_redirected",
        "legacy unprotected vacuum route blocked",
        category="security",
        outcome="denied",
        username=session.get("admin_username"),
        ip=get_client_ip(),
    )
    flash("Database cleanup now requires the password-protected operations wizard.", "warning")
    return redirect(url_for("dashboard.control_safety_operations"))


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
    if not current_app.config.get("ALLOW_DB_DUMP", True):
        audit_log("admin.db_download_denied", "db download blocked (ALLOW_DB_DUMP=0)", category="admin", outcome="denied",
                  ip=get_client_ip(), username=session.get("admin_username"))
        flash("Database dump is disabled in the current configuration.", "error")
        return redirect(url_for("dashboard.server"))
    password = request.form.get("password", "")
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")
    if not expected_hash or not check_password_hash(expected_hash, password):
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
    if not expected_hash or not check_password_hash(expected_hash, password):
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


@bp.get("/data-portability")
@login_required
def data_portability():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        counts = table_counts(conn)
        recent_import_rows = conn.execute(
            """
            SELECT id, name, status, started_at, completed_at, stats_json
            FROM import_runs
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
    recent_imports = []
    for row in recent_import_rows:
        try:
            stats = json.loads(row["stats_json"] or "{}")
        except (TypeError, ValueError):
            stats = {}
        recent_imports.append({
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "records": _summary_count(stats.get("records")),
            "inserted": _summary_count(stats.get("inserted")),
            "updated": _summary_count(stats.get("updated")),
            "errors": _summary_count(stats.get("errors")),
        })
    scopes = scope_options()
    portable_tables = {item.get("primary") for item in scopes if item.get("primary")}
    portable_total = sum(counts.get(table, 0) for table in portable_tables)
    return render_template(
        "dashboard/data_portability.html",
        scopes=scopes,
        counts=counts,
        portable_total=portable_total,
        recent_imports=recent_imports,
        import_result=session.pop("data_portability_import_result", None),
    )


_export_cache: dict[str, dict] = {}
_EXPORT_CACHE_MAX = 2
_EXPORT_CACHE_TTL_SECONDS = 300


def _prune_export_cache(now: float | None = None) -> int:
    current = now if now is not None else time.monotonic()
    expired = [
        token for token, entry in _export_cache.items()
        if current - float(entry.get("created_at") or 0) > _EXPORT_CACHE_TTL_SECONDS
    ]
    for token in expired:
        _export_cache.pop(token, None)
    while len(_export_cache) >= _EXPORT_CACHE_MAX:
        oldest = min(
            _export_cache,
            key=lambda item: float(_export_cache[item].get("created_at") or 0),
        )
        _export_cache.pop(oldest, None)
    return len(expired)


def _safe_upload_name(name: str, fallback_suffix: str = ".jsonl") -> str:
    clean = "".join(c for c in Path(name).name if c.isalnum() or c in "._-")
    if not clean or clean in {".", ".."}:
        clean = "file"
    if not Path(clean).suffix:
        clean += fallback_suffix
    return clean


def _payload_size(payload: bytes | Path) -> int:
    return len(payload) if isinstance(payload, bytes) else payload.stat().st_size


def _import_zip_dispatch(
    conn: sqlite3.Connection,
    payload: bytes | Path,
    scope: str,
    *,
    dry_run: bool,
    skip_assets: bool,
    force_import: bool,
    progress: Callable[[int, int], None] | None,
    source_name: str | None,
) -> dict[str, Any]:
    return import_zip_payload(
        conn, payload, scope, current_app.config["ASSETS_DIR"],
        dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
        progress=progress, source_name=source_name,
    )


def _safe_upload_log_items(file_data: list[tuple[str, bytes | Path]]) -> list[dict[str, int | str]]:
    return [
        {
            "index": index,
            "extension": (Path(name).suffix.lower().lstrip(".") or "none")[:12],
            "bytes": len(payload) if isinstance(payload, bytes) else payload.stat().st_size,
        }
        for index, (name, payload) in enumerate(file_data, start=1)
    ]


@bp.get("/data-portability/export/all.<fmt>")
@login_required
def data_portability_export_direct(fmt: str):
    if fmt not in {"jsonl", "zip"}:
        return Response("Invalid export format", status=400)
    with operation_maintenance(
        current_app.config["DATABASE_PATH"], f"data export: {fmt}", logger=current_app.logger
    ):
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            if fmt == "zip":
                payload = canonical_bundle_to_zip(conn, current_app.config["ASSETS_DIR"], app_version=str(current_app.config.get("APP_VERSION", "")))
            else:
                records = build_export_bundle(conn, "all")["records"]
                payload = ("\n".join(
                    json.dumps(record, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
                    for record in records
                ) + ("\n" if records else "")).encode("utf-8")
    mimetype = "application/zip" if fmt == "zip" else "application/x-ndjson"
    filename = f"MIFP_EXPORT_{date.today().isoformat()}.zip" if fmt == "zip" else "records.jsonl"
    current_app.logger.info(
        "data portability direct export ready format=%s bytes=%d", fmt, len(payload)
    )
    audit_log(
        "export.data_portability_direct",
        "direct data portability download",
        category="admin",
        outcome="success",
        format=fmt,
        bytes=len(payload),
    )
    return Response(payload, mimetype=mimetype, headers={
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Length": str(len(payload)),
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    })


@bp.post("/data-portability/export/<fmt>")
@login_required
def data_portability_export_post(fmt: str):
    started = time.monotonic()
    if fmt not in {"jsonl", "zip"}:
        return jsonify({"ok": False, "message": "Invalid export format"}), 400
    scope = "all"
    current_app.logger.info(
        "data portability export started format=%s scope=%s", fmt, scope
    )

    with operation_maintenance(
        current_app.config["DATABASE_PATH"], f"data export: {fmt}", logger=current_app.logger
    ):
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            if fmt == "zip":
                payload = canonical_bundle_to_zip(conn, current_app.config["ASSETS_DIR"], app_version=str(current_app.config.get("APP_VERSION", "")))
            else:
                records = build_export_bundle(conn, scope)["records"]
                payload = ("\n".join(
                    json.dumps(record, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
                    for record in records
                ) + ("\n" if records else "")).encode("utf-8")

    mimetype = "application/zip" if fmt == "zip" else "application/x-ndjson"
    filename = f"MIFP_EXPORT_{date.today().isoformat()}.zip" if fmt == "zip" else "records.jsonl"
    duration_ms = int((time.monotonic() - started) * 1000)
    total_bytes = len(payload)
    size_str = f"{total_bytes/1024:.1f} KB" if total_bytes < 1048576 else f"{total_bytes/1048576:.1f} MB"
    token = secrets.token_urlsafe(16)
    expired = _prune_export_cache()
    _export_cache[token] = {
        "data": payload,
        "filename": filename,
        "mimetype": mimetype,
        "created_at": time.monotonic(),
        "owner": session.get("admin_username"),
    }

    current_app.logger.info(
        "data portability export ready format=%s bytes=%d duration_ms=%d expired_tokens=%d cached_exports=%d",
        fmt, total_bytes, duration_ms, expired, len(_export_cache),
    )
    audit_log("export.data_portability", "data portability export", category="admin", outcome="success",
              scope=scope, format=fmt, bytes=total_bytes, duration_ms=duration_ms)

    def generate() -> Generator[str, None, None]:
        yield json.dumps({"event": "phase", "phase": "bundle", "label": "Building export bundle…", "percent": 0}) + "\n"
        yield json.dumps({"event": "phase", "phase": "ready", "label": "Preparing download…", "percent": 80}) + "\n"
        yield json.dumps({
            "event": "result", "ok": True,
            "title_text": "Export ready", "message": f"{fmt.upper()} export ({size_str}) ready for download.",
            "icon_class": "bi-check-lg", "icon_modifier": "is-success",
            "filename": filename, "bytes": total_bytes, "mimetype": mimetype,
            "download_token": token,
        }) + "\n"

    return Response(generate(), mimetype="application/x-ndjson", headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
    })


@bp.get("/data-portability/export-dl/<token>")
@login_required
def data_portability_export_dl(token: str):
    _prune_export_cache()
    entry = _export_cache.get(token)
    if not entry:
        audit_log(
            "export.download_rejected",
            "data portability download token rejected",
            category="admin",
            outcome="denied",
            reason="missing_or_expired",
        )
        return Response("Download link expired or invalid. Please re-export.", status=404)
    if entry.get("owner") != session.get("admin_username"):
        audit_log(
            "export.download_rejected",
            "data portability download token rejected",
            category="admin",
            outcome="denied",
            reason="owner_mismatch",
        )
        return Response("Download link expired or invalid. Please re-export.", status=404)
    entry = _export_cache.pop(token)
    payload_size = len(entry["data"])
    current_app.logger.info(
        "data portability download served format=%s bytes=%d cached_exports=%d",
        Path(entry["filename"]).suffix.lstrip("."),
        payload_size,
        len(_export_cache),
    )
    audit_log(
        "export.download",
        "data portability export downloaded",
        category="admin",
        outcome="success",
        format=Path(entry["filename"]).suffix.lstrip("."),
        bytes=payload_size,
    )
    return Response(
        entry["data"],
        mimetype=entry["mimetype"],
        headers={
            "Content-Disposition": f"attachment; filename={entry['filename']}",
            "Content-Length": str(payload_size),
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@bp.post("/data-portability/import")
@login_required
def data_portability_import():
    started = time.monotonic()
    scope = request.form.get("scope", "").strip() or "all"
    files = [f for f in request.files.getlist("data_file") if f and f.filename]
    if not files:
        flash("Upload one or more JSON/JSONL files, or a single ZIP bundle.", "error")
        return redirect(url_for("dashboard.data_portability"))
    try:
        dry_run = request.form.get("dry_run") == "1"
        skip_assets = request.form.get("skip_assets") == "1"
        force_import = request.form.get("force_import") == "1"
        current_app.logger.info(
            "data portability import request started scope=%s files=%d dry_run=%s skip_assets=%s force_import=%s",
            scope, len(files), dry_run, skip_assets, force_import,
        )

        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if not is_xhr:
            return _import_postback(scope, files, dry_run, skip_assets, started, force_import=force_import)

        # Stage uploads to disk before the thread starts: request context and
        # its FileStorage objects die once the request returns, and holding
        # every file in memory would defeat streaming.
        upload_dir = Path(tempfile.mkdtemp(prefix="mifp-import-"))
        file_data: list[tuple[str, bytes | Path]] = []
        try:
            for i, f in enumerate(files):
                name = f.filename or f"file_{i}"
                suffix = Path(name).suffix or ".jsonl"
                staged = upload_dir / f"{i}_{_safe_upload_name(name, suffix)}"
                f.save(staged)
                file_data.append((name, staged))
        except Exception:
            shutil.rmtree(upload_dir, ignore_errors=True)
            raise
        total_bytes = sum(p.stat().st_size for _, p in file_data)
        current_app.logger.info(
            "data portability import prepared scope=%s files=%s bytes=%d dry_run=%s skip_assets=%s force_import=%s",
            scope, _safe_upload_log_items(file_data), total_bytes,
            dry_run, skip_assets, force_import,
        )

        import queue

        from ..services.job_manager import JobQueueFull, get_job_manager

        event_queue: queue.Queue[dict | None] = queue.Queue()

        def event_sink(data: dict) -> None:
            event_queue.put(data)

        app = current_app._get_current_object()

        def run_import() -> None:
            with app.app_context():
                try:
                    _perform_import(scope, file_data, dry_run, skip_assets, started, event_sink, force_import=force_import)
                except Exception as exc:
                    app.logger.exception("Import failed in background thread")
                    audit_log(
                        "import.failed",
                        "data portability background import failed",
                        category="admin",
                        outcome="failure",
                        scope=scope,
                        files=len(file_data),
                        error_type=type(exc).__name__,
                    )
                    event_sink({"event": "error", "message": _safe_import_error(exc)})
                    raise
                finally:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    event_queue.put(None)

        manager = get_job_manager(
            int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
            int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
        )
        try:
            job_id, _future = manager.submit(f"data-import:{scope}", run_import)
        except JobQueueFull:
            shutil.rmtree(upload_dir, ignore_errors=True)
            audit_log(
                "import.queue_full",
                "data portability import rejected because background queue is full",
                category="admin",
                outcome="failure",
            )
            return jsonify({"ok": False, "error": "job_queue_full"}), 503
        audit_log(
            "import.queued",
            "data portability import queued",
            category="admin",
            outcome="success",
            job_id=job_id,
            scope=scope,
            files=len(file_data),
        )

        def generate() -> Generator[str, None, None]:
            while True:
                data = event_queue.get()
                if data is None:
                    break
                yield json.dumps(data, ensure_ascii=False, default=str) + "\n"

        return Response(
            generate(),
            mimetype="application/x-ndjson",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    except Exception as exc:
        current_app.logger.exception("Import failed before streaming")
        audit_log("import.failed", "data portability import failed", category="admin",
                  outcome="failure", scope=scope, files=len(files),
                  error_type=type(exc).__name__)
        flash(f"Import error: {_safe_import_error(exc)}", "error")
        return redirect(url_for("dashboard.data_portability"))


def _safe_import_error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    return "The import could not be completed. Check the server log for details."


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _perform_import(
    scope: str,
    file_data: list[tuple[str, bytes | Path]],
    dry_run: bool,
    skip_assets: bool,
    started: float,
    event_sink: Callable[[dict], None],
    *,
    force_import: bool = False,
) -> None:
    with operation_maintenance(
        current_app.config["DATABASE_PATH"],
        "data import",
        logger=current_app.logger,
    ):
        return _perform_import_unprotected(
            scope,
            file_data,
            dry_run,
            skip_assets,
            started,
            event_sink,
            force_import=force_import,
        )


def _perform_import_unprotected(
    scope: str,
    file_data: list[tuple[str, bytes | Path]],
    dry_run: bool,
    skip_assets: bool,
    started: float,
    event_sink: Callable[[dict], None],
    *,
    force_import: bool = False,
) -> None:
    summaries: list[dict[str, Any]] = []
    backup_path = None
    is_zip_import = any(n.lower().endswith(".zip") for n, _ in file_data)
    if not dry_run:
        event_sink({"event": "phase", "phase": "backup", "label": "Creating database backup…", "current_step": 0, "total_steps": 5, "percent": 0})
        current_app.logger.info("Creating pre-import database backup")
        backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label="import")
        current_app.logger.info(
            "pre-import backup ready created=%s", bool(backup_path)
        )
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        _per_file_totals: list[int] = []
        _total_records_aggregate = 0
        _completed_records = 0
        _completed_inserted = 0
        _completed_updated = 0
        _completed_assets = 0
        _completed_record_errors = 0
        _completed_asset_errors = 0

        def progress(file_name: str, done: int, total: int) -> None:
            nonlocal _per_file_totals, _total_records_aggregate
            pct = round((done / max(total, 1)) * 100)
            event_sink({
                "event": "progress", "current": done, "total": total,
                "file": file_name, "percent": pct,
            })
            if not _per_file_totals or _per_file_totals[-1] != total:
                _per_file_totals.append(total)
                _total_records_aggregate = sum(_per_file_totals)
            event_sink({
                "event": "metrics",
                "records": _completed_records + done,
                "total_records": _total_records_aggregate,
                "assets_linked": _completed_assets,
                "errors": _completed_record_errors + _completed_asset_errors,
                "asset_errors": _completed_asset_errors,
                "record_errors": _completed_record_errors,
                "inserted": _completed_inserted,
                "updated": _completed_updated,
            })

        event_sink({"event": "phase", "phase": "importing", "label": "Importing records…", "current_step": 1, "total_steps": 5, "percent": 20})

        zip_files = [(n, b) for n, b in file_data if n.lower().endswith(".zip")]
        if zip_files and len(file_data) == 1:
            filename, payload = zip_files[0]
            current_app.logger.info(
                "importing ZIP index=1 bytes=%d", _payload_size(payload)
            )
            before_asset_id = _max_asset_id(conn)
            summary = _import_zip_dispatch(
                conn, payload, scope,
                dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
                progress=lambda done, total: progress(filename, done, total),
                source_name=filename,
            )
            summary["filename"] = filename
            raw_errors = summary.get("errors")
            error_details = []
            if isinstance(raw_errors, list):
                for e in raw_errors:
                    if isinstance(e, dict):
                        msg = (e.get("error") or "").strip()
                        if msg:
                            error_details.append({"kind": "record", "record": e.get("line"), "message": msg[:500]})
            elif isinstance(raw_errors, dict):
                for v in raw_errors.values():
                    msg = str(v).strip()
                    if msg:
                        error_details.append({"kind": "record", "message": msg[:500]})
            raw_asset_errors = summary.get("asset_errors")
            if isinstance(raw_asset_errors, list):
                for e in raw_asset_errors:
                    if isinstance(e, dict):
                        msg = str(e.get("error") or "").strip()
                        if msg:
                            error_details.append({"kind": "asset", "record": e.get("line"), "message": msg[:500]})
            summary["error_details"] = error_details
            summary["errors"] = _summary_count(summary.get("errors"))
            summary["asset_errors"] = _summary_count(summary.get("asset_errors"))
            summary["linked_assets"] = _summary_count(summary.get("linked_assets"))
            summary.update(_asset_delta(conn, before_asset_id))
            zip_inserted = _summary_count(summary.get("inserted"))
            zip_updated = _summary_count(summary.get("updated"))
            link_assets = _summary_count(summary.get("linked_assets"))
            zip_errors = _summary_count(summary.get("errors"))
            zip_asset_errors = _summary_count(summary.get("asset_errors"))
            _completed_records += zip_inserted + zip_updated
            _completed_inserted += zip_inserted
            _completed_updated += zip_updated
            _completed_assets += link_assets
            _completed_record_errors += zip_errors
            _completed_asset_errors += zip_asset_errors
            summaries.append(summary)
            event_sink({
                "event": "metrics",
                "records": _completed_records,
                "total_records": _total_records_aggregate or _completed_records,
                "assets_linked": _completed_assets,
                "errors": _completed_record_errors + _completed_asset_errors,
                "asset_errors": _completed_asset_errors,
                "record_errors": _completed_record_errors,
                "inserted": _completed_inserted,
                "updated": _completed_updated,
            })
        else:
            file_count = len(file_data)
            for file_index, (filename, payload) in enumerate(file_data):
                if filename.lower().endswith(".zip"):
                    event_sink({
                        "event": "file_start", "file": filename,
                        "file_index": file_index, "file_count": file_count,
                        "bytes": _payload_size(payload),
                    })
                    current_app.logger.info(
                        "importing ZIP in mixed batch index=%d bytes=%d",
                        file_index + 1,
                        _payload_size(payload),
                    )
                    before_asset_id = _max_asset_id(conn)
                    summary = _import_zip_dispatch(
                        conn, payload, scope,
                        dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
                        progress=lambda done, total: progress(filename, done, total),
                        source_name=filename,
                    )
                    summary["filename"] = filename
                    raw_errors = summary.get("errors")
                    error_details = []
                    if isinstance(raw_errors, list):
                        error_details.extend({
                            "kind": "record", "record": entry.get("line"),
                            "message": str(entry.get("error") or "")[:500],
                        } for entry in raw_errors if isinstance(entry, dict) and entry.get("error"))
                    elif isinstance(raw_errors, dict):
                        error_details.extend({
                            "kind": "record", "message": str(message)[:500],
                        } for message in raw_errors.values() if str(message).strip())
                    raw_asset_errors = summary.get("asset_errors")
                    if isinstance(raw_asset_errors, list):
                        error_details.extend({
                            "kind": "asset", "record": entry.get("line"),
                            "message": str(entry.get("error") or "")[:500],
                        } for entry in raw_asset_errors if isinstance(entry, dict) and entry.get("error"))
                    summary["error_details"] = error_details
                    summary["errors"] = _summary_count(raw_errors)
                    summary["asset_errors"] = _summary_count(raw_asset_errors)
                    summary["linked_assets"] = _summary_count(summary.get("linked_assets"))
                    summary.update(_asset_delta(conn, before_asset_id))
                    summaries.append(summary)
                    file_inserted = _summary_count(summary.get("inserted"))
                    file_updated = _summary_count(summary.get("updated"))
                    file_assets = _summary_count(summary.get("linked_assets"))
                    file_record_errors = _summary_count(summary.get("errors"))
                    file_asset_errors = _summary_count(summary.get("asset_errors"))
                    _completed_records += file_inserted + file_updated
                    _completed_inserted += file_inserted
                    _completed_updated += file_updated
                    _completed_assets += file_assets
                    _completed_record_errors += file_record_errors
                    _completed_asset_errors += file_asset_errors
                    event_sink({
                        "event": "file_done", "file": filename,
                        "file_index": file_index, "file_count": file_count,
                    })
                    continue
                current_app.logger.info(
                    "importing JSONL index=%d bytes=%d", file_index + 1, _payload_size(payload)
                )
                if isinstance(payload, Path):
                    tmp_path = payload
                    owns_tmp = False
                else:
                    suffix = Path(filename).suffix or ".jsonl"
                    with tempfile.NamedTemporaryFile(mode='wb', suffix=suffix, delete=False) as f:
                        f.write(payload)
                        tmp_path = Path(f.name)
                    owns_tmp = True
                try:
                    event_sink({
                        "event": "file_start", "file": filename,
                        "file_index": file_index, "file_count": file_count,
                        "bytes": _payload_size(payload),
                    })
                    before_asset_id = _max_asset_id(conn)
                    counts = import_jsonl(
                        conn, tmp_path,
                        dry_run=dry_run, force_import=force_import,
                        assets_dir=current_app.config["ASSETS_DIR"],
                        progress=lambda done, total: progress(filename, done, total),
                        asset_detail=lambda msg: event_sink({"event": "detail", "message": msg}),
                        source_name=filename,
                    )
                    file_summary = {
                        "filename": filename,
                        "inserted": counts.get("inserted"),
                        "updated": counts.get("updated"),
                        "errors": _summary_count(counts.get("errors")),
                        "asset_errors": _summary_count(counts.get("asset_errors")),
                        "skipped": _summary_count(counts.get("skipped")),
                        "rolled_back": _summary_count(counts.get("rolled_back")),
                        "linked_assets": counts.get("linked_assets", 0),
                        "dry_run": dry_run,
                        "error_details": [
                            {"kind": "record", "record": e.get("line"), "message": str(e.get("error") or "")[:500]}
                            for e in (counts.get("errors") or []) if isinstance(e, dict)
                        ] + [
                            {"kind": "asset", "record": e.get("line"), "message": str(e.get("error") or "")[:500]}
                            for e in (counts.get("asset_errors") or []) if isinstance(e, dict)
                        ],
                    }
                    file_summary.update(_asset_delta(conn, before_asset_id))
                    summaries.append(file_summary)
                    file_inserted = _summary_count(counts.get("inserted"))
                    file_updated = _summary_count(counts.get("updated"))
                    file_assets = counts.get("linked_assets", 0) if isinstance(counts.get("linked_assets"), int) else _summary_count(counts.get("linked_assets"))
                    file_record_errors = len(counts.get("errors") or [])
                    file_asset_errors = len(counts.get("asset_errors") or [])
                    _completed_records += file_inserted + file_updated
                    _completed_inserted += file_inserted
                    _completed_updated += file_updated
                    _completed_assets += file_assets
                    _completed_record_errors += file_record_errors
                    _completed_asset_errors += file_asset_errors
                    event_sink({
                        "event": "file_done", "file": filename,
                        "file_index": file_index, "file_count": file_count,
                    })
                finally:
                    if owns_tmp:
                        tmp_path.unlink(missing_ok=True)

        if not dry_run:
            event_sink({"event": "phase", "phase": "assets", "label": "Recovering assets…", "current_step": 2, "total_steps": 5, "percent": 60})
            recovery = recover_missing_assets(conn, Path(current_app.config["ASSETS_DIR"]))
            if recovery.get("recovered", 0):
                current_app.logger.info("Post-import asset recovery: %d recovered", recovery["recovered"])
            if recovery.get("failed"):
                current_app.logger.warning("Post-import asset recovery: %d still failed", len(recovery["failed"]))
            summaries.append({"asset_recovery": recovery})
            event_sink({
                "event": "metrics",
                "records": _completed_records,
                "total_records": _total_records_aggregate or _completed_records,
                "assets_linked": _completed_assets,
                "errors": _completed_record_errors + _completed_asset_errors,
                "asset_errors": _completed_asset_errors,
                "record_errors": _completed_record_errors,
                "inserted": _completed_inserted,
                "updated": _completed_updated,
            })

    event_sink({"event": "phase", "phase": "result", "label": "Finalizing…", "current_step": 3, "total_steps": 5, "percent": 80})

    total_inserted = sum(_summary_count(s.get("inserted")) for s in summaries)
    total_updated = sum(_summary_count(s.get("updated")) for s in summaries)
    total_errors = sum(_summary_count(s.get("errors")) for s in summaries)
    total_asset_errors = sum(_summary_count(s.get("asset_errors")) for s in summaries)
    total_skipped = sum(_summary_count(s.get("skipped")) for s in summaries)
    total_rolled_back = sum(_summary_count(s.get("rolled_back")) for s in summaries)
    total_linked_assets = sum(_summary_count(s.get("linked_assets")) for s in summaries)
    total_new_assets = sum(int(s.get("new_assets") or 0) for s in summaries)
    total_downloaded_assets = (
        sum(int(s.get("downloaded_assets") or 0) for s in summaries)
        + sum(int((s.get("asset_recovery") or {}).get("recovered") or 0) for s in summaries)
    )
    total_external_assets = sum(int(s.get("external_assets") or 0) for s in summaries)
    error_details = _summary_error_details(summaries)

    ok = total_errors == 0 and total_asset_errors == 0
    outcome = "success" if ok else ("warning" if total_errors > 0 or total_asset_errors > 0 else "failed")
    # Summarize inserted/updated by entity type
    by_type: dict[str, dict[str, int]] = {}
    for s in summaries:
        for action in ("inserted", "updated"):
            raw = s.get(action, {})
            if isinstance(raw, dict):
                for typ, count in raw.items():
                    by_type.setdefault(typ, {})[action] = by_type.get(typ, {}).get(action, 0) + (count if isinstance(count, int) else _summary_count(count))
    for s in summaries:
        if isinstance(s.get("inserted"), int):
            for typ, count in s.get("by_type", {}).items():
                by_type.setdefault(typ, {}).update(count)

    duration_s = round(time.monotonic() - started, 1)

    _result_title = (
        "Import completed with warnings" if outcome == "warning" else
        "Files are valid" if (ok and dry_run) else
        "Import complete" if ok else
        "Import failed"
    )
    _result_icon = "bi-exclamation-lg" if outcome == "warning" else "bi-check-lg" if ok else "bi-x-lg"
    _result_icon_class = "is-warning" if outcome == "warning" else "is-success" if ok else "is-error"

    event_sink({
        "event": "result",
        "ok": ok,
        "outcome": outcome,
        "title_text": _result_title,
        "icon_class": _result_icon,
        "icon_modifier": _result_icon_class,
        "message": f"Import completed in {duration_s}s." if ok else f"Import completed with {total_errors + total_asset_errors} error(s).",
        "inserted": total_inserted,
        "updated": total_updated,
        "linked_assets": total_linked_assets,
        "errors": total_errors,
        "asset_errors": total_asset_errors,
        "skipped": total_skipped,
        "rolled_back": total_rolled_back,
        "new_assets": total_new_assets,
        "downloaded_assets": total_downloaded_assets,
        "external_assets": total_external_assets,
        "by_type": by_type,
        "error_details": error_details[:20] if error_details else [],
        "dry_run": dry_run,
        "duration_s": duration_s,
        # Do not disclose internal server paths in the streamed browser result.
        "backup_created": bool(backup_path),
    })

    audit_log(
        "import.data_portability", "data portability import", category="admin",
        outcome=outcome, scope=scope, dry_run=dry_run,
        files=len(file_data), inserted=total_inserted, updated=total_updated,
        errors=total_errors, asset_errors=total_asset_errors,
        duration_s=duration_s,
    )


def _import_postback(
    scope: str, files: list, dry_run: bool, skip_assets: bool, started: float, *, force_import: bool = False
) -> Response:
    summaries = []
    backup_path = None
    is_zip_import = any(f.filename.lower().endswith(".zip") for f in files)
    if not dry_run:
        current_app.logger.info("Creating pre-import database backup")
        backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label="import")
        current_app.logger.info(
            "pre-import backup ready created=%s", bool(backup_path)
        )
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        zip_files = [f for f in files if f.filename.lower().endswith(".zip")]
        if zip_files:
            if len(files) > 1:
                flash("Import one ZIP bundle at a time.", "error")
                return redirect(url_for("dashboard.data_portability"))  # type: ignore[return-value]
            file = zip_files[0]
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".zip", delete=False, dir=current_app.config.get("TMP_DIR") or None
            ) as f:
                file.save(f)
                zip_path = Path(f.name)
            current_app.logger.info("importing ZIP index=1 bytes=%d", zip_path.stat().st_size)
            try:
                before_asset_id = _max_asset_id(conn)
                summary = _import_zip_dispatch(
                    conn, zip_path, scope,
                    dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
                    progress=lambda done, total: current_app.logger.info("ZIP import progress: records=%d/%d", done, total),
                    source_name=file.filename,
                )
            finally:
                zip_path.unlink(missing_ok=True)
            summary["filename"] = file.filename
            raw_errors = summary.get("errors")
            error_details = []
            if isinstance(raw_errors, list):
                for e in raw_errors:
                    if isinstance(e, dict):
                        msg = (e.get("error") or "").strip()
                        if msg:
                            error_details.append({"kind": "record", "record": e.get("line"), "message": msg[:500]})
            elif isinstance(raw_errors, dict):
                for v in raw_errors.values():
                    msg = str(v).strip()
                    if msg:
                        error_details.append({"kind": "record", "message": msg[:500]})
            raw_asset_errors = summary.get("asset_errors")
            if isinstance(raw_asset_errors, list):
                for e in raw_asset_errors:
                    if isinstance(e, dict):
                        msg = str(e.get("error") or "").strip()
                        if msg:
                            error_details.append({"kind": "asset", "record": e.get("line"), "message": msg[:500]})
            summary["error_details"] = error_details
            summary["errors"] = _summary_count(summary.get("errors"))
            summary["asset_errors"] = _summary_count(summary.get("asset_errors"))
            summary["linked_assets"] = _summary_count(summary.get("linked_assets"))
            summary.update(_asset_delta(conn, before_asset_id))
            summaries.append(summary)
        else:
            for file in files:
                file_index = files.index(file) + 1
                suffix = Path(file.filename).suffix or ".jsonl"
                with tempfile.NamedTemporaryFile(
                    mode='wb', suffix=suffix, delete=False,
                    dir=current_app.config.get("TMP_DIR") or None,
                ) as f:
                    file.save(f)
                    tmp_path = Path(f.name)
                current_app.logger.info(
                    "importing JSONL index=%d bytes=%d", file_index, tmp_path.stat().st_size
                )
                try:
                    before_asset_id = _max_asset_id(conn)
                    counts = import_jsonl(
                        conn, tmp_path,
                        dry_run=dry_run, force_import=force_import,
                        assets_dir=current_app.config["ASSETS_DIR"],
                        progress=lambda done, total: current_app.logger.info(
                            "JSONL import progress index=%d records=%d/%d",
                            file_index,
                            done,
                            total,
                        ),
                        asset_detail=lambda _msg: None,
                        source_name=file.filename,
                    )
                    file_summary = {
                        "filename": file.filename,
                        "inserted": counts.get("inserted"),
                        "updated": counts.get("updated"),
                        "errors": _summary_count(counts.get("errors")),
                        "asset_errors": _summary_count(counts.get("asset_errors")),
                        "skipped": _summary_count(counts.get("skipped")),
                        "rolled_back": _summary_count(counts.get("rolled_back")),
                        "linked_assets": counts.get("linked_assets", 0),
                        "dry_run": dry_run,
                        "error_details": [
                            {"kind": "record", "record": e.get("line"), "message": str(e.get("error") or "")[:500]}
                            for e in (counts.get("errors") or []) if isinstance(e, dict)
                        ] + [
                            {"kind": "asset", "record": e.get("line"), "message": str(e.get("error") or "")[:500]}
                            for e in (counts.get("asset_errors") or []) if isinstance(e, dict)
                        ],
                    }
                    file_summary.update(_asset_delta(conn, before_asset_id))
                    summaries.append(file_summary)
                finally:
                    tmp_path.unlink(missing_ok=True)
        if not dry_run:
            recovery = recover_missing_assets(conn, Path(current_app.config["ASSETS_DIR"]))
            summaries.append({"asset_recovery": recovery})
            if recovery.get("recovered", 0):
                current_app.logger.info("Post-import recovery downloaded %d assets", recovery["recovered"])
            if recovery.get("failed"):
                current_app.logger.warning("Post-import recovery deferred/closed %d assets", len(recovery["failed"]))
    total_inserted = sum(_summary_count(s.get("inserted")) for s in summaries)
    total_updated = sum(_summary_count(s.get("updated")) for s in summaries)
    total_errors = sum(_summary_count(s.get("errors")) for s in summaries)
    total_asset_errors = sum(_summary_count(s.get("asset_errors")) for s in summaries)
    total_skipped = sum(_summary_count(s.get("skipped")) for s in summaries)
    total_rolled_back = sum(_summary_count(s.get("rolled_back")) for s in summaries)
    total_linked_assets = sum(_summary_count(s.get("linked_assets")) for s in summaries)
    total_new_assets = sum(int(s.get("new_assets") or 0) for s in summaries)
    total_downloaded_assets = (
        sum(int(s.get("downloaded_assets") or 0) for s in summaries)
        + sum(int((s.get("asset_recovery") or {}).get("recovered") or 0) for s in summaries)
    )
    total_external_assets = sum(int(s.get("external_assets") or 0) for s in summaries)
    error_details = _summary_error_details(summaries)

    by_type: dict[str, dict[str, int]] = {}
    for s in summaries:
        for action in ("inserted", "updated"):
            raw = s.get(action, {})
            if isinstance(raw, dict):
                for typ, count in raw.items():
                    by_type.setdefault(typ, {})[action] = by_type.get(typ, {}).get(action, 0) + (count if isinstance(count, int) else _summary_count(count))

    ok = total_errors == 0 and total_asset_errors == 0
    outcome = "success" if ok else ("warning" if total_errors > 0 or total_asset_errors > 0 else "failed")
    duration_s = round(time.monotonic() - started, 1)

    _result_title = (
        "Import completed with warnings" if outcome == "warning" else
        "Files are valid" if (ok and dry_run) else
        "Import complete" if ok else
        "Import failed"
    )

    import_result = {
        "ok": ok, "outcome": outcome, "title_text": _result_title,
        "message": f"Imported {total_inserted} new + {total_updated} updated records. "
                   f"Errors: {total_errors} (record) + {total_asset_errors} (asset).",
        "inserted": total_inserted, "updated": total_updated,
        "linked_assets": total_linked_assets,
        "errors": total_errors, "asset_errors": total_asset_errors,
        "skipped": total_skipped, "rolled_back": total_rolled_back,
        "new_assets": total_new_assets,
        "downloaded_assets": total_downloaded_assets,
        "external_assets": total_external_assets,
        "by_type": by_type,
        "error_details": error_details[:20],
        "dry_run": dry_run, "duration_s": duration_s,
        "backup_path": str(backup_path) if backup_path else None,
    }
    if not dry_run:
        session["data_portability_import_result"] = import_result

    flash(
        f"Imported {total_inserted} new + {total_updated} updated records with "
        f"{total_linked_assets} asset(s). Errors: {total_errors} (record) + {total_asset_errors} (asset).",
        "success" if ok else "warning",
    )
    audit_log(
        "import.data_portability", "data portability import", category="admin",
        outcome=outcome, scope=scope, dry_run=dry_run,
        files=len(files), inserted=total_inserted, updated=total_updated,
        errors=total_errors, asset_errors=total_asset_errors,
        duration_s=duration_s,
    )
    return redirect(url_for("dashboard.data_portability"))  # type: ignore[return-value]


def _max_asset_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM assets").fetchone()
    return int(row["max_id"] if isinstance(row, sqlite3.Row) else row[0])


def _asset_delta(conn: sqlite3.Connection, before_asset_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS new_assets,
            SUM(CASE WHEN COALESCE(is_external,0)=0 AND COALESCE(storage_status,'local')='local' THEN 1 ELSE 0 END) AS downloaded_assets,
            SUM(CASE WHEN COALESCE(is_external,0)=1 THEN 1 ELSE 0 END) AS external_assets
        FROM assets
        WHERE id > ?
        """,
        (before_asset_id,),
    ).fetchone()
    return {
        "new_assets": int(row["new_assets"] or 0),
        "downloaded_assets": int(row["downloaded_assets"] or 0),
        "external_assets": int(row["external_assets"] or 0),
    }


def _summary_error_details(summaries: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for summary in summaries:
        filename = summary.get("filename")
        for item in summary.get("error_details", []) or []:
            if len(details) >= limit:
                return details
            details.append({**item, "filename": filename})
        missing = int(summary.get("errors") or 0) + int(summary.get("asset_errors") or 0) - len(summary.get("error_details", []) or [])
        if missing > 0 and len(details) < limit:
            details.append({
                "kind": "summary",
                "filename": filename,
                "message": f"{missing} additional error(s) were reported without detail. Restart the webapp and rerun the import if this came from an older request.",
            })
    return details


def _summary_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_summary_count(item) for item in value.values())
    if isinstance(value, list):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
    if redirect_url and _safe_relative_redirect(redirect_url):
        return redirect(redirect_url)
    return redirect(url_for("dashboard.server"))


def _safe_relative_redirect(redirect_url: str) -> bool:
    """Accept only same-origin path redirects.

    A bare ``startswith("/")`` check lets an attacker supply ``/\\evil.example``
    (browsers treat backslashes as forward slashes, yielding ``//evil.example``,
    a scheme-relative open redirect). Reject anything with a scheme, a netloc,
    or a backslash.
    """
    parsed = urlsplit(redirect_url)
    if parsed.scheme or parsed.netloc or "\\" in redirect_url:
        return False
    return parsed.path.startswith("/")


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
    if not expected_hash or not check_password_hash(expected_hash, password):
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
    return redirect(request.referrer or url_for("dashboard.server"))


# ---------------------------------------------------------------------------
# Join Requests
# ---------------------------------------------------------------------------

JOIN_STATUSES = {"pending", "in_review", "approved", "rejected", "archived"}


@bp.get("/join-requests")
@login_required
def join_requests():
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    where = []
    params = []
    if status in JOIN_STATUSES:
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR affiliation LIKE ? OR field LIKE ?)")
        params.extend([f"%{q}%"] * 5)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        order = "CASE status WHEN 'pending' THEN 0 WHEN 'in_review' THEN 1 WHEN 'approved' THEN 2 WHEN 'rejected' THEN 3 ELSE 4 END, created_at DESC"
        count_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM join_requests{clause}", params).fetchone()
        total_filtered = int(count_row["cnt"]) if count_row else 0
        total_pages = max(1, (total_filtered + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page
        requests_list = [dict(r) for r in conn.execute(
            f"""
            SELECT * FROM join_requests
            {clause}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, offset),
        ).fetchall()]
        counts = {r["status"]: r["total"] for r in conn.execute("SELECT status, COUNT(*) AS total FROM join_requests GROUP BY status").fetchall()}
    return render_template(
        "dashboard/join_requests.html",
        requests=requests_list,
        counts=counts,
        current_status=status,
        q=q,
        pagination={"page": page, "total_pages": total_pages, "total_filtered": total_filtered, "per_page": per_page},
    )


def _join_return():
    return redirect(url_for("dashboard.join_requests", status=request.form.get("return_status", ""), q=request.form.get("return_q", "")))


@bp.post("/join-requests/<int:request_id>/update")
@login_required
def join_update(request_id: int):
    status = request.form.get("status", "").strip()
    if status not in JOIN_STATUSES:
        status = "pending"
    admin_notes = request.form.get("admin_notes", "").strip()[:4000]
    decision_note = request.form.get("decision_note", "").strip()[:2000]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            """
            UPDATE join_requests
            SET status=?, admin_notes=?, decision_note=?, reviewed_at=CASE WHEN ? IN ('approved','rejected','archived') THEN CURRENT_TIMESTAMP ELSE reviewed_at END,
                reviewed_by=?
            WHERE id=?
            """,
            (status, admin_notes or None, decision_note or None, status, session.get("admin_username"), request_id),
        )
        conn.commit()
    audit_log("join.update", "join request updated", request_id=request_id, status=status)
    flash("Join request updated.", "success")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/approve")
@login_required
def join_approve(request_id: int):
    create_member = request.form.get("create_member") == "1"
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        row = conn.execute("SELECT * FROM join_requests WHERE id=?", (request_id,)).fetchone()
        member_id = row["member_id"] if row else None
        if row and create_member and not member_id:
            cur = conn.execute(
                """
                INSERT INTO members(first_name,last_name,display_name,email,affiliation,country,field,bio,is_active,review_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,1,'published',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                """,
                (
                    row["first_name"], row["last_name"], f"{row['first_name']} {row['last_name']}", row["email"],
                    row["affiliation"], row["country"], row["field"], row["motivation"]
                ),
            )
            member_id = cur.lastrowid
        conn.execute(
            "UPDATE join_requests SET status='approved', member_id=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
            (member_id, session.get("admin_username"), request_id),
        )
        conn.commit()
    audit_log("join.approve", "join request approved", request_id=request_id, create_member=create_member)
    flash("Join request approved." + (" Member created." if create_member else ""), "success")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/reject")
@login_required
def join_reject(request_id: int):
    decision_note = request.form.get("decision_note", "").strip()[:2000]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "UPDATE join_requests SET status='rejected', decision_note=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
            (decision_note or None, session.get("admin_username"), request_id),
        )
        conn.commit()
    audit_log("join.reject", "join request rejected", request_id=request_id)
    flash("Join request rejected.", "warning")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/archive")
@login_required
def join_archive(request_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute("UPDATE join_requests SET status='archived', reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?", (session.get("admin_username"), request_id))
        conn.commit()
    audit_log("join.archive", "join request archived", request_id=request_id)
    flash("Join request archived.", "success")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/delete")
@login_required
def join_delete(request_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute("DELETE FROM join_requests WHERE id=?", (request_id,))
        conn.commit()
    audit_log("join.delete", "join request deleted", request_id=request_id)
    flash("Join request deleted.", "success")
    return _join_return()


from . import (  # noqa: E402,F401
    dashboard_assets,
    dashboard_conferences,
    dashboard_content,
    dashboard_control,
    dashboard_data_quality,
    dashboard_logs,
)

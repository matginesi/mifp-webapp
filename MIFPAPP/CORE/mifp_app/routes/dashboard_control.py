from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

from ..db.connection import connect
from ..services.admin_safety import backup_sqlite_database
from ..services.control_center import (
    asset_health,
    backup_inventory,
    content_quality_checks,
    data_quality_workflow_summary,
    global_search,
    incident_groups,
    link_hygiene,
    process_activity,
    safe_settings,
    storage_health,
    verify_backup,
)
from ..services.dashboard_repository import search_logs
from ..services.data_portability import bundle_to_zip
from ..services.job_manager import get_job_manager
from ..services.operation_maintenance import operation_maintenance
from ..services.safety_operations import execute_safe_cleanup, safety_operations_preview
from ..services.site_copy import copy_groups
from ..utils.logger import audit_log
from ..utils.security import get_client_ip
from .auth import login_required
from .dashboard import bp


def _content_action(section: str) -> str:
    if section == "events":
        return url_for("dashboard.events")
    if section == "pages":
        return url_for("dashboard.site_texts")
    return url_for("dashboard.content", section=section)


def _quality_context(conn):
    checks = content_quality_checks(conn)
    for check in checks:
        check["action_url"] = _content_action(check["section"])
    return checks


def _site_readiness(conn) -> list[dict]:
    endpoints = (
        ("Homepage", "public.home"),
        ("Members", "public.members"),
        ("News", "public.news"),
        ("Events", "public.events"),
        ("Publications", "public.publications"),
        ("Research areas", "public.research"),
        ("Sponsors", "public.sponsors"),
        ("Privacy policy", "public.privacy"),
        ("Sitemap", "public.sitemap_xml"),
        ("Health endpoint", "health"),
        ("Readiness endpoint", "ready"),
    )
    registered = {rule.endpoint for rule in current_app.url_map.iter_rules()}
    checks = [
        {
            "label": label,
            "status": "ok" if endpoint in registered else "error",
            "detail": "Route registered" if endpoint in registered else "Route is missing",
        }
        for label, endpoint in endpoints
    ]
    db_ok = False
    try:
        db_ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except Exception:
        current_app.logger.exception("control centre database quick check failed")
    checks.append(
        {
            "label": "Public database",
            "status": "ok" if db_ok else "error",
            "detail": "SQLite quick check passed" if db_ok else "SQLite quick check failed",
        }
    )
    static_folder = Path(current_app.static_folder or "")
    for label, relative in (
        ("Dashboard stylesheet", "css/dashboard.css"),
        ("Public stylesheet", "css/homepage.css"),
        ("MIFP logo", "img/logo-mifp.png"),
        ("Bootstrap", "css/vendor/bootstrap.min.css"),
    ):
        present = (static_folder / relative).is_file()
        checks.append(
            {
                "label": label,
                "status": "ok" if present else "error",
                "detail": f"static/{relative}" if present else f"Missing static/{relative}",
            }
        )
    return checks


@bp.get("/control")
@login_required
def control_center():
    cfg = current_app.config
    with connect(cfg["DATABASE_PATH"]) as conn:
        quality = _quality_context(conn)
        quality_workflow = data_quality_workflow_summary(conn)
        assets = asset_health(conn, Path(cfg["ASSETS_DIR"]), scan_orphans=False)
        processes = process_activity(conn)
        pending_join = conn.execute(
            "SELECT COUNT(*) FROM join_requests WHERE status IN ('pending','in_review')"
        ).fetchone()[0]

    storage = storage_health(cfg, scan_sizes=False)
    backups = backup_inventory(Path(cfg["DATABASE_PATH"]))
    recent_errors = search_logs(cfg["LOG_DIR"], q=None, level="ERROR", limit=100)
    attention: list[dict] = []

    def add(code, severity, label, detail, count, action_url):
        if int(count or 0) <= 0:
            return
        attention.append(
            {
                "code": code,
                "severity": severity,
                "label": label,
                "detail": detail,
                "count": int(count),
                "action_url": action_url,
            }
        )

    add(
        "recent_errors",
        "danger",
        "Recent application errors",
        "Errors found in the current log window.",
        len(recent_errors),
        url_for("dashboard.logs", level="ERROR"),
    )
    add(
        "missing_assets",
        "danger",
        "Missing local asset files",
        "Database records point to files that are not available locally.",
        assets["totals"]["missing"] + assets["totals"]["unsafe"],
        url_for("dashboard.control_assets"),
    )
    add(
        "unused_assets",
        "warning",
        "Database assets not currently used",
        "Review usage before any manual cleanup.",
        assets["totals"]["unused"],
        url_for("dashboard.control_assets"),
    )
    add(
        "pending_join",
        "warning",
        "Join requests awaiting a decision",
        "Applications are pending or under review.",
        pending_join,
        url_for("dashboard.join_requests"),
    )
    quality_total = (
        sum(item["count"] for item in quality if item["severity"] == "warning")
        + quality_workflow["open"]
    )
    add(
        "quality",
        "warning",
        "Editorial checks requiring attention",
        "Open the quality page to work through the affected records.",
        quality_total,
        url_for("dashboard.control_quality"),
    )
    failed_imports = sum(1 for item in processes["imports"] if item["status"] in {"failed", "completed_with_errors"})
    add(
        "imports",
        "warning",
        "Imports completed with problems",
        "Review the run summaries before retrying source data.",
        failed_imports,
        url_for("dashboard.control_processes"),
    )
    unsafe_storage = sum(
        1
        for item in storage["items"]
        if not item["exists"] or not item["readable"] or not item["writable"] or item["free_percent"] < 10
    )
    add(
        "storage",
        "danger",
        "Runtime storage warnings",
        "A configured path is unavailable, read-only or low on space.",
        unsafe_storage,
        url_for("dashboard.control_storage"),
    )
    backup_warning = 1 if not backups["latest"] or backups["latest"]["age_hours"] > 168 else 0
    add(
        "backup",
        "warning",
        "No recent verified database snapshot",
        "No local SQLite backup was found in the last seven days.",
        backup_warning,
        url_for("dashboard.control_backups"),
    )
    severity_order = {"danger": 0, "warning": 1, "info": 2}
    attention.sort(key=lambda item: (severity_order.get(item["severity"], 3), -item["count"]))
    return render_template(
        "dashboard/control/index.html",
        attention=attention,
        assets=assets,
        processes=processes,
        storage=storage,
        backups=backups,
    )


@bp.get("/control/processes")
@login_required
def control_processes():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        activity = process_activity(conn)
    jobs = get_job_manager(
        int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
        int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
        db_path=current_app.config["DATABASE_PATH"],
    ).snapshot()
    return render_template("dashboard/control/processes.html", activity=activity, jobs=jobs)


@bp.get("/control/quality")
@login_required
def control_quality():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        checks = _quality_context(conn)
        workflow = data_quality_workflow_summary(conn)
    return render_template(
        "dashboard/control/quality.html",
        checks=checks,
        workflow=workflow,
    )


@bp.get("/control/assets")
@login_required
def control_assets():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        health = asset_health(conn, Path(current_app.config["ASSETS_DIR"]))
    return render_template("dashboard/control/assets.html", health=health)


@bp.get("/control/storage")
@login_required
def control_storage():
    health = storage_health(current_app.config)
    return render_template("dashboard/control/storage.html", health=health)


@bp.get("/control/site")
@login_required
def control_site():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        checks = _site_readiness(conn)
        links = link_hygiene(conn)
        maintenance_rows = conn.execute(
            "SELECT key,value FROM settings WHERE key IN "
            "('maintenance_enabled','maintenance_message')"
        ).fetchall()
    maintenance = {str(row["key"]): str(row["value"] or "") for row in maintenance_rows}
    security = {
        "csrf": bool(current_app.config.get("WTF_CSRF_ENABLED", True)),
        "secure_cookie": bool(current_app.config.get("SESSION_COOKIE_SECURE")),
        "proxy_trust": bool(current_app.config.get("TRUST_PROXY")),
        "debug": bool(current_app.debug),
        "environment": current_app.config.get("ENV", "development"),
        "csp": True,
        "trusted_hosts": bool(current_app.config.get("TRUSTED_HOSTS")),
        "audit_log": bool(current_app.config.get("LOG_AUDIT_ENABLED", True)),
        "security_log": bool(current_app.config.get("LOG_SECURITY_ENABLED", True)),
        "ip_privacy": not bool(current_app.config.get("LOG_INCLUDE_CLIENT_IP", False)),
        "db_dump_restricted": not bool(current_app.config.get("ALLOW_DB_DUMP", False)),
    }
    return render_template(
        "dashboard/control/site.html",
        checks=checks,
        security=security,
        links=links,
        maintenance=maintenance,
    )


@bp.post("/control/site/maintenance")
@login_required
def control_site_maintenance():
    from ..services.operation_maintenance import clear_stale_operation_marker

    enabled = request.form.get("maintenance_enabled") == "1"
    message = request.form.get("maintenance_message", "").strip()[:300]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        values = {
            "maintenance_enabled": "1" if enabled else "0",
            "maintenance_message": message,
        }
        conn.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            values.items(),
        )
        # Remove the obsolete second-password credential from older installs.
        conn.execute("DELETE FROM settings WHERE key='maintenance_password_hash'")
        conn.commit()
    if not enabled:
        clear_stale_operation_marker(
            current_app.config["DATABASE_PATH"],
            logger=current_app.logger,
        )
    audit_log(
        "maintenance.settings",
        "work in progress mode updated",
        outcome="success",
        enabled=enabled,
    )
    flash("Work in progress mode updated.", "success")
    return redirect(url_for("dashboard.control_site") + "#maintenance-mode")


@bp.get("/control/incidents")
@login_required
def control_incidents():
    groups = incident_groups(Path(current_app.config["LOG_DIR"]))
    return render_template("dashboard/control/incidents.html", groups=groups)


@bp.get("/control/backups")
@login_required
def control_backups():
    inventory = backup_inventory(Path(current_app.config["DATABASE_PATH"]))
    return render_template("dashboard/control/backups.html", inventory=inventory, verification=None)


@bp.get("/control/safety-operations")
@login_required
def control_safety_operations():
    return render_template(
        "dashboard/control/safety_operations.html",
        preview=safety_operations_preview(current_app.config),
    )


@bp.post("/control/safety-operations/run")
@login_required
def control_safety_operations_run():
    operation = request.form.get("operation", "").strip()
    allowed = {"backup", "export", "cleanup"}
    if operation not in allowed:
        flash("Select a valid protected operation.", "error")
        return redirect(url_for("dashboard.control_safety_operations"))

    password = request.form.get("password", "")
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")
    identity = {
        "username": session.get("admin_username"),
        "ip": get_client_ip(),
        "operation": operation,
    }
    if not expected_hash or not check_password_hash(expected_hash, password):
        audit_log(
            "safety_operation.denied",
            "protected safety operation denied",
            category="security",
            outcome="denied",
            **identity,
        )
        flash("Password verification failed. No operation was performed.", "error")
        return redirect(url_for("dashboard.control_safety_operations"))
    if request.form.get("acknowledge") != "1":
        flash("Review and acknowledge the operation before continuing.", "error")
        return redirect(url_for("dashboard.control_safety_operations"))
    if operation == "cleanup" and request.form.get("confirmation", "").strip() != "CLEAN STORAGE":
        flash("Type CLEAN STORAGE exactly to authorize cleanup.", "error")
        return redirect(url_for("dashboard.control_safety_operations"))

    try:
        if operation == "backup":
            path = backup_sqlite_database(
                Path(current_app.config["DATABASE_PATH"]),
                label="manual-wizard",
                reserve_bytes=int(current_app.config.get("STORAGE_MIN_FREE_BYTES", 0)),
            )
            if path is None:
                raise RuntimeError("Database is unavailable")
            audit_log(
                "safety_operation.backup",
                "verified database snapshot created",
                category="admin",
                outcome="success",
                filename=path.name,
                **identity,
            )
            flash(f"Verified database snapshot created: {path.name}", "success")
            # Land on the inventory and independently verify the newly created
            # file, instead of resetting the wizard with no visible result.
            return redirect(url_for("dashboard.control_backup_verify", filename=path.name))

        if operation == "export":
            with operation_maintenance(
                current_app.config["DATABASE_PATH"],
                "protected portable export",
                logger=current_app.logger,
            ), connect(Path(current_app.config["DATABASE_PATH"])) as conn:
                payload = bundle_to_zip(
                    conn,
                    "all",
                    Path(current_app.config["ASSETS_DIR"]),
                    app_version=str(current_app.config.get("APP_VERSION", "")),
                )
            audit_log(
                "safety_operation.export",
                "protected portable export created",
                category="admin",
                outcome="success",
                bytes=len(payload),
                **identity,
            )
            response = send_file(
                BytesIO(payload),
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"mifp-secure-export-{date.today().isoformat()}.zip",
                max_age=0,
            )
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            return response

        with operation_maintenance(
            current_app.config["DATABASE_PATH"],
            "storage and database cleanup",
            logger=current_app.logger,
        ):
            report = execute_safe_cleanup(current_app.config)
        audit_log(
            "safety_operation.cleanup",
            "protected storage and database cleanup completed",
            category="admin",
            outcome="success",
            **report,
            **identity,
        )
        flash(
            "Cleanup completed safely: "
            f"{report['exports_removed']} export(s), "
            f"{report['backups_removed']} old backup(s), "
            f"{report['metrics_deleted']} expired metric row(s).",
            "success",
        )
    except Exception:
        current_app.logger.exception("Protected safety operation failed: %s", operation)
        audit_log(
            "safety_operation.failed",
            "protected safety operation failed",
            category="admin",
            outcome="failure",
            **identity,
        )
        flash("The protected operation failed. No unsafe retry was attempted; review the logs.", "error")
    return redirect(url_for("dashboard.control_safety_operations"))


@bp.get("/control/backups/verify")
@login_required
def control_backup_verify():
    filename = request.args.get("filename", "")
    try:
        result = verify_backup(Path(current_app.config["DATABASE_PATH"]), filename)
    except (ValueError, FileNotFoundError):
        flash("The selected backup could not be verified.", "error")
        return redirect(url_for("dashboard.control_backups"))
    except Exception:
        current_app.logger.exception("backup verification failed")
        flash("Backup verification failed. Check the server log.", "error")
        return redirect(url_for("dashboard.control_backups"))
    inventory = backup_inventory(Path(current_app.config["DATABASE_PATH"]))
    return render_template(
        "dashboard/control/backups.html",
        inventory=inventory,
        verification=result,
    )


@bp.get("/control/settings")
@login_required
def control_settings():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        rows = conn.execute("SELECT key,value FROM settings ORDER BY key").fetchall()
    database_settings = {str(row["key"]): str(row["value"] or "") for row in rows}
    settings = safe_settings(current_app.config, database_settings)
    return render_template("dashboard/control/settings.html", settings=settings)


@bp.get("/site-texts")
@login_required
def site_texts():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        rows = conn.execute("SELECT key,value FROM settings ORDER BY key").fetchall()
    settings = {str(row["key"]): str(row["value"] or "") for row in rows}
    groups = copy_groups(settings)
    return render_template(
        "dashboard/site_texts.html",
        copy_groups=groups,
        copy_total=sum(len(group["fields"]) for group in groups),
    )


@bp.get("/search")
@login_required
def dashboard_search():
    query = request.args.get("q", "").strip()[:120]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        results = global_search(conn, query)
    for result in results:
        section = result["section"]
        if section == "events":
            result["url"] = url_for("dashboard.events", q=result["title"])
        elif section == "pages":
            result["url"] = url_for("dashboard.site_texts")
        elif section == "assets":
            result["url"] = url_for("dashboard.assets_page", q=result["title"])
        else:
            result["url"] = url_for("dashboard.content", section=section, q=result["title"])
    return render_template("dashboard/control/search.html", query=query, results=results)

from __future__ import annotations

import time
from threading import Lock

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from ..db.connection import connect_readonly
from ..services.operation_maintenance import (
    clear_stale_operation_marker,
    maintenance_marker_path,
)

bp = Blueprint("maintenance", __name__)

_OPPORTUNISTIC_REAP_INTERVAL = 30.0
_last_reap_attempt = 0.0
_reap_lock = Lock()


def _opportunistic_reap() -> bool:
    """Reap a crashed operation at most once per interval.

    Mirrors the startup recovery: reaps an orphaned protected operation (owning
    PID gone and past the crash timeout) and removes a stray marker with no
    active operation. A live operation or a manual Work in Progress page is
    never disturbed.
    """
    global _last_reap_attempt
    now = time.monotonic()
    with _reap_lock:
        if now - _last_reap_attempt < _OPPORTUNISTIC_REAP_INTERVAL:
            return False
        _last_reap_attempt = now
    return clear_stale_operation_marker(
        current_app.config["DATABASE_PATH"],
        logger=current_app.logger,
    )


def _settings() -> dict[str, str]:
    try:
        with connect_readonly(current_app.config["DATABASE_PATH"]) as conn:
            rows = conn.execute(
                "SELECT key,value FROM settings WHERE key IN "
                "('maintenance_enabled','maintenance_message')"
            ).fetchall()
        return {str(row["key"]): str(row["value"] or "") for row in rows}
    except Exception:
        current_app.logger.exception("maintenance settings could not be read")
        return {}


def maintenance_gate():
    """Replace the public site with one page while keeping operations reachable."""
    if request.path.startswith((
        "/static/", "/media/", "/health", "/ready", "/login", "/logout",
        "/dashboard/", "/work-in-progress",
    )):
        return None
    marker = maintenance_marker_path(current_app.config["DATABASE_PATH"])
    if marker.is_file() and not marker.is_symlink():
        if _opportunistic_reap():
            marker = maintenance_marker_path(current_app.config["DATABASE_PATH"])
            if not marker.is_file():
                return None
        try:
            message = marker.read_text(encoding="utf-8")[:300]
        except OSError:
            message = "Secure maintenance in progress. Please try again shortly."
        g.maintenance_active = True
        return render_template("public/work_in_progress.html", message=message), 503
    settings = _settings()
    if settings.get("maintenance_enabled") != "1":
        return None
    if _opportunistic_reap():
        settings = _settings()
        if settings.get("maintenance_enabled") != "1":
            return None
    g.maintenance_active = True
    return render_template(
        "public/work_in_progress.html",
        message=settings.get("maintenance_message", ""),
    ), 503


@bp.get("/work-in-progress")
def page():
    settings = _settings()
    if settings.get("maintenance_enabled") != "1":
        return redirect(url_for("public.home"))
    return render_template(
        "public/work_in_progress.html",
        message=settings.get("maintenance_message", ""),
    ), 503

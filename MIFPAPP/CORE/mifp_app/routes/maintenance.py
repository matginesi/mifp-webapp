from __future__ import annotations

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for

from ..db.connection import connect_readonly
from ..services.operation_maintenance import maintenance_marker_path

bp = Blueprint("maintenance", __name__)


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
        try:
            message = marker.read_text(encoding="utf-8")[:300]
        except OSError:
            message = "Secure maintenance in progress. Please try again shortly."
        g.maintenance_active = True
        return render_template("public/work_in_progress.html", message=message), 503
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

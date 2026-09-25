from __future__ import annotations

from flask import current_app, flash, redirect, render_template, request, url_for

from ..db.connection import connect
from ..services.seo import (
    SEO_SETTING_DEFAULTS,
    normalize_origin,
    preferred_origin,
    seo_dashboard_snapshot,
    settings_from_conn,
)
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import bp


@bp.get("/seo")
@login_required
def seo_page():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        settings = settings_from_conn(conn)
        origin = preferred_origin(settings)
        snapshot = seo_dashboard_snapshot(conn, settings, origin)
    return render_template(
        "dashboard/seo.html",
        seo_settings=settings,
        seo_defaults=SEO_SETTING_DEFAULTS,
        snapshot=snapshot,
    )


@bp.post("/seo")
@login_required
def seo_save():
    canonical_raw = str(request.form.get("seo_canonical_origin") or "").strip()
    canonical = normalize_origin(canonical_raw)
    if canonical_raw and not canonical:
        flash("Canonical origin must be an origin-only HTTP(S) URL, for example https://mifp.eu.", "error")
        return redirect(url_for("dashboard.seo_page"))

    verification = str(request.form.get("seo_google_site_verification") or "").strip()
    if len(verification) > 256 or any(ord(char) < 32 for char in verification):
        flash("Google verification token is invalid or too long.", "error")
        return redirect(url_for("dashboard.seo_page"))

    description = " ".join(str(request.form.get("seo_default_description") or "").split()).strip()
    if not description:
        description = SEO_SETTING_DEFAULTS["seo_default_description"]
    if len(description) > 320:
        flash("Default meta description must be 320 characters or fewer.", "error")
        return redirect(url_for("dashboard.seo_page"))

    values = {
        "seo_indexing_enabled": "1" if request.form.get("seo_indexing_enabled") == "1" else "0",
        "seo_canonical_origin": canonical,
        "seo_google_site_verification": verification,
        "seo_default_description": description,
    }
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                (key, value),
            )
        conn.commit()

    audit_log(
        "seo.settings_update",
        "SEO and indexing settings updated",
        category="configuration",
        indexing_enabled=values["seo_indexing_enabled"] == "1",
        canonical_origin=canonical or "request-host fallback",
        google_verification_configured=bool(verification),
    )
    flash("SEO and indexing settings saved.", "success")
    return redirect(url_for("dashboard.seo_page"))

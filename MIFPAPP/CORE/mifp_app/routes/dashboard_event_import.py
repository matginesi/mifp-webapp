from __future__ import annotations

import json
import shutil
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, send_file, url_for

from ..db.connection import connect
from ..services.event_import import (
    apply_import,
    cleanup_staging,
    create_staging,
    destination_url,
    detect_package,
    inspect_packages,
    resolve_staging,
    save_upload,
)
from ..utils.logger import audit_log
from ._shared import admin_error_text
from .auth import login_required
from .dashboard import bp


def _package_paths(stage: Path) -> tuple[Path | None, Path | None, dict]:
    meta_path = stage / "upload.json"
    if not meta_path.is_file() or meta_path.is_symlink():
        raise ValueError("Import staging metadata is unavailable.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    website = stage / "website.zip"
    info = stage / "info.zip"
    return (website if website.is_file() else None, info if info.is_file() else None, meta)


def _existing_destinations() -> list[str]:
    root = Path(current_app.config["EVENTS_ROOT"])
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        child.name for child in root.iterdir()
        if child.is_dir() and not child.is_symlink() and not child.name.startswith(".")
    )


@bp.get("/conferences/import")
@login_required
def event_import_wizard():
    cleanup_staging(
        Path(current_app.config["TMP_DIR"]),
        int(current_app.config["EVENT_IMPORT_STAGING_TTL_SECONDS"]),
    )
    return render_template(
        "dashboard/event_import.html",
        inspection=None,
        token="",
        existing_destinations=_existing_destinations(),
    )


@bp.post("/conferences/import/validate")
@login_required
def event_import_validate():
    token, stage = create_staging(Path(current_app.config["TMP_DIR"]))
    names: dict[str, str] = {}
    try:
        uploads = [
            upload for upload in (
                request.files.get("website_package"),
                request.files.get("info_package"),
            ) if upload and upload.filename
        ]
        if not uploads:
            raise ValueError("Choose at least one ZIP package.")
        for index, upload in enumerate(uploads, start=1):
            if not upload.filename.lower().endswith(".zip"):
                raise ValueError("Event packages must use the .zip extension.")
            candidate = stage / f"upload-{index}.zip"
            save_upload(upload.stream, candidate, int(current_app.config["IMPORT_MAX_ZIP_BYTES"]))
            kind = detect_package(candidate)
            target = stage / f"{kind}.zip"
            if target.exists():
                raise ValueError(f"Two {kind.upper()} packages were uploaded.")
            candidate.rename(target)
            names[kind] = Path(upload.filename).name[:255]
        website = stage / "website.zip"
        info = stage / "info.zip"
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            inspection = inspect_packages(
                conn,
                website if website.is_file() else None,
                info if info.is_file() else None,
                names.get("website", ""),
                names.get("info", ""),
                assets_dir=Path(current_app.config["ASSETS_DIR"]),
                events_domain=current_app.config["EVENTS_DOMAIN"],
            )
        (stage / "upload.json").write_text(json.dumps({"names": names}), encoding="utf-8")
        return render_template(
            "dashboard/event_import.html",
            inspection=inspection,
            token=token,
            existing_destinations=_existing_destinations(),
        )
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        current_app.logger.warning("event package validation rejected reason=%s", exc)
        flash(str(exc), "error")
        return redirect(url_for("dashboard.event_import_wizard"))


@bp.post("/conferences/import/apply")
@login_required
def event_import_apply():
    stage = None
    try:
        stage = resolve_staging(Path(current_app.config["TMP_DIR"]), request.form.get("token", ""))
        website, info, meta = _package_paths(stage)
        names = meta.get("names") if isinstance(meta.get("names"), dict) else {}
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            inspection = inspect_packages(
                conn, website, info, names.get("website", ""), names.get("info", ""),
                assets_dir=Path(current_app.config["ASSETS_DIR"]),
                events_domain=current_app.config["EVENTS_DOMAIN"],
            )
            if inspection.errors:
                raise ValueError("Validation contains blocking errors; validate corrected packages.")
            if inspection.warnings and request.form.get("accept_warnings") != "1":
                raise ValueError("Review and accept the validation warnings before importing.")
            publish = request.form.get("publish_website") == "1"
            import_metadata = request.form.get("import_metadata") == "1"
            result = apply_import(
                conn, inspection, website, info,
                events_root=Path(current_app.config["EVENTS_ROOT"]),
                assets_dir=Path(current_app.config["ASSETS_DIR"]),
                destination=request.form.get("destination", inspection.destination),
                publish_website=publish,
                import_metadata=import_metadata,
                replace=request.form.get("existing_mode") == "replace",
                keep_rollback=request.form.get("keep_rollback") == "1",
                events_domain=current_app.config["EVENTS_DOMAIN"],
                php_state_path=Path(current_app.config["EVENTS_PHP_STATE_PATH"]),
                require_php_state=current_app.config.get("ENV") == "production",
            )
        audit_log(
            "event.import", "conference site package imported",
            destination=result["destination"], website=result["website"], metadata=result["metadata"],
        )
        shutil.rmtree(stage, ignore_errors=True)
        flash(f"Conference site imported. PHP execution is disabled. Destination: {result.get('url', result['destination'])}", "success")
        return redirect(url_for("dashboard.conference_sites"))
    except Exception as exc:
        current_app.logger.exception("event package import failed")
        flash(admin_error_text(str(exc)), "error")
        return redirect(url_for("dashboard.event_import_wizard"))


@bp.get("/conferences/import/package-spec")
@login_required
def event_import_package_spec():
    path = Path(current_app.root_path).parent / "MIFP_EVENT_IMPORT_PACKAGE_SPEC.md"
    return send_file(path, mimetype="text/markdown", as_attachment=True, download_name=path.name)


# Backward-compatible aliases for bookmarks/open tabs from the first wizard
# iteration.  Canonical navigation lives under Conference sites.
@bp.get("/events/import")
@login_required
def event_import_legacy():
    return redirect(url_for("dashboard.event_import_wizard"), code=308)


@bp.get("/events/import/package-spec")
@login_required
def event_import_package_spec_legacy():
    return redirect(url_for("dashboard.event_import_package_spec"), code=308)

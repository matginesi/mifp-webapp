from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from flask import (
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    stream_with_context,
    url_for,
)

from ..db.connection import connect
from ..runtime_storage import prune_runtime_exports
from ..services.admin_safety import backup_sqlite_database
from ..services.asset_cleanup import (
    asset_library_summary,
    export_assets_to_zip,
    import_assets_from_jsonl,
    import_assets_from_zip,
    list_exported_zips,
)
from ..services.assets import (
    reconcile_asset_storage_status,
    resolve_db_asset_path,
    store_asset,
    store_external_asset,
    validate_external_asset_url,
)
from ..services.dashboard_repository import asset_usage, assets_summary, dashboard_counts, list_assets, unused_assets
from ..services.operation_maintenance import maintenance_guarded
from ..utils.logger import audit_log
from ..utils.text_utils import normalize_url
from ._shared import ENTITY_TYPES, SECTION_TABLES, admin_error_payload, admin_error_text
from .auth import login_required
from .dashboard import bp

ENTITY_LINK_ROLES = {"primary", "website", "source", "doi", "publisher", "registration", "program", "document", "social", "other"}
ASSET_KINDS = {"image", "document", "pdf", "video", "other"}


def _asset_kind(value: str | None, *, allow_auto: bool = False) -> str | None:
    kind = str(value or "").strip().lower()
    if not kind and allow_auto:
        return None
    if kind not in ASSET_KINDS:
        raise ValueError("Unsupported asset kind")
    return kind


def _asset_text(value: str | None, *, max_length: int = 500) -> str | None:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise ValueError(f"Asset metadata exceeds {max_length} characters")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        raise ValueError("Asset metadata contains control characters")
    return text or None


def _saved_temp_upload(upload, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(prefix="mifp_asset_import_", suffix=suffix, delete=False) as handle:
        path = Path(handle.name)
    try:
        upload.save(path)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _safe_asset_filename(filename: str) -> str | None:
    path = Path(filename)
    if path.is_absolute() or ".." in path.parts or any(part == "" for part in path.parts):
        return None
    return filename


def _delete_db_asset(conn, asset_id: int) -> bool:
    row = conn.execute("SELECT path FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        return False
    fpath = resolve_db_asset_path(current_app.config["ASSETS_DIR"], str(row["path"]))
    assets_root = Path(current_app.config["ASSETS_DIR"]).resolve()
    try:
        resolved = fpath.resolve()
        if resolved.is_file() and (resolved.parent == assets_root or assets_root in resolved.parents):
            resolved.unlink()
    except OSError:
        pass
    conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    return True


@bp.get("/database-assets")
@login_required
def database_assets():
    return redirect(url_for("dashboard.assets_page"))


@bp.post("/database-assets/cleanup")
@login_required
@maintenance_guarded("unused asset cleanup")
def cleanup_unused_assets():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        requested = [int(v) for v in request.form.getlist("asset_ids") if str(v).isdigit()]
        ids = requested or [r["id"] for r in unused_assets(conn)]
        deleted = 0
        apply_cleanup = request.form.get("apply", "1") == "1"
        backup_path = None
        archive_path = None
        if apply_cleanup and ids:
            backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label="asset-cleanup")
            if backup_path is None:
                flash("Cleanup stopped: the database backup could not be created.", "error")
                return redirect(url_for("dashboard.assets_page", status="unused"))
            archive_path = export_assets_to_zip(
                conn,
                current_app.config["ASSETS_DIR"],
                only_unused=True,
                export_dir=current_app.config["EXPORT_DIR"],
            )
            if archive_path is None:
                flash("Cleanup stopped: the recovery archive could not be created.", "error")
                return redirect(url_for("dashboard.assets_page", status="unused"))
            for aid in ids:
                if _delete_db_asset(conn, aid):
                    deleted += 1
            conn.commit()
    if deleted:
        audit_log(
            "asset.cleanup", "unused assets cleanup", count=deleted,
            backup_path=str(backup_path), archive_path=str(archive_path),
        )
        flash(
            f"Cleaned up {deleted} unused assets. Recovery ZIP: {archive_path.name}.",
            "success",
        )
    else:
        flash("No unused assets were cleaned up.", "info")
    return redirect(url_for("dashboard.assets_page"))


@bp.get("/assets/<path:filename>")
@login_required
def asset_file(filename):
    safe_filename = _safe_asset_filename(filename)
    if not safe_filename:
        return Response("Invalid filename", status=400)
    assets_dir = current_app.config["ASSETS_DIR"]
    try:
        assets_root = assets_dir.resolve()
        file_path = (assets_root / safe_filename).resolve()
        file_path.relative_to(assets_root)
    except (OSError, ValueError):
        return Response("Invalid filename", status=400)
    if file_path.exists():
        resp = send_from_directory(str(assets_dir), safe_filename)
        if safe_filename.lower().endswith((".pdf", ".svg")):
            resp.headers["Content-Disposition"] = "attachment"
        return resp
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        row = conn.execute(
            "SELECT source_url FROM assets WHERE path IN (?, ?) ORDER BY id DESC LIMIT 1",
            (safe_filename, f"assets/{safe_filename}"),
        ).fetchone()
    if row and row["source_url"]:
        return redirect(row["source_url"])
    return Response("File not found", status=404)


@bp.route("/assets", methods=["GET", "POST"])
@login_required
@maintenance_guarded("asset import or export")
def assets_page():
    removed_exports = prune_runtime_exports(
        Path(current_app.config["EXPORT_DIR"]),
        max_files=int(current_app.config["EXPORT_MAX_FILES"]),
        max_bytes=int(current_app.config["EXPORT_MAX_BYTES"]),
        max_age_days=int(current_app.config["EXPORT_RETENTION_DAYS"]),
    )
    if removed_exports:
        current_app.logger.info(
            "Pruned %d expired/pending asset export(s)", len(removed_exports)
        )
    if request.method == "POST":
        action = request.form.get("action")
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            try:
                if action == "upload":
                    file = request.files.get("file")
                    if not file or not file.filename:
                        raise ValueError("Choose a file to upload")
                    asset_id = store_asset(
                        conn,
                        file,
                        current_app.config["ASSETS_DIR"],
                        kind=_asset_kind(request.form.get("kind"), allow_auto=True),
                        alt_text=_asset_text(request.form.get("alt_text")),
                        caption=_asset_text(request.form.get("caption")),
                    )
                    audit_log("asset.upload", "asset upload", asset_id=asset_id, filename=file.filename)
                    flash("Asset uploaded.", "success")
                elif action == "external":
                    asset_id = store_external_asset(
                        conn,
                        request.form.get("source_url", ""),
                        kind=_asset_kind(request.form.get("kind")) or "other",
                        alt_text=_asset_text(request.form.get("alt_text")),
                        caption=_asset_text(request.form.get("caption")),
                    )
                    audit_log("asset.register_external", "external asset registered", asset_id=asset_id)
                    flash("External asset registered.", "success")
                elif action == "update":
                    asset_id = request.form.get("id", type=int)
                    if not asset_id or not conn.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                        raise ValueError("Asset not found")
                    values = {
                        "alt_text": _asset_text(request.form.get("alt_text")),
                        "caption": _asset_text(request.form.get("caption")),
                        "kind": _asset_kind(request.form.get("kind")),
                        "source_url": (
                            validate_external_asset_url(request.form.get("source_url", ""))
                            if request.form.get("source_url", "").strip()
                            else None
                        ),
                    }
                    conn.execute(
                        """
                        UPDATE assets
                        SET alt_text=?, caption=?, kind=?, source_url=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (values["alt_text"], values["caption"], values["kind"], values["source_url"], asset_id),
                    )
                    conn.commit()
                    audit_log("asset.update", "asset metadata update", asset_id=asset_id)
                    flash("Asset updated.", "success")
                elif action == "delete":
                    asset_id = request.form.get("id", type=int)
                    usage_count = int(conn.execute(
                        "SELECT COUNT(*) FROM asset_links WHERE asset_id=?",
                        (asset_id or 0,),
                    ).fetchone()[0])
                    if usage_count:
                        flash(
                            f"Asset is linked to {usage_count} record(s). Unlink it before deletion.",
                            "warning",
                        )
                        return redirect(url_for("dashboard.assets_page"))
                    backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label="delete-asset")
                    if backup_path is None:
                        raise RuntimeError("Database backup could not be created")
                    archive_path = export_assets_to_zip(
                        conn,
                        current_app.config["ASSETS_DIR"],
                        only_unused=True,
                        export_dir=current_app.config["EXPORT_DIR"],
                    )
                    if archive_path is None:
                        raise RuntimeError("Recovery archive could not be created")
                    if asset_id and _delete_db_asset(conn, asset_id):
                        conn.commit()
                        audit_log(
                            "asset.delete", "asset delete", asset_id=asset_id, force=False,
                            backup_path=str(backup_path), archive_path=str(archive_path),
                        )
                        flash(f"Asset deleted. Recovery ZIP: {archive_path.name}.", "success")
                    else:
                        flash("Asset is in use. Remove relationships first or confirm force delete.", "warning")
                elif action == "export_unused":
                    with connect(current_app.config["DATABASE_PATH"]) as conn:
                        zip_path = export_assets_to_zip(conn, current_app.config["ASSETS_DIR"], only_unused=True, export_dir=current_app.config["EXPORT_DIR"])
                    if zip_path:
                        flash(f"Export created: {zip_path.name}", "success")
                    else:
                        flash("No unused assets to export.", "info")

                elif action == "export_all":
                    with connect(current_app.config["DATABASE_PATH"]) as conn:
                        zip_path = export_assets_to_zip(conn, current_app.config["ASSETS_DIR"], only_unused=False, export_dir=current_app.config["EXPORT_DIR"])
                    if zip_path:
                        flash(f"Exported all assets to {zip_path.name}.", "success")
                    else:
                        flash("No assets to export.", "info")
                elif action == "export_filtered":
                    kind_filter = request.form.getlist("kind_filter") or None
                    status_filter = request.form.getlist("status_filter") or None
                    only_unused = request.form.get("only_unused") == "1"
                    zip_path = export_assets_to_zip(
                        conn, current_app.config["ASSETS_DIR"],
                        kind_filter=kind_filter, status_filter=status_filter,
                        only_unused=only_unused,
                        export_dir=current_app.config["EXPORT_DIR"],
                    )
                    if zip_path:
                        flash(f"Exported filtered assets to {zip_path.name}.", "success")
                    else:
                        flash("No matching assets to export.", "info")
                elif action == "reconcile":
                    backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label="asset-reconcile")
                    if backup_path is None:
                        raise RuntimeError("Database backup could not be created")
                    result = reconcile_asset_storage_status(conn, current_app.config["ASSETS_DIR"])
                    audit_log(
                        "asset.reconcile", "asset storage status reconciled", count=result["updated"],
                        backup_path=str(backup_path),
                    )
                    flash(
                        f"Reconciled {result['updated']} asset record(s): "
                        f"{result['local']} local, {result['external']} external, {result['missing']} missing.",
                        "success",
                    )
                elif action == "import_zip":
                    file = request.files.get("zip_file")
                    if file and file.filename and file.filename.endswith(".zip"):
                        tmp = _saved_temp_upload(file, ".zip")
                        try:
                            dry_run = request.form.get("dry_run") == "1"
                            result = import_assets_from_zip(conn, current_app.config["ASSETS_DIR"], tmp, dry_run=dry_run)
                        finally:
                            tmp.unlink(missing_ok=True)
                        if dry_run:
                            flash(f"Dry-run: {result['inserted']} would be imported, {result['skipped']} skipped.", "info")
                        else:
                            flash(f"Imported {result['inserted']} assets from zip. {result['skipped']} skipped.", "success")
                    else:
                        flash("Please upload a valid .zip file.", "error")
                elif action == "import_jsonl":
                    file = request.files.get("jsonl_file")
                    if file and file.filename and file.filename.endswith(".jsonl"):
                        tmp = _saved_temp_upload(file, ".jsonl")
                        try:
                            dry_run = request.form.get("dry_run") == "1"
                            result = import_assets_from_jsonl(conn, tmp, dry_run=dry_run)
                        finally:
                            tmp.unlink(missing_ok=True)
                        if dry_run:
                            flash(f"Dry-run: {result['inserted']} assets would be imported, {result['skipped']} skipped.", "info")
                        else:
                            flash(f"Imported {result['inserted']} asset records from JSONL. {result['skipped']} skipped.", "success")
                    else:
                        flash("Please upload a valid .jsonl file.", "error")
                else:
                    raise ValueError("Unsupported asset action")
            except ValueError as exc:
                flash(f"Asset input rejected: {exc}", "error")
            except Exception:
                current_app.logger.exception("asset action failed")
                flash(admin_error_text("The asset operation failed. Check the server log."), "error")
        return redirect(url_for("dashboard.assets_page"))

    q = request.args.get("q", "").strip() or None
    kind = request.args.get("kind", "").strip() or None
    status = request.args.get("status", "").strip().lower() or None
    allowed_statuses = {"used", "unused", "missing", "recoverable", "errors", "metadata", "duplicates"}
    if status not in allowed_statuses:
        status = None
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        counts = dashboard_counts(conn)
        all_rows = list_assets(conn, q=None, limit=max(300, int(counts.get("assets", 0)) + 1))
        rows = list_assets(conn, q=q, limit=300) if q else list(all_rows[:300])
        usage_rows = asset_usage(conn)
        usage = {r["id"]: r["usage_count"] for r in usage_rows}
        linked_records: dict[int, list[dict]] = {}
        for row in conn.execute(
            """
            SELECT al.asset_id, al.entity_type, al.entity_id, al.role,
                   COALESCE(n.title,e.title,p.title,ra.title,pg.title,s.name,m.display_name) AS label
            FROM asset_links al
            LEFT JOIN news n ON al.entity_type='news' AND al.entity_id=n.id
            LEFT JOIN events e ON al.entity_type='event' AND al.entity_id=e.id
            LEFT JOIN publications p ON al.entity_type='publication' AND al.entity_id=p.id
            LEFT JOIN research_areas ra ON al.entity_type='research_area' AND al.entity_id=ra.id
            LEFT JOIN pages pg ON al.entity_type='page' AND al.entity_id=pg.id
            LEFT JOIN sponsors s ON al.entity_type='sponsor' AND al.entity_id=s.id
            LEFT JOIN members m ON al.entity_type='member' AND al.entity_id=m.id
            ORDER BY al.asset_id, al.entity_type, al.entity_id
            """
        ).fetchall():
            linked_records.setdefault(row["asset_id"], []).append(dict(row))
        summary = assets_summary(conn)
        total_mb = round(sum(r["bytes"] for r in summary) / 1024 / 1024, 2)
        metrics = asset_library_summary(conn, current_app.config["ASSETS_DIR"])
        cleanup_plan = metrics["plan"]
        unused_count = metrics["unused"]
        used_count = metrics["used"]
        missing_count = metrics["missing"]
        orphan_count = metrics["orphan_count"]
        recovery = {
            "missing": metrics["missing"],
            "with_url": metrics["recoverable"],
            "external": metrics["external"],
            "deferred": metrics["deferred"],
            "terminal": metrics["terminal"],
        }
        exported_zips = list_exported_zips(current_app.config["ASSETS_DIR"], export_dir=current_app.config["EXPORT_DIR"])
        unused_ids = metrics["unused_ids"]
        missing_ids = metrics["missing_ids"]
        recoverable_ids = metrics["recoverable_ids"]
        error_ids = metrics["error_ids"]
        metadata_ids = metrics["metadata_ids"]
        duplicate_ids = metrics["duplicate_ids"]
        recovery_states = {
            int(item["asset_id"]): dict(item)
            for item in conn.execute(
                "SELECT asset_id, attempts, last_error, terminal, next_attempt_at FROM asset_recovery_state"
            ).fetchall()
        }
        for item in rows:
            asset_id = int(item["id"])
            item["issue_missing"] = asset_id in missing_ids
            item["issue_unused"] = asset_id in unused_ids
            item["issue_metadata"] = asset_id in metadata_ids
            item["issue_duplicate"] = asset_id in duplicate_ids
            item["recovery_state"] = recovery_states.get(asset_id)
        if kind:
            rows = [item for item in rows if item.get("kind") == kind]
        status_sets = {
            "used": metrics["used_ids"],
            "unused": unused_ids,
            "missing": missing_ids,
            "recoverable": recoverable_ids,
            "errors": error_ids,
            "metadata": metadata_ids,
            "duplicates": duplicate_ids,
        }
        if status:
            rows = [item for item in rows if int(item["id"]) in status_sets[status]]
        issue_summary = {
            "missing": metrics["missing"],
            "recoverable": metrics["recoverable"],
            "errors": metrics["errors"],
            "unused": metrics["unused"],
            "metadata": metrics["metadata"],
            "duplicates": metrics["duplicates"],
        }
    return render_template(
        "dashboard/assets.html",
        counts=counts,
        assets=rows,
        usage=usage,
        summary=summary,
        total_mb=total_mb,
        unused_count=unused_count,
        used_count=used_count,
        missing_count=missing_count,
        orphan_count=orphan_count,
        cleanup_plan=cleanup_plan,
        exported_zips=exported_zips,
        linked_records=linked_records,
        recovery=recovery,
        issue_summary=issue_summary,
        metrics=metrics,
        q=q,
        kind=kind,
        status=status,
    )


@bp.get("/assets/exports/<filename>")
@login_required
def asset_export_download(filename):
    exports_dir = current_app.config["EXPORT_DIR"]
    safe = Path(filename).name
    if safe != filename:
        return Response("Invalid filename", status=400)
    filepath = exports_dir / safe
    if not filepath.exists() or not filepath.is_file():
        return Response("File not found", status=404)
    try:
        filepath.resolve().relative_to(exports_dir.resolve())
    except (OSError, ValueError):
        return Response("Invalid filename", status=400)
    audit_log("export.asset_zip", "asset zip download", category="admin", outcome="success",
              filename=safe, size=filepath.stat().st_size if filepath.exists() else 0)
    logger = current_app.logger

    def deliver_once():
        try:
            with filepath.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
        finally:
            try:
                filepath.unlink(missing_ok=True)
                logger.info(
                    "Downloaded asset export removed from temporary storage: %s", safe
                )
            except OSError:
                logger.exception(
                    "Downloaded asset export could not be removed: %s", safe
                )

    response = Response(
        stream_with_context(deliver_once()),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-MIFP-Export-Retention"] = "delete-after-download"
    return response


@bp.get("/assets/search.json")
@login_required
def asset_search_json():
    q = request.args.get("q", "").strip() or None
    kind = request.args.get("kind", "").strip() or None
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        rows = list_assets(conn, q=q, limit=100)
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        usage = {r["id"]: r["usage_count"] for r in asset_usage(conn)}
        assets_dir = current_app.config["ASSETS_DIR"]
        result = []
        for r in rows:
            image_url = None
            public_url = None
            if r.get("path"):
                filename = r["path"].split("/", 1)[1] if "/" in r["path"] else r["path"]
                public_url = url_for("dashboard.asset_file", filename=filename)
                if r.get("kind") == "image":
                    image_url = public_url
            local_exists = False
            if r.get("path") and not r.get("is_external"):
                resolved = resolve_db_asset_path(assets_dir, str(r["path"]))
                local_exists = resolved.is_file()
            result.append({
                "id": r["id"],
                "filename": r.get("original_filename") or r.get("filename"),
                "path": r.get("path"),
                "kind": r.get("kind"),
                "mime_type": r.get("mime_type"),
                "size": r.get("size"),
                "storage_status": r.get("storage_status"),
                "is_external": bool(r.get("is_external")),
                "local_exists": local_exists,
                "public_url": public_url,
                "usage_count": usage.get(r["id"], 0),
                "image_url": image_url,
                "source_url": r.get("source_url"),
            })
    return jsonify(result)


@bp.post("/assets/create.json")
@login_required
def asset_create_json():
    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            if request.form.get("source_url"):
                current_app.logger.info(
                    "asset picker create started source=external kind=%s",
                    request.form.get("kind") or "other",
                )
                asset_id = store_external_asset(
                    conn,
                    request.form.get("source_url", ""),
                    kind=_asset_kind(request.form.get("kind")) or "other",
                    alt_text=_asset_text(request.form.get("alt_text")),
                    caption=_asset_text(request.form.get("caption")),
                )
            else:
                file = request.files.get("file")
                if not file or not file.filename:
                    current_app.logger.warning("asset picker create rejected reason=missing_file")
                    return jsonify({"error": "missing_file"}), 400
                extension = Path(file.filename).suffix.lower().lstrip(".")
                current_app.logger.info(
                    "asset picker create started source=upload extension=%s content_length=%s",
                    extension or "none",
                    request.content_length,
                )
                asset_id = store_asset(
                    conn,
                    file,
                    current_app.config["ASSETS_DIR"],
                    kind=_asset_kind(request.form.get("kind"), allow_auto=True),
                    alt_text=_asset_text(request.form.get("alt_text")),
                    caption=_asset_text(request.form.get("caption")),
                )
            row = conn.execute(
                "SELECT path, source_url FROM assets WHERE id=?",
                (asset_id,),
            ).fetchone()
            public_url = None
            if row and row["path"]:
                filename = str(row["path"]).split("/", 1)[1] if "/" in str(row["path"]) else str(row["path"])
                public_url = url_for("dashboard.asset_file", filename=filename)
            elif row and row["source_url"]:
                public_url = str(row["source_url"])
            audit_log("asset.create", "asset created from picker", asset_id=asset_id)
            current_app.logger.info(
                "asset picker create completed asset_id=%s storage=%s",
                asset_id,
                "external" if row and row["source_url"] else "local",
            )
            return jsonify({"id": asset_id, "public_url": public_url})
    except ValueError as exc:
        current_app.logger.warning("asset picker create rejected reason=%s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        current_app.logger.exception("asset picker create failed")
        return admin_error_payload("The asset could not be created.")


# ---------------------------------------------------------------------------
# Content-section asset upload / link / unlink
# ---------------------------------------------------------------------------

_ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    "jpg", "jpeg", "png", "gif", "webp", "pdf", "doc", "docx", "mp4", "mov", "txt", "csv",
})

_ALLOWED_MIME_PREFIXES = (
    "image/",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats",
    "video/",
)

_MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # 16 MB


def _validate_upload(file) -> str | None:
    """Return an error string if *file* fails validation, else None."""
    filename = file.filename or ""
    # Path-traversal / executable check
    if ".." in filename or "/" in filename or "\\" in filename:
        return "Invalid filename"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return f"File extension not allowed: .{ext}"
    # MIME check
    mime = file.mimetype or ""
    if not any(mime.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES):
        return f"MIME type not allowed: {mime}"
    # Size check
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > _MAX_UPLOAD_BYTES:
        return "File too large. Maximum size is 16 MB."
    return None


def _set_primary_if_first(conn, table: str, record_id: int, role: str, asset_id: int) -> None:
    """Mark the first asset for a role as primary in asset_links."""
    entity_type = ENTITY_TYPES.get(table, table)
    row = conn.execute(
        "SELECT id FROM asset_links WHERE entity_type=? AND entity_id=? AND role=? AND is_primary=1 LIMIT 1",
        (entity_type, record_id, role),
    ).fetchone()
    if not row:
        conn.execute(
            "UPDATE asset_links SET is_primary=1 WHERE entity_type=? AND entity_id=? AND role=? AND asset_id=?",
            (entity_type, record_id, role, asset_id),
        )


def _prepare_singleton_primary_role(
    conn,
    table: str,
    record_id: int,
    role: str,
    *,
    except_asset_id: int | None = None,
) -> None:
    """Keep cover/logo unique while preserving the previous image as gallery."""
    if role not in {"cover", "logo"}:
        return
    entity_type = ENTITY_TYPES.get(table, table)
    params: list[object] = [entity_type, record_id, role]
    exclusion = ""
    if except_asset_id is not None:
        exclusion = " AND asset_id<>?"
        params.append(except_asset_id)
    conn.execute(
        "UPDATE asset_links SET role='gallery', is_primary=0 "
        "WHERE entity_type=? AND entity_id=? AND role=?" + exclusion,
        tuple(params),
    )


def _clear_primary_if_matching(conn, table: str, record_id: int, role: str, asset_id: int) -> None:
    """Clear primary marker for an asset relation."""
    entity_type = ENTITY_TYPES.get(table, table)
    conn.execute(
        "UPDATE asset_links SET is_primary=0 WHERE entity_type=? AND entity_id=? AND role=? AND asset_id=?",
        (entity_type, record_id, role, asset_id),
    )


@bp.post("/content/<section>/<int:record_id>/assets/upload")
@login_required
def content_asset_upload(section, record_id):
    table = SECTION_TABLES.get(section)
    if not table:
        return jsonify({"error": "Invalid section"}), 400

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        record = conn.execute(f'SELECT id FROM "{table}" WHERE id=?', (record_id,)).fetchone()
        if not record:
            return jsonify({"error": "Record not found"}), 404

        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "No file provided"}), 400

        validation_error = _validate_upload(file)
        if validation_error:
            return jsonify({"error": validation_error}), 400

        try:
            asset_id = store_asset(
                conn,
                file,
                current_app.config["ASSETS_DIR"],
                kind=request.form.get("kind") or None,
                alt_text=request.form.get("alt_text") or None,
                caption=request.form.get("caption") or None,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            current_app.logger.exception("content asset upload failed")
            return admin_error_payload("The asset upload could not be completed.")

        # Create asset_links entry
        role = request.form.get("role") or "attachment"
        entity_type = ENTITY_TYPES.get(table, table)
        max_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0) FROM asset_links WHERE entity_type=? AND entity_id=?",
            (entity_type, record_id),
        ).fetchone()[0]
        _prepare_singleton_primary_role(conn, table, record_id, role)
        conn.execute(
            "INSERT INTO asset_links(asset_id, entity_type, entity_id, role, sort_order) VALUES(?,?,?,?,?)",
            (asset_id, entity_type, record_id, role, max_sort + 1),
        )

        # Set primary asset field if this is the first asset of that role
        _set_primary_if_first(conn, table, record_id, role, asset_id)

        conn.commit()

    audit_log(
        "asset.content_upload",
        "content asset upload",
        category="content",
        section=section,
        record_id=record_id,
        asset_id=asset_id,
        role=role,
    )
    return jsonify({"success": True, "asset_id": asset_id, "role": role})


@bp.post("/content/<section>/<int:record_id>/assets/link")
@login_required
def content_asset_link(section, record_id):
    table = SECTION_TABLES.get(section)
    if not table:
        return jsonify({"error": "Invalid section"}), 400

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        record = conn.execute(f'SELECT id FROM "{table}" WHERE id=?', (record_id,)).fetchone()
        if not record:
            return jsonify({"error": "Record not found"}), 404

        asset_id = request.form.get("asset_id", type=int)
        source_url = request.form.get("source_url", "").strip()
        role = request.form.get("role") or "attachment"

        if not asset_id and not source_url:
            return jsonify({"error": "Provide asset_id or source_url"}), 400

        entity_type = ENTITY_TYPES.get(table, table)

        if source_url and not asset_id:
            try:
                asset_id = store_external_asset(
                    conn,
                    source_url,
                    kind=request.form.get("kind") or "other",
                    alt_text=request.form.get("alt_text") or None,
                    caption=request.form.get("caption") or None,
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except Exception:
                current_app.logger.exception("external asset link failed")
                return admin_error_payload("The external asset could not be linked.")

        # Verify asset exists
        asset_row = conn.execute("SELECT id FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not asset_row:
            return jsonify({"error": "Asset not found"}), 404

        # Check for duplicate link
        existing = conn.execute(
            "SELECT id, role FROM asset_links WHERE asset_id=? AND entity_type=? AND entity_id=?",
            (asset_id, entity_type, record_id),
        ).fetchone()
        if existing and role not in {"cover", "logo"}:
            return jsonify({"error": "Asset already linked to this record"}), 409

        _prepare_singleton_primary_role(
            conn,
            table,
            record_id,
            role,
            except_asset_id=asset_id,
        )
        if existing:
            conn.execute(
                "UPDATE asset_links SET role=?, is_primary=1 WHERE id=?",
                (role, existing["id"]),
            )
        else:
            max_sort = conn.execute(
                "SELECT COALESCE(MAX(sort_order),0) FROM asset_links WHERE entity_type=? AND entity_id=?",
                (entity_type, record_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO asset_links(asset_id, entity_type, entity_id, role, sort_order) VALUES(?,?,?,?,?)",
                (asset_id, entity_type, record_id, role, max_sort + 1),
            )
            # Set primary field if appropriate
            _set_primary_if_first(conn, table, record_id, role, asset_id)

        conn.commit()

    audit_log(
        "asset.link",
        "content asset link",
        category="content",
        section=section,
        record_id=record_id,
        asset_id=asset_id,
        role=role,
    )
    return jsonify({"success": True, "asset_id": asset_id, "role": role})


@bp.post("/content/<section>/<int:record_id>/links/add")
@login_required
def content_external_link_add(section, record_id):
    table = SECTION_TABLES.get(section)
    if not table:
        return jsonify({"error": "Invalid section"}), 400

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        record = conn.execute(f'SELECT id FROM "{table}" WHERE id=?', (record_id,)).fetchone()
        if not record:
            return jsonify({"error": "Record not found"}), 404

        url = normalize_url(request.form.get("url"))
        if not url:
            return jsonify({"error": "Invalid URL"}), 400
        role = str(request.form.get("role") or "primary").strip().lower()
        if role not in ENTITY_LINK_ROLES:
            return jsonify({"error": f"Invalid role: {role}"}), 400
        label = request.form.get("label", "").strip() or None
        entity_type = ENTITY_TYPES.get(table, table)
        max_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order),0) FROM entity_links WHERE entity_type=? AND entity_id=?",
            (entity_type, record_id),
        ).fetchone()[0]
        has_primary = conn.execute(
            "SELECT 1 FROM entity_links WHERE entity_type=? AND entity_id=? AND is_primary=1 LIMIT 1",
            (entity_type, record_id),
        ).fetchone()
        try:
            conn.execute(
                """
                INSERT INTO entity_links(entity_type, entity_id, url, label, role, is_primary, sort_order)
                VALUES(?,?,?,?,?,?,?)
                """,
                (entity_type, record_id, url, label, role, 0 if has_primary else 1, max_sort + 1),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "Link already exists for this record"}), 409
        except Exception:
            current_app.logger.exception("external link add failed")
            return admin_error_payload("The external link could not be added.")

    audit_log("content.link_add", "external link added", category="content", section=section, record_id=record_id, role=role)
    return jsonify({"success": True, "url": url, "role": role})


@bp.post("/content/<section>/<int:record_id>/links/delete")
@login_required
def content_external_link_delete(section, record_id):
    table = SECTION_TABLES.get(section)
    if not table:
        return jsonify({"error": "Invalid section"}), 400
    link_id = request.form.get("link_id", type=int)
    if not link_id:
        return jsonify({"error": "Missing link_id"}), 400

    entity_type = ENTITY_TYPES.get(table, table)
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "DELETE FROM entity_links WHERE id=? AND entity_type=? AND entity_id=?",
            (link_id, entity_type, record_id),
        )
        conn.commit()

    audit_log("content.link_delete", "external link deleted", category="content", section=section, record_id=record_id, link_id=link_id)
    return jsonify({"success": True})


@bp.post("/content/<section>/<int:record_id>/assets/unlink")
@login_required
def content_asset_unlink(section, record_id):
    table = SECTION_TABLES.get(section)
    if not table:
        return jsonify({"error": "Invalid section"}), 400

    asset_id = request.form.get("asset_id", type=int)
    if not asset_id:
        return jsonify({"error": "Missing asset_id"}), 400

    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            record = conn.execute(f'SELECT id FROM "{table}" WHERE id=?', (record_id,)).fetchone()
            if not record:
                return jsonify({"error": "Record not found"}), 404

            entity_type = ENTITY_TYPES.get(table, table)
            link = conn.execute(
                "SELECT id, role FROM asset_links WHERE asset_id=? AND entity_type=? AND entity_id=?",
                (asset_id, entity_type, record_id),
            ).fetchone()
            if not link:
                current_app.logger.info(
                    "asset unlink already complete section=%s record_id=%s asset_id=%s",
                    section, record_id, asset_id,
                )
                return jsonify({
                    "success": True,
                    "asset_id": asset_id,
                    "already_unlinked": True,
                })

            role = link["role"]
            conn.execute(
                "DELETE FROM asset_links WHERE asset_id=? AND entity_type=? AND entity_id=?",
                (asset_id, entity_type, record_id),
            )

            # Clear primary field if it points to this asset
            _clear_primary_if_matching(conn, table, record_id, role, asset_id)

            conn.commit()
    except Exception:
        current_app.logger.exception("asset unlink failed")
        return admin_error_payload("The asset link could not be removed.")

    audit_log(
        "asset.unlink",
        "content asset unlink",
        category="content",
        section=section,
        record_id=record_id,
        asset_id=asset_id,
        role=role,
    )
    return jsonify({"success": True, "asset_id": asset_id})

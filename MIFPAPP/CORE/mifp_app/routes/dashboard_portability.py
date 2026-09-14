from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable, Generator
from datetime import date
from pathlib import Path
from typing import Any

from flask import (
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ..db.connection import connect
from ..services.admin_safety import (
    PORTABILITY_CACHE_PREFIX as _EXPORT_CACHE_PREFIX,
    PORTABILITY_CACHE_TTL_SECONDS as _EXPORT_CACHE_TTL_SECONDS,
    backup_sqlite_database,
)
from ..services.assets import AssetWriteSession, recover_missing_assets
from ..services.data_portability import (
    bundle_to_jsonl_file,
    bundle_to_zip_file,
    import_jsonl_payload,
    import_zip_payload,
    table_counts,
)
from ..services.portability_contract import build_import_format_guide, scope_options
from ..services.metrics_service import get_import_export_summary
from ..services.operation_maintenance import operation_maintenance
from ..utils.logger import audit_log
from ..utils.security import admin_password_matches, get_client_ip, ip_rate_allowed
from .auth import login_required
from .dashboard import bp

@bp.get("/data-portability")
@login_required
def data_portability():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        counts = table_counts(conn)
        import_export_summary = get_import_export_summary(conn)
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
        import_export_summary=import_export_summary,
        import_result=session.pop("data_portability_import_result", None),
    )


@bp.get("/data-portability/import-guide.md")
@login_required
def data_portability_import_guide():
    guide = build_import_format_guide()
    audit_log(
        "import.guide_downloaded",
        "LLM import format guide downloaded",
        category="admin",
        outcome="success",
        bytes=len(guide.encode("utf-8")),
    )
    return Response(
        guide,
        mimetype="text/markdown",
        headers={
            "Content-Disposition": 'attachment; filename="MIFP_LLM_IMPORT_GUIDE.md"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


_EXPORT_CACHE_MAX = 2


def _export_cache_dir() -> Path:
    root = Path(current_app.config["EXPORT_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _export_cache_paths(token: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    root = _export_cache_dir()
    base = f"{_EXPORT_CACHE_PREFIX}{digest}"
    return root / f"{base}.json", root / f"{base}.bin"


def _read_export_cache_meta(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _discard_export_cache_file(meta_path: Path) -> None:
    meta = _read_export_cache_meta(meta_path)
    if meta:
        data_name = str(meta.get("data_name") or "")
        if data_name.startswith(_EXPORT_CACHE_PREFIX) and data_name.endswith(".bin"):
            (_export_cache_dir() / Path(data_name).name).unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)


def _prune_export_cache(now: float | None = None) -> int:
    """Delete expired/corrupt disk-backed export tokens and stale payloads."""
    current = now if now is not None else time.time()
    root = _export_cache_dir()
    removed = 0
    live_data: set[str] = set()

    for meta_path in root.glob(f"{_EXPORT_CACHE_PREFIX}*.json"):
        meta = _read_export_cache_meta(meta_path)
        try:
            created_at = float((meta or {}).get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        if not meta or current - created_at > _EXPORT_CACHE_TTL_SECONDS:
            _discard_export_cache_file(meta_path)
            removed += 1
            continue
        data_name = str(meta.get("data_name") or "")
        if not data_name.startswith(_EXPORT_CACHE_PREFIX) or not data_name.endswith(".bin"):
            _discard_export_cache_file(meta_path)
            removed += 1
            continue
        live_data.add(Path(data_name).name)

    # Claimed metadata belongs to a download already in progress. A crashed
    # worker can leave it behind, so clean it after the same TTL.
    for claim_path in root.glob(f"{_EXPORT_CACHE_PREFIX}*.claim"):
        try:
            stale = current - claim_path.stat().st_mtime > _EXPORT_CACHE_TTL_SECONDS
        except OSError:
            stale = True
        if stale:
            _discard_export_cache_file(claim_path)
            removed += 1
        else:
            meta = _read_export_cache_meta(claim_path) or {}
            data_name = str(meta.get("data_name") or "")
            if data_name.startswith(_EXPORT_CACHE_PREFIX) and data_name.endswith(".bin"):
                live_data.add(Path(data_name).name)

    # Sweep temporary writes left by a crashed worker.
    for temp_path in root.glob(f"{_EXPORT_CACHE_PREFIX}*.tmp"):
        try:
            stale = current - temp_path.stat().st_mtime > _EXPORT_CACHE_TTL_SECONDS
        except OSError:
            stale = True
        if stale:
            temp_path.unlink(missing_ok=True)
            removed += 1

    # Sweep orphan payloads from crashes or interrupted metadata writes.
    for data_path in root.glob(f"{_EXPORT_CACHE_PREFIX}*.bin"):
        if data_path.name in live_data:
            continue
        try:
            stale = current - data_path.stat().st_mtime > _EXPORT_CACHE_TTL_SECONDS
        except OSError:
            stale = True
        if stale:
            data_path.unlink(missing_ok=True)
            removed += 1
    return removed


def _evict_export_cache_for_new() -> None:
    root = _export_cache_dir()
    entries: list[tuple[float, Path]] = []
    for meta_path in root.glob(f"{_EXPORT_CACHE_PREFIX}*.json"):
        meta = _read_export_cache_meta(meta_path)
        if meta:
            try:
                created_at = float(meta.get("created_at") or 0)
            except (TypeError, ValueError):
                created_at = 0
            entries.append((created_at, meta_path))
    entries.sort(key=lambda item: item[0])
    while len(entries) >= _EXPORT_CACHE_MAX:
        _, oldest = entries.pop(0)
        _discard_export_cache_file(oldest)


def _export_cache_count() -> int:
    return sum(1 for _ in _export_cache_dir().glob(f"{_EXPORT_CACHE_PREFIX}*.json"))


def _cache_export_file(
    token: str,
    source_path: Path,
    *,
    filename: str,
    mimetype: str,
    owner: str | None,
    session_key: str,
) -> None:
    _prune_export_cache()
    _evict_export_cache_for_new()
    meta_path, data_path = _export_cache_paths(token)
    source_path = Path(source_path)
    source_path.replace(data_path)
    try:
        data_path.chmod(0o600)
    except OSError:
        pass

    meta = {
        "data_name": data_path.name,
        "filename": filename,
        "mimetype": mimetype,
        "bytes": data_path.stat().st_size,
        "created_at": time.time(),
        "owner": owner,
        "session_key": session_key,
    }
    temp_meta = meta_path.with_suffix(f".{secrets.token_hex(4)}.tmp")
    try:
        temp_meta.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
        try:
            temp_meta.chmod(0o600)
        except OSError:
            pass
        temp_meta.replace(meta_path)
    except Exception:
        temp_meta.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)
        raise


def _claim_export_cache_entry(token: str) -> tuple[dict[str, Any], Path, Path] | None:
    meta_path, expected_data_path = _export_cache_paths(token)
    entry = _read_export_cache_meta(meta_path)
    if not entry:
        return None
    if (
        entry.get("owner") != session.get("admin_username")
        or entry.get("session_key") != _export_session_key()
    ):
        return None
    data_name = str(entry.get("data_name") or "")
    if Path(data_name).name != expected_data_path.name or not expected_data_path.is_file():
        _discard_export_cache_file(meta_path)
        return None
    claim_path = meta_path.with_suffix(f".{secrets.token_hex(4)}.claim")
    try:
        meta_path.rename(claim_path)
    except OSError:
        return None
    return entry, expected_data_path, claim_path


def _export_session_key() -> str:
    """Bind a cached export to this exact authenticated browser session."""
    material = "\0".join((
        str(session.get("admin_username") or ""),
        str(session.get("_csrf_token") or ""),
        str(session.get("admin_login_at") or ""),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _export_denied(message: str, status: int) -> Response:
    payload = json.dumps({
        "event": "error",
        "ok": False,
        "title_text": "Export authorization failed",
        "message": message,
        "icon_class": "bi-shield-x",
        "icon_modifier": "is-error",
    })
    return Response(payload + "\n", mimetype="application/x-ndjson", status=status, headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


def _import_denied(message: str, status: int, *, is_xhr: bool) -> Response:
    if not is_xhr:
        flash(message, "error")
        return redirect(url_for("dashboard.data_portability"))
    payload = json.dumps({
        "event": "result",
        "ok": False,
        "outcome": "authorization_denied",
        "title_text": "Import authorization failed",
        "message": message,
        "icon_class": "bi-shield-x",
        "icon_modifier": "is-error",
    })
    return Response(payload + "\n", mimetype="application/x-ndjson", status=status, headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


def _safe_upload_name(name: str, fallback_suffix: str = ".jsonl") -> str:
    clean = "".join(c for c in Path(name).name if c.isalnum() or c in "._-")
    if not clean or clean in {".", ".."}:
        clean = "file"
    if not Path(clean).suffix:
        clean += fallback_suffix
    return clean


_ALLOWED_IMPORT_EXTENSIONS = {".json", ".jsonl", ".zip"}


def _validate_import_selection(files: list[Any]) -> None:
    invalid = [
        Path(str(file.filename or "")).suffix.lower() or "(none)"
        for file in files
        if Path(str(file.filename or "")).suffix.lower() not in _ALLOWED_IMPORT_EXTENSIONS
    ]
    if invalid:
        raise ValueError(
            "Unsupported import file type. Use JSON/JSONL files or MIFP ZIP bundles."
        )


def _stage_import_uploads(files: list[Any]) -> tuple[Path, list[tuple[str, Path]]]:
    upload_dir = Path(tempfile.mkdtemp(prefix="mifp-import-"))
    staged_files: list[tuple[str, Path]] = []
    try:
        for index, file in enumerate(files):
            name = str(file.filename or f"file_{index}.jsonl")
            suffix = Path(name).suffix.lower() or ".jsonl"
            staged = upload_dir / f"{index}_{_safe_upload_name(name, suffix)}"
            file.save(staged)
            size = staged.stat().st_size
            limit = (
                int(current_app.config["IMPORT_MAX_ZIP_BYTES"])
                if suffix == ".zip"
                else max(
                    int(current_app.config["IMPORT_MAX_JSONL_BYTES"]),
                    int(current_app.config.get("MAX_CONTENT_LENGTH") or 0),
                )
            )
            if size > limit:
                label = "ZIP" if suffix == ".zip" else "JSON/JSONL"
                raise ValueError(f"{label} import exceeds maximum size: {limit} bytes")
            staged_files.append((name, staged))
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    return upload_dir, staged_files


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
    cancel_check: Callable[[], bool] | None = None,
    file_session: AssetWriteSession | None = None,
) -> dict[str, Any]:
    return import_zip_payload(
        conn, payload, scope, current_app.config["ASSETS_DIR"],
        dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
        progress=progress, source_name=source_name, cancel_check=cancel_check, commit=False,
        file_session=file_session,
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


@bp.post("/data-portability/export/<fmt>")
@login_required
def data_portability_export_post(fmt: str):
    started = time.monotonic()
    if fmt not in {"jsonl", "zip"}:
        return jsonify({"ok": False, "message": "Invalid export format"}), 400
    password = request.form.get("password", "")
    expected_hash = str(current_app.config.get("ADMIN_PASSWORD_HASH") or "")
    identity = {
        "username": session.get("admin_username"),
        "ip": get_client_ip(),
        "format": fmt,
    }
    if not admin_password_matches(password, expected_hash):
        within_limit = ip_rate_allowed(
            "portable_export_password_failure",
            f"{get_client_ip()}:{session.get('admin_username') or '-'}",
            limit=5,
            window_seconds=300,
        )
        audit_log(
            "export.authorization_denied",
            "portable export password verification failed",
            category="security",
            outcome="denied",
            rate_limited=not within_limit,
            **identity,
        )
        if not within_limit:
            return _export_denied("Too many failed attempts. Try again in a few minutes.", 429)
        return _export_denied("Password verification failed. No export was created.", 403)
    audit_log(
        "export.authorization_success",
        "portable export password verified",
        category="security",
        outcome="success",
        **identity,
    )
    scope = "all"
    current_app.logger.info(
        "data portability export started format=%s scope=%s", fmt, scope
    )

    record_counts: dict[str, int] = {}
    mimetype = "application/zip" if fmt == "zip" else "application/x-ndjson"
    filename = f"MIFP_EXPORT_{date.today().isoformat()}.zip" if fmt == "zip" else "records.jsonl"
    token = secrets.token_urlsafe(32)
    export_owner = session.get("admin_username")
    export_session_key = _export_session_key()
    export_dir = _export_cache_dir()
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=f"{_EXPORT_CACHE_PREFIX}write-", suffix=".tmp",
        dir=export_dir, delete=False,
    ) as handle:
        temp_export_path = Path(handle.name)

    import queue

    from ..services.job_manager import JobQueueFull, get_job_manager

    event_queue: queue.Queue[dict | None] = queue.Queue()
    app = current_app._get_current_object()

    def progress_cb(message: str, pct: int) -> None:
        event_queue.put({"event": "phase", "phase": "bundle", "label": message, "percent": pct})

    def run_export(cancelled: Callable[[], bool]) -> None:
        with app.app_context():
            try:
                with operation_maintenance(
                    current_app.config["DATABASE_PATH"], f"data export: {fmt}", logger=app.logger
                ):
                    with connect(current_app.config["DATABASE_PATH"]) as conn:
                        if fmt == "zip":
                            bundle_to_zip_file(
                                conn, "all", current_app.config["ASSETS_DIR"], temp_export_path,
                                app_version=str(current_app.config.get("APP_VERSION", "")),
                                progress_callback=progress_cb,
                            )
                        else:
                            manifest = bundle_to_jsonl_file(
                                conn, "all", current_app.config["ASSETS_DIR"], temp_export_path,
                                app_version=str(current_app.config.get("APP_VERSION", "")),
                                progress_callback=progress_cb,
                            )
                            record_counts.update(dict(manifest.get("counts") or {}))
                            app.logger.info(
                                "data portability JSONL package written records=%d assets=%d state=%s",
                                int(manifest.get("records") or 0), len(manifest.get("files") or []),
                                bool(manifest.get("state_sha256")),
                            )
                total_bytes = temp_export_path.stat().st_size
                max_export_bytes = int(current_app.config["EXPORT_MAX_BYTES"])
                if total_bytes > max_export_bytes:
                    raise ValueError(f"Export exceeds configured maximum size: {max_export_bytes} bytes")
                expired = _prune_export_cache()
                _cache_export_file(
                    token, temp_export_path, filename=filename, mimetype=mimetype,
                    owner=export_owner, session_key=export_session_key,
                )
                size_str = f"{total_bytes/1024:.1f} KB" if total_bytes < 1048576 else f"{total_bytes/1048576:.1f} MB"
                app.logger.info(
                    "data portability export ready format=%s bytes=%d duration_ms=%d expired_tokens=%d cached_exports=%d counts=%s",
                    fmt, total_bytes, int((time.monotonic() - started) * 1000), expired,
                    _export_cache_count(), record_counts,
                )
                audit_log("export.data_portability", "data portability export", category="admin", outcome="success",
                          scope="all", format=fmt, bytes=total_bytes, counts=json.dumps(record_counts, separators=(",", ":")) if record_counts else None)
                event_queue.put({
                    "event": "result", "ok": True,
                    "title_text": "Export ready", "message": f"{fmt.upper()} export ({size_str}) ready for download.",
                    "icon_class": "bi-check-lg", "icon_modifier": "is-success",
                    "filename": filename, "bytes": total_bytes, "mimetype": mimetype,
                    "download_token": token,
                })
            except Exception:
                temp_export_path.unlink(missing_ok=True)
                app.logger.exception("data portability export failed format=%s scope=%s", fmt, "all")
                audit_log("export.data_portability", "data portability export", category="admin", outcome="failure",
                          scope="all", format=fmt)
                event_queue.put({
                    "event": "error", "ok": False,
                    "title_text": "Export failed",
                    "message": "The export could not be generated. Check the server logs and try again.",
                    "icon_class": "bi-x-lg", "icon_modifier": "is-error",
                })
            finally:
                event_queue.put(None)

    manager = get_job_manager(
        int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
        int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
        db_path=str(current_app.config["DATABASE_PATH"]),
    )
    try:
        job_id, _future = manager.submit(f"data-export:{fmt}", run_export)
    except JobQueueFull:
        temp_export_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": "job_queue_full"}), 503

    def generate() -> Generator[str, None, None]:
        yield json.dumps({"event": "queued", "job_id": job_id}) + "\n"
        while True:
            data = event_queue.get()
            if data is None:
                break
            yield json.dumps(data, ensure_ascii=False, default=str) + "\n"

    return Response(generate(), mimetype="application/x-ndjson", headers={
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@bp.get("/data-portability/export-dl/<token>")
@login_required
def data_portability_export_dl(token: str):
    _prune_export_cache()
    meta_path, _ = _export_cache_paths(token)
    entry = _read_export_cache_meta(meta_path)
    if not entry:
        audit_log(
            "export.download_rejected",
            "data portability download token rejected",
            category="admin",
            outcome="denied",
            reason="missing_or_expired",
        )
        return Response("Download link expired or invalid. Please re-export.", status=404)
    if (
        entry.get("owner") != session.get("admin_username")
        or entry.get("session_key") != _export_session_key()
    ):
        audit_log(
            "export.download_rejected",
            "data portability download token rejected",
            category="admin",
            outcome="denied",
            reason="owner_mismatch",
        )
        return Response("Download link expired or invalid. Please re-export.", status=404)

    claimed = _claim_export_cache_entry(token)
    if not claimed:
        return Response("Download link expired or invalid. Please re-export.", status=404)
    entry, cache_path, claim_path = claimed
    payload_size = int(entry.get("bytes") or cache_path.stat().st_size)
    current_app.logger.info(
        "data portability download served format=%s bytes=%d cached_exports=%d",
        Path(entry["filename"]).suffix.lstrip("."),
        payload_size,
        _export_cache_count(),
    )
    audit_log(
        "export.download",
        "data portability export downloaded",
        category="admin",
        outcome="success",
        format=Path(entry["filename"]).suffix.lstrip("."),
        bytes=payload_size,
    )
    try:
        response = send_file(
            cache_path,
            mimetype=entry["mimetype"],
            as_attachment=True,
            download_name=entry["filename"],
            conditional=False,
            max_age=0,
        )
    except Exception:
        current_app.logger.exception(
            "data portability download failed token=%s format=%s",
            token[:8], Path(entry["filename"]).suffix.lstrip("."),
        )
        cache_path.unlink(missing_ok=True)
        claim_path.unlink(missing_ok=True)
        raise
    response.headers["Content-Length"] = str(payload_size)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

    def cleanup_export_download() -> None:
        cache_path.unlink(missing_ok=True)
        claim_path.unlink(missing_ok=True)

    response.call_on_close(cleanup_export_download)
    return response


@bp.post("/data-portability/import")
@login_required
def data_portability_import():
    started = time.monotonic()
    scope = "all"
    force_import = False
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    password = request.form.get("password", "")
    expected_hash = str(current_app.config.get("ADMIN_PASSWORD_HASH") or "")
    identity = {
        "username": session.get("admin_username"),
        "ip": get_client_ip(),
        "scope": scope,
    }
    if not admin_password_matches(password, expected_hash):
        within_limit = ip_rate_allowed(
            "portable_import_password_failure",
            f"{get_client_ip()}:{session.get('admin_username') or '-'}",
            limit=5,
            window_seconds=300,
        )
        audit_log(
            "import.authorization_denied",
            "portable import password verification failed",
            category="security",
            outcome="denied",
            rate_limited=not within_limit,
            **identity,
        )
        if not within_limit:
            return _import_denied(
                "Too many failed attempts. Try again in a few minutes.", 429,
                is_xhr=is_xhr,
            )
        return _import_denied(
            "Password verification failed. No file was processed and the database was not changed.",
            403,
            is_xhr=is_xhr,
        )
    audit_log(
        "import.authorization_success",
        "portable import password verified",
        category="security",
        outcome="success",
        **identity,
    )
    files = [f for f in request.files.getlist("data_file") if f and f.filename]
    if not files:
        flash("Upload one or more JSON/JSONL files or ZIP bundles.", "error")
        return redirect(url_for("dashboard.data_portability"))
    try:
        dry_run = request.form.get("dry_run") == "1"
        skip_assets = request.form.get("skip_assets") == "1"
        _validate_import_selection(files)
        current_app.logger.info(
            "data portability import request started scope=%s files=%d dry_run=%s skip_assets=%s",
            scope, len(files), dry_run, skip_assets,
        )

        if not is_xhr:
            return _import_postback(scope, files, dry_run, skip_assets, started, force_import=force_import)

        # Stage uploads to disk before the thread starts: request context and
        # its FileStorage objects die once the request returns, and holding
        # every file in memory would defeat streaming.
        upload_dir, staged_files = _stage_import_uploads(files)
        file_data: list[tuple[str, bytes | Path]] = list(staged_files)
        total_bytes = sum(p.stat().st_size for _, p in file_data)
        current_app.logger.info(
            "data portability import prepared scope=%s files=%s bytes=%d dry_run=%s skip_assets=%s",
            scope, _safe_upload_log_items(file_data), total_bytes,
            dry_run, skip_assets,
        )

        import queue

        from ..services.job_manager import JobCancelled, JobQueueFull, get_job_manager

        event_queue: queue.Queue[dict | None] = queue.Queue()

        def event_sink(data: dict) -> None:
            event_queue.put(data)

        app = current_app._get_current_object()

        def run_import(cancelled: Callable[[], bool]) -> None:
            with app.app_context():
                try:
                    _perform_import(
                        scope, file_data, dry_run, skip_assets, started, event_sink,
                        force_import=force_import, cancel_check=cancelled,
                    )
                except JobCancelled:
                    app.logger.warning("data portability import cancelled scope=%s files=%d", scope, len(file_data))
                    audit_log(
                        "import.cancelled", "data portability import cancelled", category="admin",
                        outcome="cancelled", scope=scope, files=len(file_data),
                    )
                    event_sink({
                        "event": "result", "ok": False, "cancelled": True, "outcome": "cancelled",
                        "title_text": "Import cancelled",
                        "message": "The import was cancelled before completion. Uncommitted database changes were rolled back.",
                        "icon_class": "bi-x-lg", "icon_modifier": "is-warning",
                    })
                    raise
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
                    message = (
                        f"{_safe_import_error(exc)} "
                        "No database changes from this failed batch were committed."
                    )
                    event_sink({"event": "error", "message": message})
                    event_sink({
                        "event": "result", "ok": False, "outcome": "failed",
                        "title_text": "Import failed", "message": message,
                        "icon_class": "bi-x-lg", "icon_modifier": "is-error",
                    })
                    raise
                finally:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    event_queue.put(None)

        manager = get_job_manager(
            int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
            int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
            db_path=str(current_app.config["DATABASE_PATH"]),
        )
        try:
            job_id, _future = manager.submit_cancellable(f"data-import:{scope}", run_import)
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
            yield json.dumps({"event": "queued", "job_id": job_id, "cancel_url": url_for("dashboard.data_portability_import_cancel", job_id=job_id)}) + "\n"
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
        message = _safe_import_error(exc)
        if is_xhr:
            payload = {
                "event": "result", "ok": False, "outcome": "rejected",
                "title_text": "Import rejected", "message": message,
                "icon_class": "bi-x-lg", "icon_modifier": "is-error",
            }
            return Response(
                json.dumps(payload, ensure_ascii=False) + "\n",
                mimetype="application/x-ndjson",
                status=400,
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )
        flash(f"Import error: {message}", "error")
        return redirect(url_for("dashboard.data_portability"))



@bp.post("/data-portability/import/<job_id>/cancel")
@login_required
def data_portability_import_cancel(job_id: str):
    from ..services.job_manager import get_job_manager

    manager = get_job_manager(
        int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
        int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
        db_path=str(current_app.config["DATABASE_PATH"]),
    )
    cancelled = manager.request_cancel(job_id)
    current_app.logger.warning(
        "data portability import cancel requested job_id=%s accepted=%s", job_id, cancelled
    )
    audit_log(
        "import.cancel_requested", "data portability import cancellation requested",
        category="admin", outcome="success" if cancelled else "ignored", job_id=job_id,
    )
    if not cancelled:
        return jsonify({"ok": False, "message": "Import job is no longer cancellable."}), 409
    return jsonify({"ok": True, "job_id": job_id}), 202

def _safe_import_error(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)[:500]
    return "The import could not be completed. Check the server log for details."


class _MonotonicProgress:
    """Track the highest percent emitted so far for one import request."""

    def __init__(self) -> None:
        self._last = 0.0

    def __call__(self, pct: float) -> int:
        self._last = max(self._last, min(100.0, max(0.0, pct)))
        return round(self._last)


def _perform_import(
    scope: str,
    file_data: list[tuple[str, bytes | Path]],
    dry_run: bool,
    skip_assets: bool,
    started: float,
    event_sink: Callable[[dict], None],
    *,
    force_import: bool = False,
    cancel_check: Callable[[], bool] | None = None,
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
            cancel_check=cancel_check,
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
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    summaries: list[dict[str, Any]] = []
    backup_path = None
    mono = _MonotonicProgress()
    if cancel_check and cancel_check():
        from ..services.job_manager import JobCancelled
        raise JobCancelled("Import cancelled by administrator")
    if not dry_run:
        event_sink({"event": "phase", "phase": "backup", "label": "Creating database backup…", "current_step": 0, "total_steps": 5, "percent": mono(5)})
        current_app.logger.info("Creating pre-import database backup")
        backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label="import")
        current_app.logger.info(
            "pre-import backup ready created=%s", bool(backup_path)
        )
    with AssetWriteSession(Path(current_app.config["ASSETS_DIR"])) as file_session, \
            connect(current_app.config["DATABASE_PATH"]) as conn:
        _per_file_totals: list[int] = []
        _total_records_aggregate = 0
        _current_index = 0
        _completed_records = 0
        _completed_inserted = 0
        _completed_updated = 0
        _completed_assets = 0
        _completed_record_errors = 0
        _completed_asset_errors = 0
        _completed_skipped = 0

        def progress(file_name: str, done: int, total: int) -> None:
            nonlocal _per_file_totals, _total_records_aggregate, _current_index
            pct = round((done / max(total, 1)) * 100)
            global_pct = 10 + 80 * ((_current_index + (done / max(total, 1))) / max(file_count, 1))
            event_sink({
                "event": "progress", "current": done, "total": total,
                "file": file_name, "percent": pct,
                "global_percent": mono(global_pct),
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
                "skipped": _completed_skipped,
            })

        file_count = len(file_data)
        event_sink({"event": "phase", "phase": "importing", "label": "Importing records…", "current_step": 1, "total_steps": 5, "percent": mono(10)})

        for file_index, (filename, payload) in enumerate(file_data):
            _current_index = file_index
            if cancel_check and cancel_check():
                from ..services.job_manager import JobCancelled
                raise JobCancelled("Import cancelled by administrator")

            is_zip = filename.lower().endswith(".zip")
            event_sink({
                "event": "file_start", "file": filename,
                "file_index": file_index, "file_count": file_count,
                "bytes": _payload_size(payload),
            })

            safe_filename = _safe_upload_name(filename, Path(filename).suffix or ".jsonl")
            try:
                if is_zip:
                    current_app.logger.info(
                        "importing ZIP file=%s index=%d/%d bytes=%d",
                        safe_filename, file_index + 1, file_count, _payload_size(payload),
                    )
                    before_asset_id = _max_asset_id(conn)
                    summary = _import_zip_dispatch(
                        conn, payload, scope,
                        dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
                        progress=lambda done, total, name=filename: progress(name, done, total),
                        source_name=filename, cancel_check=cancel_check, file_session=file_session,
                    )
                else:
                    summary = None
            except Exception as exc:
                current_app.logger.exception(
                    "import file failed file=%s index=%d/%d type=%s",
                    safe_filename, file_index + 1, file_count,
                    "zip" if is_zip else "jsonl",
                )
                if isinstance(exc, ValueError):
                    raise ValueError(f"{safe_filename}: {exc}") from exc
                raise

            if is_zip:
                assert summary is not None
                summary["filename"] = filename
                raw_errors = summary.get("errors")
                error_details = []
                if isinstance(raw_errors, list):
                    for error in raw_errors:
                        if isinstance(error, dict):
                            message = (error.get("error") or "").strip()
                            if message:
                                error_details.append({
                                    "kind": "record", "record": error.get("line"),
                                    "message": message[:500],
                                })
                elif isinstance(raw_errors, dict):
                    for value in raw_errors.values():
                        message = str(value).strip()
                        if message:
                            error_details.append({"kind": "record", "message": message[:500]})
                raw_asset_errors = summary.get("asset_errors")
                if isinstance(raw_asset_errors, list):
                    for error in raw_asset_errors:
                        if isinstance(error, dict):
                            message = str(error.get("error") or "").strip()
                            if message:
                                error_details.append({
                                    "kind": "asset", "record": error.get("line"),
                                    "message": message[:500],
                                })
                summary["error_details"] = error_details
                summary["errors"] = _summary_count(summary.get("errors"))
                summary["asset_errors"] = _summary_count(summary.get("asset_errors"))
                summary["linked_assets"] = _summary_count(summary.get("linked_assets"))
                summary.update(_asset_delta(conn, before_asset_id))

                file_inserted = _summary_count(summary.get("inserted"))
                file_updated = _summary_count(summary.get("updated"))
                file_assets = _summary_count(summary.get("linked_assets"))
                file_record_errors = _summary_count(summary.get("errors"))
                file_asset_errors = _summary_count(summary.get("asset_errors"))
                file_skipped = _summary_count(summary.get("skipped"))
                summaries.append(summary)
                current_app.logger.info(
                    "import ZIP completed file=%s inserted=%d updated=%d errors=%d asset_errors=%d linked_assets=%d",
                    filename, file_inserted, file_updated, file_record_errors, file_asset_errors, file_assets,
                )
            else:
                current_app.logger.info(
                    "importing JSON/JSONL index=%d/%d bytes=%d",
                    file_index + 1, file_count, _payload_size(payload),
                )
                if isinstance(payload, Path):
                    tmp_path = payload
                    owns_tmp = False
                else:
                    suffix = Path(filename).suffix or ".jsonl"
                    with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as handle:
                        handle.write(payload)
                        tmp_path = Path(handle.name)
                    owns_tmp = True
                try:
                    before_asset_id = _max_asset_id(conn)
                    counts = import_jsonl_payload(
                        conn, tmp_path, scope, Path(current_app.config["ASSETS_DIR"]),
                        dry_run=dry_run, skip_assets=skip_assets, force_import=force_import,
                        progress=lambda done, total, name=filename: progress(name, done, total),
                        asset_detail=lambda msg: event_sink({"event": "detail", "message": msg}),
                        source_name=filename, cancel_check=cancel_check, commit=False,
                        file_session=file_session,
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
                            {"kind": "record", "record": error.get("line"), "message": str(error.get("error") or "")[:500]}
                            for error in (counts.get("errors") or []) if isinstance(error, dict)
                        ] + [
                            {"kind": "asset", "record": error.get("line"), "message": str(error.get("error") or "")[:500]}
                            for error in (counts.get("asset_errors") or []) if isinstance(error, dict)
                        ],
                    }
                    file_summary.update(_asset_delta(conn, before_asset_id))
                    summaries.append(file_summary)
                    file_inserted = _summary_count(counts.get("inserted"))
                    file_updated = _summary_count(counts.get("updated"))
                    file_assets = counts.get("linked_assets", 0) if isinstance(counts.get("linked_assets"), int) else _summary_count(counts.get("linked_assets"))
                    file_record_errors = len(counts.get("errors") or [])
                    file_asset_errors = len(counts.get("asset_errors") or [])
                    file_skipped = _summary_count(counts.get("skipped"))
                    current_app.logger.info(
                        "import JSON/JSONL completed file=%s inserted=%d updated=%d errors=%d asset_errors=%d linked_assets=%d",
                        filename, file_inserted, file_updated, file_record_errors, file_asset_errors, file_assets,
                    )
                finally:
                    if owns_tmp:
                        tmp_path.unlink(missing_ok=True)

            _completed_records += file_inserted + file_updated
            _completed_inserted += file_inserted
            _completed_updated += file_updated
            _completed_assets += file_assets
            _completed_record_errors += file_record_errors
            _completed_asset_errors += file_asset_errors
            _completed_skipped += file_skipped
            event_sink({
                "event": "file_done", "file": filename,
                "file_index": file_index, "file_count": file_count,
            })
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
                "skipped": _completed_skipped,
            })

        if cancel_check and cancel_check():
            from ..services.job_manager import JobCancelled
            raise JobCancelled("Import cancelled by administrator")
        if not dry_run:
            event_sink({"event": "phase", "phase": "assets", "label": "Recovering assets…", "current_step": 2, "total_steps": 5, "percent": mono(90)})
            recovery = recover_missing_assets(
                conn, Path(current_app.config["ASSETS_DIR"]),
                cancel_check=cancel_check, commit=False, file_session=file_session,
            )
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
                "skipped": _completed_skipped,
            })

    event_sink({"event": "phase", "phase": "result", "label": "Finalizing…", "current_step": 3, "total_steps": 5, "percent": mono(98)})

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
        "Validation completed" if (ok and dry_run) else
        "Validation completed with issues" if (dry_run and not ok) else
        "Import completed with issues" if outcome == "warning" else
        "Import complete"
    )
    _result_icon = "bi-exclamation-lg" if outcome == "warning" or (dry_run and not ok) else "bi-check-lg"
    _result_icon_class = "is-warning" if outcome == "warning" or (dry_run and not ok) else "is-success"

    if dry_run:
        _result_message = (
            "Validation completed with "
            f"{total_errors + total_asset_errors} issue(s). "
            f"{total_inserted} record(s) would be inserted and {total_updated} updated. "
            "Nothing was changed."
            if total_errors + total_asset_errors
            else f"The package is valid: {total_inserted} record(s) would be inserted "
                 f"and {total_updated} updated. Nothing was changed."
        )
    else:
        _result_message = (
            f"Import completed in {duration_s}s: {total_inserted} inserted, "
            f"{total_updated} updated, {total_errors + total_asset_errors} error(s)."
            if ok
            else f"Import completed with {total_errors + total_asset_errors} issue(s): "
                 f"{total_inserted} inserted, {total_updated} updated."
        )

    event_sink({
        "event": "result",
        "ok": ok,
        "outcome": outcome,
        "title_text": _result_title,
        "icon_class": _result_icon,
        "icon_modifier": _result_icon_class,
        "message": _result_message,
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

    current_app.logger.info(
        "data portability import finished scope=%s dry_run=%s files=%d inserted=%d updated=%d errors=%d asset_errors=%d duration_s=%.1f",
        scope, dry_run, len(file_data), total_inserted, total_updated, total_errors, total_asset_errors, duration_s,
    )

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
    """Run the same import pipeline used by XHR and adapt its result for postback UI."""
    upload_dir, staged_files = _stage_import_uploads(files)
    result: dict[str, Any] = {}

    def event_sink(event: dict) -> None:
        nonlocal result
        if event.get("event") == "result":
            result = dict(event)

    try:
        _perform_import(
            scope,
            list(staged_files),
            dry_run,
            skip_assets,
            started,
            event_sink,
            force_import=force_import,
        )
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    if not result:
        raise RuntimeError("Import completed without a result summary")
    if not dry_run:
        session["data_portability_import_result"] = result

    flash(
        f"Imported {int(result.get('inserted') or 0)} new + "
        f"{int(result.get('updated') or 0)} updated records with "
        f"{int(result.get('linked_assets') or 0)} asset(s). Errors: "
        f"{int(result.get('errors') or 0)} (record) + "
        f"{int(result.get('asset_errors') or 0)} (asset).",
        "success" if result.get("ok") else "warning",
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

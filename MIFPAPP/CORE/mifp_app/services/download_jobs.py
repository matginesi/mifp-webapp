"""Background download jobs.

Heavy protected operations (safety-wizard export, database dump) run inside the
shared JobManager and produce a file that is served once through an owner- and
session-bound token. Artifacts live in ``EXPORT_DIR`` with a dedicated prefix so
they never collide with Data portability's export cache; the transient job
status registry is in-process (the JobManager mirrors finished job records to
its sibling ``jobs.sqlite3`` for history).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

_DL_CACHE_PREFIX = ".mifp-dl-"
_DL_TTL_SECONDS = 900
_DL_MAX_CACHED = 8

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}


def _cache_dir() -> Path:
    from flask import current_app
    root = Path(current_app.config["EXPORT_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_paths(token: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    root = _cache_dir()
    base = f"{_DL_CACHE_PREFIX}{digest}"
    return root / f"{base}.json", root / f"{base}.bin"


def prune() -> int:
    """Delete expired download entries and stale registry rows."""
    removed = 0
    now = time.time()
    for meta_path in _cache_dir().glob(f"{_DL_CACHE_PREFIX}*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if now - float(meta.get("created_at") or 0) > _DL_TTL_SECONDS:
            meta_path.with_suffix(".bin").unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            removed += 1
    with _lock:
        for job_id in [jid for jid, st in _jobs.items() if now - float(st.get("updated_at") or 0) > _DL_TTL_SECONDS]:
            _jobs.pop(job_id, None)
    return removed


def _set_job(job_id: str, **updates: Any) -> None:
    with _lock:
        state = _jobs.setdefault(job_id, {"status": "queued", "percent": 0, "message": "Waiting…"})
        state.update(updates)
        state["updated_at"] = time.time()


def get_download_job_status(job_id: str) -> dict[str, Any] | None:
    with _lock:
        state = _jobs.get(job_id)
        if state is None:
            return None
        return {
            "status": state.get("status", "queued"),
            "percent": int(state.get("percent") or 0),
            "message": str(state.get("message") or ""),
            "records": state.get("records"),
            "assets": state.get("assets"),
            "errors": state.get("errors"),
        }


def submit_download_job(
    *,
    name: str,
    owner: str,
    session_key: str,
    build: Callable[[Path, Callable[[int, str], None]], dict[str, Any]],
) -> tuple[str, str]:
    """Queue a background artifact build and return ``(job_id, token)``.

    ``build(artifact_path, progress)`` writes the artifact to ``artifact_path``
    and returns ``{"filename", "mimetype", "bytes"}``. ``progress(pct, message)``
    updates the transient status used by polling endpoints.
    """
    from flask import current_app

    from ..services.job_manager import JobQueueFull, get_job_manager

    app = current_app._get_current_object()
    job_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    temp_path = _cache_dir() / f"{_DL_CACHE_PREFIX}write-{job_id}.bin"
    _set_job(job_id, status="queued", percent=0, message="Waiting…")

    def progress(pct: int, message: str, records: int | None = None, assets: int | None = None, errors: int | None = None) -> None:
        _set_job(job_id, status="running", percent=pct, message=message, records=records, assets=assets, errors=errors)

    def run(cancel_event: Callable[[], bool]) -> None:
        with app.app_context():
            try:
                if cancel_event():
                    _set_job(job_id, status="cancelled", percent=0, message="Cancelled")
                    return
                _set_job(job_id, status="running", percent=1, message="Starting…")
                meta = build(temp_path, progress)
                meta_path, data_path = _cache_paths(token)
                temp_path.replace(data_path)
                meta_path.write_text(json.dumps({
                    "data_name": data_path.name,
                    "filename": str(meta["filename"]),
                    "mimetype": str(meta["mimetype"]),
                    "bytes": int(meta["bytes"]),
                    "created_at": time.time(),
                    "owner": owner,
                    "session_key": session_key,
                }), encoding="utf-8")
                prune()
                _set_job(job_id, status="ready", percent=100, message="Ready")
            except Exception as exc:
                temp_path.unlink(missing_ok=True)
                _set_job(job_id, status="failed", message=str(exc))

    manager = get_job_manager(
        int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
        int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
        db_path=str(current_app.config["DATABASE_PATH"]),
    )
    try:
        manager.submit(f"download:{name}", run)
    except JobQueueFull:
        temp_path.unlink(missing_ok=True)
        _set_job(job_id, status="failed", message="Background job queue full")
    return job_id, token


def claim_download(
    token: str, *, owner: str, session_key: str | None = None
) -> tuple[dict[str, Any], Path] | None:
    """Claim a one-time, owner/session-bound download; ``None`` on rejection."""
    prune()
    meta_path, data_path = _cache_paths(token)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("owner") != owner:
        return None
    if session_key is not None and meta.get("session_key") != session_key:
        return None
    if time.time() - float(meta.get("created_at") or 0) > _DL_TTL_SECONDS:
        meta_path.unlink(missing_ok=True)
        data_path.unlink(missing_ok=True)
        return None
    if not data_path.is_file():
        meta_path.unlink(missing_ok=True)
        return None
    meta_path.unlink(missing_ok=True)  # one-shot
    return meta, data_path


def session_key() -> str:
    """Bind a download to the exact authenticated browser session."""
    from flask import session
    material = "\0".join((
        str(session.get("admin_username") or ""),
        str(session.get("_csrf_token") or ""),
        str(session.get("admin_login_at") or ""),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

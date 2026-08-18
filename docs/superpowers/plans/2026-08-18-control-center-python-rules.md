# Control Center / Server Python-Owned Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move site-management business rules out of JS into safe Python for Control Center and Server: server-owned completeness and slug rules, background Python jobs for the safety-wizard export and the server database dump, and uniform, clearer copy across those pages.

**Architecture:** Python remains the single source for every rule. JS becomes presentation-only (it reads server-rendered JSON and renders; it never computes a business rule). Heavy protected operations run as `JobManager` background jobs that cache the artifact and hand back an owner-bound, one-time download token — reusing the proven data-quality analyze pattern (`app.app_context()` inside the worker) and the export-cache token semantics from Data portability, without touching Data portability itself.

**Tech Stack:** Flask routes (`dashboard_control.py`, `dashboard.py`, `dashboard_content.py`, `dashboard_data_quality.py`), new service `mifp_app/services/download_jobs.py`, existing `mifp_app/services/job_manager.py`, vanilla JS (`content.js`, `events.js`, `data-quality.js`, `safety-operations.js`), Jinja templates, pytest.

## Global Constraints

- Backup and cleanup operations in the safety wizard stay synchronous (only `export` becomes async).
- Data portability architecture is untouched (its `bundle_to_*` builders are used read-only by the new export job).
- JS keeps all UI conveniences (sorting, filters, asset picker, live slug preview). Only rule ownership moves to Python.
- Server-side validation already exists for completeness (`dashboard_content.py:79-103`, `514-530`) and for bulk data-quality (`dashboard_data_quality.py:469-503`); this plan makes JS read/trust those instead of re-deriving them.
- UI copy is English and uniform; existing URLs and route names are unchanged.
- The webapp suite is the versioned CI suite; run `pytest TESTS/webapp -q` after each task.

---
## File Structure

- Create `MIFPAPP/CORE/mifp_app/services/download_jobs.py` — background artifact cache + one-time token download (new service).
- Modify `MIFPAPP/CORE/mifp_app/routes/dashboard_control.py` — async safety export + status/download endpoints.
- Modify `MIFPAPP/CORE/mifp_app/routes/dashboard.py` — async db-dump.
- Modify `MIFPAPP/CORE/mifp_app/routes/dashboard_content.py` — expose completeness JSON; event slug authority.
- Modify `MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js`, `events.js`, `data-quality.js`, `safety-operations.js`.
- Modify `MIFPAPP/CORE/mifp_app/templates/dashboard/server.html`, `control/safety_operations.html`, `control/*.html`, `_event_wizard.html`.
- Create/modify tests: `TESTS/webapp/test_download_jobs.py`, `test_safety_operations.py`, `test_db_dump.py`, `test_data_quality.py`, `test_dashboard_actions.py`.

---

### Task 1: New `services/download_jobs.py` background-download service

**Files:**
- Create: `MIFPAPP/CORE/mifp_app/services/download_jobs.py`
- Test: `TESTS/webapp/test_download_jobs.py`

**Interfaces:**
- Produces:
  - `submit_download_job(*, name: str, owner: str, session_key: str, build: Callable[[Path, Callable[[int, str], None]], dict]) -> tuple[str, str]` — queues `build(artifact_path, progress)` in the JobManager; returns `(job_id, token)`. `build` must write the artifact to `artifact_path` and return `{"filename": str, "mimetype": str, "bytes": int}`.
  - `get_download_job_status(job_id: str) -> dict | None` — `{"status", "percent", "message"}` where `status ∈ {queued, running, ready, failed}`.
  - `claim_download(token: str, *, owner: str, session_key: str) -> tuple[dict, Path] | None` — one-time, owner+session-bound claim returning `(meta, data_path)`.
  - `prune() -> int` — removes expired artifact entries.

- [ ] **Step 1: Write the failing tests**

Create `TESTS/webapp/test_download_jobs.py`:

```python
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def app(tmp_path):
    os.environ.update({
        "TESTING": "1",
        "DATABASE_PATH": str(tmp_path / "mifp.db"),
        "ASSETS_DIR": str(tmp_path / "assets"),
        "EXPORT_DIR": str(tmp_path / "exports"),
        "LOG_DIR": str(tmp_path / "logs"),
        "SECRET_KEY": "download-jobs-test-secret",
        "LOG_ACCESS_ENABLED": "0",
    })
    from mifp_app import create_app
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        EXPORT_DIR=tmp_path / "exports",
        ASSETS_DIR=tmp_path / "assets",
        LOG_DIR=tmp_path / "logs",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    return app


def _build(tmp_path: Path):
    def build(path: Path, progress) -> dict:
        progress(50, "Writing artifact…")
        path.write_bytes(b"payload")
        progress(100, "Done")
        return {"filename": "artifact.bin", "mimetype": "application/octet-stream", "bytes": 7}
    return build


def test_submit_and_download_roundtrip(app, tmp_path):
    from mifp_app.services import download_jobs
    with app.app_context():
        job_id, token = download_jobs.submit_download_job(
            name="test", owner="admin", session_key="sess", build=_build(tmp_path)
        )
        for _ in range(50):
            status = download_jobs.get_download_job_status(job_id)
            if status and status["status"] in {"ready", "failed"}:
                break
            time.sleep(0.01)
        assert status["status"] == "ready", status
        assert status["percent"] == 100
        claimed = download_jobs.claim_download(token, owner="admin", session_key="sess")
        assert claimed is not None
        meta, data_path = claimed
        assert meta["filename"] == "artifact.bin"
        assert data_path.read_bytes() == b"payload"
        # one-shot: a second claim must fail
        assert download_jobs.claim_download(token, owner="admin", session_key="sess") is None


def test_claim_rejects_wrong_owner_and_session(app, tmp_path):
    from mifp_app.services import download_jobs
    with app.app_context():
        job_id, token = download_jobs.submit_download_job(
            name="test", owner="admin", session_key="sess", build=_build(tmp_path)
        )
        for _ in range(50):
            status = download_jobs.get_download_job_status(job_id)
            if status and status["status"] in {"ready", "failed"}:
                break
            time.sleep(0.01)
        assert download_jobs.claim_download(token, owner="other", session_key="sess") is None
        assert download_jobs.claim_download(token, owner="admin", session_key="nope") is None


def test_failed_build_reports_failure(app, tmp_path):
    from mifp_app.services import download_jobs
    def broken(path: Path, progress):
        raise RuntimeError("boom")
    with app.app_context():
        job_id, _token = download_jobs.submit_download_job(
            name="broken", owner="admin", session_key="sess", build=broken
        )
        for _ in range(50):
            status = download_jobs.get_download_job_status(job_id)
            if status and status["status"] in {"ready", "failed"}:
                break
            time.sleep(0.01)
        assert status["status"] == "failed"
        assert "boom" in status["message"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_download_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mifp_app.services.download_jobs'`

- [ ] **Step 3: Implement the service**

Create `MIFPAPP/CORE/mifp_app/services/download_jobs.py`:

```python
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
import shutil
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

    def progress(pct: int, message: str) -> None:
        _set_job(job_id, status="running", percent=pct, message=message)

    def run() -> None:
        with app.app_context():
            try:
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
                _set_job(job_id, status="failed", message=str(exc)[:500])

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
    token: str, *, owner: str, session_key: str
) -> tuple[dict[str, Any], Path] | None:
    """Claim a one-time, owner/session-bound download; ``None`` on rejection."""
    prune()
    meta_path, data_path = _cache_paths(token)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("owner") != owner or meta.get("session_key") != session_key:
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
```

Note: `shutil` is imported but unused — remove it from the import list to keep lint clean.

- [ ] **Step 4: Run the tests**

Run: `pytest TESTS/webapp/test_download_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/services/download_jobs.py TESTS/webapp/test_download_jobs.py
git commit -m "feat(control): background download job service with one-time tokens"
```

---

### Task 2: Safety-wizard export runs as a background job

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard_control.py:439-532`
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/safety-operations.js:70-110`
- Test: `TESTS/webapp/test_safety_operations.py`

**Interfaces:**
- Consumes: `submit_download_job`, `get_download_job_status`, `claim_download`, `session_key` from `services/download_jobs`; `bundle_to_zip_file` from `services/data_portability` (with `progress_callback`).
- Produces:
  - `POST /control/safety-operations/run` with `operation=export` returns JSON `{"ok": true, "job_id", "status_url", "download_url"}`.
  - `GET /control/safety-operations/status/<job_id>` → `{"ok": true, "status", "percent", "message"}` (404 when unknown).
  - `GET /control/safety-operations/dl/<token>` → one-shot ZIP download (404 on claim failure).

- [ ] **Step 1: Write the failing tests**

Replace `test_password_gated_export_is_import_compatible` in `TESTS/webapp/test_safety_operations.py` with an async flow:

```python
def test_password_gated_export_is_import_compatible(client, app):
    import json

    from mifp_app.services.data_portability import parse_zip_payload

    response = _run(client, "export")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["job_id"]
    status_url = payload["status_url"]
    download_url = payload["download_url"]

    status = None
    for _ in range(200):
        status_resp = client.get(status_url)
        assert status_resp.status_code == 200
        status = status_resp.get_json()
        if status["status"] in {"ready", "failed"}:
            break
        time.sleep(0.02)
    assert status["status"] == "ready", status

    dl = client.get(download_url)
    assert dl.status_code == 200
    assert dl.headers["Content-Disposition"].startswith("attachment;")
    parsed = parse_zip_payload(dl.data)
    assert parsed["manifest"]["format"] == "mifp-jsonl-v2"
    assert parsed["manifest"]["scope"] == "all"

    # token is one-shot
    assert client.get(download_url).status_code == 404


def test_safety_export_status_rejects_unknown_job(client):
    response = client.get("/dashboard/control/safety-operations/status/does-not-exist")
    assert response.status_code == 404


def test_safety_export_requires_valid_password_still(client, app):
    response = client.post(
        "/dashboard/control/safety-operations/run",
        data={"operation": "export", "password": "wrong", "acknowledge": "1"},
    )
    assert response.status_code == 302  # flash + redirect, same as before
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_safety_operations.py -k "export" -v`
Expected: FAIL (route currently returns a raw ZIP blob, so `response.get_json()` returns `None`).

- [ ] **Step 3: Convert the export branch to a background job**

In `dashboard_control.py`, adjust the imports at the top of the file:

```python
from flask import current_app, flash, jsonify, redirect, render_template, request, Response, send_file, session, url_for
```
and add, next to the existing `from ..services.data_portability import bundle_to_zip` line, replace it with:
```python
from ..services import download_jobs
from ..services.data_portability import bundle_to_zip_file
```
(`bundle_to_zip` is no longer used after this change; remove it to keep lint clean. `connect`, `operation_maintenance`, `audit_log`, `date`, `session`, `current_app`, `send_file` are already imported.)

Replace the `if operation == "export":` branch (`dashboard_control.py:503-532`) with:

```python
        if operation == "export":
            export_owner = session.get("admin_username")
            export_session_key = download_jobs.session_key()
            app = current_app._get_current_object()

            def build(path, progress) -> dict:
                def report(message: str, pct: int) -> None:
                    progress(pct, message)
                with operation_maintenance(
                    current_app.config["DATABASE_PATH"],
                    "protected portable export",
                    logger=current_app.logger,
                ), connect(Path(current_app.config["DATABASE_PATH"])) as conn:
                    bundle_to_zip_file(
                        conn, "all", Path(current_app.config["ASSETS_DIR"]), path,
                        app_version=str(current_app.config.get("APP_VERSION", "")),
                        progress_callback=report,
                    )
                return {
                    "filename": f"mifp-secure-export-{date.today().isoformat()}.zip",
                    "mimetype": "application/zip",
                    "bytes": path.stat().st_size,
                }

            job_id, token = download_jobs.submit_download_job(
                name="safety-export", owner=export_owner,
                session_key=export_session_key, build=build,
            )
            audit_log(
                "safety_operation.export_queued",
                "protected portable export queued",
                category="admin",
                outcome="success",
                job_id=job_id,
                **identity,
            )
            return jsonify({
                "ok": True,
                "job_id": job_id,
                "status_url": url_for("dashboard.control_safety_operations_status", job_id=job_id),
                "download_url": url_for("dashboard.control_safety_operations_download", token=token),
            })
```

- [ ] **Step 4: Add the status and download endpoints**

Append after `control_safety_operations_run` (end of the function, before the next `@bp` decorator) in `dashboard_control.py`:

```python
@bp.get("/control/safety-operations/status/<job_id>")
@login_required
def control_safety_operations_status(job_id: str):
    status = download_jobs.get_download_job_status(job_id)
    if status is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, **status})


@bp.get("/control/safety-operations/dl/<token>")
@login_required
def control_safety_operations_download(token: str):
    claimed = download_jobs.claim_download(
        token,
        owner=session.get("admin_username"),
        session_key=download_jobs.session_key(),
    )
    if claimed is None:
        audit_log(
            "safety_operation.download_rejected",
            "protected export download token rejected",
            category="admin", outcome="denied", reason="missing_or_expired",
        )
        return Response("Download link expired or invalid. Please re-run the operation.", status=404)
    meta, data_path = claimed
    audit_log(
        "safety_operation.export_downloaded",
        "protected portable export downloaded",
        category="admin", outcome="success",
        bytes=int(meta["bytes"]),
    )
    try:
        response = send_file(
            data_path,
            mimetype=meta["mimetype"],
            as_attachment=True,
            download_name=meta["filename"],
            conditional=False,
            max_age=0,
        )
    except Exception:
        data_path.unlink(missing_ok=True)
        raise
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.call_on_close(lambda: data_path.unlink(missing_ok=True))
    return response
```

Check that `jsonify`, `send_file`, `Response`, `session`, `current_app`, `url_for`, `connect`, `date`, `audit_log` are imported in `dashboard_control.py`; add any that are missing (the file already uses `jsonify`, `flash`, `redirect`, `request`, `send_file`, `session`, `current_app`, `url_for`, `connect`, `date`, `audit_log` per existing code).

- [ ] **Step 5: Update the JS to poll and download**

In `safety-operations.js:70-110`, replace the export submit handler body:

```javascript
  wizard.addEventListener('submit', async (event) => {
    if (operation() !== 'export') return;
    event.preventDefault();
    if (!wizard.reportValidity()) return;
    submit.disabled = true;
    window.MIFPLog?.info('safety.export_started', { operation: 'export' });
    try {
      const response = await fetch(wizard.action, {
        method: 'POST',
        body: new FormData(wizard),
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (!response.ok) {
        throw new Error(response.status === 403
          ? 'Authorization failed. Check the administrator password.'
          : 'The secure export was not authorized or could not be created.');
      }
      const data = await response.json();
      if (!data.ok || !data.status_url || !data.download_url) {
        throw new Error('The export job could not be started.');
      }
      submit.textContent = 'Creating secure export…';
      let status = null;
      for (let attempt = 0; attempt < 300; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const statusResponse = await fetch(data.status_url, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        if (!statusResponse.ok) throw new Error('The export job is no longer available.');
        status = await statusResponse.json();
        if (status.status === 'failed') {
          throw new Error(status.message || 'The export job failed.');
        }
        if (status.status === 'ready') break;
      }
      if (!status || status.status !== 'ready') {
        throw new Error('The export took too long. Check the server log and try again.');
      }
      const link = document.createElement('a');
      link.href = data.download_url;
      link.download = 'mifp-secure-export.zip';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.MIFPUI?.showToast('Secure export created and download started.', 'success');
      window.MIFPLog?.info('safety.export_completed', { job_id: data.job_id });
    } catch (error) {
      window.MIFPUI?.showToast(error.message || 'The protected operation failed.', 'error');
      window.MIFPLog?.error('safety.export_failed', { error: error });
    } finally {
      submit.disabled = false;
      window.MIFPUI?.clearFormLoading(wizard);
      render();
    }
  });
```

Note: `render()` recomputes the submit label; call it in `finally` to restore the default text.

- [ ] **Step 6: Run the safety tests**

Run: `pytest TESTS/webapp/test_safety_operations.py -q`
Expected: PASS (backup/cleanup tests untouched; export test now async).

- [ ] **Step 7: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard_control.py MIFPAPP/CORE/mifp_app/static/js/dashboard/safety-operations.js TESTS/webapp/test_safety_operations.py
git commit -m "feat(control): safety-wizard export runs as a background download job"
```

---

### Task 3: Server database dump runs as a background job

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard.py:394-423` (`server_db_dump`)
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/server.html:167-197`
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js` (add async dump handler) — or a small inline script
- Test: `TESTS/webapp/test_db_dump.py`

**Interfaces:**
- Consumes: `submit_download_job`, `get_download_job_status`, `claim_download`, `session_key` from `services/download_jobs`; `backup_sqlite_database` from `services/admin_safety`.
- Produces:
  - `POST /server/db-dump` returns JSON `{"ok": true, "job_id", "status_url", "download_url"}`.
  - `GET /server/db-dump/status/<job_id>` → `{"ok": true, "status", "percent", "message"}`.
  - `GET /server/db-dump/dl/<token>` → one-shot `.sqlite` download.

- [ ] **Step 1: Write the failing tests**

Modify `TESTS/webapp/test_db_dump.py`:

- Update the `app_with_admin` fixture so the background job writes only to `tmp_path` (the default `EXPORT_DIR` resolves to the real `MIFPAPP/DATABASE/exports`; tests must never write there):

```python
@pytest.fixture
def app_with_admin(tmp_path):
    import os
    from werkzeug.security import generate_password_hash
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["EXPORT_DIR"] = str(tmp_path / "exports")
    os.environ["ASSETS_DIR"] = str(tmp_path / "assets")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-pass")
    app.config["EXPORT_DIR"] = tmp_path / "exports"
    app.config["ASSETS_DIR"] = tmp_path / "assets"
    app.config["EXPORT_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["ASSETS_DIR"].mkdir(parents=True, exist_ok=True)
    yield app
```

- Keep `test_db_dump_blocked_when_disabled` and `test_db_dump_requires_password` (both still redirect on the same pre-job checks).
- Replace `test_db_dump_sets_no_cache_headers` with an async check, and rewrite `test_full_database_download_and_restore_round_trip` to download via the token before restoring:

```python
def test_db_dump_sets_no_cache_headers(app_with_admin):
    app_with_admin.config["ALLOW_DB_DUMP"] = True
    with app_with_admin.test_client() as client:
        _login(client)
        start = client.post("/dashboard/server/db-dump", data={"password": "test-pass"})
        assert start.status_code == 200
        job = start.get_json()
        status = _wait_ready(client, job["status_url"])
        assert status["status"] == "ready", status
        dl = client.get(job["download_url"])
        assert dl.status_code == 200
        assert "no-store" in dl.headers.get("Cache-Control", "")


def _wait_ready(client, status_url, timeout=300):
    import time
    for _ in range(timeout):
        resp = client.get(status_url)
        assert resp.status_code == 200
        status = resp.get_json()
        if status["status"] in {"ready", "failed"}:
            return status
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def test_full_database_download_and_restore_round_trip(app_with_admin):
    import io
    import sqlite3

    app_with_admin.config["ALLOW_DB_DUMP"] = True
    app_with_admin.config["ALLOW_DB_RESTORE"] = True
    db_path = app_with_admin.config["DATABASE_PATH"]
    wal_writer = sqlite3.connect(db_path)
    wal_writer.execute("PRAGMA journal_mode=WAL")
    wal_writer.execute("DELETE FROM news WHERE slug IN ('snapshot-record','later-record')")
    wal_writer.execute(
        "INSERT INTO news(title,slug,review_status) VALUES('Snapshot record','snapshot-record','published')"
    )
    wal_writer.commit()

    with app_with_admin.test_client() as client:
        _login(client)
        start = client.post("/dashboard/server/db-dump", data={"password": "test-pass"})
        assert start.status_code == 200
        job = start.get_json()
        status = _wait_ready(client, job["status_url"])
        assert status["status"] == "ready", status
        wal_writer.close()
        downloaded = client.get(job["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.data.startswith(b"SQLite format 3")
        assert downloaded.headers["X-MIFP-Backup-Type"] == "full-sqlite-snapshot"
        assert client.get(job["download_url"]).status_code == 404  # one-shot

        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM news WHERE slug='snapshot-record'")
            conn.execute(
                "INSERT INTO news(title,slug,review_status) VALUES('Later record','later-record','published')"
            )
            conn.commit()

        restored = client.post(
            "/dashboard/server/db-restore",
            data={
                "password": "test-pass",
                "confirmation": "RESTORE DATABASE",
                "database_file": (
                    io.BytesIO(downloaded.data),
                    "mifp_full_database.sqlite",
                    "application/vnd.sqlite3",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert restored.status_code == 200
        assert b"Full database restored successfully" in restored.data

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM news WHERE slug='snapshot-record'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM news WHERE slug='later-record'"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_db_dump.py -k "async_job" -v`
Expected: FAIL (route returns a raw SQLite file, not JSON).

- [ ] **Step 3: Convert `server_db_dump`**

In `dashboard.py:394-423`, keep the `ALLOW_DB_DUMP` and password checks, then replace the snapshot+download tail with a background job:

```python
    db_path = Path(current_app.config["DATABASE_PATH"])
    owner = session.get("admin_username")
    job_session_key = download_jobs.session_key()
    app = current_app._get_current_object()

    def build(path, progress) -> dict:
        with operation_maintenance(db_path, "consistent database download", logger=current_app.logger):
            snapshot = backup_sqlite_database(db_path, label="download", _maintenance_guard=False)
        if snapshot is None:
            raise RuntimeError("Database file is unavailable")
        shutil.copyfile(snapshot, path)
        return {
            "filename": f"mifp_full_database_{date.today().isoformat()}.sqlite",
            "mimetype": "application/vnd.sqlite3",
            "bytes": path.stat().st_size,
        }

    job_id, token = download_jobs.submit_download_job(
        name="db-dump", owner=owner, session_key=job_session_key, build=build,
    )
    audit_log("admin.db_download", "verified database snapshot queued", category="admin", outcome="success",
              ip=get_client_ip(), username=session.get("admin_username"))
    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status_url": url_for("dashboard.server_db_dump_status", job_id=job_id),
        "download_url": url_for("dashboard.server_db_dump_download", token=token),
    })
```

Add at the top of `dashboard.py` the import `from ..services import download_jobs` (check existing imports to avoid duplicates; `shutil`, `jsonify`, `url_for`, `operation_maintenance`, `backup_sqlite_database`, `date`, `get_client_ip`, `session` are already used in the file).

- [ ] **Step 4: Add status and download endpoints**

Append after `server_db_dump` in `dashboard.py`:

```python
@bp.get("/server/db-dump/status/<job_id>")
@login_required
def server_db_dump_status(job_id: str):
    status = download_jobs.get_download_job_status(job_id)
    if status is None:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, **status})


@bp.get("/server/db-dump/dl/<token>")
@login_required
def server_db_dump_download(token: str):
    claimed = download_jobs.claim_download(
        token,
        owner=session.get("admin_username"),
        session_key=download_jobs.session_key(),
    )
    if claimed is None:
        audit_log("admin.db_download_denied", "db download token rejected", category="admin",
                  outcome="denied", reason="missing_or_expired",
                  ip=get_client_ip(), username=session.get("admin_username"))
        return Response("Download link expired or invalid. Please download again.", status=404)
    meta, data_path = claimed
    audit_log("admin.db_download", "verified database snapshot downloaded", category="admin", outcome="success",
              ip=get_client_ip(), username=session.get("admin_username"),
              db_size=int(meta["bytes"]))
    try:
        response = send_file(
            data_path,
            mimetype=meta["mimetype"],
            as_attachment=True,
            download_name=meta["filename"],
            conditional=False,
            max_age=0,
        )
    except Exception:
        data_path.unlink(missing_ok=True)
        raise
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-MIFP-Backup-Type"] = "full-sqlite-snapshot"
    response.call_on_close(lambda: data_path.unlink(missing_ok=True))
    return response
```

Verify `send_file` is imported in `dashboard.py` (it is used elsewhere, e.g. `data_portability_export_dl`).

- [ ] **Step 5: Update `server.html` and add client polling**

In `server.html:174`, change the download form so it no longer uses the blob-download handler. Replace the form element:

```html
      <form method="post" action="{{ url_for('dashboard.server_db_dump') }}" id="databaseDownloadForm" data-db-dump>
```

Remove the `data-download-response` attribute. Update the modal footer button label stays "Download database". Add a small dedicated JS block at the end of `server.html` inside `{% block extra_js %}` (content.js is already loaded; add this after it):

```html
{% block extra_js %}{{ super() }}
<script src="{{ url_for('static', filename='js/dashboard/content.js') }}" defer></script>
<script nonce="{{ csp_nonce }}">
document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('databaseDownloadForm');
  if (!form) return;
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var button = form.querySelector('button[type="submit"]');
    var csrf = form.querySelector('input[name="_csrf_token"]')?.value || '';
    var password = form.querySelector('input[name="password"]')?.value || '';
    if (button) { button.disabled = true; button.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Preparing…'; }
    fetch(form.action, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': csrf, 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'password=' + encodeURIComponent(password) + '&_csrf_token=' + encodeURIComponent(csrf),
    })
      .then(function (response) { return response.ok ? response.json() : Promise.reject(new Error('HTTP ' + response.status)); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || 'The database dump could not be started.');
        return data;
      })
      .then(function (data) {
        var poll = function (attempt) {
          return fetch(data.status_url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Job not found')); })
            .then(function (status) {
              if (status.status === 'failed') throw new Error(status.message || 'The database dump failed.');
              if (status.status === 'ready') return data;
              if (attempt >= 300) throw new Error('The database dump took too long. Try again.');
              return new Promise(function (resolve) { setTimeout(function () { resolve(poll(attempt + 1)); }, 500); });
            });
        };
        return poll(0);
      })
      .then(function (data) {
        var link = document.createElement('a');
        link.href = data.download_url;
        link.download = 'mifp_backup.sqlite';
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.MIFPUI?.showToast('Database backup downloaded.', 'success');
        if (form.closest('.modal') && window.bootstrap) window.bootstrap.Modal.getInstance(form.closest('.modal'))?.hide();
      })
      .catch(function (error) {
        window.MIFPUI?.showToast(error.message, 'error');
      })
      .finally(function () {
        if (button) { button.disabled = false; button.innerHTML = '<i class="bi bi-download"></i> Download database'; }
      });
  });
});
</script>
{% endblock %}
```

Note: `server.html` currently has no `{% block extra_js %}`; the `content.js` script is loaded via `{% block extra_js %}` on line 4 (`{% block extra_js %}{{ super() }}<script src=...content.js ...>{% endblock %}`). Keep that and append the inline script inside the same block.

- [ ] **Step 6: Run the db-dump tests**

Run: `pytest TESTS/webapp/test_db_dump.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard.py MIFPAPP/CORE/mifp_app/templates/dashboard/server.html TESTS/webapp/test_db_dump.py
git commit -m "feat(server): database dump runs as a background download job"
```

---

### Task 4: Completeness rules served by Python, rendered by JS

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard_content.py:33-38, 281-282, 360, 514-530`
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js:79-111`
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/events.js:165-188`
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/content.html` (create form), `templates/dashboard/_event_wizard.html`
- Test: `TESTS/webapp/test_dashboard_actions.py`

**Interfaces:**
- Consumes: `_validate_new_record_completeness` and the event completeness block in `dashboard_content.py`.
- Produces:
  - Server renders `data-completeness='{"members": [...], "publications": [...], ...}'` JSON on the create-record form (publishable/active required fields) and `data-completeness='{"date": true, "description": true, "location": true, "cover": true}'` on the event wizard form.
  - JS reads the attribute and blocks only when the server rule says the field is required.

- [ ] **Step 1: Write the failing tests**

`TESTS/webapp/test_content_quality_contract.py` has no HTTP fixtures, so put these two HTTP tests in `TESTS/webapp/test_dashboard_actions.py` (it already defines an `app` fixture and a `client` fixture whose session is pre-authenticated as `admin`):

```python
def test_content_page_renders_completeness_rules_from_server(app, client):
    response = client.get("/dashboard/content/members")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-completeness=' in body
    assert '"display_name"' in body
    assert '"affiliation"' in body


def test_event_wizard_renders_completeness_rules_from_server(app, client):
    response = client.get("/dashboard/events")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'data-completeness=' in body
    assert '"date"' in body and '"cover"' in body
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_content_quality_contract.py -k "completeness_rules" -v`
Expected: FAIL (no `data-completeness=` attribute rendered).

- [ ] **Step 3: Server renders completeness JSON for content pages**

In `dashboard_content.py`, define a module-level map next to `_CREATE_REQUIRED_FIELDS`:

```python
_PUBLISHABLE_COMPLETENESS = {
    "members": ["display_name", "affiliation", "country", "role_id"],
    "publications": ["title", "authors", "year"],
    "research_areas": ["title", "summary", "description"],
    "sponsors": ["name", "description", "tier", "primary_asset"],
}
```

In the content page route, compute the JSON and pass it to the template (near `required_fields`, `dashboard_content.py:360`):

```python
        import json as _json
        completeness_json = _json.dumps(_PUBLISHABLE_COMPLETENESS.get(table, []))
```

and pass `completeness_json=completeness_json` to `render_template`. (Use a top-level `import json` if the file doesn't already import it.)

- [ ] **Step 4: Render the attribute on the create-record form**

In `templates/dashboard/content.html`, find the `[data-create-record-form]` form element and add:

```html
data-completeness="{{ completeness_json }}"
```

- [ ] **Step 5: Content JS reads the server rule**

In `content.js:79-111`, replace the hardcoded `completeness` map with a read of the form attribute:

```javascript
  form.addEventListener('submit', function(event) {
    var fields = Array.from(form.elements).filter(function(field) {
      return field.name && field.name !== '_csrf_token' && field.name !== 'id';
    });
    var section = form.dataset.section || 'unknown';
    var status = form.elements.review_status?.value || '';
    var active = form.elements.is_active?.value === '1';
    var requiredForPublish;
    try { requiredForPublish = JSON.parse(form.dataset.completeness || '[]'); }
    catch (_) { requiredForPublish = []; }
    var required = section === 'sponsors'
      ? (active ? requiredForPublish : [])
      : (status === 'published' ? requiredForPublish : []);
    var missing = required.filter(function(name) {
      var field = form.elements[name];
      if (field?.type === 'file') return !field.files?.length;
      return !String(field?.value || '').trim();
    });
    if (missing.length) {
      event.preventDefault();
      event.stopImmediatePropagation();
      contentLog.warn('content.create.blocked', {
        section: section,
        missing_fields: missing,
      });
      showToast(
        'Complete ' + missing.map(function(name) { return name.replace(/_/g, ' '); }).join(', ')
          + ' or save the record as draft/inactive.',
        'warning'
      );
      form.elements[missing[0]]?.focus();
      return;
    }
    contentLog.info('content.create.submit', {
      section: section,
      fields: fields.map(function(field) { return field.name; }),
      filled_fields: fields.filter(function(field) {
        return String(field.value || '').trim() !== '';
      }).length,
    });
  });
```

Note: for `sponsors`, `_PUBLISHABLE_COMPLETENESS["sponsors"]` is `["name", "description", "tier", "primary_asset"]`. The `primary_asset` entry keeps the existing JS behaviour of blocking active sponsors without a chosen logo (`field?.type === 'file'` → `!field.files?.length`); the server separately enforces the logo upload in `_validate_new_record_completeness`.

- [ ] **Step 6: Server renders event completeness and JS reads it**

In the events route (`dashboard_content.py`, before `render_template`, ~line 609), add:

```python
    import json as _json
    event_completeness = _json.dumps({
        "date": True, "description": True, "location": True, "cover": True,
    })
```

pass `event_completeness=event_completeness` to `render_template` for `templates/dashboard/events.html`. In `events.html`, on the element that includes the wizard (check how `_event_wizard.html` is included), add the attribute to the wizard form. The simplest robust option: put it on the wizard root `#eventWizard` inside `_event_wizard.html`:

```html
<div class="modal fade" id="eventWizard" data-completeness="{{ event_completeness }}" ...>
```

Then in `events.js:165-188`, replace the hardcoded `missingPublished` computation:

```javascript
    if (wizardForm.elements.review_status?.value === 'published') {
      var required = [];
      try { required = JSON.parse(eventWizard.dataset.completeness || '{}'); }
      catch (_) { required = {}; }
      var missingPublished = [];
      if (required.date && !(wizardForm.elements.start_date?.value || wizardForm.elements.date_text?.value.trim())) {
        missingPublished.push('date');
      }
      if (required.description && !wizardForm.elements.description?.value.trim()) missingPublished.push('description');
      if (required.location && !(wizardForm.elements.location?.value.trim() || wizardForm.elements.remote_url?.value.trim())) {
        missingPublished.push('location or external URL');
      }
      if (required.cover && !wizardForm.elements.cover_asset_id?.value) missingPublished.push('cover image');
      if (missingPublished.length) {
        event.preventDefault();
        event.stopImmediatePropagation();
        eventLog.warn('event.create.blocked', {
          reason: 'published_event_incomplete',
          missing_fields: missingPublished,
        });
        showToast(
          'Complete ' + missingPublished.join(', ') + ' or save the event as draft.',
          'warning'
        );
        return;
      }
    }
```

- [ ] **Step 7: Run the tests**

Run: `pytest TESTS/webapp/test_content_quality_contract.py TESTS/webapp/test_dashboard_actions.py -q`
Expected: FAIL on the two new `test_*_renders_completeness_rules_from_server` tests; the rest PASS.

- [ ] **Step 8: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard_content.py MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js MIFPAPP/CORE/mifp_app/static/js/dashboard/events.js MIFPAPP/CORE/mifp_app/templates/dashboard/content.html MIFPAPP/CORE/mifp_app/templates/dashboard/_event_wizard.html MIFPAPP/CORE/mifp_app/templates/dashboard/events.html TESTS/webapp/test_content_quality_contract.py
git commit -m "feat(control): completeness rules served by Python, rendered by JS"
```

---

### Task 5: Event slug is Python-authoritative

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard_content.py:496-540` (event save)
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/_event_wizard.html:46-49` (mark slug automated)
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/events.js:135-143` (keep preview only)
- Test: `TESTS/webapp/test_dashboard_actions.py`

**Interfaces:**
- Produces: on event save, the server computes `slug = slugify(title)` unless the client explicitly marked the slug as manually edited (`slug_automated=0`). A manual slug is accepted as-is. The JS auto-preview writes the hidden `slug_automated` marker only.

- [ ] **Step 1: Write the failing tests**

Append to `TESTS/webapp/test_dashboard_actions.py` (reuse its existing fixture/login helpers):

```python
def test_event_save_slug_is_server_derived_from_title(app_with_admin):
    client = app_with_admin.test_client()
    _login(client)
    resp = client.post("/dashboard/events", data={
        "_csrf_token": "x",
        "title": "My Conference 2026",
        "review_status": "draft",
        "slug": "client-supplied-stale",
        "slug_automated": "1",
    })
    assert resp.status_code in (200, 302)
    with app_with_admin.app_context():
        from mifp_app.db.connection import connect
        from pathlib import Path
        with connect(app_with_admin.config["DATABASE_PATH"]) as conn:
            slug = conn.execute("SELECT slug FROM events WHERE title='My Conference 2026'").fetchone()
    assert slug is not None
    assert slug["slug"] == "my-conference-2026"


def test_event_save_keeps_manually_edited_slug(app_with_admin):
    client = app_with_admin.test_client()
    _login(client)
    resp = client.post("/dashboard/events", data={
        "_csrf_token": "x",
        "title": "Another Event",
        "review_status": "draft",
        "slug": "custom-slug",
        "slug_automated": "0",
    })
    assert resp.status_code in (200, 302)
    with app_with_admin.app_context():
        from mifp_app.db.connection import connect
        with connect(app_with_admin.config["DATABASE_PATH"]) as conn:
            slug = conn.execute("SELECT slug FROM events WHERE title='Another Event'").fetchone()
    assert slug is not None
    assert slug["slug"] == "custom-slug"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_dashboard_actions.py -k "slug" -v`
Expected: FAIL (`slug_automated` is ignored; the stale client slug is stored as-is).

- [ ] **Step 3: Server re-slugifies when automated**

In the event save route (`dashboard_content.py`, right after the title check at `dashboard_content.py:510-511`), add:

```python
                title = str(form_data.get("title") or "").strip()
                automated = str(form_data.get("slug_automated") or "").strip() != "0"
                if automated:
                    form_data["slug"] = slugify(title) or None
                else:
                    form_data["slug"] = str(form_data.get("slug") or "").strip() or (slugify(title) or None)
```

`slugify` is already imported in `dashboard_content.py` (line 27).

- [ ] **Step 4: Wizard marks automation**

In `_event_wizard.html:46-49`, add a hidden input that tracks whether the slug was auto-generated:

```html
              <label class="field">
                <span>Slug</span>
                <input name="slug" class="form-control form-control-sm" placeholder="Auto-generated from title">
                <input type="hidden" name="slug_automated" value="1">
              </label>
```

In `events.js`, the slug handlers (`events.js:135-143`) already manage `dataset.automated` on the input; mirror that into the hidden field so the value the server sees is explicit:

```javascript
  var wizardTitle = wizardForm.querySelector('input[name="title"]');
  var wizardSlug = wizardForm.querySelector('input[name="slug"]');
  var wizardSlugAutomated = wizardForm.querySelector('input[name="slug_automated"]');
  function syncSlugAutomated() {
    if (wizardSlugAutomated) wizardSlugAutomated.value = wizardSlug.dataset.automated === 'false' ? '0' : '1';
  }
  wizardTitle.addEventListener('input', function() {
    if (!wizardSlug.value || wizardSlug.dataset.automated !== 'false') {
      wizardSlug.value = wizardTitle.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || '';
      wizardSlug.dataset.automated = 'true';
    }
    syncSlugAutomated();
  });
  wizardSlug.addEventListener('input', function() { wizardSlug.dataset.automated = 'false'; syncSlugAutomated(); });
```

- [ ] **Step 5: Run the tests**

Run: `pytest TESTS/webapp/test_dashboard_actions.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard_content.py MIFPAPP/CORE/mifp_app/templates/dashboard/_event_wizard.html MIFPAPP/CORE/mifp_app/static/js/dashboard/events.js TESTS/webapp/test_dashboard_actions.py
git commit -m "feat(control): event slug authority moved to Python"
```

---

### Task 6: Remove client-side classification forcing in data-quality

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js:180-188`
- Test: `TESTS/webapp/test_data_quality.py` (server already enforces; add a guard test if missing)

**Interfaces:**
- Consumes: the server-side automatic-only enforcement in `dashboard_data_quality.py:469-503`.
- Produces: the bulk-accept request no longer injects `filters.classification = 'automatic'` client-side.

- [ ] **Step 1: Write the failing test (server is authoritative)**

The bulk-decision endpoint (`dashboard_data_quality.py:469-478`) returns 400 when `decision == "accept"` and an explicit `workflow` other than `""`/`"automatic"` is supplied, and it always forces the `automatic` classification server-side (`dashboard_data_quality.py:501-503`) regardless of the client filter. The HTTP fixtures live in `TESTS/webapp/test_dashboard_actions.py` (its `client` fixture is already authenticated; no `_login` call needed). Append there:

```python
def test_data_quality_bulk_accept_rejects_explicit_non_automatic_workflow(client, app):
    """The server, not the browser, decides the automatic-only workflow."""
    response = client.post(
        "/dashboard/data-quality/bulk-decision",
        json={"decision": "accept", "workflow": "manual", "filters": {}},
    )
    assert response.status_code == 400
    assert b"only available for safe automatic findings" in response.data


def test_data_quality_bulk_accept_ignores_client_classification(client, app):
    """A stale browser classification must not narrow the automatic set."""
    response = client.post(
        "/dashboard/data-quality/bulk-decision",
        json={"decision": "accept", "workflow": "", "filters": {"classification": "manual"}},
    )
    # No 400: the server coerces the classification to automatic and ignores the
    # client filter. With no run findings, the endpoint reports 0 matched.
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
```

- [ ] **Step 2: Remove the JS forcing**

In `data-quality.js:180-188`, delete the `filters.classification = 'automatic';` line and its comment, leaving the filters as submitted:

```javascript
    var filters = Object.fromEntries(new FormData(filtersForm).entries());
    var btn = $('dqAcceptAll');
```

- [ ] **Step 3: Run the tests**

Run: `pytest TESTS/webapp/test_data_quality.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js TESTS/webapp/test_data_quality.py
git commit -m "fix(control): data-quality bulk accept relies on server workflow rule"
```

---

### Task 7: Copy/presentation pass for Safety wizard, Server, Control Center

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/control/safety_operations.html`
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/server.html`
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/control/_nav.html`
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/control/*.html` (title/description sweep)
- Test: `TESTS/webapp/test_safety_operations.py`, `TESTS/webapp/test_dashboard_actions.py`

**Interfaces:**
- Produces: uniform English terminology and clearer per-operation explanations. No route/URL changes.

- [ ] **Step 1: Write the failing tests**

Append to `TESTS/webapp/test_safety_operations.py`:

```python
def test_safety_wizard_explains_what_export_does_not_do(client):
    response = client.get("/dashboard/control/safety-operations")
    body = response.get_data(as_text=True)
    assert "Portable recovery export" in body
    assert "What it does" in body.lower() or "What this does" in body.lower()
    assert "password-gated" in body.lower() or "password" in body.lower()


def test_safety_wizard_review_explains_backup_and_cleanup(client):
    response = client.get("/dashboard/control/safety-operations")
    body = response.get_data(as_text=True)
    assert "Recovery snapshot" in body
    assert "Conservative cleanup" in body
    assert "No content rows or assets are changed" in body
```

Append to `TESTS/webapp/test_dashboard_actions.py` (or `test_db_dump.py`):

```python
def test_server_page_groups_panels_and_separates_actions(app_with_admin):
    client = app_with_admin.test_client()
    _login(client)
    response = client.get("/dashboard/server")
    body = response.get_data(as_text=True)
    assert "Guarded operations" in body
    assert "Protected operations" in body
    assert "Runtime" in body and "Security posture" in body
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_safety_operations.py -k "explains" TESTS/webapp/test_dashboard_actions.py -k "server_page_groups" -v`
Expected: FAIL (the wizard lacks "What it does" copy; Server page already has most labels but the test pins the wording).

- [ ] **Step 3: Safety wizard copy**

In `safety_operations.html`, add a short "What this does" line to each operation card (`data-wizard-panel="1"`, the `.safety-operation` labels) and expand the review panel (`data-operation-review`) copy:

- Backup card small text → keep "Consistent SQLite backup, integrity checked, newest two retained." and add a review bullet under `data-operation-review="backup"`:
```html
        <li><span>What this does</span><b>Creates a read-only snapshot. No content or assets are changed.</b></li>
```
- Export card small text → "Import-compatible mifp-jsonl-v2 ZIP with content, durable state and managed assets." and add under `data-operation-review="export"`:
```html
        <li><span>What this does</span><b>Builds a private ZIP in the background and downloads it once. The database is not modified.</b></li>
```
- Cleanup card small text → keep, and add under `data-operation-review="cleanup"`:
```html
        <li><span>What this does</span><b>Backs up first, then removes only retention-expired generated files and compacts SQLite.</b></li>
```

- [ ] **Step 4: Server page copy**

In `server.html`:
- Change the "Database maintenance" intro (`server.html:116`) to be explicit about scope:
```html
    <p>Integrity checks are read-only. VACUUM creates a safety backup before optimising SQLite. Downloads are prepared in the background.</p>
```
- The db-download warning (`server.html:181-184`) already explains the snapshot; add one line about the background preparation:
```html
            <span>This is a verified, consistent SQLite snapshot including committed WAL data. It is prepared in the background and downloaded once. Restore it only with “Restore database”, not through Import / Export.</span>
```

- [ ] **Step 5: Control Center nav/terminology sweep**

In `_nav.html`, ensure the section labels match the glossary (they already do: Overview, Processes, Content quality, Storage, Site check, Incidents, Backups, Configuration). Leave labels as-is. Sweep `control/*.html` page headers for any inconsistent casing/terms (e.g. "Control centre" vs "Control Center") — unify to "Control centre" only where it already appears, and leave the rest unchanged unless the test suite's copy assertions require it.

- [ ] **Step 6: Run the tests**

Run: `pytest TESTS/webapp/test_safety_operations.py TESTS/webapp/test_dashboard_actions.py TESTS/webapp/test_db_dump.py -q`
Expected: PASS

- [ ] **Step 7: Browser check**

Run `./mifp local`, open Control centre → Protected operations and the Server page, and verify the new copy renders and the export/db-dump jobs still work end-to-end from the UI.

- [ ] **Step 8: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/templates/dashboard/control/safety_operations.html MIFPAPP/CORE/mifp_app/templates/dashboard/server.html TESTS/webapp/test_safety_operations.py TESTS/webapp/test_dashboard_actions.py
git commit -m "feat(control): clearer safety-wizard and server copy"
```

---

### Task 8: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full webapp suite**

Run: `pytest TESTS/webapp -q`
Expected: PASS (all ~618 tests including the new ones)

- [ ] **Step 2: Lint/type sanity**

Run: `python -m compileall -q MIFPAPP/CORE/mifp_app/services/download_jobs.py MIFPAPP/CORE/mifp_app/routes/dashboard_control.py MIFPAPP/CORE/mifp_app/routes/dashboard.py MIFPAPP/CORE/mifp_app/routes/dashboard_content.py`
Expected: no output, exit 0.

- [ ] **Step 3: Browser smoke check**

Run `./mifp local`, log in, and exercise: safety-wizard **Backup** (still synchronous, redirects to verify page), **Export** (job status → one-time download), **Cleanup** (still synchronous); Server **Download database** (job status → one-time download); event create/update slug auto-generation and manual override; data-quality bulk accept (no client-side classification forcing).

- [ ] **Step 4: Update the design/plan status**

Mark the checklist items in `docs/superpowers/specs/2026-08-18-dashboard-clarity-and-python-owned-rules-design.md` Section B as implemented.

- [ ] **Step 5: Commit any stragglers**

```bash
git add -A
git commit -m "docs: mark control-center/server workstream complete"
```

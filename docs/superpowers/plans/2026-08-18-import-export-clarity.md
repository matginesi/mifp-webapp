# Import / Export Clarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard Import/Export page clearly report what is happening: monotonic, server-owned progress, real export progress, non-alarming copy, and explicit result counts.

**Architecture:** The server stays the single source of progress and rules. The JS renders streamed NDJSON events verbatim and stops blending/mixing percents. Export switches from build-then-stream to build-in-thread-stream-progress, reusing the import job pattern. Copy/terminology is corrected in templates and in the server-emitted result events.

**Tech Stack:** Flask (routes in `mifp_app/routes/dashboard.py`, builders in `mifp_app/services/data_portability.py`), vanilla JS (`mifp_app/static/js/dashboard/data-portability.js`), Jinja templates, pytest.

## Global Constraints

- The stream NDJSON protocol of Data portability is unchanged: events `phase`, `progress`, `file_start`, `file_done`, `detail`, `metrics`, `error`, `result`, `queued`. Do not introduce polling/reconnect for import.
- No panel restructure: `data_portability.html` layout stays as-is.
- `bundle_to_zip_file` / `bundle_to_jsonl_file` gain an optional `progress_callback` parameter (default `None`) — all existing callers keep working unchanged.
- UI copy is English and consistent ("Validate only" / "Import data", "Import / Export", "Protected operations").
- The webapp test suite is the versioned CI suite; run `pytest TESTS/webapp -q` after each task.
- Do not touch `mifp.db` from tests; tests use `tmp_path` fixtures.

---
## File Structure

- Modify `routes/dashboard.py` — monotonic import progress; threaded export; result copy; dead plumbing.
- Modify `services/data_portability.py` — `progress_callback` in `_write_bundle_zip`, `bundle_to_zip_file`, `bundle_to_jsonl_file`.
- Modify `static/js/dashboard/data-portability.js` — render global percent, upload indeterminate, batch-failure summary, JSONL limit check, drop `force_import` logging.
- Modify `templates/dashboard/data_portability.html` — dynamic dry-run modal copy, "Waiting…" placeholder, `maxJsonlBytes` config.
- Modify tests: `TESTS/webapp/test_data_portability_http.py`, `TESTS/webapp/test_exporters.py`, `TESTS/webapp/test_data_portability_zip.py` (as needed).

---

### Task 1: Monotonic server-owned import progress

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard.py:1384-1422` (`_perform_import_unprotected`) and the `progress` closure at `dashboard.py:1445-1466` and the loop at `dashboard.py:1467-1663`
- Test: `TESTS/webapp/test_data_portability_http.py` (new tests)

**Interfaces:**
- Produces: `progress` events now carry both `percent` (per-file, unchanged) and a new `global_percent` field (0-100, never decreasing within a request). `phase` events carry a monotonic `percent` anchor.

- [ ] **Step 1: Write the failing test**

Append to `TESTS/webapp/test_data_portability_http.py` inside `class TestDataPortabilityHTTP`.

**Corrected test (user-approved semantics, replaces the earlier verbatim draft):** the monotonicity check is evaluated in **stream order**, and the test asserts `global_percent` is actually present. (The grouped `globals_seen + phase_seen` comparison false-passes on old code — an empty `globals_seen` plus a sorted `phase_seen` is still sorted — and could never pass with the plan's own anchors since e.g. `[37, 90, 10, 98]` is unsorted. Stream-order monotonicity matches "never decreasing within a request" and Task 2's browser acceptance `10% → … → 90% → 98%`.)

```python
    def test_import_progress_percent_is_never_decreasing(self, app_with_admin):
        client = app_with_admin.test_client()
        _login(client)
        lines = [
            {"title": "One"},
            {"title": "Two"},
            {"title": "Three"},
        ]
        payload = "".join(json.dumps(row) + "\n" for row in lines).encode("utf-8")
        data = {"_csrf_token": "x", "password": "test-pass", "dry_run": "1"}
        data["data_file"] = (io.BytesIO(payload), "records.jsonl")
        resp = client.post(
            "/dashboard/data-portability/import",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200
        events = [json.loads(l) for l in resp.data.decode("utf-8").splitlines() if l.strip()]
        globals_seen = [
            ev["global_percent"]
            for ev in events
            if ev.get("event") == "progress" and ev.get("global_percent") is not None
        ]
        assert globals_seen, "expected server-owned global_percent in progress stream"
        all_values = [
            ev.get("global_percent")
            if ev.get("event") == "progress" and ev.get("global_percent") is not None
            else ev.get("percent")
            for ev in events
            if (ev.get("event") == "progress" and ev.get("global_percent") is not None)
            or (ev.get("event") == "phase" and ev.get("percent") is not None)
        ]
        assert all_values, "expected progress values in stream"
        assert all_values == sorted(all_values), f"percent went backwards: {all_values}"

    def test_import_phase_anchors_map_to_new_ranges(self, app_with_admin):
        client = app_with_admin.test_client()
        _login(client)
        payload = b'{"title": "Solo"}\n'
        data = {"_csrf_token": "x", "password": "test-pass", "dry_run": "1"}
        data["data_file"] = (io.BytesIO(payload), "records.jsonl")
        resp = client.post(
            "/dashboard/data-portability/import",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200
        events = [json.loads(l) for l in resp.data.decode("utf-8").splitlines() if l.strip()]
        phases = [ev for ev in events if ev.get("event") == "phase"]
        assert phases, "expected phase events"
        assert all(0 <= ev["percent"] <= 100 for ev in phases)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest TESTS/webapp/test_data_portability_http.py::TestDataPortabilityHTTP::test_import_progress_percent_is_never_decreasing -v`
Expected: FAIL (old code carries no `global_percent`, so the `assert globals_seen` presence check fails).

- [ ] **Step 3: Implement the monotonic percent helper**

Add a module-level helper class in `routes/dashboard.py` near `_perform_import`:

```python
class _MonotonicProgress:
    """Track the highest percent emitted so far for one import request."""

    def __init__(self) -> None:
        self._last = 0.0

    def __call__(self, pct: float) -> int:
        self._last = max(self._last, min(100.0, max(0.0, pct)))
        return round(self._last)
```

- [ ] **Step 4: Wire the helper into `_perform_import_unprotected`**

In `_perform_import_unprotected` (`dashboard.py:1412`), right after the function docstring/start, before the `if cancel_check` block, add:

```python
    mono = _MonotonicProgress()
```

Then make these replacements:

1. Backup phase (`dashboard.py:1429`):
```python
        event_sink({"event": "phase", "phase": "backup", "label": "Creating database backup…", "current_step": 0, "total_steps": 5, "percent": mono(5)})
```

2. Importing phase (`dashboard.py:1467`) — move `file_count = len(file_data)` above it and use it in the progress closure. Replace:
```python
        file_count = len(file_data)
        event_sink({"event": "phase", "phase": "importing", "label": "Importing records…", "current_step": 1, "total_steps": 5, "percent": mono(10)})
```

3. The `progress` closure (`dashboard.py:1445-1451`) — add `_current_index` nonlocal and emit `global_percent`:
```python
        def progress(file_name: str, done: int, total: int) -> None:
            nonlocal _per_file_totals, _total_records_aggregate, _current_index
            pct = round((done / max(total, 1)) * 100)
            global_pct = 10 + 80 * ((_current_index + (done / max(total, 1))) / max(file_count, 1))
            event_sink({
                "event": "progress", "current": done, "total": total,
                "file": file_name, "percent": pct,
                "global_percent": mono(global_pct),
            })
```

4. Declare `_current_index = 0` at the top of the `with connect(...)` block (near `_per_file_totals`), and set it at the top of the file loop, right after the `for file_index, (filename, payload) in enumerate(file_data):` line (`dashboard.py:1470`):
```python
            _current_index = file_index
```

5. Assets recovery phase (`dashboard.py:1635`) — replace `"percent": 60` with `"percent": mono(90)`.
6. Finalizing phase (`dashboard.py:1657`) — replace `"percent": 80` with `"percent": mono(98)`.

The result event (`dashboard.py:1700`) needs no percent.

- [ ] **Step 5: Run the new tests**

Run: `pytest TESTS/webapp/test_data_portability_http.py::TestDataPortabilityHTTP::test_import_progress_percent_is_never_decreasing TESTS/webapp/test_data_portability_http.py::TestDataPortabilityHTTP::test_import_phase_anchors_map_to_new_ranges -v`
Expected: PASS

- [ ] **Step 6: Run the import test module to confirm no regressions**

Run: `pytest TESTS/webapp/test_data_portability_http.py TESTS/webapp/test_data_portability_zip.py -q`
Expected: PASS (result messages are touched later in Task 3; keep them unchanged for now)

- [ ] **Step 7: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard.py TESTS/webapp/test_data_portability_http.py
git commit -m "feat(data-portability): server-owned monotonic import progress"
```

---

### Task 2: Client renders server progress without blending

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js:180-211, 226-231, 417-427, 646-657, 752-754`

**Interfaces:**
- Consumes: `global_percent` from `progress` events (Task 1); monotonic `percent` from `phase` events (Task 1).
- Produces: main progress bar driven only by `batchProgress(msg.global_percent ?? msg.percent)`; upload shows indeterminate; `force_import` removed from logs.

- [ ] **Step 1: Remove the upload blend that reaches 100%**

In `data-portability.js`, the `xhr.upload.addEventListener('progress', ...)` block (`data-portability.js:646-651`) currently calls `setProgress(batchProgress(upload.loaded / upload.total * 100))`. Replace the whole handler body so it does not touch the bar (which stays indeterminate from `resetModal`):

```javascript
    xhr.upload.addEventListener('progress', function (upload) {
      if (!upload.lengthComputable) return;
      status.textContent = 'Uploading package ' + (batchIndex + 1) + ' of ' + batchTotal + '…';
      detail.textContent = sizeLabel(upload.loaded) + ' of ' + sizeLabel(upload.total) + ' uploaded in this package';
    });
```

- [ ] **Step 2: Use server global percent for the main bar**

In `handleStreamMessage`, the `phase` branch (`data-portability.js:417-419`) stays (it already feeds `batchProgress(msg.percent)`). Update the `progress` branch (`data-portability.js:420-427`) so the per-file bar uses the per-file `percent` and the main bar uses `global_percent`:

```javascript
    } else if (msg.event === 'progress') {
      var fileEl = fileProgressEls[msg.file];
      if (fileEl && msg.percent != null) {
        fileEl.querySelector('.file-bar-fill').style.width = msg.percent + '%';
        fileEl.querySelector('small').textContent = msg.percent + '%';
      }
      if (msg.global_percent != null) setProgress(batchProgress(msg.global_percent));
      if (detail && msg.file) detail.textContent = msg.file + ': ' + msg.current + '/' + msg.total;
    }
```

- [ ] **Step 3: Replace the "4%" placeholder**

In `data-portability.js`, `resetModal` (`data-portability.js:251`) currently sets `percent.textContent = 'Working…'`. Leave it (it is already non-magic). In the template `data_portability.html:200` the static initial text is `4%`; that is changed in Task 6 (template edits are grouped there). No JS change needed here beyond Step 2.

- [ ] **Step 4: Remove `force_import` from the auth log**

In `startAuthorizedImport` (`data-portability.js:746-755`), remove the `force_import: Boolean(...)` line and the comma after `skip_assets`:

```javascript
    transferLog.info('import.authorization_submitted', {
      files: files.length, batches: batches.length,
      bytes: totalBytes, dry_run: dryRun,
      skip_assets: Boolean(form.querySelector('[name="skip_assets"]')?.checked),
    });
```

- [ ] **Step 5: Verify**

Run: `pytest TESTS/webapp/test_data_portability_http.py -q`
Expected: PASS (server behaviour unchanged; JS is not covered by pytest, so this is a no-regression check).

Open the Import/Export page with `./mifp local`, stage one JSONL and run a **Validate only** run. Confirm in the browser that:
- The bar shows "Waiting…" during upload and never jumps to 100% before the server responds.
- After upload completes the bar advances monotonically (10% → … → 90% → 98%) to the result.

- [ ] **Step 6: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js
git commit -m "feat(data-portability): render server progress without client blending"
```

---

### Task 3: Export streams real progress from the bundle builders

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_portability.py:511-703`
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard.py:946-1075` (`data_portability_export_post`)
- Test: `TESTS/webapp/test_exporters.py`, `TESTS/webapp/test_data_portability_http.py`

**Interfaces:**
- Produces:
  - `progress_callback: Callable[[str, int], None] | None = None` optional keyword on `bundle_to_zip_file(conn, scope, assets_dir, destination, *, app_version="", progress_callback=None) -> int` and `bundle_to_jsonl_file(..., progress_callback=None) -> dict`. The callback receives `(message, percent)`.
  - `_write_bundle_zip` gains the same optional parameter and reports milestones: `("Collecting records…", 5)`, `("Serializing records…", 15)`, per asset `("Packaging assets i/N…", 15 + 70*i//N)`, `("Writing manifest…", 92)`, `("Finalizing…", 100)`.
  - `bundle_to_jsonl_file` reports: `("Collecting records…", 5)`, `("Serializing records…", 15)`, per asset `("Packaging assets i/N…", 15 + 70*i//N)`, `("Finalizing…", 100)`.

- [ ] **Step 1: Write the failing unit tests**

`test_exporters.py` currently has no Flask fixture, so add one first (mirroring the pattern from `test_data_portability_http.py`; it stages an isolated app that writes only to `tmp_path`):

```python
import os
from werkzeug.security import generate_password_hash


@pytest.fixture
def app_with_admin(tmp_path):
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
    for key in ("DATABASE_PATH", "EXPORT_DIR", "ASSETS_DIR", "LOG_DIR", "SECRET_KEY", "LOG_ACCESS_ENABLED", "TESTING"):
        os.environ.pop(key, None)
```

Note: the fixture must `mkdir` the EXPORT_DIR/ASSETS_DIR because the export tests write into them; `create_app()` only warns about missing dirs and the fixture leaves the default paths (which would resolve to the real `MIFPAPP/DATABASE/...`) unless the env vars above are set before `create_app()`.

Then append the tests:

```python
def test_bundle_to_zip_file_reports_progress(app_with_admin):
    from mifp_app.services.data_portability import bundle_to_zip_file

    app = app_with_admin
    events = []

    def cb(message: str, pct: int) -> None:
        events.append((message, pct))

    with app.app_context():
        from mifp_app.db.connection import connect
        with connect(app.config["DATABASE_PATH"]) as conn:
            bundle_to_zip_file(conn, "all", app.config["ASSETS_DIR"],
                               app.config["EXPORT_DIR"] / "progress.zip",
                               app_version="test", progress_callback=cb)
    assert events, "expected progress milestones"
    percents = [pct for _, pct in events]
    assert percents == sorted(percents), f"percent went backwards: {percents}"
    assert events[-1][1] == 100


def test_bundle_to_jsonl_file_reports_progress(app_with_admin):
    from mifp_app.services.data_portability import bundle_to_jsonl_file

    app = app_with_admin
    events = []

    def cb(message: str, pct: int) -> None:
        events.append((message, pct))

    with app.app_context():
        from mifp_app.db.connection import connect
        with connect(app.config["DATABASE_PATH"]) as conn:
            bundle_to_jsonl_file(conn, "all", app.config["ASSETS_DIR"],
                                 app.config["EXPORT_DIR"] / "progress.jsonl",
                                 app_version="test", progress_callback=cb)
    assert events, "expected progress milestones"
    assert events[-1][1] == 100
```

The important assertions are: milestones exist, percents never decrease, last is 100. The tests require no login; the fixture provides the app context and isolated paths only.

- [ ] **Step 2: Run the failing tests**

Run: `pytest TESTS/webapp/test_exporters.py -k progress -v`
Expected: FAIL with `TypeError: bundle_to_zip_file() got an unexpected keyword argument 'progress_callback'`

- [ ] **Step 3: Add the callback to `_write_bundle_zip`**

In `services/data_portability.py:511`, change the signature:

```python
def _write_bundle_zip(
    conn: sqlite3.Connection,
    scope: str,
    assets_dir: Path,
    target: BytesIO | Path,
    *,
    app_version: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
```

Add a local helper at the top of the function body:

```python
    def report(message: str, pct: int) -> None:
        if progress_callback:
            progress_callback(message, pct)
```

Then insert milestone calls:
- After `bundle = build_export_bundle(conn, scope)` → `report("Collecting records…", 5)`
- After `records_payload = _records_to_jsonl(records)` → `report("Serializing records…", 15)`
- Inside the `for asset in asset_rows:` loop, right after the `seen_archive_paths.add(archive_path)` line, add:
```python
            report(f"Packaging assets {len(seen_archive_paths)}/{len(asset_rows)}…", 15 + 70 * len(seen_archive_paths) // max(len(asset_rows), 1))
```
- After the loop, before `zf.writestr(ZIP_MANIFEST_NAME, ...)` → `report("Writing manifest…", 92)`
- Just before `return manifest` → `report("Finalizing…", 100)`

- [ ] **Step 4: Add the callback to `bundle_to_zip_file`**

In `services/data_portability.py:588`:

```python
def bundle_to_zip_file(
    conn: sqlite3.Connection,
    scope: str,
    assets_dir: Path,
    destination: Path,
    *,
    app_version: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
) -> int:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_bundle_zip(conn, scope, assets_dir, destination,
                      app_version=app_version, progress_callback=progress_callback)
    return destination.stat().st_size
```

- [ ] **Step 5: Add the callback to `bundle_to_jsonl_file`**

In `services/data_portability.py:605`, add the parameter and report milestones inside the function body using the same `report` helper pattern:

- After `records_payload = _records_to_jsonl(records)` → `report("Collecting records…", 5)` / `report("Serializing records…", 15)` at the same places as the ZIP builder.
- In the `for asset in asset_rows:` loop, after `packaged_assets.append({...})`, add:
```python
        report(f"Packaging assets {len(packaged_assets)}/{len(asset_rows)}…", 15 + 70 * len(packaged_assets) // max(len(asset_rows), 1))
```
- Just before `return manifest` → `report("Finalizing…", 100)`.

(Read the tail of `bundle_to_jsonl_file` before editing so the milestone placement matches its actual structure.)

- [ ] **Step 6: Run the progress tests**

Run: `pytest TESTS/webapp/test_exporters.py -k progress -v`
Expected: PASS

- [ ] **Step 7: Thread the export route and stream progress**

Rework `data_portability_export_post` (`routes/dashboard.py:946-1075`):

1. Capture session-derived values before any thread starts (Flask `session` is request-scoped and unavailable in worker threads). Right after `filename`/`token` are computed, add:
```python
    export_owner = session.get("admin_username")
    export_session_key = _export_session_key()
```

2. Replace the synchronous build block (`dashboard.py:999-1028`) with a queued worker, mirroring the import route. Keep the `try/except` semantics but inside the worker. The new body:

```python
    import queue

    from ..services.job_manager import JobQueueFull, get_job_manager

    event_queue: queue.Queue[dict | None] = queue.Queue()
    app = current_app._get_current_object()

    def progress_cb(message: str, pct: int) -> None:
        event_queue.put({"event": "phase", "phase": "bundle", "label": message, "percent": pct})

    def run_export() -> None:
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
```

3. Replace the tail `generate()` (`dashboard.py:1058-1067`) to drain the queue:

```python
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
```

Also remove the now-dead `started`-based `duration_ms` and `size_str`/`cached_exports` computations that used to live before `generate()` (they moved into the worker). Remove the old `except Exception` block that returned a 500 NDJSON stream (`dashboard.py:1029-1044`); error handling now lives inside `run_export`. Ensure the `job_id` variable is in scope for `generate` (it is, defined by `manager.submit`).

Keep `time` and `Response` imports (both already imported). Verify `json`, `secrets`, `tempfile`, `operation_maintenance`, `connect`, `_prune_export_cache`, `_cache_export_file`, `_export_session_key`, `_export_cache_count` are imported/available (they already are in `routes/dashboard.py`).

- [ ] **Step 8: Run the export test module**

Run: `pytest TESTS/webapp/test_data_portability_http.py -k "export" -q`
Expected: PASS. Existing tests assert the NDJSON result contains `download_token`; they already tolerate `queued`/`phase` events. If `test_export_requires_password_before_creating_any_file` or `test_export_uses_explicit_user_download_control` asserts event ordering, adjust the assertion to scan events rather than check position (progress now precedes the result).

Run: `pytest TESTS/webapp/test_exporters.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/services/data_portability.py MIFPAPP/CORE/mifp_app/routes/dashboard.py TESTS/webapp/test_exporters.py TESTS/webapp/test_data_portability_http.py
git commit -m "feat(data-portability): stream real export progress via background job"
```

---

### Task 4: Result copy and dry-run modal clarity

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard.py:1691-1724` (result title/message)
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/data_portability.html:104-127, 200`
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js:786-787, 790-804`
- Test: `TESTS/webapp/test_data_portability_http.py`

**Interfaces:**
- Produces: `result` events whose `title_text`/`message` contain explicit counts and never claim the database "will change" for dry-runs. The import auth modal gets a dynamic operation-specific notice.

- [ ] **Step 1: Write the failing tests**

Append to `TestDataPortabilityHTTP` in `TESTS/webapp/test_data_portability_http.py`:

```python
    def test_import_dry_run_result_mentions_counts_and_no_changes(self, app_with_admin):
        client = app_with_admin.test_client()
        _login(client)
        payload = b'{"title": "Dry"}'
        data = {"_csrf_token": "x", "password": "test-pass", "dry_run": "1"}
        data["data_file"] = (io.BytesIO(payload), "records.jsonl")
        resp = client.post(
            "/dashboard/data-portability/import",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        events = [json.loads(l) for l in resp.data.decode("utf-8").splitlines() if l.strip()]
        result = next(ev for ev in events if ev.get("event") == "result")
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert "Validation completed" in result["title_text"]
        assert "would be inserted" in result["message"]

    def test_import_real_result_reports_explicit_counts(self, app_with_admin):
        client = app_with_admin.test_client()
        _login(client)
        payload = b'{"title": "Real"}'
        data = {"_csrf_token": "x", "password": "test-pass", "dry_run": "0"}
        data["data_file"] = (io.BytesIO(payload), "records.jsonl")
        resp = client.post(
            "/dashboard/data-portability/import",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        events = [json.loads(l) for l in resp.data.decode("utf-8").splitlines() if l.strip()]
        result = next(ev for ev in events if ev.get("event") == "result")
        assert result["ok"] is True
        assert "1 inserted" in result["message"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest TESTS/webapp/test_data_portability_http.py -k "result_mentions" -v`
Expected: FAIL (current messages: dry-run title "Files are valid", message "Import completed in Xs.").

- [ ] **Step 3: Server result copy**

In `_perform_import_unprotected` (`dashboard.py:1691-1707`), replace the title/message computation:

```python
    _result_title = (
        "Validation completed" if (ok and dry_run) else
        "Import completed with issues" if outcome == "warning" else
        "Import complete" if ok else
        "Import failed"
    )
    _result_icon = "bi-exclamation-lg" if outcome == "warning" else "bi-check-lg" if ok else "bi-x-lg"
    _result_icon_class = "is-warning" if outcome == "warning" else "is-success" if ok else "is-error"

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
```

Then in the `event_sink({"event": "result", ...})` payload (`dashboard.py:1700-1724`), replace `"message": f"Import completed in {duration_s}s." if ok else f"Import completed with {total_errors + total_asset_errors} error(s)."` with `"message": _result_message,`.

- [ ] **Step 4: Run the copy tests**

Run: `pytest TESTS/webapp/test_data_portability_http.py -k "result_mentions" -v`
Expected: PASS

- [ ] **Step 5: Dry-run modal copy**

In `data_portability.html:108-115`, give the notice span an id and a neutral default:

```html
      <div class="modal-body">
        <div class="export-auth-notice">
          <i class="bi bi-database-lock" aria-hidden="true"></i>
          <div><b>Confirm this sensitive operation</b><span id="importAuthNotice">The selected files can change database records and managed assets. Enter the administrator password to continue.</span></div>
        </div>
```

In `data-portability.js`, in the import form submit handler (`data-portability.js:786-787`), set the notice and the modal title based on `dryRun`:

```javascript
    var dryRun = form.querySelector('[name="dry_run"]:checked')?.value === '1';
    pendingImportRequest = { files: files, dryRun: dryRun };
    if (importAuthOperation) importAuthOperation.textContent = dryRun ? 'Validate selected files' : 'Import selected data';
    var notice = document.getElementById('importAuthNotice');
    if (notice) {
      notice.textContent = dryRun
        ? 'Validation checks the files only. No records or assets will be changed. Enter the administrator password to continue.'
        : 'The selected files can change database records and managed assets. Enter the administrator password to continue.';
    }
    if (importAuthTitle) importAuthTitle.firstChild.textContent = dryRun ? ' Confirm validation' : ' Authorize secure import';
```

Add `var importAuthTitle = document.getElementById('importAuthTitle');` near the other element lookups at the top of the file (line ~45). Note: `importAuthTitle` currently has an icon `<i>` followed by text; setting `firstChild.textContent` updates the text node only. If the first child is whitespace, target the last text node instead:

```javascript
    if (importAuthTitle) {
      var titleText = importAuthTitle.lastChild && importAuthTitle.lastChild.nodeType === Node.TEXT_NODE
        ? importAuthTitle.lastChild : null;
      if (titleText) titleText.textContent = dryRun ? ' Confirm validation' : ' Authorize secure import';
    }
```

- [ ] **Step 6: Verify dry-run copy and "Waiting…"**

Run: `pytest TESTS/webapp/test_data_portability_http.py -q`
Expected: PASS

Open the page in the browser (`./mifp local`): pick a file, choose **Validate only**, confirm the auth modal reads "Confirm validation" with "No records or assets will be changed", and the transfer modal shows "Waiting…" before events arrive.

- [ ] **Step 7: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard.py MIFPAPP/CORE/mifp_app/templates/dashboard/data_portability.html MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js TESTS/webapp/test_data_portability_http.py
git commit -m "feat(data-portability): explicit result counts and dry-run modal copy"
```

---

### Task 5: Preserve successful-batch summary on later failure

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js:703-744` (`runImportQueue`)

**Interfaces:**
- Consumes: the `summary` accumulator already maintained in `runImportQueue`.
- Produces: on batch failure, the final `result` event includes the counts of completed batches instead of discarding them.

- [ ] **Step 1: Merge summary into the failure payload**

Replace the `catch` block in `runImportQueue` (`data-portability.js:726-736`):

```javascript
    } catch (failure) {
      failure = failure || {};
      failure.event = 'result';
      failure.ok = false;
      failure.dry_run = dryRun;
      ['inserted', 'updated', 'skipped', 'rolled_back', 'linked_assets', 'asset_errors', 'errors', 'new_assets', 'downloaded_assets'].forEach(function (key) {
        if (failure[key] == null) failure[key] = summary[key];
      });
      if (!failure.error_details) failure.error_details = summary.error_details;
      if (!failure.by_type) failure.by_type = summary.by_type;
      failure.title_text = failure.title_text || 'Import queue stopped';
      var prefix = completed ? completed + ' of ' + batches.length + ' uploads completed. ' : '';
      failure.message = prefix + (failure.message || 'The next package could not be imported.');
      failure.icon_class = failure.icon_class || 'bi-x-lg';
      failure.icon_modifier = failure.icon_modifier || 'is-error';
      showResult(failure);
    } finally {
```

- [ ] **Step 2: Verify**

Run: `pytest TESTS/webapp/test_data_portability_http.py -q`
Expected: PASS (server unchanged; JS is verified in-browser).

Manual browser check: stage two JSONL files that form two upload batches (or two ZIPs), let the first import and force a failure on the second (e.g., pick a second file with an invalid schema). The result screen must show the first batch's Inserted/Updated counts alongside the error.

- [ ] **Step 3: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js
git commit -m "fix(data-portability): keep completed-batch summary when a later batch fails"
```

---

### Task 6: JSONL client-side limit and dead plumbing cleanup

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js:136-153` (`selectionProblem`)
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/data_portability.html:200, 249-258`
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard.py:1161, 1209` (dead `scope`/`force_import` reads)

**Interfaces:**
- Consumes: new `maxJsonlBytes` config value from the `dataPortabilityConfig` JSON.
- Produces: `selectionProblem` flags oversized `.jsonl`/`.json` files with a clear message; the import route no longer reads `force_import`/`scope` from the form.

- [ ] **Step 1: Add the config value**

In `data_portability.html:249-258`, add `'maxJsonlBytes': config.IMPORT_MAX_JSONL_BYTES` to the JSON config blob:

```html
  'maxUploadBytes': config.MAX_CONTENT_LENGTH,
  'maxZipBytes': config.IMPORT_MAX_ZIP_BYTES,
  'maxJsonlBytes': config.IMPORT_MAX_JSONL_BYTES
```

- [ ] **Step 2: Client-side JSONL limit**

In `selectionProblem` (`data-portability.js:136-153`), add a JSONL check between the ZIP and generic checks:

```javascript
    var oversizedJsonl = files.find(function (file) {
      return /\.jsonl?$/i.test(String(file.name || ''))
        && Number(config.maxJsonlBytes || 0) > 0
        && file.size > Number(config.maxJsonlBytes);
    });
    if (oversizedJsonl) {
      return oversizedJsonl.name + ' is ' + sizeLabel(oversizedJsonl.size) + '; each JSON/JSONL file is limited to ' + sizeLabel(Number(config.maxJsonlBytes)) + '.';
    }
```

- [ ] **Step 3: Replace the "4%" placeholder**

In `data_portability.html:200`, change `<span id="transferPercent">4%</span>` to `<span id="transferPercent">Waiting…</span>`.

- [ ] **Step 4: Drop dead form reads in the import route**

In `data_portability_import` (`dashboard.py:1161`), replace:

```python
    scope = request.form.get("scope", "").strip() or "all"
```
with:
```python
    scope = "all"
```

At `dashboard.py:1209`, remove:
```python
        force_import = request.form.get("force_import") == "1"
```
and update the two `current_app.logger.info` calls at `dashboard.py:1211-1214` and `1225-1229` to drop the `force_import` argument/format specifier (keep the variables `dry_run`, `skip_assets`). Keep the `force_import=force_import` kwargs in `_perform_import`/`_import_postback` calls unchanged by binding `force_import = False` at the top of the `try` block (or by passing `False`). Simplest: add `force_import = False` right after `scope = "all"` so all downstream call sites keep working.

- [ ] **Step 5: Verify**

Run: `pytest TESTS/webapp/test_data_portability_http.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/templates/dashboard/data_portability.html MIFPAPP/CORE/mifp_app/static/js/dashboard/data-portability.js MIFPAPP/CORE/mifp_app/routes/dashboard.py
git commit -m "feat(data-portability): client-side JSONL limit and dead plumbing cleanup"
```

---

### Task 7: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full webapp suite**

Run: `pytest TESTS/webapp -q`
Expected: PASS (all ~618 tests including the new ones)

- [ ] **Step 2: Lint/type sanity**

Run: `python -m compileall -q MIFPAPP/CORE/mifp_app/routes/dashboard.py MIFPAPP/CORE/mifp_app/services/data_portability.py`
Expected: no output, exit 0.

- [ ] **Step 3: Browser smoke check**

Run `./mifp local`, log in, and exercise: a **Validate only** import (monotonic bar, "Confirm validation" modal, "Waiting…" state), a **real import** of a small records.jsonl (counts in the result), a **ZIP export** and a **JSONL export** (progress streaming, download works), and a rejected oversized JSONL on the client before upload.

- [ ] **Step 4: Update the design/plan status**

Mark the checklist items in `docs/superpowers/specs/2026-08-18-dashboard-clarity-and-python-owned-rules-design.md` Section A as implemented.

- [ ] **Step 5: Commit any stragglers**

```bash
git add -A
git commit -m "docs: mark import/export clarity workstream complete"
```

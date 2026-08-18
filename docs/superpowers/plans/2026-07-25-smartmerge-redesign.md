# SmartMerging Redesign Implementation Plan

> **Implementation status (2026-07-25): completed and verified.**
>
> The corrections below supersede conflicting snippets later in this document:
>
> - `merge_candidates()` accepts a database `Path`, not an open connection. This
>   lets the executor own one `BEGIN IMMEDIATE` transaction and close its
>   connection deterministically.
> - Analysis and merge streaming endpoints are authenticated `POST` routes.
>   They use the existing CSRF header; no state-changing `GET` endpoint is
>   permitted.
> - Streaming uses `fetch()` plus `ReadableStream` in the browser and a
>   queue-backed worker on the server. Buffering all progress events until the
>   merge finishes is not real-time progress and is therefore not an acceptable
>   implementation.
> - Every selected candidate is validated before the backup and before any
>   mutation. Missing, overlapping, cross-run, unresolved, blocked, and stale
>   selections fail the whole request; stale candidates are not silently
>   skipped.
> - A merge marks processed candidates as merged, records history, creates one
>   verified SQLite backup, and commits all operations in one transaction.
> - The server chooses a coherent, non-overlapping subset for “merge all safe”;
>   selection and batch sizes are capped at 500 candidates.
> - The focused suite contains four behavior-oriented tests, not the obsolete
>   bundle-count expectations shown below.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-step bundle workflow (analyze → decide → create bundle → dry-run → apply) with a direct 3-step flow (analyze → select → merge) with SSE progress bars.

**Architecture:** Remove `planner.py` (bundle creation), simplify `executor.py` to work directly on candidate lists, add SSE streaming for real-time progress. Frontend rewritten with 2 tabs (Analysis + Merge candidates), inline candidate detail, batch selection toolbar.

**Tech Stack:** Flask, SQLite, SSE (Server-Sent Events), vanilla JS (no framework), CSS custom properties

## Global Constraints

- All new routes require `@login_required`
- CSRF validation on POST endpoints; SSE GET endpoints safe because only authenticated users can access
- Bundle database tables kept for historical data; no migration needed
- Existing analyzer/normalization/repository unchanged except removing bundle functions
- Tests must preserve normalization, analysis, decision persistence, scale guard tests

---

### Task 1: Service layer — add `merge_candidates()`, remove `planner.py`

**Files:**
- Create: (none)
- Modify: `MIFPAPP/CORE/mifp_app/services/smart_merge/executor.py` (add `merge_candidates()`, remove `dry_run_bundle()`, `apply_bundle()`)
- Delete: `MIFPAPP/CORE/mifp_app/services/smart_merge/planner.py`
- Modify: `MIFPAPP/CORE/mifp_app/services/smart_merge/__init__.py`

**Interfaces:**
- Consumes: `executor._merge_entity_operation()`, `executor._merge_asset_operation()`, `executor.QuarantineManager`, `executor._integrity_checks()`, `executor._current_candidate_fingerprint()`, `repository.get_candidate()`, `admin_safety.backup_sqlite_database()`
- Produces: `merge_candidates(conn, assets_dir, candidate_ids, progress_callback=None) -> dict`

- [ ] **Step 1: Remove `planner.py`**

Delete the file. Its `decide_candidate()` function is actually in `repository.py` as `save_decision()` — the `planner.decide_candidate()` was just a thin wrapper. The `create_bundle()` function is the bundle logic being removed.

Run: `rm MIFPAPP/CORE/mifp_app/services/smart_merge/planner.py`

- [ ] **Step 2: Update `executor.py` — remove bundle-specific imports**

In `executor.py`, remove `get_bundle`, `mark_bundle_failed`, `update_bundle_validation` from the import:
```python
from .repository import ENTITY_TABLES, get_candidate
```

- [ ] **Step 3: Update `executor.py` — remove `dry_run_bundle()` and `apply_bundle()` functions**

Remove lines 128-237 entirely (the `dry_run_bundle()`, `apply_bundle()` functions and their imports/usages). Keep `QuarantineManager`, `_current_candidate_fingerprint()`, `_merge_entity_operation()`, `_apply_field_plan()`, `_move_asset_links()`, `_move_entity_links()`, `_move_relations()`, `_normalize_primary_flags()`, `_merge_asset_operation()`, `_integrity_checks()`.

- [ ] **Step 4: Add `merge_candidates()` to `executor.py`**

Add this new function after the class definitions (before `_current_candidate_fingerprint`):

```python
def merge_candidates(
    db_path: Path,
    assets_dir: Path,
    candidate_ids: list[int],
    progress_callback: Callable[[int, int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    db_path = Path(db_path)
    assets_dir = Path(assets_dir)
    candidate_ids = sorted({int(value) for value in candidate_ids})
    if not candidate_ids:
        raise ValueError("No candidates provided")

    with connect(db_path) as conn:
        candidates: list[dict[str, Any]] = []
        for cid in candidate_ids:
            candidate = get_candidate(conn, cid)
            if not candidate:
                raise ValueError(f"Candidate {cid} not found")
            if candidate.get("classification") in {"blocked", "not_duplicate"}:
                raise ValueError(f"Candidate {cid} is blocked or not a duplicate")
            if candidate.get("classification") != "safe" and candidate.get("decision_state") != "approved":
                raise ValueError(f"Candidate {cid} requires explicit approval")
            if any(bool(item.get("requires_review")) for item in (candidate.get("field_plan") or [])):
                raise ValueError(f"Candidate {cid} has unresolved field conflicts")
            candidates.append(candidate)

        consumed: dict[tuple[str, int], int] = {}
        operations: list[dict[str, Any]] = []
        for candidate in candidates:
            keys = [(str(candidate["entity_type"]), int(rid)) for rid in candidate.get("record_ids", [])]
            collision = next((key for key in keys if key in consumed), None)
            if collision:
                raise ValueError(f"Candidate {candidate['id']} overlaps with candidate {consumed[collision]}")
            for key in keys:
                consumed[key] = int(candidate["id"])
            canonical_id = int(candidate.get("canonical_id") or 0)
            record_ids = [int(rid) for rid in candidate.get("record_ids", [])]
            absorb_ids = [rid for rid in record_ids if rid != canonical_id]
            operations.append({
                "candidate_id": int(candidate["id"]),
                "entity_type": str(candidate["entity_type"]),
                "table": str(candidate["table_name"]),
                "canonical_id": canonical_id,
                "absorb_ids": absorb_ids,
                "record_ids": record_ids,
                "fingerprint": str(candidate["fingerprint"]),
                "classification": str(candidate["classification"]),
                "field_plan": list(candidate.get("field_plan") or []),
                "impact": candidate.get("impact") or {},
            })

    backup = backup_sqlite_database(db_path, label="smart-merge")
    if backup is None:
        raise RuntimeError("Database backup could not be created")

    with connect(db_path) as conn:
        quarantine = QuarantineManager(assets_dir, 0)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = {"operations": 0, "records_removed": 0, "fields_updated": 0, "links_moved": 0, "assets_moved": 0, "relations_moved": 0, "files_quarantined": 0, "bytes_quarantined": 0}

            for idx, operation in enumerate(operations):
                cid = int(operation["candidate_id"])
                total = len(operations)
                title = str(next((c["title"] for c in candidates if int(c["id"]) == cid), f"Candidate #{cid}"))
                if progress_callback:
                    progress_callback(idx, total, cid, title, "merging")

                candidate = get_candidate(conn, cid)
                if not candidate:
                    if progress_callback:
                        progress_callback(idx, total, cid, title, "skipped")
                    continue
                current_fp = _current_candidate_fingerprint(conn, candidate)
                if not current_fp or current_fp != str(operation.get("fingerprint") or ""):
                    if progress_callback:
                        progress_callback(idx, total, cid, title, "skipped")
                    continue

                if operation["entity_type"] == "asset":
                    item = _merge_asset_operation(conn, operation, quarantine)
                else:
                    item = _merge_entity_operation(conn, operation)
                result["operations"] += 1
                for key in ("records_removed", "fields_updated", "links_moved", "assets_moved", "relations_moved", "files_quarantined", "bytes_quarantined"):
                    result[key] += int(item.get(key) or 0)
                if progress_callback:
                    progress_callback(idx, total, cid, title, "merged")

            _integrity_checks(conn)
            manifest_name = quarantine.finalize()
            result["quarantine_manifest"] = manifest_name
            result["backup"] = {"filename": backup.name, "size": backup.stat().st_size, "verified": True}
            conn.execute(
                "INSERT INTO smart_merge_history(action, run_id, details_json) VALUES('merge.applied', ?, ?)",
                (candidates[0]["run_id"] if candidates else None, json.dumps(result, ensure_ascii=False, default=str)),
            )
            conn.commit()
            return {"ok": True, "result": result}
        except Exception:
            conn.rollback()
            quarantine.rollback()
            raise
```

Add the `Callable` import at the top:
```python
from collections.abc import Callable
```

- [ ] **Step 5: Update `executor.py` — remove `BundleValidationError` class**

Since it was only used by the bundle functions being removed, remove the `BundleValidationError` class (lines 17-18). Replace its usage in `QuarantineManager._safe_asset_path()` with `ValueError`.

In `QuarantineManager._safe_asset_path()`, change:
```python
raise BundleValidationError("Unsafe asset path")
```
to:
```python
raise ValueError("Unsafe asset path")
```

(Do the same for all other `BundleValidationError` raises in `QuarantineManager` and the remaining helper functions — they may need to be replaced with `ValueError`.)

- [ ] **Step 6: Update `__init__.py`**

```python
from .executor import merge_candidates
from .repository import get_candidate, get_run, latest_run, list_candidates

__all__ = [
    "analyze_database", "merge_candidates", "decide_candidate",
    "get_candidate", "get_run", "latest_run", "list_candidates",
]
```

Note: `decide_candidate` is now imported separately — let me check where it's defined.

Actually, `decide_candidate` is in `planner.py` but the actual logic is `repository.save_decision()`. I need to re-export it. Let me import it directly from repository:

```python
from .executor import merge_candidates
from .repository import get_candidate, get_run, latest_run, list_candidates, save_decision as decide_candidate
```

- [ ] **Step 7: Run tests to verify**

Run: `python -m pytest TESTS/webapp/test_smart_merge.py -x -v`

Expected: 2 tests should fail (the bundle tests), the rest should pass. After fixing tests in Task 7, all should pass.

---

### Task 2: Repository — remove bundle functions, update imports

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/smart_merge/repository.py`

- [ ] **Step 1: Remove bundle functions**

Remove these functions from `repository.py`:
- `create_bundle_row()` (lines 415-429)
- `get_bundle()` (lines 432-434)
- `list_bundles()` (lines 437-442)
- `_hydrate_bundle()` (lines 445-452)
- `update_bundle_validation()` (lines 455-460)
- `mark_bundle_applied()` (lines 463-477)
- `mark_bundle_failed()` (lines 480-490)

- [ ] **Step 2: Verify no remaining references**

Run: `rg "create_bundle_row|get_bundle|list_bundles|_hydrate_bundle|update_bundle_validation|mark_bundle_applied|mark_bundle_failed" MIFPAPP/CORE/mifp_app/`

Expected: No matches (except possibly in historical data or comments)

---

### Task 3: Routes — add SSE merge-stream + POST merge, remove bundle routes

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard.py`

- [ ] **Step 1: Update imports in `dashboard.py`**

Replace the smart_merge import block:
```python
from ..services.smart_merge import (
    analyze_database as smart_merge_analyze_database,
    decide_candidate as smart_merge_decide_candidate,
    get_candidate as smart_merge_get_candidate,
    get_run as smart_merge_get_run,
    latest_run as smart_merge_latest_run,
    list_candidates as smart_merge_list_candidates,
    merge_candidates as smart_merge_merge_candidates,
)
```

- [ ] **Step 2: Remove bundle routes**

Remove these 4 route handlers from `dashboard.py`:
- `smart_merge_bundles_create()` (lines 1066-1091)
- `smart_merge_bundle()` (lines 1094-1101)
- `smart_merge_bundle_dry_run()` (lines 1104-1116)
- `smart_merge_bundle_apply()` (lines 1119-1138)

- [ ] **Step 3: Add `/smart-merge/merge-stream` SSE route**

After line ~1064 (after `smart_merge_candidate_decision`):

```python
@bp.get("/smart-merge/merge-stream")
@login_required
def smart_merge_merge_stream():
    import time
    run_id = request.args.get("run_id", type=int)
    mode = request.args.get("mode", "").strip()
    raw_ids = request.args.get("candidate_ids", "").strip()

    if mode == "safe" and run_id:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            result = smart_merge_list_candidates(conn, run_id=run_id, classification="safe", per_page=500, sort="score")
            candidate_ids = [item["id"] for item in result["items"]]
    elif raw_ids:
        candidate_ids = [int(value) for value in raw_ids.split(",") if value.strip()]
    else:
        return jsonify({"error": "Specify mode=safe&run_id=X or candidate_ids=1,2,3"}), 400

    if not candidate_ids:
        return jsonify({"error": "No candidates to merge"}), 400

    def generate():
        def progress(current, total, cid, title, status):
            data = json.dumps({"current": current, "total": total, "candidate_id": cid, "title": title, "status": status}, ensure_ascii=False)
            yield f"event: progress\ndata: {data}\n\n"

        try:
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                result = smart_merge_merge_candidates(
                    conn,
                    Path(current_app.config["ASSETS_DIR"]),
                    candidate_ids,
                    progress_callback=lambda cur, tot, cid, title, status: progress(cur, tot, cid, title, status),
                )
            yield f"event: complete\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
        except (ValueError, RuntimeError) as exc:
            yield f"event: error\ndata: {json.dumps({'ok': False, 'message': str(exc)[:500]})}\n\n"
        except Exception:
            yield f"event: error\ndata: {json.dumps({'ok': False, 'message': 'Merge failed. Check server log.'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")
```

Wait, there's a problem with the `generate()` function. The `progress` function defined inside `generate()` uses `yield` from within a non-generator. I need to restructure this. The callback should not be a generator itself — instead, the callback should push events to a queue that `generate()` reads from.

Let me use a simpler approach: `progress_callback` collects events into a list, and `generate()` yields them:

```python
def generate():
    events: list[str] = []

    def progress(current, total, cid, title, status):
        data = json.dumps({"current": current, "total": total, "candidate_id": cid, "title": title, "status": status}, ensure_ascii=False)
        events.append(f"event: progress\ndata: {data}\n\n")

    try:
        result = smart_merge_merge_candidates(
            Path(current_app.config["DATABASE_PATH"]), Path(current_app.config["ASSETS_DIR"]), candidate_ids,
            progress_callback=progress,
        )
    except (ValueError, RuntimeError) as exc:
        events.append(f"event: error\ndata: {json.dumps({'ok': False, 'message': str(exc)[:500]})}\n\n")
    except Exception:
        events.append(f"event: error\ndata: {json.dumps({'ok': False, 'message': 'Merge failed. Check server log.'})}\n\n")
    else:
        events.append(f"event: complete\ndata: {json.dumps(result, ensure_ascii=False)}\n\n")

    for event in events:
        yield event
```

- [ ] **Step 4: Add SSE endpoint for analysis**

```python
@bp.get("/smart-merge/analyze-stream")
@login_required
def smart_merge_analyze_stream():
    def generate():
        try:
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                assets_dir = Path(current_app.config["ASSETS_DIR"])
                result = smart_merge_analyze_database(conn, assets_dir)
            yield f"event: complete\ndata: {json.dumps({'ok': True, **result}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'ok': False, 'message': str(exc)[:500]})}\n\n"

    return Response(generate(), mimetype="text/event-stream")
```

- [ ] **Step 5: Add `/smart-merge/merge` POST fallback route**

```python
@bp.post("/smart-merge/merge")
@login_required
def smart_merge_merge():
    payload = request.get_json(silent=True) or request.form
    try:
        raw_ids = payload.get("candidate_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [value for value in raw_ids.split(",") if value.strip()]
        candidate_ids = [int(value) for value in raw_ids]
        if not candidate_ids:
            return jsonify({"ok": False, "message": "No candidates provided"}), 400
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            result = smart_merge_merge_candidates(Path(current_app.config["DATABASE_PATH"]), Path(current_app.config["ASSETS_DIR"]), candidate_ids)
        audit_log("smart_merge.merge", "SmartMerging applied", category="admin", outcome="success",
                   merged=result.get("result", {}).get("operations", 0))
        return jsonify(result)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    except Exception:
        current_app.logger.exception("SmartMerging merge failed")
        return jsonify({"ok": False, "message": "Merge failed and was rolled back."}), 500
```

- [ ] **Step 6: Update the page route to remove bundles context**

In `smart_merge_page()`, remove the `smart_merge_list_bundles()` call:
```python
@bp.get("/smart-merge")
@login_required
def smart_merge_page():
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        run = smart_merge_latest_run(conn)
    return render_template("dashboard/smart_merge.html", latest_run=run)
```

- [ ] **Step 7: Add `Response` import if not present**

Add to the imports at top of `dashboard.py`:
```python
from flask import Response
```

---

### Task 4: Template — rewrite `smart_merge.html`

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/smart_merge.html`
- Delete: `MIFPAPP/CORE/mifp_app/templates/dashboard/_merge_modal.html` (already orphaned)

- [ ] **Step 1: Rewrite the template**

```html
{% extends "dashboard/layout.html" %}
{% from "dashboard/_components.html" import page_header %}
{% block page_title %}SmartMerging{% endblock %}
{% block content %}
{% call page_header('Operations', 'SmartMerging', 'Duplicate detection, review and automated merging with real-time progress.') %}{% endcall %}

<section class="modern-card sm-header" aria-labelledby="smartMergeStatusTitle">
  <div class="sm-header-copy">
    <div class="panel-head">
      <div>
        <h3 id="smartMergeStatusTitle"><i class="bi bi-intersect" aria-hidden="true"></i> Analysis status</h3>
        <p>Read-only analysis. Review candidates below before merging.</p>
      </div>
      <div class="sm-header-actions">
        <button type="button" class="btn btn-primary btn-sm" id="smAnalyze"><i class="bi bi-search"></i> Analyze database</button>
      </div>
    </div>
    <div class="sm-run-meta" id="smRunMeta" aria-live="polite">
      {% if latest_run %}
      <span class="status-badge status-{{ 'success' if latest_run.status == 'completed' else 'warning' }}" id="smRunStatus">{{ latest_run.status|title }}</span>
      <span>Run #<b id="smRunId">{{ latest_run.id }}</b></span>
      <span>Algorithm <b id="smAlgorithm">{{ latest_run.algorithm_version }}</b></span>
      <span>Started <b id="smRunStarted">{{ latest_run.started_at }}</b></span>
      {% else %}
      <span class="status-badge status-muted" id="smRunStatus">Not analyzed</span>
      <span>Run #<b id="smRunId">—</b></span>
      <span>Algorithm <b id="smAlgorithm">—</b></span>
      <span>Started <b id="smRunStarted">—</b></span>
      {% endif %}
    </div>
  </div>
  <div class="sm-kpis" id="smKpis" aria-label="Analysis summary"></div>
</section>

<nav class="sm-tabs" aria-label="SmartMerging sections">
  <a href="#analysis" data-sm-tab="analysis" class="is-active">Analysis</a>
  <a href="#candidates" data-sm-tab="candidates">Merge candidates</a>
</nav>

<section class="sm-section" id="analysis" data-sm-section="analysis">
  <div class="sm-section-head">
    <div><h2>Analysis</h2><p>Read-only database health and duplicate detection results.</p></div>
    <div class="sm-section-actions">
      <span class="sm-read-only"><i class="bi bi-lock"></i> Read-only</span>
      <button type="button" class="btn btn-accent btn-sm" id="smMergeAllSafe" hidden><i class="bi bi-shield-check"></i> Merge all safe (<span id="smSafeCount">0</span>)</button>
    </div>
  </div>
  <div class="sm-analysis-grid">
    <article class="modern-card">
      <div class="panel-head"><div><h3>Entities</h3><p>Blocking efficiency and candidate generation per type.</p></div></div>
      <div class="table-wrap"><table class="data-table sm-table"><thead><tr><th>Type</th><th>Records</th><th>Blocks</th><th>Pairs</th><th>Pruned</th><th>Suggestions</th></tr></thead><tbody id="smEntityMetrics"><tr><td colspan="6" class="empty-cell">Run an analysis to populate metrics.</td></tr></tbody></table></div>
    </article>
    <article class="modern-card">
      <div class="panel-head"><div><h3>Database health</h3><p>Integrity and asset cleanup signals; no repair is applied here.</p></div></div>
      <div class="sm-health-list" id="smHealth"><p class="empty-state">No analysis available.</p></div>
    </article>
  </div>
  <article class="modern-card">
    <div class="panel-head"><div><h3>Analysis instrumentation</h3><p>Rule version, timing and warnings.</p></div></div>
    <div class="sm-metric-strip" id="smInstrumentation"></div>
    <div class="sm-warning-list" id="smWarnings" hidden></div>
  </article>
</section>

<section class="sm-section" id="candidates" data-sm-section="candidates" hidden>
  <div class="sm-section-head">
    <div><h2>Merge candidates</h2><p>Select candidates below and merge them in bulk.</p></div>
    <span id="smCandidateCount">0 candidates</span>
  </div>
  <div class="modern-card sm-filter-card">
    <form id="smFilters" class="sm-filters">
      <label>Entity<select name="entity_type"><option value="">All</option><option value="member">Members</option><option value="event">Events</option><option value="news">News</option><option value="publication">Publications</option><option value="asset">Assets</option></select></label>
      <label>Class<select name="classification"><option value="">All</option><option value="safe">Safe</option><option value="probable">Probable</option><option value="possible">Possible</option><option value="blocked">Blocked</option><option value="not_duplicate">Not duplicate</option></select></label>
      <label>Decision<select name="decision"><option value="">All</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="keep_separate">Keep separate</option><option value="later">Later</option></select></label>
      <label>Conflicts<select name="has_conflicts"><option value="">All</option><option value="1">With conflicts</option><option value="0">Without conflicts</option></select></label>
      <label>Sort<select name="sort"><option value="score">Confidence</option><option value="impact">Impact</option><option value="risk">Risk</option><option value="date">Newest</option></select></label>
      <label class="sm-search-label">Search<input type="search" name="q" maxlength="100" placeholder="Title, reason or record ID"></label>
      <button class="btn btn-outline btn-sm" type="submit"><i class="bi bi-funnel"></i> Apply</button>
    </form>
  </div>
  <div class="sm-selection-toolbar" id="smSelectionToolbar" hidden>
    <button type="button" class="btn btn-outline btn-sm" data-select="all"><i class="bi bi-check-all"></i> Select all</button>
    <button type="button" class="btn btn-outline btn-sm" data-select="safe"><i class="bi bi-shield"></i> Select safe</button>
    <button type="button" class="btn btn-outline btn-sm" data-select="probable"><i class="bi bi-exclamation-triangle"></i> Select probable</button>
    <button type="button" class="btn btn-outline btn-sm" data-select="none"><i class="bi bi-x"></i> Deselect all</button>
    <span class="sm-selection-count"><b id="smSelectedCount">0</b> selected</span>
  </div>
  <div class="sm-candidate-list" id="smCandidateList"><p class="empty-state">No candidates loaded.</p></div>
  <div class="sm-action-bar" id="smActionBar" hidden>
    <button type="button" class="btn btn-primary btn-sm" id="smMergeSelected" disabled><i class="bi bi-merge"></i> Merge selected (<span id="smMergeSelectedCount">0</span>)</button>
    <button type="button" class="btn btn-accent btn-sm" id="smMergeAllSafeBtn" hidden><i class="bi bi-shield-check"></i> Merge all safe (<span id="smMergeAllSafeCount">0</span>)</button>
  </div>
  <div class="sm-pagination" id="smPagination"></div>
</section>

<div class="sm-progress-overlay" id="smProgressOverlay" hidden>
  <div class="sm-progress-card">
    <h4 id="smProgressTitle">Merging candidates...</h4>
    <div class="sm-progress-bar-wrap">
      <div class="progress">
        <div class="progress-bar" id="smProgressBar" role="progressbar" style="width: 0%">0 / 0</div>
      </div>
    </div>
    <div class="sm-progress-log" id="smProgressLog"></div>
    <div class="sm-progress-actions">
      <button type="button" class="btn btn-outline btn-sm" id="smProgressClose" hidden>Close</button>
    </div>
  </div>
</div>
{% endblock %}

{% block extra_js %}
{{ super() }}
<script src="{{ url_for('static', filename='js/dashboard/smart-merge.js') }}" defer></script>
<script type="application/json" id="smartMergeConfig" nonce="{{ csp_nonce }}">{{ {
  'runId': latest_run.id if latest_run else none,
  'analyzeUrl': url_for('dashboard.smart_merge_analyze'),
  'analyzeStreamUrl': url_for('dashboard.smart_merge_analyze_stream'),
  'runUrl': url_for('dashboard.smart_merge_run', run_id=0),
  'candidatesUrl': url_for('dashboard.smart_merge_candidates'),
  'candidateUrl': url_for('dashboard.smart_merge_candidate', candidate_id=0),
  'decisionUrl': url_for('dashboard.smart_merge_candidate_decision', candidate_id=0),
  'mergeStreamUrl': url_for('dashboard.smart_merge_merge_stream'),
  'mergeUrl': url_for('dashboard.smart_merge_merge')
}|tojson }}</script>
{% endblock %}
```

- [ ] **Step 2: Delete orphaned `_merge_modal.html`**

Run: `rm MIFPAPP/CORE/mifp_app/templates/dashboard/_merge_modal.html`
(If it still exists after previous cleanup.)

---

### Task 5: CSS — remove bundle classes, add new classes

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/css/dashboard.css`

- [ ] **Step 1: Remove bundle CSS classes**

Delete lines 5526-5545 (all `.sm-bundle-*`, `.sm-apply-confirm`, `.sm-inline-warning`, `.sm-success-note` classes) from dashboard.css.

Also remove references in the responsive sections (lines 5553, 5560, 5564).

- [ ] **Step 2: Add new CSS classes**

Add before the responsive section (before the `@media` blocks):

```css
.sm-section-actions { display: flex; gap: .5rem; align-items: center; }
.sm-header-actions { display: flex; gap: .5rem; align-items: center; }
.sm-selection-toolbar { display: flex; gap: .35rem; align-items: center; flex-wrap: wrap; padding: .5rem .65rem; margin: .5rem 0; border: 1px solid var(--border-soft); border-radius: var(--radius-sm); background: var(--surface-2); }
.sm-selection-count { margin-left: auto; font-size: .72rem; color: var(--text-2); }
.sm-action-bar { display: flex; gap: .5rem; align-items: center; padding: .5rem 0; }
.sm-progress-overlay { position: fixed; inset: 0; z-index: 9999; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,.45); }
.sm-progress-card { width: min(90vw, 32rem); padding: 1.2rem; background: var(--surface); border-radius: var(--radius); box-shadow: 0 .25rem 1.5rem rgba(0,0,0,.25); }
.sm-progress-card h4 { margin: 0 0 .65rem; font-size: .85rem; color: var(--text-bright); }
.sm-progress-bar-wrap { margin-bottom: .65rem; }
.sm-progress-log { max-height: 14rem; overflow-y: auto; font-size: .7rem; font-family: var(--font-mono, monospace); display: grid; gap: .2rem; }
.sm-progress-line { padding: .15rem .35rem; border-radius: 3px; }
.sm-progress-line.is-merged { color: var(--green); background: var(--green-bg); }
.sm-progress-line.is-merging { color: var(--accent); background: var(--accent-subtle); }
.sm-progress-line.is-skipped { color: var(--text-3); }
.sm-progress-line.is-pending { color: var(--text-3); opacity: .5; }
.sm-progress-line.is-error { color: var(--red); background: var(--red-bg); }
.sm-progress-actions { margin-top: .65rem; display: flex; gap: .35rem; justify-content: flex-end; }
```

---

### Task 6: JS — rewrite `smart-merge.js`

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/smart-merge.js`

- [ ] **Step 1: Rewrite entirely**

Full rewrite (~350 loc):

```javascript
(function () {
  'use strict';

  var configNode = document.getElementById('smartMergeConfig');
  if (!configNode || !window.MIFP) return;
  var config = {};
  try { config = JSON.parse(configNode.textContent || '{}'); } catch (_) { return; }

  var state = { runId: config.runId || null, page: 1, selected: new Set(), activeCandidate: null, totalSafe: 0 };
  var byId = function (id) { return document.getElementById(id); };
  var replaceId = function (template, value) { return template.replace(/\/0(?:\/|$)/, '/' + value + (template.endsWith('/0') ? '' : '/')); };

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }

  function statusClass(value) {
    return 'sm-class sm-class-' + String(value || 'pending').replace(/[^a-z_]/g, '');
  }

  function formatNumber(value) {
    return new Intl.NumberFormat().format(Number(value || 0));
  }

  function formatBytes(value) {
    var bytes = Number(value || 0);
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function setLoading(button, loading, label) {
    if (!button) return;
    if (loading) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      button.textContent = label || 'Working\u2026';
    } else {
      button.disabled = false;
      button.textContent = button.dataset.label || label || button.textContent;
    }
  }

  function showTab(name) {
    document.querySelectorAll('[data-sm-section]').forEach(function (s) { s.hidden = s.dataset.smSection !== name; });
    document.querySelectorAll('[data-sm-tab]').forEach(function (l) { l.classList.toggle('is-active', l.dataset.smTab === name); });
    if (name === 'candidates' && state.runId) loadCandidates();
  }

  function routeFromHash() {
    var name = (location.hash || '#analysis').slice(1);
    if (!['analysis', 'candidates'].includes(name)) name = 'analysis';
    showTab(name);
  }

  document.querySelectorAll('[data-sm-tab]').forEach(function (link) {
    link.addEventListener('click', function () { window.setTimeout(routeFromHash, 0); });
  });
  window.addEventListener('hashchange', routeFromHash);

  function renderRun(run) {
    if (!run) return;
    state.runId = run.id;
    byId('smRunId').textContent = run.id;
    byId('smAlgorithm').textContent = run.algorithm_version || '\u2014';
    byId('smRunStarted').textContent = run.started_at || '\u2014';
    var statusEl = byId('smRunStatus');
    statusEl.textContent = String(run.status || 'unknown').replace('_', ' ');
    statusEl.className = 'status-badge status-' + (run.status === 'completed' ? 'success' : run.status === 'failed' ? 'danger' : 'warning');
    renderSummary(run.summary || {}, run.metrics || {});
  }

  function renderSummary(summary, metrics) {
    var classes = summary.classifications || {};
    var assets = summary.assets || {};
    var health = summary.health || {};
    var safeCount = Number(classes.safe || 0);
    state.totalSafe = safeCount;
    var kpis = [
      ['Records analyzed', summary.records_analyzed],
      ['Safe duplicates', classes.safe],
      ['Needs review', Number(classes.probable || 0) + Number(classes.possible || 0)],
      ['Blocked', classes.blocked],
      ['Asset duplicates', assets.exact_duplicates],
      ['Unused assets', assets.unused],
      ['Missing files', assets.missing_files],
      ['Recoverable', formatBytes(assets.recoverable_bytes)],
    ];
    var grid = byId('smKpis');
    grid.replaceChildren();
    kpis.forEach(function (item) {
      var card = node('a', 'sm-kpi');
      card.href = item[0].includes('Asset') || item[0].includes('Missing') || item[0].includes('Recoverable') ? '#analysis' : '#candidates';
      card.append(node('b', '', item[1] === undefined ? 0 : item[1]), node('span', '', item[0]));
      grid.append(card);
    });

    var entityBody = byId('smEntityMetrics');
    entityBody.replaceChildren();
    var entities = metrics.entities || {};
    Object.keys(entities).forEach(function (key) {
      var item = entities[key];
      var tr = node('tr');
      [key, item.records_read, item.candidate_blocks, item.pairs_evaluated, item.pairs_pruned, item.candidate_groups].forEach(function (v) { tr.append(node('td', '', formatNumber(v))); });
      entityBody.append(tr);
    });
    if (!entityBody.children.length) { var td = node('td', 'empty-cell', 'No entity metrics available.'); td.colSpan = 6; var tr = node('tr'); tr.append(td); entityBody.append(tr); }

    var healthList = byId('smHealth');
    healthList.replaceChildren();
    Object.keys(health).sort().forEach(function (key) {
      var row = node('div', 'sm-health-row');
      row.append(node('span', '', key.replaceAll('_', ' ')), node('b', '', Number.isFinite(Number(health[key])) ? formatNumber(health[key]) : health[key]));
      healthList.append(row);
    });

    var totals = metrics.totals || {};
    var instr = byId('smInstrumentation');
    instr.replaceChildren();
    [['Duration', (Number(totals.duration_ms || 0) / 1000).toFixed(2) + ' s'],
     ['Blocks', totals.candidate_blocks], ['Pairs evaluated', totals.pairs_evaluated],
     ['Pairs avoided', totals.pairs_pruned], ['Fuzzy comparisons', totals.fuzzy_comparisons],
     ['Candidates', totals.candidate_groups]].forEach(function (item) {
      var box = node('div'); box.append(node('b', '', item[1] || 0), node('span', '', item[0])); instr.append(box);
    });
    var warnings = byId('smWarnings');
    warnings.replaceChildren();
    (summary.warnings || []).forEach(function (w) { warnings.append(node('p', '', w)); });
    warnings.hidden = !warnings.children.length;

    // Show/hide merge all safe button
    var mergeBtn = byId('smMergeAllSafe');
    if (safeCount > 0) {
      byId('smSafeCount').textContent = safeCount;
      mergeBtn.hidden = false;
    } else {
      mergeBtn.hidden = true;
    }
    // Also update the action bar button
    var mergeAllBtn = byId('smMergeAllSafeBtn');
    if (safeCount > 0) { byId('smMergeAllSafeCount').textContent = safeCount; mergeAllBtn.hidden = false; } else { mergeAllBtn.hidden = true; }
  }

  async function loadRun() {
    if (!state.runId) return;
    try {
      var result = await window.MIFP.request(replaceId(config.runUrl, state.runId));
      renderRun(result.data.run);
    } catch (_) {}
  }

  // --- Analyze ---
  var analyzeBtn = byId('smAnalyze');
  analyzeBtn.addEventListener('click', async function () {
    setLoading(analyzeBtn, true, 'Analyzing\u2026');
    try {
      var result = await window.MIFP.request(config.analyzeUrl, { method: 'POST', json: {}, timeout: 180000 });
      state.runId = result.data.run_id;
      state.page = 1;
      state.selected.clear();
      await loadRun();
      await loadCandidates();
      location.hash = '#analysis';
    } catch (error) {
      window.MIFPUI?.toast(error.message, 'error');
    } finally {
      setLoading(analyzeBtn, false);
    }
  });

  // --- Filters ---
  function filtersQuery() {
    var params = new URLSearchParams(new FormData(byId('smFilters')));
    params.set('page', state.page);
    params.set('per_page', '25');
    if (state.runId) params.set('run_id', state.runId);
    Array.from(params.entries()).forEach(function (e) { if (!e[1]) params.delete(e[0]); });
    return params.toString();
  }

  async function loadCandidates() {
    if (!state.runId) return;
    var list = byId('smCandidateList');
    list.replaceChildren(node('p', 'empty-state', 'Loading candidates\u2026'));
    try {
      var result = await window.MIFP.request(config.candidatesUrl + '?' + filtersQuery());
      renderCandidates(result.data);
    } catch (error) {
      list.replaceChildren(node('p', 'empty-state is-error', error.message));
    }
  }

  function renderCandidates(payload) {
    var list = byId('smCandidateList');
    list.replaceChildren();
    byId('smCandidateCount').textContent = formatNumber(payload.total) + ' candidates';
    (payload.items || []).forEach(function (item) {
      var article = node('article', 'sm-candidate');
      article.dataset.candidateId = item.id;
      var check = node('input'); check.type = 'checkbox'; check.checked = state.selected.has(item.id); check.setAttribute('aria-label', 'Select candidate ' + item.id);
      check.addEventListener('change', function () {
        if (check.checked) state.selected.add(item.id); else state.selected.delete(item.id);
        updateSelection();
      });
      var main = node('button', 'sm-candidate-main'); main.type = 'button';
      var heading = node('span', 'sm-candidate-heading');
      heading.append(node('em', statusClass(item.classification), item.classification), node('b', '', item.title));
      var reason = node('small', '', item.reason);
      var facts = node('span', 'sm-candidate-facts');
      facts.append(node('span', '', Math.round(Number(item.score || 0) * 100) + '% confidence'));
      facts.append(node('span', '', (item.evidences || []).length + ' evidence'));
      facts.append(node('span', '', (item.conflicts || []).length + ' conflicts'));
      facts.append(node('span', '', item.decision_state || 'pending'));
      main.append(heading, reason, facts);
      main.addEventListener('click', function () { openCandidate(item.id); });
      article.append(check, main);
      list.append(article);
    });
    if (!list.children.length) list.append(node('p', 'empty-state', 'No candidates match these filters.'));
    renderPagination(payload);
    updateSelection();
  }

  function renderPagination(payload) {
    var wrap = byId('smPagination'); wrap.replaceChildren();
    var pages = Math.max(1, Math.ceil(Number(payload.total || 0) / Number(payload.per_page || 25)));
    var prev = node('button', 'btn btn-outline btn-sm', 'Previous'); prev.type = 'button'; prev.disabled = state.page <= 1;
    var next = node('button', 'btn btn-outline btn-sm', 'Next'); next.type = 'button'; next.disabled = state.page >= pages;
    prev.addEventListener('click', function () { state.page -= 1; loadCandidates(); });
    next.addEventListener('click', function () { state.page += 1; loadCandidates(); });
    wrap.append(prev, node('span', '', 'Page ' + state.page + ' of ' + pages), next);
  }

  function updateSelection() {
    var count = state.selected.size;
    byId('smSelectedCount').textContent = count;
    byId('smMergeSelected').disabled = count === 0;
    byId('smMergeSelectedCount').textContent = count;
    var toolbar = byId('smSelectionToolbar');
    var actionBar = byId('smActionBar');
    toolbar.hidden = false;
    actionBar.hidden = false;
  }

  byId('smFilters').addEventListener('submit', function (e) { e.preventDefault(); state.page = 1; loadCandidates(); });

  // --- Selection toolbar ---
  byId('smSelectionToolbar').addEventListener('click', function (e) {
    var btn = e.target.closest('[data-select]');
    if (!btn) return;
    var action = btn.dataset.select;
    var checks = byId('smCandidateList').querySelectorAll('.sm-candidate input[type=checkbox]');
    checks.forEach(function (check) {
      var candidate = check.closest('.sm-candidate');
      if (!candidate) return;
      var id = Number(candidate.dataset.candidateId);
      var classification = candidate.querySelector('.sm-candidate-heading em')?.textContent || '';
      if (action === 'all') { check.checked = true; state.selected.add(id); }
      else if (action === 'none') { check.checked = false; state.selected.delete(id); }
      else if (action === 'safe' && classification === 'safe') { check.checked = true; state.selected.add(id); }
      else if (action === 'probable' && (classification === 'probable' || classification === 'possible')) { check.checked = true; state.selected.add(id); }
      else if (action === 'none') { check.checked = false; state.selected.delete(id); }
    });
    updateSelection();
  });

  // --- Candidate detail (inline expand) ---
  async function openCandidate(id) {
    state.activeCandidate = id;
    var panel = byId('smCandidateDetail') || document.body;
    // Instead of side panel, show inline detail in a modal/overlay
    // For simplicity, use the same detail rendering approach
    try {
      var result = await window.MIFP.request(replaceId(config.candidateUrl, id));
      renderCandidateInline(result.data.candidate);
    } catch (error) {
      window.MIFPUI?.toast(error.message, 'error');
    }
  }

  function renderCandidateInline(item) {
    // Simple inline expand: show a modal-like card below the candidate
    var existing = document.querySelector('.sm-candidate-expand');
    if (existing) existing.remove();
    var card = node('div', 'sm-candidate-expand');
    var head = node('div', 'sm-detail-head');
    head.append(node('em', statusClass(item.classification), item.classification), node('h3', '', item.title), node('p', '', item.reason));
    card.append(head);
    var records = item.records || [];
    var canonicalSelect = null;
    if (records.length) {
      card.append(node('h4', '', 'Canonical record'));
      canonicalSelect = node('select', 'form-select form-select-sm sm-canonical-select');
      records.forEach(function (rec) {
        var opt = node('option', '', '#' + rec.id + ' \u00b7 ' + (rec.display_name || rec.title || rec.name || rec.slug || ('Record #' + rec.id)));
        opt.value = rec.id;
        opt.selected = Number(rec.id) === Number(item.canonical_id);
        canonicalSelect.append(opt);
      });
      card.append(canonicalSelect);
    }
    card.append(node('h4', '', 'Evidence'));
    var evidence = node('div', 'sm-evidence-list');
    (item.evidences || []).forEach(function (e) { evidence.append(node('p', 'is-' + e.strength, e.label + (e.value ? ': ' + e.value : ''))); });
    (item.conflicts || []).forEach(function (e) { evidence.append(node('p', e.hard ? 'is-hard' : 'is-warning', e.label)); });
    if (!evidence.children.length) evidence.append(node('p', '', 'No evidence details.'));
    card.append(evidence);

    if ((item.field_plan || []).length) {
      card.append(node('h4', '', 'Field plan'));
      var wrap = node('div', 'sm-table-scroll');
      var table = node('table', 'data-table sm-field-table');
      var thead = node('thead'); var trh = node('tr'); ['Field', 'Decision', 'Final value', 'Evidence'].forEach(function (l) { trh.append(node('th', '', l)); }); thead.append(trh);
      var tbody = node('tbody');
      var reviewSelects = [];
      item.field_plan.forEach(function (field) {
        var tr = node('tr', field.requires_review ? 'is-review' : '');
        tr.append(node('td', '', field.field));
        var dc = node('td'); var vc = node('td', '', field.final_value === null ? '\u2014' : String(field.final_value).slice(0, 180));
        if (field.requires_review && records.length) {
          var sel = node('select', 'form-select form-select-sm');
          var ph = node('option', '', 'Resolve conflict\u2026'); ph.value = ''; ph.selected = true; sel.append(ph);
          records.forEach(function (rec) {
            var raw = rec[field.field];
            var val = raw === null || raw === undefined || raw === '' ? 'empty' : String(raw).slice(0, 90);
            var opt = node('option', '', '#' + rec.id + ' \u00b7 ' + val); opt.value = rec.id; sel.append(opt);
          });
          sel.dataset.field = field.field;
          reviewSelects.push(sel); dc.append(sel);
        } else { dc.textContent = field.action.replaceAll('_', ' '); }
        tr.append(dc, vc, node('td', '', field.reason));
        tbody.append(tr);
      });
      table.append(thead, tbody); wrap.append(table); card.append(wrap);
    }

    var actions = node('div', 'sm-detail-actions');
    var approve = node('button', 'btn btn-sm btn-primary', 'Approve'); approve.type = 'button';
    approve.addEventListener('click', function () {
      var overrides = (reviewSelects || []).map(function (s) { return { field: s.dataset.field, source_record_id: Number(s.value) }; });
      decide(item.id, 'approved', canonicalSelect ? Number(canonicalSelect.value) : item.canonical_id, overrides);
    });
    actions.append(approve);
    [['keep_separate', 'Keep separate'], ['later', 'Review later']].forEach(function (c) {
      var btn = node('button', 'btn btn-sm btn-outline', c[1]); btn.type = 'button';
      btn.addEventListener('click', function () { decide(item.id, c[0], canonicalSelect ? Number(canonicalSelect.value) : item.canonical_id, []); }); actions.append(btn);
    });
    card.append(actions);
    // Insert after the candidate article
    var candidateEl = document.querySelector('.sm-candidate[data-candidate-id="' + item.id + '"]');
    if (candidateEl) candidateEl.after(card);
  }

  async function decide(id, decision, canonicalId, fieldOverrides) {
    try {
      await window.MIFP.request(replaceId(config.decisionUrl, id), { method: 'POST', json: { decision: decision, canonical_id: canonicalId, field_overrides: fieldOverrides || [] } });
      var expand = document.querySelector('.sm-candidate-expand');
      if (expand) expand.remove();
      await loadCandidates();
      window.MIFPUI?.toast('Decision saved.', 'success');
    } catch (error) { window.MIFPUI?.toast(error.message, 'error'); }
  }

  // --- Merge functions ---
  function startMergeStream(url) {
    var overlay = byId('smProgressOverlay');
    var bar = byId('smProgressBar');
    var log = byId('smProgressLog');
    var title = byId('smProgressTitle');
    var close = byId('smProgressClose');
    overlay.hidden = false;
    log.replaceChildren();
    close.hidden = true;
    title.textContent = 'Merging candidates...';
    bar.style.width = '0%';
    bar.textContent = '0 / 0';

    var eventSource = new EventSource(url);
    eventSource.addEventListener('progress', function (e) {
      var data = JSON.parse(e.data || '{}');
      var pct = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
      bar.style.width = Math.min(pct, 100) + '%';
      bar.textContent = data.current + ' / ' + data.total;
      var line = node('div', 'sm-progress-line');
      if (data.status === 'merged') { line.className = 'sm-progress-line is-merged'; line.textContent = '\u2713 ' + data.title; }
      else if (data.status === 'merging') { line.className = 'sm-progress-line is-merging'; line.textContent = '\u27f3 ' + data.title; }
      else if (data.status === 'skipped') { line.className = 'sm-progress-line is-skipped'; line.textContent = '\u2717 ' + data.title + ' (skipped)'; }
      else { line.textContent = data.title; }
      log.append(line);
      log.scrollTop = log.scrollHeight;
    });

    eventSource.addEventListener('complete', function (e) {
      eventSource.close();
      var data = JSON.parse(e.data || '{}');
      title.textContent = 'Merge complete';
      var line = node('div', 'sm-progress-line is-merged');
      line.textContent = '\u2713 ' + (data.result?.operations || 0) + ' operations completed. ' + (data.result?.records_removed || 0) + ' records removed.';
      log.append(line);
      close.hidden = false;
      loadRun();
      loadCandidates();
    });

    eventSource.addEventListener('error', function (e) {
      eventSource.close();
      var data = {};
      try { data = JSON.parse((e.data || '{}')); } catch (_) {}
      title.textContent = 'Merge failed';
      var line = node('div', 'sm-progress-line is-error');
      line.textContent = '\u2717 ' + (data.message || 'Connection lost. Check server log.');
      log.append(line);
      close.hidden = false;
    });

    close.addEventListener('click', function () { overlay.hidden = true; eventSource.close(); }, { once: true });
  }

  // Merge all safe
  byId('smMergeAllSafe').addEventListener('click', function () {
    if (!state.runId) return;
    var url = config.mergeStreamUrl + '?mode=safe&run_id=' + state.runId;
    startMergeStream(url);
  });
  byId('smMergeAllSafeBtn').addEventListener('click', function () {
    if (!state.runId) return;
    var url = config.mergeStreamUrl + '?mode=safe&run_id=' + state.runId;
    startMergeStream(url);
  });

  // Merge selected
  byId('smMergeSelected').addEventListener('click', function () {
    if (state.selected.size === 0) return;
    var ids = Array.from(state.selected).join(',');
    var url = config.mergeStreamUrl + '?candidate_ids=' + ids;
    startMergeStream(url);
  });

  routeFromHash();
  if (state.runId) loadRun().then(loadCandidates).catch(function () {});
})();
```

---

### Task 7: Tests — update `test_smart_merge.py`

**Files:**
- Modify: `TESTS/webapp/test_smart_merge.py`

- [ ] **Step 1: Update imports**

Replace:
```python
from mifp_app.services.smart_merge import (
    analyze_database,
    apply_bundle,
    create_bundle,
    decide_candidate,
    dry_run_bundle,
    get_candidate,
    list_candidates,
)
```
with:
```python
from mifp_app.services.smart_merge import (
    analyze_database,
    decide_candidate,
    get_candidate,
    list_candidates,
    merge_candidates,
)
from mifp_app.services.smart_merge.executor import merge_candidates as executor_merge
```

Wait, `merge_candidates` should be exported from `__init__.py` directly. Let me fix the import.

Actually, `decide_candidate` — where is it? It's in `planner.py` which will be removed. It was just a wrapper for `repository.save_decision()`. I need to import `save_decision` as `decide_candidate` from `__init__.py`.

In `__init__.py`:
```python
from .repository import save_decision as decide_candidate
```

So the test import just needs:
```python
from mifp_app.services.smart_merge import (
    analyze_database,
    decide_candidate,
    get_candidate,
    list_candidates,
    merge_candidates,
)
```

- [ ] **Step 2: Remove bundle tests**

Remove `test_bundle_is_atomic_backed_up_and_idempotent` and `test_stale_bundle_is_blocked_without_partial_write`.

- [ ] **Step 3: Add new merge tests**

Add after `test_keep_separate_is_persisted_until_fingerprint_changes`:

```python
def test_merge_safe_candidates_works(tmp_path: Path) -> None:
    db_path, assets_dir = _database(tmp_path)
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO members(slug,display_name,email,affiliation) VALUES(?,?,?,?)",
            [
                ("alex-a", "Prof. Alex Smith", "alex@example.org", "Old University"),
                ("alex-b", "Alex Smith", "alex@example.org", "New University"),
            ],
        )
        conn.commit()
        run = analyze_database(conn, assets_dir)
        safe = [item for item in list_candidates(conn, run_id=run["run_id"], classification="safe", per_page=100)["items"]]
        assert len(safe) > 0
        safe_ids = [item["id"] for item in safe]
        result = merge_candidates(db_path, assets_dir, safe_ids)
    assert result["ok"] is True
    assert result["result"]["records_removed"] > 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1


def test_merge_selected_candidates_works(tmp_path: Path) -> None:
    db_path, assets_dir = _database(tmp_path)
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO members(slug,display_name,email,affiliation) VALUES(?,?,?,?)",
            [
                ("member-a", "Signor Mario Rossi", "mario@example.org", "Uni"),
                ("member-b", "Mario Rossi", "mario@example.org", "Uni"),
            ],
        )
        conn.commit()
        run = analyze_database(conn, assets_dir)
        candidate = get_candidate(conn, list_candidates(conn, run_id=run["run_id"], entity_type="member", per_page=100)["items"][0]["id"])
        overrides = [
            {"field": f["field"], "source_record_id": candidate["canonical_id"]}
            for f in candidate["field_plan"] if f.get("requires_review")
        ]
        decide_candidate(conn, candidate["id"], "approved", field_overrides=overrides)
        result = merge_candidates(conn, assets_dir, [candidate["id"]])
    assert result["ok"] is True
    assert result["result"]["records_removed"] == 1


def test_merge_skips_stale_candidates(tmp_path: Path) -> None:
    db_path, assets_dir = _database(tmp_path)
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO events(slug,title,start_date,date_precision,series_key,location) VALUES(?,?,?,?,?,?)",
            [
                ("conf-a", "Physics Conf", "2024-05-10", "day", "physics", "Rome"),
                ("conf-b", "Physics Conference", "2024-05-10", "day", "physics", "Rome"),
            ],
        )
        conn.commit()
        run = analyze_database(conn, assets_dir)
        candidate = list_candidates(conn, run_id=run["run_id"], entity_type="event", per_page=100)["items"][0]
        conn.execute("UPDATE events SET title='Changed after analysis', updated_at=CURRENT_TIMESTAMP WHERE id=2")
        conn.commit()
        result = merge_candidates(conn, assets_dir, [candidate["id"]])
    assert result["ok"] is True
    assert result["result"]["operations"] == 0  # stale candidate skipped


def test_merge_rollback_on_error(tmp_path: Path) -> None:
    db_path, assets_dir = _database(tmp_path)
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO members(slug,display_name,email) VALUES(?,?,?)",
            [
                ("safe-a", "Safe Person", "safe@example.org"),
                ("safe-b", "Safe Person", "safe@example.org"),
            ],
        )
        conn.commit()
        run = analyze_database(conn, assets_dir)
        safe = [item for item in list_candidates(conn, run_id=run["run_id"], classification="safe", per_page=100)["items"]]
        safe_ids = [item["id"] for item in safe]
    with connect(db_path) as conn:
        # Force an error by passing a candidate from a different DB
        with pytest.raises((ValueError, Exception)):
            merge_candidates(conn, assets_dir, [99999])
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 2
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest TESTS/webapp/test_smart_merge.py -x -v`
Expected: 6 passed (normalization, analysis, keep_separate, merge_safe, merge_selected, merge_skips_stale, merge_rollback_on_error, scale_guard)

---

### Task 8: Full test suite

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest TESTS/webapp/ -x -q`
Expected: All tests pass (no regressions)

- [ ] **Step 2: Check for any remaining bundle references**

Run: `rg -i "bundle" MIFPAPP/CORE/mifp_app/services/smart_merge/ MIFPAPP/CORE/mifp_app/routes/dashboard.py MIFPAPP/CORE/mifp_app/static/js/dashboard/smart-merge.js`
Expected: No significant code references (only DB table historical data mentions).

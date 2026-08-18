# Data Quality Page Rewrite — Design Spec

**Date:** 2026-07-27
**Status:** Approved (verbal review with user)
**Supersedes:** None (replaces the current 3-phase implementation in `data_quality.html` + `data-quality.js` + `dashboard_data_quality.py`)

## 1. Problem Statement

The current Data Quality page has two systemic problems reported by the user:

1. **Runtime errors.** The frontend silently creates a bundle and batch-accepts every finding immediately after analysis completes (`data-quality.js:111-118`). When ambiguous or overlapping findings end up in the same draft, `validate_bundle` returns `valid: false`, and the user sees an opaque "Validation failed" toast at apply time — with no way to identify or fix the offending items.
2. **Usability confusion.** The 2-phase layout conflates "review the findings" and "apply the bundle" into a single phase. Per-finding actions are limited to "Ignore forever" — there is no per-finding Accept. The review queue (the bundle contents) is hidden behind a single number; the user never sees what they are about to apply before clicking "Apply all".

## 2. Goal

Redo both the logic (route handlers +Accessory state) and the UI of the Data Quality page so that:

- The flow is **explicit** at every step: analyze → review findings one-by-one (or in bulk) → dry-run the bundle → apply.
- No silent batch-accept. Findings enter the bundle only when the user accepts them.
- The bundle contents are always visible and editable before apply.
- The hardened services (`analyzer.py`, `planner.py`, `executor.py`, `cluster.py`, `normalizers.py`, `policies.py`, `models.py`) are **kept as-is** — they passed 58 tests in the previous enhancement cycle and are not the source of the runtime errors.

## 3. Non-Goals (YAGNI)

- WebSocket push for analysis progress (1.5s polling is fine).
- Undo/redo of applied bundles (the on-disk SQLite backup already covers this).
- CSV/PDF export of findings (the `/dump` route already covers exports).
- Side-by-side visual diff of two records (the text plan is enough).
- "Reopen" of a rejected finding (low value).
- Rewrite of `services/data_quality/` modules.

## 4. Architecture

### 4.1 Flow

```
Analyze (background thread, unchanged)
   ↓
Findings list (read-only output of the analysis)
   ↓ user reviews per-finding (Accept → bundle, Ignore → pending, Reject → resolved)
Review queue (accumulates accepted findings as bundle items)
   ↓ explicit dry-run, readable report
Apply bundle (backup → apply → verify_invariants)
   ↓
History of past applications (audit log)
```

### 4.2 What Stays Identical

`services/data_quality/` modules (just hardened):
- `analyzer.py` → `analyze(conn, run_id=...)`, `database_fingerprint`, `latest_run`, `list_findings`, `count_findings`, `get_finding`.
- `planner.py` → `build_merge_plan`, `apply_best_quality`, resolver functions, `build_clean_plan`, `build_split_plan`.
- `executor.py` → `validate_bundle`, `apply_bundle`, `verify_invariants`, `create_bundle`, `add_to_bundle`, `remove_from_bundle`, `delete_draft`, `bundle_detail`.
- `models.py`, `cluster.py`, `normalizers.py`, `policies.py`.

### 4.3 What Changes

#### Routes (`routes/dashboard_data_quality.py`)

**Removed:**
- `POST /data-quality/batch-accept` — the silent queue-all behavior. Source of the bug.
- `POST /data-quality/batch-reject` — never called by the current frontend; replaced by the new `bulk-decision` endpoint.
- `POST /data-quality/bundles` (manual create) — bundle is auto-created on first `accept`.
- `POST /data-quality/bundles/<id>/items` (manual add) — accept now adds directly.

**Added / Changed:**
- `POST /data-quality/findings/<id>/decision` — extended. The `decision` value set becomes:
  - `accept` (NEW): changes finding status to `accepted`, auto-creates a draft bundle if none exists, calls `add_to_bundle`. Returns `{ok, finding, bundle_id, item_id}`.
  - `ignore` (NEW alias of `defer`): status → `deferred`.
  - `reject` (unchanged): status → `rejected`.
  - `keep_separate | same_series | false_positive | ignored_test_data` (unchanged): writes `merge_exclusions`.
  - `defer` (kept for back-compat): same as `ignore`.
- `POST /data-quality/bulk-decision` (NEW):
  ```http
  Request:
    {
      "decision": "accept" | "reject" | "ignore" | "keep_separate" | "same_series" | "false_positive",
      "finding_ids": [12, 34, 56],       # explicit IDs, OR
      "filters": {                        # alternatively, operate on visible
        "action_type": "...", "entity_type": "...", "classification": "...",
        "run_id": 45
      }
    }
  Response:
    { "ok": true, "result": {
        "applied": 23, "failed": 1,
        "failures": [{"finding_id": 56, "message": "ambiguous identity still requires review"}]
    }}
  ```
  - If `finding_ids` non-empty → applies to those. Otherwise uses `filters` against the run's findings, capped at 1000 to avoid timeouts.
  - For `accept`: opens a single DB connection, creates/looks-up a draft bundle, calls `add_to_bundle` for each. Individual failures do not abort the loop.
- `GET /data-quality/bundles/<id>/dry-run` (NEW, replaces the POST variant — read-only result of `validate_bundle`):
  - Calls `validate_bundle(conn, bundle_id, persist=True)` and returns the resulting report. Idempotent: each call re-runs validation and overwrites the persisted `bundle_report` row. Callers use it both to refresh the report after item removal and to gate `Apply` (the UI button is disabled until the most recent call returns `valid: true`).
- `POST /data-quality/bundles/<id>/apply` (CHANGED): now **requires** a fresh `validate_bundle` call inside the same handler. If invalid → returns 409 with the full `report` (so the UI can highlight the offending items).
- `DELETE /data-quality/bundles/<id>/items/<item_id>` (unchanged, used by the new "remove from queue" button on each item in the bundle modal).

#### Database

No schema changes needed. The existing `quality_findings.status` CHECK already allows `bundled` (literal meaning: "this finding has been added to a bundle"), which is exactly the user-accepted state. The `add_to_bundle` executor function (executor.py:100) already sets `status='bundled'` when adding to a bundle. The `accept` decision in the route simply calls `add_to_bundle`; no new status value, no migration.

The previously proposed `accepted` status was redundant with `bundled` — dropped after reviewing executor.py.

#### Template (`templates/dashboard/data_quality.html`) — full rewrite

```
┌─────────────────────────────────────────────────────────────────────┐
│ [Analyze database]  run #N completed 4s · 47 findings               │  header + run summary
├──────────────────────────────┬──────────────────────────────────────┤
│ FINDING LIST                 │ DETAIL PANEL                         │
│ (sidebar 40%, sticky)        │ (60%)                                │
│                              │                                       │
│ Filters: [action▾][entity▾]  │ EVIDENCE                             │
│   [class▾]  [Apply]          │   Same email · same DOI · ...        │
│ ☐Select all visible (N)      │                                       │
│   [Bulk accept][Bulk reject] │ PLAN (auto-generated)                │
│                              │   Action: merge_records              │
│ ┌──────────────────────────┐ │   Canonical: #1 "Alexey Kavokin"    │
│ │ ● exact dup      clean #123│ │   Records to merge:                │
│ │ Alexey Kavokin · member   │ │     #1 … ★ canonical               │
│ │ 2 records to consolidate │ │     #2 …                           │
│ └──────────────────────────┘ │   Preserved: 1 assets · 4 links    │
│ ┌──────────────────────────┐ │                                       │
│ │ ● reviewable    split #124│ │ HISTORY                            │
│ │ Conference Topics · event│ │   2026-07-27 14:32 opened          │
│ │ …                         │ │                                     │
│ └──────────────────────────┘ │ [Accept to bundle] [Ignore] [Reject]│
│                              │   [Keep separate ▾] (advanced)       │
│ [Load 30 more]               │                                       │
└──────────────────────────────┴──────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ Review queue: 3 accepted · 0 pending   [Open bundle →]              │  sticky bottom bar
└─────────────────────────────────────────────────────────────────────┘
```

Card states (visual cues):
- `open` — gray status dot.
- `bundled` (accepted) — green left border + "queued" badge.
- `rejected` — strikethrough title + 40% opacity.
- `deferred` (ignored) — gray strikethrough.

Responsive (`@media (max-width: 991.98px)`): the two-column grid collapses to a single column. Tapping a card slides the detail panel in from the right as a full-height drawer (`position: fixed; right: 0; width: 100%; z-index: 1080; box-shadow: var(--shadow-md)`); a back button (`[← Back to list]`) at the top of the detail panel returns to the list. The bundle modal stays full-screen. No horizontal scroll at any breakpoint.

#### Bundle modal (full-screen overlay)

Triggered by `[Open bundle →]` in the sticky bar. Contents:
- Title: `Bundle #12 — draft` and total counts (e.g. "47 ops · 12 aliases estimated").
- Item list grouped by `entity_type`, each row:
  ```
  [member] merge Kavokin Alexey / Alexey Kavokin     [Remove]
  [event]  split Conference Topics                  [Remove]
  ```
  Remove calls `DELETE /bundles/<id>/items/<item_id>` and updates counts.
- `[Run dry-run]` button → calls `GET /bundles/<id>/dry-run` and displays the report inline:
  - On success: green check + `valid: true`, "47 operations, 12 aliases".
  - On invalid: red panel listing each error from `report.errors` with the offending item highlighted (row in the list above turns red).
- `[Apply bundle]` button — **disabled** unless the latest dry-run returned `valid: true`. Clicking opens a confirmation dialog, then `POST /bundles/<id>/apply`.
- On success: toast + close modal + reload findings (the applied findings become `status='applied'` server-side via executor and disappear from review queue).

#### JS (`static/js/dashboard/data-quality.js`) — full rewrite

State machine with these top-level modes:
1. `idle` — no run yet.
2. `analyzing` — progress bar visible, polling `progressUrl` every 1.5s.
3. `reviewing` — findings list loaded. Detail panel shows the selected finding.
4. `bundle_open` — bundle modal open on top of `reviewing`.

Key behaviors:
- Selectors (`id('dqFindingsList')`, `id('dqDetailPanel')`, `id('dqBundleModal')`) replace the phase containers.
- `acceptFinding(id)` → `POST /findings/<id>/decision {decision: 'accept'}`. On success, mark the card as `bundled` (green border + "queued" badge), increment the queue count, optionally pre-fetch the bundle to refresh counts.
- `ignoreFinding`, `rejectFinding`, `keepSeparate` → corresponding `decision` values.
- `bulkDecision(decision, ids|filters)` → `POST /bulk-decision`. Uses `Promise.allSettled` pattern is NOT needed (single call); results update cards and queue count.
- `openBundle()` → fetches `GET /bundles/<id>`, renders rows, then `GET /bundles/<id>/dry-run` to immediately populate the report.
- `applyBundle()` → confirmation dialog showing operation count and backup path → `POST /bundles/<id>/apply` → close modal + reload findings.

The auto-create-bundle-and-batch-accept code path (lines 111-118 of the current file) is removed entirely.

#### CSS (`static/css/dashboard.css`) — `.dq-` section rewrite

Remove ~91 existing `.dq-` rules; add rules for:
- `.dq-app` (flex layout, two columns)
- `.dq-list` (sidebar), `.dq-card` (with status modifier classes)
- `.dq-detail` (right panel)
- `.dq-bundle-modal` (full-screen overlay)
- `.dq-queue-bar` (sticky bottom)
- `.dq-bulk-toolbar` (above the list)
- `.dq-status-dot` (color variants)
- `.dq-report` (success/error states of the dry-run report)

Follows the existing CSS variable palette (`--surface`, `--surface-2`, `--accent`, `--accent-subtle`, `--border-soft`, `--text-bright`, `--text-2`, `--text-3`, `--green`, `--green-bg`, `--radius`, `--radius-sm`). No Tailwind required (per AGENTS.md).

#### Conventions check
- Routes under `bp` (registered via `dashboard.py`).
- `@login_required` on every handler (inherited already).
- CSRF: every POST/DELETE accepts the standard `_csrf_token` form field OR `X-CSRF-Token` header — this is already enforced by the app's `before_request` hook in `mifp_app/__init__.py`, no changes needed.
- Bootstrap 5 modal class (already imported), self-hosted in `static/vendor/`.

## 5. Testing

### 5.1 Unit tests — extend `TESTS/webapp/test_data_quality_unit.py`

- `test_decision_accept_creates_bundle_and_item` — accept on a finding in an in-memory DB creates a draft bundle, sets finding status `bundled` (via add_to_bundle), adds an item. Returns `bundle_id` and `item_id`.
- `test_decision_accept_reuses_existing_draft` — second accept on a different finding adds to the same draft, not a new one.
- `test_decision_ignore_defers` — `ignore` sets status `deferred` (alias of `defer`).
- `test_bulk_decision_accept_with_ids` — bulk accept 3 finding_ids → 3 applied, 0 failed.
- `test_bulk_decision_accept_partial_failure` — mix of `exact_duplicate` (auto-addable) and `ambiguous` (raises in `add_to_bundle`) → reports both `applied` and `failed` with messages.
- `test_bulk_decision_with_filters` — bulk accept with `filters={action_type: 'clean_record', run_id: 1}` → applies to all matching.
- `test_bulk_decision_with_filters_respects_limit` — pass a synthetic dataset > 1000 matches → applies to first 1000 only, returns `applied=1000`.
- `test_apply_requires_fresh_valid_dry_run` — invalid bundle → 409 with `report` containing `errors`.

### 5.2 Route tests — new `TESTS/webapp/test_routes_data_quality.py`

- `GET /data-quality` → 200 with the rewritten template (assert presence of `dq-app`, `dq-list`, `dq-detail` markers).
- `GET /data-quality/findings?run_id=N&action_type=clean_record` → correct filtered payload.
- `POST /data-quality/findings/<id>/decision {decision: 'accept'}` → 200, finding is `bundled`, item appears in `bundle_detail`.
- `POST /data-quality/bulk-decision {decision: '.accept', finding_ids: [...]}` → 200 with `result.applied` count.
- `GET /data-quality/bundles/<id>/dry-run` → 200 with `report`.
- `POST /data-quality/bundles/<id>/apply` with invalid bundle → 409 with `report.errors`.
- `DELETE /data-quality/bundles/<id>/items/<item_id>` → 200, item removed.

### 5.3 Browser smoke test — extend if it covers `/data-quality`

The existing `tools/browser_smoke_test.py` runs `GET` content validation on key pages — add the assertion that the new markers (`dq-app`, `dq-list`, `dq-detail`, `dq-queue-bar`) are present.

### 5.4 Regression commands

```bash
python -m pytest TESTS/webapp -q                  # all webapp tests (should still be 283+ passing)
python -m pytest TESTS/webapp/test_data_quality_unit.py -v
python tools/security_check.py                   # pre-push audit
```

## 6. Implementation Plan (7 tasks)

Ordered to enable testing at every step. Each task ends with a green test suite + a git commit.

**Task 1 — Route refactor: extend `decision` with `accept`, remove silent batch endpoints**
- In `routes/dashboard_data_quality.py`:
  - Add `accept` to the `decision` validator set.
  - On `accept`: open a single connection, get-or-create a draft bundle (query `quality_bundles WHERE status IN ('draft','validated') ORDER BY id DESC LIMIT 1`; if none, call `create_bundle`), call `add_to_bundle(conn, bundle_id, finding_id)` (uses `best_quality` automatically via the un-reviewed path, since no submitted plan is passed), then return `{ok, finding_id, bundle_id, item_id}`.
  - Add `ignore` as alias of `defer`.
  - Remove `POST /data-quality/batch-accept` and `POST /data-quality/batch-reject` routes.
  - Remove `POST /data-quality/bundles` (manual creation) and `POST /data-quality/bundles/<id>/items` (manual add); accept does this implicitly.
- Update existing call sites in templates and JS references (only `data-quality.js` and `data-quality.html` refer to these; both are rewritten in later tasks, so the routes dead-code check is sufficient here).
- Add unit tests for `accept` and `ignore` decisions in `TESTS/webapp/test_data_quality_unit.py`.

**Task 2 — `bulk-decision` endpoint**
- New `POST /data-quality/bulk-decision` handler in `dashboard_data_quality.py`.
- Acceptor loop with per-item try/except, single DB connection, bundle reuse (same get-or-create pattern as Task 1).
- For `accept`: per-finding call `add_to_bundle`, capture exceptions per item (e.g., "ambiguous identity still requires review") and report via `failed[]`.
- For `reject`/`ignore`/`keep_separate`/etc.: loop over findings and apply the same logic as `decision` for each.
- Cap filters-mode at 1000 items; raise 400 if exceeded without explicit acknowledgement.
- Unit tests: bulk with explicit ids, bulk with filters, bulk with mixed pass/fail, filters cap.

**Task 3 — `dry-run` GET + hardened `apply`**
- Add new `GET /data-quality/bundles/<id>/dry-run` route (read-only). Returns `{ok, report}`.
- Keep the POST variant as a thin wrapper or remove it entirely (just delete it — the JS rewrite in Task 7 uses the GET).
- Modify `POST /data-quality/bundles/<id>/apply`: call `validate_bundle(conn, bundle_id, persist=True)` first; on `valid=false` return 409 with full `report` (so the UI can highlight offending items).
- Unit tests for both endpoints.

**Task 4 — CSS rewrite**
- Replace the ~91 existing `.dq-` rules in `static/css/dashboard.css` with new rules per §4.3 (CSS section).
- No markup yet (Task 5 adds it). Verify visually by previewing the file changes — Tailwind/Bootstrap utility classes can be sanity-checked without a live page after Task 6 + Task 7 land.

**Task 5 — Template rewrite**
- Full rewrite of `templates/dashboard/data_quality.html` per §4.3 (Template section): master/detail layout, sticky bottom bar, bundle modal scaffolding, and a minimal empty-state.
- The template renders correctly with `run=None, bundle=None` (initial page state). JS not wired yet (Task 7); all interactive elements carry IDs and disabled states.

**Task 6 — JS rewrite**
- Full rewrite of `static/js/dashboard/data-quality.js` per §4.3 (JS section): state machine, accept/ignore/reject, bulk, bundle modal, dry-run, apply, polling.
- Remove the auto-batch-accept block (current lines 111-118).
- Test end-to-end manually + ensure browser smoke test still passes.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Migration breaks existing DBs | Test on a backup before deploying; migration is idempotent (re-runnable, rows already in `'accepted'` are kept). |
| Users with existing draft bundles find them in inconsistent state | The migration does not touch bundles; on first visit to `/data-quality` the new UI will simply show the existing draft (if any). Old silently-accepted items remain in the bundle — the user can remove them from the new bundle modal. |
| Frontend rewrite introduces new bugs | Tight unit + route tests + browser smoke test + manual walkthrough before merging. |
| Removing `POST /data-quality/batch-accept` could break other callers | Verified: nothing in the codebase calls it outside `data-quality.js`; the JS rewrite removes the only call site. |
| `bulk-decision` with filters could be slow on large DBs | Cap at 1000 items; the response includes `applied` vs `failed` so partial progress is visible. For >1000, the user filters more narrowly. |

## 8. Success Criteria

- All 283+ existing webapp tests still pass.
- New unit + route tests pass (estimated +20 tests → ~303 total).
- Manual walkthrough: analyze → review 5 findings → accept 3, ignore 1, reject 1 → open bundle → dry-run valid → apply → success toast + backup path shown → findings list refreshed.
- The OOM-path "silent bundle full of overlapping items" no longer exists.
- No regression in `/data-quality` page response time (still < 200ms for a typical run).

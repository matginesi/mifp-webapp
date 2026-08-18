# Data Quality — 3-Phase Redesign

## Objective
Replace the current single-page data quality UI with a clean 3-phase workflow: **Analysis → Review → Action**.

## Background
The existing data quality system works but the UI is confusing: findings, merge groups, bundle controls, and multiple "add" strategies are mixed together. The user wants a linear 3-phase flow where each phase has one clear purpose.

## New Flow

### Phase 1 — Analysis
**Purpose:** Discover all data quality issues. No mutation.

- Single "Analyze database" button triggers the existing `analyze()` pipeline.
- Progress bar shows current entity type being scanned.
- On completion, summary cards are shown:
  - One card per `action_type`: `clean_record`, `split_aggregated_record`, `enrich_record`, `repair_relations_or_assets`, `merge_records`.
  - Each card shows count and a short description (e.g., "52 records to clean").
  - Cards double as quick-filter links to Phase 2.
- If no analysis has been run, a prompt message is shown instead.

### Phase 2 — Review
**Purpose:** Review each finding with its automatic "best data" solution. Accept or reject.

- Findings are displayed in a scrollable list, filtered by action type (default: all executable).
- Each finding is a compact card showing:
  - **Problem description** — the evidence text (e.g., "Start date (2023-01-01) is after end date (2022-05-27)").
  - **Proposed solution** — the `best_quality` auto-solution fields shown inline (e.g., "End date → set to None", "First name → Andrea, Last name → D'Andrea").
  - Accept / Reject buttons.
- Accepted findings are added to the Action queue (backed by the existing `quality_bundles` + `quality_bundle_items` tables).
- Rejected findings are dismissed (status → `rejected`).
- Toolbar with:
  - Filter dropdown by action type.
  - "Accept all visible" button.
  - "Clear all accepted" button.
- Pagination (30 per page, "Load more").
- Auto-loads latest analysis results on page load.

### Phase 3 — Action
**Purpose:** Review the queue of accepted findings and execute them.

- Simple list of accepted findings (same card style as Phase 2, read-only).
- Single "Apply all" button.
  - On click: server validates bundle (dry-run), creates backup, applies all actions.
  - Progress spinner during application.
  - On success: shows summary (backup path, operations applied, records removed/cleaned).
  - On error: shows error message; bundle is rolled back.
- Queue is cleared after successful application.
- History of previous applications shown below (last 5 bundles with status and timestamp).

## Implementation Constraints

### No New Backend API
- Reuse existing endpoints: `analyze`, `findings`, `finding`, `decision`, `bundles`, `bundle`, `bundle/add`, `bundle/remove`, `bundle/dry-run`, `bundle/apply`.
- The `decision` endpoint already handles reject (`reject`, `keep_separate`, `defer`).
- The `bundle` endpoints already handle create, add item, dry-run, apply, delete draft.

### New Behavior (Minor Changes)
- The `add_to_bundle` function in `executor.py` already applies `apply_best_quality()` when strategy is `"best_quality"`. This is the default strategy in Phase 2.
- Finding status transitions:
  - `open` → `bundled` (when accepted / added to queue).
  - `open` → `rejected` (when rejected in Phase 2).
  - `bundled` → `resolved` (after successful apply).
- The Phase 3 "Apply all" does dry-run + apply sequentially as two API calls from JS.

### Frontend-Only Rewrite
- `data_quality.html` — full template rewrite with 3-phase layout.
- `data-quality.js` — full rewrite with simplified state management (no `smState`, no bundle state machine).
- `dashboard.css` — add `.dq-phase-*` classes, remove unused `.quality-*` and `.sm-*` classes.

## Data Flow

```
[Analyze] → POST /data-quality/analyze → returns run_id, summary
           ↓
[Load findings] → GET /data-quality/findings?run_id=X → returns paginated findings
           ↓
[Accept finding] → POST /data-quality/bundles → returns bundle_id (auto-create)
                 → POST /data-quality/bundles/:id/items {finding_id, strategy: "best_quality"}
           ↓
[Reject finding] → POST /data-quality/findings/:id/decision {decision: "reject"}
           ↓
[Apply all]   → POST /data-quality/bundles/:id/dry-run → validates
              → POST /data-quality/bundles/:id/apply → executes
```

## Files Changed

| File | Change |
|------|--------|
| `mifp_app/templates/dashboard/data_quality.html` | Rewrite template body to 3-phase layout |
| `mifp_app/static/js/dashboard/data-quality.js` | Rewrite JS (remove sm functions, simplify state) |
| `mifp_app/static/css/dashboard.css` | Add `.dq-*` phase classes, prune unused |
| `mifp_app/services/data_quality/analyzer.py` | Already updated (unchanged by this design) |
| `mifp_app/services/data_quality/executor.py` | Possibly: ensure `add_to_bundle` handles best-quality rejection cleanly |

## Testing

- Existing pytest suite (210 tests) must continue to pass.
- New behavior implicitly tested via existing bundle + decision endpoints.
- Manual verification: run analysis, accept/reject findings, apply bundle.

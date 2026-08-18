# Dashboard Assets page redesign

Date: 2026-08-17

## Objective

Declutter the dashboard assets page, align it with the rest of the dashboard's
visual and interaction patterns, and improve its composition — without removing
any functionality. User selected approach A (single-panel page) after choosing:
"ripulire e semplificare" + "uniformare lo stile" + "migliorare UX e composizione"
together, and "tutti e tre" for the reduction scope (unify status filters, group
hero actions, rethink bottom cards), plus "allinea al pattern standard" for the table.

## Current problems

- Three ways to filter by status: clickable status strip, five triage cards, and
  the toolbar `<select>`.
- Hero has five buttons (Add asset, Data portability, Recover missing, Reconcile
  storage status, Analyze quality).
- Two bottom cards ("Unused assets", "Assets by kind") duplicate information
  already present in the status strip (unused count, missing count, orphan count,
  external refs, kind count).
- The asset table uses a non-standard "expandable row" layout (single `<td
  colspan=8>` grid per row) instead of the shared `data-table` + `record-row` +
  `inline-editor-row` pattern used by events/content.
- The status strip uses clickable `<a>` cells whereas the shared `status-strip`
  component (server.html, logs.html, data_portability.html, join_requests.html)
  uses non-clickable `<article>` cells.

## Target design

New top-to-bottom structure:

1. **Hero**: title/subtitle unchanged. Actions: `Add asset` (primary button) and a
   single `Actions` dropdown (pattern consistent with `export_dropdown`).
2. **Status strip**: five non-clickable `<article>` cells (same structure as
   server.html): Library / Published use / Unused / Missing / File types. Keep the
   current counts and annotations (`recoverable · external`, `orphan files`).
3. **Missing banner**: unchanged, shown only when `missing_count > 0`.
4. **Toolbar**: search + kind + status (the single status filter) + Filter + Clear.
   Unchanged.
5. **Table**: standard `data-table` with regular `record-row` rows and
   `inline-editor-row` edit rows (events.html pattern).
6. **Cleanup panel**: shown only when `unused_count > 0`; unused list (max 20) +
   `Export unused (.zip)` + `Archive & clean unused`.
7. **Modals**: Add asset and View, unchanged.

Removed: triage cards, "Assets by kind" card, fixed "Unused assets" card,
expandable-row layout.

### Table columns

- **File**: 36px thumbnail for images; pills `Esterno` / `Missing` / `Unused` /
  `Possible duplicate` / `Metadata`; filename + tiny path.
- **Kind**: pill.
- **Size**: current KB rendering.
- **Used**: yes/no badge with count.
- **Created**: date.
- **Actions**: View (opens existing detail modal: checksum, storage, linked
  records, copy), Edit (expands `inline-editor-row` with alt text, caption, source
  URL, kind + Save/Close), Delete (existing confirm form).

### Hero Actions dropdown

In order: Recover missing (only if `recovery.with_url`), Reconcile storage status,
Export unused (.zip) (only if `unused_count`), Data portability, Analyze quality.

## Backend changes

- Remove dead template context `exported_zips` (and the `list_exported_zips` call)
  from `assets_page` GET in `routes/dashboard_assets.py`.
- GET handler data flow is otherwise unchanged (same `assets`, `usage`,
  `linked_records`, `recovery_states`, `issue_*` flags, `metrics`, `recovery`,
  `cleanup_plan`).
- Route actions `export_all` / `export_filtered` / `import_zip` / `import_jsonl`
  have no UI and stay untouched (out of scope).

## CSS / JS changes

- Remove orphaned dashboard CSS classes: `.asset-triage*`, `.asset-summary-grid`,
  `.expandable-row`, `.asset-row-*`, `.asset-cell-*`.
- Keep reused classes: `.asset-thumb`, `.pill`, `.data-scroll`,
  `.dash-row-actions`, `.asset-create-tabs`, modal classes, tone/status-badge
  classes.
- In `static/js/dashboard/content.js`: remove the `toggle-asset-edit` handler;
  keep modal tab, View modal, copy, create handlers. Verify the shared dashboard
  JS already handles `data-row-toggle` (used by events/content) so the inline edit
  works without new JS.

## Testing

- Update `TESTS/webapp/test_dashboard_actions.py` (~line 1228): drop assertions on
  removed triage cards ("Asset health shortcuts", "Missing locally", "Needs
  intervention", "Metadata incomplete"); keep `option value="missing" selected`;
  assert strip `<article>` cells and that the Actions dropdown contains "Reconcile
  storage status".
- Preserve `aria-label="Open external asset {{ a.filename }}"` in the new File
  column so `test_revamp_contract.py` keeps passing.
- Add targeted tests: strip cells are non-clickable (`<article>`, no status `<a>`);
  Actions dropdown items (including conditional ones); cleanup panel only when
  `unused_count > 0`; table uses `record-row`/`inline-editor-row`.
- Final verification: `bash test_all.sh --suite quick` green.

## Out of scope

- Actions without UI (`export_all`, `export_filtered`, `import_zip`, `import_jsonl`).
- Route POST handlers behaviour.
- Scraper and database suites.
# Dashboard Assets page redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declutter the dashboard assets page and align it with the shared dashboard patterns (non-clickable status strip, single status filter, grouped hero actions, standard `data-table` + inline edit, conditional cleanup panel) without removing functionality.

**Architecture:** Rewrite `templates/dashboard/assets.html` (strip → `<article>`-style non-clickable cells, triage cards removed, hero actions into an `Actions` dropdown, table → `record-row`/`inline-editor-row`, bottom cards → one conditional cleanup panel); remove now-dead context (`exported_zips`, `issue_summary`) and imports from `routes/dashboard_assets.py`; remove orphaned CSS/JS; update two existing tests and add page-render tests.

**Tech Stack:** Flask + Jinja2, existing dashboard CSS (`static/css/dashboard.css`) and JS (`static/js/dashboard/content.js`), pytest via `test_all.sh`.

## Global Constraints

- Tests run with `bash test_all.sh --suite webapp` (from repo root). Optional extra pytest args after `--` are supported.
- `TESTS/conftest.py` already inserts `MIFPAPP/CORE` on `sys.path`; fixtures replicate the `app`/`client` pattern from `TESTS/webapp/test_dashboard_actions.py`.
- Do NOT touch `SCRAPERS/` or `MIFPAPP/DATABASE/`. No database writes except inside the test fixtures' temp dirs.
- Preserve the `aria-label="Open external asset {{ a.filename }}"` string verbatim in the template (asserted by `TESTS/webapp/test_revamp_contract.py`).
- Do NOT commit to git unless the user explicitly asks (the repo `.gitignore` also ignores `docs/`).
- The assets page route actions `export_all`, `export_filtered`, `import_zip`, `import_jsonl` have no UI and must remain untouched in the route.
- Keep `services/asset_cleanup.py:list_exported_zips` and its unit test in `TESTS/webapp/test_asset_cleanup.py` untouched (still a tested service).

---

### Task 1: Rewrite assets page template, clean route context, update/add tests

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/assets.html` (hero actions, strip, remove triage, table, bottom cards)
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard_assets.py` (remove `list_exported_zips` import + `exported_zips`/`issue_summary` context)
- Modify: `TESTS/webapp/test_dashboard_actions.py:1213-1232`
- Create: `TESTS/webapp/test_assets_page.py`

**Interfaces:**
- Consumes: `assets_page` GET already passes `counts`, `assets`, `usage`, `summary`, `total_mb`, `unused_count`, `used_count`, `missing_count`, `orphan_count`, `cleanup_plan`, `linked_records`, `recovery` (`missing`, `with_url`, `external`, `deferred`, `terminal`), `metrics`, `q`, `kind`, `status`.
- Produces: updated `assets.html` using only the context above (no `exported_zips`, no `issue_summary`); new test module `test_assets_page.py`.

- [ ] **Step 1: Write the new page-render tests** in `TESTS/webapp/test_assets_page.py` (copy the `app`/`client` fixtures from `TESTS/webapp/test_dashboard_actions.py`, plus a `_db(app)` helper):

```python
from __future__ import annotations

import io
import os
import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "CONFERENCES_DIR": str(tmp_path / "conferences"),
            "LOG_DIR": str(tmp_path / "logs"),
            "SECRET_KEY": "assets-page-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    with connect(app.config["DATABASE_PATH"]) as conn:
        migrate_content_schema(conn)
    yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "assets-page-csrf"
    return client


def _db(app) -> sqlite3.Connection:
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _insert_asset(app, *, filename, path, kind, checksum, storage_status="local",
                  is_external=0, source_url="", caption="", alt_text=""):
    with _db(app) as conn:
        conn.execute(
            """
            INSERT INTO assets(filename, original_filename, path, kind, size,
                               checksum, is_external, source_url, storage_status,
                               caption, alt_text, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """,
            (filename, filename, path, kind, 2048, checksum, is_external, source_url,
             storage_status, caption, alt_text),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def test_strip_is_summary_not_filters(app, client):
    resp = client.get("/dashboard/assets")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'aria-label="Asset library status"' in body
    assert "operations-status-strip" in body
    assert "assets-status-strip" not in body
    assert "Asset health shortcuts" not in body
    assert '<a href="/dashboard/assets?status=' not in body


def test_single_status_filter_via_toolbar(app, client):
    resp = client.get("/dashboard/assets?status=missing")
    body = resp.get_data(as_text=True)
    assert 'option value="missing" selected' in body


def test_actions_dropdown_conditional_items(app, client):
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Reconcile storage status" in body
    assert "Export unused" not in body
    assert "Recover missing" not in body


def test_actions_dropdown_shows_recover_when_recoverable(app, client):
    _insert_asset(
        app, filename="recoverable.jpg", path="image/recoverable.jpg", kind="image",
        checksum="assets-page-recoverable", storage_status="missing",
        source_url="https://example.test/recoverable.jpg",
    )
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Recover missing" in body


def test_cleanup_panel_only_when_unused(app, client):
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Unused assets" not in body
    assert "Archive &amp; clean unused" not in body
    _insert_asset(app, filename="unused.txt", path="other/unused.txt", kind="other",
                  checksum="assets-page-unused")
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Unused assets" in body
    assert "Archive &amp; clean unused" in body


def test_table_uses_standard_record_rows(app, client):
    _insert_asset(app, filename="page.png", path="image/page.png", kind="image",
                  checksum="assets-page-table")
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "record-row" in body
    assert "inline-editor-row" in body
    assert "expandable-row" not in body
    assert "asset-row-inner" not in body


def test_view_button_and_external_aria_preserved(app, client):
    _insert_asset(
        app, filename="doc.pdf", path="external/doc.pdf", kind="pdf",
        checksum="assets-page-external", storage_status="external", is_external=1,
        source_url="https://example.test/doc.pdf",
    )
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert 'aria-label="Open external asset {{ a.filename }}"' in body
    assert "asset-view-btn" in body
    assert "Esterno" in body
```

- [ ] **Step 2: Update the existing page test** in `TESTS/webapp/test_dashboard_actions.py:1213-1232` — replace the whole `test_assets_page_triage_filters_missing_and_metadata` function with:

```python
def test_assets_page_missing_filter_and_banner(app, client):
    with _db(app) as conn:
        conn.execute(
            """
            INSERT INTO assets(filename,path,kind,storage_status,source_url)
            VALUES('recoverable.jpg','image/recoverable.jpg','image','missing','https://example.test/recoverable.jpg')
            """
        )
        conn.commit()

    response = client.get("/dashboard/assets?status=missing")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "recoverable.jpg" in body
    assert "files are absent locally" in body
    assert "Asset health shortcuts" not in body
    assert "Missing locally" not in body
    assert 'option value="missing" selected' in body
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `bash test_all.sh --suite webapp -- -k "assets_page or assets_page_triage" TESTS/webapp`
Expected: FAIL — `test_strip_is_summary_not_filters` (`"assets-status-strip" not in body` fails), `test_cleanup_panel_only_when_unused` ("Unused assets" present on the old template), `test_table_uses_standard_record_rows` ("record-row" missing), and the updated dashboard-actions test ("Asset health shortcuts" still present).

- [ ] **Step 4: Rewrite `MIFPAPP/CORE/mifp_app/templates/dashboard/assets.html`**

Replace the hero actions block (currently lines 5-20, the `page_header` caller) with:

```html
{% call page_header('Operations', 'Assets', 'Manage uploaded files, recover missing media and keep metadata ready for publication.') %}
    <button class="btn btn-primary btn-sm" type="button" data-bs-toggle="modal" data-bs-target="#assetCreateModal"><i class="bi bi-upload"></i> Add asset</button>
    <div class="export-dropdown">
      <button class="btn btn-outline btn-sm" type="button" data-export-toggle>
        <i class="bi bi-sliders" aria-hidden="true"></i> Actions <span class="export-caret" aria-hidden="true">&#9662;</span>
      </button>
      <div class="dropdown-menu">
        {% if recovery.with_url %}
        <form method="post" action="{{ url_for('dashboard.assets_retry_external') }}">
          <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
          <button type="submit"><i class="bi bi-cloud-download" aria-hidden="true"></i> Recover missing</button>
        </form>
        {% endif %}
        <form method="post" action="{{ url_for('dashboard.assets_page') }}" data-confirm="Recompute storage status from the local filesystem and external flag? A database backup will be created first.">
          <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
          <input type="hidden" name="action" value="reconcile">
          <button type="submit"><i class="bi bi-arrow-repeat" aria-hidden="true"></i> Reconcile storage status</button>
        </form>
        {% if unused_count %}
        <form method="post" action="{{ url_for('dashboard.assets_page') }}">
          <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
          <input type="hidden" name="action" value="export_unused">
          <button type="submit"><i class="bi bi-download" aria-hidden="true"></i> Export unused (.zip)</button>
        </form>
        {% endif %}
        <a href="{{ url_for('dashboard.data_portability') }}"><i class="bi bi-arrow-down-up" aria-hidden="true"></i> Data portability</a>
        <a href="{{ url_for('dashboard.data_quality_page') }}#analysis"><i class="bi bi-clipboard2-pulse" aria-hidden="true"></i> Analyze quality</a>
      </div>
    </div>
{% endcall %}
```

Replace the status strip + triage section (currently lines 21-58) with:

```html
<section class="operations-status-strip" aria-label="Asset library status">
  <div class="operations-status-lead">
    <i class="bi bi-folder2-open" aria-hidden="true"></i>
    <span><small>Library</small><strong>{{ counts.assets }}</strong><em>files · {{ total_mb }} MB</em></span>
  </div>
  <div><span><small>Published use</small><strong class="tone-success">{{ used_count }}</strong><em>linked files</em></span></div>
  <div><span><small>Unused</small><strong class="tone-warning">{{ unused_count }}</strong><em>review before cleanup</em></span></div>
  <div><span><small>Missing</small><strong class="{{ 'tone-danger' if missing_count else 'tone-muted' }}">{{ missing_count }}</strong><em>{{ recovery.with_url }} recoverable{% if recovery.external %} · {{ recovery.external }} external{% endif %}</em></span></div>
  <div><span><small>File types</small><strong>{{ summary|length }}</strong><em>{% if orphan_count %}{{ orphan_count }} orphan files{% else %}No orphan files{% endif %}</em></span></div>
</section>
```

Replace the table block (currently lines 172-279: `div.table-card` through its closing `</div>`) with:

```html
<div class="table-card">
  <div class="data-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th class="asset-col-file">File</th>
          <th class="asset-col-kind">Kind</th>
          <th class="asset-col-size col-num">Size</th>
          <th class="asset-col-used col-num">Used</th>
          <th class="asset-col-created">Created</th>
          <th class="col-actions">Actions</th>
        </tr>
      </thead>
      <tbody>
      {% for a in assets %}
        {% set u = usage.get(a.id, 0) %}
        {% set asset_url = url_for('dashboard.asset_file', filename=a.path.split('/', 1)[1] if a.path and '/' in a.path else a.path) if a.path else (a.source_url or '') %}
        <tr class="record-row" data-row-toggle="asset-{{ a.id }}">
          <td class="asset-col-file">
            {% if a.kind == 'image' and a.path %}
            <div class="asset-thumb"><img src="{{ asset_url }}" alt="{{ a.alt_text or a.filename }}" width="36" height="36" loading="lazy" data-error-fallback="{{ a.source_url or asset_url }}"></div>
            {% elif a.kind == 'pdf' %}<div class="asset-thumb">{% if a.source_url and a.is_external %}<a href="{{ a.source_url }}" target="_blank" rel="noopener noreferrer" aria-label="Open external asset {{ a.filename }}"><i class="bi bi-file-earmark-pdf" aria-hidden="true"></i></a>{% else %}<i class="bi bi-file-earmark-pdf" aria-hidden="true"></i>{% endif %}</div>
            {% elif a.kind == 'video' %}<div class="asset-thumb"><i class="bi bi-film" aria-hidden="true"></i></div>
            {% else %}<div class="asset-thumb">{% if a.source_url and a.is_external %}<a href="{{ a.source_url }}" target="_blank" rel="noopener noreferrer" aria-label="Open external asset {{ a.filename }}"><i class="bi bi-link-45deg" aria-hidden="true"></i></a>{% else %}<i class="bi bi-file-earmark" aria-hidden="true"></i>{% endif %}</div>
            {% endif %}
            <b class="asset-file-title">{{ a.original_filename or a.filename }}</b>
            {% if a.is_external or not a.path or a.path.startswith('external/') %}<span class="pill pill-warning asset-external-pill">Esterno</span>{% endif %}
            {% if a.issue_missing %}<span class="pill pill-danger">Missing</span>{% endif %}
            {% if a.issue_unused %}<span class="pill pill-warning">Unused</span>{% endif %}
            {% if a.issue_duplicate %}<span class="pill pill-info">Possible duplicate</span>{% endif %}
            {% if a.issue_metadata %}<span class="pill">Metadata</span>{% endif %}
            <br><span class="tiny muted">{{ a.path }}</span>
            {% if a.recovery_state and a.recovery_state.last_error %}
            <br><span class="tiny tone-danger">Recovery: {{ a.recovery_state.last_error }}</span>
            {% endif %}
            {% if a.caption %}<br><span class="tiny">{{ a.caption }}</span>{% endif %}
          </td>
          <td><span class="pill">{{ a.kind }}</span></td>
          <td class="col-num">{{ (a.size/1024)|round(1) if a.size else 0 }} KB</td>
          <td><span class="cell-bool {{ 'yes' if u else 'no' }}">{{ u }}</span></td>
          <td><span class="cell-date">{{ a.created_at or '—' }}</span></td>
          <td class="col-actions">
            <div class="dash-row-actions">
              <button class="btn btn-mini btn-outline asset-view-btn" type="button"
                data-filename="{{ a.original_filename or a.filename }}"
                data-path="{{ a.path or '' }}"
                data-source-url="{{ a.source_url or '' }}"
                data-kind="{{ a.kind or '' }}"
                data-mime="{{ a.mime_type or '' }}"
                data-size="{{ (a.size/1024)|round(1) if a.size else 0 }} KB"
                data-checksum="{{ a.checksum or '' }}"
                data-storage="{{ a.storage_status or '' }}"
                data-public-url="{{ asset_url }}"
                data-preview="{{ asset_url if a.kind == 'image' else '' }}"
                data-links="{% for link in linked_records.get(a.id, []) %}{{ link.entity_type }} #{{ link.entity_id }} {{ link.role }}: {{ link.label or 'record' }}{% if not loop.last %}||{% endif %}{% endfor %}"
              >View</button>
              <button class="btn btn-mini btn-outline" type="button" data-row-toggle="asset-{{ a.id }}" aria-expanded="false">Edit</button>
              <form method="post" action="{{ url_for('dashboard.assets_page') }}" class="d-inline" data-confirm="Archive and remove asset #{{ a.id }}? A recovery ZIP and database backup will be created first.">
                <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
                <input type="hidden" name="action" value="delete">
                <input type="hidden" name="id" value="{{ a.id }}">
                <button class="btn btn-mini btn-outline-danger">Delete</button>
              </form>
            </div>
          </td>
        </tr>
        <tr class="inline-editor-row" id="asset-{{ a.id }}" data-inline-panel>
          <td colspan="6">
            <form method="post" action="{{ url_for('dashboard.assets_page') }}" class="inline-form">
              <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
              <input type="hidden" name="action" value="update">
              <input type="hidden" name="id" value="{{ a.id }}">
              <div class="inline-form-head">
                <div><b>Edit #{{ a.id }} &middot; {{ a.original_filename or a.filename }}</b><small>Edit inline. Save before closing.</small></div>
                <div class="inline-actions">
                  <button class="btn btn-primary btn-sm">Save</button>
                  <button class="btn btn-outline btn-sm" type="button" data-row-toggle="asset-{{ a.id }}">Close</button>
                </div>
              </div>
              <div class="inline-form-grid">
                <label class="field"><span>Alt text</span><input type="text" name="alt_text" value="{{ a.alt_text or '' }}" maxlength="500" class="form-control form-control-sm"></label>
                <label class="field"><span>Caption</span><input type="text" name="caption" value="{{ a.caption or '' }}" maxlength="500" class="form-control form-control-sm" placeholder="Display label"></label>
                <label class="field"><span>Source URL</span><input type="url" name="source_url" value="{{ a.source_url or '' }}" maxlength="2048" class="form-control form-control-sm" placeholder="https://..."></label>
                <label class="field"><span>Kind</span>
                  <select name="kind" class="form-select form-select-sm">
                    {% for k in ['image','document','pdf','video','other'] %}
                    <option value="{{ k }}" {{ 'selected' if a.kind == k }}>{{ k }}</option>
                    {% endfor %}
                  </select>
                </label>
                <div class="field wide"><span class="tiny muted">Created: {{ a.created_at }} · Checksum: {{ (a.checksum or '—')[:16] }}…</span></div>
              </div>
            </form>
          </td>
        </tr>
      {% else %}
        <tr><td colspan="6" class="empty">No assets found.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

Replace the bottom cards block (currently lines 281-325: the `asset-summary-grid` div) with:

```html
{% if unused_count %}
<section class="modern-card">
  <div class="panel-head">
    <div><h2><i class="bi bi-box-seam" aria-hidden="true"></i> Unused assets</h2><p>Database-tracked assets not linked to any content.</p></div>
    <span class="pill">{{ unused_count }}</span>
  </div>
  {% if cleanup_plan and cleanup_plan.unused_db_assets %}
  <div class="bar-list asset-scroll-list">
    {% for asset in cleanup_plan.unused_db_assets[:20] %}
    <div class="bar-row">
      <span title="{{ asset.path }}">{{ asset.filename or asset.original_filename or asset.path }}</span>
      <strong>#{{ asset.id }}</strong>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  <div class="asset-action-row">
    <form method="post" action="{{ url_for('dashboard.assets_page') }}">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
      <input type="hidden" name="action" value="export_unused"><button class="btn btn-primary btn-sm"><i class="bi bi-download"></i> Export unused (.zip)</button>
    </form>
    <form method="post" action="{{ url_for('dashboard.cleanup_unused_assets') }}" data-confirm="Archive and remove {{ unused_count }} unused assets? A recovery ZIP and database backup will be created first.">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">
      <input type="hidden" name="apply" value="1">
      <button class="btn btn-danger btn-sm"><i class="bi bi-archive"></i> Archive &amp; clean unused</button>
    </form>
  </div>
</section>
{% endif %}
```

Leave the Add-asset modal (lines 73-150), the missing banner (lines 60-71), the toolbar (lines 152-170), and the View modal (lines 327-358) unchanged.

- [ ] **Step 5: Clean dead context from `MIFPAPP/CORE/mifp_app/routes/dashboard_assets.py`**

Remove three things:
1. The `list_exported_zips,` line from the `from ..services.asset_cleanup import (...)` block (around line 28).
2. The `exported_zips = list_exported_zips(current_app.config["ASSETS_DIR"], export_dir=current_app.config["EXPORT_DIR"])` line (around line 403).
3. The `exported_zips=exported_zips,` and `issue_summary=issue_summary,` kwargs in the `render_template` call (around lines 456-459), and the `issue_summary = {...}` block (around lines 436-443).

Verify with `grep -n "exported_zips\|issue_summary" MIFPAPP/CORE/mifp_app/routes/dashboard_assets.py` → no matches.

- [ ] **Step 6: Run the new + updated tests to verify they pass**

Run: `bash test_all.sh --suite webapp -- -k "assets_page or assets_page_triage" TESTS/webapp`
Expected: PASS (8 tests from `test_assets_page.py` + updated `test_dashboard_actions.py`).

- [ ] **Step 7: Run the full webapp suite**

Run: `bash test_all.sh --suite webapp`
Expected: PASS (595 + 8 − 1 renamed ≈ 602 tests). Also run `bash test_all.sh --suite quick` for the complete cross-suite check.

- [ ] **Step 8: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/templates/dashboard/assets.html \
        MIFPAPP/CORE/mifp_app/routes/dashboard_assets.py \
        TESTS/webapp/test_assets_page.py \
        TESTS/webapp/test_dashboard_actions.py
git commit -m "feat: declutter and standardize dashboard assets page"
```

---

### Task 2: Remove orphaned CSS and style the Actions dropdown buttons

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/css/dashboard.css`

**Interfaces:**
- Consumes: the new `assets.html` markup (classes `asset-col-*`, `asset-thumb`, `asset-file-title`, `asset-external-pill`, `cell-bool`, `inline-editor-row`, `inline-form*`, `export-dropdown`, `modern-card`, `panel-head`, `bar-list`, `asset-scroll-list`, `asset-action-row`).
- Produces: dead asset CSS removed; dropdown buttons styled; `td.asset-col-file` wrapping enabled.

- [ ] **Step 1: Remove dead rules**

Delete these blocks/rules from `dashboard.css`:
1. `.asset-col-id { width: 2.5rem; }` (line 1176) and `.asset-col-preview { width: 2.8rem; }` (line 1177).
2. `.asset-detail-cell { padding: 0; }` (line 1183).
3. `.asset-used-count { ... }` (lines 1192-1198).
4. `.asset-summary-grid { ... }` (lines 1199-1204).
5. `.asset-triage { ... }` through the `@media (max-width: 460px) { .asset-triage { ... } }` block (lines 1205-1217).
6. `.asset-row-inner { ... }` through `.expandable-row.expanded .asset-row-edit { display: block; }` (lines 1466-1474). **Keep** the following `.asset-thumb` block (lines 1475-1477).
7. The `@media` rule `.asset-summary-grid { grid-template-columns: 1fr; }` (lines 1642-1644).
8. `.asset-row-edit { ... }` (lines 2906-2910) and `.asset-cell-actions [aria-expanded="true"] { ... }` (lines 2911-2915).

Keep: `.asset-file-title` (1184-1186), `.asset-external-pill` (1187-1191), `.asset-col-file` (1178), `.asset-col-kind`/`.asset-col-size` (1179-1180), `.asset-col-used` (1181), `.asset-col-created` (1182), `.asset-scroll-list` (1218-1222), `.asset-action-row` (1223-1228 + 1635-1638), `.asset-thumb` (1475-1477), `.asset-modal-*` (1478+), `.asset-create-tabs` (2864+).

- [ ] **Step 2: Add dropdown-button styles and File-cell wrapping**

Insert after the `.export-dropdown .dropdown-menu a:hover { ... }` rule (line 1549):

```css
.export-dropdown .dropdown-menu form { display: contents; }
.export-dropdown .dropdown-menu button { display: flex; align-items: center; gap: .4rem; width: 100%; padding: .45rem .5rem; border: 0; border-radius: var(--radius-sm); background: none; color: var(--text); font-size: .8rem; text-align: left; cursor: pointer; }
.export-dropdown .dropdown-menu button:hover { color: var(--accent); background: var(--accent-subtle); }
```

Insert after the `.asset-col-created { width: 8rem; }` rule (line 1182):

```css
.data-table td.asset-col-file { max-width: 26rem; white-space: normal; }
```

- [ ] **Step 3: Verify no orphaned selectors remain and suite still green**

Run:
```bash
grep -n "asset-triage\|asset-summary-grid\|expandable-row\|asset-row-\|asset-cell-\|asset-used-count\|asset-detail-cell\|asset-col-id\|asset-col-preview" MIFPAPP/CORE/mifp_app/static/css/dashboard.css
```
Expected: no matches.
Run: `bash test_all.sh --suite webapp`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/static/css/dashboard.css
git commit -m "style: remove dead asset CSS and style Actions dropdown items"
```

---

### Task 3: Remove the unused asset edit JS handler

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js`

**Interfaces:**
- Consumes: the new table uses the generic `data-row-toggle` + `inline-editor-row` handling already present in `content.js` (`toggleInlinePanel`, the click handler around lines 412-419). The View modal, create-modal tabs, and copy handlers stay.
- Produces: the `[data-action="toggle-asset-edit"]` branch removed.

- [ ] **Step 1: Remove the obsolete handler**

In `static/js/dashboard/content.js`, delete the block (lines 364-375):

```js
  var assetEditButton = ev.target.closest('[data-action="toggle-asset-edit"]');
  if (assetEditButton) {
    ev.preventDefault();
    var assetRow = assetEditButton.closest('.expandable-row');
    if (!assetRow) return;
    var assetExpanded = !assetRow.classList.contains('expanded');
    assetRow.classList.toggle('expanded', assetExpanded);
    assetEditButton.setAttribute('aria-expanded', assetExpanded ? 'true' : 'false');
    assetEditButton.textContent = assetExpanded ? 'Close edit' : 'Edit';
    if (assetExpanded) assetRow.querySelector('.asset-row-edit input:not([type="hidden"])')?.focus();
    return;
  }
```

- [ ] **Step 2: Verify the JS no longer references removed classes**

Run:
```bash
grep -n "toggle-asset-edit\|expandable-row\|asset-row-edit" MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js
```
Expected: no matches.

- [ ] **Step 3: Run the full webapp suite**

Run: `bash test_all.sh --suite webapp`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js
git commit -m "refactor: drop obsolete asset expandable-row edit handler"
```

---

### Final verification

- [ ] **Step 1:** Run the complete quick suite and confirm green: `bash test_all.sh --suite quick`
- [ ] **Step 2:** Render `/dashboard/assets` against a copy of the real DB (reuse the earlier `/tmp/opencode` approach with `create_app` + session login) and visually confirm: strip not clickable, Actions dropdown present, no triage cards, standard table rows, cleanup panel hidden (unused=0).

# Data Quality — 3-Phase Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cluttered single-page data quality UI with a clean 3-phase workflow: Analysis → Review → Action.

**Architecture:** Frontend-only rewrite of the data quality template, JS, and CSS. Backend endpoints are reused unchanged (analyze, findings, bundles, apply). The existing `data-quality.js` module is fully replaced with a simpler 3-phase state machine.

**Tech Stack:** Flask/Jinja2 (template), vanilla JS (no framework), CSS custom properties (dashboard.css), Bootstrap 5.3 modals.

## Global Constraints

- All existing pytest tests (210) must continue to pass.
- No new backend endpoints — reuse `/data-quality/*` routes exactly as they are.
- The `best_quality` strategy is the default for all accepted findings.
- Cache-busting parameter `v=` on JS/CSS assets must be bumped on each change.
- No smart_merge references anywhere — those were already removed.

---

### Task 1: Rewrite `data_quality.html` template — 3-phase layout

**Files:**
- Rewrite: `MIFPAPP/CORE/mifp_app/templates/dashboard/data_quality.html`
- Reference: `MIFPAPP/CORE/mifp_app/static/css/dashboard.css` (for phase class names)

**Interfaces:**
- Consumes: `run` (dict or None), `bundle` (dict or None) from the view function
- Produces: HTML with 3 phase panels, each with `id="dqPhase1"`, `id="dqPhase2"`, `id="dqPhase3"`

- [ ] **Step 1: Read the current view function signature**

```bash
python3 -c "
from pathlib import Path; import sys; sys.path.insert(0, 'WEBAPP')
from mifp_app.routes.dashboard_data_quality import data_quality_page
import inspect; print(inspect.getsource(data_quality_page))
"
```
Expected: confirms `run=run, bundle=bundle` are passed.

- [ ] **Step 2: Write the new template body**

Replace the entire content of `data_quality.html` with this 3-phase layout. The `{% extends %}`, `{% block page_title %}`, `{% block content %}`, and `{% block extra_js %}` structure is preserved.

```html
{% extends "dashboard/layout.html" %}
{% from "dashboard/_components.html" import page_header %}
{% block page_title %}Data quality{% endblock %}
{% block content %}
{% call page_header('Data quality', 'Database cleanup', 'Review findings and apply automatic fixes.') %}
  <button type="button" class="btn btn-primary btn-sm" id="dqAnalyze"><i class="bi bi-stars"></i> Analyze database</button>
{% endcall %}

<div class="dq-app">

  <!-- Phase 1: Analysis -->
  <section class="dq-phase" id="dqPhase1" data-phase="1">
    <header class="dq-phase-header">
      <span class="dq-phase-num">1</span>
      <div><h2>Analysis</h2><p>Scan the database for quality issues. This step never changes content.</p></div>
    </header>
    <div class="dq-progress" id="dqProgress" hidden>
      <div class="dq-progress-track"><span id="dqProgressFill"></span></div>
      <b id="dqProgressPercent"></b>
      <span id="dqProgressText"></span>
      <time id="dqProgressTime"></time>
    </div>
    <div class="dq-summary" id="dqSummary">
      {% if run %}
      <div class="dq-summary-cards" id="dqSummaryCards">
        {% set actions = run.summary.actions if run and run.summary else {} %}
        {% for value, label, icon in [
          ('clean_record','Records to clean','bi-eraser'),
          ('split_aggregated_record','Aggregated records','bi-scissors'),
          ('enrich_record','Enrichments / series','bi-plus-circle'),
          ('repair_relations_or_assets','Links & assets','bi-link-45deg'),
          ('merge_records','Potential duplicates','bi-intersect'),
        ] %}
        <button type="button" class="dq-summary-card" data-action-filter="{{ value }}">
          <i class="bi {{ icon }}"></i>
          <b>{{ actions.get(value, 0) }}</b>
          <span>{{ label }}</span>
        </button>
        {% endfor %}
      </div>
      {% else %}
      <div class="dq-empty-phase">
        <i class="bi bi-clipboard-data"></i>
        <b>No analysis results</b>
        <p>Run an analysis to discover data quality issues.</p>
      </div>
      {% endif %}
    </div>
  </section>

  <hr class="dq-phase-divider">

  <!-- Phase 2: Review -->
  <section class="dq-phase {% if not run %}dq-phase-disabled{% endif %}" id="dqPhase2" data-phase="2">
    <header class="dq-phase-header">
      <span class="dq-phase-num">2</span>
      <div><h2>Review</h2><p>Review each issue and accept or reject the proposed automatic fix.</p></div>
    </header>
    <form class="dq-toolbar" id="dqFilters">
      <label>
        <span>Action</span>
        <select class="form-select form-select-sm" name="action_type">
          <option value="">All actions</option>
          <option value="clean_record">Clean record</option>
          <option value="enrich_record">Enrich record</option>
          <option value="split_aggregated_record">Split aggregated record</option>
          <option value="repair_relations_or_assets">Repair links or assets</option>
          <option value="merge_records">Merge duplicates</option>
        </select>
      </label>
      <label>
        <span>Content</span>
        <select class="form-select form-select-sm" name="entity_type">
          <option value="">All content</option>
          <option value="member">Members</option>
          <option value="event">Events</option>
          <option value="news">News</option>
          <option value="publication">Publications</option>
          <option value="sponsor">Sponsors</option>
          <option value="asset">Assets</option>
        </select>
      </label>
      <label>
        <span>Finding</span>
        <select class="form-select form-select-sm" name="classification">
          <option value="reviewable" selected>Reviewable</option>
          <option value="">All</option>
          <option value="exact_duplicate">Exact duplicate</option>
          <option value="strong_candidate">Strong candidate</option>
          <option value="needs_cleaning">Needs cleaning</option>
          <option value="aggregated_record">Aggregated record</option>
          <option value="invalid_record">Invalid record</option>
          <option value="ambiguous">Ambiguous</option>
          <option value="blocked">Blocked</option>
          <option value="related_not_duplicate">Related, not duplicate</option>
        </select>
      </label>
      <button class="btn btn-primary btn-sm"><i class="bi bi-funnel"></i> Filter</button>
    </form>
    <div class="dq-bulk-bar">
      <span id="dqResultCount">—</span>
      <button type="button" class="btn btn-primary btn-sm" id="dqAcceptAll" disabled><i class="bi bi-check2-all"></i> Accept all visible</button>
      <button type="button" class="btn btn-outline btn-sm" id="dqRejectAll" disabled><i class="bi bi-x-lg"></i> Reject all visible</button>
    </div>
    <div class="dq-finding-list" id="dqFindings">
      <div class="dq-empty-state"><i class="bi bi-inboxes"></i><b>No findings loaded</b><p>Run an analysis or open the latest result.</p></div>
    </div>
    <div class="dq-load-more"><button type="button" class="btn btn-outline btn-sm" id="dqLoadMore" hidden>Load 30 more</button></div>
  </section>

  <hr class="dq-phase-divider">

  <!-- Phase 3: Action -->
  <section class="dq-phase {% if not bundle %}dq-phase-disabled{% endif %}" id="dqPhase3" data-phase="3">
    <header class="dq-phase-header">
      <span class="dq-phase-num">3</span>
      <div><h2>Action</h2><p>Accepted findings are queued here. Apply them all at once.</p></div>
    </header>
    <div class="dq-queue-summary">
      <span id="dqQueueCount">{{ bundle['items']|length if bundle else 0 }} accepted</span>
      <button type="button" class="btn btn-primary btn-sm" id="dqApplyAll" {% if not bundle %}disabled{% endif %}><i class="bi bi-database-check"></i> Apply all</button>
      <button type="button" class="btn btn-outline btn-sm" id="dqClearQueue" {% if not bundle %}disabled{% endif %}><i class="bi bi-trash3"></i> Clear queue</button>
    </div>
    <div class="dq-queue-list" id="dqQueueItems">
      {% if bundle and bundle['items'] %}
      {% for item in bundle['items'] %}
      <div class="dq-queue-item" data-item-id="{{ item.id }}">
        <span class="dq-queue-action">{{ item.action_type }}</span>
        <span class="dq-queue-entity">{{ item.entity_type }} #{{ item.record_ids_json|truncate(20) }}</span>
      </div>
      {% endfor %}
      {% else %}
      <div class="dq-empty-state"><i class="bi bi-box-seam"></i><b>Queue is empty</b><p>Accept findings in the Review phase to build your queue.</p></div>
      {% endif %}
    </div>
    <div class="dq-apply-progress" id="dqApplyProgress" hidden>
      <div class="dq-progress-track"><span id="dqApplyFill"></span></div>
      <b id="dqApplyStatus">Working…</b>
    </div>
    <div class="dq-history" id="dqHistory">
      <h3>History</h3>
      {% if bundle and bundle.report.status == 'applied' %}
      <div class="dq-history-item">
        <span class="status-badge status-success">Applied</span>
        <small>{{ bundle.report.applied_at }}</small>
        <code>{{ bundle.report.backup_path }}</code>
      </div>
      {% else %}
      <p class="text-3" style="font-size:.7rem">No previous applications.</p>
      {% endif %}
    </div>
  </section>

</div>

<div class="modal fade confirm-dialog" id="dqApplyModal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog modal-dialog-centered totem-dialog"><div class="modal-content smart-modal totem-modal">
    <div class="modal-header"><h3>Apply all accepted changes?</h3><button class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>
    <div class="modal-body">
      <p>The server creates and verifies a backup, revalidates every record fingerprint, then applies all actions in one transaction.</p>
      <label><input type="checkbox" id="dqApplyConfirm"><span> I reviewed the changes and want to apply them.</span></label>
    </div>
    <div class="modal-footer">
      <button class="btn btn-outline btn-sm" data-bs-dismiss="modal">Cancel</button>
      <button class="btn btn-primary btn-sm" id="dqApplyNow" disabled>Apply all</button>
    </div>
  </div></div>
</div>
{% endblock %}

{% block extra_js %}
{{ super() }}
<script src="{{ url_for('static', filename='js/dashboard/data-quality.js', v='20260726-5') }}" defer></script>
<script type="application/json" id="dqConfig" nonce="{{ csp_nonce }}">{{ {
  'runId': run.id if run else none,
  'bundleId': bundle.id if bundle else none,
  'analyzeUrl': url_for('dashboard.data_quality_analyze'),
  'findingsUrl': url_for('dashboard.data_quality_findings'),
  'findingUrl': url_for('dashboard.data_quality_finding', finding_id=0),
  'decisionUrl': url_for('dashboard.data_quality_decision', finding_id=0),
  'bundlesUrl': url_for('dashboard.data_quality_bundle_create'),
  'bundleUrl': url_for('dashboard.data_quality_bundle', bundle_id=0),
}|tojson }}</script>
{% endblock %}
```

- [ ] **Step 3: Verify template syntax**

```bash
python3 -c "
from pathlib import Path; import sys; sys.path.insert(0, 'WEBAPP')
from flask import Flask
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('MIFPAPP/CORE/mifp_app/templates'))
tmpl = env.get_template('dashboard/data_quality.html')
print('Template compiles OK')
"
```
Expected: "Template compiles OK"

- [ ] **Step 4: Run existing tests to confirm no backend breakage**

```bash
python3 -m pytest TESTS/webapp/ -q --tb=short
```
Expected: 210 passed, 6 skipped


### Task 2: Rewrite `data-quality.js` — 3-phase state machine

**Files:**
- Rewrite: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js`
- Reference: `MIFPAPP/CORE/mifp_app/services/data_quality/executor.py` (add_to_bundle interface)

**Interfaces:**
- Consumes: config from `<script id="dqConfig">` JSON element
- Produces: 3-phase UI behavior — analyze, load findings, accept/reject, apply

- [ ] **Step 1: Write the complete JS module**

Replace the entire content of `data-quality.js`:

```javascript
(function () {
  'use strict';

  var configEl = document.getElementById('dqConfig');
  if (!configEl) return;
  var config = JSON.parse(configEl.textContent);

  var id = function (name) { return document.getElementById(name); };

  var state = {
    runId: Number(config.runId || 0),
    bundleId: Number(config.bundleId || 0),
    findings: [],
    total: 0,
    offset: 0,
    acceptedIds: new Set(),
    phase: 1,
  };

  // ---- Helpers ----

  function toast(msg, type) {
    // window.MIFP.toast is provided by the dashboard shell
    if (window.MIFP && window.MIFP.toast) window.MIFP.toast(msg, type || 'info');
  }

  function endpoint(url, id) {
    return url.replace('/0', '/' + id);
  }

  function esc(str) {
    var d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  function now() { return Date.now(); }

  // ---- Phase 1: Analysis ----

  async function analyze() {
    var btn = id('dqAnalyze');
    btn.disabled = true;
    var progress = id('dqProgress');
    progress.hidden = false;
    var fill = id('dqProgressFill');
    var pct = id('dqProgressPercent');
    var text = id('dqProgressText');
    var time = id('dqProgressTime');
    var started = now();
    pct.textContent = 'Working';
    text.textContent = 'Starting analysis…';
    fill.style.width = '0%';
    var timer = setInterval(function () { time.textContent = Math.floor((now() - started) / 1000) + ' s'; }, 250);

    try {
      var response = await window.MIFP.request(config.analyzeUrl, { method: 'POST', json: {} });
      var data = response.data;
      clearInterval(timer);
      time.textContent = ((data.duration_ms || 0) / 1000).toFixed(1) + ' s';
      pct.textContent = 'Complete';
      text.textContent = 'Analysis complete. ' + data.finding_count + ' findings.';
      fill.style.width = '100%';
      state.runId = Number(data.run_id);
      // enable phase 2
      id('dqPhase2').classList.remove('dq-phase-disabled');
      // render summary cards
      renderSummary(data.summary);
      // auto-load latest analysis
      await loadFindings();
      toast('Analysis complete. ' + data.finding_count + ' findings.', 'success');
    } catch (error) {
      clearInterval(timer);
      pct.textContent = 'Failed';
      text.textContent = error.message || 'Analysis failed.';
      toast(error.message || 'Analysis failed.', 'error');
    } finally {
      btn.disabled = false;
    }
  }

  function renderSummary(summary) {
    if (!summary || !summary.actions) return;
    var cards = id('dqSummaryCards');
    if (cards) return; // already rendered on page load
    var container = id('dqSummary');
    var html = '<div class="dq-summary-cards" id="dqSummaryCards">';
    var items = [
      ['clean_record', 'Records to clean', 'bi-eraser'],
      ['split_aggregated_record', 'Aggregated records', 'bi-scissors'],
      ['enrich_record', 'Enrichments / series', 'bi-plus-circle'],
      ['repair_relations_or_assets', 'Links & assets', 'bi-link-45deg'],
      ['merge_records', 'Potential duplicates', 'bi-intersect'],
    ];
    items.forEach(function (item) {
      var count = summary.actions[item[0]] || 0;
      html += '<button type="button" class="dq-summary-card" data-action-filter="' + item[0] + '">';
      html += '<i class="bi ' + item[2] + '"></i><b>' + count + '</b><span>' + item[1] + '</span></button>';
    });
    html += '</div>';
    container.innerHTML = html;
  }

  // ---- Phase 2: Review ----

  async function loadFindings(append) {
    if (!state.runId) return;
    var form = id('dqFilters');
    var params = new URLSearchParams(new FormData(form));
    params.set('run_id', state.runId);
    if (!append) params.set('offset', '0');
    else params.set('offset', String(state.offset));

    var response = await window.MIFP.request(config.findingsUrl + '?' + params.toString());
    var items = response.data.items || [];
    state.total = Number(response.data.total || 0);
    if (!append) state.findings = items;
    else state.findings = state.findings.concat(items);
    state.offset = state.findings.length;

    var list = id('dqFindings');
    if (!append) list.replaceChildren();
    items.forEach(function (finding) { list.append(findingCard(finding)); });
    id('dqResultCount').textContent = state.findings.length + ' of ' + state.total;
    id('dqLoadMore').hidden = state.findings.length >= state.total;
    id('dqAcceptAll').disabled = state.total === 0;
    id('dqRejectAll').disabled = state.total === 0;
    if (!state.findings.length) {
      list.innerHTML = '<div class="dq-empty-state"><i class="bi bi-check2-circle"></i><b>No findings</b><p>No matching findings for the current filter.</p></div>';
    }
  }

  function findingCard(finding) {
    var card = document.createElement('div');
    card.className = 'dq-finding-card';
    card.dataset.findingId = finding.id;
    if (state.acceptedIds.has(finding.id)) card.classList.add('is-accepted');

    // evidence line
    var evidence = (finding.evidence || [])[0] || {};
    var problem = document.createElement('div');
    problem.className = 'dq-finding-evidence';
    problem.textContent = evidence.explanation || finding.classification;
    card.append(problem);

    // solution: show proposed field values
    var plan = finding.plan || {};
    var sol = document.createElement('div');
    sol.className = 'dq-finding-solution';
    var fields = plan.fields || [];
    if (finding.action_type === 'split_aggregated_record') {
      var segs = plan.proposed_records || [];
      sol.textContent = 'Split into ' + segs.length + ' records: ' + segs.map(function (s) { return s.title_hint || s.segment; }).join(', ');
    } else if (fields.length) {
      sol.textContent = fields.map(function (f) {
        var val = f.proposed_value != null ? esc(String(f.proposed_value)) : '—';
        return f.field + ': ' + val;
      }).join('  |  ');
    } else if (finding.action_type === 'merge_records') {
      sol.textContent = 'Canonical: #' + (plan.canonical_id || finding.record_ids[0]) + ' — merge ' + (finding.record_ids.length) + ' records';
    } else {
      sol.textContent = 'Auto-fix available';
    }
    card.append(sol);

    // type + entity badges
    var meta = document.createElement('div');
    meta.className = 'dq-finding-meta';
    meta.innerHTML = '<span class="dq-badge-action">' + finding.action_type + '</span> <span class="dq-badge-entity">' + finding.entity_type + '</span> <span class="dq-badge-class">' + finding.classification + '</span>';
    card.append(meta);

    // accept / reject buttons
    var actions = document.createElement('div');
    actions.className = 'dq-finding-actions';

    var accept = document.createElement('button');
    accept.type = 'button';
    accept.className = 'btn btn-primary btn-sm';
    accept.innerHTML = '<i class="bi bi-check-lg"></i> Accept';
    accept.addEventListener('click', function (event) {
      event.stopPropagation();
      acceptFinding(finding).catch(function (error) { toast(error.message, 'error'); });
    });
    actions.append(accept);

    var rejectBtn = document.createElement('button');
    rejectBtn.type = 'button';
    rejectBtn.className = 'btn btn-outline btn-sm';
    rejectBtn.innerHTML = '<i class="bi bi-x-lg"></i> Reject';
    rejectBtn.addEventListener('click', function (event) {
      event.stopPropagation();
      rejectFinding(finding).catch(function (error) { toast(error.message, 'error'); });
    });
    actions.append(rejectBtn);

    card.append(actions);
    return card;
  }

  async function acceptFinding(finding) {
    if (state.acceptedIds.has(finding.id)) return;
    // auto-create bundle on first accept
    if (!state.bundleId) {
      var res = await window.MIFP.request(config.bundlesUrl, { method: 'POST', json: {} });
      state.bundleId = Number(res.data.bundle_id);
      // enable phase 3
      id('dqPhase3').classList.remove('dq-phase-disabled');
      id('dqApplyAll').disabled = false;
      id('dqClearQueue').disabled = false;
    }
    await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId) + '/items', {
      method: 'POST',
      json: { finding_id: finding.id, strategy: 'best_quality', plan: finding.plan },
    });
    state.acceptedIds.add(finding.id);
    // update card appearance
    var card = document.querySelector('.dq-finding-card[data-finding-id="' + finding.id + '"]');
    if (card) card.classList.add('is-accepted');
    await renderQueue();
    toast('Finding accepted.', 'success');
  }

  async function rejectFinding(finding) {
    if (!state.acceptedIds.has(finding.id)) {
      await window.MIFP.request(endpoint(config.decisionUrl, finding.id), {
        method: 'POST',
        json: { decision: 'reject' },
      });
    }
    // remove from card
    var card = document.querySelector('.dq-finding-card[data-finding-id="' + finding.id + '"]');
    if (card) {
      card.classList.add('is-rejected');
      card.querySelectorAll('.btn').forEach(function (b) { b.disabled = true; });
    }
    toast('Finding rejected.', 'success');
  }

  async function acceptAllVisible() {
    var cards = document.querySelectorAll('.dq-finding-card:not(.is-accepted):not(.is-rejected)');
    var errors = 0;
    for (var i = 0; i < cards.length; i++) {
      var fid = Number(cards[i].dataset.findingId);
      var finding = state.findings.find(function (f) { return Number(f.id) === fid; });
      if (!finding) continue;
      try { await acceptFinding(finding); } catch (e) { errors++; }
    }
    if (errors) toast(errors + ' findings could not be accepted.', 'warning');
    else toast('All visible findings accepted.', 'success');
  }

  async function rejectAllVisible() {
    var cards = document.querySelectorAll('.dq-finding-card:not(.is-accepted):not(.is-rejected)');
    var errors = 0;
    for (var i = 0; i < cards.length; i++) {
      var fid = Number(cards[i].dataset.findingId);
      var finding = state.findings.find(function (f) { return Number(f.id) === fid; });
      if (!finding) continue;
      try { await rejectFinding(finding); } catch (e) { errors++; }
    }
    if (errors) toast(errors + ' findings could not be rejected.', 'warning');
    else toast('All visible findings rejected.', 'success');
  }

  // ---- Phase 3: Action ----

  async function renderQueue() {
    if (!state.bundleId) {
      id('dqQueueItems').innerHTML = '<div class="dq-empty-state"><i class="bi bi-box-seam"></i><b>Queue is empty</b></div>';
      id('dqQueueCount').textContent = '0 accepted';
      return;
    }
    var response = await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId));
    var items = response.data.bundle.items || [];
    id('dqQueueCount').textContent = items.length + ' accepted';
    if (!items.length) {
      id('dqQueueItems').innerHTML = '<div class="dq-empty-state"><i class="bi bi-box-seam"></i><b>Queue is empty</b><p>Accept findings in the Review phase to build your queue.</p></div>';
      return;
    }
    var html = '';
    items.forEach(function (item) {
      html += '<div class="dq-queue-item"><span class="dq-queue-action">' + esc(item.action_type) + '</span>';
      html += '<span class="dq-queue-entity">' + esc(item.entity_type) + ' #' + esc(item.record_ids_json) + '</span></div>';
    });
    id('dqQueueItems').innerHTML = html;
  }

  async function applyAll() {
    if (!state.bundleId) return;
    var prog = id('dqApplyProgress');
    prog.hidden = false;
    id('dqApplyStatus').textContent = 'Validating…';
    id('dqApplyAll').disabled = true;
    id('dqClearQueue').disabled = true;
    try {
      // dry-run
      var dry = await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId) + '/dry-run', { method: 'POST', json: {} });
      if (!dry.data.report.valid) {
        toast('Dry-run failed: ' + (dry.data.report.errors || []).join(', '), 'error');
        id('dqApplyStatus').textContent = 'Validation failed';
        id('dqApplyAll').disabled = false;
        id('dqClearQueue').disabled = false;
        prog.hidden = true;
        return;
      }
      id('dqApplyStatus').textContent = 'Applying…';
      // apply
      var apply = await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId) + '/apply', { method: 'POST', json: {}, timeout: 180000 });
      var report = apply.data.report;
      id('dqApplyStatus').textContent = 'Applied ✓';
      prog.hidden = true;
      toast('Bundle applied. Backup: ' + report.backup_path, 'success');
      // show result
      var result = id('dqQueueItems');
      result.innerHTML = '<div class="dq-apply-result"><i class="bi bi-check-circle text-success"></i><b>Applied successfully</b>';
      result.innerHTML += '<p>Backup: <code>' + esc(report.backup_path) + '</code></p>';
      result.innerHTML += '<p>Operations: ' + report.operations + ', aliases: ' + report.aliases + '</p></div>';
      id('dqQueueCount').textContent = '0 accepted';
      id('dqApplyAll').disabled = true;
      id('dqClearQueue').disabled = true;
      state.bundleId = 0;
      // clear accepted state
      state.acceptedIds.clear();
      document.querySelectorAll('.dq-finding-card.is-accepted').forEach(function (c) { c.classList.remove('is-accepted'); });
      // reload findings to refresh statuses
      await loadFindings();
    } catch (error) {
      id('dqApplyStatus').textContent = 'Failed';
      toast(error.message || 'Apply failed.', 'error');
      id('dqApplyAll').disabled = false;
      id('dqClearQueue').disabled = false;
      prog.hidden = true;
    }
  }

  async function clearQueue() {
    if (!state.bundleId) return;
    try {
      await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId), { method: 'DELETE', json: {} });
      state.bundleId = 0;
      state.acceptedIds.clear();
      document.querySelectorAll('.dq-finding-card.is-accepted').forEach(function (c) { c.classList.remove('is-accepted'); });
      await renderQueue();
      id('dqApplyAll').disabled = true;
      id('dqClearQueue').disabled = true;
      id('dqPhase3').classList.add('dq-phase-disabled');
      toast('Queue cleared.', 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

  // ---- Event wiring ----

  id('dqAnalyze').addEventListener('click', analyze);
  id('dqFilters').addEventListener('submit', function (event) { event.preventDefault(); state.offset = 0; loadFindings().catch(function (e) { toast(e.message, 'error'); }); });
  id('dqLoadMore').addEventListener('click', function () { loadFindings(true).catch(function (e) { toast(e.message, 'error'); }); });
  id('dqAcceptAll').addEventListener('click', function () { acceptAllVisible().catch(function (e) { toast(e.message, 'error'); }); });
  id('dqRejectAll').addEventListener('click', function () { rejectAllVisible().catch(function (e) { toast(e.message, 'error'); }); });
  id('dqApplyAll').addEventListener('click', function () { id('dqApplyConfirm').checked = false; id('dqApplyNow').disabled = true; new bootstrap.Modal(id('dqApplyModal')).show(); });
  id('dqApplyConfirm').addEventListener('change', function (event) { id('dqApplyNow').disabled = !event.target.checked; });
  id('dqApplyNow').addEventListener('click', applyAll);
  id('dqClearQueue').addEventListener('click', function () { clearQueue().catch(function (e) { toast(e.message, 'error'); }); });

  // summary card click → filter
  document.addEventListener('click', function (event) {
    var card = event.target.closest('.dq-summary-card');
    if (card) {
      var filter = card.dataset.actionFilter;
      if (filter && id('dqFilters').elements.action_type) {
        id('dqFilters').elements.action_type.value = filter;
        id('dqFilters').dispatchEvent(new Event('submit'));
      }
    }
  });

  // ---- Initial load ----
  if (state.runId) { loadFindings().catch(function (e) { toast(e.message, 'error'); }); }
  if (state.bundleId) { renderQueue().catch(function (e) { toast(e.message, 'error'); }); }
})();
```

- [ ] **Step 2: Verify no JS syntax errors**

```bash
node -e "
var fs = require('fs');
var code = fs.readFileSync('MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js', 'utf-8');
try { new Function(code); console.log('JS syntax OK'); }
catch(e) { console.error('Syntax error:', e.message); }
"
```
Expected: "JS syntax OK"

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest TESTS/webapp/ -q --tb=short
```
Expected: 210 passed, 6 skipped


### Task 3: Add CSS phase classes to `dashboard.css`

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/static/css/dashboard.css` (append new classes)

- [ ] **Step 1: Append CSS for 3-phase layout**

Add at end of `dashboard.css`:

```css
/* ---- Data Quality 3-Phase ---- */
.dq-app { display: flex; flex-direction: column; gap: .5rem; }
.dq-phase { border: 1px solid var(--border-soft); border-radius: var(--radius-md); background: var(--surface); overflow: hidden; }
.dq-phase.dq-phase-disabled { opacity: .5; pointer-events: none; }
.dq-phase-header { display: flex; align-items: center; gap: .65rem; padding: .7rem .85rem; background: var(--surface-2); border-bottom: 1px solid var(--border-soft); }
.dq-phase-num { display: grid; place-items: center; width: 1.6rem; height: 1.6rem; border-radius: 99px; background: var(--accent); color: #fff; font-size: .7rem; font-weight: 800; flex-shrink: 0; }
.dq-phase-header div { min-width: 0; }
.dq-phase-header h2 { margin: 0; font-size: .78rem; }
.dq-phase-header p { margin: .1rem 0 0; color: var(--text-3); font-size: .62rem; }
.dq-phase-divider { margin: 0 2rem; border-color: var(--border-soft); }
.dq-empty-phase { display: grid; place-items: center; gap: .25rem; padding: 1.5rem; color: var(--text-3); text-align: center; font-size: .65rem; }
.dq-empty-phase i { font-size: 1.5rem; color: var(--accent); }
.dq-empty-phase b { color: var(--text-bright); font-size: .72rem; }

/* progress */
.dq-progress { display: grid; grid-template-columns: 1fr auto minmax(8rem, auto) auto; align-items: center; gap: .65rem; padding: .6rem .85rem; background: var(--surface-2); }
.dq-progress-track { height: 4px; background: var(--border-soft); border-radius: 99px; overflow: hidden; }
.dq-progress-track span { display: block; height: 100%; background: var(--accent); width: 0; transition: width .3s; }
.dq-progress b { font-size: .62rem; }
.dq-progress > span, .dq-progress time { color: var(--text-3); font-size: .62rem; }
.dq-progress time { font-family: var(--font-mono); }

/* summary cards */
.dq-summary-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: .5rem; padding: .7rem; }
.dq-summary-card { display: flex; align-items: center; gap: .5rem; padding: .6rem .7rem; border: 1px solid var(--border-soft); border-radius: var(--radius-sm); background: var(--surface); cursor: pointer; text-align: left; color: inherit; transition: border-color .15s, background .15s; }
.dq-summary-card:hover { border-color: var(--accent); background: var(--surface-2); }
.dq-summary-card i { font-size: 1.1rem; color: var(--accent); flex-shrink: 0; }
.dq-summary-card b { font-size: .85rem; color: var(--text-bright); }
.dq-summary-card span { display: block; font-size: .6rem; color: var(--text-3); }

/* toolbar */
.dq-toolbar { display: flex; flex-wrap: wrap; gap: .4rem; align-items: end; padding: .55rem .7rem; background: var(--surface-2); border-bottom: 1px solid var(--border-soft); }
.dq-toolbar label { display: flex; flex-direction: column; gap: .15rem; font-size: .6rem; color: var(--text-3); }
.dq-toolbar select { font-size: .62rem; }

/* bulk bar */
.dq-bulk-bar { display: flex; align-items: center; gap: .35rem; padding: .4rem .7rem; border-bottom: 1px solid var(--border-soft); background: var(--surface); }
.dq-bulk-bar > span { margin-right: auto; font-size: .62rem; color: var(--text-2); }
.dq-bulk-bar .btn { font-size: .6rem; padding: .22rem .45rem; }

/* finding list */
.dq-finding-list { display: grid; gap: .3rem; padding: .5rem .7rem; }
.dq-finding-list .dq-empty-state { display: grid; place-items: center; gap: .2rem; padding: 1.5rem; color: var(--text-3); text-align: center; font-size: .65rem; }
.dq-finding-list .dq-empty-state i { font-size: 1.25rem; color: var(--accent); }
.dq-finding-list .dq-empty-state b { color: var(--text-bright); font-size: .72rem; }

.dq-finding-card { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: .3rem .5rem; padding: .5rem .65rem; border: 1px solid var(--border-soft); border-radius: var(--radius-sm); background: var(--surface); transition: border-color .15s, opacity .15s; }
.dq-finding-card.is-accepted { border-color: var(--success); background: var(--green-bg); }
.dq-finding-card.is-rejected { opacity: .45; }
.dq-finding-evidence { grid-column: 1 / -1; font-size: .7rem; color: var(--text-bright); }
.dq-finding-solution { grid-column: 1 / -1; font-size: .62rem; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dq-finding-meta { display: flex; gap: .25rem; align-items: center; }
.dq-badge-action, .dq-badge-entity, .dq-badge-class { padding: .08rem .3rem; border-radius: 99px; font-size: .55rem; font-weight: 700; }
.dq-badge-action { color: var(--text-2); background: var(--surface-3); }
.dq-badge-entity { color: var(--accent); background: var(--blue-bg); }
.dq-badge-class { color: var(--text-2); background: var(--surface-3); }
.dq-finding-actions { display: flex; gap: .2rem; align-items: center; }
.dq-finding-actions .btn { font-size: .58rem; padding: .15rem .4rem; }

.dq-load-more { padding: .4rem .7rem .6rem; text-align: center; }

/* queue */
.dq-queue-summary { display: flex; align-items: center; gap: .4rem; padding: .5rem .7rem; border-bottom: 1px solid var(--border-soft); background: var(--surface-2); }
.dq-queue-summary > span { margin-right: auto; font-size: .62rem; color: var(--text-2); }
.dq-queue-summary .btn { font-size: .6rem; padding: .22rem .45rem; }
.dq-queue-list { display: grid; gap: .2rem; padding: .5rem .7rem; }
.dq-queue-item { display: flex; gap: .5rem; align-items: center; padding: .35rem .5rem; border: 1px solid var(--border-soft); border-radius: var(--radius-sm); font-size: .64rem; }
.dq-queue-action { font-weight: 700; color: var(--text-bright); }
.dq-queue-entity { color: var(--text-2); }

.dq-apply-progress { display: flex; align-items: center; gap: .5rem; padding: .5rem .7rem; }
.dq-apply-progress .dq-progress-track { flex: 1; }
.dq-apply-progress b { font-size: .62rem; }

.dq-apply-result { display: grid; place-items: center; gap: .2rem; padding: .8rem; text-align: center; font-size: .64rem; }
.dq-apply-result i { font-size: 1.3rem; }
.dq-apply-result b { font-size: .72rem; }
.dq-apply-result code { font-size: .6rem; }

.dq-history { padding: .5rem .7rem .7rem; }
.dq-history h3 { margin: 0 0 .3rem; font-size: .65rem; color: var(--text-3); }
.dq-history-item { display: flex; align-items: center; gap: .35rem; padding: .25rem .4rem; font-size: .6rem; }
.dq-history-item code { font-size: .58rem; }

@media (max-width: 640px) {
  .dq-summary-cards { grid-template-columns: repeat(2, 1fr); }
  .dq-finding-card { grid-template-columns: 1fr; }
  .dq-finding-actions { grid-column: 1; }
}
```

- [ ] **Step 2: Verify CSS doesn't break existing styles**

```bash
python3 -m pytest TESTS/webapp/ -q --tb=short
```
Expected: 210 passed, 6 skipped


### Task 4: Update executor to handle best-quality rejection cleanly

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/executor.py` (lines 22-51, `add_to_bundle`)

**Problem:** The current `add_to_bundle` at line 29 rejects findings with `classification in {"blocked", "related_not_duplicate", "keep_separate"}`. But in Phase 2, the user may try to accept a finding that was already rejected (classified as `keep_separate`). This is fine — the error message just needs to be clear. No code change needed.

**But:** The `add_to_bundle` at line 36-37 rejects best_quality strategy for `split_aggregated_record` and `repair_relations_or_assets`. This is correct — those types don't support field-level best-quality selection. However, in Phase 2 we're always passing `strategy: 'best_quality'` to `add_to_bundle`. This will fail for split and repair findings.

**Fix:** Only pass `strategy: 'best_quality'` for action types that support it (`merge_records`, `enrich_record`, `clean_record`). For other types, pass the plan directly without strategy.

No code change needed in the Python backend — this is handled in JS (Task 2 already passes `plan: finding.plan` alongside `strategy: 'best_quality'`, and the executor will fall back to the provided plan if strategy fails). Wait, actually looking at `executor.py:36`:

```python
if finding["action_type"] not in {"merge_records", "enrich_record", "clean_record"}:
    raise ValueError("best-quality selection is available for merge, enrichment and cleanup actions")
```

So if we always send `strategy: 'best_quality'` for split/repair findings, it will error. The JS needs to conditionally send strategy. Let me update the JS `acceptFinding` function:

- [ ] **Step 1: Update `acceptFinding` in JS to conditionally use best_quality strategy**

The fix is in `data-quality.js` `acceptFinding` — only use `strategy: 'best_quality'` for action types that support it:

Current code:
```javascript
await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId) + '/items', {
  method: 'POST',
  json: { finding_id: finding.id, strategy: 'best_quality', plan: finding.plan },
});
```

Replace with:
```javascript
var supportsBest = ['merge_records', 'enrich_record', 'clean_record'].indexOf(finding.action_type) !== -1;
var payload = { finding_id: finding.id, plan: finding.plan };
if (supportsBest) payload.strategy = 'best_quality';
await window.MIFP.request(endpoint(config.bundleUrl, state.bundleId) + '/items', {
  method: 'POST',
  json: payload,
});
```

- [ ] **Step 2: Run tests**

```bash
python3 -m pytest TESTS/webapp/ -q --tb=short
```
Expected: 210 passed, 6 skipped

### Task 5: Final verification

- [ ] **Step 1: Full test suite**

```bash
python3 -m pytest TESTS/webapp/ -v --tb=short
```
Expected: 210 passed, 6 skipped

- [ ] **Step 2: Verify template renders without error**

```bash
python3 -c "
from pathlib import Path; import sys; sys.path.insert(0, 'WEBAPP')
from mifp_app import create_app
app = create_app({'TESTING': True, 'DATABASE_PATH': 'MIFPAPP/DATABASE/mifp.db', 'SECRET_KEY': 'test', 'ASSETS_DIR': 'MIFPAPP/CORE/static/assets'})
client = app.test_client()
# login
with client.session_transaction() as s: s['admin_logged_in'] = True; s['admin_username'] = 'test'
resp = client.get('/dashboard/data-quality')
print('Status:', resp.status_code)
print('Phase 1 present:', b'dqPhase1' in resp.data)
print('Phase 2 present:', b'dqPhase2' in resp.data)
print('Phase 3 present:', b'dqPhase3' in resp.data)
print('Config present:', b'dqConfig' in resp.data)
"
```
Expected: Status 200, all 3 phases present, config present

- [ ] **Step 3: Bump version and commit**

```bash
git add MIFPAPP/CORE/mifp_app/templates/dashboard/data_quality.html \
        MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js \
        MIFPAPP/CORE/mifp_app/static/css/dashboard.css \
        docs/superpowers/specs/2026-07-26-data-quality-3-phase-redesign.md \
        docs/superpowers/plans/2026-07-26-data-quality-3-phase-redesign.md
git commit -m "feat: data quality 3-phase redesign (Analyze → Review → Action)"
```

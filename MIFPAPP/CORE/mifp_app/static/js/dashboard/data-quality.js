(function () {
  'use strict';

  var cfgEl = document.getElementById('dqConfig');
  if (!cfgEl) return;
  var C = JSON.parse(cfgEl.textContent);

  var $ = function (id) { return document.getElementById(id); };

  var S = {
    runId: Number(C.runId || 0),
    bundleId: Number(C.bundleId || 0),
    offset: 0,
    _pollId: null,
    findingsRequest: 0,
    findingsController: null,
    queueRequest: 0,
  };

  function toast(msg, t) {
    if (window.MIFPUI && window.MIFPUI.showToast) window.MIFPUI.showToast(msg, t || 'info');
  }

  function u(base, id) { return base.replace('/0', '/' + id); }

  function reloadFindings() { return loadFindings(false); }

  async function loadFindings(append) {
    if (!S.runId) return;
    var f = $('dqFilters');
    var list = $('dqFindings');
    if (!(f instanceof HTMLFormElement) || !list) {
      throw new Error('The scan workspace is not ready. Reload the page and try again.');
    }
    if (S.findingsController) S.findingsController.abort();
    var controller = new AbortController();
    S.findingsController = controller;
    var requestId = ++S.findingsRequest;
    var requestedOffset = append ? S.offset : 0;
    var p = new URLSearchParams(new FormData(f));
    p.set('run_id', S.runId);
    p.set('offset', String(requestedOffset));
    f.setAttribute('aria-busy', 'true');
    if ($('dqFilterBtn')) $('dqFilterBtn').disabled = true;
    if ($('dqLoadMore')) $('dqLoadMore').disabled = true;
    try {
      var r = await window.MIFP.request(C.findingsUrl + '?' + p.toString(), {
        signal: controller.signal
      });
      if (requestId !== S.findingsRequest || Number(r.data.run_id) !== S.runId) return null;
      var d = r.data;
      S.offset = requestedOffset + d.items.length;
      if (!append) list.innerHTML = d.items_html;
      else list.insertAdjacentHTML('beforeend', d.items_html);
      if ($('dqFilteredCount')) $('dqFilteredCount').textContent = String(d.total);
      $('dqLoadMore').hidden = d.items.length < 30 || S.offset >= d.total;
      var bulk = $('dqAcceptAll');
      if (bulk) {
        var workflow = String((f.elements.classification && f.elements.classification.value) || 'automatic');
        var safeAutomatic = workflow === 'automatic';
        bulk.disabled = !safeAutomatic || Number(d.total || 0) === 0;
        bulk.textContent = safeAutomatic ? 'Queue all automatic fixes' : 'Automatic bulk queue is available only in the Automatic fixes view';
        bulk.title = safeAutomatic ? 'Queue every currently filtered deterministic/high-confidence fix' : 'Switch to Automatic fixes, or review the items that need a decision';
      }
      return d;
    } catch (error) {
      if (error && error.name === 'AbortError') return null;
      throw error;
    } finally {
      if (requestId === S.findingsRequest) {
        S.findingsController = null;
        f.removeAttribute('aria-busy');
        if ($('dqFilterBtn')) $('dqFilterBtn').disabled = false;
        if ($('dqLoadMore')) $('dqLoadMore').disabled = false;
      }
    }
  }

  async function renderQueue() {
    var requestId = ++S.queueRequest;
    var r = await window.MIFP.request(C.stateUrl);
    if (requestId !== S.queueRequest) return null;
    var d = r.data;
    S.bundleId = Number(d.bundle_id || 0);
    if (d.run_id) S.runId = Number(d.run_id);
    $('dqQueueStats').innerHTML = d.queue_html || '';
    $('dqApplyAll').disabled = !d.can_apply;
    $('dqClearQueue').disabled = !d.can_apply;
    // loadFindings owns bulk-action enablement because it knows the active workflow filter.
    return d;
  }

  async function ensureEditableBundle() {
    if (S.bundleId) return S.bundleId;
    var created = await window.MIFP.request(C.bundlesUrl, {
      method: 'POST',
      json: {}
    });
    S.bundleId = Number(created.data.bundle_id || 0);
    if (!S.bundleId) throw new Error('The review queue could not be created.');
    return S.bundleId;
  }

  async function openFindingDetail(findingId) {
    var body = $('dqDetailBody');
    body.innerHTML = '<div class="dq-detail-placeholder">Loading…</div>';
    bootstrap.Modal.getOrCreateInstance($('dqDetailModal')).show();
    try {
      var response = await window.MIFP.request(u(C.findingUrl, findingId));
      body.innerHTML = response.data.detail_html || '<div class="alert alert-danger">Failed to load detail.</div>';
    } catch (error) {
      var alert = document.createElement('div');
      alert.className = 'alert alert-danger';
      alert.textContent = error && error.message ? error.message : 'Error';
      body.textContent = '';
      body.appendChild(alert);
    }
  }

  async function queueManualPlan(form) {
    var findingId = Number(form.dataset.findingId || 0);
    if (!findingId) throw new Error('Invalid finding.');
    var plan = {};
    var canonical = form.querySelector('input[name="canonical_id"]:checked');
    if (form.querySelector('input[name="canonical_id"]') && !canonical) {
      throw new Error('Choose the canonical record to keep.');
    }
    if (canonical) plan.canonical_id = Number(canonical.value);
    var fields = Array.from(form.querySelectorAll('[data-plan-field]'));
    if (fields.length) {
      plan.fields = fields.map(function (input) {
        return { field: input.dataset.planField, proposed_value: input.value };
      });
    }
    var splitInputs = Array.from(form.querySelectorAll('[data-split-index]'));
    if (splitInputs.length) {
      plan.proposed_records = splitInputs.map(function (input) {
        return { title: input.value.trim() };
      });
      if (plan.proposed_records.some(function (item) { return !item.title; })) {
        throw new Error('Every split record requires a title.');
      }
    }
    var submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    form.setAttribute('aria-busy', 'true');
    try {
      var bundleId = await ensureEditableBundle();
      await window.MIFP.request(u(C.bundleUrl, bundleId) + '/items', {
        method: 'POST',
        json: { finding_id: findingId, plan: plan }
      });
      bootstrap.Modal.getOrCreateInstance($('dqDetailModal')).hide();
      await Promise.all([reloadFindings(), renderQueue()]);
      toast('Reviewed change added to the queue.', 'success');
    } finally {
      form.removeAttribute('aria-busy');
      if (submit) submit.disabled = false;
    }
  }

  async function acceptFinding(findingId) {
    var response = await window.MIFP.request(u(C.decisionUrl, findingId), { method: 'POST', json: { decision: 'accept' } });
    if (response.data && response.data.reviewed_without_change) {
      toast('Review completed. No database change was required.', 'success');
      await Promise.all([reloadFindings(), renderQueue()]);
      return;
    }
    toast('Finding accepted.', 'success');
    await renderQueue();
    await reloadFindings();
  }

  async function ignoreFinding(findingId) {
    await window.MIFP.request(u(C.decisionUrl, findingId), { method: 'POST', json: { decision: 'reject' } });
    toast('Finding ignored.', 'success');
    await Promise.all([reloadFindings(), renderQueue()]);
  }

  async function acceptAll() {
    if (!S.runId) { toast('Run a scan first.', 'warning'); return; }
    var filtersForm = $('dqFilters');
    if (!(filtersForm instanceof HTMLFormElement)) { toast('The filters are not available. Reload the page.', 'error'); return; }
    var filters = Object.fromEntries(new FormData(filtersForm).entries());
    // Bulk acceptance is intentionally restricted to the server-side automatic workflow.
    // This explicit marker also protects against stale browser code and ambiguous pseudo-filters.
    filters.classification = 'automatic';
    var btn = $('dqAcceptAll');
    if (btn) btn.disabled = true;
    if (window.MIFPLog && window.MIFPLog.info) {
      window.MIFPLog.info('data-quality.bulk-accept.started', { runId: S.runId, filters: filters });
    }
    try {
      var r = await window.MIFP.request(C.bulkDecisionUrl, {
        method: 'POST',
        json: { decision: 'accept', workflow: 'automatic', run_id: S.runId, filters: filters }
      });
      if (r.data.ok) {
        if (r.data.result.bundle_id) S.bundleId = Number(r.data.result.bundle_id);
        var skippedReview = Number(r.data.result.skipped_review || 0);
        var failed = Number(r.data.result.failed || 0);
        var applied = Number(r.data.result.applied || 0);
        var matched = Number(r.data.result.matched || 0);
        var summary;
        var tone = 'success';
        if (!matched) {
          summary = 'No automatic fixes match the current filters';
          tone = 'info';
        } else {
          summary = applied + ' automatic fixes queued';
          if (skippedReview) {
            summary += ', ' + skippedReview + ' need a decision';
            tone = 'warning';
          }
          if (failed) {
            summary += ', ' + failed + ' failed';
            tone = 'warning';
          }
        }
        toast(summary + '.', tone);
        if (window.MIFPLog && window.MIFPLog.info) {
          window.MIFPLog.info('data-quality.bulk-accept.finished', r.data.result);
        }
        await renderQueue();
        await reloadFindings();
      }
    } catch (e) {
      if (window.MIFPLog && window.MIFPLog.error) window.MIFPLog.error('data-quality.bulk-accept.failed', { message: e.message });
      toast(e.message, 'error');
    }
    if (btn) btn.disabled = false;
  }

  async function pollProgress(runId, fill, txt) {
    if (S._pollId === null) return null;
    try {
      var r = await window.MIFP.request(C.progressUrl);
      var p = r.data;
      if (p.status === 'completed' || p.status === 'failed') { S._pollId = null; return p; }
      if (p.run_id === runId && p.pct > 0) { fill.style.width = Math.min(p.pct, 99) + '%'; if (p.message) txt.textContent = p.message; }
    } catch (e) { /* ignore */ }
    return null;
  }

  function waitForCompletion(runId, fill, txt, resolve) {
    if (S._pollId === null) { resolve(null); return; }
    pollProgress(runId, fill, txt).then(function (r) { if (r) { resolve(r); return; } setTimeout(function () { waitForCompletion(runId, fill, txt, resolve); }, 1500); });
  }

  async function analyze() {
    var btn = $('dqAnalyze');
    btn.disabled = true;
    var prog = $('dqProgress');
    prog.hidden = false;
    var fill = $('dqProgressFill');
    var pct = $('dqProgressPercent');
    var txt = $('dqProgressText');
    var time = $('dqProgressTime');
    var started = Date.now();
    fill.style.width = '0%';
    fill.classList.add('is-active');
    pct.textContent = 'Starting';
    txt.textContent = 'Scanning…';
    var timer = setInterval(function () { time.textContent = Math.floor((Date.now() - started) / 1000) + ' s'; }, 250);
    try {
      var resp = await window.MIFP.request(C.analyzeUrl, { method: 'POST', json: {}, timeout: 10000 });
      var runId = Number(resp.data.run_id);
      if (!runId) throw new Error('No run ID.');
      S.runId = runId;
      pct.textContent = 'Working';
      S._pollId = runId;
      var finalStatus = await new Promise(function (resolve) { waitForCompletion(runId, fill, txt, resolve); });
      S._pollId = null;
      clearInterval(timer);
      time.textContent = Math.floor((Date.now() - started) / 1000) + ' s';
      if (finalStatus && finalStatus.status === 'completed') {
        pct.textContent = 'Done';
        txt.textContent = 'Loading results…';
        fill.style.width = '100%';
        fill.classList.remove('is-active');

        // On the first scan Flask must render the complete review workspace.
        // Scanning is read-only: queue mutations always require an explicit
        // Accept or Accept all action from the administrator.
        if (!($('dqFilters') instanceof HTMLFormElement) || !$('dqFindings')) {
          window.location.reload();
          return;
        }

        $('dqPhase2').classList.remove('dq-phase-disabled');
        await reloadFindings();
        await renderQueue();
        $('dqPhase2').scrollIntoView({ behavior: 'smooth', block: 'start' });
        toast('Scan complete: no records changed yet. Queue the automatic fixes, then Apply changes to consolidate duplicates.', 'success');
      } else {
        txt.textContent = 'Scan failed on server.';
        fill.classList.remove('is-active');
        toast('Scan failed.', 'error');
      }
    } catch (error) {
      S._pollId = null;
      clearInterval(timer);
      pct.textContent = 'Failed';
      txt.textContent = error.message || 'Error';
      fill.classList.remove('is-active');
      toast(error.message || 'Scan failed.', 'error');
    }
    btn.disabled = false;
  }

  async function applyAll() {
    if (!S.bundleId) { toast('Nothing to apply.', 'warning'); return; }
    $('dqApplyAll').disabled = true;
    $('dqClearQueue').disabled = true;
    $('dqApplyClose').disabled = true;
    $('dqApplyResult').hidden = true;
    $('dqApplyProgress').hidden = false;
    $('dqApplyFill').style.width = '0%';
    $('dqApplyFill').classList.add('is-active');
    $('dqApplyStatus').textContent = 'Applying…';
    var modal = new bootstrap.Modal($('dqApplyModal'));
    modal.show();
    try {
      $('dqApplyFill').style.width = '30%';
      var apply = await window.MIFP.request(u(C.bundleUrl, S.bundleId) + '/apply', { method: 'POST', json: {}, timeout: 180000 });
      var report = apply.data.report;
      var noChanges = report.status === 'no_changes';
      $('dqApplyFill').classList.remove('is-active');
      $('dqApplyProgress').hidden = true;
      $('dqApplyResult').hidden = false;
      $('dqApplyResultTitle').textContent = noChanges ? 'No current changes to apply' : 'Applied successfully';
      $('dqApplyResultDetail').innerHTML = noChanges
        ? 'Queued findings were based on older source data and were safely discarded. Run a new scan.'
        : 'Backup: <code>' + (report.backup_path || '') + '</code> &middot; ' + report.operations + ' ops, ' + report.aliases + ' aliases';
      $('dqApplyClose').disabled = false;
      toast(noChanges ? 'No changes applied. Run a new scan.' : 'Applied. Backup: ' + report.backup_path, noChanges ? 'warning' : 'success');
      S.bundleId = 0;
      S.runId = 0;
      S.offset = 0;
      $('dqPhase2').classList.add('dq-phase-disabled');
      var sc = $('dqSummaryCards');
      if (sc) sc.remove();
      await renderQueue();
      $('dqFindings').innerHTML = '<div class="dq-empty-list"><i class="bi bi-check2-circle"></i><b>Changes applied</b><p>All queued changes have been applied to the database.</p></div>';
      $('dqLoadMore').hidden = true;
      $('dqApplyClose').onclick = function () { window.location.reload(); };
    } catch (error) {
      $('dqApplyFill').classList.remove('is-active');
      var applyMessage = error.message || 'Apply failed.';
      $('dqApplyStatus').textContent = applyMessage;
      $('dqApplyProgress').hidden = true;
      $('dqApplyResult').hidden = false;
      $('dqApplyResultTitle').textContent = error.status === 409
        ? 'Bundle needs a new review'
        : 'Apply failed';
      $('dqApplyResultDetail').textContent = applyMessage;
      $('dqApplyClose').disabled = false;
      $('dqApplyAll').disabled = false;
      $('dqClearQueue').disabled = false;
      toast(applyMessage, error.status === 409 ? 'warning' : 'error');
    }
  }

  async function clearQueue() {
    if (!S.bundleId) return;
    try {
      await window.MIFP.request(u(C.bundleUrl, S.bundleId), { method: 'DELETE', json: {} });
      S.bundleId = 0;
      await renderQueue();
      await reloadFindings();
      toast('Queue cleared.', 'success');
    } catch (e) { toast(e.message, 'error'); }
  }

  // ---- Event wiring (delegated) ----
  document.addEventListener('click', function (e) {
    var manual = e.target.closest('.btn-dq-manual');
    if (manual) {
      e.preventDefault();
      e.stopPropagation();
      openFindingDetail(Number(manual.dataset.findingId)).catch(function (err) {
        toast(err.message, 'error');
      });
      return;
    }
    var target = e.target.closest('[data-finding-id]');
    if (target) {
      var fid = Number(target.dataset.findingId);
      if (target.classList.contains('btn-dq-accept')) { e.stopPropagation(); acceptFinding(fid).catch(function (err) { toast(err.message, 'error'); }); return; }
      if (target.classList.contains('btn-dq-ignore')) { e.stopPropagation(); ignoreFinding(fid).catch(function (err) { toast(err.message, 'error'); }); return; }
    }
    var sc = e.target.closest('[data-workflow-filter], .dq-summary-card');
    if (sc && $('dqFilters')) {
      if (sc.dataset.actionFilter !== undefined && $('dqFilters').elements.action_type) {
        $('dqFilters').elements.action_type.value = sc.dataset.actionFilter || '';
      }
      if (sc.dataset.workflowFilter !== undefined && $('dqFilters').elements.classification) {
        $('dqFilters').elements.classification.value = sc.dataset.workflowFilter || 'automatic';
      }
      reloadFindings().catch(function (err) { toast(err.message, 'error'); });
      return;
    }
    // finding card click → detail modal
    var card = e.target.closest('.dq-finding-card');
    if (card && !e.target.closest('button')) {
      var findingId = Number(card.dataset.findingId);
      if (findingId) openFindingDetail(findingId);
    }
  });

  document.addEventListener('submit', function (e) {
    if (!e.target.matches('#dqManualEditor')) return;
    e.preventDefault();
    queueManualPlan(e.target).catch(function (error) {
      toast(error.message || 'The reviewed change could not be queued.', 'error');
    });
  });

  // ---- Direct event wiring ----
  $('dqAnalyze') && ($('dqAnalyze').onclick = function () { analyze().catch(function (e) { toast(e.message, 'error'); }); });
  $('dqFilterBtn') && ($('dqFilterBtn').onclick = function () { reloadFindings().catch(function (e) { toast(e.message, 'error'); }); });
  $('dqLoadMore') && ($('dqLoadMore').onclick = function () { loadFindings(true).catch(function (e) { toast(e.message, 'error'); }); });
  $('dqApplyAll') && ($('dqApplyAll').onclick = function () { applyAll().catch(function (e) { toast(e.message, 'error'); }); });
  $('dqClearQueue') && ($('dqClearQueue').onclick = function () { clearQueue().catch(function (e) { toast(e.message, 'error'); }); });
  $('dqAcceptAll') && ($('dqAcceptAll').onclick = function () { acceptAll().catch(function (e) { toast(e.message, 'error'); }); });

  // ---- Initial load ----
  if (S.runId) { reloadFindings().catch(function (e) { toast(e.message, 'error'); }); }
  if (S.bundleId) { renderQueue().catch(function (e) { toast(e.message, 'error'); }); }
})();

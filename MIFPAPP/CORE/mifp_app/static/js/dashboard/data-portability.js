/* Data portability progress enhancement */
(function () {
  'use strict';
  var clearFormLoading = window.MIFPUI.clearFormLoading;
  var transferLog = window.MIFPLog || {
    debug: function(){}, info: function(){}, warn: function(){}, error: function(){}
  };

  var modalElement = document.getElementById('transferModal');
  var importAuthElement = document.getElementById('importAuthModal');
  var exportAuthElement = document.getElementById('exportAuthModal');
  var configElement = document.getElementById('dataPortabilityConfig');
  var config = {};
  try { config = JSON.parse(configElement?.textContent || '{}'); } catch (_) { config = {}; }
  var resultMessage = document.getElementById('transferResultMessage');

  /* ── Helpers ─────────────────────────────────────────────── */

  function tableLabel(table) {
    return ({ events: 'Events', news: 'News', members: 'Members', publications: 'Publications', research_areas: 'Research areas', sponsors: 'Sponsors', pages: 'Pages' })[table] || table;
  }

  function icon(className) {
    var item = document.createElement('i');
    item.className = 'bi ' + className;
    item.setAttribute('aria-hidden', 'true');
    return item;
  }

  /* ── Transfer-only setup ──────────────────────────────────── */


  if (!modalElement || !window.bootstrap) return;

  var modal = new bootstrap.Modal(modalElement, { backdrop: 'static', keyboard: false });
  var importAuthModal = importAuthElement ? new bootstrap.Modal(importAuthElement) : null;
  var exportAuthModal = exportAuthElement ? new bootstrap.Modal(exportAuthElement) : null;
  modalElement.addEventListener('hidden.bs.modal', function () {
    clearFormLoading(form);
    clearInterval(clockTimer);
    if (refreshAfterImport) window.location.reload();
  });
  var form = document.getElementById('transferImportForm');
  var importAuthForm = document.getElementById('importAuthForm');
  var importAuthPassword = document.getElementById('importAuthPassword');
  var importAuthOperation = document.getElementById('importAuthOperation');
  var exportAuthForm = document.getElementById('exportAuthForm');
  var exportAuthPassword = document.getElementById('exportAuthPassword');
  var exportAuthFormat = document.getElementById('exportAuthFormat');
  var fileInput = document.getElementById('transferFiles');
  var selection = document.getElementById('transferSelection');
  var selectionError = document.getElementById('transferSelectionError');
  var batchNotice = document.getElementById('transferBatchNotice');
  var importButton = document.getElementById('transferImportButton');
  var skipAssets = document.getElementById('skipAssetsOption');
  var working = document.getElementById('transferWorking');
  var result = document.getElementById('transferResult');
  var footer = document.getElementById('transferFooter');
  var cancelButton = document.getElementById('transferCancel');
  var downloadButton = document.getElementById('transferDownload');
  var closeButton = document.getElementById('transferClose');
  var progress = document.getElementById('transferProgress');
  var status = document.getElementById('transferStatus');
  var detail = document.getElementById('transferDetail');
  var title = document.getElementById('transferModalTitle');
  var resultMark = document.getElementById('transferResultMark');
  var resultTitle = document.getElementById('transferResultTitle');
  var resultGrid = document.getElementById('transferResultGrid');
  var errors = document.getElementById('transferErrors');
  var percent = document.getElementById('transferPercent');
  var elapsed = document.getElementById('transferElapsed');
  var activityLog = document.getElementById('transferActivityLog');
  var filesContainer = document.getElementById('transferFilesProgress');
  var byTypeContainer = document.getElementById('transferResultByType');
  var byTypeGrid = document.getElementById('transferResultByTypeGrid');
  var metricsRecords = document.getElementById('metricsRecords');
  var metricsRecordsTotal = document.getElementById('metricsRecordsTotal');
  var metricsInserted = document.getElementById('metricsInserted');
  var metricsUpdated = document.getElementById('metricsUpdated');
  var metricsAssets = document.getElementById('metricsAssets');
  var metricsErrors = document.getElementById('metricsErrors');
  var metricsRecordErrors = document.getElementById('metricsRecordErrors');
  var metricsAssetErrors = document.getElementById('metricsAssetErrors');
  var startedAt = 0;
  var clockTimer = null;
  var streamBuffer = '';
  var fileProgressEls = {};
  var exportDlToken = null;
  var exportDlFilename = null;
  var operationActive = false;
  var stagedFiles = [];
  var refreshAfterImport = false;
  var activeImportXhr = null;
  var activeImportJobId = null;
  var activeImportCancelUrl = null;
  var transferHasResult = false;
  var lastStreamError = null;
  var pendingExportFormat = null;
  var pendingImportRequest = null;
  var importBatchContext = null;

  if (importAuthElement) {
    importAuthElement.addEventListener('shown.bs.modal', function () {
      if (importAuthPassword) importAuthPassword.focus();
    });
    importAuthElement.addEventListener('hidden.bs.modal', function () {
      if (importAuthPassword) importAuthPassword.value = '';
      pendingImportRequest = null;
    });
  }

  if (exportAuthElement) {
    exportAuthElement.addEventListener('shown.bs.modal', function () {
      if (exportAuthPassword) exportAuthPassword.focus();
    });
    exportAuthElement.addEventListener('hidden.bs.modal', function () {
      if (exportAuthPassword) exportAuthPassword.value = '';
      pendingExportFormat = null;
    });
  }

  function setOperationActive(active) {
    operationActive = active;
    importButton.disabled = active;
    document.querySelectorAll('.transfer-export-button[data-export]').forEach(function (button) {
      button.disabled = active;
    });
  }

  function sizeLabel(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function selectionProblem(files) {
    var zipFiles = files.filter(function (file) {
      return String(file.name || '').toLowerCase().endsWith('.zip');
    });
    var oversizedZip = zipFiles.find(function (file) {
      return Number(config.maxZipBytes || 0) > 0 && file.size > Number(config.maxZipBytes);
    });
    if (oversizedZip) {
      return oversizedZip.name + ' is ' + sizeLabel(oversizedZip.size) + '; the ZIP limit is ' + sizeLabel(Number(config.maxZipBytes)) + '.';
    }
    var oversizedFile = files.find(function (file) {
      return Number(config.maxUploadBytes || 0) > 0 && file.size > Number(config.maxUploadBytes);
    });
    if (oversizedFile) {
      return oversizedFile.name + ' is ' + sizeLabel(oversizedFile.size) + '; each upload is limited to ' + sizeLabel(Number(config.maxUploadBytes)) + '.';
    }
    return '';
  }

  function buildImportBatches(files) {
    var uploadLimit = Number(config.maxUploadBytes || 0);
    var batches = [];
    var pending = [];
    var pendingBytes = 0;
    function flushPending() {
      if (pending.length) batches.push(pending);
      pending = [];
      pendingBytes = 0;
    }
    files.forEach(function (file) {
      var isZip = String(file.name || '').toLowerCase().endsWith('.zip');
      if (isZip) {
        flushPending();
        batches.push([file]);
        return;
      }
      if (pending.length && uploadLimit > 0 && pendingBytes + file.size > uploadLimit) flushPending();
      pending.push(file);
      pendingBytes += file.size;
    });
    flushPending();
    return batches;
  }

  function batchProgress(localPercent) {
    if (!importBatchContext) return localPercent;
    return ((importBatchContext.index + (Number(localPercent) / 100)) / importBatchContext.total) * 100;
  }

  function acceptImportResult(payload) {
    if (importBatchContext) {
      importBatchContext.result = payload;
      return;
    }
    showResult(payload);
  }

  function showSelectionProblem(message) {
    if (selectionError) {
      selectionError.textContent = message || '';
      selectionError.hidden = !message;
    }
    fileInput.setCustomValidity(message || '');
  }

  function elapsedLabel(milliseconds) {
    var seconds = Math.max(0, Math.floor(milliseconds / 1000));
    return String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
  }

  function setProgress(value) {
    var safe = Math.max(0, Math.min(100, Math.round(value)));
    progress.parentElement.classList.remove('is-loading');
    progress.style.width = safe + '%';
    percent.textContent = safe + '%';
  }

  function logEvent(message, state) {
    var row = document.createElement('div');
    var itemIcon = document.createElement('i');
    var copy = document.createElement('span');
    row.className = 'transfer-activity-entry' + (state ? ' is-' + state : '');
    itemIcon.className = state === 'done' ? 'bi bi-check-circle-fill' : state === 'error' ? 'bi bi-x-circle-fill' : 'bi bi-arrow-right-circle';
    copy.textContent = message;
    row.append(itemIcon, copy);
    activityLog.appendChild(row);
    var entries = activityLog.children;
    while (entries.length > 4) entries[0].remove();
  }

  function setPhase(message, description, value) {
    status.textContent = message;
    detail.textContent = description || '';
    if (value != null) setProgress(value);
    logEvent(message, value === 100 ? 'done' : 'active');
  }

  function resetModal(modalTitle, message) {
    title.textContent = modalTitle;
    status.textContent = message;
    detail.textContent = '';
    activityLog.replaceChildren();
    filesContainer.replaceChildren();
    fileProgressEls = {};
    metricsRecords.textContent = '0';
    metricsRecordsTotal.textContent = '';
    metricsAssets.textContent = '0';
    metricsErrors.textContent = '0';
    byTypeContainer.hidden = true;
    byTypeGrid.replaceChildren();
    streamBuffer = '';
    exportDlToken = null;
    exportDlFilename = null;
    progress.parentElement.classList.add('is-loading');
    progress.style.removeProperty('width');
    percent.textContent = 'Working…';
    startedAt = Date.now();
    elapsed.textContent = '00:00';
    clearInterval(clockTimer);
    clockTimer = window.setInterval(function () { elapsed.textContent = elapsedLabel(Date.now() - startedAt); }, 500);
    working.hidden = false;
    result.hidden = true;
    footer.hidden = false;
    transferHasResult = false;
    lastStreamError = null;
    activeImportJobId = null;
    activeImportCancelUrl = null;
    if (cancelButton) { cancelButton.hidden = false; cancelButton.disabled = false; cancelButton.innerHTML = '<i class="bi bi-x-circle"></i> Cancel'; }
    if (downloadButton) {
      downloadButton.hidden = true;
      downloadButton.removeAttribute('href');
      downloadButton.removeAttribute('download');
      downloadButton.removeAttribute('aria-disabled');
      downloadButton.innerHTML = '<i class="bi bi-download"></i> Download export';
    }
    if (closeButton) closeButton.hidden = true;
    var smartMerge = document.getElementById('transferSmartMerge');
    if (smartMerge) smartMerge.hidden = true;
    errors.hidden = true;
    errors.replaceChildren();
    resultGrid.hidden = false;
    resultGrid.replaceChildren();
  }

  function addMetricToGrid(label, value) {
    var item = document.createElement('div');
    var number = document.createElement('b');
    var caption = document.createElement('span');
    item.className = 'import-result-pill';
    number.textContent = String(value || 0);
    caption.textContent = label;
    item.append(number, caption);
    resultGrid.appendChild(item);
  }

  function showResult(payload) {
    clearInterval(clockTimer);
    elapsed.textContent = elapsedLabel(Date.now() - startedAt);
    logEvent(payload.title_text || 'Transfer completed', payload.icon_modifier === 'is-error' ? 'error' : payload.icon_modifier === 'is-warning' ? 'active' : 'done');
    working.hidden = true;
    result.hidden = false;
    footer.hidden = false;
    transferHasResult = true;
    if (cancelButton) cancelButton.hidden = true;
    if (closeButton) closeButton.hidden = false;
    var modifier = ['is-success', 'is-warning', 'is-error'].includes(payload.icon_modifier)
      ? payload.icon_modifier : 'is-success';
    var resultIcon = ['bi-check-lg', 'bi-exclamation-lg', 'bi-x-lg', 'bi-shield-x'].includes(payload.icon_class)
      ? payload.icon_class : 'bi-check-lg';
    resultMark.className = 'transfer-result-mark ' + modifier;
    resultMark.replaceChildren(icon(resultIcon));
    resultTitle.textContent = payload.title_text || 'Import complete';
    resultMessage.textContent = payload.message || '';
    var smartMerge = document.getElementById('transferSmartMerge');
    if (smartMerge) {
      var changed = Number(payload.inserted || 0) + Number(payload.updated || 0);
      smartMerge.hidden = Boolean(payload.dry_run) || changed === 0;
      smartMerge.href = config.smartMergeUrl || smartMerge.href;
    }
    if (downloadButton && payload.download_token && payload.filename) {
      exportDlToken = payload.download_token;
      exportDlFilename = payload.filename;
      downloadButton.href = config.exportDlUrl.replace('TOKEN', exportDlToken);
      downloadButton.download = exportDlFilename;
      downloadButton.hidden = false;
    }
    addMetricToGrid('Inserted', payload.inserted);
    addMetricToGrid('Updated', payload.updated);
    addMetricToGrid('Skipped', payload.skipped);
    if (payload.rolled_back) addMetricToGrid('Rolled back', payload.rolled_back);
    addMetricToGrid('Asset links', payload.linked_assets);
    addMetricToGrid('Errors', (payload.errors || 0) + (payload.asset_errors || 0));
    addMetricToGrid('New assets', payload.new_assets);
    addMetricToGrid('Downloaded assets', payload.downloaded_assets);
    var details = payload.error_details || [];
    if (details.length) {
      errors.hidden = false;
      var heading = document.createElement('b');
      heading.textContent = 'Details';
      errors.appendChild(heading);
      details.slice(0, 20).forEach(function (entry) {
        var row = document.createElement('p');
        row.textContent = [entry.filename, entry.record ? 'record ' + entry.record : '', entry.message].filter(Boolean).join(' · ');
        errors.appendChild(row);
      });
    }
    var byType = payload.by_type;
    if (byType && Object.keys(byType).length) {
      byTypeContainer.hidden = false;
      byTypeGrid.replaceChildren();
      Object.keys(byType).forEach(function (type) {
        var cell = document.createElement('div');
        var num = document.createElement('b');
        var cap = document.createElement('span');
        cell.className = 'import-result-bytype-cell';
        num.textContent = String((byType[type].inserted || 0) + (byType[type].updated || 0));
        cap.textContent = tableLabel(type);
        cell.append(num, cap);
        byTypeGrid.appendChild(cell);
      });
    }
    if (payload.ok && !payload.dry_run && !payload.download_token) {
      stagedFiles = [];
      syncStagedFiles();
      refreshAfterImport = true;
    }
    transferLog[payload.icon_modifier === 'is-error' ? 'error' : payload.icon_modifier === 'is-warning' ? 'warn' : 'info'](
      payload.download_token ? 'export.ready'
        : payload.event === 'error' ? 'export.failed'
        : 'import.completed',
      {
        ok: Boolean(payload.ok),
        dry_run: Boolean(payload.dry_run),
        inserted: Number(payload.inserted || 0),
        updated: Number(payload.updated || 0),
        skipped: Number(payload.skipped || 0),
        errors: Number(payload.errors || 0),
        asset_errors: Number(payload.asset_errors || 0),
        bytes: Number(payload.bytes || 0),
      }
    );
  }

  function processStreamChunk() {
    if (!streamBuffer) return;
    var lines = streamBuffer.split('\n');
    streamBuffer = lines.pop() || '';
    lines.forEach(function (line) {
      line = line.trim();
      if (!line) return;
      try {
        var msg = JSON.parse(line);
        handleStreamMessage(msg);
      } catch (error) {
        transferLog.error('transfer.stream_parse_failed', { message: error && error.message, line: line.slice(0, 200) });
      }
    });
  }

  function handleStreamMessage(msg) {
    transferLog.debug('transfer.stream_event', { event: msg.event, phase: msg.phase, file: msg.file, ok: msg.ok });
    if (!msg.event && msg.error) {
      var rejectionMessage = msg.message || (
        msg.error === 'file_too_large'
          ? 'This upload exceeds the ' + String(msg.max_mb || '?') + ' MB limit. Reselect the files so the dashboard can queue them automatically.'
          : 'The server rejected the import.'
      );
      transferLog.error('import.rejected', {
        reason: msg.error, status: activeImportXhr && activeImportXhr.status,
        request_id: msg.request_id, max_mb: msg.max_mb,
      });
      acceptImportResult({
        event: 'result', ok: false, title_text: 'Import rejected',
        message: rejectionMessage + (msg.request_id ? ' Request ID: ' + msg.request_id + '.' : ''),
        icon_class: 'bi-x-lg', icon_modifier: 'is-error',
      });
    } else if (msg.event === 'queued') {
      activeImportJobId = msg.job_id || null;
      activeImportCancelUrl = msg.cancel_url || null;
      transferLog.info('import.queued', { job_id: activeImportJobId });
      logEvent('Server job queued', 'done');
    } else if (msg.event === 'phase') {
      setPhase(msg.label, '', msg.percent == null ? null : batchProgress(msg.percent));
      logEvent(msg.label, 'active');
    } else if (msg.event === 'progress') {
      var fileEl = fileProgressEls[msg.file];
      if (fileEl && msg.percent != null) {
        fileEl.querySelector('.file-bar-fill').style.width = msg.percent + '%';
        fileEl.querySelector('small').textContent = msg.percent + '%';
      }
      if (msg.percent != null) setProgress(batchProgress(msg.percent));
      if (detail && msg.file) detail.textContent = msg.file + ': ' + msg.current + '/' + msg.total;
    } else if (msg.event === 'file_start') {
      var row = document.createElement('div');
      var label = document.createElement('div');
      var name = document.createElement('span');
      var pct = document.createElement('small');
      var barBg = document.createElement('div');
      var barFill = document.createElement('span');
      var assetsBar = document.createElement('div');
      var assetsLabel = document.createElement('span');
      var assetsBg = document.createElement('div');
      var assetsFill = document.createElement('span');
      row.className = 'file-bar-row';
      label.className = 'file-bar-label';
      name.textContent = msg.file;
      pct.textContent = '0%';
      barBg.className = 'file-bar-bg';
      barFill.className = 'file-bar-fill';
      barBg.appendChild(barFill);
      label.append(name, pct);
      assetsBar.className = 'file-bar-assets';
      assetsBar.hidden = true;
      assetsLabel.className = 'file-bar-assets-label';
      assetsLabel.textContent = 'Assets';
      assetsBg.className = 'file-bar-bg';
      assetsFill.className = 'file-bar-fill';
      assetsBg.appendChild(assetsFill);
      assetsBar.append(assetsLabel, assetsBg);
      row.append(label, barBg, assetsBar);
      filesContainer.appendChild(row);
      fileProgressEls[msg.file] = row;
      logEvent('Processing ' + msg.file, 'active');
    } else if (msg.event === 'file_done') {
      var doneEl = fileProgressEls[msg.file];
      if (doneEl) {
        doneEl.classList.add('is-done');
        doneEl.querySelector('.file-bar-fill').style.width = '100%';
        doneEl.querySelector('small').textContent = '100%';
      }
      logEvent(msg.file + ' done', 'done');
    } else if (msg.event === 'detail') {
      if (detail) detail.textContent = msg.message || '';
      logEvent(msg.message || '', 'active');
      var assetMatch = (msg.message || '').match(/Asset (\d+)\/(\d+)/);
      if (assetMatch) {
        var assetCurrent = parseInt(assetMatch[1], 10);
        var assetTotal = parseInt(assetMatch[2], 10);
        var currentFileEl = null;
        var lastFileBar = filesContainer.lastElementChild;
        if (lastFileBar) currentFileEl = lastFileBar;
        if (currentFileEl) {
          var assetsBarEl = currentFileEl.querySelector('.file-bar-assets');
          if (assetsBarEl) {
            assetsBarEl.hidden = false;
            var fill = assetsBarEl.querySelector('.file-bar-fill');
            if (fill) fill.style.width = (assetCurrent / assetTotal * 100) + '%';
          }
        }
      }
    } else if (msg.event === 'metrics') {
      if (metricsRecords) metricsRecords.textContent = String(msg.records || 0);
      if (metricsRecordsTotal) metricsRecordsTotal.textContent = msg.total_records ? '/ ' + msg.total_records : '';
      if (metricsInserted) metricsInserted.textContent = String(msg.inserted || 0);
      if (metricsUpdated) metricsUpdated.textContent = String(msg.updated || 0);
      if (metricsAssets) metricsAssets.textContent = String(msg.assets_linked || 0);
      var totalErrors = (msg.errors || 0);
      var recordErrors = (msg.record_errors || 0);
      var assetErrors = (msg.asset_errors || 0);
      if (metricsErrors) metricsErrors.textContent = String(totalErrors);
      if (metricsRecordErrors) metricsRecordErrors.textContent = String(recordErrors);
      if (metricsAssetErrors) metricsAssetErrors.textContent = String(assetErrors);
      var recordRow = document.getElementById('metricsRecordErrorsRow');
      var assetRow = document.getElementById('metricsAssetErrorsRow');
      if (recordRow) recordRow.hidden = recordErrors === 0;
      if (assetRow) assetRow.hidden = assetErrors === 0;
    } else if (msg.event === 'error') {
      lastStreamError = msg;
      logEvent(msg.message || 'Import error', 'error');
    } else if (msg.event === 'result') {
      acceptImportResult(msg);
    }
  }

  function fileKey(file) {
    return [file.name, file.size, file.lastModified, file.type].join(':');
  }

  function syncStagedFiles() {
    // DataTransfer is not constructible in every supported browser. The
    // staged array is the source of truth; assigning FileList is only a
    // progressive enhancement for native form validity and accessibility.
    try {
      var transfer = new DataTransfer();
      stagedFiles.forEach(function (file) { transfer.items.add(file); });
      fileInput.files = transfer.files;
    } catch (_) {
      if (!stagedFiles.length) fileInput.value = '';
    }
    renderStagedFiles();
  }

  function renderStagedFiles() {
    var files = stagedFiles;
    var fileCount = document.getElementById('transferFileCount');
    var clearFiles = document.getElementById('transferClearFiles');
    skipAssets.hidden = files.length === 0;
    fileCount.textContent = files.length + (files.length === 1 ? ' file' : ' files');
    clearFiles.hidden = files.length === 0;
    selection.replaceChildren();
    if (!files.length) {
      var empty = document.createElement('span');
      empty.className = 'transfer-empty';
      empty.textContent = 'No files selected yet.';
      selection.appendChild(empty);
    }
    files.forEach(function (file) {
      var row = document.createElement('div');
      var fileIcon = icon(file.name.toLowerCase().endsWith('.zip') ? 'bi-file-zip' : 'bi-filetype-json');
      var copy = document.createElement('span');
      var name = document.createElement('b');
      var meta = document.createElement('small');
      var remove = document.createElement('button');
      row.className = 'transfer-selected-file';
      name.textContent = file.name;
      meta.textContent = sizeLabel(file.size);
      copy.append(name, meta);
      remove.type = 'button';
      remove.className = 'transfer-remove-file';
      remove.setAttribute('aria-label', 'Remove ' + file.name);
      remove.appendChild(icon('bi-x-lg'));
      remove.addEventListener('click', function () {
        stagedFiles = stagedFiles.filter(function (candidate) { return fileKey(candidate) !== fileKey(file); });
        syncStagedFiles();
      });
      row.append(fileIcon, copy, remove);
      selection.appendChild(row);
    });
    showSelectionProblem(selectionProblem(files));
    if (batchNotice) {
      var batches = buildImportBatches(files);
      batchNotice.hidden = batches.length < 2;
      batchNotice.textContent = batches.length < 2 ? '' : (
        files.length + ' files will be processed automatically in ' + batches.length + ' sequential uploads.'
      );
    }
  }

  fileInput.addEventListener('change', function () {
    var incoming = Array.from(fileInput.files || []);
    var known = new Set(stagedFiles.map(fileKey));
    incoming.forEach(function (file) {
      if (!known.has(fileKey(file))) {
        stagedFiles.push(file);
        known.add(fileKey(file));
      }
    });
    syncStagedFiles();
    transferLog.info('import.files_staged', {
      files: stagedFiles.length,
      bytes: stagedFiles.reduce(function(total, file) { return total + file.size; }, 0),
      extensions: stagedFiles.map(function(file) {
        return String(file.name || '').split('.').pop().toLowerCase();
      }),
    });
  });
  document.getElementById('transferClearFiles').addEventListener('click', function () {
    stagedFiles = [];
    syncStagedFiles();
  });
  renderStagedFiles();
  form.querySelectorAll('[name="dry_run"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      var validateOnly = form.querySelector('[name="dry_run"]:checked')?.value === '1';
      importButton.replaceChildren(icon(validateOnly ? 'bi-shield-check' : 'bi-upload'));
      importButton.append(document.createTextNode(validateOnly ? ' Validate files' : ' Import data'));
    });
  });

  function sendImportBatch(files, batchIndex, batchTotal, password) {
    return new Promise(function (resolve, reject) {
      streamBuffer = '';
      lastStreamError = null;
      activeImportJobId = null;
      activeImportCancelUrl = null;
      var context = { index: batchIndex, total: batchTotal, result: null };
      importBatchContext = context;
      var terminalResult = null;
      var settled = false;
      function finish() {
        if (settled) return;
        settled = true;
        activeImportXhr = null;
        importBatchContext = null;
        var payload = context.result || terminalResult;
        if (!payload) {
          payload = {
            event: 'result', ok: false, title_text: 'Import ended unexpectedly',
            message: 'Upload ' + (batchIndex + 1) + ' ended without a final result. Check the server log.',
            icon_class: 'bi-x-lg', icon_modifier: 'is-error'
          };
        }
        if (payload.ok) resolve(payload);
        else reject(payload);
      }
    var xhr = new XMLHttpRequest();
    activeImportXhr = xhr;
    xhr.open('POST', form.action, true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    var lastStreamPos = 0;
    xhr.addEventListener('readystatechange', function () {
      if (xhr.readyState === 3 || xhr.readyState === 4) {
        var newData = xhr.responseText.substring(lastStreamPos);
        if (newData) {
          lastStreamPos = xhr.responseText.length;
          streamBuffer += newData;
          processStreamChunk();
        }
      }
    });
    xhr.upload.addEventListener('progress', function (upload) {
      if (!upload.lengthComputable) return;
      setProgress(batchProgress(upload.loaded / upload.total * 100));
      status.textContent = 'Uploading package ' + (batchIndex + 1) + ' of ' + batchTotal + '…';
      detail.textContent = sizeLabel(upload.loaded) + ' of ' + sizeLabel(upload.total) + ' uploaded in this package';
    });
    xhr.upload.addEventListener('load', function () {
      logEvent('Upload ' + (batchIndex + 1) + '/' + batchTotal + ' complete · server processing started', 'done');
      progress.parentElement.classList.add('is-loading');
      progress.style.removeProperty('width');
      percent.textContent = 'Processing…';
    });
    xhr.addEventListener('loadend', function () {
      streamBuffer += xhr.responseText.substring(lastStreamPos);
      processStreamChunk();
      if (!context.result && xhr.status >= 400 && !terminalResult) {
        terminalResult = { event: 'result', ok: false, title_text: 'Import failed',
          message: 'Package ' + (batchIndex + 1) + ' of ' + batchTotal + ' was rejected (HTTP ' + (xhr.status || 0) + '). Check the browser and server logs.',
          icon_class: 'bi-x-lg', icon_modifier: 'is-error' };
      }
      transferLog.info('import.response_finished', { status: xhr.status, ok: xhr.status >= 200 && xhr.status < 300 });
      finish();
    });
    xhr.addEventListener('error', function () {
      logEvent('Network error while waiting for the server', 'error');
      transferLog.error('import.network_failed', { status: xhr.status || 0 });
      terminalResult = { event: 'result', ok: false, title_text: 'Network error', message: 'The connection to the server was interrupted during package ' + (batchIndex + 1) + '.', icon_class: 'bi-x-lg', icon_modifier: 'is-error' };
    });
    xhr.addEventListener('abort', function () {
      logEvent('Import request cancelled', 'error');
      transferLog.warn('import.cancelled', {});
      terminalResult = { event: 'result', ok: false, cancelled: true, title_text: 'Import cancelled', message: 'The upload/import queue was cancelled.', icon_class: 'bi-x-lg', icon_modifier: 'is-warning' };
    });
    var payload = new FormData(form);
    payload.delete('data_file');
    payload.delete('password');
    payload.append('password', password);
    files.forEach(function (file) {
      payload.append('data_file', file, file.name);
    });
    xhr.send(payload);
    });
  }

  function mergeImportResult(summary, payload) {
    ['inserted', 'updated', 'skipped', 'rolled_back', 'linked_assets', 'asset_errors', 'errors', 'new_assets', 'downloaded_assets'].forEach(function (key) {
      summary[key] += Number(payload[key] || 0);
    });
    (payload.error_details || []).forEach(function (entry) { summary.error_details.push(entry); });
    Object.keys(payload.by_type || {}).forEach(function (type) {
      if (!summary.by_type[type]) summary.by_type[type] = { inserted: 0, updated: 0, skipped: 0 };
      ['inserted', 'updated', 'skipped'].forEach(function (key) {
        summary.by_type[type][key] += Number(payload.by_type[type][key] || 0);
      });
    });
  }

  async function runImportQueue(batches, dryRun, password) {
    var summary = {
      event: 'result', ok: true, dry_run: dryRun,
      inserted: 0, updated: 0, skipped: 0, rolled_back: 0,
      linked_assets: 0, asset_errors: 0, errors: 0,
      new_assets: 0, downloaded_assets: 0, error_details: [], by_type: {}
    };
    var completed = 0;
    try {
      for (var index = 0; index < batches.length; index += 1) {
        status.textContent = 'Package ' + (index + 1) + ' of ' + batches.length;
        logEvent('Starting upload ' + (index + 1) + '/' + batches.length + ': ' + batches[index].map(function (file) { return file.name; }).join(', '), 'active');
        var batchResult = await sendImportBatch(batches[index], index, batches.length, password);
        mergeImportResult(summary, batchResult);
        completed += 1;
      }
      summary.title_text = dryRun ? 'Validation complete' : 'Import complete';
      summary.message = batches.length > 1
        ? 'All ' + batches.length + ' queued uploads completed successfully.'
        : 'The selected data was processed successfully.';
      summary.icon_class = 'bi-check-lg';
      summary.icon_modifier = summary.errors || summary.asset_errors ? 'is-warning' : 'is-success';
      showResult(summary);
    } catch (failure) {
      failure = failure || {};
      failure.event = 'result';
      failure.ok = false;
      failure.dry_run = dryRun;
      failure.title_text = failure.title_text || 'Import queue stopped';
      var prefix = completed ? completed + ' of ' + batches.length + ' uploads completed. ' : '';
      failure.message = prefix + (failure.message || 'The next package could not be imported.');
      failure.icon_class = failure.icon_class || 'bi-x-lg';
      failure.icon_modifier = failure.icon_modifier || 'is-error';
      showResult(failure);
    } finally {
      clearFormLoading(form);
      setOperationActive(false);
      activeImportXhr = null;
      importBatchContext = null;
      password = '';
    }
  }

  function startAuthorizedImport(files, dryRun, password) {
    setOperationActive(true);
    var batches = buildImportBatches(files);
    var totalBytes = files.reduce(function (total, file) { return total + file.size; }, 0);
    transferLog.info('import.authorization_submitted', {
      files: files.length, batches: batches.length,
      bytes: totalBytes, dry_run: dryRun,
      skip_assets: Boolean(form.querySelector('[name="skip_assets"]')?.checked),
      force_import: Boolean(form.querySelector('[name="force_import"]')?.checked),
    });
    resetModal(dryRun ? 'Check import' : 'Import data', 'Preparing upload queue…');
    logEvent('Selected ' + files.length + ' file(s) · ' + sizeLabel(totalBytes) + ' · ' + batches.length + ' upload(s)', 'done');
    logEvent(dryRun ? 'Validation mode: database will not be changed' : 'A database backup is created before each upload', 'active');
    modal.show();
    runImportQueue(batches, dryRun, password);
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (operationActive) return;
    if (!stagedFiles.length) {
      fileInput.setCustomValidity('Choose at least one JSONL file or ZIP package.');
      form.reportValidity();
      transferLog.warn('import.blocked', { reason: 'no_files' });
      return;
    }
    var problem = selectionProblem(stagedFiles);
    if (problem) {
      showSelectionProblem(problem);
      form.reportValidity();
      transferLog.warn('import.blocked', {
        reason: 'invalid_selection', files: stagedFiles.length,
        bytes: stagedFiles.reduce(function (total, file) { return total + file.size; }, 0),
      });
      return;
    }
    showSelectionProblem('');
    var files = stagedFiles.slice();
    var dryRun = form.querySelector('[name="dry_run"]:checked')?.value === '1';
    pendingImportRequest = { files: files, dryRun: dryRun };
    if (importAuthOperation) importAuthOperation.textContent = dryRun ? 'Validate selected files' : 'Import selected data';
    if (importAuthModal) importAuthModal.show();
  });

  if (importAuthForm) {
    importAuthForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (!pendingImportRequest || !importAuthPassword || !importAuthPassword.value) {
        importAuthForm.reportValidity();
        return;
      }
      var requestData = pendingImportRequest;
      var password = importAuthPassword.value;
      importAuthPassword.value = '';
      pendingImportRequest = null;
      importAuthModal.hide();
      startAuthorizedImport(requestData.files, requestData.dryRun, password);
    });
  }

  if (cancelButton) cancelButton.addEventListener('click', async function () {
    if (!operationActive) return;
    cancelButton.disabled = true;
    cancelButton.textContent = 'Cancelling…';
    try {
      if (!activeImportJobId || !activeImportCancelUrl) {
        if (activeImportXhr) activeImportXhr.abort();
        else {
          setOperationActive(false);
          showResult({ event: 'result', ok: false, cancelled: true, title_text: 'Operation cancelled', message: 'The operation was cancelled.', icon_class: 'bi-x-lg', icon_modifier: 'is-warning' });
        }
        return;
      }
      transferLog.warn('import.cancel_requested', { job_id: activeImportJobId });
      logEvent('Cancellation requested; rolling back safely…', 'active');
      status.textContent = 'Cancelling import…';
      var response = await fetch(activeImportCancelUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRF-Token': config.csrfToken, 'X-Requested-With': 'XMLHttpRequest' }
      });
      if (!response.ok && response.status !== 409) throw new Error('Cancel request failed (HTTP ' + response.status + ')');
      cancelButton.textContent = 'Cancellation requested';
    } catch (error) {
      cancelButton.disabled = false;
      cancelButton.innerHTML = '<i class="bi bi-x-circle"></i> Cancel';
      transferLog.error('import.cancel_failed', { message: error && error.message });
      logEvent(error && error.message ? error.message : 'Unable to cancel import', 'error');
    }
  });

  function startAuthorizedExport(fmt, password) {
      setOperationActive(true);
      var fmtUpper = fmt.toUpperCase();
      transferLog.info('export.authorization_submitted', { format: fmt });
      resetModal('Export data', 'Preparing ' + fmtUpper + '…');
      if (cancelButton) cancelButton.hidden = true;
      logEvent(fmtUpper === 'ZIP' ? 'Collecting records and local assets' : 'Serializing records as JSONL', 'active');
      modal.show();

      var xhr = new XMLHttpRequest();
      var url = fmt === 'zip' ? config.exportZipUrl : config.exportJsonlUrl;
      xhr.open('POST', url, true);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      xhr.setRequestHeader('X-CSRF-Token', config.csrfToken);
      xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
      var lastStreamPos = 0;
      xhr.addEventListener('readystatechange', function () {
        if (xhr.readyState === 3 || xhr.readyState === 4) {
          var newData = xhr.responseText.substring(lastStreamPos);
          if (newData) {
            lastStreamPos = xhr.responseText.length;
            streamBuffer += newData;
            processStreamChunk();
          }
        }
      });
      xhr.addEventListener('loadend', function () {
        setOperationActive(false);
        streamBuffer += xhr.responseText.substring(lastStreamPos);
        processStreamChunk();
        if (exportDlToken && exportDlFilename) {
          logEvent(exportDlFilename + ' is ready to download', 'done');
          transferLog.info('export.download_ready', { format: fmt, filename: exportDlFilename });
        } else if (xhr.status >= 400) {
          var serverMessage = lastStreamError && lastStreamError.message;
          var lines = streamBuffer.split('\n').filter(Boolean);
          try {
            var lastMsg = JSON.parse(lines[lines.length - 1] || '{}');
            if (lastMsg && lastMsg.ok === false && lastMsg.message) serverMessage = lastMsg.message;
          } catch (_) {}
          showResult({
            event: 'error', ok: false,
            title_text: (lastStreamError && lastStreamError.title_text) || 'Export failed',
            message: serverMessage || ('The export could not be generated (HTTP ' + (xhr.status || 0) + ').'),
            icon_class: (lastStreamError && lastStreamError.icon_class) || 'bi-x-lg', icon_modifier: 'is-error',
          });
          transferLog.error('export.failed', { format: fmt, status: xhr.status || 0 });
        }
      });
      xhr.addEventListener('error', function () {
        logEvent('Network error during export', 'error');
        setOperationActive(false);
        transferLog.error('export.network_failed', { format: fmt, status: xhr.status || 0 });
        if (!transferHasResult) showResult({ event: 'result', ok: false, title_text: 'Export network error', message: 'The connection to the server was interrupted during export.', icon_class: 'bi-x-lg', icon_modifier: 'is-error' });
      });
      var requestBody = (
        '_csrf_token=' + encodeURIComponent(config.csrfToken)
        + '&password=' + encodeURIComponent(password)
      );
      password = '';
      xhr.send(requestBody);
      requestBody = '';
  }

  document.querySelectorAll('.transfer-export-button[data-export]').forEach(function (button) {
    button.addEventListener('click', function (event) {
      event.preventDefault();
      if (operationActive || !exportAuthModal) return;
      pendingExportFormat = button.dataset.export;
      if (exportAuthFormat) exportAuthFormat.textContent = pendingExportFormat.toUpperCase();
      exportAuthModal.show();
    });
  });

  if (exportAuthForm) {
    exportAuthForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (!pendingExportFormat || !exportAuthPassword || !exportAuthPassword.value) {
        exportAuthForm.reportValidity();
        return;
      }
      var fmt = pendingExportFormat;
      var password = exportAuthPassword.value;
      exportAuthPassword.value = '';
      exportAuthModal.hide();
      startAuthorizedExport(fmt, password);
    });
  }

  // Intercept result events from export: capture download_token
  var origHandleStream = handleStreamMessage;
  handleStreamMessage = function (msg) {
    if (msg.event === 'result' && msg.download_token) {
      exportDlToken = msg.download_token;
      exportDlFilename = msg.filename;
    }
    origHandleStream(msg);
  };

  if (downloadButton) {
    downloadButton.addEventListener('click', function () {
      if (!exportDlFilename || !downloadButton.getAttribute('href')) return;
      logEvent('Downloading ' + exportDlFilename, 'done');
      transferLog.info('export.download_started', { filename: exportDlFilename });
      window.setTimeout(function () {
        downloadButton.removeAttribute('href');
        downloadButton.removeAttribute('download');
        downloadButton.setAttribute('aria-disabled', 'true');
        downloadButton.innerHTML = '<i class="bi bi-check-lg"></i> Download started';
      }, 0);
    });
  }

  if (config.importResult) {
    resetModal('Import data', 'Import complete');
    modal.show();
    showResult(config.importResult);
  }
})();

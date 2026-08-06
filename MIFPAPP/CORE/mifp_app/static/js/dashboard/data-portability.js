/* Data portability progress enhancement */
(function () {
  'use strict';
  var clearFormLoading = window.MIFPUI.clearFormLoading;
  var transferLog = window.MIFPLog || {
    debug: function(){}, info: function(){}, warn: function(){}, error: function(){}
  };

  var modalElement = document.getElementById('transferModal');
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
  modalElement.addEventListener('hidden.bs.modal', function () {
    clearFormLoading(form);
    clearInterval(clockTimer);
    if (refreshAfterImport) window.location.reload();
  });
  var form = document.getElementById('transferImportForm');
  var fileInput = document.getElementById('transferFiles');
  var selection = document.getElementById('transferSelection');
  var importButton = document.getElementById('transferImportButton');
  var skipAssets = document.getElementById('skipAssetsOption');
  var working = document.getElementById('transferWorking');
  var result = document.getElementById('transferResult');
  var footer = document.getElementById('transferFooter');
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
    footer.hidden = true;
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
    var modifier = ['is-success', 'is-warning', 'is-error'].includes(payload.icon_modifier)
      ? payload.icon_modifier : 'is-success';
    var resultIcon = ['bi-check-lg', 'bi-exclamation-lg', 'bi-x-lg'].includes(payload.icon_class)
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
      payload.download_token ? 'export.ready' : 'import.completed',
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
      } catch (_) {}
    });
  }

  function handleStreamMessage(msg) {
    if (msg.event === 'phase') {
      setPhase(msg.label, '', msg.percent);
      logEvent(msg.label, 'active');
    } else if (msg.event === 'progress') {
      var fileEl = fileProgressEls[msg.file];
      if (fileEl && msg.percent != null) {
        fileEl.querySelector('.file-bar-fill').style.width = msg.percent + '%';
        fileEl.querySelector('small').textContent = msg.percent + '%';
      }
      if (msg.percent != null) setProgress(msg.percent);
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
      logEvent(msg.message || 'Import error', 'error');
    } else if (msg.event === 'result') {
      clearInterval(clockTimer);
      showResult(msg);
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
    var zipFiles = files.filter(function (file) { return file.name.toLowerCase().endsWith('.zip'); });
    var fileCount = document.getElementById('transferFileCount');
    var clearFiles = document.getElementById('transferClearFiles');
    skipAssets.hidden = zipFiles.length === 0;
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
    fileInput.setCustomValidity('');
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

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (operationActive) return;
    if (!stagedFiles.length) {
      fileInput.setCustomValidity('Choose at least one JSONL file or one ZIP package.');
      form.reportValidity();
      transferLog.warn('import.blocked', { reason: 'no_files' });
      return;
    }
    fileInput.setCustomValidity('');
    setOperationActive(true);
    var files = stagedFiles.slice();
    var dryRun = form.querySelector('[name="dry_run"]:checked')?.value === '1';
    var totalBytes = files.reduce(function (total, file) { return total + file.size; }, 0);
    transferLog.info('import.started', {
      files: files.length,
      bytes: totalBytes,
      dry_run: dryRun,
      skip_assets: Boolean(form.querySelector('[name="skip_assets"]')?.checked),
      force_import: Boolean(form.querySelector('[name="force_import"]')?.checked),
    });
    resetModal(dryRun ? 'Check import' : 'Import data', 'Preparing upload…');
    logEvent('Selected ' + files.length + ' file(s) · ' + sizeLabel(totalBytes), 'done');
    logEvent(dryRun ? 'Validation mode: database will not be changed' : 'Database backup scheduled before import', 'active');
    modal.show();

    var xhr = new XMLHttpRequest();
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
      setProgress(upload.loaded / upload.total * 100);
      status.textContent = 'Uploading files…';
      detail.textContent = sizeLabel(upload.loaded) + ' of ' + sizeLabel(upload.total) + ' uploaded';
    });
    xhr.upload.addEventListener('load', function () {
      logEvent('Upload complete · server processing started', 'done');
      progress.parentElement.classList.add('is-loading');
      progress.style.removeProperty('width');
      percent.textContent = 'Processing…';
    });
    xhr.addEventListener('loadend', function () {
      clearFormLoading(form);
      setOperationActive(false);
      streamBuffer += xhr.responseText.substring(lastStreamPos);
      processStreamChunk();
      transferLog.info('import.response_finished', {
        status: xhr.status,
        ok: xhr.status >= 200 && xhr.status < 300,
      });
    });
    xhr.addEventListener('error', function () {
      clearFormLoading(form);
      logEvent('Network error while waiting for the server', 'error');
      setOperationActive(false);
      transferLog.error('import.network_failed', { status: xhr.status || 0 });
    });
    xhr.addEventListener('abort', function () {
      clearFormLoading(form);
      logEvent('Import request cancelled', 'error');
      setOperationActive(false);
      transferLog.warn('import.cancelled', {});
    });
    var payload = new FormData(form);
    payload.delete('data_file');
    files.forEach(function (file) {
      payload.append('data_file', file, file.name);
    });
    xhr.send(payload);
  });

  document.querySelectorAll('.transfer-export-button[data-export]').forEach(function (button) {
    button.addEventListener('click', function (event) {
      event.preventDefault();
      if (operationActive) return;
      setOperationActive(true);
      var fmt = button.dataset.export;
      var fmtUpper = fmt.toUpperCase();
      transferLog.info('export.started', { format: fmt });
      resetModal('Export data', 'Preparing ' + fmtUpper + '…');
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
          var dlUrl = config.exportDlUrl.replace('TOKEN', exportDlToken);
          var anchor = document.createElement('a');
          anchor.href = dlUrl;
          anchor.download = exportDlFilename;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          logEvent('Downloading ' + exportDlFilename, 'done');
          transferLog.info('export.download_started', { format: fmt });
          exportDlToken = null;
          exportDlFilename = null;
        }
      });
      xhr.addEventListener('error', function () {
        logEvent('Network error during export', 'error');
        setOperationActive(false);
        transferLog.error('export.network_failed', { format: fmt, status: xhr.status || 0 });
      });
      xhr.send('_csrf_token=' + encodeURIComponent(config.csrfToken));
    });
  });

  // Intercept result events from export: capture download_token
  var origHandleStream = handleStreamMessage;
  handleStreamMessage = function (msg) {
    if (msg.event === 'result' && msg.download_token) {
      exportDlToken = msg.download_token;
      exportDlFilename = msg.filename;
    }
    origHandleStream(msg);
  };

  if (config.importResult) {
    resetModal('Import data', 'Import complete');
    modal.show();
    showResult(config.importResult);
  }
})();

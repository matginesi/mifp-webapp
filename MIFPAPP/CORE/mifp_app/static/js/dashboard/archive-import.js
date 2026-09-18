/* Historical archive import progress enhancement. */
(function () {
  'use strict';

  var form = document.getElementById('archiveImportForm');
  var modalElement = document.getElementById('archiveTransferModal');
  var authElement = document.getElementById('archiveAuthModal');
  var authForm = document.getElementById('archiveAuthForm');
  var fileInput = document.getElementById('archiveZip');
  if (!form || !modalElement || !authElement || !authForm || !fileInput || !window.bootstrap) return;

  var modal = new window.bootstrap.Modal(modalElement, { backdrop: 'static', keyboard: false });
  var authModal = new window.bootstrap.Modal(authElement);
  var authPassword = document.getElementById('archiveAuthPassword');
  var authOperation = document.getElementById('archiveAuthOperation');
  var authError = document.getElementById('archiveAuthError');
  var authSubmit = document.getElementById('archiveAuthSubmit');
  var selection = document.getElementById('archiveSelection');
  var submitButton = document.getElementById('archiveImportButton');
  var working = document.getElementById('archiveWorking');
  var result = document.getElementById('archiveResult');
  var resultMark = document.getElementById('archiveResultMark');
  var resultTitle = document.getElementById('archiveResultTitle');
  var resultMessage = document.getElementById('archiveResultMessage');
  var resultGrid = document.getElementById('archiveResultGrid');
  var modalTitle = document.getElementById('archiveModalTitle');
  var status = document.getElementById('archiveStatus');
  var detail = document.getElementById('archiveDetail');
  var progress = document.getElementById('archiveProgress');
  var progressTrack = progress.parentElement;
  var percent = document.getElementById('archivePercent');
  var elapsed = document.getElementById('archiveElapsed');
  var activity = document.getElementById('archiveActivityLog');
  var packageName = document.getElementById('archivePackageName');
  var packageSize = document.getElementById('archivePackageSize');
  var packageState = document.getElementById('archivePackageState');
  var packageTotem = document.getElementById('archivePackageTotem');
  var cancelButton = document.getElementById('archiveCancel');
  var closeButton = document.getElementById('archiveClose');
  var metrics = {
    events: document.getElementById('archiveMetricEvents'),
    documents: document.getElementById('archiveMetricDocuments'),
    images: document.getElementById('archiveMetricImages'),
    people: document.getElementById('archiveMetricPeople')
  };
  var requestController = null;
  var cancelUrl = null;
  var timer = null;
  var startedAt = 0;
  var refreshOnClose = false;

  // Operation-totem contract: the label is free text, the tone is one of the
  // shared states (idle|queued|working|success|warning|error) written to
  // `data-state`. Keeping both in one call is what stops this widget from
  // drifting into its own private state classes again.
  function setPackageState(label, state) {
    if (packageState) packageState.textContent = label;
    if (packageTotem) packageTotem.dataset.state = state;
  }

  function selectedMode() {
    return form.querySelector('input[name="dry_run"]:checked')?.value === '0' ? 'import' : 'validate';
  }

  function updateButton() {
    var importing = selectedMode() === 'import';
    submitButton.innerHTML = importing
      ? '<i class="bi bi-archive"></i> Import archive'
      : '<i class="bi bi-shield-check"></i> Validate package';
  }

  function renderSelection() {
    var file = fileInput.files[0];
    selection.replaceChildren();
    selection.classList.toggle('has-file', Boolean(file));
    if (!file) {
      var empty = document.createElement('span');
      empty.className = 'transfer-empty';
      empty.textContent = 'No package selected yet.';
      selection.appendChild(empty);
      return;
    }
    var icon = document.createElement('i');
    icon.className = 'bi bi-file-earmark-zip';
    var name = document.createElement('b');
    name.textContent = file.name;
    var size = document.createElement('small');
    size.textContent = window.MIFP.formatBytes(file.size);
    selection.append(icon, name, size);
  }

  function log(message, tone) {
    var row = document.createElement('div');
    row.className = 'transfer-activity-entry is-' + (tone || 'active');
    var icon = document.createElement('i');
    icon.className = tone === 'done' ? 'bi bi-check-circle-fill' : tone === 'error' ? 'bi bi-x-circle-fill' : 'bi bi-arrow-right-circle';
    var copy = document.createElement('span');
    copy.textContent = message;
    row.append(icon, copy);
    activity.appendChild(row);
    while (activity.children.length > 5) activity.firstElementChild.remove();
  }

  function setProgress(value, message, description) {
    // Geometry/ARIA come from the shared primitive; the status and detail lines
    // are specific to the archive panel.
    window.MIFPUI.setProgressBar(progressTrack, progress, percent, value);
    if (message) status.textContent = message;
    if (description != null) detail.textContent = description;
  }

  function resetModal(file, mode) {
    refreshOnClose = false;
    cancelUrl = null;
    working.hidden = false;
    result.hidden = true;
    closeButton.hidden = true;
    cancelButton.hidden = false;
    cancelButton.disabled = false;
    cancelButton.innerHTML = '<i class="bi bi-x-circle"></i> Cancel';
    modalTitle.textContent = mode === 'import' ? 'Import historical archive' : 'Validate historical archive';
    status.textContent = 'Uploading package…';
    detail.textContent = 'The package is staged securely before processing starts.';
    packageName.textContent = file.name;
    packageSize.textContent = window.MIFP.formatBytes(file.size);
    setPackageState('Uploading', 'working');
    activity.replaceChildren();
    resultGrid.replaceChildren();
    Object.keys(metrics).forEach(function (key) { metrics[key].textContent = '0'; });
    progressTrack.classList.add('is-loading');
    progress.style.removeProperty('width');
    progressTrack.setAttribute('aria-valuenow', '0');
    percent.textContent = 'Working…';
    startedAt = Date.now();
    elapsed.textContent = '00:00';
    clearInterval(timer);
    timer = window.setInterval(function () { elapsed.textContent = window.MIFPUI.elapsedLabel(Date.now() - startedAt); }, 500);
    log('Package upload started', 'active');
  }

  function addResultMetric(label, value) {
    var item = document.createElement('div');
    item.className = 'import-result-pill';
    var number = document.createElement('b');
    number.textContent = String(value || 0);
    var caption = document.createElement('span');
    caption.textContent = label;
    item.append(number, caption);
    resultGrid.appendChild(item);
  }

  function showResult(payload, mode) {
    clearInterval(timer);
    var data = payload.result || {};
    var ok = payload.ok === true;
    var cancelled = !ok && Boolean(payload.message && payload.message.toLowerCase().includes('cancel'));
    working.hidden = true;
    result.hidden = false;
    cancelButton.hidden = true;
    closeButton.hidden = false;
    resultMark.className = 'transfer-result-mark' + (ok ? '' : ' is-error');
    resultMark.innerHTML = ok ? '<i class="bi bi-check-lg"></i>' : '<i class="bi bi-x-lg"></i>';
    setPackageState(
      ok ? (mode === 'import' ? 'Imported' : 'Validated') : (cancelled ? 'Cancelled' : 'Failed'),
      ok ? 'success' : (cancelled ? 'warning' : 'error')
    );
    resultTitle.textContent = ok
      ? (mode === 'import' ? 'Archive imported' : 'Package validated')
      : (cancelled ? 'Import cancelled' : 'Archive import failed');
    resultMessage.textContent = ok
      ? (mode === 'import'
        ? 'Historical records and valid assets are now connected to canonical events.'
        : 'The package is valid. Review the action counts, then choose Import archive when ready.')
      : (payload.message || 'The package could not be processed. No uncommitted changes were kept.');
    if (ok) {
      addResultMetric('Events', data.events);
      addResultMetric('Documents', data.documents);
      addResultMetric('Images', data.images);
      addResultMetric('People', data.people);
      Object.keys(metrics).forEach(function (key) { metrics[key].textContent = String(data[key] || 0); });
      refreshOnClose = mode === 'import';
    }
  }

  function handleEvent(event, mode) {
    if (event.event === 'queued') {
      cancelUrl = event.cancel_url;
      setPackageState('Queued', 'queued');
      status.textContent = 'Package queued';
      detail.textContent = 'Waiting for an available archive worker.';
      log('Package accepted by the import queue', 'done');
      return;
    }
    if (event.event === 'progress') {
      setPackageState(mode === 'import' ? 'Importing' : 'Validating', 'working');
      setProgress(event.percent, event.message, mode === 'import' ? 'Events and assets are processed transactionally.' : 'No database changes are being written.');
      log(event.message || 'Processing package', 'active');
      return;
    }
    if (event.event === 'result') showResult(event, mode);
  }

  async function consumeStream(response, mode) {
    if (!response.ok) {
      var contentType = response.headers.get('Content-Type') || '';
      var failure = contentType.includes('application/json') ? await response.json() : {};
      var error = new Error(failure.message || 'The archive package was rejected.');
      error.status = response.status;
      throw error;
    }
    if (!response.body) throw new Error('The server did not provide an import progress stream.');
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      var lines = buffer.split('\n');
      buffer = lines.pop() || '';
      lines.forEach(function (line) {
        if (!line.trim()) return;
        handleEvent(JSON.parse(line), mode);
      });
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer), mode);
  }

  fileInput.addEventListener('change', renderSelection);
  form.addEventListener('change', function (event) {
    if (event.target.matches('input[name="dry_run"]')) updateButton();
  });

  function showAuthorizationError(message) {
    authError.textContent = message;
    authError.hidden = false;
    authPassword.select();
  }

  async function startTransfer(password) {
    var file = fileInput.files[0];
    var mode = selectedMode();
    resetModal(file, mode);
    submitButton.disabled = true;
    var transferVisible = new Promise(function (resolve) {
      modalElement.addEventListener('shown.bs.modal', resolve, { once: true });
    });
    modal.show();
    await transferVisible;
    requestController = new AbortController();
    var retainPassword = false;
    try {
      var body = new FormData(form);
      body.append('password', password);
      var response = await fetch(form.action, {
        method: 'POST',
        body: body,
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal: requestController.signal
      });
      await consumeStream(response, mode);
    } catch (error) {
      if (error.name !== 'AbortError' && (error.status === 403 || error.status === 429)) {
        retainPassword = true;
        modalElement.addEventListener('hidden.bs.modal', function reopenAuthorization() {
          authModal.show();
          showAuthorizationError(error.message);
        }, { once: true });
        modal.hide();
      } else if (error.name !== 'AbortError') {
        showResult({ ok: false, message: error.message }, mode);
      }
    } finally {
      requestController = null;
      submitButton.disabled = false;
      if (!retainPassword) authPassword.value = '';
      window.MIFPUI?.clearFormLoading(form);
    }
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!form.reportValidity()) return;
    authError.hidden = true;
    authError.textContent = '';
    authOperation.textContent = selectedMode() === 'import' ? 'Import archive' : 'Validate package';
    authModal.show();
  });

  authForm.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!authForm.reportValidity()) return;
    var password = authPassword.value;
    authSubmit.disabled = true;
    authError.hidden = true;
    authElement.addEventListener('hidden.bs.modal', function beginAuthorizedTransfer() {
      startTransfer(password).finally(function () {
        authSubmit.disabled = false;
      });
    }, { once: true });
    authModal.hide();
  });

  cancelButton.addEventListener('click', async function () {
    cancelButton.disabled = true;
    cancelButton.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Cancelling…';
    if (cancelUrl) {
      try {
        var token = form.querySelector('input[name="_csrf_token"]').value;
        var body = new FormData();
        body.append('_csrf_token', token);
        await fetch(cancelUrl, { method: 'POST', body: body, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        status.textContent = 'Cancellation requested…';
        detail.textContent = 'The current safe checkpoint will stop the operation.';
        log('Cancellation requested', 'active');
      } catch (_) {
        cancelButton.disabled = false;
        cancelButton.innerHTML = '<i class="bi bi-x-circle"></i> Cancel';
      }
      return;
    }
    requestController?.abort();
    showResult({ ok: false, message: 'Upload cancelled.' }, selectedMode());
  });

  modalElement.addEventListener('hidden.bs.modal', function () {
    clearInterval(timer);
    if (refreshOnClose) window.location.reload();
  });
  authElement.addEventListener('shown.bs.modal', function () { authPassword.focus(); });
  authElement.addEventListener('hidden.bs.modal', function () {
    if (!requestController) authSubmit.disabled = false;
  });

  renderSelection();
  updateButton();
})();

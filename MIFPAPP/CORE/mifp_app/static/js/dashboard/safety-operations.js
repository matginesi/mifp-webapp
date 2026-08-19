(() => {
  'use strict';

  const wizard = document.querySelector('[data-safety-wizard]');
  if (!wizard) return;
  const panels = [...wizard.querySelectorAll('[data-wizard-panel]')];
  const markers = [...wizard.querySelectorAll('[data-wizard-marker]')];
  const reviews = [...wizard.querySelectorAll('[data-operation-review]')];
  const cleanupConfirm = wizard.querySelector('[data-cleanup-confirm]');
  const cleanupInput = cleanupConfirm?.querySelector('input');
  const cleanupWarning = wizard.querySelector('[data-cleanup-warning]');
  const submit = wizard.querySelector('[data-wizard-submit]');
  let step = 1;

  wizard.classList.add('is-enhanced');

  function operation() {
    return wizard.querySelector('input[name="operation"]:checked')?.value || '';
  }

  function render() {
    panels.forEach((panel) => {
      const active = Number(panel.dataset.wizardPanel) === step;
      panel.classList.toggle('is-current', active);
      panel.hidden = !active;
    });
    markers.forEach((marker) => {
      const markerStep = Number(marker.dataset.wizardMarker);
      marker.classList.toggle('is-current', markerStep === step);
      marker.classList.toggle('is-complete', markerStep < step);
    });
    const selected = operation();
    reviews.forEach((review) => {
      review.hidden = review.dataset.operationReview !== selected;
    });
    const cleanup = selected === 'cleanup';
    if (cleanupConfirm) cleanupConfirm.hidden = !cleanup;
    if (cleanupWarning) cleanupWarning.hidden = !cleanup;
    if (cleanupInput) {
      cleanupInput.required = cleanup;
      if (!cleanup) cleanupInput.value = '';
    }
    if (submit) {
      submit.lastChild.textContent = cleanup ? ' Clean storage and database' :
        selected === 'export' ? ' Create secure export' : ' Create verified snapshot';
    }
    panels.find((panel) => !panel.hidden)?.querySelector('h2')?.focus?.({ preventScroll: true });
  }

  wizard.addEventListener('click', (event) => {
    const next = event.target.closest('[data-wizard-next]');
    const back = event.target.closest('[data-wizard-back]');
    if (next) {
      if (step === 1 && !operation()) {
        const first = wizard.querySelector('input[name="operation"]');
        first?.focus();
        window.MIFPUI?.showToast('Select one operation before continuing.', 'warning');
        return;
      }
      step = Math.min(3, step + 1);
      render();
    } else if (back) {
      step = Math.max(1, step - 1);
      render();
    }
  });
  wizard.addEventListener('change', (event) => {
    if (event.target.matches('input[name="operation"]')) render();
  });

  wizard.addEventListener('submit', async (event) => {
    if (operation() !== 'export' && operation() !== 'excel') return;
    event.preventDefault();
    if (!wizard.reportValidity()) return;
    submit.disabled = true;
    window.MIFPLog?.info('safety.export_started', { operation: operation() });

    try {
      const response = await fetch(wizard.action, {
        method: 'POST',
        body: new FormData(wizard),
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      const contentType = response.headers.get('Content-Type') || '';
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(response.status === 403
          ? 'Authorization failed. Check the administrator password.'
          : contentType.includes('application/zip')
            ? 'The secure export was not authorized or could not be created.'
            : contentType.includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
              ? 'Excel export ready for download.'
              : 'An unexpected error occurred.');
      }

      if (contentType.includes('application/zip') || contentType.includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) {
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^";]+)"?/i);
        const filename = match?.[1] || (operation() === 'excel' ? 'mifp-users.xlsx' : 'mifp-secure-export.zip');
        const url = URL.createObjectURL(response.response);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        window.MIFPUI?.showToast(`${operation()} export ready.`, 'success');
        window.MIFPLog?.info('safety.export_completed', { operation: operation(), filename: filename });
      } else {
        const jobData = await response.json();
        const jobId = jobData.job_id;
        const statusUrl = jobData.status_url;
        const downloadUrl = jobData.download_url;

        showProgress();
        pollExportStatus(jobId, statusUrl, downloadUrl);
      }
    } catch (error) {
      window.MIFPUI?.showToast(error.message || 'The protected operation failed.', 'error');
      window.MIFPLog?.error('safety.export_failed', { error: error });
    } finally {
      submit.disabled = false;
      window.MIFPUI?.clearFormLoading(wizard);
    }
  });

  function showProgress() {
    const container = document.querySelector('[data-safety-progress]');
    if (!container) return;
    container.innerHTML = `
      <div class="safety-progress-bar">
        <div class="safety-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="safety-progress-fill"></div>
        </div>
        <div class="safety-progress-percent">0%</div>
      </div>
      <div class="safety-progress-details">
        <div class="safety-progress-phase">Collecting records…</div>
        <div class="safety-progress-metrics">
          <span class="safety-progress-metric">
            <span class="safety-progress-label">Records</span>
            <span class="safety-progress-value">0</span>
          </span>
          <span class="safety-progress-metric">
            <span class="safety-progress-label">Assets</span>
            <span class="safety-progress-value">0</span>
          </span>
          <span class="safety-progress-metric">
            <span class="safety-progress-label">Errors</span>
            <span class="safety-progress-value">0</span>
          </span>
          <span class="safety-progress-metric" data-safety-size hidden>
            <span class="safety-progress-label">Size</span>
            <span class="safety-progress-value">—</span>
          </span>
        </div>
        <div class="safety-progress-counts" data-safety-counts hidden></div>
      </div>
    `;
    container.hidden = false;
  }

  function hideProgress() {
    const container = document.querySelector('[data-safety-progress]');
    if (container) container.hidden = true;
  }

  const COUNT_LABELS = {
    member: 'Members', news: 'News', event: 'Events', publication: 'Publications',
    research_area: 'Research areas', sponsor: 'Sponsors', page: 'Pages',
  };

  function sizeLabel(bytes) {
    if (!bytes || bytes <= 0) return '—';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function renderCounts(counts) {
    const container = document.querySelector('[data-safety-progress] [data-safety-counts]');
    if (!container) return;
    container.replaceChildren();
    Object.keys(counts || {}).forEach(function (type) {
      const value = Number(counts[type]) || 0;
      if (value <= 0) return;
      const chip = document.createElement('span');
      chip.className = 'safety-progress-chip';
      chip.innerHTML = `<b>${value}</b> ${COUNT_LABELS[type] || type}`;
      container.appendChild(chip);
    });
    container.hidden = container.childElementCount === 0;
  }

  function updateProgress(progress) {
    const trackElement = document.querySelector('[data-safety-progress] .safety-progress-track');
    const fillElement = document.querySelector('[data-safety-progress] .safety-progress-fill');
    const percentElement = document.querySelector('[data-safety-progress] .safety-progress-percent');
    const phaseElement = document.querySelector('[data-safety-progress] .safety-progress-phase');
    const recordsElement = document.querySelector('[data-safety-progress] .safety-progress-metric:nth-child(1) .safety-progress-value');
    const assetsElement = document.querySelector('[data-safety-progress] .safety-progress-metric:nth-child(2) .safety-progress-value');
    const errorsElement = document.querySelector('[data-safety-progress] .safety-progress-metric:nth-child(3) .safety-progress-value');
    const sizeMetric = document.querySelector('[data-safety-progress] [data-safety-size]');
    const sizeValue = document.querySelector('[data-safety-progress] [data-safety-size] .safety-progress-value');
    const percent = Math.min(100, Math.max(0, Number(progress.percent) || 0));
    
    if (fillElement) fillElement.style.width = `${percent}%`;
    if (trackElement) trackElement.setAttribute('aria-valuenow', String(percent));
    if (percentElement) percentElement.textContent = `${percent}%`;
    if (phaseElement) phaseElement.textContent = progress.message || progress.phase || 'Collecting records…';
    if (recordsElement) recordsElement.textContent = progress.records || 0;
    if (assetsElement) assetsElement.textContent = progress.total_assets != null && progress.total_assets > 0
      ? `${progress.assets || 0} / ${progress.total_assets}`
      : (progress.assets || 0);
    if (errorsElement) errorsElement.textContent = progress.errors || 0;
    if (sizeValue && progress.bytes != null && progress.bytes > 0) {
      sizeValue.textContent = sizeLabel(progress.bytes);
      if (sizeMetric) sizeMetric.hidden = false;
    }
    renderCounts(progress.counts);
  }

  async function pollExportStatus(jobId, statusUrl, downloadUrl) {
    const maxAttempts = 60;
    const interval = 1000;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      try {
        const response = await fetch(statusUrl, {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        const data = await response.json();
        
        if (data.ok && (data.status === 'ready' || data.status === 'completed')) {
          updateProgress({
            percent: 100,
            message: 'Export ready',
            records: data.records || 0,
            assets: data.assets || 0,
            errors: data.errors || 0,
            counts: data.counts,
            total_assets: data.total_assets,
            bytes: data.bytes,
          });
          setTimeout(() => {
            window.location.href = downloadUrl;
          }, 500);
          return;
        }

        if (data.ok && data.status === 'failed') {
          hideProgress();
          window.MIFPUI?.showToast('Export failed: ' + (data.error || 'Unknown error'), 'error');
          return;
        }

        if (data.ok && (data.status === 'running' || data.status === 'queued')) {
          updateProgress({
            percent: data.percent || 0,
            message: data.message || data.phase || 'Processing…',
            records: data.records || 0,
            assets: data.assets || 0,
            errors: data.errors || 0,
            counts: data.counts,
            total_assets: data.total_assets,
            bytes: data.bytes,
          });
        }
      } catch (error) {
        window.MIFPUI?.showToast('Failed to check export status', 'error');
        return;
      }

      await new Promise(resolve => setTimeout(resolve, interval));
    }

    hideProgress();
    window.MIFPUI?.showToast('Export timed out', 'warning');
  }

  render();
})();

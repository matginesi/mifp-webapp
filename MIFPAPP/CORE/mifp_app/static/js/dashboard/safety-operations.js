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
      window.MIFPUI?.showToast(` ${operation()} export ready.', 'success');
      window.MIFPLog?.info('safety.export_completed', { operation: operation(), filename: filename });
    } catch (error) {
      window.MIFPUI?.showToast(error.message || 'The protected operation failed.', 'error');
      window.MIFPLog?.error('safety.export_failed', { error: error });
    } finally {
      submit.disabled = false;
      window.MIFPUI?.clearFormLoading(wizard);
    }
  });
  render();
})();

(() => {
  'use strict';
  const wizard = document.querySelector('[data-event-import-wizard]');
  if (!wizard) return;
  let step = Number(wizard.dataset.startStep || 1);
  const panels = [...wizard.querySelectorAll('[data-wizard-panel]')];
  const markers = [...wizard.querySelectorAll('[data-wizard-marker]')];

  function show(next) {
    step = next;
    panels.forEach((panel) => panel.classList.toggle('is-active', Number(panel.dataset.wizardPanel) === step));
    markers.forEach((marker) => {
      const markerStep = Number(marker.dataset.wizardMarker);
      marker.classList.toggle('is-active', markerStep === step);
      marker.classList.toggle('is-complete', markerStep < step);
    });
  }

  wizard.addEventListener('click', (event) => {
    const next = event.target.closest('[data-wizard-next]');
    const back = event.target.closest('[data-wizard-back]');
    if (next) {
      const current = panels.find((panel) => Number(panel.dataset.wizardPanel) === step);
      const required = current?.querySelectorAll('input:invalid, select:invalid') || [];
      if (required.length) { required[0].reportValidity(); return; }
      show(Math.min(5, step + 1));
    }
    if (back) show(Math.max(2, step - 1));
  });

  wizard.querySelectorAll('[data-package-input]').forEach((input) => {
    input.addEventListener('change', () => {
      const label = input.closest('.event-dropzone')?.querySelector('[data-file-label]');
      if (label) label.textContent = input.files?.[0]?.name || 'No file selected';
    });
  });

  const destination = wizard.querySelector('[data-destination]');
  const preview = wizard.querySelector('[data-url-preview]');
  const review = wizard.querySelector('[data-review-url]');
  function updatePreview() {
    if (!destination || !preview) return;
    const path = destination.value.replace(/^\/+|\/+$/g, '');
    const value = `${preview.dataset.host}/${path}/`;
    preview.textContent = value;
    if (review) review.textContent = value;
  }
  destination?.addEventListener('input', updatePreview);
  wizard.querySelector('[data-existing-path]')?.addEventListener('change', (event) => {
    if (event.target.value && destination) destination.value = event.target.value;
    updatePreview();
  });

  const forthcomingReview = wizard.querySelector('[data-review-forthcoming]');
  wizard.querySelectorAll('[data-forthcoming]').forEach((input) => {
    input.addEventListener('change', () => {
      if (!forthcomingReview || !input.checked) return;
      forthcomingReview.textContent = input.value === '1' ? 'Yes — homepage Forthcoming' : 'No';
    });
  });

  wizard.querySelector('[data-import-form]')?.addEventListener('submit', (event) => {
    if (!event.currentTarget.reportValidity()) return;
    show(6);
  });
  show(step);
})();

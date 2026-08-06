(() => {
  'use strict';
  const log = window.MIFPLog || { info() {}, warn() {}, error() {} };
  document.querySelectorAll('[data-conference-build]').forEach((link) => {
    link.addEventListener('click', () => {
      log.info('conference.build_started', { conference_id: location.pathname.split('/').pop() });
    });
  });
  document.querySelectorAll('.conference-import, .conference-asset-upload').forEach((form) => {
    form.addEventListener('submit', () => {
      log.info('conference.import_started', {
        kind: form.querySelector('[name="people_file"]') ? 'people' : 'asset',
        files: form.querySelector('[name="assets"]')?.files?.length || 0,
      });
    });
  });

  document.querySelectorAll('[data-conference-create-wizard]').forEach((form) => {
    const steps = [...form.querySelectorAll('[data-conference-create-step]')];
    const markers = [...form.querySelectorAll('[data-conference-step-marker]')];
    const back = form.querySelector('[data-conference-create-back]');
    const next = form.querySelector('[data-conference-create-next]');
    const submit = form.querySelector('[data-conference-create-submit]');
    let current = 0;
    const render = () => {
      steps.forEach((step, index) => {
        step.hidden = index !== current;
        step.classList.toggle('is-active', index === current);
      });
      markers.forEach((marker, index) => {
        marker.classList.toggle('is-active', index === current);
        marker.classList.toggle('is-complete', index < current);
      });
      back.disabled = current === 0;
      next.classList.toggle('d-none', current === steps.length - 1);
      submit.classList.toggle('d-none', current !== steps.length - 1);
    };
    next?.addEventListener('click', () => {
      const invalid = steps[current]?.querySelector(':invalid');
      if (invalid) {
        invalid.reportValidity();
        log.warn('conference.create_step_invalid', { step: current + 1 });
        return;
      }
      current = Math.min(current + 1, steps.length - 1);
      render();
      steps[current]?.querySelector('input,select,textarea')?.focus();
      log.info('conference.create_step', { step: current + 1 });
    });
    back?.addEventListener('click', () => {
      current = Math.max(0, current - 1);
      render();
    });
    form.addEventListener('submit', () => {
      log.info('conference.create_submitted', { step: current + 1 });
    });
    form.closest('.modal')?.addEventListener('hidden.bs.modal', () => {
      current = 0;
      render();
    });
    render();
  });

  const countdownList = document.querySelector('[data-countdown-list]');
  const addCountdown = document.querySelector('[data-countdown-add]');
  function countdownRow() {
    const row = document.createElement('div');
    row.className = 'conference-countdown-row';
    row.innerHTML = [
      '<input class="form-control form-control-sm" name="countdown_label" placeholder="Important date label" required>',
      '<input class="form-control form-control-sm" name="countdown_date" placeholder="2027-05-10T09:00:00+02:00" required>',
      '<input class="form-control form-control-sm" name="countdown_end_date" placeholder="Optional end date">',
      '<select class="form-select form-select-sm" name="countdown_type"><option value="deadline">Deadline</option><option value="event">Event</option></select>',
      '<button class="btn btn-mini btn-outline-danger" type="button" data-countdown-remove aria-label="Remove date"><i class="bi bi-trash"></i></button>',
    ].join('');
    return row;
  }
  addCountdown?.addEventListener('click', () => {
    const row = countdownRow();
    countdownList?.appendChild(row);
    row.querySelector('input')?.focus();
    log.info('conference.countdown_added', {});
  });
  countdownList?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-countdown-remove]');
    if (!button) return;
    button.closest('.conference-countdown-row')?.remove();
    log.info('conference.countdown_removed', {});
  });
})();

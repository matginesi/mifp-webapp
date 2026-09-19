(function () {
  'use strict';

  let config = {};
  try { config = JSON.parse(document.body && document.body.dataset.regConfig || '{}'); }
  catch (error) { console.error('[MIFP][REGFORM][ERROR][config] Invalid client configuration', error); }
  const appearance = config.appearance || {};
  const runtime = config.runtime || {};
  const formConfig = config.form || {};
  const levels = { trace: 10, debug: 20, info: 30, warn: 40, error: 50, silent: 99 };

  function threshold() {
    const selected = String(runtime.debug === true ? (runtime.debug_log_level || 'debug') : (runtime.log_level || 'info')).toLowerCase();
    return levels[selected] || levels.info;
  }

  function log(level, scope, message, data) {
    const normalized = levels[level] ? level : 'info';
    if (levels[normalized] < threshold()) return;
    const prefix = `[${runtime.log_prefix || 'MIFP'}][REGFORM][${normalized.toUpperCase()}][${scope}]`;
    const method = normalized === 'error' ? 'error' : normalized === 'warn' ? 'warn' : normalized === 'debug' || normalized === 'trace' ? 'debug' : 'info';
    if (data === undefined) console[method](`${prefix} ${message}`);
    else console[method](`${prefix} ${message}`, data);
  }

  function findById(items, id) {
    return (Array.isArray(items) ? items : []).find((item) => String(item && item.id || '').toLowerCase() === String(id || '').toLowerCase()) || null;
  }

  function sanitizeId(value) {
    const safe = String(value || '').replace(/[^A-Za-z0-9_-]/g, '');
    return safe || 'default';
  }

  function applyAppearance() {
    const root = document.documentElement;
    const debug = runtime.debug === true;
    const remember = appearance.remember_preferences !== false;
    let themeId = appearance.default_theme || '';
    let paletteId = appearance.default_palette || '';

    try {
      if (debug && remember) {
        themeId = localStorage.getItem('mifp-debug-theme') || themeId;
        paletteId = localStorage.getItem('mifp-debug-palette') || paletteId;
      }
    } catch (error) {
      log('warn', 'appearance', 'Local appearance preference could not be read', { error: error.message });
    }

    const themes = Array.isArray(appearance.themes) ? appearance.themes : [];
    const palettes = Array.isArray(appearance.palettes) ? appearance.palettes : [];
    const theme = findById(themes, themeId) || findById(themes, appearance.default_theme) || themes[0];
    const palette = findById(palettes, paletteId) || findById(palettes, appearance.default_palette) || palettes[0];

    Array.from(root.classList).forEach((name) => {
      if (name.startsWith('mifp-theme-') || name.startsWith('mifp-palette-')) root.classList.remove(name);
    });
    if (theme) {
      root.classList.add(`mifp-theme-${sanitizeId(theme.id)}`);
      root.dataset.theme = theme.id || '';
    }
    if (palette) {
      root.classList.add(`mifp-palette-${sanitizeId(palette.id)}`);
      root.dataset.palette = palette.id || '';
    }

    log('info', 'appearance', 'Conference appearance applied', {
      theme: root.dataset.theme || 'default',
      palette: root.dataset.palette || 'default'
    });
  }

  function initForm() {
    const form = document.getElementById('registrationForm');
    const submit = document.getElementById('registrationSubmit');
    const overlay = document.getElementById('submissionOverlay');
    if (!form) {
      if (formConfig.success === true) log('info', 'submission', 'Registration confirmation page displayed');
      return;
    }

    const fileInput = form.querySelector('input[type="file"][name="proof_of_payment"]');
    const maxBytes = Math.max(1, Number(formConfig.max_upload_mb || 5)) * 1024 * 1024;
    const allowedTypes = new Set(['application/pdf', 'image/jpeg', 'image/png']);
    const allowedExtensions = new Set(['pdf', 'jpg', 'jpeg', 'png']);

    function setSubmitting(active) {
      form.classList.toggle('is-submitting', active);
      if (submit) submit.disabled = active || formConfig.registration_open !== true;
      if (overlay) {
        overlay.classList.toggle('active', active);
        overlay.setAttribute('aria-hidden', active ? 'false' : 'true');
      }
      document.body.classList.toggle('registration-submitting', active);
    }

    function validateFile() {
      if (!fileInput || !fileInput.files || !fileInput.files[0]) return true;
      const file = fileInput.files[0];
      const extension = String(file.name || '').split('.').pop().toLowerCase();
      let message = '';
      if (file.size > maxBytes) message = `The proof of payment must be smaller than ${formConfig.max_upload_mb || 5} MB.`;
      else if ((file.type && !allowedTypes.has(file.type)) || (!file.type && !allowedExtensions.has(extension))) message = 'Only PDF, JPEG and PNG files are accepted.';
      fileInput.setCustomValidity(message);
      log(message ? 'warn' : 'debug', 'upload', message ? 'Proof of payment rejected by client validation' : 'Proof of payment selected', {
        type: file.type || extension || 'unknown',
        size_bytes: file.size
      });
      return message === '';
    }

    if (fileInput) fileInput.addEventListener('change', validateFile);

    form.addEventListener('invalid', (event) => {
      const field = event.target && event.target.name ? event.target.name : 'unknown';
      log('warn', 'validation', 'Field failed client validation', { field });
    }, true);

    form.addEventListener('submit', (event) => {
      validateFile();
      if (!form.checkValidity()) {
        event.preventDefault();
        setSubmitting(false);
        const invalidFields = Array.from(form.querySelectorAll(':invalid')).map((node) => node.name || node.id || node.tagName.toLowerCase());
        log('warn', 'submission', 'Submission blocked by client validation', { fields: invalidFields });
        form.reportValidity();
        const first = form.querySelector(':invalid');
        if (first && typeof first.focus === 'function') first.focus({ preventScroll: false });
        return;
      }

      setSubmitting(true);
      log('info', 'submission', 'Registration submission started');
    });

    window.addEventListener('pageshow', () => setSubmitting(false));

    const serverErrors = document.querySelectorAll('.field-error, .notice-danger').length;
    if (serverErrors > 0 || formConfig.has_errors === true) {
      log('warn', 'submission', 'Server returned registration errors', { visible_errors: serverErrors });
      const firstError = document.querySelector('[aria-invalid="true"], .notice-danger');
      if (firstError && typeof firstError.scrollIntoView === 'function') firstError.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      log('info', 'form', 'Registration form ready', { registration_open: formConfig.registration_open === true });
    }
  }

  window.addEventListener('error', (event) => log('error', 'runtime', 'Unhandled JavaScript error', { message: event.message || 'unknown error' }));
  window.addEventListener('unhandledrejection', (event) => log('error', 'runtime', 'Unhandled promise rejection', { message: String(event.reason && event.reason.message || event.reason || 'unknown error') }));

  applyAppearance();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initForm, { once: true });
  else initForm();
})();

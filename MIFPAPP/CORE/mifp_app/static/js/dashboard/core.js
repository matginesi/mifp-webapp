(() => {
"use strict";

/* Dashboard shared components */
/* ── MIFP Admin — Common JavaScript ───────────────────────────
   Handles: confirm dialog, toast, form loading,
   asset picker, password toggle, page loader (single mechanism).
   ──────────────────────────────────────────────────────────── */

/* ── Toast Notifications ───────────────────────────────────── */
const TOAST_ICONS = {
  success: 'bi-check-circle-fill',
  error: 'bi-x-circle-fill',
  warning: 'bi-exclamation-triangle-fill',
  info: 'bi-info-circle-fill',
};

function dashboardLog(level, eventName, details) {
  const method = level === 'warning' ? 'warn' : level;
  if (window.MIFPLog && typeof window.MIFPLog[method] === 'function') {
    window.MIFPLog[method](eventName, details);
  }
}

function safeLogPath(value) {
  if (window.MIFPLog && typeof window.MIFPLog.safePath === 'function') {
    return window.MIFPLog.safePath(value);
  }
  return String(value || '').split('?')[0].slice(0, 500);
}

function logToastToConsole(message, type) {
  dashboardLog(type === 'warning' ? 'warn' : type, 'ui.toast', {
    type: type,
    message: String(message ?? ''),
  });
}

function showToast(message, type) {
  type = type || 'info';
  logToastToConsole(message, type);
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'toast-notification toast-' + type;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  const iconEl = document.createElement('i');
  iconEl.className = 'bi ' + (TOAST_ICONS[type] || TOAST_ICONS.info);
  const msgEl = document.createElement('span');
  msgEl.textContent = message;
  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Dismiss notification');
  closeBtn.addEventListener('click', function () { el.remove(); });
  el.appendChild(iconEl);
  el.appendChild(msgEl);
  el.appendChild(closeBtn);
  container.appendChild(el);
  setTimeout(function () {
    el.classList.add('toast-out');
    setTimeout(function () { el.remove(); }, 220);
  }, 4000);
}

/* ── Confirm Dialog ────────────────────────────────────────── */
const confirmDialog = {
  modal: null, okBtn: null, cancelBtn: null, messageEl: null, resolve: null,
  init() {
    this.modal = document.getElementById('confirmDialog');
    if (!this.modal || !window.bootstrap) return;
    this.okBtn = document.getElementById('confirmOk');
    this.cancelBtn = document.getElementById('confirmCancel');
    this.messageEl = document.getElementById('confirmMessage');
    this.modal.addEventListener('hidden.bs.modal', () => {
      if (this.resolve) { this.resolve(false); this.resolve = null; }
    });
    this.okBtn.addEventListener('click', () => {
      if (this.resolve) { this.resolve(true); this.resolve = null; }
      bootstrap.Modal.getInstance(this.modal).hide();
    });
    this.cancelBtn.addEventListener('click', () => {
      if (this.resolve) { this.resolve(false); this.resolve = null; }
    });
  },
  show(message) {
    return new Promise((resolve) => {
      this.resolve = resolve;
      if (this.messageEl) this.messageEl.textContent = message;
      if (this.modal && window.bootstrap) new bootstrap.Modal(this.modal).show();
      else resolve(true);
    });
  },
};

/* ── Image load error handler ──────────────────────────────── */
document.addEventListener('error', function (ev) {
  var img = ev.target.closest('.asset-thumb img[data-error-fallback]');
  if (!img) return;
  ev.preventDefault();
  img.onerror = null;
  var fallbackUrl = img.getAttribute('data-error-fallback');
  var parent = img.parentElement;
  var link = document.createElement('a');
  link.href = fallbackUrl;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  var fallbackIcon = document.createElement('i');
  fallbackIcon.className = 'bi bi-link-45deg';
  fallbackIcon.setAttribute('aria-hidden', 'true');
  link.appendChild(fallbackIcon);
  link.setAttribute('aria-label', 'Open external asset');
  if (parent) {
    parent.replaceChildren(link);
  }
}, true);

/* ── Form Loading State ────────────────────────────────────── */
function setFormLoading(form) {
  if (!form || form.dataset.noSpinner === '1') return;
  const btn = form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
  if (!btn || btn.classList.contains('is-loading')) return;
  btn.classList.add('is-loading');
  btn.setAttribute('aria-busy', 'true');
  if (btn.tagName === 'BUTTON') {
    btn.dataset.originalHtml = btn.innerHTML;
    const label = btn.textContent.trim() || 'Loading';
    btn.replaceChildren();
    const spinner = document.createElement('span');
    spinner.className = 'btn-spinner';
    spinner.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = label;
    btn.appendChild(spinner);
    btn.appendChild(text);
  }
}

function clearFormLoading(form) {
  if (!form) return;
  const btn = form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
  if (!btn) return;
  btn.classList.remove('is-loading');
  btn.removeAttribute('aria-busy');
  btn.disabled = false;
  if (btn.tagName === 'BUTTON' && btn.dataset.originalHtml) {
    btn.innerHTML = btn.dataset.originalHtml;
    delete btn.dataset.originalHtml;
  }
}

/* ── Operation progress primitives ─────────────────────────────
   Shared by every long-running dashboard operation (archive import, data
   transfer). Both callers used to carry their own copy, which is how the
   transfer bar lost its aria-valuenow update; one implementation keeps the
   accessibility contract identical everywhere.
   ──────────────────────────────────────────────────────────── */
function elapsedLabel(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
}

// `track` is the element carrying role="progressbar", `fill` is the bar inside
// it. Returns the clamped value so callers can reuse it for their own labels.
function setProgressBar(track, fill, percentEl, value) {
  const safe = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
  if (track) {
    track.classList.remove('is-loading');
    track.setAttribute('aria-valuenow', String(safe));
  }
  if (fill) fill.style.width = safe + '%';
  if (percentEl) percentEl.textContent = safe + '%';
  return safe;
}

/* ── Asset Picker ──────────────────────────────────────────── */
let assetPickerResolve = null;
let pickerFocusIndex = -1;
let assetSearchController = null;

function pickerMessage(container, message) {
  const item = document.createElement('div');
  item.className = 'asset-picker-empty';
  item.textContent = message;
  container.replaceChildren(item);
}

function storageStatusBadge(status) {
  if (!status) return '';
  const map = {
    local: { cls: 'storage-badge storage-local', label: 'Local' },
    external: { cls: 'storage-badge storage-external', label: 'External' },
    download_failed: { cls: 'storage-badge storage-failed', label: 'Download Failed' },
  };
  const info = map[status] || { cls: 'storage-badge', label: status };
  const badge = document.createElement('span');
  badge.className = info.cls;
  badge.textContent = info.label;
  return badge;
}

function openAssetPicker(fieldId, kind) {
  return new Promise((resolve) => {
    assetPickerResolve = resolve;
    pickerFocusIndex = -1;
    const modal = document.getElementById('assetPickerModal');
    if (!modal || !window.bootstrap) return;
    modal.dataset.targetField = fieldId;
    const query = document.getElementById('assetPickerQuery');
    const kindSel = document.getElementById('assetPickerKind');
    const results = document.getElementById('assetPickerResults');
    if (kind && kindSel) kindSel.value = kind;
    if (query) query.value = '';
    if (results) pickerMessage(results, 'Loading assets…');
    new bootstrap.Modal(modal).show();
    // Auto-load first batch of assets
    searchAssets();
  });
}

function searchAssets() {
  const query = document.getElementById('assetPickerQuery')?.value || '';
  const kind = document.getElementById('assetPickerKind')?.value || '';
  const results = document.getElementById('assetPickerResults');
  const status = document.getElementById('assetPickerStatus');
  if (!results) return;
  if (assetSearchController) assetSearchController.abort();
  assetSearchController = new AbortController();
  const controller = assetSearchController;
  pickerMessage(results, 'Searching…');
  if (status) status.textContent = 'Searching…';
  pickerFocusIndex = -1;
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (kind) params.set('kind', kind);
  window.MIFP.request(
    (document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/assets/search.json?' + params,
    { signal: controller.signal }
  )
    .then(result => {
      const data = result.data;
      if (!Array.isArray(data)) throw new Error('The asset search returned an invalid response.');
      // Clear results
      results.replaceChildren();
      if (!data || data.length === 0) {
        const emptyEl = document.createElement('div');
        emptyEl.className = 'asset-picker-empty';
        emptyEl.textContent = 'No assets found.';
        results.appendChild(emptyEl);
        if (status) status.textContent = '0 results';
        return;
      }
      // Build result items using DOM API for security
      data.forEach(function (a) {
        const item = document.createElement('div');
        item.className = 'asset-picker-item';
        item.setAttribute('data-id', a.id);
        item.setAttribute('tabindex', '0');

        // Thumb / icon area
        var thumb = document.createElement('div');
        thumb.className = 'asset-picker-thumb';
        if (a.kind === 'image') {
          var imgSrc = a.public_url || a.image_url || a.source_url || '';
          if (imgSrc && (a.storage_status === 'local' || a.local_exists)) {
            var img = document.createElement('img');
            img.src = imgSrc;
            img.alt = '';
            img.loading = 'lazy';
            img.width = 45;
            img.height = 45;
            img.onerror = function () {
              this.onerror = null;
              this.remove();
              var icon = document.createElement('i');
              icon.className = 'bi bi-link-45deg';
              icon.setAttribute('aria-hidden', 'true');
              thumb.appendChild(icon);
            };
            thumb.appendChild(img);
          } else if (imgSrc) {
            // Remote image – use icon fallback for security/speed
            var icon = document.createElement('i');
            icon.className = 'bi bi-link-45deg';
            thumb.appendChild(icon);
          } else {
            var icon = document.createElement('i');
            icon.className = 'bi bi-image';
            thumb.appendChild(icon);
          }
        } else if (a.kind === 'pdf') {
          var icon = document.createElement('i');
          icon.className = 'bi bi-file-earmark-pdf';
          thumb.appendChild(icon);
        } else if (a.kind === 'video') {
          var icon = document.createElement('i');
          icon.className = 'bi bi-film';
          thumb.appendChild(icon);
        } else {
          var icon = document.createElement('i');
          icon.className = 'bi bi-file-earmark';
          thumb.appendChild(icon);
        }
        item.appendChild(thumb);

        // Info area
        var info = document.createElement('div');
        info.className = 'asset-picker-info';

        // Filename line
        var nameRow = document.createElement('div');
        nameRow.className = 'asset-picker-name-row';
        var nameB = document.createElement('b');
        nameB.textContent = a.filename || 'untitled';
        nameRow.appendChild(nameB);

        // Storage status badge
        if (a.storage_status) {
          nameRow.appendChild(storageStatusBadge(a.storage_status));
        }
        // External badge
        if (a.is_external) {
          var extBadge = document.createElement('span');
          extBadge.className = 'storage-badge storage-external';
          extBadge.textContent = 'External';
          nameRow.appendChild(extBadge);
        }
        // Download failed badge
        if (a.storage_status === 'download_failed') {
          var failBadge = document.createElement('span');
          failBadge.className = 'storage-badge storage-failed';
          failBadge.textContent = 'Download Failed';
          nameRow.appendChild(failBadge);
        }
        info.appendChild(nameRow);

        // Detail line: ID · kind · size
        var detailSpan = document.createElement('span');
        detailSpan.className = 'tiny muted';
        var detailParts = [];
        if (a.id) detailParts.push('#' + a.id);
        if (a.kind) detailParts.push(a.kind);
        if (a.size) detailParts.push(window.MIFP.formatBytes(a.size, ''));
        if (a.usage_count !== undefined && a.usage_count !== null) detailParts.push('used ' + a.usage_count + '\u00d7');
        detailSpan.textContent = detailParts.join(' \u00b7 ');
        info.appendChild(detailSpan);

        // Path / source_url line (truncated)
        if (a.path) {
          var pathSpan = document.createElement('span');
          pathSpan.className = 'tiny muted asset-picker-path';
          pathSpan.textContent = a.path;
          pathSpan.title = a.path;
          info.appendChild(pathSpan);
        } else if (a.source_url) {
          var urlSpan = document.createElement('span');
          urlSpan.className = 'tiny muted asset-picker-path';
          urlSpan.textContent = a.source_url;
          urlSpan.title = a.source_url;
          info.appendChild(urlSpan);
        }

        item.appendChild(info);

        // Select button
        var selectBtn = document.createElement('button');
        selectBtn.className = 'btn btn-primary btn-sm asset-picker-select';
        selectBtn.setAttribute('data-id', a.id);
        selectBtn.textContent = 'Select';
        item.appendChild(selectBtn);

        results.appendChild(item);
      });
      if (status) status.textContent = data.length + ' result' + (data.length !== 1 ? 's' : '');
    })
    .catch(error => {
      if (error.name === 'AbortError') return;
      pickerMessage(results, error.message || 'Search failed.');
      if (status) status.textContent = 'Search failed';
    })
    .finally(() => {
      if (assetSearchController === controller) assetSearchController = null;
    });
}

function selectAssetId(id) {
  const fieldId = document.getElementById('assetPickerModal')?.dataset?.targetField;
  const field = fieldId ? document.getElementById(fieldId) : null;
  if (field && id) {
    field.value = id;
    field.dispatchEvent(new Event('change', { bubbles: true }));
  }
  if (assetPickerResolve) { assetPickerResolve(id); assetPickerResolve = null; }
  const modal = document.getElementById('assetPickerModal');
  if (modal && window.bootstrap) bootstrap.Modal.getInstance(modal)?.hide();
}

async function createPickerAsset(form) {
  const status = document.getElementById('assetPickerStatus');
  if (status) status.textContent = 'Saving asset…';
  const result = await window.MIFP.request((document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/assets/create.json', {
    method: 'POST',
    body: new FormData(form),
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  const data = result.data || {};
  if (!data.id) throw new Error('Asset creation failed.');
  if (status) status.textContent = 'Asset #' + data.id + ' selected';
  selectAssetId(data.id);
}

/* ── Page Loader — single DOMContentLoaded mechanism ───────── */
function hidePageLoaders() {
  document.querySelectorAll('.page-loader').forEach(function (loader) {
    loader.classList.add('done');
    setTimeout(function () { loader.style.display = 'none'; }, 300);
  });
}

/* ── Event Listeners (consolidated) ────────────────────────── */
document.addEventListener('DOMContentLoaded', function () {
  confirmDialog.init();
  hidePageLoaders();
  var token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  if (token) {
    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function (form) {
      if (!form.querySelector('input[name="_csrf_token"]')) {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = '_csrf_token';
        input.value = token;
        form.prepend(input);
      }
    });
  }

  var selectAll = document.getElementById('joinSelectAll');
  if (selectAll) {
    var requestBoxes = document.querySelectorAll('input[name="request_ids"][form="joinBulkForm"]');
    selectAll.addEventListener('change', function () {
      requestBoxes.forEach(function (box) {
        box.checked = selectAll.checked;
      });
    });
    requestBoxes.forEach(function (box) {
      box.addEventListener('change', function () {
        selectAll.checked = Array.prototype.every.call(requestBoxes, function (b) { return b.checked; });
      });
    });
  }
});

document.addEventListener('click', async function (ev) {
  // Asset picker open
  const pickerBtn = ev.target.closest('[data-picker]');
  if (pickerBtn) {
    ev.preventDefault();
    openAssetPicker(pickerBtn.dataset.picker, pickerBtn.dataset.pickerKind || '');
  }

  // Asset picker select
  const selectBtn = ev.target.closest('.asset-picker-select');
  if (selectBtn) {
    ev.preventDefault();
    selectAssetId(selectBtn.dataset.id);
  }

  // Asset picker search
  if (ev.target.closest('#assetPickerSearchBtn')) {
    searchAssets();
  }

  const tab = ev.target.closest('[data-asset-tab]');
  if (tab) {
    ev.preventDefault();
    activateAssetTab(tab);
  }

  // Export dropdown toggle
  const dropdownToggle = ev.target.closest('[data-export-toggle]');
  if (dropdownToggle) {
    ev.preventDefault();
    ev.stopPropagation();
    dropdownToggle.closest('.export-dropdown')?.classList.toggle('show');
  }

  // Close export dropdown on outside click
  if (!ev.target.closest('.export-dropdown')) {
    document.querySelectorAll('.export-dropdown.show').forEach(function (el) { el.classList.remove('show'); });
  }
});

// Asset picker Enter key search + keyboard navigation
document.addEventListener('keydown', function (ev) {
  const assetTab = ev.target.closest('[data-asset-tab]');
  if (assetTab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) {
    const tabs = Array.from(assetTab.closest('[role="tablist"]').querySelectorAll('[data-asset-tab]'));
    const current = tabs.indexOf(assetTab);
    ev.preventDefault();
    const next = ev.key === 'Home' ? 0 : ev.key === 'End' ? tabs.length - 1 :
      (current + (ev.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
    activateAssetTab(tabs[next]);
    return;
  }
  if (ev.key === 'Enter' && ev.target.closest('#assetPickerQuery')) {
    searchAssets();
    return;
  }

  // Arrow key navigation in asset picker results
  var resultsEl = document.getElementById('assetPickerResults');
  if (!resultsEl) return;
  var items = resultsEl.querySelectorAll('.asset-picker-item');
  if (items.length === 0) return;

  // Only handle arrow keys when picker is open
  var modal = document.getElementById('assetPickerModal');
  if (!modal || !modal.classList.contains('show')) return;

  if (ev.key === 'ArrowDown') {
    ev.preventDefault();
    pickerFocusIndex = Math.min(pickerFocusIndex + 1, items.length - 1);
    items[pickerFocusIndex].focus();
    items[pickerFocusIndex].classList.add('is-focused');
    if (pickerFocusIndex > 0) items[pickerFocusIndex - 1].classList.remove('is-focused');
  } else if (ev.key === 'ArrowUp') {
    ev.preventDefault();
    if (pickerFocusIndex > 0) items[pickerFocusIndex].classList.remove('is-focused');
    pickerFocusIndex = Math.max(pickerFocusIndex - 1, 0);
    items[pickerFocusIndex].focus();
    items[pickerFocusIndex].classList.add('is-focused');
  } else if (ev.key === 'Enter' && pickerFocusIndex >= 0 && pickerFocusIndex < items.length) {
    ev.preventDefault();
    var id = items[pickerFocusIndex].getAttribute('data-id');
    if (id) selectAssetId(id);
  }
});

function activateAssetTab(tab) {
  document.querySelectorAll('.asset-tab').forEach(function (item) {
    const active = item === tab;
    item.classList.toggle('is-active', active);
    item.setAttribute('aria-selected', active ? 'true' : 'false');
    item.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll('.asset-tab-panel').forEach(function (panel) {
    const active = panel.id === 'asset-tab-' + tab.dataset.assetTab;
    panel.classList.toggle('is-active', active);
    panel.hidden = !active;
  });
}

document.addEventListener('change', function (ev) {
  if (ev.target.matches('[data-auto-submit]') && ev.target.form) ev.target.form.requestSubmit();
});

/* ── Compact dashboard shell ──────────────────────────────── */
function initDashboardShell() {
  const shell = document.querySelector('[data-dashboard-shell]');
  const toggle = document.querySelector('[data-shell-toggle]');
  const closeControl = document.querySelector('[data-shell-close]');
  const sidebar = document.getElementById('dashboardSidebar');
  if (!shell || !toggle || !sidebar || shell.dataset.shellReady === '1') return;
  shell.dataset.shellReady = '1';
  const mobileQuery = window.matchMedia('(max-width: 768px)');

  function setExpanded(expanded) {
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    toggle.setAttribute('aria-label', expanded ? 'Collapse navigation' : 'Expand navigation');
  }

  function closeMobile(restoreFocus) {
    if (!shell.classList.contains('is-mobile-open')) return;
    shell.classList.remove('is-mobile-open');
    setExpanded(false);
    if (restoreFocus) toggle.focus();
  }

  function toggleShell() {
    if (mobileQuery.matches) {
      const opening = !shell.classList.contains('is-mobile-open');
      shell.classList.toggle('is-mobile-open', opening);
      setExpanded(opening);
      if (opening) {
        const activeLink = sidebar.querySelector('.sidebar-link.is-active, .sidebar-link');
        if (activeLink) activeLink.focus();
      }
      return;
    }
    const collapsed = !shell.classList.contains('is-collapsed');
    shell.classList.toggle('is-collapsed', collapsed);
    setExpanded(!collapsed);
    try {
      window.localStorage.setItem('mifp-dashboard-sidebar-collapsed', collapsed ? '1' : '0');
    } catch (_) {
      // Storage is optional; shell behavior remains available.
    }
  }

  if (!mobileQuery.matches) {
    let collapsed = false;
    try {
      collapsed = window.localStorage.getItem('mifp-dashboard-sidebar-collapsed') === '1';
    } catch (_) {
      collapsed = false;
    }
    shell.classList.toggle('is-collapsed', collapsed);
    setExpanded(!collapsed);
  } else {
    setExpanded(false);
  }

  toggle.addEventListener('click', toggleShell);
  if (closeControl) closeControl.addEventListener('click', function () { closeMobile(true); });
  sidebar.addEventListener('click', function (event) {
    if (mobileQuery.matches && event.target.closest('a')) closeMobile(false);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && mobileQuery.matches) closeMobile(true);
  });
  mobileQuery.addEventListener('change', function (event) {
    shell.classList.remove('is-mobile-open');
    if (event.matches) {
      setExpanded(false);
      return;
    }
    setExpanded(!shell.classList.contains('is-collapsed'));
  });
}

// Form submit handling (confirm + loading)
document.addEventListener('submit', function (ev) {
  dashboardLog('info', 'form.submit', {
    method: String(ev.target.method || 'GET').toUpperCase(),
    action: safeLogPath(ev.target.action || window.location.pathname),
    form: ev.target.id || ev.target.getAttribute('name') || undefined,
  });
  const pickerCreate = ev.target.closest('#assetPickerUploadForm, #assetPickerUrlForm');
  if (pickerCreate) {
    ev.preventDefault();
    createPickerAsset(pickerCreate).catch(function (err) {
      const status = document.getElementById('assetPickerStatus');
      if (status) status.textContent = err.message;
      showToast(err.message, 'error');
    });
    return;
  }
  const form = ev.target;
  // Some navigation forms (notably logout) must be allowed to leave the page
  // immediately. A loading overlay can otherwise survive while the browser
  // clears/replaces session state and look like a frozen dashboard.
  if (form.dataset.noSpinner === '1') return;
  // Login form loading
  if (form.classList.contains('login-form')) {
    setFormLoading(form);
    return;
  }
  // Confirm dialog for forms with data-confirm
  const msg = form.dataset.confirm;
  if (msg) {
    ev.preventDefault();
    confirmDialog.show(msg).then(function (ok) {
      if (ok) {
        setFormLoading(form);
        form.submit();
      }
    });
    return;
  }
  setFormLoading(form);
});

function refreshOpenOverlaysAfterResize() {
  let scheduled = false;
  function refresh() {
    scheduled = false;
    document.querySelectorAll('.modal.show').forEach(function (element) {
      try {
        const instance = window.bootstrap && window.bootstrap.Modal
          ? window.bootstrap.Modal.getInstance(element)
          : null;
        if (instance && typeof instance.handleUpdate === 'function') instance.handleUpdate();
      } catch (error) {
        dashboardLog('warn', 'overlay.resize-update-failed', { message: error && error.message });
      }
    });
  }
  return function () {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(refresh);
  };
}

const updateOpenOverlays = refreshOpenOverlaysAfterResize();
window.addEventListener('resize', updateOpenOverlays, { passive: true });
if (window.visualViewport) window.visualViewport.addEventListener('resize', updateOpenOverlays, { passive: true });

initDashboardShell();
document.querySelectorAll('.sidebar-link.is-active').forEach(function (link) {
  link.setAttribute('aria-current', 'page');
});

window.MIFPUI = Object.freeze({
  showToast: showToast,
  setFormLoading: setFormLoading,
  clearFormLoading: clearFormLoading,
  openAssetPicker: openAssetPicker,
  elapsedLabel: elapsedLabel,
  setProgressBar: setProgressBar,
});
})();

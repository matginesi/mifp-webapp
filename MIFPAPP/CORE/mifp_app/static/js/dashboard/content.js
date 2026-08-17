(() => {
'use strict';
var showToast = window.MIFPUI.showToast;
var openAssetPicker = window.MIFPUI.openAssetPicker;
var contentLog = window.MIFPLog || { debug: function(){}, info: function(){}, warn: function(){}, error: function(){} };

/* Dashboard content tables, editors, assets, and external links. */
/* ── MIFP Admin — Dashboard JavaScript ─────────────────────────
   Handles: charts, table sorting, inline panel toggling,
   asset upload/link/unlink for content records.
   ──────────────────────────────────────────────────────────── */

/* ── Table Sorting ─────────────────────────────────────────── */
function initTableSort() {
  document.querySelectorAll('.data-table thead th:not(.col-actions)').forEach(function (th) {
    th.style.cursor = 'pointer';
    th.addEventListener('click', function () {
      var table = th.closest('table');
      var tbody = table.querySelector('tbody');
      var idx = Array.from(th.parentNode.children).indexOf(th);
      var dir = th.dataset.sortDir === 'asc' ? 'desc' : 'asc';
      th.closest('tr').querySelectorAll('th').forEach(function (h) {
        h.dataset.sortDir = '';
        h.classList.remove('sort-asc', 'sort-desc');
      });
      th.dataset.sortDir = dir;
      th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
      var pairs = Array.from(tbody.querySelectorAll('tr.record-row')).map(function (row) {
        var key = (row.children[idx]?.textContent || '').trim();
        var detailId = row.dataset.rowToggle;
        var detail = detailId ? tbody.querySelector('tr#' + CSS.escape(detailId)) : null;
        return { row: row, detail: detail, key: key };
      });
      pairs.sort(function (a, b) {
        var va = a.key, vb = b.key;
        var na = parseFloat(va.replace(/[^0-9.\-]/g, ''));
        var nb = parseFloat(vb.replace(/[^0-9.\-]/g, ''));
        var da = new Date(va), db = new Date(vb);
        if (!isNaN(na) && !isNaN(nb)) return dir === 'asc' ? na - nb : nb - na;
        if (!isNaN(da.getTime()) && !isNaN(db.getTime())) return dir === 'asc' ? da - db : db - da;
        return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      });
      pairs.forEach(function (p) { tbody.appendChild(p.row); if (p.detail) tbody.appendChild(p.detail); });
    });
  });
}

/* ── Inline Panel Toggle ───────────────────────────────────── */
function toggleInlinePanel(id) {
  if (!id) return;
  var panel = document.getElementById(id);
  if (!panel) return;
  panel.classList.toggle('is-open');
  var row = document.querySelector('.record-row[data-row-toggle="' + CSS.escape(id) + '"]');
  if (row) row.classList.toggle('is-selected', panel.classList.contains('is-open'));
}

/* ── Asset Upload/Link/Unlink for content records ─────────── */
function getCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

document.addEventListener('click', function (event) {
  var opener = event.target.closest('[data-create-record-open]');
  if (!opener) return;
  contentLog.info('content.create.open', {
    section: opener.dataset.section || 'unknown',
  });
});

document.querySelectorAll('[data-create-record-form]').forEach(function(form) {
  form.addEventListener('invalid', function(event) {
    contentLog.warn('content.create.invalid', {
      section: form.dataset.section || 'unknown',
      field: event.target.name || 'unknown',
      reason: event.target.validity?.valueMissing ? 'required' : 'invalid',
    });
  }, true);
  form.addEventListener('submit', function(event) {
    var fields = Array.from(form.elements).filter(function(field) {
      return field.name && field.name !== '_csrf_token' && field.name !== 'id';
    });
    var section = form.dataset.section || 'unknown';
    var status = form.elements.review_status?.value || '';
    var active = form.elements.is_active?.value === '1';
    var completeness = {
      members: status === 'published' ? ['display_name', 'affiliation', 'country', 'role_id'] : [],
      publications: status === 'published' ? ['title', 'authors', 'year'] : [],
      research: status === 'published' ? ['title', 'summary', 'description'] : [],
      sponsors: active ? ['name', 'description', 'tier', 'primary_asset'] : [],
    };
    var missing = (completeness[section] || []).filter(function(name) {
      var field = form.elements[name];
      if (field?.type === 'file') return !field.files?.length;
      return !String(field?.value || '').trim();
    });
    if (missing.length) {
      event.preventDefault();
      event.stopImmediatePropagation();
      contentLog.warn('content.create.blocked', {
        section: section,
        missing_fields: missing,
      });
      showToast(
        'Complete ' + missing.map(function(name) { return name.replace(/_/g, ' '); }).join(', ')
          + ' or save the record as draft/inactive.',
        'warning'
      );
      form.elements[missing[0]]?.focus();
      return;
    }
    contentLog.info('content.create.submit', {
      section: section,
      fields: fields.map(function(field) { return field.name; }),
      filled_fields: fields.filter(function(field) {
        return String(field.value || '').trim() !== '';
      }).length,
    });
  });
  });

  /* Inline edit form: show loading state, prevent double-submit */
  document.querySelectorAll('form.inline-form').forEach(function(form) {
    form.addEventListener('submit', function(event) {
      var btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        var originalHtml = btn.innerHTML;
        btn.dataset.originalHtml = originalHtml;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving…';
      }
    });
  });

function requestForm(url, formData) {
  return window.MIFP.request(url, {
    method: 'POST',
    body: formData,
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  }).then(function (result) { return result.data || {}; });
}

function uploadAssetToRecord(section, recordId, file, role) {
  var formData = new FormData();
  formData.append('file', file);
  formData.append('role', role);
  formData.append('_csrf_token', getCsrfToken());
  return requestForm(
    (document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/content/' + encodeURIComponent(section) + '/' + recordId + '/assets/upload',
    formData
  );
}

function linkAssetToRecord(section, recordId, assetId, role) {
  var formData = new FormData();
  formData.append('asset_id', String(assetId));
  formData.append('role', role);
  formData.append('_csrf_token', getCsrfToken());
  return requestForm(
    (document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/content/' + encodeURIComponent(section) + '/' + recordId + '/assets/link',
    formData
  );
}

function unlinkAssetFromRecord(section, recordId, assetId) {
  var formData = new FormData();
  formData.append('asset_id', String(assetId));
  formData.append('_csrf_token', getCsrfToken());
  return requestForm(
    (document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/content/' + encodeURIComponent(section) + '/' + recordId + '/assets/unlink',
    formData
  );
}

function addExternalLinkToRecord(section, recordId, url, role, label) {
  var formData = new FormData();
  formData.append('url', url);
  formData.append('role', role || 'primary');
  formData.append('label', label || '');
  formData.append('_csrf_token', getCsrfToken());
  return requestForm(
    (document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/content/' + encodeURIComponent(section) + '/' + recordId + '/links/add',
    formData
  );
}

function deleteExternalLinkFromRecord(section, recordId, linkId) {
  var formData = new FormData();
  formData.append('link_id', String(linkId));
  formData.append('_csrf_token', getCsrfToken());
  return requestForm(
    (document.documentElement.dataset.dashboardPrefix || '/dashboard') + '/content/' + encodeURIComponent(section) + '/' + recordId + '/links/delete',
    formData
  );
}

/* ── Asset library dialogs and metadata editor ────────────── */
function setAssetText(id, value) {
  var element = document.getElementById(id);
  if (element) element.textContent = value || '—';
}

function setAssetCreateTab(button) {
  var name = button.dataset.assetCreateTab;
  document.querySelectorAll('[data-asset-create-tab]').forEach(function (tab) {
    var active = tab === button;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll('[data-asset-create-panel]').forEach(function (panel) {
    panel.hidden = panel.dataset.assetCreatePanel !== name;
  });
  var submit = document.getElementById('assetCreateSubmit');
  if (submit) {
    var isUpload = name === 'upload';
    submit.setAttribute('form', isUpload ? 'assetUploadForm' : 'assetUrlForm');
    var icon = document.createElement('i');
    icon.className = isUpload ? 'bi bi-upload' : 'bi bi-link-45deg';
    submit.replaceChildren(icon, document.createTextNode(isUpload ? ' Upload asset' : ' Register URL'));
  }
  document.querySelector('[data-asset-create-panel="' + CSS.escape(name) + '"]')
    ?.querySelector('input:not([type="hidden"]), select')?.focus();
}

function setAssetActionLink(element, url) {
  if (!element) return;
  var available = Boolean(url);
  element.href = available ? url : '#';
  element.classList.toggle('disabled', !available);
  element.setAttribute('aria-disabled', available ? 'false' : 'true');
  element.tabIndex = available ? 0 : -1;
}

function openAssetDetails(button) {
  var modal = document.getElementById('assetViewModal');
  if (!modal || !window.bootstrap) return;
  var data = button.dataset;
  setAssetText('assetViewTitle', data.filename || 'Asset');
  setAssetText('assetViewFilename', data.filename);
  setAssetText('assetViewPath', data.path);
  setAssetText('assetViewSource', data.sourceUrl);
  setAssetText('assetViewKind', [data.kind, data.mime].filter(Boolean).join(' / '));
  setAssetText('assetViewSize', data.size);
  setAssetText('assetViewChecksum', data.checksum);
  setAssetText('assetViewStorage', data.storage);

  var links = document.getElementById('assetViewLinks');
  if (links) {
    var values = (data.links || '').split('||').filter(Boolean);
    if (!values.length) {
      links.textContent = 'No linked records';
    } else {
      links.replaceChildren(...values.map(function (value) {
        var pill = document.createElement('span');
        pill.className = 'pill';
        pill.textContent = value;
        return pill;
      }));
    }
  }

  var preview = document.getElementById('assetViewPreview');
  if (preview) {
    var child = document.createElement(data.preview ? 'img' : 'i');
    if (data.preview) {
      child.src = data.preview;
      child.alt = data.filename || '';
    } else {
      child.className = data.kind === 'pdf' ? 'bi bi-file-earmark-pdf' : 'bi bi-file-earmark-text';
      child.setAttribute('aria-hidden', 'true');
    }
    preview.replaceChildren(child);
  }

  var url = data.publicUrl || data.sourceUrl || '';
  setAssetActionLink(document.getElementById('assetViewOpen'), url);
  setAssetActionLink(document.getElementById('assetViewDownload'), url);
  bootstrap.Modal.getOrCreateInstance(modal).show();
}

async function copyAssetText(button) {
  var target = document.getElementById(button.dataset.copyTarget || '');
  var value = (target?.textContent || '').trim();
  if (!value || value === '—') {
    showToast('Nothing to copy.', 'warning');
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    showToast('Copied.', 'success');
  } catch (_) {
    var selection = window.getSelection();
    var range = document.createRange();
    range.selectNodeContents(target);
    selection.removeAllRanges();
    selection.addRange(range);
    showToast('Text selected. Copy it with the keyboard.', 'info');
  }
}

/* ── Init ──────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function () {
  initTableSort();
  var createModal = document.querySelector('.create-record-modal[data-open-on-load]');
  if (createModal && window.bootstrap) bootstrap.Modal.getOrCreateInstance(createModal).show();
});

document.addEventListener('submit', function (event) {
  var form = event.target.closest('[data-download-response]');
  if (!form) return;
  event.preventDefault();
  var button = form.querySelector('button[type="submit"]');
  if (button) button.disabled = true;
  fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin' })
    .then(function (response) {
      var disposition = response.headers.get('Content-Disposition') || '';
      if (!response.ok) {
        var responseError = new Error(window.MIFP.safeMessage(response.status, null));
        responseError.status = response.status;
        throw responseError;
      }
      if (!disposition.includes('attachment')) throw new Error('Backup not available. Check the password and try again.');
      return response.blob().then(function (blob) { return { blob: blob, disposition: disposition }; });
    })
    .then(function (result) {
      var match = result.disposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
      var filename = match ? match[1].replace(/['"]/g, '') : (form.dataset.downloadName || 'download');
      var url = URL.createObjectURL(result.blob);
      var link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      var modal = form.closest('.modal');
      if (modal && window.bootstrap) bootstrap.Modal.getInstance(modal)?.hide();
      showToast('Database backup downloaded.', 'success');
    })
    .catch(function (error) {
      var message = error.status ? window.MIFP.safeMessage(error.status, error.payload) : error.message;
      showToast(message, 'error');
    })
    .finally(function () {
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
        button.removeAttribute('aria-busy');
        if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
      }
    });
});

// Inline panel toggle (click on row or button)
document.addEventListener('click', function (ev) {
  var assetCreateTab = ev.target.closest('[data-asset-create-tab]');
  if (assetCreateTab) {
    ev.preventDefault();
    setAssetCreateTab(assetCreateTab);
    return;
  }

  var assetViewButton = ev.target.closest('.asset-view-btn');
  if (assetViewButton) {
    ev.preventDefault();
    openAssetDetails(assetViewButton);
    return;
  }

  var assetCopyButton = ev.target.closest('.asset-copy');
  if (assetCopyButton) {
    ev.preventDefault();
    copyAssetText(assetCopyButton);
    return;
  }

  var nextToggle = ev.target.closest('[data-toggle-next]');
  if (nextToggle) {
    var nextPanel = nextToggle.nextElementSibling;
    var expanded = !nextPanel?.classList.contains('is-open');
    nextPanel?.classList.toggle('is-open', expanded);
    nextToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    return;
  }

  var traceButton = ev.target.closest('[data-copy-trace]');
  if (traceButton) {
    ev.preventDefault();
    var trace = document.getElementById(traceButton.dataset.copyTrace || '');
    if (trace && navigator.clipboard) {
      navigator.clipboard.writeText(trace.textContent || '').then(function () {
        showToast('Trace copied.', 'success');
      });
    }
    return;
  }

  var ownToggleButton = ev.target.closest('button[data-row-toggle]');
  var rowToggle = ev.target.closest('tr.record-row[data-row-toggle], article[data-row-toggle]');
  var toggle = ownToggleButton || rowToggle || ev.target.closest('[data-row-toggle]');
  if (!toggle) return;
  if (!ownToggleButton && ev.target.closest('a, button, input, textarea, select, label, form')) return;
  ev.preventDefault();
  ev.stopPropagation();
  toggleInlinePanel(toggle.dataset.rowToggle);
});

document.addEventListener('keydown', function (event) {
  var tab = event.target.closest('[data-asset-create-tab]');
  if (!tab || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  var tabs = Array.from(document.querySelectorAll('[data-asset-create-tab]'));
  var next = (tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].focus();
  setAssetCreateTab(tabs[next]);
});

// Asset and external-link actions
document.addEventListener('click', async function (ev) {
  var uploadBtn = ev.target.closest('.asset-upload-btn');
  if (uploadBtn) {
    ev.preventDefault();
    var section = uploadBtn.dataset.section;
    var recordId = uploadBtn.dataset.recordId;
    var container = uploadBtn.closest('.record-assets-section');
    var fileInput = container ? container.querySelector('.asset-upload-input') : null;
    if (fileInput) {
      fileInput.click();
    }
    return;
  }

  // Asset link button — open asset picker
  var linkBtn = ev.target.closest('.asset-link-btn');
  if (linkBtn) {
    ev.preventDefault();
    var section = linkBtn.dataset.section;
    var recordId = linkBtn.dataset.recordId;
    var assetRole = linkBtn.dataset.assetRole || 'attachment';
    var pickerKind = linkBtn.dataset.pickerKind || '';
    openAssetPicker(null, pickerKind).then(function(assetId) {
      if (assetId) {
        linkAssetToRecord(section, recordId, assetId, assetRole).then(function(data) {
          if (data.success) {
            showToast('Asset linked.', 'success');
            setTimeout(function() { window.location.reload(); }, 500);
          } else {
            showToast(data.error || 'Link failed.', 'error');
          }
        }).catch(function(err) {
          showToast('Link error: ' + err.message, 'error');
        });
      }
    });
    return;
  }

  // Asset unlink button
  var unlinkBtn = ev.target.closest('.asset-unlink-btn');
  if (unlinkBtn) {
    ev.preventDefault();
    var section = unlinkBtn.dataset.section;
    var recordId = unlinkBtn.dataset.recordId;
    var assetId = unlinkBtn.dataset.assetId;
    if (section && recordId && assetId) {
      if (unlinkBtn.disabled) return;
      unlinkBtn.disabled = true;
      unlinkAssetFromRecord(section, recordId, assetId).then(function(data) {
        if (data.success) {
          showToast(data.already_unlinked ? 'Asset was already unlinked.' : 'Asset unlinked.', data.already_unlinked ? 'info' : 'success');
          document.querySelectorAll('.asset-unlink-btn').forEach(function(button) {
            if (
              button.dataset.section === section &&
              button.dataset.recordId === String(recordId) &&
              button.dataset.assetId === String(assetId)
            ) {
              var card = button.closest('.linked-asset-card');
              if (card) card.remove();
            }
          });
        } else {
          unlinkBtn.disabled = false;
          showToast(data.error || 'Unlink failed.', 'error');
        }
      }).catch(function(err) {
        unlinkBtn.disabled = false;
        showToast('Unlink error: ' + err.message, 'error');
      });
    }
    return;
  }

  var linkAddBtn = ev.target.closest('.entity-link-add-btn');
  if (linkAddBtn) {
    ev.preventDefault();
    openExternalLinkDialog(linkAddBtn.dataset.section, linkAddBtn.dataset.recordId);
    return;
  }

  var linkDeleteBtn = ev.target.closest('.entity-link-delete-btn');
  if (linkDeleteBtn) {
    ev.preventDefault();
    var delSection = linkDeleteBtn.dataset.section;
    var delRecordId = linkDeleteBtn.dataset.recordId;
    var linkId = linkDeleteBtn.dataset.linkId;
    if (!await confirmDialog.show('Remove this external link from the record? The link can be added again later.')) return;
    deleteExternalLinkFromRecord(delSection, delRecordId, linkId).then(function(data) {
      if (data.success) {
        showToast('Link removed.', 'success');
        var card = linkDeleteBtn.closest('.linked-asset-card');
        if (card) card.remove();
      } else {
        showToast(data.error || 'Link remove failed.', 'error');
      }
    }).catch(function(err) {
      showToast('Link remove error: ' + err.message, 'error');
    });
    return;
  }
});

/* ── External-link dialog (replaces window.prompt) ─────────── */
var pendingExternalLink = null;

function openExternalLinkDialog(section, recordId) {
  var modalEl = document.getElementById('externalLinkModal');
  if (!modalEl || !window.bootstrap) return;
  pendingExternalLink = { section: section, recordId: recordId };
  var form = modalEl.querySelector('#externalLinkForm');
  if (form) {
    form.reset();
    var roleInput = form.elements.role;
    if (roleInput) roleInput.value = 'primary';
  }
  var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  modal.show();
  modalEl.addEventListener('shown.bs.modal', function focusUrl() {
    modalEl.removeEventListener('shown.bs.modal', focusUrl);
    var urlInput = form ? form.elements.url : null;
    if (urlInput) urlInput.focus();
  });
  modalEl.addEventListener('hidden.bs.modal', function clearPending() {
    modalEl.removeEventListener('hidden.bs.modal', clearPending);
    pendingExternalLink = null;
  });
}

document.addEventListener('submit', function (event) {
  var linkForm = event.target.closest('#externalLinkForm');
  if (!linkForm || !pendingExternalLink) return;
  event.preventDefault();
  var url = (linkForm.elements.url.value || '').trim();
  var role = (linkForm.elements.role.value || '').trim() || 'primary';
  var label = (linkForm.elements.label.value || '').trim();
  var pending = pendingExternalLink;
  if (!url) {
    showToast('Enter a URL for the external link.', 'warning');
    linkForm.elements.url.focus();
    return;
  }
  addExternalLinkToRecord(pending.section, pending.recordId, url, role, label).then(function(data) {
    if (data.success) {
      showToast('Link added.', 'success');
      var modalEl = document.getElementById('externalLinkModal');
      if (modalEl && window.bootstrap) bootstrap.Modal.getInstance(modalEl)?.hide();
      setTimeout(function() { window.location.reload(); }, 500);
    } else {
      showToast(data.error || 'Link add failed.', 'error');
    }
  }).catch(function(err) {
    showToast('Link error: ' + err.message, 'error');
  });
});

// Asset upload file input change
document.addEventListener('change', function (ev) {
  var fileInput = ev.target.closest('.asset-upload-input');
  if (!fileInput || !fileInput.files || !fileInput.files.length) return;
  var section = fileInput.dataset.section;
  var recordId = fileInput.dataset.recordId;
  var file = fileInput.files[0];
  var role = section === 'event'
    ? (file.type.startsWith('image/') ? 'cover' : 'document')
    : 'attachment';
  uploadAssetToRecord(section, recordId, file, role).then(function(data) {
    if (data.success) {
      showToast('Asset uploaded and linked.', 'success');
      setTimeout(function() { window.location.reload(); }, 500);
    } else {
      showToast(data.error || 'Upload failed.', 'error');
    }
  }).catch(function(err) {
    showToast('Upload error: ' + err.message, 'error');
  });
  fileInput.value = '';
});

/* ── Logs auto-refresh (replaces <meta http-equiv="refresh">) ── */
(function () {
  var params = new URLSearchParams(window.location.search);
  var seconds = parseInt(params.get('refresh') || '0', 10);
  if (!seconds || seconds <= 0) return;
  var results = document.querySelector('.log-results');
  if (!results) return;
  setInterval(function () {
    if (document.visibilityState === 'hidden') return;
    fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (res) { return res.ok ? res.text() : Promise.reject(new Error('HTTP ' + res.status)); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var freshResults = doc.querySelector('.log-results');
        var currentResults = document.querySelector('.log-results');
        if (freshResults && currentResults) currentResults.replaceWith(freshResults);
        var freshStatus = doc.querySelector('.log-status-strip');
        var currentStatus = document.querySelector('.log-status-strip');
        if (freshStatus && currentStatus) currentStatus.replaceWith(freshStatus);
      })
      .catch(function () {});
  }, seconds * 1000);
})();
})();

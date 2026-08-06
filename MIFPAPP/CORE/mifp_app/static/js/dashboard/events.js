(() => {
'use strict';
/* Event management (table + wizard) */
var showToast = window.MIFPUI.showToast;
var eventWizard = document.getElementById('eventWizard');
var eventLog = window.MIFPLog || { debug: function(){}, info: function(){}, warn: function(){}, error: function(){} };
var pendingEventUploads = 0;

function setEventUploadBusy(delta) {
  pendingEventUploads = Math.max(0, pendingEventUploads + delta);
  var submit = document.getElementById('wizardSubmit');
  if (submit) {
    submit.disabled = pendingEventUploads > 0;
    submit.setAttribute('aria-busy', pendingEventUploads > 0 ? 'true' : 'false');
  }
}

if (eventWizard) {
  var wizardForm = document.getElementById('eventWizardForm');
  var wizardNext = document.getElementById('wizardNext');
  var wizardPrev = document.getElementById('wizardPrev');
  var wizardSubmit = document.getElementById('wizardSubmit');
  var wizardSteps = document.querySelectorAll('.wizard-step');
  var wizardPanels = document.querySelectorAll('.wizard-panel');
  var currentStep = 1;
  var totalSteps = 3;

  function setPreviewCheck(name, ready) {
    var item = eventWizard.querySelector('[data-preview-check="' + name + '"]');
    if (!item) return;
    item.classList.toggle('is-ready', ready);
    var icon = item.querySelector('i');
    if (icon) icon.className = 'bi ' + (ready ? 'bi-check-circle-fill' : 'bi-circle');
  }

  function updateWizardPreview() {
    var value = function(name) {
      var field = wizardForm.elements[name];
      return field ? String(field.value || '').trim() : '';
    };
    var title = value('title');
    var type = value('event_type');
    var start = value('start_date');
    var end = value('end_date');
    var dateText = value('date_text');
    var location = value('location');
    var status = value('review_status');
    var cover = document.getElementById('wizardCoverImg');
    var media = document.getElementById('wizardPublicPreviewMedia');

    document.getElementById('wizardPublicPreviewTitle').textContent = title || 'Your event title';
    document.getElementById('wizardPublicPreviewType').textContent = (type || 'conference').replace(/_/g, ' ');
    document.getElementById('wizardPublicPreviewDate').textContent =
      dateText || (start ? start + (end && end !== start ? ' – ' + end : '') : 'Date not set');
    document.getElementById('wizardPublicPreviewLocation').textContent = location || 'Location not set';
    document.getElementById('wizardPublicPreviewStatus').textContent = status || 'published';

    if (media) {
      if (cover && cover.src && !document.getElementById('wizardCoverPreview').classList.contains('d-none')) {
        var previewImage = document.createElement('img');
        previewImage.src = cover.src;
        previewImage.alt = '';
        media.replaceChildren(previewImage);
      } else {
        var icon = document.createElement('i');
        var label = document.createElement('span');
        icon.className = 'bi bi-image';
        icon.setAttribute('aria-hidden', 'true');
        label.textContent = 'Add a cover image';
        media.replaceChildren(icon, label);
      }
    }
    setPreviewCheck('title', Boolean(title));
    setPreviewCheck('date', Boolean(dateText || start));
    setPreviewCheck('cover', Boolean(cover && cover.src && document.getElementById('wizardCoverAssetId').value));
  }

  function showWizardStep(step) {
    currentStep = step;
    wizardSteps.forEach(function(s) { s.classList.toggle('is-active', parseInt(s.dataset.step) === step); });
    wizardPanels.forEach(function(p) { p.classList.toggle('is-active', parseInt(p.dataset.wizardPanel) === step); });
    wizardPrev.disabled = step === 1;
    if (step === totalSteps) {
      wizardNext.classList.add('d-none');
      wizardSubmit.classList.remove('d-none');
    } else {
      wizardNext.classList.remove('d-none');
      wizardSubmit.classList.add('d-none');
    }
  }

  wizardNext.addEventListener('click', function() {
    var panel = eventWizard.querySelector('[data-wizard-panel="' + currentStep + '"]');
    var invalid = panel ? Array.from(panel.querySelectorAll('[required]')).find(function(field) {
      return !field.checkValidity();
    }) : null;
    if (invalid) {
      invalid.reportValidity();
      invalid.focus();
      return;
    }
    if (currentStep < totalSteps) showWizardStep(currentStep + 1);
  });
  wizardPrev.addEventListener('click', function() {
    if (currentStep > 1) showWizardStep(currentStep - 1);
  });
  var newEventButton = document.querySelector('[data-event-wizard="new"]');
  if (newEventButton) newEventButton.addEventListener('click', function() {
    wizardForm.reset();
    wizardForm.querySelector('input[name="id"]').value = '';
    var docs = document.getElementById('wizardDocsList');
    if (docs) {
      docs.innerHTML = '<p class="empty" style="text-align:center;padding:.75rem;color:var(--text-3);font-size:.75rem">No documents yet. Upload a PDF, DOC or DOCX file.</p>';
    }
    var coverPreview = document.getElementById('wizardCoverPreview');
    var coverDrop = document.getElementById('wizardCoverDrop');
    var coverImage = document.getElementById('wizardCoverImg');
    if (coverPreview) coverPreview.classList.add('d-none');
    if (coverDrop) coverDrop.classList.remove('d-none');
    if (coverImage) coverImage.removeAttribute('src');
    pendingEventUploads = 0;
    setEventUploadBusy(0);
    showWizardStep(1);
    updateWizardPreview();
    eventLog.info('event.create.open', { mode: 'create' });
    bootstrap.Modal.getOrCreateInstance(eventWizard).show();
  });

  /* Cover image upload for wizard */
  setupCoverUpload('wizard');
  /* Document management for wizard */
  setupDocManager('wizard', 'wizardDocsList');

  /* Slug auto-generation */
  var wizardTitle = wizardForm.querySelector('input[name="title"]');
  var wizardSlug = wizardForm.querySelector('input[name="slug"]');
  wizardTitle.addEventListener('input', function() {
    if (!wizardSlug.value || wizardSlug.dataset.automated !== 'false') {
      wizardSlug.value = wizardTitle.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || '';
      wizardSlug.dataset.automated = 'true';
    }
  });
  wizardSlug.addEventListener('input', function() { wizardSlug.dataset.automated = 'false'; });
  wizardForm.addEventListener('input', updateWizardPreview);
  wizardForm.addEventListener('change', updateWizardPreview);
  wizardForm.addEventListener('submit', function(event) {
    var incompleteDocuments = Array.from(wizardForm.querySelectorAll('.doc-card')).filter(function(card) {
      return !card.querySelector('.doc-asset-id')?.value;
    }).length;
    if (pendingEventUploads > 0 || incompleteDocuments > 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      eventLog.warn('event.create.blocked', {
        pending_uploads: pendingEventUploads,
        incomplete_documents: incompleteDocuments,
      });
      showToast(
        pendingEventUploads > 0
          ? 'Wait for all uploads to finish before creating the event.'
          : 'Remove documents that did not finish uploading.',
        'warning'
      );
      return;
    }
    if (wizardForm.elements.review_status?.value === 'published') {
      var missingPublished = [];
      if (!(wizardForm.elements.start_date?.value || wizardForm.elements.date_text?.value.trim())) {
        missingPublished.push('date');
      }
      if (!wizardForm.elements.description?.value.trim()) missingPublished.push('description');
      if (!(wizardForm.elements.location?.value.trim() || wizardForm.elements.remote_url?.value.trim())) {
        missingPublished.push('location or external URL');
      }
      if (!wizardForm.elements.cover_asset_id?.value) missingPublished.push('cover image');
      if (missingPublished.length) {
        event.preventDefault();
        event.stopImmediatePropagation();
        eventLog.warn('event.create.blocked', {
          reason: 'published_event_incomplete',
          missing_fields: missingPublished,
        });
        showToast(
          'Complete ' + missingPublished.join(', ') + ' or save the event as draft.',
          'warning'
        );
        return;
      }
    }
    eventLog.info('event.create.submit', {
      has_title: Boolean(wizardForm.elements.title?.value.trim()),
      has_date: Boolean(wizardForm.elements.start_date?.value),
      has_cover: Boolean(wizardForm.elements.cover_asset_id?.value),
      documents: Array.from(wizardForm.querySelectorAll('.doc-asset-id')).filter(function(field) {
        return Boolean(field.value);
      }).length,
    });
  });
  updateWizardPreview();
}

function uploadEventAsset(file) {
  var fd = new FormData();
  fd.append('file', file);
  setEventUploadBusy(1);
  eventLog.info('event.asset.upload_started', {
    extension: String(file.name || '').split('.').pop().toLowerCase(),
    bytes: Number(file.size || 0),
  });
  return window.MIFP.request('/dashboard/assets/create.json', {
    method: 'POST',
    body: fd,
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  }).then(function (result) {
    var data = result.data || {};
    if (!data.id || !data.public_url) throw new Error('The upload response is incomplete.');
    eventLog.info('event.asset.upload_completed', { asset_id: data.id });
    return { asset_id: data.id, public_url: data.public_url };
  }).catch(function(error) {
    eventLog.error('event.asset.upload_failed', { error: error });
    throw error;
  }).finally(function() {
    setEventUploadBusy(-1);
  });
}

function setDropStatus(dropZone, iconClass, label, tone) {
  var icon = document.createElement('i');
  var text = document.createElement('span');
  icon.className = 'bi ' + iconClass;
  icon.setAttribute('aria-hidden', 'true');
  if (tone) icon.classList.add('tone-' + tone);
  text.textContent = label;
  dropZone.replaceChildren(icon, text);
}

function setupCoverUpload(prefix) {
  var drop = document.getElementById(prefix + 'CoverDrop');
  var input = document.getElementById(prefix + 'CoverInput');
  var preview = document.getElementById(prefix + 'CoverPreview');
  var img = document.getElementById(prefix + 'CoverImg');
  var removeBtn = document.getElementById(prefix + 'CoverRemove');
  var assetIdField = document.getElementById(prefix + 'CoverAssetId');
  if (!drop) return;

  drop.addEventListener('click', function() { input.click(); });
  drop.addEventListener('dragover', function(e) { e.preventDefault(); drop.style.borderColor = 'var(--accent)'; });
  drop.addEventListener('dragleave', function() { drop.style.borderColor = ''; });
  drop.addEventListener('drop', function(e) { e.preventDefault(); drop.style.borderColor = ''; if (e.dataTransfer.files.length) handleCoverFile(e.dataTransfer.files[0], prefix); });
  input.addEventListener('change', function() { if (input.files.length) handleCoverFile(input.files[0], prefix); });
  if (removeBtn) removeBtn.addEventListener('click', function() {
    preview.classList.add('d-none');
    drop.classList.remove('d-none');
    img.src = '';
    assetIdField.value = '';
    wizardForm.dispatchEvent(new Event('change', { bubbles: true }));
  });

  function handleCoverFile(file, pfx) {
    if (!file.type.startsWith('image/')) {
      showToast('Choose an image file for the event cover.', 'warning');
      return;
    }
    window.MIFP.once(drop, function () {
      setDropStatus(drop, 'bi-arrow-repeat', 'Uploading…');
      return uploadEventAsset(file)
      .then(function(res) {
        assetIdField.value = res.asset_id;
        img.src = res.public_url;
        preview.classList.remove('d-none');
        drop.classList.add('d-none');
        wizardForm.dispatchEvent(new Event('change', { bubbles: true }));
      })
      .catch(function(err) {
        setDropStatus(drop, 'bi-image', 'Choose cover image');
        showToast(err.message || 'Cover upload failed.', 'error');
      });
    });
  }
}

function setupDocManager(prefix, listId) {
  var addBtn = document.getElementById(prefix + 'AddDoc');
  var list = document.getElementById(listId);
  if (!addBtn || !list) return;

  addBtn.addEventListener('click', function() { createDocUpload(list); });

  list.addEventListener('click', function(e) {
    var removeBtn = e.target.closest('.doc-card-remove');
    if (removeBtn) {
      var card = removeBtn.closest('.doc-card');
      if (card) card.remove();
      if (!list.querySelectorAll('.doc-card').length) {
        list.innerHTML = '<p class="empty" style="text-align:center;padding:.75rem;color:var(--text-3);font-size:.75rem">No documents yet.</p>';
      }
    }
  });
}

function createDocUpload(list) {
  var empty = list.querySelector('.empty');
  if (empty) empty.remove();

  var card = document.createElement('div');
  card.className = 'doc-card';
  card.innerHTML =
    '<div class="doc-card-file">' +
      '<input type="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" class="doc-file-input d-none">' +
      '<button type="button" class="doc-drop-zone"><i class="bi bi-file-earmark-text" aria-hidden="true"></i><span>Select PDF, DOC or DOCX</span></button>' +
      '<span class="doc-card-name"></span>' +
    '</div>' +
    '<div class="doc-card-type">' +
      '<select class="form-select form-select-xs doc-type-select">' +
        '<option value="Program">Program / Schedule</option>' +
        '<option value="Proceedings">Proceedings / Abstracts</option>' +
        '<option value="Brochure">Brochure / Flyer</option>' +
        '<option value="Poster">Poster</option>' +
        '<option value="">Other</option>' +
      '</select>' +
      '<input type="hidden" name="doc_asset_id" class="doc-asset-id" value="">' +
      '<input type="hidden" name="doc_type" class="doc-type-hidden" value="Program">' +
    '</div>' +
    '<button type="button" class="btn btn-mini btn-outline-danger doc-card-remove" aria-label="Remove document">&times;</button>';

  var fileInput = card.querySelector('.doc-file-input');
  var dropZone = card.querySelector('.doc-drop-zone');
  var nameEl = card.querySelector('.doc-card-name');
  var typeSelect = card.querySelector('.doc-type-select');
  var typeHidden = card.querySelector('.doc-type-hidden');
  var assetIdField = card.querySelector('.doc-asset-id');

  typeSelect.addEventListener('change', function() { typeHidden.value = typeSelect.value; });

  dropZone.addEventListener('click', function() { fileInput.click(); });
  fileInput.addEventListener('change', function() {
    if (!fileInput.files.length) return;
    uploadDocFile(fileInput.files[0], card, nameEl, assetIdField, dropZone);
  });

  list.appendChild(card);
}

function uploadDocFile(file, card, nameEl, assetIdField, dropZone) {
  var extension = String(file.name || '').split('.').pop().toLowerCase();
  if (!['pdf', 'doc', 'docx'].includes(extension)) {
    setDropStatus(dropZone, 'bi-file-earmark-text', 'Select PDF, DOC or DOCX');
    showToast('Unsupported document. Select a PDF, DOC or DOCX file.', 'warning');
    return;
  }
  nameEl.textContent = file.name;
  setDropStatus(dropZone, 'bi-arrow-repeat', 'Uploading…');
  window.MIFP.once(card, function () {
    return uploadEventAsset(file)
    .then(function(res) {
      assetIdField.value = res.asset_id;
      setDropStatus(dropZone, 'bi-check-lg', 'Uploaded', 'success');
    })
    .catch(function(err) {
      setDropStatus(dropZone, 'bi-exclamation-triangle', 'Failed', 'danger');
      showToast(err.message || 'Document upload failed.', 'error');
    });
  });
}

})();

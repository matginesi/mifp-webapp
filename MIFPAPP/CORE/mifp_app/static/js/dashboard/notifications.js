/* Notifications page: bounded visual composer with a plain-text escape hatch. */
(function () {
  'use strict';

  var panel = document.querySelector('.notification-compose-panel');
  if (!panel) return;

  var form = panel.querySelector('.notification-compose-form');
  var plainToggle = panel.querySelector('[data-notification-plain-toggle]');
  var titleField = panel.querySelector('.notification-html-title-field');
  var editor = panel.querySelector('[data-notification-mail-editor]');
  var tools = panel.querySelector('[data-notification-format-tools]');
  var richInput = panel.querySelector('[data-notification-rich-input]');
  var plainInput = panel.querySelector('[data-notification-plain-input]');
  var textPayload = panel.querySelector('[data-notification-message-text]');
  var htmlPayload = panel.querySelector('[data-notification-message-html]');
  var count = panel.querySelector('[data-notification-message-count]');
  var attachmentInput = panel.querySelector('[data-notification-attachments]');
  var attachmentList = panel.querySelector('[data-notification-attachment-list]');
  var maxLength = Number((plainInput && plainInput.getAttribute('maxlength')) || 10000);
  var attachmentsValid = true;

  if (!form || !plainToggle || !editor || !richInput || !plainInput || !textPayload || !htmlPayload) return;

  function textValue() {
    return plainToggle.checked ? plainInput.value : (richInput.innerText || '');
  }

  function updateCount() {
    if (count) count.textContent = String(textValue().length);
  }

  function syncFormatMode() {
    var plain = plainToggle.checked;
    panel.classList.toggle('is-plain-text', plain);
    if (titleField) titleField.hidden = plain;
    if (tools) tools.hidden = plain;
    richInput.hidden = plain;
    plainInput.hidden = !plain;

    if (plain && !plainInput.value.trim() && richInput.innerText.trim()) {
      plainInput.value = richInput.innerText.trim();
    } else if (!plain && !richInput.innerText.trim() && plainInput.value.trim()) {
      richInput.textContent = plainInput.value;
    }
    updateCount();
  }

  function cleanUrl(value) {
    var raw = String(value || '').trim();
    if (!raw) return '';
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)) return 'mailto:' + raw;
    if (!/^[a-z][a-z0-9+.-]*:/i.test(raw)) raw = 'https://' + raw;
    try {
      var parsed = new URL(raw);
      return ['http:', 'https:', 'mailto:'].includes(parsed.protocol) ? raw : '';
    } catch (_) {
      return '';
    }
  }

  function insertNode(node) {
    var selection = window.getSelection();
    var range = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
    if (!range || !richInput.contains(range.commonAncestorContainer)) {
      richInput.appendChild(node);
    } else {
      range.deleteContents();
      range.insertNode(node);
    }
    var spacer = document.createElement('p');
    spacer.appendChild(document.createElement('br'));
    node.after(spacer);
    if (selection) {
      var nextRange = document.createRange();
      nextRange.setStart(spacer, 0);
      nextRange.collapse(true);
      selection.removeAllRanges();
      selection.addRange(nextRange);
    }
    updateCount();
  }

  function insertImage() {
    var source = cleanUrl(window.prompt('Public image URL (HTTPS):', 'https://'));
    if (!source || !source.toLowerCase().startsWith('https://')) return;
    var image = document.createElement('img');
    image.src = source;
    image.alt = String(window.prompt('Alternative text:', '') || '').trim().slice(0, 180);
    image.width = 560;
    insertNode(image);
  }

  function insertTable() {
    var rowCount = Number(window.prompt('Number of rows (2–10):', '3'));
    var columnCount = Number(window.prompt('Number of columns (2–6):', '2'));
    if (!Number.isInteger(rowCount) || rowCount < 2 || rowCount > 10 ||
        !Number.isInteger(columnCount) || columnCount < 2 || columnCount > 6) return;
    var table = document.createElement('table');
    var body = document.createElement('tbody');
    for (var rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
      var row = document.createElement('tr');
      for (var columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
        var cell = document.createElement(rowIndex === 0 ? 'th' : 'td');
        cell.textContent = rowIndex === 0 ? 'Heading ' + (columnIndex + 1) : 'Cell';
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
    table.appendChild(body);
    insertNode(table);
  }

  function runCommand(button) {
    var command = button.dataset.richCommand;
    if (!command) return;
    richInput.focus();
    if (command === 'createLink') {
      var href = cleanUrl(window.prompt('Link URL or email address:', 'https://'));
      if (!href) return;
      document.execCommand('createLink', false, href);
    } else if (command === 'formatBlock') {
      document.execCommand(command, false, button.dataset.richValue || 'p');
    } else {
      document.execCommand(command, false, null);
    }
    updateCount();
  }

  if (tools) {
    tools.addEventListener('click', function (event) {
      var button = event.target.closest('[data-rich-command], [data-rich-action]');
      if (!button || !tools.contains(button)) return;
      event.preventDefault();
      if (button.dataset.richAction === 'image') insertImage();
      else if (button.dataset.richAction === 'table') insertTable();
      else runCommand(button);
    });
  }

  function formatBytes(bytes) {
    return bytes >= 1024 * 1024
      ? (bytes / (1024 * 1024)).toFixed(1) + ' MB'
      : Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function renderAttachments() {
    if (!attachmentInput || !attachmentList) return;
    var files = Array.from(attachmentInput.files || []);
    attachmentList.replaceChildren();
    attachmentsValid = files.length <= 5;
    var total = files.reduce(function (sum, file) { return sum + file.size; }, 0);
    if (total > 10 * 1024 * 1024) attachmentsValid = false;
    if (!files.length) {
      var empty = document.createElement('li');
      empty.textContent = 'No files selected.';
      attachmentList.appendChild(empty);
      return;
    }
    files.forEach(function (file) {
      var item = document.createElement('li');
      var allowed = /\.(pdf|png|jpe?g|txt|csv|docx|xlsx)$/i.test(file.name);
      var valid = allowed && file.size > 0 && file.size <= 5 * 1024 * 1024;
      if (!valid) attachmentsValid = false;
      var name = document.createElement('span');
      name.textContent = file.name;
      var size = document.createElement('small');
      size.textContent = formatBytes(file.size) + (valid ? '' : ' · check file type or size');
      item.classList.toggle('is-invalid', !valid);
      item.append(name, size);
      attachmentList.appendChild(item);
    });
    if (!attachmentsValid) {
      var warning = document.createElement('li');
      warning.className = 'is-invalid';
      warning.textContent = 'Review attachments before sending (5 files, 5 MB each, 10 MB total).';
      attachmentList.appendChild(warning);
    }
  }

  if (attachmentInput) attachmentInput.addEventListener('change', renderAttachments);

  richInput.addEventListener('paste', function (event) {
    event.preventDefault();
    var text = (event.clipboardData || window.clipboardData).getData('text/plain');
    document.execCommand('insertText', false, text);
  });

  richInput.addEventListener('input', function () {
    if ((richInput.innerText || '').length > maxLength) {
      document.execCommand('undo', false, null);
    }
    updateCount();
  });
  plainInput.addEventListener('input', updateCount);
  plainToggle.addEventListener('change', syncFormatMode);

  form.addEventListener('submit', function (event) {
    if (!attachmentsValid) {
      event.preventDefault();
      attachmentInput.focus();
      return;
    }
    var text = textValue().trim();
    if (!text) {
      event.preventDefault();
      (plainToggle.checked ? plainInput : richInput).focus();
      return;
    }
    if (text.length > maxLength) {
      event.preventDefault();
      (plainToggle.checked ? plainInput : richInput).focus();
      return;
    }
    textPayload.value = text;
    htmlPayload.value = plainToggle.checked ? '' : richInput.innerHTML;
  });

  syncFormatMode();
  renderAttachments();
})();

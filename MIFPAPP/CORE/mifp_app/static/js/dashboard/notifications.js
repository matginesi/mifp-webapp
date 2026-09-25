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
  var maxLength = Number((plainInput && plainInput.getAttribute('maxlength')) || 10000);

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
      var button = event.target.closest('[data-rich-command]');
      if (!button || !tools.contains(button)) return;
      event.preventDefault();
      runCommand(button);
    });
  }

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
})();

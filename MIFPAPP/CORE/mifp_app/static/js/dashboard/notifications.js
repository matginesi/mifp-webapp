/* Notifications page: keep the manual mail editor simple and explicit. */
(function () {
  'use strict';

  var panel = document.querySelector('.notification-compose-panel');
  if (!panel) return;

  var plainToggle = panel.querySelector('[data-notification-plain-toggle]');
  var titleField = panel.querySelector('.notification-html-title-field');
  var editor = panel.querySelector('[data-notification-mail-editor]');
  var tools = panel.querySelector('[data-notification-format-tools]');
  var preview = panel.querySelector('[data-notification-format-preview]');

  if (!plainToggle || !editor) return;

  function syncFormatMode() {
    var plain = plainToggle.checked;
    panel.classList.toggle('is-plain-text', plain);
    editor.classList.toggle('is-plain-text', plain);
    if (titleField) titleField.hidden = plain;
    if (tools) tools.hidden = plain;
    if (preview) preview.hidden = plain;
  }

  plainToggle.addEventListener('change', syncFormatMode);
  syncFormatMode();
})();

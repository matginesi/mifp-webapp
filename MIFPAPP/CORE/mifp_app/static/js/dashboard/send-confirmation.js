(function () {
  'use strict';
  var element = document.getElementById('sendAuthModal');
  if (!element) return;
  var modal = new window.bootstrap.Modal(element);
  var form = document.getElementById('sendAuthForm');
  var input = document.getElementById('sendAuthPassword');
  var pending = null;
  var password = '';
  window.MIFPConfirmSend = function () {
    if (pending) return Promise.resolve(null);
    return new Promise(function (resolve) { pending = resolve; modal.show(); });
  };
  element.addEventListener('shown.bs.modal', function () { input.focus(); });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    password = input.value;
    input.value = '';
    modal.hide();
  });
  element.addEventListener('hidden.bs.modal', function () {
    input.value = '';
    if (pending) pending(password || null);
    pending = null;
    password = '';
  });
  // Native forms retain their existing validation and CSRF handling.
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-direct-email-form], .notification-test-form').forEach(function (target) {
      target.addEventListener('submit', async function (event) {
        if (event.defaultPrevented || target.dataset.emailMode === 'file') return;
        event.preventDefault();
        var secret = await window.MIFPConfirmSend();
        if (!secret) return;
        var field = document.createElement('input');
        field.type = 'hidden';
        field.name = 'password';
        field.value = secret;
        target.append(field);
        try { HTMLFormElement.prototype.submit.call(target); }
        finally { field.remove(); secret = ''; }
      });
    });
  });
})();

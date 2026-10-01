(function () {
  'use strict';
  var form = document.querySelector('[data-direct-email-form]');
  if (!form) return;
  var filePanel = form.querySelector('[data-email-batch-panel]');
  var directField = form.querySelector('[data-direct-recipient-fields]');
  var copyFields = form.querySelector('.notification-copy-fields');
  var attachmentField = form.querySelector('.notification-attachment-field');
  var submit = form.querySelector('[data-email-submit]');
  var tableData = null;
  var mapped = null;
  var batch = { running: false, paused: false, recipients: [], failed: [], index: 0, accepted: 0, controller: null, started: 0 };

  function fileMode() { return form.querySelector('[data-recipient-mode]:checked')?.value === 'file'; }
  function show(message, error) {
    var toast = document.createElement('div');
    toast.className = 'poll-feedback is-visible' + (error ? ' is-error' : '');
    toast.setAttribute('role', 'status');
    toast.textContent = message;
    document.body.append(toast);
    window.setTimeout(function () { toast.remove(); }, 5000);
  }

  function toggleMode() {
    var enabled = fileMode();
    filePanel.hidden = !enabled;
    directField.hidden = enabled;
    copyFields.hidden = enabled;
    attachmentField.hidden = enabled;
    directField.querySelector('[name="recipients"]').required = !enabled;
    copyFields.querySelectorAll('textarea').forEach(function (input) { input.disabled = enabled; });
    attachmentField.querySelectorAll('input').forEach(function (input) { input.disabled = enabled; });
    submit.querySelector('span').textContent = enabled ? 'Send batch' : 'Send email';
    submit.disabled = submit.hasAttribute('data-smtp-disabled') || (enabled && (!mapped || mapped.valid.length === 0 || batch.running));
  }

  form.querySelectorAll('[data-recipient-mode]').forEach(function (input) { input.addEventListener('change', toggleMode); });

  function fill(select, headers, optional) {
    select.replaceChildren();
    if (optional) { var none = document.createElement('option'); none.value = ''; none.textContent = 'Not mapped'; select.append(none); }
    headers.forEach(function (header, index) { var option = document.createElement('option'); option.value = String(index); option.textContent = header; select.append(option); });
  }

  function refreshMapping() {
    if (!tableData) return;
    var selected = {};
    form.querySelectorAll('[data-email-map]').forEach(function (select) { selected[select.dataset.emailMap] = select.value; });
    if (selected.email === '') { mapped = null; return toggleMode(); }
    mapped = window.MIFPRecipientFiles.recipients(tableData, selected);
    ['total', 'invalid', 'duplicate'].forEach(function (key) { form.querySelector('[data-email-count="' + key + '"]').textContent = mapped[key]; });
    form.querySelector('[data-email-count="valid"]').textContent = mapped.valid.length;
    form.querySelector('[data-email-recipient-summary]').hidden = false;
    toggleMode();
  }

  form.querySelector('[data-email-recipient-file]').addEventListener('change', async function (event) {
    try {
      tableData = await window.MIFPRecipientFiles.parse(event.target.files[0]);
      var guesses = window.MIFPRecipientFiles.mapping(tableData);
      form.querySelectorAll('[data-email-map]').forEach(function (select) {
        fill(select, tableData.headers, select.dataset.emailMap !== 'email');
        select.value = guesses[select.dataset.emailMap];
        select.addEventListener('change', refreshMapping);
      });
      form.querySelector('[data-email-recipient-mapping]').hidden = false;
      refreshMapping();
    } catch (error) { tableData = null; mapped = null; toggleMode(); show(error.message, true); }
  });

  function content(recipient) {
    var plainMode = form.querySelector('[data-notification-plain-toggle]').checked;
    var rich = form.querySelector('[data-notification-rich-input]');
    var plain = form.querySelector('[data-notification-plain-input]');
    return {
      email: recipient.email,
      first_name: recipient.first_name || '',
      last_name: recipient.last_name || '',
      subject: form.querySelector('[name="subject"]').value,
      mail_title: form.querySelector('[name="mail_title"]').value,
      message: plainMode ? plain.value : rich.innerText,
      message_html: plainMode ? '' : rich.innerHTML,
    };
  }

  async function audit(phase) {
    try {
      await window.MIFP.request(form.dataset.batchAuditUrl, { method: 'POST', json: {
        phase: phase, attempted: batch.recipients.length, accepted: batch.accepted,
        failed: batch.failed.length, duration_ms: batch.started ? Date.now() - batch.started : 0,
      } });
    } catch (_) { /* Delivery must not be retried because an aggregate audit write failed. */ }
  }

  function delay(milliseconds) { return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); }); }
  function updateProgress() {
    var panel = form.querySelector('[data-email-batch-progress]');
    panel.hidden = false;
    var total = batch.recipients.length;
    panel.querySelector('[data-email-progress-title]').textContent = (batch.paused ? 'Paused ' : batch.running ? 'Sending ' : 'Finished ') + batch.index + ' / ' + total;
    var bar = panel.querySelector('[data-email-progress-bar]'); bar.max = Math.max(1, total); bar.value = batch.index;
    panel.querySelector('[data-email-progress="accepted"]').textContent = batch.accepted;
    panel.querySelector('[data-email-progress="failed"]').textContent = batch.failed.length;
    panel.querySelector('[data-email-progress="pending"]').textContent = Math.max(0, total - batch.index);
    panel.querySelector('[data-email-batch-pause]').disabled = !batch.running || batch.paused;
    panel.querySelector('[data-email-batch-resume]').disabled = !batch.running || !batch.paused;
    panel.querySelector('[data-email-batch-retry]').disabled = batch.running || batch.failed.length === 0;
    submit.disabled = batch.running;
  }

  async function run(recipients) {
    batch = { running: true, paused: false, recipients: recipients.slice(), failed: [], index: 0, accepted: 0, controller: new AbortController(), started: Date.now() };
    updateProgress();
    await audit('started');
    while (batch.index < batch.recipients.length && !batch.controller.signal.aborted) {
      while (batch.paused && !batch.controller.signal.aborted) await delay(200);
      if (batch.controller.signal.aborted) break;
      var recipient = batch.recipients[batch.index];
      try {
        await window.MIFP.request(form.dataset.batchUrl, { method: 'POST', json: content(recipient), signal: batch.controller.signal, timeout: 30000 });
        batch.accepted += 1;
      } catch (error) {
        if (error.name === 'AbortError') break;
        batch.failed.push(recipient);
      }
      batch.index += 1;
      updateProgress();
      if (batch.index < batch.recipients.length) await delay(350);
    }
    batch.running = false;
    updateProgress();
    toggleMode();
    await audit(batch.failed.length === batch.recipients.length ? 'failed' : 'completed');
    show('Batch finished: ' + batch.accepted + ' accepted, ' + batch.failed.length + ' failed.', batch.failed.length > 0);
  }

  form.addEventListener('submit', function (event) {
    if (!fileMode()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!mapped || !mapped.valid.length || batch.running) return show('Choose a recipient file with at least one valid email.', true);
    if (!form.querySelector('[name="subject"]').value.trim() || !form.querySelector('[name="mail_title"]').value.trim()) return show('Subject and email title are required.', true);
    var message = form.querySelector('[data-notification-plain-toggle]').checked ? form.querySelector('[data-notification-plain-input]').value : form.querySelector('[data-notification-rich-input]').innerText;
    if (!message.trim()) return show('Message is required.', true);
    run(mapped.valid).catch(function (error) { batch.running = false; updateProgress(); toggleMode(); show(error.message, true); });
  });

  form.querySelector('[data-email-send-test]').addEventListener('click', async function () {
    var email = form.querySelector('[data-email-test-address]').value.trim();
    if (!email) return show('Enter a test recipient email.', true);
    try { await window.MIFP.request(form.dataset.batchUrl, { method: 'POST', json: content({ email: email, first_name: '', last_name: '' }) }); show('Test email accepted by SMTP.'); }
    catch (error) { show(error.message, true); }
  });
  form.querySelector('[data-email-batch-pause]').addEventListener('click', function () { batch.paused = true; updateProgress(); });
  form.querySelector('[data-email-batch-resume]').addEventListener('click', function () { batch.paused = false; updateProgress(); });
  form.querySelector('[data-email-batch-retry]').addEventListener('click', function () { if (batch.failed.length) run(batch.failed).catch(function (error) { show(error.message, true); }); });
  window.addEventListener('pagehide', function () { if (batch.controller) batch.controller.abort(); }, { once: true });
  toggleMode();
})();

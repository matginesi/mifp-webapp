(function () {
  'use strict';
  var form = document.querySelector('[data-direct-email-form]');
  if (!form) return;
  var filePanel = form.querySelector('[data-email-batch-panel]');
  if (!filePanel) return;
  var directField = form.querySelector('[data-direct-recipient-fields]');
  var copyFields = form.querySelector('.notification-copy-fields');
  var attachmentField = form.querySelector('.notification-attachment-field');
  var submit = form.querySelector('[data-email-submit]');
  var tableData = null;
  var mapped = null;
  var templateValid = false;
  var previewRow = form.querySelector('[data-email-preview-row]');
  var variableError = form.querySelector('[data-email-variable-error]');
  var batch = { running: false, paused: false, recipients: [], failed: [], index: 0, accepted: 0, controller: null, started: 0 };

  function fileMode() { return form.dataset.emailMode === 'file'; }
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
    submit.disabled = submit.hasAttribute('data-smtp-disabled') || (enabled && (!mapped || mapped.valid.length === 0 || !templateValid || batch.running));
  }


  function fill(select, headers, optional) {
    select.replaceChildren();
    if (optional) { var none = document.createElement('option'); none.value = ''; none.textContent = 'Not mapped'; select.append(none); }
    headers.forEach(function (header, index) { var option = document.createElement('option'); option.value = String(index); option.textContent = header; select.append(option); });
  }

  function refreshMapping() {
    mapped = null;
    templateValid = false;
    previewRow.replaceChildren();
    previewRow.disabled = true;
    form.querySelector('[data-email-recipient-summary]').hidden = true;
    if (!tableData) return toggleMode();
    var selected = {};
    form.querySelectorAll('[data-email-map]').forEach(function (select) { selected[select.dataset.emailMap] = select.value; });
    if (selected.email === '') return toggleMode();
    try {
      mapped = window.MIFPRecipientFiles.recipients(tableData, selected, true);
      var names = Array.from(new Set(window.MIFPRecipientFiles.variableNames(tableData).concat(['first_name', 'last_name'])));
      form.querySelector('[data-email-variables]').textContent = names.map(function (name) { return '{{' + name + '}}'; }).join(', ');
      ['total', 'invalid', 'duplicate'].forEach(function (key) { form.querySelector('[data-email-count="' + key + '"]').textContent = mapped[key]; });
      form.querySelector('[data-email-count="valid"]').textContent = mapped.valid.length;
      form.querySelector('[data-email-recipient-summary]').hidden = false;
      previewRow.replaceChildren();
      mapped.valid.forEach(function (recipient, index) {
        var option = document.createElement('option');
        option.value = String(index);
        option.textContent = (index + 1) + '. ' + recipient.email;
        previewRow.append(option);
      });
      previewRow.disabled = mapped.valid.length === 0;
      updatePreview();
    } catch (error) { mapped = null; updatePreview(); show(error.message, true); }
    toggleMode();
  }

  form.querySelectorAll('[data-email-map]').forEach(function (select) { select.addEventListener('change', refreshMapping); });

  form.querySelector('[data-email-recipient-file]').addEventListener('change', async function (event) {
    mapped = null;
    updatePreview();
    try {
      tableData = await window.MIFPRecipientFiles.parse(event.target.files[0]);
      var guesses = window.MIFPRecipientFiles.mapping(tableData);
      form.querySelectorAll('[data-email-map]').forEach(function (select) {
        fill(select, tableData.headers, select.dataset.emailMap !== 'email');
        select.value = guesses[select.dataset.emailMap];
      });
      form.querySelector('[data-email-recipient-mapping]').hidden = false;
      refreshMapping();
    } catch (error) { tableData = null; mapped = null; templateValid = false; previewRow.replaceChildren(); previewRow.disabled = true; updatePreview(); toggleMode(); show(error.message, true); }
  });

  function content(recipient, template) {
    var plainMode = form.querySelector('[data-notification-plain-toggle]').checked;
    var rich = form.querySelector('[data-notification-rich-input]');
    var plain = form.querySelector('[data-notification-plain-input]');
    var result = {
      email: recipient.email,
      first_name: recipient.first_name || '',
      last_name: recipient.last_name || '',
      variables: recipient.variables || {},
      plain_text_only: plainMode,
      subject: form.querySelector('[name="subject"]').value,
      mail_title: form.querySelector('[name="mail_title"]').value,
      message: plainMode ? plain.value : rich.innerText,
      message_html: plainMode ? '' : rich.innerHTML,
    };
    if (template) ['subject', 'mail_title', 'message', 'message_html', 'plain_text_only'].forEach(function (key) { result[key] = template[key]; });
    return result;
  }

  function personalize(value, variables) {
    return value.replace(/{{([^{}]*)}}/g, function (_, key) {
      key = key.trim();
      if (!/^[a-z][a-z0-9_]{0,63}$/.test(key)) throw new Error('Use lowercase column names and underscores in placeholders.');
      if (!Object.prototype.hasOwnProperty.call(variables, key)) throw new Error('Unknown placeholder: ' + key + '. Check the file columns.');
      return variables[key];
    });
  }

  function updatePreview() {
    templateValid = false;
    variableError.hidden = true;
    var recipient = mapped && mapped.valid[Number(previewRow.value) || 0];
    if (recipient) {
      var message = content(recipient);
      try {
        ['email', 'subject', 'mail_title', 'message'].forEach(function (key) {
          var value = key === 'email' ? message.email : key === 'mail_title' && message.plain_text_only ? '' : personalize(message[key], recipient.variables);
          form.querySelector('[data-email-preview="' + key + '"]').textContent = value || '—';
        });
        if (!message.plain_text_only) personalize(message.message_html, recipient.variables);
        templateValid = true;
      } catch (error) { variableError.textContent = error.message; variableError.hidden = false; }
    } else {
      form.querySelectorAll('[data-email-preview]').forEach(function (item) { item.textContent = '—'; });
    }
    form.querySelector('[data-email-send-test]').disabled = !templateValid || batch.running;
    toggleMode();
  }

  previewRow.addEventListener('change', updatePreview);
  form.addEventListener('input', updatePreview);
  form.querySelector('[data-notification-plain-toggle]').addEventListener('change', updatePreview);

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
    if (batch.running || !templateValid) return;
    var template = content(recipients[0]);
    var password = await window.MIFPConfirmSend();
    if (!password) return;
    try {
      batch = { running: true, paused: false, recipients: recipients.slice(), failed: [], index: 0, accepted: 0, controller: new AbortController(), started: Date.now() };
      updateProgress();
      await audit('started');
      while (batch.index < batch.recipients.length && !batch.controller.signal.aborted) {
        while (batch.paused && !batch.controller.signal.aborted) await delay(200);
        if (batch.controller.signal.aborted) break;
        var recipient = batch.recipients[batch.index];
        try {
          await window.MIFP.request(form.dataset.batchUrl, { method: 'POST', json: Object.assign(content(recipient, template), { password: password }), signal: batch.controller.signal, timeout: 30000 });
          batch.accepted += 1;
        } catch (error) {
          if (error.name === 'AbortError') break;
          if (error.status === 403) {
            batch.failed.push.apply(batch.failed, batch.recipients.slice(batch.index));
            batch.index = batch.recipients.length;
            show(error.message, true);
            break;
          }
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
    } finally { password = ''; updatePreview(); }
  }

  form.addEventListener('submit', function (event) {
    if (!fileMode()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!mapped || !mapped.valid.length || batch.running) return show('Choose a recipient file with at least one valid email.', true);
    if (!form.querySelector('[name="subject"]').value.trim() || (!form.querySelector('[data-notification-plain-toggle]').checked && !form.querySelector('[name="mail_title"]').value.trim())) return show('Subject and email title are required.', true);
    var message = form.querySelector('[data-notification-plain-toggle]').checked ? form.querySelector('[data-notification-plain-input]').value : form.querySelector('[data-notification-rich-input]').innerText;
    if (!message.trim()) return show('Message is required.', true);
    run(mapped.valid).catch(function (error) { batch.running = false; updateProgress(); toggleMode(); show(error.message, true); });
  });

  form.querySelector('[data-email-send-test]').addEventListener('click', async function () {
    var email = form.querySelector('[data-email-test-address]').value.trim();
    if (!email) return show('Enter a test recipient email.', true);
    var sample = mapped && mapped.valid[Number(previewRow.value) || 0];
    if (!sample || !templateValid) return show('Load a file and review its placeholders first.', true);
    var message = content(Object.assign({}, sample, { email: email }));
    var password = await window.MIFPConfirmSend();
    if (!password) return;
    try { await window.MIFP.request(form.dataset.batchUrl, { method: 'POST', json: Object.assign(message, { password: password }) }); show('Test email accepted by SMTP.'); }
    catch (error) { show(error.message, true); }
    finally { password = ''; }
  });
  form.querySelector('[data-email-batch-pause]').addEventListener('click', function () { batch.paused = true; updateProgress(); });
  form.querySelector('[data-email-batch-resume]').addEventListener('click', function () { batch.paused = false; updateProgress(); });
  form.querySelector('[data-email-batch-retry]').addEventListener('click', function () { if (batch.failed.length) run(batch.failed).catch(function (error) { show(error.message, true); }); });
  window.addEventListener('pagehide', function () { if (batch.controller) batch.controller.abort(); }, { once: true });
  updatePreview();
})();

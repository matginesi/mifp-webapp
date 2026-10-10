/* Reuse the dashboard totem/styles; server work has no measurable percentage. */
(function () {
  'use strict';
  var panel = document.getElementById('registrationOperation');
  if (!panel) return;
  var totem = document.getElementById('registrationOperationTotem');
  var title = document.getElementById('registrationOperationTitle');
  var meta = document.getElementById('registrationOperationMeta');
  var state = document.getElementById('registrationOperationState');
  var bar = panel.querySelector('[role="progressbar"]');
  var busy = false;

  function update(label, detail, tone, percent) {
    panel.hidden = false;
    panel.setAttribute('aria-busy', String(tone === 'working'));
    title.textContent = label;
    meta.textContent = detail;
    totem.dataset.state = tone;
    state.textContent = tone === 'working' ? 'Working' : tone === 'success' ? 'Completed' : 'Failed';
    window.MIFPUI.setProgressBar(bar, bar.querySelector('span'), null, percent || 0);
    if (percent === undefined) {
      bar.removeAttribute('aria-valuenow');
      bar.classList.toggle('is-loading', tone === 'working');
    }
  }

  document.addEventListener('submit', async function (event) {
    var form = event.target.closest('[data-registration-operation]');
    if (!form) return;
    if (busy) { event.preventDefault(); return; }
    var button = event.submitter || form.querySelector('button[type="submit"]');
    var download = form.dataset.registrationOperation === 'export';
    var label = download ? 'Export · ' + button.textContent.trim() :
      form.dataset.registrationOperation === 'import' ? 'Import registrations' :
      button && button.value === 'retry' ? 'Request notification recovery' : 'Delete registrations';
    update(label, download ? 'Preparing document…' : 'Verifying authorization and processing the request…', 'working');
    panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    busy = true;
    // Mutations use native navigation: never replay a destructive request.
    if (!download) return;
    event.preventDefault();
    var body = new FormData(form);
    var buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
    buttons.forEach(function (item) { item.disabled = true; });
    try {
      var response = await fetch(button.formAction || form.action, {
        method: 'POST', body: body, credentials: 'same-origin'
      });
      var disposition = response.headers.get('Content-Disposition') || '';
      if (!response.ok || !disposition.includes('attachment')) {
        throw new Error(response.ok ? 'Export unavailable. Check your session and try again.' : window.MIFP.safeMessage(response.status, null));
      }
      var length = Number(response.headers.get('Content-Length'));
      var measurable = length > 0 && !response.headers.get('Content-Encoding');
      var blob;
      if (measurable && response.body) {
        var reader = response.body.getReader();
        var chunks = [], received = 0;
        while (true) {
          var chunk = await reader.read();
          if (chunk.done) break;
          chunks.push(chunk.value);
          received += chunk.value.length;
          update(label, 'Receiving document…', 'working', Math.min(99, Math.floor(received * 100 / length)));
        }
        blob = new Blob(chunks, { type: response.headers.get('Content-Type') });
      } else {
        update(label, 'Receiving document…', 'working');
        blob = await response.blob();
      }
      var match = disposition.match(/filename="([^"\r\n]+)"/);
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url;
      link.download = match ? match[1] : 'registrations';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      update(label, 'Document ready; download started.', 'success', 100);
    } catch (error) {
      window.MIFPLog?.warn('registrations.export_failed', { status: response ? response.status : 0 });
      update(label, error.message || 'Export failed. Check your connection.', 'error', 0);
    } finally {
      form.querySelector('input[name="password"]').value = '';
      buttons.forEach(function (item) { item.disabled = false; });
      busy = false;
    }
  });
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) { busy = false; panel.hidden = true; }
  });
}());

(function () {
  'use strict';
  var app = document.querySelector('[data-respond-app]');
  if (!app) return;
  var state = app.querySelector('[data-respond-state]');
  var form = app.querySelector('[data-respond-form]');
  var pollContainer = app.querySelector('[data-respond-poll]');
  var success = app.querySelector('[data-respond-success]');
  var csrf = app.dataset.csrfToken || '';
  var poll = null;

  function showError(message) {
    state.hidden = false;
    form.hidden = true;
    state.replaceChildren();
    var heading = document.createElement('h1');
    heading.textContent = 'Poll unavailable';
    var detail = document.createElement('p');
    detail.textContent = message;
    state.append(heading, detail);
  }

  async function request(path, options) {
    options = options || {};
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, 20000);
    var headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.json !== undefined) {
      headers.set('Content-Type', 'application/json');
      headers.set('X-CSRF-Token', csrf);
      options.body = JSON.stringify(options.json);
    }
    try {
      var response = await fetch(path, Object.assign({}, options, { headers: headers, credentials: 'same-origin', signal: controller.signal }));
      var payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'The poll request failed.');
      return payload;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function load() {
    var parameters = new URLSearchParams(window.location.hash.slice(1));
    var pollId = parameters.get('p') || '';
    var token = parameters.get('t') || '';
    if (window.location.hash) history.replaceState(null, '', window.location.pathname);
    try {
      if (pollId && token) {
        await request('/respond/exchange', { method: 'POST', json: { poll_id: pollId, token: token } });
      }
      var payload = await request('/respond/poll');
      poll = payload.poll;
      window.MIFPPollRenderer.render(pollContainer, poll, { disabled: poll.locked });
      state.hidden = true;
      form.hidden = false;
      if (poll.locked) {
        form.querySelector('[data-respond-submit]').hidden = true;
        var note = document.createElement('p');
        note.className = 'poll-locked-note';
        note.textContent = 'Your response has already been received and this poll does not allow changes.';
        form.querySelector('.poll-response-actions').prepend(note);
      }
    } catch (error) {
      showError(error.message || 'The invitation link is invalid or expired.');
    }
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (!form.reportValidity()) return;
    var button = form.querySelector('[data-respond-submit]');
    button.disabled = true;
    button.textContent = 'Submitting…';
    try {
      await request('/respond/submit', { method: 'POST', json: { answers: window.MIFPPollRenderer.answers(pollContainer) } });
      form.hidden = true;
      success.hidden = false;
    } catch (error) {
      showError(error.message || 'Your response could not be submitted.');
    } finally {
      button.disabled = false;
      button.textContent = 'Submit response';
    }
  });

  load();
})();

/* Shared dashboard HTTP boundary. Server responses are treated as data, never HTML. */
(function () {
  'use strict';

  var activeForms = new WeakSet();

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function safeMessage(status, payload) {
    var defaults = {
      401: 'Your session has expired. Sign in and try again.',
      403: 'You do not have permission to perform this action.',
      422: 'Review the highlighted information and try again.',
      429: 'Too many requests. Wait a moment and try again.',
    };
    var message = defaults[status] || 'The request could not be completed.';
    if (![401, 403].includes(status) && payload && typeof payload === 'object') {
      var value = payload.message || payload.error;
      if (typeof value === 'string' && value.length <= 2000 && !/<[a-z][\s\S]*>/i.test(value)) {
        message = value.replace(/_/g, ' ');
      }
    }
    if (payload && typeof payload.request_id === 'string' && payload.request_id && payload.request_id !== '-') {
      message += ' Reference: ' + payload.request_id + '.';
    }
    return message;
  }

  async function request(url, options) {
    options = options || {};
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, options.timeout || 30000);
    var externalSignal = options.signal;
    var forwardAbort = function () { controller.abort(); };
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', forwardAbort, { once: true });
    }
    var headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (!headers.has('X-CSRF-Token') && !/^(GET|HEAD)$/i.test(options.method || 'GET')) {
      headers.set('X-CSRF-Token', csrfToken());
    }
    if (options.json !== undefined) {
      headers.set('Content-Type', 'application/json');
      options.body = JSON.stringify(options.json);
    }
    try {
      var response = await fetch(url, Object.assign({}, options, {
        credentials: 'same-origin',
        headers: headers,
        signal: controller.signal,
      }));
      var contentType = response.headers.get('content-type') || '';
      var payload = null;
      if (contentType.includes('application/json')) {
        try {
          payload = await response.json();
        } catch (_) {
          var invalidResponse = new Error('The server returned an invalid response.');
          invalidResponse.status = response.status;
          throw invalidResponse;
        }
      }
      if (!response.ok) {
        var error = new Error(safeMessage(response.status, payload));
        error.status = response.status;
        error.payload = payload;
        if (window.MIFPLog && typeof window.MIFPLog.error === 'function') {
          window.MIFPLog.error('api.request_failed', {
            method: String(options.method || 'GET').toUpperCase(),
            path: new URL(url, window.location.origin).pathname,
            status: response.status,
            message: error.message,
            request_id: response.headers.get('X-Request-ID') || undefined,
          });
        }
        throw error;
      }
      return { response: response, data: payload };
    } catch (error) {
      if (error.name === 'AbortError') {
        if (externalSignal && externalSignal.aborted) throw error;
        throw new Error('The request timed out. Try again.');
      }
      if (error instanceof TypeError) throw new Error('The network is unavailable. Check the connection and try again.');
      throw error;
    } finally {
      window.clearTimeout(timeout);
      if (externalSignal) externalSignal.removeEventListener('abort', forwardAbort);
    }
  }

  async function once(form, task) {
    if (activeForms.has(form)) return;
    activeForms.add(form);
    form.setAttribute('aria-busy', 'true');
    try {
      return await task();
    } finally {
      activeForms.delete(form);
      form.removeAttribute('aria-busy');
    }
  }

  window.MIFP = Object.freeze({
    request: request,
    once: once,
    csrfToken: csrfToken,
    safeMessage: safeMessage,
  });
})();

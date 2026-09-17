/* Shared, privacy-safe browser diagnostics for public and dashboard pages. */
(function () {
  'use strict';

  if (window.MIFPLog) return;

  var LEVELS = Object.freeze({ debug: 10, info: 20, warn: 30, error: 40, silent: 50 });
  var METHODS = Object.freeze({ debug: 'debug', info: 'info', warn: 'warn', error: 'error' });
  var SENSITIVE_KEY = /(?:pass(?:word|wd)?|secret|token|csrf|authorization|cookie|session|api[-_]?key|private[-_]?key|email|phone)/i;
  var EMAIL = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
  var CREDENTIAL = /\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+/gi;
  var SECRET_VALUE = /([?&;\s](?:pass(?:word|wd)?|secret|token|csrf|authorization|cookie|session|api[-_]?key)=)[^&;\s]+/gi;
  var configuredLevel = String(document.documentElement.dataset.mifpLogLevel || 'warn').toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(LEVELS, configuredLevel)) configuredLevel = 'warn';
  var threshold = LEVELS[configuredLevel];

  function redact(value, key, depth) {
    depth = depth || 0;
    if (key && SENSITIVE_KEY.test(String(key))) return '[REDACTED]';
    if (value == null || typeof value === 'boolean' || typeof value === 'number') return value;
    if (typeof value === 'string') {
      return value.slice(0, 1000)
        .replace(EMAIL, '[REDACTED_EMAIL]')
        .replace(CREDENTIAL, '[REDACTED_CREDENTIAL]')
        .replace(SECRET_VALUE, '$1[REDACTED]');
    }
    if (value instanceof Error) {
      return {
        name: String(value.name || 'Error').slice(0, 80),
        message: redact(value.message || 'Unknown error', 'message', depth + 1),
        stack: redact(value.stack || '', 'stack', depth + 1),
      };
    }
    if (depth >= 3) return '[MAX_DEPTH]';
    if (Array.isArray(value)) {
      return value.slice(0, 20).map(function (item) { return redact(item, '', depth + 1); });
    }
    if (typeof value === 'object') {
      var output = {};
      Object.keys(value).slice(0, 40).forEach(function (itemKey) {
        output[itemKey] = redact(value[itemKey], itemKey, depth + 1);
      });
      return output;
    }
    return String(value).slice(0, 300);
  }

  function safePath(value) {
    try {
      var url = new URL(String(value || ''), window.location.origin);
      return url.origin === window.location.origin ? url.pathname : url.origin + url.pathname;
    } catch (_) {
      return String(value || '').split('?')[0].slice(0, 500);
    }
  }

  function moduleFor(eventName) {
    var name = String(eventName || 'browser.event');
    return (name.split(/[.:]/, 1)[0] || 'browser').slice(0, 40);
  }

  function emit(level, eventName, details) {
    if (!Object.prototype.hasOwnProperty.call(LEVELS, level) || LEVELS[level] < threshold) return;
    if (typeof console === 'undefined') return;
    var event = String(eventName || 'browser.event').slice(0, 120);
    var payload = {
      event: event,
      module: moduleFor(event),
      page: document.querySelector('[data-dashboard-view]')?.dataset.dashboardView || 'public',
      path: window.location.pathname,
      timestamp: new Date().toISOString(),
      details: redact(details || {}, '', 0),
    };
    var serialized;
    try {
      serialized = JSON.stringify(payload);
    } catch (_) {
      serialized = JSON.stringify({
        event: payload.event,
        module: payload.module,
        path: payload.path,
        timestamp: payload.timestamp,
        details: '[UNSERIALIZABLE]',
      });
    }
    var method = METHODS[level] || 'log';
    (console[method] || console.log).call(
      console,
      '[MIFP][' + level.toUpperCase() + '][' + payload.module + '] ' + serialized
    );
  }

  function installGlobalDiagnostics() {
    if (window.__mifpDiagnosticsInstalled) return;
    window.__mifpDiagnosticsInstalled = true;
    var nativeFetch = window.fetch;
    if (typeof nativeFetch === 'function') {
      window.fetch = async function (input, init) {
        var method = String(init?.method || input?.method || 'GET').toUpperCase();
        var path = safePath(input?.url || input);
        var started = performance.now();
        emit('debug', 'http.request', { method: method, path: path });
        try {
          var response = await nativeFetch.apply(this, arguments);
          var duration = Math.round(performance.now() - started);
          var context = {
            method: method,
            path: path,
            status: response.status,
            duration_ms: duration,
            request_id: response.headers.get('X-Request-ID') || undefined,
          };
          if (response.status >= 500) emit('error', 'http.response', context);
          else if (duration >= 2000) emit('warn', 'http.slow', context);
          else emit('debug', 'http.response', context);
          return response;
        } catch (error) {
          emit(error && error.name === 'AbortError' ? 'debug' : 'error', error && error.name === 'AbortError' ? 'http.aborted' : 'http.network_error', {
            method: method,
            path: path,
            duration_ms: Math.round(performance.now() - started),
            error: error,
          });
          throw error;
        }
      };
    }

    var nativeOpen = XMLHttpRequest.prototype.open;
    var nativeSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__mifpLogMeta = {
        method: String(method || 'GET').toUpperCase(),
        path: safePath(url),
      };
      return nativeOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      var xhr = this;
      var context = xhr.__mifpLogMeta || { method: 'GET', path: 'unknown' };
      var started = performance.now();
      var aborted = false;
      emit('debug', 'xhr.request', context);
      xhr.addEventListener('abort', function () { aborted = true; }, { once: true });
      xhr.addEventListener('loadend', function () {
        var duration = Math.round(performance.now() - started);
        var details = {
          method: context.method,
          path: context.path,
          status: xhr.status,
          duration_ms: duration,
          request_id: xhr.getResponseHeader('X-Request-ID') || undefined,
        };
        if (aborted) emit('debug', 'xhr.aborted', details);
        else if (xhr.status === 0 || xhr.status >= 500) emit('error', 'xhr.response', details);
        else if (duration >= 2000) emit('warn', 'xhr.slow', details);
        else emit('debug', 'xhr.response', details);
      }, { once: true });
      return nativeSend.apply(this, arguments);
    };

    window.addEventListener('error', function (event) {
      if (event.target && event.target !== window) {
        emit('warn', 'resource.load_failed', {
          element: event.target.tagName,
          source: safePath(event.target.currentSrc || event.target.src || event.target.href || ''),
        });
        return;
      }
      emit('error', 'javascript.error', {
        message: event.message,
        source: safePath(event.filename),
        line: event.lineno,
        column: event.colno,
        error: event.error,
      });
    }, true);

    window.addEventListener('unhandledrejection', function (event) {
      emit('error', 'javascript.unhandled_rejection', { reason: event.reason });
    });
  }

  window.MIFPLog = Object.freeze({
    debug: function (eventName, details) { emit('debug', eventName, details); },
    info: function (eventName, details) { emit('info', eventName, details); },
    warn: function (eventName, details) { emit('warn', eventName, details); },
    error: function (eventName, details) { emit('error', eventName, details); },
    redact: function (value) { return redact(value, '', 0); },
    safePath: safePath,
    level: configuredLevel,
  });
  installGlobalDiagnostics();
  emit('debug', 'page.ready', { title: document.title });
})();

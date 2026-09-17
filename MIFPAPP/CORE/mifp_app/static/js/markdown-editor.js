/* Shared tabs and safe Markdown preview for institutional editors. */
(function () {
  'use strict';

  var ALLOWED_TAGS = new Set([
    'A', 'BLOCKQUOTE', 'BR', 'CODE', 'DEL', 'EM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
    'HR', 'LI', 'OL', 'P', 'PRE', 'STRONG', 'TABLE', 'TBODY', 'TD', 'TH', 'THEAD', 'TR', 'UL'
  ]);

  function safeLink(value) {
    var href = String(value || '').trim();
    if (!href) return '';
    if (href.startsWith('#') || href.startsWith('/')) return href;
    try {
      var url = new URL(href, window.location.origin);
      return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? href : '';
    } catch (_) {
      return '';
    }
  }

  function sanitizeMarkdown(html) {
    var template = document.createElement('template');
    template.innerHTML = String(html || '');
    Array.from(template.content.querySelectorAll('*')).forEach(function (element) {
      if (!ALLOWED_TAGS.has(element.tagName)) {
        element.replaceWith(document.createTextNode(element.textContent || ''));
        return;
      }
      var href = element.tagName === 'A' ? safeLink(element.getAttribute('href')) : '';
      Array.from(element.attributes).forEach(function (attribute) {
        element.removeAttribute(attribute.name);
      });
      if (href) {
        element.setAttribute('href', href);
        if (/^https?:/i.test(href)) element.setAttribute('rel', 'noopener noreferrer');
      }
    });
    return template.content;
  }

  function renderPreview(textarea) {
    var shell = textarea.closest('.markdown-editor-shell');
    var preview = shell && shell.querySelector('.markdown-preview-body');
    if (!preview) return;
    if (!window.marked || typeof window.marked.parse !== 'function') {
      preview.textContent = textarea.value || '';
      window.MIFPLog?.error('markdown.preview_unavailable', { reason: 'marked_not_loaded' });
      return;
    }
    try {
      preview.replaceChildren(sanitizeMarkdown(window.marked.parse(textarea.value || '')));
    } catch (error) {
      preview.textContent = textarea.value || '';
      window.MIFPLog?.error('markdown.preview_failed', { error: error });
    }
  }

  function initTabs() {
    var tabs = document.querySelectorAll('.dash-tabs[role="tablist"] .dash-tab');
    if (!tabs.length) return;
    function activate(tab) {
      if (!tab) return;
      tabs.forEach(function (item) {
        var active = item === tab;
        item.classList.toggle('active', active);
        item.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      document.querySelectorAll('.dash-tab-content').forEach(function (panel) {
        panel.hidden = panel.id !== 'tab-' + tab.dataset.tab;
      });
    }
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () { activate(tab); });
    });
    var requested = new URLSearchParams(window.location.search).get('tab');
    if (requested) {
      activate(Array.from(tabs).find(function (tab) { return tab.dataset.tab === requested; }));
    }
  }

  function initEditor(shell) {
    var textarea = shell.querySelector('textarea');
    if (!textarea) return;
    var toolbar = shell.querySelector('.markdown-editor-toolbar');
    if (toolbar) {
      toolbar.addEventListener('click', function (event) {
        var button = event.target.closest('[data-md-before]');
        if (!button || !toolbar.contains(button)) return;
        var before = button.dataset.mdBefore || '';
        var after = button.dataset.mdAfter || '';
        var start = textarea.selectionStart;
        var end = textarea.selectionEnd;
        var selected = textarea.value.substring(start, end);
        textarea.setRangeText(before + selected + after, start, end, 'select');
        textarea.selectionStart = start + before.length;
        textarea.selectionEnd = start + before.length + selected.length;
        textarea.focus();
        renderPreview(textarea);
      });
    }
    textarea.addEventListener('input', function () { renderPreview(textarea); });
    renderPreview(textarea);
  }

  initTabs();
  document.querySelectorAll('.markdown-editor-shell').forEach(initEditor);

  var bannerCard = document.querySelector('.banner-settings-card');
  if (bannerCard) {
    var bannerText = bannerCard.querySelector('[data-banner-text]');
    var previewText = bannerCard.querySelector('[data-banner-preview-text]');
    var count = bannerCard.querySelector('[data-banner-count]');
    var enabled = bannerCard.querySelector('[data-banner-enabled]');
    var status = bannerCard.querySelector('[data-banner-status]');
    var theme = bannerCard.querySelector('[data-banner-theme]');
    var notice = bannerCard.querySelector('.banner-preview-notice');
    var linkToggle = bannerCard.querySelector('[data-banner-link]');
    var previewLink = bannerCard.querySelector('[data-banner-preview-link]');
    var dismiss = bannerCard.querySelector('[data-banner-dismiss]');
    var previewDismiss = bannerCard.querySelector('[data-banner-preview-dismiss]');
    var controls = [bannerText, dismiss, enabled, theme, linkToggle];
    if (controls.every(Boolean) && previewText && count && status && notice && previewLink && previewDismiss) {
      var fallback = 'This site uses a session cookie for admin authentication. No tracking or analytics cookies are used.';
      var updateBannerPreview = function () {
        previewText.textContent = bannerText.value.trim() || fallback;
        count.textContent = bannerText.value.length;
        notice.classList.toggle('is-hidden', enabled.value !== '1');
        notice.classList.toggle('theme-brand', theme.value === 'brand');
        notice.classList.toggle('theme-neutral', theme.value === 'neutral');
        previewLink.hidden = !linkToggle.checked;
        previewDismiss.textContent = dismiss.value.trim() || 'Dismiss';
        status.textContent = enabled.value === '1' ? 'Visible' : 'Hidden';
        status.className = 'status-badge ' + (enabled.value === '1' ? 'status-success' : 'status-neutral');
        status.setAttribute('data-banner-status', '');
      };
      controls.forEach(function (control) {
        control.addEventListener(control === bannerText || control === dismiss ? 'input' : 'change', updateBannerPreview);
      });
      updateBannerPreview();
    } else {
      window.MIFPLog?.warn('privacy.preview_incomplete', { reason: 'missing_control' });
    }
  }
})();

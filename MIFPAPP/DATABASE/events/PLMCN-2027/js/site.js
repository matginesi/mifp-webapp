/*
 * MIFP Static Conference Template — v1.6.4
 * ------------------------------------------------------------
 * This is the only application runtime used by the v5 HTML pages.
 * Responsibilities are intentionally kept explicit:
 *   1. load conference.yaml + CSV files;
 *   2. apply theme and section toggles;
 *   3. render only elements carrying data-render;
 *   4. build navigation/footer/privacy UI;
 *   5. expose diagnostics only when runtime.debug=true.
 *
 * If you remove data-render from an HTML block, this file leaves it alone.
 */
(function () {
  'use strict';

  const VERSION = '1.6.8';
  const SCRIPT_URL = document.currentScript && document.currentScript.src
    ? new URL(document.currentScript.src)
    : new URL('js/site.js', document.baseURI);
  const SITE_ROOT = new URL('../', SCRIPT_URL);
  const CONFIG_URL = new URL('conference.yaml', SITE_ROOT);

  const state = {
    config: null,
    people: [],
    peopleHeaders: [],
    program: [],
    programHeaders: [],
    logs: [],
    paths: { siteRoot: SITE_ROOT.href, runtime: SCRIPT_URL.href, config: CONFIG_URL.href },
    debugOpen: false
  };

  // -------------------------------------------------------------------------
  // Small helpers
  // -------------------------------------------------------------------------
  const q = (selector, root) => (root || document).querySelector(selector);
  const qa = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const arr = (value) => Array.isArray(value) ? value : [];
  const str = (value, fallback = '') => value == null ? fallback : String(value);
  const esc = (value) => str(value).replace(/[&<>"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));
  const truthy = (value) => /^(1|true|yes|y|visible)$/i.test(str(value).trim());

  // Turn a scalar YAML value into display text. If a lightweight YAML parser
  // produced a one-key object from an unquoted `key: value` fragment, keep the
  // renderer alive and show a readable representation instead of failing.
  function displayValue(value) {
    if (value == null) return '';
    if (typeof value !== 'object') return str(value);
    const entries = Object.entries(value);
    if (entries.length === 1) return `${entries[0][0]}: ${displayValue(entries[0][1])}`;
    return entries.map(([key, item]) => `${key}: ${displayValue(item)}`).join(' · ');
  }

  function get(path, fallback) {
    let node = state.config;
    for (const part of String(path || '').split('.')) {
      if (!part) continue;
      if (node == null || typeof node !== 'object' || !(part in node)) return fallback;
      node = node[part];
    }
    return node == null ? fallback : node;
  }

  function sectionEnabled(key) {
    const section = get(key, null);
    return !(section && typeof section === 'object' && section.enabled === false);
  }

  // Local YAML asset/file paths are always resolved from the real site root,
  // derived from js/site.js. No /conference/ or other deployment folder exists
  // in this runtime.
  function localUrl(path) {
    const value = str(path).trim();
    if (!value) return '';
    if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return value;
    if (value.includes('..')) {
      log('warn', 'security', 'Rejected local path containing ..', { path: value });
      return '';
    }
    return new URL(value.replace(/^\.\//, '').replace(/^\//, ''), SITE_ROOT).href;
  }

  function safeLink(value, fallback = '#') {
    const raw = str(value).trim();
    if (!raw) return fallback;
    if (raw.startsWith('#')) return raw;
    if (/^(mailto:|tel:)/i.test(raw)) return raw;
    if (/^https:\/\//i.test(raw)) return raw;
    if (/^http:\/\//i.test(raw)) { log('warn','security','Blocked insecure HTTP link',{value:raw}); return fallback; }
    if (/^(javascript:|data:|vbscript:)/i.test(raw)) {
      log('warn', 'security', 'Blocked unsafe link', { value: raw });
      return fallback;
    }
    return localUrl(raw) || fallback;
  }

  function configuredLink(value) {
    const raw = str(value).trim();
    if (!raw || /^(TBC|TBD)$/i.test(raw)) return '';
    return safeLink(raw, '');
  }

  function assetUrl(value) {
    const raw = str(value).trim();
    if (!raw) return '';
    if (/^https:\/\//i.test(raw)) {
      if (get('security.allow_external_assets', false) === true) return raw;
      log('warn', 'security', 'External asset blocked by configuration', { value: raw });
      return '';
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return '';
    return localUrl(raw);
  }

  function externalAttrs(href) {
    return /^https?:\/\//i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : '';
  }

  function safeFrame(value) {
    const raw=str(value).trim();
    if (/^https:\/\/www\.openstreetmap\.org\//i.test(raw)) return raw;
    if (raw) log('warn','security','Blocked unapproved iframe source',{value:raw});
    return '';
  }

  // -------------------------------------------------------------------------
  // Console logging
  // -------------------------------------------------------------------------
  const LEVELS = { trace: 10, debug: 20, info: 30, warn: 40, error: 50, silent: 99 };

  function currentThreshold() {
    const debug = get('runtime.debug', false) === true;
    const selected = str(debug ? get('runtime.debug_log_level', 'debug') : get('runtime.log_level', 'warn')).toLowerCase();
    return LEVELS[selected] || LEVELS.info;
  }

  function log(level, scope, message, data) {
    const normalized = LEVELS[level] ? level : 'info';
    const entry = {
      time: new Date().toISOString(),
      level: normalized,
      scope: str(scope, 'site'),
      message: str(message),
      data: data === undefined ? null : data
    };
    state.logs.push(entry);
    const max = Number(get('runtime.log_buffer_entries', 500)) || 500;
    if (state.logs.length > max) state.logs.splice(0, state.logs.length - max);

    if (LEVELS[normalized] < currentThreshold()) return;
    const prefix = `[${str(get('runtime.log_prefix', 'MIFP'))}][${normalized.toUpperCase()}][${entry.scope}]`;
    const method = normalized === 'error' ? 'error' : normalized === 'warn' ? 'warn' : normalized === 'debug' || normalized === 'trace' ? 'debug' : 'info';
    if (data === undefined) console[method](prefix, message);
    else console[method](prefix, message, data);
  }

  window.addEventListener('error', (event) => log('error', 'window', event.message || 'Unhandled error', { file: event.filename, line: event.lineno, column: event.colno }));
  window.addEventListener('unhandledrejection', (event) => log('error', 'promise', 'Unhandled promise rejection', { reason: str(event.reason) }));

  // -------------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------------
  async function fetchText(url, label) {
    const started = performance.now();
    const response = await fetch(url, { cache: 'default', credentials: 'same-origin', referrerPolicy: 'no-referrer' });
    if (!response.ok) throw new Error(`${label}: HTTP ${response.status} (${url})`);
    const text = await response.text();
    log('debug', 'load', `${label} loaded`, { bytes: text.length, ms: +(performance.now() - started).toFixed(1), url: url.href || str(url) });
    return text;
  }

  async function loadData() {
    const yamlText = await fetchText(CONFIG_URL, 'conference.yaml');
    state.config = window.YamlLite.parse(yamlText);

    const peoplePath = get('runtime.people_csv', 'data/people.csv');
    const programPath = get('runtime.program_csv', 'data/program.csv');
    const peopleUrl = new URL(str(peoplePath).replace(/^\.\//, '').replace(/^\//, ''), SITE_ROOT);
    const programUrl = new URL(str(programPath).replace(/^\.\//, '').replace(/^\//, ''), SITE_ROOT);
    state.paths.people = peopleUrl.href;
    state.paths.program = programUrl.href;

    const [peopleText, programText] = await Promise.all([
      fetchText(peopleUrl, 'people.csv'),
      fetchText(programUrl, 'program.csv')
    ]);
    const people = window.CsvUtil.parse(peopleText);
    const program = window.CsvUtil.parse(programText);
    state.peopleHeaders = people.headers;
    state.people = people.rows;
    state.programHeaders = program.headers;
    state.program = program.rows;
  }

  // -------------------------------------------------------------------------
  // Theme/palette. UI controls are created ONLY in debug mode.
  // -------------------------------------------------------------------------
  function findById(items, id) {
    return arr(items).find((item) => str(item.id).toLowerCase() === str(id).toLowerCase()) || null;
  }

  function applyTheme(id, remember = false) {
    const themes = get('appearance.themes', []);
    const fallback = get('appearance.default_theme', arr(themes)[0] && arr(themes)[0].id);
    const theme = findById(themes, id) || findById(themes, fallback) || arr(themes)[0];
    if (!theme) return;
    const root = document.documentElement;
    const map = {
      '--bg': theme.bg, '--bg-alt': theme.bg_alt, '--card': theme.bg_card,
      '--nav-bg': theme.nav_bg, '--nav-hover': theme.nav_hover,
      '--border': theme.border, '--border-light': theme.border_light,
      '--text': theme.text, '--muted': theme.text_muted, '--dim': theme.text_dim,
      '--heading': theme.text_heading
    };
    Object.entries(map).forEach(([key, value]) => value && root.style.setProperty(key, value));
    const scheme = String(theme.color_scheme || 'dark').toLowerCase() === 'light' ? 'light' : 'dark';
    root.style.colorScheme = scheme;
    // Solid controls keep the exact brand color; small accent text gets a
    // contrast-safe variant on dark themes. The expression still follows
    // the currently selected palette because it references --primary.
    root.style.setProperty('--primary-ink', scheme === 'dark'
      ? 'color-mix(in srgb, var(--primary) 60%, #ffffff)'
      : 'color-mix(in srgb, var(--primary) 52%, var(--heading))');
    root.style.setProperty('--danger-ink', scheme === 'dark'
      ? 'color-mix(in srgb, var(--danger) 60%, #ffffff)'
      : 'var(--danger)');
    root.dataset.theme = theme.id;
    if (remember && get('runtime.debug', false) === true && get('appearance.remember_preferences', true) !== false) localStorage.setItem('mifp-debug-theme', theme.id);
    log('debug', 'appearance', 'Theme applied', { id: theme.id });
  }

  // Pick readable foreground text for solid accent buttons. This keeps
  // every palette accessible without hard-coding a text colour per palette.
  function contrastText(hex) {
    const value = str(hex).replace('#','').trim();
    if (!/^[0-9a-f]{6}$/i.test(value)) return '#ffffff';
    const rgb = [0,2,4].map((i) => parseInt(value.slice(i,i+2),16) / 255);
    const linear = rgb.map((v) => v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055,2.4));
    const luminance = .2126*linear[0] + .7152*linear[1] + .0722*linear[2];
    return luminance > .43 ? '#111827' : '#ffffff';
  }

  function applyPalette(id, remember = false) {
    const palettes = get('appearance.palettes', []);
    const fallback = get('appearance.default_palette', arr(palettes)[0] && arr(palettes)[0].id);
    const palette = findById(palettes, id) || findById(palettes, fallback) || arr(palettes)[0];
    if (!palette) return;
    const root = document.documentElement;
    if (palette.primary) { root.style.setProperty('--primary', palette.primary); root.style.setProperty('--on-primary', contrastText(palette.primary)); }
    if (palette.secondary) root.style.setProperty('--secondary', palette.secondary);
    root.dataset.palette = palette.id;
    if (remember && get('runtime.debug', false) === true && get('appearance.remember_preferences', true) !== false) localStorage.setItem('mifp-debug-palette', palette.id);
    log('debug', 'appearance', 'Palette applied', { id: palette.id });
  }

  function initAppearance() {
    const debug = get('runtime.debug', false) === true;
    const storedTheme = debug ? localStorage.getItem('mifp-debug-theme') : null;
    const storedPalette = debug ? localStorage.getItem('mifp-debug-palette') : null;
    applyTheme(storedTheme || get('appearance.default_theme', 'midnight'));
    applyPalette(storedPalette || get('appearance.default_palette', 'emerald'));
    const width = get('appearance.max_content_width', '1080px');
    const sidebar = get('appearance.sidebar_width', '260px');
    document.documentElement.style.setProperty('--content-max', width);
    document.documentElement.style.setProperty('--sidebar-width', sidebar);
  }


  // SEO ---------------------------------------------------------------------
  // Public pages already contain useful static metadata. Once site.base_url is
  // confirmed, this adds canonical and absolute social URLs automatically.
  function initSeo() {
    const base = str(get('site.base_url', '')).trim();
    if (!/^https:\/\//i.test(base) || /\b(TBC|TBD)\b/i.test(base)) return;
    let baseUrl;
    try { baseUrl = new URL(base.endsWith('/') ? base : `${base}/`); }
    catch (_) { log('warn', 'seo', 'Invalid site.base_url', { base }); return; }
    const filename = location.pathname.split('/').pop() || 'index.html';
    const canonical = new URL(filename === '' ? 'index.html' : filename, baseUrl).href;
    let link = document.querySelector('link[rel="canonical"]');
    if (!link) { link = document.createElement('link'); link.rel = 'canonical'; document.head.appendChild(link); }
    link.href = canonical;
    const setMeta = (selector, attr, value) => {
      if (!value) return;
      let node = document.head.querySelector(selector);
      if (!node) { node = document.createElement('meta'); document.head.appendChild(node); }
      const [key, keyValue] = selector.includes('property=') ? ['property', selector.match(/property="([^"]+)"/)[1]] : ['name', selector.match(/name="([^"]+)"/)[1]];
      node.setAttribute(key, keyValue); node.setAttribute(attr, value);
    };
    setMeta('meta[property="og:url"]', 'content', canonical);
    const image = str(get('site.social_image', '')).trim();
    if (image && !/^(TBC|TBD)$/i.test(image)) {
      const absoluteImage = new URL(image.replace(/^\//,''), baseUrl).href;
      setMeta('meta[property="og:image"]', 'content', absoluteImage);
      setMeta('meta[name="twitter:image"]', 'content', absoluteImage);
    }
    log('debug','seo','Canonical metadata applied',{canonical});
  }

  // -------------------------------------------------------------------------
  // Navigation shell
  // -------------------------------------------------------------------------
  function visibleNavigation() {
    return arr(get('navigation', [])).filter((item) => {
      if (item.enabled === false) return false;
      if (item.section && !sectionEnabled(item.section)) return false;
      return true;
    });
  }

  function navHtml(mobile) {
    const page = document.body.dataset.page || 'home';
    return visibleNavigation().map((item) => {
      const href = safeLink(item.file || '#');
      const active = (page === 'home' && item.file === 'index.html') || page === item.section || page === item.page;
      return `<a href="${esc(href)}" class="${active ? 'active' : ''}">${esc(item.label)}</a>`;
    }).join('');
  }

  // Registration availability is controlled by one YAML switch:
  // registration.enabled: false hides registration content and routes all
  // registration entry points to the neutral "to be defined" page.
  function registrationEnabled() {
    return get('registration.enabled', true) !== false;
  }

  function registrationUnavailableUrl() {
    return str(get('registration.unavailable_url', 'registration-tbd.html'), 'registration-tbd.html');
  }

  function registrationActiveUrl() {
    const c = get('registration', {});
    return c.registration_url || get('conference.registration_url', '') || 'regform/';
  }

  function normalizeLocalHref(value) {
    return str(value, '').trim().replace(/^\.\//, '').split('#')[0].split('?')[0].replace(/\/+$/, '');
  }

  function registrationAwareTarget(value) {
    if (registrationEnabled()) return value;
    const candidate = normalizeLocalHref(value);
    const known = [
      'registration.html',
      registrationActiveUrl(),
      get('registration.registration_url', ''),
      get('conference.registration_url', '')
    ].map(normalizeLocalHref).filter(Boolean);
    return known.includes(candidate) ? registrationUnavailableUrl() : value;
  }

  // The global Registration CTA remains visible when registration is not yet
  // published, but it opens the explanatory page instead of a dead form.
  function registrationCta(location) {
    const c = get('registration', {});
    if (c.show_navigation_cta === false) return '';
    const href = safeLink(registrationEnabled() ? registrationActiveUrl() : registrationUnavailableUrl());
    const label = location === 'topbar' ? (c.topbar_label || 'Register') : (c.nav_label || 'Register Now');
    const cls = location === 'topbar' ? 'nav-register nav-register-top' : 'nav-register';
    return `<a class="${cls}" href="${esc(href)}">${esc(label)}</a>`;
  }

  function renderShell() {
    const short = esc(get('conference.acronym', get('site.short_name', 'Conference')));
    const year = esc(get('site.year', ''));
    const date = esc(get('conference.date_label', ''));
    const place = esc([get('conference.city', ''), get('conference.country', '')].filter(Boolean).join(', '));
    const debug = get('runtime.debug', false) === true;
    const shellLogo = assetUrl(get('assets.logo', ''));
    const logoAlt = esc(`${get('site.title', 'Conference')} logo`);

    const sidebar = q('#siteSidebar');
    if (sidebar) sidebar.innerHTML = `
      <div class="sidebar-head">
        <a class="brand" href="${esc(safeLink('index.html'))}">${shellLogo ? `<img class="sidebar-logo" src="${esc(shellLogo)}" alt="${logoAlt}">` : ''}<span>${short}</span><b>${year}</b></a>
        <div class="sidebar-meta">${date}${place ? `<br>${place}` : ''}</div>
      </div>
      <nav class="sidebar-nav" aria-label="Conference navigation">${navHtml(false)}${registrationCta('sidebar')}</nav>
      ${get('countdown.enabled', false) === true && get('countdown.show_in_sidebar', true) !== false ? '<div class="sidebar-countdown" id="sidebarCountdown" aria-live="polite"></div>' : ''}
      ${debug ? '<div class="sidebar-tools"><button type="button" class="debug-button" data-debug-open>Debug</button></div>' : ''}
      <div class="sidebar-foot"><strong>${esc(get('organization.short_name', 'MIFP'))}</strong><span>Matteo Ginesi 2026</span></div>`;

    const topbar = q('#siteTopbar');
    if (topbar) topbar.innerHTML = `
      <a class="topbar-brand" href="${esc(safeLink('index.html'))}">${shellLogo ? `<img class="topbar-logo" src="${esc(shellLogo)}" alt="${logoAlt}">` : ''}<span>${short}</span><b>${year}</b></a>
      <div class="topbar-actions">
        ${registrationCta('topbar')}
        ${debug ? '<button type="button" class="top-debug" data-debug-open>Debug</button>' : ''}
        <button type="button" class="menu-button" id="menuButton" aria-expanded="false" aria-controls="mobileNav"><span></span><span></span><span></span><span class="menu-label">Menu</span></button>
      </div>`;

    const mobile = q('#mobileNav');
    if (mobile) mobile.innerHTML = `<div class="mobile-nav-head">${shellLogo ? `<img class="mobile-nav-logo" src="${esc(shellLogo)}" alt="${logoAlt}">` : ''}<div><strong>${short} ${year}</strong><span>${date}</span></div></div><div class="mobile-nav-links">${navHtml(true)}${registrationCta('mobile')}</div>`;

    const menuButton = q('#menuButton');
    const backdrop = q('#navBackdrop');
    const closeMenu = () => {
      mobile && mobile.classList.remove('open');
      backdrop && backdrop.classList.remove('open');
      menuButton && menuButton.setAttribute('aria-expanded', 'false');
      document.body.classList.remove('menu-open');
    };
    const toggleMenu = () => {
      const open = !(mobile && mobile.classList.contains('open'));
      mobile && mobile.classList.toggle('open', open);
      backdrop && backdrop.classList.toggle('open', open);
      menuButton && menuButton.setAttribute('aria-expanded', String(open));
      document.body.classList.toggle('menu-open', open);
    };
    menuButton && menuButton.addEventListener('click', toggleMenu);
    backdrop && backdrop.addEventListener('click', closeMenu);
    qa('a', mobile).forEach((link) => link.addEventListener('click', closeMenu));
    window.addEventListener('resize', () => { if (window.innerWidth >= 1024) closeMenu(); });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeMenu();
        closeDebug();
      }
    });
  }

  // -------------------------------------------------------------------------
  // People helpers
  // -------------------------------------------------------------------------
  function personName(person) { return [person['First Name'], person['Last Name']].filter(Boolean).join(' '); }
  function canonicalRole(value) {
    const raw=str(value).trim();const key=raw.toLowerCase().replace(/[-_]+/g,' ').replace(/\s+/g,' ');
    if(['committee','committee member','committee members','program committee member','program committee members'].includes(key))return 'Program Committee';
    if(['organizing committee member','organizing committee members','organising committee','organising committee member','organising committee members'].includes(key))return 'Organizing Committee';
    if(['program committee chairman','program committee chair','program committee chairperson','scientific chairman','scientific chair'].includes(key))return 'Program Committee Chairman';
    if(['program committee co chairman','program committee co chair','program committee cochairman','program committee cochair'].includes(key))return 'Program Committee Co-chairman';
    if(['organizer','organiser','local organiser'].includes(key))return 'Local Organizer';
    if(['co chairman','co chair','cochairman','cochair','vice chairman','vice chair','organizing committee co chairman','organizing committee co chair'].includes(key))return 'Co-chairman';
    if(['conference chairman','conference chair','chair','chairperson','organizing committee chairman','organizing committee chair'].includes(key))return 'Chairman';
    return raw;
  }
  function personRoles(person) { return str(person.Role).split(/[;|]/).map((role) => canonicalRole(role)).filter(Boolean); }
  function hasRole(person, role) {
    const wanted=canonicalRole(role).toLowerCase();
    const roles=personRoles(person);const lower=roles.map((value)=>value.toLowerCase());
    if(wanted==='organizing committee') {
      if(lower.includes('organizing committee'))return true;
      if(lower.includes('program committee chairman')||lower.includes('program committee co-chairman'))return false;
      if(lower.includes('program committee')&&lower.includes('chairman'))return false;
      return lower.includes('chairman')||lower.includes('co-chairman');
    }
    if(wanted==='program committee')return lower.includes('program committee')||lower.includes('program committee chairman')||lower.includes('program committee co-chairman');
    if(wanted==='local organizer')return lower.includes('local organizer');
    return lower.includes(wanted);
  }

  function committeeRoleLabel(person, groupRole) {
    const wanted=canonicalRole(groupRole).toLowerCase();
    const roles=personRoles(person);const lower=roles.map((value)=>value.toLowerCase());
    if(wanted==='organizing committee') {
      if(lower.includes('co-chairman'))return 'Co-chairman';
      if(lower.includes('chairman'))return 'Chairman';
      return 'Organizing Committee';
    }
    if(wanted==='program committee') {
      if(lower.includes('program committee chairman')||(lower.includes('program committee')&&lower.includes('chairman')))return 'Program Committee Chairman';
      if(lower.includes('program committee co-chairman'))return 'Program Committee Co-chairman';
      return 'Program Committee';
    }
    if(wanted==='local organizer')return 'Local Organizer';
    return canonicalRole(groupRole);
  }
  function visiblePeople() { return state.people.filter((person) => truthy(person.Visible != null && person.Visible !== '' ? person.Visible : person.Visibile)); }

  function personImage(person) {
    const name = personName(person).toLowerCase();
    const override = arr(get('people_overrides', [])).find((item) => str(item.name).toLowerCase() === name);
    return assetUrl(str(person.Image).trim() || (override && override.image ? override.image : get('assets.people_fallback', 'assets/people/no_face.jpg')));
  }

  function personCard(person, compact = false, roleLabel = '') {
    const name = personName(person);
    const image = personImage(person);
    const affiliation = str(person.Affiliation).trim();
    const country = get('people.show_country', true) === true ? str(person.Country).trim() : '';
    const meta = [affiliation, country].filter(Boolean).join(' · ');
    const category = str(person.Category).trim();
    const search = esc([name, category, person.Role, person.Affiliation, person.Country].join(' ').toLowerCase());

    if (compact) {
      return `<article class="person-row" data-search="${search}">
        <img src="${esc(image)}" alt="${esc(name)}" loading="lazy">
        <div>${roleLabel ? `<span class="person-role">${esc(roleLabel)}</span>` : ''}<strong>${esc(name)}</strong>${meta ? `<small>${esc(meta)}</small>` : ''}</div>
      </article>`;
    }

    const invitedClass = hasRole(person, 'Invited Speaker') ? ' invited-speaker' : '';
    return `<article class="speaker-card${invitedClass}" data-search="${search}">
      <img src="${esc(image)}" alt="${esc(name)}" loading="lazy">
      <div class="speaker-body">${category ? `<span class="person-role">${esc(category)}</span>` : ''}<h3>${esc(name)}</h3>${meta ? `<p>${esc(meta)}</p>` : ''}</div>
    </article>`;
  }

  // -------------------------------------------------------------------------
  // Selectable image gallery widget
  // -------------------------------------------------------------------------
  function normalizedGalleryItems(items, fallbackLabel = 'Gallery image') {
    return arr(items).map((item, index) => {
      const value = typeof item === 'string' ? { src:item } : (item || {});
      const src = str(value.src || value.image).trim();
      if (!src) return null;
      return {
        src: assetUrl(src),
        alt: str(value.alt || value.caption || `${fallbackLabel} ${index + 1}`),
        caption: str(value.caption || value.alt || `${fallbackLabel} ${index + 1}`)
      };
    }).filter(Boolean);
  }

  function galleryWidget(items, options = {}) {
    const galleryItems = normalizedGalleryItems(items, options.fallbackLabel || 'Gallery image');
    if (!galleryItems.length) return '';
    const requested = Number(options.initialIndex || 0);
    const initial = Math.max(0, Math.min(galleryItems.length - 1, Number.isFinite(requested) ? requested : 0));
    const current = galleryItems[initial];
    const label = str(options.label || 'Image gallery');
    const variant = ['compact','wide'].includes(options.variant) ? options.variant : 'wide';
    const controls = galleryItems.length > 1 ? `<div class="media-gallery-controls"><button type="button" data-gallery-prev aria-label="Previous image">‹</button><span data-gallery-counter>${initial + 1} / ${galleryItems.length}</span><button type="button" data-gallery-next aria-label="Next image">›</button></div>` : `<div class="media-gallery-controls media-gallery-controls-single"><span data-gallery-counter>1 / 1</span></div>`;
    const thumbs = galleryItems.map((item, index) => `<button type="button" class="media-gallery-thumb${index === initial ? ' active' : ''}" data-gallery-thumb data-index="${index}" data-gallery-src="${esc(item.src)}" data-gallery-alt="${esc(item.alt)}" data-gallery-caption="${esc(item.caption)}" aria-label="Show image ${index + 1}: ${esc(item.caption)}" aria-current="${index === initial ? 'true' : 'false'}"><img src="${esc(item.src)}" alt="" loading="lazy"><span>${esc(item.caption)}</span></button>`).join('');
    return `<div class="media-gallery media-gallery-${variant}" data-gallery data-gallery-current="${initial}" aria-label="${esc(label)}"><figure class="media-gallery-main"><div class="media-gallery-stage"><img src="${esc(current.src)}" alt="${esc(current.alt)}" data-gallery-main data-lightbox data-caption="${esc(current.caption)}" tabindex="0" role="button">${controls}</div><figcaption data-gallery-caption>${esc(current.caption)}</figcaption></figure><div class="media-gallery-thumbs" aria-label="${esc(label)} image selector">${thumbs}</div></div>`;
  }

  function initGalleryWidgets(scope = document) {
    qa('[data-gallery]', scope).forEach((gallery) => {
      if (gallery.dataset.galleryReady === 'true') return;
      gallery.dataset.galleryReady = 'true';
      const thumbs = qa('[data-gallery-thumb]', gallery);
      if (!thumbs.length) return;
      const main = q('[data-gallery-main]', gallery);
      const caption = q('[data-gallery-caption]', gallery);
      const counter = q('[data-gallery-counter]', gallery);

      const select = (requestedIndex, moveFocus = false) => {
        const total = thumbs.length;
        const index = (requestedIndex + total) % total;
        const thumb = thumbs[index];
        if (!thumb || !main) return;
        gallery.dataset.galleryCurrent = String(index);
        main.src = thumb.dataset.gallerySrc || main.src;
        main.alt = thumb.dataset.galleryAlt || '';
        main.dataset.caption = thumb.dataset.galleryCaption || main.alt;
        if (caption) caption.textContent = thumb.dataset.galleryCaption || main.alt;
        if (counter) counter.textContent = `${index + 1} / ${total}`;
        thumbs.forEach((button, buttonIndex) => {
          const active = buttonIndex === index;
          button.classList.toggle('active', active);
          button.setAttribute('aria-current', active ? 'true' : 'false');
        });
        if (moveFocus) thumb.focus();
      };

      thumbs.forEach((thumb, index) => thumb.addEventListener('click', () => select(index)));
      q('[data-gallery-prev]', gallery)?.addEventListener('click', () => select(Number(gallery.dataset.galleryCurrent || 0) - 1));
      q('[data-gallery-next]', gallery)?.addEventListener('click', () => select(Number(gallery.dataset.galleryCurrent || 0) + 1));
      gallery.addEventListener('keydown', (event) => {
        if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
        if (!(event.target.closest && event.target.closest('[data-gallery-thumb], [data-gallery-prev], [data-gallery-next]'))) return;
        event.preventDefault();
        const delta = event.key === 'ArrowLeft' ? -1 : 1;
        select(Number(gallery.dataset.galleryCurrent || 0) + delta, event.target.matches('[data-gallery-thumb]'));
      });
    });
  }

  // -------------------------------------------------------------------------
  // Renderers: home sections
  // -------------------------------------------------------------------------
  function setEnabled(root, key) {
    const enabled = sectionEnabled(key);
    root.hidden = !enabled;
    return enabled;
  }

  function renderHero(root) {
    if (!setEnabled(root, 'hero')) return;
    const c = get('hero', {});
    const useImage = c.background_image_enabled === true && c.background_image;
    root.classList.toggle('hero-with-image', !!useImage);
    if (useImage) {
      root.style.backgroundImage = `linear-gradient(rgba(5,8,16,${Number(c.overlay_strength || .7)}),rgba(5,8,16,${Math.min(.98, Number(c.overlay_strength || .7)+.08)})),url("${assetUrl(c.background_image)}")`;
      root.style.backgroundPosition = c.background_position || 'center';
    } else {
      root.style.removeProperty('background-image');
      root.style.removeProperty('background-position');
    }
    const logo = c.show_logo === true ? assetUrl(get('assets.logo_large', get('assets.logo', ''))) : '';
    root.innerHTML = `<div class="hero-inner">
      <div class="hero-copy"><div class="hero-eyebrow">${esc(c.eyebrow || '')}</div><h1>${esc(c.title || get('site.title', 'Conference'))}</h1><p class="hero-conference-name">${esc(c.subtitle || '')}</p>${c.description ? `<p class="hero-description">${esc(c.description)}</p>` : ''}${c.school_label ? `<div class="hero-school-label">${esc(c.school_label)}</div>` : ''}${c.meta ? `<div class="hero-meta">${esc(c.meta)}</div>` : ''}
      <div class="hero-actions">${c.primary_button_label ? `<a class="btn btn-primary" href="${esc(safeLink(registrationAwareTarget(c.primary_button_href || '#')))}">${esc(c.primary_button_label)}</a>` : ''}${c.secondary_button_label ? `<a class="btn btn-ghost" href="${esc(safeLink(registrationAwareTarget(c.secondary_button_href || '#')))}">${esc(c.secondary_button_label)}</a>` : ''}</div></div>
      ${logo ? `<img class="hero-logo" src="${esc(logo)}" alt="${esc(get('site.title', 'Conference'))}">` : ''}
    </div>`;
  }

  function renderHomeIntro(root) {
    if (!setEnabled(root, 'home_intro')) return;
    const c = get('home_intro', {});
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2>${arr(c.paragraphs).map((p) => `<p class="lead-copy">${esc(p)}</p>`).join('')}<div class="button-row">${arr(c.links).map((link) => { const href=safeLink(link.href); return `<a class="btn btn-outline btn-sm" href="${esc(href)}"${externalAttrs(href)}>${esc(link.label)}</a>`; }).join('')}</div>`;
  }

  function renderQuantumSchool(root) {
    if (!setEnabled(root, 'quantum_school')) return;
    const c = get('quantum_school', {});
    const image = c.image || get('assets.school_image', '');
    const caption = c.image_caption || c.title || 'Quantum Optics School';
    const galleryItems = arr(c.images).length ? arr(c.images) : (image ? [{src:image, alt:caption, caption}] : []);
    const schoolInline = (value) => esc(value || '').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    const introParagraphs = arr(c.paragraphs).length ? arr(c.paragraphs) : (c.intro ? [c.intro] : []);
    const topics = arr(c.topics).map((item) => `<article class="school-topic"><span></span><div><h3>${esc(item.title || '')}</h3><p>${schoolInline(item.text || '')}</p></div></article>`).join('');
    const closing = arr(c.closing_paragraphs).map((p) => `<p>${schoolInline(p)}</p>`).join('');
    const media = c.gallery_enabled === false
      ? (image ? `<figure class="school-visual"><img src="${esc(assetUrl(image))}" alt="${esc(caption)}" data-lightbox data-caption="${esc(caption)}" tabindex="0" role="button"><figcaption>${esc(caption)}</figcaption></figure>` : '')
      : galleryWidget(galleryItems, { label:c.gallery_label || c.title || 'School gallery', variant:'compact', initialIndex:c.gallery_initial, fallbackLabel:'School image' });
    root.innerHTML = `<div class="school-feature"><div class="school-copy"><div class="section-kicker">${esc(c.label || 'Quantum Optics School')}</div><h2>${esc(c.title || '')}</h2>${c.subtitle ? `<p class="school-subtitle">${esc(c.subtitle)}</p>` : ''}<div class="school-intro">${introParagraphs.map((p) => `<p>${schoolInline(p)}</p>`).join('')}</div><div class="school-topics">${topics}</div>${closing ? `<div class="school-closing">${closing}</div>` : ''}${c.notice ? `<div class="school-notice">${schoolInline(c.notice)}</div>` : ''}</div>${media}</div>${arr(c.format_items).length ? `<div class="school-format"><strong>${esc(c.format_title || 'School information')}</strong>${arr(c.format_items).map((item)=>`<span>${esc(item)}</span>`).join('')}</div>` : ''}`;
  }

  function renderAtGlance(root) {
    if (!setEnabled(root, 'at_glance')) return;
    const c = get('at_glance', {});
    const actions = arr(c.actions).map((action) => {
      const href = safeLink(registrationAwareTarget(action.href || '#'));
      const cls = action.style === 'primary' ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
      return `<a class="${cls}" href="${esc(href)}"${externalAttrs(href)}>${esc(displayValue(action.label || 'Open'))}</a>`;
    }).join('');
    root.innerHTML = `<div class="section-head-row"><div><div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2></div>${actions ? `<div class="section-head-actions">${actions}</div>` : ''}</div><div class="glance-grid">${arr(c.items).map((item) => `<div class="glance-item"><span>${esc(item.label)}</span><strong>${esc(item.value)}</strong></div>`).join('')}</div>`;
  }

  function renderStatistics(root) {
    if (!setEnabled(root, 'statistics')) return;
    const c = get('statistics', {});
    root.className = 'stats-grid';
    root.innerHTML = arr(c.items).map((item) => `<div class="stat"><strong>${esc(item.value)}</strong><span>${esc(item.label)}</span></div>`).join('');
  }

  // Countdown system --------------------------------------------------------
  // Any number of timers can be declared in conference.yaml. Each timer is
  // independently enabled and can be "large" or "compact". A timer marked
  // primary:true is the main conference countdown; compact timers are useful
  // for deadlines such as Early Bird registration.
  function countdownState(item, now = new Date()) {
    const start = new Date(item.date);
    const end = item.end_date ? new Date(item.end_date) : null;
    const urgentDays = Math.max(0, Number(item.urgent_within_days ?? get('countdown.urgent_within_days', 7)) || 0);
    const urgentMs = urgentDays * 86400000;
    if (Number.isNaN(start.getTime())) return { state: 'invalid', value: 'TBC', totalMs: 0, units: null, urgent: false };
    if (end && !Number.isNaN(end.getTime()) && now >= start && now <= end) {
      return { state: 'live', value: item.type === 'event' ? 'Conference in progress' : 'In progress', totalMs: 0, units: null, urgent: true };
    }
    const ms = start.getTime() - now.getTime();
    if (ms <= 0) return { state: 'past', value: item.type === 'event' ? 'Conference ended' : 'Deadline closed', totalMs: 0, units: null, urgent: false };
    const totalSeconds = Math.floor(ms / 1000);
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return { state: 'upcoming', value: `${days}d ${hours}h ${minutes}m ${seconds}s`, totalMs: ms, units: { days, hours, minutes, seconds }, urgent: urgentMs > 0 && ms <= urgentMs };
  }

  function formatCountdownDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return str(value);
    const options = { day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' };
    const zone = str(get('conference.timezone', '')).trim();
    if (zone && !/^(TBC|TBD)$/i.test(zone)) options.timeZone = zone;
    try { return new Intl.DateTimeFormat(document.documentElement.lang || 'en', options).format(date); }
    catch (_) { delete options.timeZone; return new Intl.DateTimeFormat(document.documentElement.lang || 'en', options).format(date); }
  }

  function countdownItems() {
    return arr(get('countdown.items', []))
      .filter((item) => item && item.enabled !== false && item.label && item.date)
      .sort((a,b) => new Date(a.date) - new Date(b.date));
  }

  function countdownPageVisible(item) {
    // show_on_page is canonical. show_on_home remains a compatibility fallback
    // for configuration files created before v1.5.
    if (item && Object.prototype.hasOwnProperty.call(item, 'show_on_page')) return item.show_on_page !== false;
    return !item || item.show_on_home !== false;
  }

  function primaryCountdownItem(items) {
    const declared = items.find((item) => item.primary === true && countdownPageVisible(item));
    if (declared) return declared;
    return items.find((item) => str(item.size).toLowerCase() === 'large' && countdownPageVisible(item)) || null;
  }

  function sidebarCountdownItems(items) {
    return items.filter((item) => item.show_in_sidebar !== false && ['live','upcoming'].includes(countdownState(item).state));
  }

  function setCountdownUnits(node, status) {
    if (!node) return;
    const units = status.units || { days:'—', hours:'—', minutes:'—', seconds:'—' };
    ['days','hours','minutes','seconds'].forEach((key) => {
      const target = q(`[data-countdown-${key}]`, node);
      if (target) target.textContent = String(units[key]).padStart(key === 'days' ? 1 : 2, '0');
    });
  }

  function updateCountdowns() {
    const items = countdownItems();
    qa('[data-countdown-item]').forEach((node) => {
      const id = node.dataset.countdownItem;
      const item = items.find((candidate) => str(candidate.id || candidate.label) === id);
      if (!item) return;
      const status = countdownState(item);
      node.dataset.state = status.state;
      node.dataset.urgent = status.urgent ? 'true' : 'false';
      setCountdownUnits(node, status);
      const statusNode = q('[data-countdown-status]', node);
      if (statusNode) statusNode.textContent = status.value;
    });

    const sidebar = q('#sidebarCountdown');
    const activeItems = sidebarCountdownItems(items);
    if (sidebar) {
      if (!activeItems.length) { sidebar.hidden = true; sidebar.innerHTML = ''; return; }
      sidebar.hidden = false;
      sidebar.innerHTML = `<div class="sidebar-countdown-head"><span>${esc(get('countdown.label', 'Countdowns'))}</span><strong>${activeItems.length} active</strong></div>${activeItems.map((item) => {
        const status = countdownState(item);
        return `<article class="sidebar-countdown-item" data-state="${esc(status.state)}" data-urgent="${status.urgent ? 'true' : 'false'}"><span>${esc(item.label)}</span><strong>${esc(status.value)}</strong><small>${esc(formatCountdownDate(item.date))}${item.provisional === true ? ' · TBC' : ''}</small></article>`;
      }).join('')}`;
    }
  }

  function countdownUnitsHtml() {
    return `<div class="countdown-units" aria-label="Time remaining"><div><strong data-countdown-days>0</strong><span>Days</span></div><div><strong data-countdown-hours>00</strong><span>Hours</span></div><div><strong data-countdown-minutes>00</strong><span>Minutes</span></div><div><strong data-countdown-seconds>00</strong><span>Seconds</span></div></div>`;
  }

  function renderCountdown(root) {
    const globalPageVisible = get('countdown.show_on_page', get('countdown.show_on_home', true)) !== false;
    if (!setEnabled(root, 'countdown') || !globalPageVisible) { root.hidden = true; return; }
    const c = get('countdown', {});
    const items = countdownItems().filter(countdownPageVisible);
    if (!items.length) { root.hidden = true; return; }
    root.hidden = false;
    const primary = primaryCountdownItem(items);
    const compact = items.filter((item) => item !== primary);
    const itemId = (item) => esc(str(item.id || item.label));
    root.innerHTML = `
      <div class="countdown-head">
        <div><div class="section-kicker">${esc(c.label || 'Countdown')}</div><h2>${esc(c.title || 'Countdown')}</h2></div>
        ${c.note ? `<p>${esc(c.note)}</p>` : ''}
      </div>
      ${primary ? `<article class="countdown-live" data-countdown-item="${itemId(primary)}">
        <div class="countdown-live-meta"><span>${esc(primary.label)}</span><time>${esc(formatCountdownDate(primary.date))}</time><strong data-countdown-status></strong></div>
        ${countdownUnitsHtml()}
      </article>` : ''}
      ${compact.length ? `<div class="countdown-compact-grid">${compact.map((item) => `<article class="countdown-compact" data-countdown-item="${itemId(item)}">
        <div class="countdown-compact-meta"><span>${esc(item.label)}</span><time>${esc(formatCountdownDate(item.date))}</time>${item.provisional === true ? '<small>TBC · provisional</small>' : ''}</div>
        ${countdownUnitsHtml()}<strong class="countdown-compact-status" data-countdown-status></strong>
      </article>`).join('')}</div>` : ''}`;
    updateCountdowns();
  }

  function renderImportantDates(root) {
    if (!setEnabled(root, 'important_dates')) return;
    const c = get('important_dates', {});
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2><div class="timeline">${arr(c.items).map((item) => `<div class="timeline-row"><time>${esc(item.date)}</time><span>${esc(item.description)}</span></div>`).join('')}</div>`;
  }

  function renderRegistration(root) {
    if (!setEnabled(root, 'registration')) return;
    const c = get('registration', {});
    const isHome = document.body.dataset.page === 'home';
    const compactHome = isHome && c.home_compact !== false;
    const formConfig = c.form || {};
    const registrationTarget = registrationEnabled()
      ? ((!isHome && str(formConfig.mode, 'static').toLowerCase() === 'php' && formConfig.action) ? formConfig.action : (c.registration_url || 'registration.html'))
      : registrationUnavailableUrl();
    const registrationUrl = safeLink(registrationTarget);
    const payment = c.payment || {};
    const paymentUrl = safeLink(payment.url || c.payment_url || '#');
    const earlyDeadline = displayValue(c.early_bird_deadline || '');
    const regularFrom = displayValue(c.regular_from || '');

    const plans = c.plans_enabled === false ? '' : arr(c.plans).map((plan) => {
      const earlyPrice = displayValue(plan.early_price || plan.price);
      const latePrice = displayValue(plan.late_price || '');
      const buttonLabel = displayValue(plan.button_label || c.plan_button_label || 'Registration');
      return `<article class="price-card">
        <div class="price-name">${esc(displayValue(plan.name))}</div>
        ${earlyDeadline ? `<div class="price-period">Early bird · until ${esc(earlyDeadline)}</div>` : ''}
        <div class="price">${esc(earlyPrice)}</div>
        ${latePrice ? `<div class="late"><span>${esc(regularFrom ? `From ${regularFrom}` : 'After early registration')}</span><strong>${esc(latePrice)}</strong></div>` : ''}
        <ul>${arr(plan.features).map((feature) => `<li>${esc(displayValue(feature))}</li>`).join('')}</ul>
        <a class="btn btn-primary" href="${esc(registrationUrl)}">${esc(buttonLabel)}</a>
      </article>`;
    }).join('');

    const warnings = c.warnings_enabled === false ? [] : arr(c.warnings);
    const importantNotes = arr(c.important_notes);
    const warningHtml = warnings.length ? `<article class="notice danger registration-warning-panel"><h3>${esc(displayValue(c.warnings_title || 'Disclaimer'))}</h3>${c.warnings_intro ? `<p>${esc(displayValue(c.warnings_intro))}</p>` : ''}<ul>${warnings.map((warning) => `<li>${esc(displayValue(warning))}</li>`).join('')}</ul></article>` : '';
    const importantHtml = importantNotes.length ? `<article class="notice accent registration-important-note"><h3>${esc(displayValue(c.important_note_title || 'Important note'))}</h3><ul>${importantNotes.map((note) => `<li>${esc(displayValue(note))}</li>`).join('')}</ul></article>` : (c.important_note ? `<div class="notice accent registration-important-note"><h3>${esc(displayValue(c.important_note_title || 'Important note'))}</h3><p>${esc(displayValue(c.important_note))}</p></div>` : '');
    const head = isHome ? `<div class="registration-head"><div><div class="section-kicker">${esc(displayValue(c.label))}</div><h2>${esc(displayValue(c.title))}</h2><p class="lead-copy">${esc(displayValue(c.intro))}</p></div>${earlyDeadline ? `<div class="early-bird-box"><span>${esc(displayValue(c.early_bird_label || 'Early bird deadline'))}</span><strong>${esc(earlyDeadline)}</strong>${regularFrom ? `<small>Regular fees from ${esc(regularFrom)}</small>` : ''}</div>` : ''}</div>` : '';

    if (compactHome) {
      const compactPlans = c.plans_enabled === false ? '' : arr(c.plans).map((plan) => {
        const earlyPrice = displayValue(plan.early_price || plan.price);
        const latePrice = displayValue(plan.late_price || '');
        return `<article class="price-card price-card-home">
          <div class="price-name">${esc(displayValue(plan.name))}</div>
          <div class="price">${esc(earlyPrice)}</div>
          ${latePrice ? `<div class="late"><span>${esc(regularFrom ? `From ${regularFrom}` : 'Regular fee')}</span><strong>${esc(latePrice)}</strong></div>` : ''}
          <a class="btn btn-primary" href="${esc(registrationUrl)}">${esc(displayValue(c.plan_button_label || 'Registration'))}</a>
        </article>`;
      }).join('');

      const participantPlans = arr(c.plans);
      const commonFeatures = participantPlans.length > 1
        ? arr(participantPlans[0].features).filter((feature) => participantPlans.slice(1, 2).every((plan) => arr(plan.features).includes(feature)))
        : arr(participantPlans[0]?.features);
      const accompanying = participantPlans.find((plan) => str(plan.id).toLowerCase() === 'accompanying');
      const accompanyingFeatures = arr(accompanying?.features);
      const detailItems = [
        commonFeatures.length ? `<div><strong>Participant & Student include</strong><span>${commonFeatures.map((item) => esc(displayValue(item))).join(' · ')}</span></div>` : '',
        accompanyingFeatures.length ? `<div><strong>Accompanying includes</strong><span>${accompanyingFeatures.map((item) => esc(displayValue(item))).join(' · ')}</span></div>` : '',
        c.tax_note ? `<div><strong>Fees</strong><span>${esc(displayValue(c.tax_note))}</span></div>` : '',
        c.refund_deadline ? `<div><strong>Refunding deadline</strong><span>${esc(displayValue(c.refund_deadline))}</span></div>` : ''
      ].filter(Boolean).join('');
      const conditions = [...warnings, ...importantNotes];
      const conditionsHtml = conditions.length ? `<details class="registration-home-details"><summary>Registration conditions & included services</summary><div class="registration-home-detail-grid">${detailItems}</div><ul>${conditions.map((item) => `<li>${esc(displayValue(item))}</li>`).join('')}</ul></details>` : (detailItems ? `<div class="registration-home-detail-grid">${detailItems}</div>` : '');

      root.innerHTML = `${head}<div class="pricing-grid pricing-grid-home">${compactPlans}</div><div class="registration-home-bar"><div>${c.tax_note ? `<strong>${esc(displayValue(c.tax_note))}</strong>` : ''}${c.refund_deadline ? `<span>Refunding deadline: <b>${esc(displayValue(c.refund_deadline))}</b></span>` : ''}</div><a class="btn btn-primary" href="${esc(registrationUrl)}">${esc(displayValue(c.registration_button_label || 'Open registration form'))}</a></div>${conditionsHtml}`;
      return;
    }

    const methods = arr(c.payment_methods).map((method) => `<article class="info-card registration-method"><h3>${esc(displayValue(method.title))}</h3>${arr(method.steps).length ? `<ol>${arr(method.steps).map((step) => `<li>${esc(displayValue(step))}</li>`).join('')}</ol>` : ''}${arr(method.fields).length ? `<dl class="data-list">${arr(method.fields).map((field) => `<div><dt>${esc(displayValue(field.label))}</dt><dd>${esc(displayValue(field.value))}</dd></div>`).join('')}</dl>` : ''}${method.note ? `<p class="note">${esc(displayValue(method.note))}</p>` : ''}</article>`).join('');
    const paymentHtml = payment.enabled === false ? '' : `<div class="registration-payment-head"><div class="section-kicker">${esc(displayValue(payment.label || 'Payment'))}</div><h3>${esc(displayValue(payment.title || 'Payment methods & instructions'))}</h3>${payment.intro ? `<p class="lead-copy">${esc(displayValue(payment.intro))}</p>` : ''}<div class="registration-actions"><a class="btn btn-primary" href="${esc(paymentUrl)}"${externalAttrs(paymentUrl)}>${esc(displayValue(payment.button_label || 'Open payment website'))}</a></div></div><div class="payment-grid">${methods}</div>`;
    const plansHtml = `<div class="pricing-grid" id="registration-plans">${plans}</div><div class="registration-foot">${c.tax_note ? `<strong>${esc(displayValue(c.tax_note))}</strong>` : ''}${c.refund_deadline ? `<span>Refunding deadline: <b>${esc(displayValue(c.refund_deadline))}</b></span>` : ''}</div>`;
    root.innerHTML = `${head}${plansHtml}${warningHtml}${importantHtml}${paymentHtml}${c.provider_note ? `<p class="fine-print">${esc(displayValue(c.provider_note))}</p>` : ''}${c.guide_title ? `<div class="guide"><h3>${esc(displayValue(c.guide_title))}</h3><ol>${arr(c.guide_steps).map((step) => `<li>${esc(displayValue(step))}</li>`).join('')}</ol>${payment.instructions_note ? `<p>${esc(displayValue(payment.instructions_note))}</p>` : ''}</div>` : ''}`;
  }

  function renderRegistrationUnavailable(root) {
    const c = get('registration.unavailable', {});
    const title = displayValue(c.title || 'Registration to be defined');
    const message = displayValue(c.message || 'Registration information for this conference has not been published yet.');
    const note = c.note ? `<p class="fine-print">${esc(displayValue(c.note))}</p>` : '';
    const buttonUrl = safeLink(c.button_url || 'index.html');
    const buttonLabel = displayValue(c.button_label || 'Back to conference');
    root.innerHTML = `<article class="notice accent registration-tbd-card"><div class="section-kicker">${esc(displayValue(c.label || 'Registration'))}</div><h2>${esc(title)}</h2><p class="lead-copy">${esc(message)}</p>${note}<div class="button-row"><a class="btn btn-primary" href="${esc(buttonUrl)}">${esc(buttonLabel)}</a></div></article>`;
  }

  // Registration form -------------------------------------------------------
  // The field/options configuration lives in conference.yaml. In `mode: php`
  // the static page opens the isolated PHP handler instead of posting directly,
  // so the PHP form can own its CSRF/session security.
  function registrationFieldHtml(field) {
    const name = str(field.name).trim();
    if (!name) return '';
    const id = `reg-${name.replace(/[^a-z0-9_-]+/gi, '-')}`;
    const type = str(field.type, 'text').toLowerCase();
    const required = field.required === true;
    const requiredAttr = required ? ' required' : '';
    const requiredMark = required ? '<span class="form-required" aria-hidden="true">*</span>' : '';
    const full = field.full_width === true ? ' form-field-wide' : '';
    const autocomplete = field.autocomplete ? ` autocomplete="${esc(field.autocomplete)}"` : '';
    const help = field.help ? `<small class="form-help">${esc(displayValue(field.help))}</small>` : '';
    let control = '';

    if (type === 'textarea') {
      control = `<textarea id="${esc(id)}" name="${esc(name)}" rows="3"${requiredAttr}${autocomplete}></textarea>`;
    } else if (type === 'select') {
      const options = arr(field.options).map((option) => {
        const value = typeof option === 'object' ? displayValue(option.value ?? option.label) : displayValue(option);
        const label = typeof option === 'object' ? displayValue(option.label ?? option.value) : displayValue(option);
        return `<option value="${esc(value)}">${esc(label)}</option>`;
      }).join('');
      control = `<select id="${esc(id)}" name="${esc(name)}"${requiredAttr}><option value="">Select…</option>${options}</select>`;
    } else if (type === 'file') {
      const accept = field.accept ? ` accept="${esc(field.accept)}"` : '';
      control = `<input id="${esc(id)}" name="${esc(name)}" type="file"${accept}${requiredAttr}>`;
    } else if (type === 'checkbox') {
      const href = configuredLink(field.link_href || '');
      const link = href ? ` <a href="${esc(href)}"${externalAttrs(href)}>${esc(displayValue(field.link_label || 'Read more'))}</a>` : '';
      return `<div class="form-field form-field-checkbox${full}"><label for="${esc(id)}"><input id="${esc(id)}" name="${esc(name)}" type="checkbox" value="yes"${requiredAttr}><span>${esc(displayValue(field.label))}${requiredMark}${link}</span></label>${help}</div>`;
    } else {
      const safeType = ['text','email','date','tel','number'].includes(type) ? type : 'text';
      control = `<input id="${esc(id)}" name="${esc(name)}" type="${safeType}"${requiredAttr}${autocomplete}>`;
    }

    return `<div class="form-field${full}"><label for="${esc(id)}">${esc(displayValue(field.label))}${requiredMark}</label>${control}${help}</div>`;
  }

  function renderRegistrationForm(root) {
    if (!setEnabled(root, 'registration')) return;
    const c = get('registration', {});
    root.hidden = false;

    // The public site deliberately knows nothing about regform/settings.yaml.
    // It only links to the isolated PHP module; opening/closing submissions and
    // every form/backend/mail option are handled server-side by regform itself.
    const action = configuredLink(c.registration_url || 'regform/');
    const buttonLabel = displayValue(c.registration_button_label || 'Open registration form');
    const label = displayValue(c.label || 'Registration');
    const title = displayValue(c.title || 'Registration');
    const intro = c.intro ? `<p class="lead-copy">${esc(displayValue(c.intro))}</p>` : '';
    const launch = action
      ? `<a class="btn btn-primary" href="${esc(action)}">${esc(buttonLabel)}</a>`
      : `<button class="btn btn-primary" type="button" disabled aria-disabled="true">Registration unavailable</button>`;

    root.innerHTML = `<div class="registration-form-head"><div><div class="section-kicker">${esc(label)}</div><h2>${esc(title)}</h2>${intro}</div></div><div class="registration-php-launch">${launch}<p class="fine-print">The secure registration module manages form availability, validation, email delivery and uploads separately from the public conference configuration.</p></div>`;
  }

  function renderHomeVenue(root) {
    if (!setEnabled(root, 'home_venue')) return;
    const c = get('home_venue', {});
    const map = configuredLink(c.map_url || '');
    const image = c.image || '';
    const galleryItems = arr(c.images).length ? arr(c.images) : (image ? [{src:image, alt:c.name || 'Venue', caption:c.name || 'Venue'}] : []);
    const mapHtml = c.map_enabled === false ? '' : mapMarkup(c.map_embed_url || get('venue.map_embed_url',''), c.map_url || get('venue.map_url',''), c.map_title || 'Venue map', 'map-container-home');
    const gallery = c.gallery_enabled === false ? '' : galleryWidget(galleryItems, { label:c.gallery_label || `${c.name || 'Venue'} gallery`, variant:'compact', initialIndex:c.gallery_initial, fallbackLabel:'Venue image' });
    const media = mapHtml || gallery || (image ? `<div class="venue-placeholder"><img src="${esc(assetUrl(image))}" alt="${esc(c.name || 'Venue')}" data-lightbox data-caption="${esc(c.name || 'Venue')}" tabindex="0" role="button"></div>` : '');
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2><div class="split-card home-venue-card"><div><h3>${esc(c.name || '')}</h3><p>${esc(c.description || '')}</p><div class="mini-stats">${arr(c.stats).map((s) => `<div><strong>${esc(s.value)}</strong><span>${esc(s.label)}</span></div>`).join('')}</div><div class="button-row">${c.gallery_link_enabled === false ? '' : `<a class="btn btn-outline btn-sm" href="${esc(safeLink(c.gallery_link_href || 'venue.html'))}">${esc(c.gallery_link_label || 'Venue details & gallery')}</a>`}</div></div>${media}</div><div class="address-card wide"><span>Address</span><strong>${esc(c.address || '')}</strong>${map ? `<a href="${esc(map)}"${externalAttrs(map)}>Open directions in Google Maps ↗</a>` : `<small>Directions: TBC</small>`}</div>`;
  }

  function renderHomeProgram(root) {
    if (!setEnabled(root, 'home_program')) return;
    const c = get('home_program', {});
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2><p class="lead-copy">${esc(c.description || '')}</p><div class="resource-grid">${arr(c.resources).map((item) => { const href=safeLink(item.target); return `<a class="resource-card" href="${esc(href)}"${externalAttrs(href)}><span class="badge">${esc(item.badge || '')}</span><h3>${esc(item.title)}</h3><p>${esc(item.description || '')}</p><strong>${esc(item.label || 'Open')}</strong></a>`; }).join('')}</div>`;
  }

  function renderHomeSpeakers(root) {
    if (!setEnabled(root, 'home_speakers')) return;
    const c = get('home_speakers', {});
    const invited = visiblePeople().filter((p) => hasRole(p, 'Invited Speaker')).slice(0, 4);
    const href = safeLink(c.button_href || 'speakers.html');
    const content = invited.length
      ? `<div class="speaker-grid home-speaker-grid">${invited.map((p)=>personCard(p,false)).join('')}</div>`
      : `<section class="people-tbc home-speaker-tbc" aria-label="Invited speakers to be confirmed"><div class="people-tbc-copy"><div class="section-kicker">Programme update</div><h3>Invited speakers to be confirmed</h3><p>The invited-speaker line-up is currently being finalised. Confirmed names and affiliations will appear here as soon as they are available.</p></div><div class="people-tbc-mark" aria-hidden="true">TBC</div></section>`;
    root.innerHTML = `<div class="section-kicker">${esc(c.label || 'Speakers')}</div><h2>${esc(c.title || 'Invited Speakers')}</h2>${c.intro?`<p class="lead-copy">${esc(c.intro)}</p>`:''}${content}<div class="button-row"><a class="btn btn-outline btn-sm" href="${esc(href)}">${esc(c.button_label || 'View invited speakers')}</a></div>`;
  }

  function renderHomeVenueAccommodation(root) {
    if (!setEnabled(root, 'home_venue_accommodation')) return;
    const c = get('home_venue_accommodation', {});
    const accommodation = get('accommodation', {});
    const warnings = arr(get('registration.warnings', [])).slice(0, 2);
    const venueHref = safeLink(c.venue_button_href || 'venue.html');
    const accommodationHref = safeLink(c.accommodation_button_href || 'accommodation.html');
    root.innerHTML = `<div class="section-kicker">${esc(c.label || 'Practical Information')}</div><h2>${esc(c.title || 'Venue & Accommodation')}</h2><p class="lead-copy">${esc(c.intro || '')}</p><div class="two-grid practical-summary"><article class="info-card"><h3>${esc(get('venue.subtitle','Conference venue'))}</h3><p>${esc(get('venue.description',''))}</p><a class="btn btn-outline btn-sm" href="${esc(venueHref)}">${esc(c.venue_button_label || 'Venue details')}</a></article><article class="notice danger"><h3>${esc(accommodation.alert_title || 'Hotel booking scam alert')}</h3><p>${esc(accommodation.alert_text || '')}</p></article></div>${warnings.length?`<div class="home-warning-row">${warnings.map((warning)=>`<div class="notice danger compact-warning"><strong>Important</strong><p>${esc(displayValue(warning))}</p></div>`).join('')}</div>`:''}<div class="button-row"><a class="btn btn-outline btn-sm" href="${esc(accommodationHref)}">${esc(c.accommodation_button_label || 'Accommodation information')}</a></div>`;
  }

  function renderHomeVisa(root) {
    if (!setEnabled(root, 'home_visa')) return;
    const c = get('home_visa', {});
    const href = safeLink(c.button_href || get('venue.visa_url','#'));
    root.innerHTML = `<div class="section-kicker">${esc(c.label || 'Travel Documents')}</div><h2>${esc(c.title || 'Visa regime for foreign citizens')}</h2><p class="lead-copy">${esc(c.intro || '')}</p><div class="button-row"><a class="btn btn-outline btn-sm" href="${esc(href)}"${externalAttrs(href)}>${esc(c.button_label || 'Official visa information')}</a></div>`;
  }

  function renderHomeSocial(root) {
    if (!setEnabled(root, 'home_social')) return;
    const c = get('home_social', {}); const social=get('social_program',{});
    const href = safeLink(c.button_href || 'social_program.html');
    const preview=c.preview_enabled===false?'':arr(social.items).filter((item)=>item.enabled!==false).slice(0,Number(c.preview_count)||3).map((item)=>`<article class="home-social-item"><span>${esc(item.kicker||'Social')}</span><strong>${esc(item.title||'')}</strong><small>${esc(item.date||item.time||'TBC')}</small></article>`).join('');
    const gallery = c.gallery_enabled === false ? '' : galleryWidget(arr(social.gallery), { label:c.gallery_label || 'Social program preview gallery', variant:'wide', initialIndex:c.gallery_initial ?? social.gallery_initial, fallbackLabel:'Social program image' });
    root.innerHTML = `<div class="section-kicker">${esc(c.label || 'Social Program')}</div><h2>${esc(c.title || 'Social Program')}</h2><p class="lead-copy">${esc(c.intro || '')}</p>${preview?`<div class="home-social-grid">${preview}</div>`:''}${gallery}<div class="button-row"><a class="btn btn-outline btn-sm" href="${esc(href)}">${esc(c.button_label || 'View Social Program')}</a></div>`;
  }

  function renderAbstractSubmission(root) {
    if (!setEnabled(root, 'abstract_submission')) return;
    const c = get('abstract_submission', {});
    const email = `mailto:${str(c.email)}`;
    const templateFile = c.template_enabled === false ? '' : assetUrl(c.template_file || '');
    const templateCard = templateFile ? `<article class="submission-template"><div><span class="resource-badge">${esc(displayValue(c.template_format || 'DOCX'))}</span><h3>${esc(displayValue(c.template_title || 'Abstract Template'))}</h3><p>${esc(displayValue(c.template_text || 'Use the official conference template for your abstract.'))}</p></div><a class="btn btn-primary btn-sm" href="${esc(templateFile)}" download>${esc(displayValue(c.template_label || 'Download Template'))}</a></article>` : '';
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><div class="submission-head"><div><h2>${esc(c.title || '')}</h2><p class="lead-copy">${esc(c.intro || '')} <a href="${esc(email)}">${esc(c.email || '')}</a></p></div><div class="deadline-box compact"><span>Abstract Submission Deadline</span><strong>${esc(c.deadline || '')}</strong></div></div>${templateCard}<div class="two-grid submission-formats"><article class="info-card"><h3>${esc(c.oral_title || '')}</h3><p>${esc(c.oral_text || '')}</p></article><article class="info-card"><h3>${esc(c.poster_title || '')}</h3><p>${esc(c.poster_text || '')}</p></article></div>`;
  }

  function renderSponsorPackages(root) {
    if (!setEnabled(root, 'sponsor_packages')) return;
    const c = get('sponsor_packages', {});
    const packages = arr(c.packages).map((pkg) => {
      const subject = encodeURIComponent(`${get('conference.acronym','Conference')} Sponsorship - ${pkg.name}`);
      const href = `mailto:${c.contact_email}?subject=${subject}`;
      return `<article class="sponsor-plan"><div class="sponsor-tier">${esc(pkg.name)}</div><div class="sponsor-price">${esc(pkg.price)}</div><ul>${arr(pkg.features).map((f)=>`<li>${esc(f)}</li>`).join('')}</ul><a class="btn btn-outline" href="${esc(href)}">Contact Us</a></article>`;
    }).join('');
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2><p class="lead-copy">${esc(c.intro || '')}</p><div class="sponsor-grid">${packages}</div><div class="custom-sponsor"><h3>${esc(c.custom_title || '')}</h3><p>${esc(c.custom_text || '')}</p></div>`;
  }

  function renderOrganizers(root) {
    if (!setEnabled(root, 'organizers')) return;
    const c = get('organizers', {});
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2>${c.intro ? `<p class="lead-copy">${esc(c.intro)}</p>` : ''}<div class="logo-grid">${arr(c.items).map((item) => { const href=safeLink(item.url); const logo=assetUrl(item.logo); return `<a class="logo-card" href="${esc(href)}"${externalAttrs(href)}><img src="${esc(logo)}" alt="${esc(item.name)}"><strong>${esc(item.name)}</strong><span>${esc(item.short_name || '')}</span></a>`; }).join('')}</div>`;
  }

  function renderCommittee(root) {
    if (!setEnabled(root, 'committee')) return;
    const c = get('committee', {});
    const people = visiblePeople();
    const featuredGroups = arr(c.featured_roles);
    const featuredRoles = new Set(featuredGroups.flatMap((group) => arr(group.roles).map((role) => str(role).trim().toLowerCase())).filter(Boolean));
    const featured = featuredGroups.map((group) => {
      const groupRoles=arr(group.roles);
      const members=people.filter((person)=>groupRoles.some((role)=>hasRole(person,role)));
      const cards=members.map((person)=>{const matched=groupRoles.find((role)=>hasRole(person,role))||groupRoles[0]||group.group;return personCard(person,true,committeeRoleLabel(person,matched));}).join('');
      return `<article class="committee-box"><h3>${esc(group.group)}</h3><div class="committee-box-members">${cards || '<p class="empty">No visible members.</p>'}</div></article>`;
    }).join('');
    // Legacy members_title/members_role are rendered only when they describe a
    // role that is not already present in featured_roles. This avoids the old
    // Program Committee / Program Committee Members duplicate list.
    const legacyRole = str(c.members_role || '').trim();
    const showLegacy = legacyRole && !featuredRoles.has(legacyRole.toLowerCase());
    const legacyMembers = showLegacy ? people.filter((p) => hasRole(p, legacyRole)) : [];
    const legacy = showLegacy ? `<div class="section-kicker sub-kicker">${esc(c.members_title || 'Committee Members')}</div><div class="committee-members">${legacyMembers.map((p) => personCard(p,true)).join('')}</div>` : '';
    root.innerHTML = `<div class="section-kicker">${esc(c.label || '')}</div><h2>${esc(c.title || '')}</h2><p class="lead-copy">${esc(c.intro || '')}</p><div class="committee-grid">${featured}</div>${legacy}`;
  }

  // -------------------------------------------------------------------------
  // Renderers: full pages
  // -------------------------------------------------------------------------
  function renderPeople(root) {
    if (!setEnabled(root, 'people')) return;
    const c = get('people', {});
    const people = visiblePeople();
    const groups = arr(c.groups).filter((group) => group.enabled !== false);
    const grouped = groups.map((group) => ({ group, members: people.filter((p) => hasRole(p, group.role)) }));
    const hasPublishedPeople = grouped.some((item) => item.members.length);
    const search = hasPublishedPeople && c.filter_enabled !== false ? `<input class="people-search" id="peopleSearch" type="search" placeholder="${esc(c.search_placeholder || 'Search people…')}">` : '';
    const tbc = `<section class="people-tbc" aria-label="Speakers to be confirmed"><div class="people-tbc-copy"><div class="section-kicker">Programme update</div><h2>Speakers to be confirmed</h2><p>The invited speakers and committee details are currently being finalised. Confirmed names and affiliations will be published here as soon as they are available.</p></div><div class="people-tbc-mark" aria-hidden="true">TBC</div></section>`;
    root.innerHTML = `<div class="page-head"><div class="section-kicker">${esc(c.page_label || 'People')}</div><h1>${esc(c.page_title || 'Conference People')}</h1>${search}</div><div id="peopleGroups">${hasPublishedPeople ? grouped.map(({group,members}) => {
      if (!members.length && c.show_empty_groups !== true) return '';
      return `<section class="section people-group"><div class="section-kicker">${esc(group.role)}</div><h2>${esc(group.title)}</h2><div class="${group.style === 'cards' ? 'speaker-grid' : 'people-list'}">${members.map((p) => personCard(p, group.style !== 'cards')).join('')}</div></section>`;
    }).join('') : tbc}</div>`;
    const input = q('#peopleSearch', root);
    if (input) input.addEventListener('input', () => {
      const term = input.value.trim().toLowerCase();
      qa('[data-search]', root).forEach((card) => card.hidden = !!term && !card.dataset.search.includes(term));
    });
  }

  function visibleProgramRows() { return state.program.filter((row) => truthy(row.Visible)); }
  function programEnd(row) { return row['End Time'] ? `–${row['End Time']}` : ''; }
  function programMeta(row) { return [row.Location, row.Notes].filter(Boolean).join(' · '); }

  // Program download has only two public modes:
  //   local     -> download a PDF supplied in assets/documents;
  //   generated -> create a PDF from the same program.csv used by the page.
  function programDownloadControl() {
    const c = get('program.download', {});
    if (c.enabled === false) return '';
    const mode = str(c.mode, 'generated').toLowerCase();
    if (mode === 'local') {
      const href = safeLink(c.local_file || '');
      return href && href !== '#'
        ? `<a class="btn btn-primary btn-sm" href="${esc(href)}" download="${esc(c.local_filename || 'conference-program.pdf')}">${esc(c.label || 'Download program PDF')}</a>`
        : '';
    }
    return `<button class="btn btn-primary btn-sm" id="pdfProgram" type="button">${esc(c.label || 'Download program PDF')}</button>`;
  }

  function programGroups() {
    const groups=[]; const index=new Map();
    visibleProgramRows().forEach((row)=>{
      const key=`${row.Day||''}|${row.Date||''}`;
      if(!index.has(key)){ const group={day:row.Day||'',date:row.Date||'',rows:[]}; index.set(key,group); groups.push(group); }
      index.get(key).rows.push(row);
    });
    return groups;
  }

  function escapeIcs(value) { return str(value).replace(/\\/g,'\\\\').replace(/\n/g,'\\n').replace(/,/g,'\\,').replace(/;/g,'\\;'); }
  function compactDate(value) {
    const raw=str(value).trim();
    const parsed=new Date(raw);
    if(!Number.isNaN(parsed.getTime())) return `${parsed.getFullYear()}${String(parsed.getMonth()+1).padStart(2,'0')}${String(parsed.getDate()).padStart(2,'0')}`;
    return raw.replace(/\D/g,'').slice(0,8);
  }
  function compactTime(value) { const m=str(value).match(/^(\d{1,2}):(\d{2})$/); return m ? `${m[1].padStart(2,'0')}${m[2]}00` : ''; }

  function downloadCalendar() {
    const c=get('program',{}); const location=get('conference.venue','');
    const lines=['BEGIN:VCALENDAR','VERSION:2.0',`PRODID:-//${escapeIcs(get('conference.acronym','Conference'))}//Program//EN`,'CALSCALE:GREGORIAN','METHOD:PUBLISH'];
    visibleProgramRows().filter((row)=>!row['Parent ID']).forEach((row)=>{
      const date=compactDate(row.Date);
      const startTime=compactTime(row['Start Time']);
      if(date.length!==8 || !startTime) return;
      const endTime=compactTime(row['End Time']) || startTime;
      lines.push('BEGIN:VEVENT',`DTSTART:${date}T${startTime}`,`DTEND:${date}T${endTime}`,`SUMMARY:${escapeIcs(`${get('conference.acronym','Conference')} — ${row.Title||row.Type||'Program'}`)}`,`LOCATION:${escapeIcs(row.Location||location)}`,'END:VEVENT');
    });
    lines.push('END:VCALENDAR');
    const blob=new Blob([lines.join('\r\n')+'\r\n'],{type:'text/calendar;charset=utf-8'}); const url=URL.createObjectURL(blob); const a=document.createElement('a');
    a.href=url; a.download=str(c.calendar_filename,'conference-program.ics').replace(/[^a-zA-Z0-9._-]+/g,'-'); document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    log('info','program','Calendar downloaded',{events:visibleProgramRows().filter((row)=>!row['Parent ID']).length});
  }

  async function downloadProgramPdf() {
    const c = get('program', {});
    if (!window.ProgramPdf || typeof window.ProgramPdf.download !== 'function') {
      log('error', 'program', 'Local PDF generator is not available');
      return;
    }
    const download = get('program.download', {});
    const pdfConfig = c.pdf || {};
    try {
      const result = await window.ProgramPdf.download({
        rows: visibleProgramRows(),
        program: c,
        conference: get('conference', {}),
        organization: get('organization', {}),
        colors: pdfConfig.colors || {},
        logos: {
          organizer: assetUrl((pdfConfig.logos && pdfConfig.logos.organizer) || get('assets.organizer_logo', '')),
          conference: assetUrl((pdfConfig.logos && pdfConfig.logos.conference) || get('assets.logo', ''))
        },
        filename: download.generated_filename || pdfConfig.filename || 'conference-program.pdf'
      });
      log('info', 'program', 'Program PDF generated', result);
    } catch (error) {
      log('error', 'program', 'Could not generate program PDF', { error: error.message });
    }
  }

  function renderProgram(root) {
    if (!setEnabled(root, 'program')) return;
    const c=get('program',{}); const rows=visibleProgramRows(); const groups=programGroups();
    const resources=c.resources_enabled===false?[]:arr(c.resources);
    let html=`<div class="page-head program-head"><div><div class="section-kicker">${esc(c.label||'')}</div><h1>${esc(c.title||'')}</h1><p>${esc(c.intro||'')}</p></div><div class="program-actions">${c.calendar_enabled!==false?`<button class="btn btn-outline btn-sm" id="calendarProgram" type="button">${esc(c.calendar_label||'Add to calendar')}</button>`:''}${programDownloadControl()}</div></div>`;
    if(groups.length>1 && c.tabs_enabled!==false) html+=`<div class="program-tabs" role="tablist">${groups.map((g,i)=>`<button class="program-tab${i===0?' active':''}" type="button" data-program-tab="${i}" role="tab" aria-selected="${i===0?'true':'false'}">${esc([g.day,g.date].filter(Boolean).join(', '))}</button>`).join('')}</div>`;
    if(resources.length) html+=`<div class="resource-grid compact">${resources.map((r)=>{const href=safeLink(r.file);return `<a class="resource-card" href="${esc(href)}"${externalAttrs(href)}><span class="badge">PDF</span><h3>${esc(r.label)}</h3><strong>Download</strong></a>`;}).join('')}</div>`;
    if(!rows.length) html+=`<div class="notice error">${esc(c.empty_message||'No program entries found.')}</div>`;
    groups.forEach((day,dayIndex)=>{
      const byId=new Map(day.rows.filter((r)=>r.ID).map((r)=>[r.ID,r]));
      html+=`<section class="section program-day" data-program-day="${dayIndex}"${dayIndex&&c.tabs_enabled!==false?' hidden':''}><div class="program-day-head"><span>${esc(day.day)}</span><h2>${esc(day.date)}</h2></div><div class="program-list">`;
      day.rows.forEach((row)=>{
        if(row['Parent ID']&&byId.has(row['Parent ID'])) return;
        const children=row.ID?day.rows.filter((child)=>child['Parent ID']===row.ID):[]; const meta=programMeta(row);
        html+=`<article class="program-row type-${esc(str(row.Type).toLowerCase())}"><time>${esc((row['Start Time']||'')+programEnd(row))}</time><div><span class="badge">${esc(row.Type||'')}</span><h3>${esc(row.Title||'')}</h3>${row.Speaker?`<p class="speaker-line"><strong>${esc(row.Speaker)}</strong>${row.Affiliation?` · ${esc(row.Affiliation)}`:''}</p>`:''}${row.Chair?`<p class="chair-line">${esc(c.chair_label||'Chair')}: ${esc(row.Chair)}</p>`:''}${meta?`<p class="row-meta">${esc(meta)}</p>`:''}${children.length?`<div class="talk-list">${children.map((talk)=>`<div class="talk"><time>${esc((talk['Start Time']||'')+programEnd(talk))}</time><div><strong>${esc(talk.Title||'')}</strong>${talk.Speaker?`<span>${esc(talk.Speaker)}${talk.Affiliation?` · ${esc(talk.Affiliation)}`:''}</span>`:''}${programMeta(talk)?`<small>${esc(programMeta(talk))}</small>`:''}</div></div>`).join('')}</div>`:''}</div></article>`;
      });
      html+='</div></section>';
    });
    root.innerHTML=html;
    q('#calendarProgram',root)?.addEventListener('click',downloadCalendar);
    q('#pdfProgram',root)?.addEventListener('click', downloadProgramPdf);
    qa('[data-program-tab]',root).forEach((button)=>button.addEventListener('click',()=>{
      const index=button.dataset.programTab; qa('[data-program-tab]',root).forEach((b)=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));}); qa('[data-program-day]',root).forEach((day)=>day.hidden=day.dataset.programDay!==index);
    }));
  }

  // OpenStreetMap is the only embedded map provider. Google Maps is never
  // embedded: it is opened only after an explicit user click on the overlay.
  function mapMarkup(embedValue, googleValue, title, extraClass = '') {
    const embed = safeFrame(embedValue);
    if (!embed) return '';
    const google = safeLink(googleValue || '');
    const overlay = google && google !== '#'
      ? `<a class="map-click-overlay" href="${esc(google)}"${externalAttrs(google)} aria-label="Open ${esc(title || 'map')} in Google Maps"><span>Open in Google Maps ↗</span></a>`
      : '';
    return `<div class="map-container ${esc(extraClass)}"><iframe src="${esc(embed)}" loading="lazy" referrerpolicy="no-referrer" title="${esc(title || 'OpenStreetMap')}"></iframe>${overlay}</div>`;
  }

  function renderVenue(root) {
    if (!setEnabled(root, 'venue')) return;
    const c=get('venue',{}); const map=configuredLink(c.map_url); const cityMap=configuredLink(c.city_map_url||'');
    const venueDetails=arr(c.details).map((paragraph)=>`<p class="lead-copy venue-detail-copy">${esc(paragraph)}</p>`).join('');
    const cityParagraphs=arr(c.city_paragraphs).map((paragraph)=>`<p class="lead-copy venue-city-copy">${esc(paragraph)}</p>`).join('');
    const venueMap=c.maps_enabled!==false ? mapMarkup(c.map_embed_url, c.map_url, c.map_title || 'Venue Map') : '';
    const cityMapHtml=c.city_map_enabled!==false ? mapMarkup(c.city_map_embed_url, c.city_map_url, c.city_map_title || 'City Map', 'map-container-city') : '';
    const features=c.features_enabled===false?'':`<div class="venue-facts">${arr(c.features).map((item)=>`<div><span>${esc(displayValue(item.label))}</span><strong>${esc(displayValue(item.value))}</strong></div>`).join('')}</div>`;
    const galleryItems=arr(c.images);
    const gallery=c.gallery_enabled===false || !galleryItems.length ? '' : `<section class="section venue-gallery-section"><div class="section-kicker">Gallery</div><h2>${esc(c.gallery_title||'Venue Gallery')}</h2>${c.gallery_intro?`<p class="lead-copy">${esc(c.gallery_intro)}</p>`:''}${galleryWidget(galleryItems,{label:c.gallery_title||'Venue Gallery',variant:'wide',initialIndex:c.gallery_initial,fallbackLabel:'Venue image'})}</section>`;
    root.innerHTML=`<div class="page-head"><div class="section-kicker">${esc(c.label||'')}</div><h1>${esc(c.title||'')}</h1></div><section class="section venue-intro-grid"><div><h2>${esc(c.subtitle||'Conference Venue')}</h2><p class="lead-copy">${esc(c.description||'')}</p>${venueDetails}${features}<div class="address-card wide"><span>Address</span><strong>${esc(c.address||'')}</strong>${map?`<a href="${esc(map)}"${externalAttrs(map)}>Open directions in Google Maps ↗</a>`:`<small>Directions: TBC</small>`}</div></div>${venueMap?`<div><div class="section-kicker">Map</div><h2>${esc(c.map_title||'Venue Map')}</h2>${venueMap}</div>`:''}</section><section class="section venue-notices"><div class="two-grid"><article class="notice"><h3>${esc(c.disclaimer_title||'')}</h3><p>${esc(c.disclaimer||'')}</p></article><article class="notice accent"><h3>${esc(c.important_title||'')}</h3><p>${esc(c.important_note||'')}</p></article></div></section>${gallery}<section class="section"><h2>${esc(c.city_title||'')}</h2><p class="lead-copy">${esc(c.city_text||'')}</p>${cityParagraphs}${cityMapHtml?`<div class="map-block"><h3>${esc(c.city_map_title||'City Map')}</h3>${cityMapHtml}</div>`:''}</section>`;
  }

  function renderAccommodation(root) {
    if (!setEnabled(root, 'accommodation')) return;
    const c=get('accommodation',{});
    const bookingUrl=configuredLink(c.booking_url || '');
    const visaLinks=arr(c.visa_links).map((item)=>{
      const href=configuredLink(item.url || '');
      return href ? `<a class="btn btn-outline btn-sm" href="${esc(href)}"${externalAttrs(href)}>${esc(item.label || 'Official visa information')}</a>` : '';
    }).join('');
    const options=arr(c.options);
    const accommodationGallery = c.gallery_enabled === false ? '' : galleryWidget(arr(c.images), { label:c.gallery_label || c.booking_title || 'Accommodation gallery', variant:'wide', initialIndex:c.gallery_initial, fallbackLabel:'Accommodation image' });
    const bookingSection = c.booking_enabled === false ? '' : `<section class="section"><div class="section-kicker">Accommodation</div><h2>${esc(c.booking_title || 'Booking Information')}</h2><ul class="plain-list">${arr(c.booking_items).map((item)=>`<li>${esc(item)}</li>`).join('')}</ul>${bookingUrl?`<div class="button-row"><a class="btn btn-primary" href="${esc(bookingUrl)}"${externalAttrs(bookingUrl)}>${esc(c.booking_link_label || 'Official booking')}</a></div>`:''}${accommodationGallery}</section>`;
    const visaSection = c.visa_enabled === false ? '' : `<section class="section"><div class="section-kicker">Travel documents</div><h2>${esc(c.visa_title || 'Visa information')}</h2>${c.visa_text?`<p class="lead-copy">${esc(c.visa_text)}</p>`:''}${visaLinks?`<div class="button-row">${visaLinks}</div>`:`<p>TBC</p>`}</section>`;
    root.innerHTML = `<div class="page-head"><div class="section-kicker">${esc(c.label || '')}</div><h1>${esc(c.title || '')}</h1>${c.intro?`<p>${esc(c.intro)}</p>`:''}</div>
      ${c.alert_enabled === false ? '' : `<div class="notice danger accommodation-scam"><h3>${esc(c.alert_title || '')}</h3><p>${esc(c.alert_text || '')}</p></div>`}
      ${bookingSection}
      ${visaSection}
      ${options.length ? `<div class="two-grid">${options.map((item)=>`<article class="info-card"><h3>${esc(item.name)}</h3><p>${esc(item.description||'')}</p></article>`).join('')}</div>`:''}`;
  }

  function renderSocialProgram(root) {
    if (!setEnabled(root, 'social_program')) return;
    const c=get('social_program',{});
    const items=c.items_enabled===false?[]:arr(c.items).filter((item)=>item.enabled!==false);
    const events=items.map((item) => { const image=item.image?assetUrl(item.image):''; return `<article class="social-event">${image?`<img src="${esc(image)}" alt="${esc(item.title||'Social event')}" loading="lazy" data-lightbox data-caption="${esc(item.title||'Social event')}" tabindex="0" role="button">`:''}<div class="social-event-body"><div class="social-event-top"><span class="section-kicker">${esc(item.kicker||'Social Program')}</span>${item.date?`<time>${esc(item.date)}</time>`:''}</div><h2>${esc(item.title||'')}</h2><div class="social-event-meta">${item.time?`<span><b>Time</b>${esc(item.time)}</span>`:''}${item.location?`<span><b>Location</b>${esc(item.location)}</span>`:''}</div><p>${esc(item.description||'')}</p>${item.included?`<p class="social-included">${esc(item.included)}</p>`:''}</div></article>`; }).join('');
    const galleryItems=arr(c.gallery);
    const gallery=c.gallery_enabled===false||!galleryItems.length?'':`<section class="section social-gallery-section"><div class="section-kicker">Gallery</div><h2>${esc(c.gallery_title||'Social Program Gallery')}</h2>${c.gallery_intro?`<p class="lead-copy">${esc(c.gallery_intro)}</p>`:''}${galleryWidget(galleryItems,{label:c.gallery_title||'Social Program Gallery',variant:'wide',initialIndex:c.gallery_initial,fallbackLabel:'Social program image'})}</section>`;
    root.innerHTML=`<div class="page-head"><div class="section-kicker">${esc(c.label||'')}</div><h1>${esc(c.title||'')}</h1><p>${esc(c.intro||'')}</p></div>${events?`<div class="social-events">${events}</div>`:''}${gallery}`;
  }

  function renderTravel(root) {
    if (!setEnabled(root, 'travel')) return;
    const c=get('travel',{});
    root.innerHTML = `<div class="page-head"><div class="section-kicker">${esc(c.label || '')}</div><h1>${esc(c.title || '')}</h1>${c.intro?`<p>${esc(c.intro)}</p>`:''}</div><div class="travel-grid">${arr(c.sections).map((item)=>{const href=item.url?safeLink(item.url):''; return `<article class="travel-card">${item.icon?`<div class="travel-icon">${esc(item.icon)}</div>`:''}<h2>${esc(item.title)}</h2><p>${esc(item.text||'')}</p>${href?`<a class="btn btn-outline btn-sm" href="${esc(href)}"${externalAttrs(href)}>${esc(item.button_label||'More information')}</a>`:''}</article>`;}).join('')}</div>`;
  }

  function renderPrivacy(root) {
    if (!setEnabled(root, 'privacy')) return;
    const c=get('privacy',{});
    const sections=arr(c.sections).map((section)=>{
      const paragraphs=arr(section.paragraphs).map((item)=>`<p>${esc(displayValue(item))}</p>`).join('');
      return `<article class="privacy-section-card"><h2>${esc(displayValue(section.title))}</h2>${paragraphs}</article>`;
    }).join('');
    root.innerHTML = `<div class="page-head privacy-page-head"><div class="section-kicker">${esc(displayValue(c.page_label || 'Legal & privacy'))}</div><h1>${esc(displayValue(c.page_title || 'Privacy & Cookies'))}</h1><p>${esc(displayValue(c.intro || ''))}</p></div><div class="privacy-sections">${sections}</div>`;
  }

  // -------------------------------------------------------------------------
  // Local image lightbox
  // -------------------------------------------------------------------------
  // Content images can opt in with data-lightbox. No external library is used.
  function initImageLightbox() {
    let modal = q('#imageLightbox');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'imageLightbox';
      modal.className = 'image-lightbox';
      modal.hidden = true;
      modal.innerHTML = `<button class="image-lightbox-backdrop" type="button" data-lightbox-close aria-label="Close image"></button><div class="image-lightbox-dialog" role="dialog" aria-modal="true" aria-label="Image preview"><button class="image-lightbox-close" type="button" data-lightbox-close aria-label="Close image">×</button><img alt=""><p></p></div>`;
      document.body.appendChild(modal);
    }
    const image = q('img', modal);
    const caption = q('p', modal);
    const close = () => { modal.hidden = true; document.body.classList.remove('lightbox-open'); image.removeAttribute('src'); caption.textContent=''; };
    const open = (target) => {
      if (!target || !target.src) return;
      image.src = target.currentSrc || target.src;
      image.alt = target.alt || '';
      caption.textContent = target.dataset.caption || target.alt || '';
      modal.hidden = false;
      document.body.classList.add('lightbox-open');
      q('.image-lightbox-close', modal)?.focus();
    };
    qa('[data-lightbox-close]', modal).forEach((button)=>button.addEventListener('click', close));
    document.addEventListener('click', (event) => { const target = event.target.closest && event.target.closest('img[data-lightbox]'); if (target) open(target); });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !modal.hidden) close();
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches && event.target.matches('img[data-lightbox]')) { event.preventDefault(); open(event.target); }
    });
  }

  // Debug-only visual reference. It intentionally uses the same CSS classes as
  // production pages, so it acts as a living ground truth for the design system.
  function renderUiKit(root) {
    if (get('runtime.debug', false) !== true || get('runtime.ui_kit_enabled', true) === false) {
      root.innerHTML='<div class="page-head"><div class="section-kicker">Debug</div><h1>UI Kit unavailable</h1><p>Enable <code>runtime.debug: true</code> in conference.yaml.</p></div>';
      return;
    }
    const samplePerson=visiblePeople()[0];
    const venueGallery=arr(get('venue.images',[]));
    const accommodationGallery=arr(get('accommodation.images',[]));
    const homeVenueGallery=arr(get('home.venue.images',[]));
    const sampleGallery=venueGallery.length?venueGallery:(accommodationGallery.length?accommodationGallery:homeVenueGallery);
    const gallery=sampleGallery.length?galleryWidget(sampleGallery.slice(0,5),{label:'UI Kit media gallery',variant:'wide'}):'<article class="info-card"><h3>Media gallery</h3><p>Add images to Venue or Accommodation to preview the production gallery component here.</p></article>';
    const tbcState=`<section class="people-tbc" aria-label="Speakers to be confirmed"><div class="people-tbc-copy"><div class="section-kicker">Programme update</div><h2>Speakers to be confirmed</h2><p>The invited speakers and committee details are currently being finalised. Confirmed names and affiliations will be published here as soon as they are available.</p></div><div class="people-tbc-mark" aria-hidden="true">TBC</div></section>`;
    root.innerHTML=`<div class="page-head"><div class="section-kicker">Debug reference</div><h1>Conference UI Kit</h1><p>Living catalogue of the production tokens, states and components used by the conference pages. Change theme or palette in Debug to audit every component together.</p></div>
      <section class="section ui-kit-section"><div class="section-kicker">Foundation</div><h2>Typography & colours</h2><div class="ui-swatch-grid">${['bg','bg-alt','card','primary','secondary','text','muted','border'].map((name)=>`<div class="ui-swatch" data-token="${esc(name)}"><i></i><span>--${esc(name)}</span></div>`).join('')}</div><div class="ui-type"><h1>Heading 1</h1><h2>Heading 2</h2><h3>Heading 3</h3><p>Body text uses the same typography and contrast as every conference page.</p><p class="fine-print">Fine print / secondary information.</p></div></section>
      <section class="section ui-kit-section"><div class="section-kicker">Actions</div><h2>Buttons, links, badges & controls</h2><div class="button-row"><button class="btn btn-primary">Primary</button><a class="btn btn-primary" href="#ui-kit-actions">Primary link</a><button class="btn btn-outline">Outline</button><button class="btn btn-ghost">Ghost</button><button class="btn btn-primary" disabled>Disabled</button><span class="badge">Badge</span></div><div class="ui-control-grid"><label>Text input<input class="people-search" value="Example value"></label><label>Select<select><option>Option one</option><option>Option two</option></select></label></div></section>
      <section class="section ui-kit-section"><div class="section-kicker">Surfaces</div><h2>Cards, notices & status</h2><div class="three-grid"><article class="info-card"><h3>Information card</h3><p>Neutral reusable information surface.</p></article><article class="notice accent"><h3>Accent notice</h3><p>Important but non-destructive information.</p></article><article class="notice danger"><h3>Warning</h3><p>Warnings keep readable foreground contrast in every palette.</p></article></div></section>
      <section class="section ui-kit-section"><div class="section-kicker">Empty state</div><h2>TBC / not yet published</h2><p class="lead-copy">Use an intentional editorial state instead of an empty grid or a bare “No data” message.</p>${tbcState}</section>
      <section class="section ui-kit-section"><div class="section-kicker">Media</div><h2>Selectable gallery</h2><p class="lead-copy">The same image gallery widget is used for Venue, Accommodation, School and social content.</p>${gallery}</section>
      <section class="section ui-kit-section"><div class="section-kicker">Data</div><h2>People & programme</h2>${samplePerson?`<div class="ui-person-preview">${personCard(samplePerson,false)}</div>`:'<article class="info-card"><h3>Person card</h3><p>Load People data to preview the production speaker card here.</p></article>'}<article class="program-row"><time>09:00–10:30</time><div><span class="badge">Session</span><h3>Scientific session · TBC</h3><p class="speaker-line"><strong>First Name Last Name</strong> · Affiliation</p><div class="talk-list"><div class="talk"><time>09:00</time><div><strong>Talk title · TBC</strong><span>First Name Last Name · Affiliation</span></div></div></div></div></article></section>
      <section class="section ui-kit-section"><div class="section-kicker">Registration</div><h2>Pricing, deadlines & form controls</h2><div class="registration-head"><div><p class="lead-copy">Registration cards and form controls use the same theme tokens as the public site and regform.</p></div><div class="early-bird-box"><span>Early bird deadline</span><strong>TBC</strong><small>Regular fees: TBC</small></div></div><div class="pricing-grid"><article class="price-card"><div class="price-name">Participant</div><div class="price-period">Early bird · TBC</div><div class="price">TBC</div><div class="late"><span>Regular fee</span><strong>TBC</strong></div><ul><li>Included services: TBC</li></ul><a class="btn btn-primary" href="#ui-kit-registration">Choose this plan</a></article></div><div class="registration-form-grid"><label class="form-field"><span>Full name</span><input type="text" value="First Name Last Name"></label><label class="form-field"><span>Attendance type</span><select><option>Participant</option><option>Student</option></select></label><label class="form-field form-field-wide"><span>Notes</span><textarea rows="3">Example multiline field</textarea></label></div></section>
      <section class="section ui-kit-section"><div class="section-kicker">Layout</div><h2>Responsive grids</h2><div class="three-grid"><div class="card"><h3>Column A</h3><p>Uses common surface tokens.</p></div><div class="card"><h3>Column B</h3><p>Collapses consistently on small screens.</p></div><div class="card"><h3>Column C</h3><p>No page-specific theme fork.</p></div></div></section>`;
  }

  const RENDERERS = {
    'hero': renderHero,
    'home-intro': renderHomeIntro,
    'quantum-school': renderQuantumSchool,
    'at-glance': renderAtGlance,
    'statistics': renderStatistics,
    'countdown': renderCountdown,
    'important-dates': renderImportantDates,
    'registration': renderRegistration,
    'registration-unavailable': renderRegistrationUnavailable,
    'registration-form': renderRegistrationForm,
    'home-venue': renderHomeVenue,
    'home-program': renderHomeProgram,
    'abstract-submission': renderAbstractSubmission,
    'sponsor-packages': renderSponsorPackages,
    'organizers': renderOrganizers,
    'committee': renderCommittee,
    'home-speakers': renderHomeSpeakers,
    'home-venue-accommodation': renderHomeVenueAccommodation,
    'home-visa': renderHomeVisa,
    'home-social': renderHomeSocial,
    'people': renderPeople,
    'program': renderProgram,
    'venue': renderVenue,
    'accommodation': renderAccommodation,
    'social-program': renderSocialProgram,
    'travel': renderTravel,
    'privacy': renderPrivacy,
    'ui-kit': renderUiKit
  };

  function renderAll() {
    qa('[data-render]').forEach((root) => {
      const name = root.dataset.render;
      const renderer = RENDERERS[name];
      if (!renderer) { log('warn', 'render', 'Unknown renderer', { name }); return; }
      try { renderer(root); log('debug', 'render', 'Rendered section', { name }); }
      catch (error) { log('error', 'render', `Failed renderer ${name}`, { error: error.message, stack: error.stack || '', renderer: name, page: document.body.dataset.page || '' }); root.innerHTML = `<div class="notice error">Could not render ${esc(name)}.</div>`; }
    });
  }

  // -------------------------------------------------------------------------
  // Footer and privacy notice
  // -------------------------------------------------------------------------
  function renderFooter() {
    const footer=q('#siteFooter'); const c=get('footer',{}); if (!footer || c.enabled === false) { if(footer) footer.hidden=true; return; }
    const org=get('organization',{}); const logo=assetUrl(org.logo || get('assets.organizer_logo',''));
    footer.innerHTML = `<div class="footer-inner"><div class="footer-brand">${logo?`<img src="${esc(logo)}" alt="${esc(org.short_name || 'MIFP')}">`:''}<div><strong>${esc(org.name || '')}</strong>${c.show_description!==false?`<p>${esc(org.description||'')}</p>`:''}</div></div>${c.show_contact!==false?`<div class="footer-block"><h3>Organization</h3><span>${esc(org.short_name || '')} — ${esc(org.name || '')}</span><span>${esc(org.address || '')}</span><span>Phone: ${esc(org.phone || '')}</span><span>Email: <a href="mailto:${esc(org.email || '')}">${esc(org.email || '')}</a></span><span>Web: <a href="${esc(safeLink(org.website || '#'))}"${externalAttrs(safeLink(org.website || '#'))}>${esc(org.website_label || org.website || '')}</a></span></div>`:''}${c.show_links!==false?`<div class="footer-block"><h3>Links</h3>${visibleNavigation().slice(1,4).map((item)=>`<a href="${esc(safeLink(item.file))}">${esc(item.label)}</a>`).join('')}</div>`:''}</div><div class="footer-bottom"><span>${esc(get('site.footer','MIFP · Matteo Ginesi 2026'))}</span><a href="${esc(safeLink('privacy.html'))}">Privacy & Cookies</a></div>`;
  }

  function renderPrivacyBanner() {
    const banner=q('#privacyBanner'); const c=get('privacy',{}); if(!banner) return;
    if(c.enabled===false || c.show_banner===false) { banner.hidden=true; return; }
    const key=str(c.storage_key,'mifp-conference-privacy');
    if(localStorage.getItem(key)==='dismissed') { banner.hidden=true; return; }
    banner.innerHTML=`<span>${esc(c.banner_text || '')}</span><button type="button" id="privacyOk">OK</button>`;
    q('#privacyOk',banner)?.addEventListener('click',()=>{localStorage.setItem(key,'dismissed');banner.hidden=true;});
  }

  // First-version Back to top control, now YAML-configurable.
  function initBackToTop() {
    const button=q('#backToTop'); const c=get('back_to_top',{});
    if(!button || c.enabled===false){ if(button) button.hidden=true; return; }
    button.setAttribute('aria-label',str(c.label,'Back to top'));
    const update=()=>button.classList.toggle('visible',window.scrollY > (Number(c.show_after_px)||320));
    window.addEventListener('scroll',update,{passive:true}); button.addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'})); update();
  }

  // -------------------------------------------------------------------------
  // Debug totem: only exists when runtime.debug=true.
  // -------------------------------------------------------------------------
  function programDiagnostics() {
    const ids = new Map(); const duplicates=[]; const orphans=[];
    visibleProgramRows().forEach((row)=>{ if(row.ID){ if(ids.has(row.ID)) duplicates.push(row.ID); ids.set(row.ID,row); } });
    visibleProgramRows().forEach((row)=>{ if(row['Parent ID'] && !ids.has(row['Parent ID'])) orphans.push({id:row.ID,parent:row['Parent ID']}); });
    return { duplicates, orphans };
  }

  function peopleDiagnostics() {
    const expected=['First Name','Last Name','Category','Role','Affiliation','Country','Image','Visible'];
    return { expected, actual:state.peopleHeaders, missing:expected.filter((h)=>!state.peopleHeaders.includes(h)) };
  }

  function debugText(value) {
    if (value == null || value === '') return '—';
    if (Array.isArray(value)) return `${value.length} items`;
    if (typeof value === 'object') return `${Object.keys(value).length} fields`;
    return str(value);
  }

  function debugContext(data) {
    if (!data || typeof data !== 'object') return '';
    return Object.entries(data).slice(0, 6).map(([key,value]) => `<span><b>${esc(key)}</b>${esc(debugText(value))}</span>`).join('');
  }

  function debugHtml() {
    const pd=programDiagnostics(); const pe=peopleDiagnostics();
    const themes=arr(get('appearance.themes',[])); const palettes=arr(get('appearance.palettes',[]));
    const sectionNames=['hero','home_intro','at_glance','statistics','countdown','important_dates','registration','home_venue','home_program','abstract_submission','sponsor_packages','organizers','committee','home_speakers','home_venue_accommodation','home_visa','home_social','people','program','venue','accommodation','social_program','travel','privacy'];
    const enabledCount=sectionNames.filter(sectionEnabled).length;
    const warnings=pe.missing.length + pd.duplicates.length + pd.orphans.length + state.logs.filter((item)=>item.level==='error').length;
    const currentTheme=document.documentElement.dataset.theme||'';
    const currentPalette=document.documentElement.dataset.palette||'';
    const pathRows=Object.entries(state.paths).map(([name,value])=>`<div class="debug-path-row"><span>${esc(name)}</span><code title="${esc(value)}">${esc(value)}</code><b class="debug-ok">loaded</b></div>`).join('');
    const sectionRows=sectionNames.map((name)=>`<span class="debug-section-pill ${sectionEnabled(name)?'on':'off'}"><i></i>${esc(name.replaceAll('_',' '))}</span>`).join('');
    const peopleHeaderRows=pe.expected.map((name)=>`<span class="debug-header-pill ${pe.missing.includes(name)?'bad':'good'}">${esc(name)}</span>`).join('');
    const programHeaderRows=state.programHeaders.slice(0,18).map((name)=>`<span class="debug-header-pill good">${esc(name)}</span>`).join('');
    const recentLogs=state.logs.slice(-60).reverse().map((entry)=>`<div class="debug-log-row level-${esc(entry.level)}"><div class="debug-log-meta"><time>${esc(entry.time)}</time><b>${esc(entry.level.toUpperCase())}</b><span>${esc(entry.scope)}</span></div><p>${esc(entry.message)}</p>${entry.data?`<div class="debug-log-context">${debugContext(entry.data)}</div>`:''}</div>`).join('');
    const themeCards=themes.map((theme)=>`<button class="debug-theme-card${currentTheme===theme.id?' selected':''}" type="button" data-debug-theme="${esc(theme.id)}"><span class="debug-theme-preview"><i data-debug-color="${esc(theme.bg)}"></i><i data-debug-color="${esc(theme.bg_card)}"></i><i data-debug-color="${esc(theme.text_heading)}"></i></span><strong>${esc(theme.label||theme.id)}</strong><small>${esc(theme.color_scheme||'theme')}</small></button>`).join('');
    const paletteCards=palettes.map((palette)=>`<button class="debug-palette-card${currentPalette===palette.id?' selected':''}" type="button" data-debug-palette="${esc(palette.id)}"><span class="debug-palette-preview"><i data-debug-color="${esc(palette.primary)}"></i><i data-debug-color="${esc(palette.secondary)}"></i></span><span><strong>${esc(palette.label||palette.id)}</strong><small>${esc(palette.primary)} · ${esc(palette.secondary)}</small></span></button>`).join('');
    return `<button class="debug-backdrop" data-debug-close type="button" aria-label="Close debug"></button><aside class="debug-totem" role="dialog" aria-modal="true" aria-label="Conference debug"><header><div><span>Conference diagnostics</span><strong>MIFP v${VERSION}</strong></div><button type="button" data-debug-close>×</button></header><div class="debug-body">
      <div class="debug-health ${warnings?'warning':'healthy'}"><div><span>${warnings?'Check recommended':'Runtime healthy'}</span><strong>${warnings?`${warnings} diagnostic item${warnings===1?'':'s'}`:'No structural errors detected'}</strong></div><b>${warnings?'!':'✓'}</b></div>
      <div class="debug-grid"><div><span>Page</span><strong>${esc(document.body.dataset.page || '')}</strong></div><div><span>Viewport</span><strong>${window.innerWidth}×${window.innerHeight}</strong></div><div><span>People</span><strong>${state.people.length}</strong></div><div><span>Program rows</span><strong>${state.program.length}</strong></div><div><span>Sections on</span><strong>${enabledCount}/${sectionNames.length}</strong></div><div><span>Renderers</span><strong>${qa('[data-render]').length}</strong></div><div><span>Theme</span><strong data-debug-current-theme>${esc(currentTheme)}</strong></div><div><span>Palette</span><strong data-debug-current-palette>${esc(currentPalette)}</strong></div></div>
      <section class="debug-section"><div class="debug-section-title"><div><span>Appearance lab</span><h3>Theme</h3></div><small>Debug only</small></div><div class="debug-theme-grid">${themeCards}</div><div class="debug-subtitle">Palette</div><div class="debug-palette-grid">${paletteCards}</div><div class="debug-live-token"><span>Current accent</span><i class="token-primary"></i><i class="token-secondary"></i><code>${esc(currentPalette)}</code></div></section>
      <section class="debug-section"><div class="debug-section-title"><div><span>Content</span><h3>Section switches</h3></div><small>${enabledCount} enabled</small></div><div class="debug-section-pills">${sectionRows}</div></section>
      <section class="debug-section"><div class="debug-section-title"><div><span>Data sources</span><h3>Resolved runtime paths</h3></div><small>root-relative</small></div><div class="debug-path-list">${pathRows}</div></section>
      <section class="debug-section"><div class="debug-section-title"><div><span>CSV health</span><h3>People</h3></div><small>${pe.missing.length?'missing headers':'headers OK'}</small></div><div class="debug-header-pills">${peopleHeaderRows}</div><div class="debug-validation-line"><span>Rows</span><b>${state.people.length}</b><span>Missing required headers</span><b class="${pe.missing.length?'bad-text':'good-text'}">${pe.missing.length}</b></div></section>
      <section class="debug-section"><div class="debug-section-title"><div><span>CSV health</span><h3>Program</h3></div><small>${pd.duplicates.length||pd.orphans.length?'review':'structure OK'}</small></div><div class="debug-header-pills">${programHeaderRows}</div><div class="debug-validation-line"><span>Rows</span><b>${state.program.length}</b><span>Duplicate IDs</span><b class="${pd.duplicates.length?'bad-text':'good-text'}">${pd.duplicates.length}</b><span>Orphans</span><b class="${pd.orphans.length?'bad-text':'good-text'}">${pd.orphans.length}</b></div></section>
      <section class="debug-section"><div class="debug-section-title"><div><span>Privacy & runtime</span><h3>Feature status</h3></div><small>static frontend</small></div><div class="debug-feature-grid"><div><span>External assets</span><b>${get('security.allow_external_assets',false)===true?'allowed':'blocked'}</b></div><div><span>Maps</span><b>OpenStreetMap</b></div><div><span>Google Maps</span><b>click only</b></div><div><span>Program PDF</span><b>${window.ProgramPdf?'ready':'unavailable'}</b></div><div><span>UI Kit</span><b>${get('runtime.ui_kit_enabled',true)!==false?'enabled':'disabled'}</b></div><div><span>Debug storage</span><b>${get('appearance.remember_preferences',true)!==false?'local':'off'}</b></div></div></section>
      <section class="debug-section"><div class="debug-section-title"><div><span>Console</span><h3>Recent runtime log</h3></div><small>${state.logs.length} buffered</small></div><div class="debug-log-list">${recentLogs || '<p class="fine-print">No logs yet.</p>'}</div></section>
      <div class="debug-actions">${get('runtime.ui_kit_enabled',true)!==false?`<a class="btn btn-primary btn-sm" href="${esc(localUrl('ui-kit.html'))}" target="_blank" rel="noopener">Open UI Kit</a>`:''}<button class="btn btn-outline btn-sm" id="debugRefresh" type="button">Refresh panel</button><button class="btn btn-outline btn-sm" id="debugReload" type="button">Reload page</button><button class="btn btn-outline btn-sm" id="debugDownloadLogs" type="button">Download logs</button><button class="btn btn-outline btn-sm" id="debugResetTheme" type="button">Reset appearance</button></div>
    </div></aside>`;
  }

  function paintDebugSwatches(mount) {
    qa('[data-debug-color]',mount).forEach((node)=>{
      const value=node.dataset.debugColor;
      if (/^#[0-9a-f]{6}$/i.test(value||'')) node.style.background=value;
    });
  }

  function refreshDebugAppearance(mount) {
    qa('[data-debug-theme]',mount).forEach((node)=>node.classList.toggle('selected',node.dataset.debugTheme===document.documentElement.dataset.theme));
    qa('[data-debug-palette]',mount).forEach((node)=>node.classList.toggle('selected',node.dataset.debugPalette===document.documentElement.dataset.palette));
    qa('[data-debug-current-theme]',mount).forEach((node)=>{node.textContent=document.documentElement.dataset.theme||'';});
    qa('[data-debug-current-palette]',mount).forEach((node)=>{node.textContent=document.documentElement.dataset.palette||'';});
    q('.debug-live-token code',mount) && (q('.debug-live-token code',mount).textContent=document.documentElement.dataset.palette||'');
  }

  function openDebug() {
    if(get('runtime.debug',false)!==true) return;
    const mount=q('#debugMount'); if(!mount) return;
    mount.innerHTML=debugHtml(); mount.classList.add('open'); state.debugOpen=true; document.body.classList.add('debug-open');
    qa('[data-debug-close]',mount).forEach((b)=>b.addEventListener('click',closeDebug));
    paintDebugSwatches(mount);
    qa('[data-debug-theme]',mount).forEach((button)=>button.addEventListener('click',()=>{ applyTheme(button.dataset.debugTheme,true); refreshDebugAppearance(mount); }));
    qa('[data-debug-palette]',mount).forEach((button)=>button.addEventListener('click',()=>{ applyPalette(button.dataset.debugPalette,true); refreshDebugAppearance(mount); }));
    q('#debugRefresh',mount)?.addEventListener('click',()=>{ closeDebug(); openDebug(); });
    q('#debugReload',mount)?.addEventListener('click',()=>location.reload());
    q('#debugResetTheme',mount)?.addEventListener('click',()=>{localStorage.removeItem('mifp-debug-theme');localStorage.removeItem('mifp-debug-palette');applyTheme(get('appearance.default_theme','midnight'));applyPalette(get('appearance.default_palette','emerald'));closeDebug();openDebug();});
    q('#debugDownloadLogs',mount)?.addEventListener('click',downloadLogs);
    log('info','debug','Debug totem opened');
  }

  function closeDebug() {
    const mount=q('#debugMount'); if(!mount||!state.debugOpen) return;
    mount.classList.remove('open'); mount.innerHTML=''; state.debugOpen=false; document.body.classList.remove('debug-open');
  }

  function downloadLogs() {
    const text=state.logs.map((entry)=>JSON.stringify(entry)).join('\n')+'\n';
    const blob=new Blob([text],{type:'application/x-ndjson'}); const url=URL.createObjectURL(blob); const a=document.createElement('a');
    a.href=url;a.download=`mifp-debug-${new Date().toISOString().replace(/[:.]/g,'-')}.jsonl`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  function initDebug() {
    if(get('runtime.debug',false)!==true) return;
    qa('[data-debug-open]').forEach((button)=>button.addEventListener('click',openDebug));
    window.MIFP_DEBUG=Object.freeze({ version:VERSION, open:openDebug, close:closeDebug, state:()=>({version:VERSION,paths:{...state.paths},people:state.people.length,program:state.program.length,theme:document.documentElement.dataset.theme,palette:document.documentElement.dataset.palette}), logs:()=>state.logs.slice(), config:()=>state.config });
  }

  // -------------------------------------------------------------------------
  // Boot / fatal error
  // -------------------------------------------------------------------------
  function finishBoot() {
    document.body.removeAttribute('data-booting');
    q('#bootScreen')?.remove();
    document.documentElement.dataset.runtime = VERSION;
    log('info','boot',`MIFP v${VERSION} ready`, { page:document.body.dataset.page, paths:state.paths, people:state.people.length, program:state.program.length });
  }

  function fatal(error) {
    console.error(`[MIFP][FATAL] v${VERSION}`, error);
    document.body.removeAttribute('data-booting');
    const screen=q('#bootScreen');
    if(screen) screen.innerHTML=`<div class="fatal"><strong>Conference could not start</strong><p>${esc(error.message || error)}</p><small>Expected config: ${esc(CONFIG_URL.href)}</small></div>`;
    document.body.dataset.bootFailed='true';
  }

  async function init() {
    try {
      await loadData();
      const page = document.body.dataset.page || 'home';
      if (page === 'registration' && !registrationEnabled()) {
        window.location.replace(safeLink(registrationUnavailableUrl()));
        return;
      }
      if (page === 'registration-tbd' && registrationEnabled()) {
        window.location.replace(safeLink(registrationActiveUrl()));
        return;
      }
      initAppearance();
      initSeo();
      renderShell();
      renderAll();
      initGalleryWidgets();
      initImageLightbox();
      renderFooter();
      renderPrivacyBanner();
      initBackToTop();
      initDebug();
      updateCountdowns();
      if (get('countdown.enabled', false) === true) window.setInterval(updateCountdowns, Math.max(1, Number(get('countdown.update_interval_seconds', 1)) || 1) * 1000);
      finishBoot();
    } catch (error) {
      fatal(error);
    }
  }

  init();
})();

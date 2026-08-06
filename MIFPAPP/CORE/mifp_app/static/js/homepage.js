(() => {
"use strict";

/* Navigation */
(function () {
  'use strict';

  var nav = document.getElementById('mainNav');
  var institutionToggle = document.getElementById('instToggle');
  var institutionMenu = document.getElementById('instMenu');
  var institutionWrap = document.getElementById('instWrap');
  var mobileToggle = document.getElementById('navToggle');
  var mobileMenu = document.getElementById('mobileMenu');
  var lastMobileFocus = null;

  function setExpanded(button, expanded) {
    if (button) button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  }

  function closeInstitutionMenu(restoreFocus) {
    if (!institutionMenu || !institutionToggle) return;
    institutionMenu.classList.remove('open');
    setExpanded(institutionToggle, false);
    if (restoreFocus) institutionToggle.focus();
  }

  function closeMobileMenu(restoreFocus) {
    if (!mobileMenu || !mobileToggle) return;
    mobileMenu.classList.remove('open');
    document.body.classList.remove('nav-menu-open');
    setExpanded(mobileToggle, false);
    if (restoreFocus) (lastMobileFocus || mobileToggle).focus();
  }

  if (nav) {
    var updateNav = function () { nav.classList.toggle('scrolled', window.scrollY > 30); };
    updateNav();
    window.addEventListener('scroll', updateNav, { passive: true });
  }

  if (institutionToggle && institutionMenu) {
    institutionToggle.addEventListener('click', function () {
      var opening = !institutionMenu.classList.contains('open');
      institutionMenu.classList.toggle('open', opening);
      setExpanded(institutionToggle, opening);
      if (opening) institutionMenu.querySelector('a')?.focus();
    });
  }

  if (mobileToggle && mobileMenu) {
    mobileToggle.addEventListener('click', function () {
      var opening = !mobileMenu.classList.contains('open');
      lastMobileFocus = document.activeElement;
      mobileMenu.classList.toggle('open', opening);
      document.body.classList.toggle('nav-menu-open', opening);
      setExpanded(mobileToggle, opening);
      if (opening) mobileMenu.querySelector('a')?.focus();
    });
    mobileMenu.addEventListener('click', function (event) {
      if (event.target.closest('a')) closeMobileMenu(false);
    });
  }

  document.addEventListener('click', function (event) {
    if (institutionWrap && !institutionWrap.contains(event.target)) closeInstitutionMenu(false);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (institutionMenu?.classList.contains('open')) closeInstitutionMenu(true);
    if (mobileMenu?.classList.contains('open')) closeMobileMenu(true);
  });

})();

/* Accessible media dialogs */
(function() {
  'use strict';

  var overlay = null;
  var overlayImg = null;
  var overlayCaption = null;
  var overlayContent = null;
  var closeBtn = null;
  var previousFocus = null;

  function ensureOverlay() {
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'mifp-lightbox';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-hidden', 'true');
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay || e.target === closeBtn) close();
      });

      closeBtn = document.createElement('button');
      closeBtn.className = 'mifp-lightbox-close';
      closeBtn.type = 'button';
      closeBtn.setAttribute('aria-label', 'Close modal');
      closeBtn.innerHTML = '&times;';
      overlay.appendChild(closeBtn);

      var panel = document.createElement('div');
      panel.className = 'mifp-lightbox-panel';
      overlay.appendChild(panel);

      overlayImg = document.createElement('img');
      overlayImg.className = 'mifp-lightbox-img';
      panel.appendChild(overlayImg);

      overlayCaption = document.createElement('div');
      overlayCaption.className = 'mifp-lightbox-caption';
      panel.appendChild(overlayCaption);

      overlayContent = document.createElement('div');
      overlayContent.className = 'mifp-lightbox-content';
      panel.appendChild(overlayContent);

      document.body.appendChild(overlay);
    }
  }

  function setSiblingsInert(state) {
    var children = document.body.children;
    for (var i = 0; i < children.length; i++) {
      if (children[i] === overlay) continue;
      if (state) children[i].setAttribute('inert', '');
      else children[i].removeAttribute('inert');
    }
  }

  function focusTrapKeydown(e) {
    if (!overlay || !overlay.classList.contains('is-open')) return;
    if (e.key === 'Escape') {
      close();
      return;
    }
    if (e.key !== 'Tab') return;
    var focusable = overlay.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])');
    if (!focusable.length) {
      e.preventDefault();
      closeBtn.focus();
      return;
    }
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === overlay)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function open(src, alt, caption) {
    ensureOverlay();
    previousFocus = document.activeElement;
    overlay.classList.remove('is-content');
    overlayContent.replaceChildren();
    overlayImg.alt = alt || '';
    overlayImg.src = src;
    overlayCaption.textContent = caption || alt || '';
    overlay.classList.add('is-open');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    setSiblingsInert(true);
    closeBtn.focus();
  }

  function openContent(source) {
    ensureOverlay();
    previousFocus = document.activeElement;
    overlay.classList.add('is-content');
    overlayImg.removeAttribute('src');
    overlayCaption.textContent = '';
    var content = source.cloneNode(true);
    content.removeAttribute('hidden');
    content.removeAttribute('id');
    overlayContent.replaceChildren(content);
    var heading = content.querySelector('h1, h2, h3');
    overlay.setAttribute('aria-label', heading ? heading.textContent.trim() : 'Sponsor profile');
    overlay.classList.add('is-open');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    setSiblingsInert(true);
    closeBtn.focus();
  }

  function close() {
    if (overlay) {
      overlay.classList.remove('is-open');
      overlay.classList.remove('is-content');
      overlay.setAttribute('aria-hidden', 'true');
      document.body.style.overflow = '';
      setSiblingsInert(false);
      if (previousFocus && document.contains(previousFocus)) previousFocus.focus();
      previousFocus = null;
    }
  }

  document.addEventListener('keydown', focusTrapKeydown);

  document.addEventListener('click', function(e) {
    var target = e.target.closest('.js-lightbox');
    if (!target) return;
    e.preventDefault();
    var src = target.getAttribute('data-lightbox-src') || target.getAttribute('src');
    var img = target.tagName === 'IMG' ? target : target.querySelector('img');
    var alt = target.getAttribute('alt') || (img ? img.getAttribute('alt') : '');
    var caption = target.getAttribute('data-lightbox-caption') || alt;
    if (src) open(src, alt, caption);
  });

  document.addEventListener('click', function(e) {
    var target = e.target.closest('.js-sponsor-modal');
    if (!target) return;
    var id = target.getAttribute('data-sponsor-target');
    var source = id ? document.getElementById(id) : null;
    if (source) {
      e.preventDefault();
      openContent(source);
    }
  });
})();

/* Visual publication filtering */
document.addEventListener('DOMContentLoaded', function () {
  var search = document.getElementById('pubSearch');
  if (!search) return;
  var list = document.getElementById('pubList');
  var empty = document.getElementById('pubEmpty');
  if (!list) return;

  search.addEventListener('input', function () {
    var q = this.value.toLowerCase().trim();
    var cards = list.querySelectorAll('.pub-card');
    var visible = 0;
    var sections = list.querySelectorAll('.year-section');
    sections.forEach(function (s) { s.style.display = ''; s.style.opacity = '1'; });

    cards.forEach(function (c) {
      var text = c.getAttribute('data-search') || '';
      if (!q || text.indexOf(q) !== -1) {
        c.style.display = '';
        visible++;
      } else {
        c.style.display = 'none';
      }
    });

    var yearSections = list.querySelectorAll('.year-section');
    yearSections.forEach(function (ys) {
      var next = ys.nextElementSibling;
      var hasVisible = false;
      while (next && !next.classList.contains('year-section')) {
        if (next.classList.contains('pub-card') && next.style.display !== 'none') {
          hasVisible = true;
          break;
        }
        next = next.nextElementSibling;
      }
      ys.style.display = hasVisible ? '' : 'none';
      if (hasVisible) ys.style.opacity = '1';
    });

    if (empty) {
      empty.classList.toggle('is-hidden', visible > 0);
    }

    var status = document.getElementById('pubSearchStatus');
    if (status) {
      status.textContent = q
        ? visible + ' of ' + cards.length + ' publication' + (cards.length === 1 ? '' : 's') + ' match your search.'
        : '';
    }
  });
});

/* Server-supplied research charts */
(function () {
  var data = {};
  try { data = JSON.parse(document.getElementById('researchData')?.textContent || '{}'); } catch (_) { data = {}; }
  var PUB_YEARS = data.pubByYear || [];
  var MEMBERS_CTRY = data.membersByCountry || [];

  var fontCfg = { family: "'Inter', -apple-system, sans-serif" };
  var colorMifpRed = '#b42318';
  var colorMifpBlue = '#175cd3';
  var colorGrid = 'rgba(255,255,255,0.06)';
  var colorText = 'rgba(209,213,219,0.8)';

  function initChart(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    new Chart(el, cfg);
  }

  if (PUB_YEARS.length) {
    initChart('pubByYearChart', {
      type: 'bar',
      data: {
        labels: PUB_YEARS.map(function (d) { return d.year; }),
        datasets: [{
          label: 'Publications',
          data: PUB_YEARS.map(function (d) { return d.total; }),
          backgroundColor: 'rgba(180,35,24,0.6)',
          borderColor: colorMifpRed,
          borderWidth: 1.5,
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: colorText, font: fontCfg },
            grid: { color: colorGrid },
          },
          y: {
            ticks: { color: colorText, font: fontCfg, stepSize: 1 },
            grid: { color: colorGrid },
          }
        }
      }
    });
  }

  if (MEMBERS_CTRY.length) {
    initChart('membersByCountryChart', {
      type: 'bar',
      data: {
        labels: MEMBERS_CTRY.map(function (d) { return d.country; }),
        datasets: [{
          label: 'Members',
          data: MEMBERS_CTRY.map(function (d) { return d.total; }),
          backgroundColor: 'rgba(23,92,211,0.6)',
          borderColor: colorMifpBlue,
          borderWidth: 1.5,
          borderRadius: 4,
        }]
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            ticks: { color: colorText, font: fontCfg, stepSize: 1 },
            grid: { color: colorGrid },
          },
          y: {
            ticks: { color: colorText, font: fontCfg },
            grid: { display: false },
          }
        }
      }
    });
  }
})();

/* Cookie notice dismiss — page-local by design (no tracking storage). */
(function() {
  var banner = document.getElementById('cookie-banner');
  if (!banner) return;
  if (banner.getAttribute('data-force-show') !== '0') {
    banner.style.removeProperty('display');
    banner.hidden = false;
  }
  var btn = document.getElementById('cookie-banner-close');
  if (!btn) return;
  btn.addEventListener('click', function() {
    if (banner.classList.contains('is-dismissing')) return;
    banner.classList.add('is-dismissing');
    window.setTimeout(function() {
      banner.hidden = true;
      banner.style.display = 'none';
    }, 230);
  });
})();

})();

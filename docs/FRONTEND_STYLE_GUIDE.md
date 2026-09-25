# MIFP frontend style guide

Practical rules for changing the MIFP frontend without eroding its two visual
systems. Read this before adding CSS, a dashboard component or a browser log
call. The design rationale lives in
[`MIFPAPP/CORE/docs/THEME_SYSTEMS.md`](../MIFPAPP/CORE/docs/THEME_SYSTEMS.md).

---

## 1. Two visual identities, deliberately different

| | Public website | Dashboard |
| --- | --- | --- |
| Theme name | **Scientific atlas** | **Control instrument** |
| Sheet | `static/css/homepage.css` | `static/css/dashboard.css` |
| Loaded by | `public/base.html`, `auth/login.html`, `errors/error.html` | `base.html` (plus the scoped `archive.css` on the three archive views, and `work-in-progress.css` on the WIP page) |
| Feel | institutional, editorial, spacious, content-led | operational, compact, information-dense, instrument-like |
| Shell | dark field with a paper-like reading surface | dark shell (`--shell-*`) around a light workspace (`--content-bg`) |
| Gradients | allowed, and only 3 of them, only for the scientific field | **never** (a test asserts `linear-gradient(`/`radial-gradient(` are absent) |
| Type | `--f-display` / `--f-body` / `--f-data` / `--f-serif` | `--font-family-base` + exactly one use of Georgia via `--font-family-editorial` |

They share only brand fundamentals: the MIFP identity, brand red, the
accessibility contracts (`:focus-visible`), and naming conventions.

**Never** make a public card look like a dashboard panel or vice versa. A public
card is an editorial object; a dashboard panel is an instrument surface.

`homepage.css` and `dashboard.css` are never loaded together, so a class name
reused in both is not a conflict — but do not assume one file's rules apply in
the other. Two further sheets are narrowly scoped and are neither of the two
identities: `archive.css` (the public historical-archive/search views) and
`work-in-progress.css` (the standalone holding page).

---

## 2. Tokens

Each sheet owns its tokens in `:root`. `homepage.css` must keep exactly **two**
`:root` blocks (the second is the ≤1024px `--nav-h` override); add new public
tokens to the first. Do not rename tokens wholesale; the names below are the
vocabulary.

**Public** — surfaces `--ink-950…--ink-500`, `--surface-glow`; text `--paper`,
`--paper-mute`, `--paper-dim`, `--paper-faint`, `--paper-ghost`; lines `--line`,
`--line-soft`; brand `--mifp-red`, `--mifp-red-hover`, `--mifp-red-deep`,
`--mifp-blue`, `--mifp-blue-soft`; radii `--r-xs --r-sm --r --r-lg --r-xl`;
layout `--container`, `--container-wide`, `--nav-h`; type `--f-*`; focus
`--focus-ring` (`#86a8eb`), `--focus-halo`; motion `--t`, `--t-slow`. There are
**no semantic state tokens** on the public side: public pages do not carry
success/danger state.

**Dashboard** — workspace `--content-bg` (`#eef1f4`), `--surface`, `--surface-2`,
`--surface-3`; shell `--shell-bg`, `--shell-bg-elevated`, `--shell-hover`,
`--shell-border`, `--shell-text`, `--shell-muted`, `--shell-topbar-height`,
`--shell-sidebar-width`, `--shell-sidebar-collapsed`; text `--text`, `--text-2`,
`--text-3`, `--text-bright`; lines `--border`, `--border-soft`; accent `--accent`,
`--accent-hover`, `--accent-subtle`; radii `--radius`, `--radius-sm`,
`--radius-md`, `--radius-lg`, `--radius-full`; type `--font-family-base`,
`--font-family-editorial`, `--font-family-mono`, `--font-size-xs…xl`; focus
`--focus-ring` (`#456f9d`), `--focus-halo`, `--focus-ring-shell`; spacing
`--dashboard-pad`, `--dashboard-gap`; elevation `--shadow`, `--shadow-sm`,
`--shadow-md`; motion `--transition`.

Prefer an existing token over a raw value. **Do not** re-add the retired
`--dashboard-*` aliases that shadowed these (`--dashboard-bg`,
`--dashboard-card-bg`, `--dashboard-border`, `--dashboard-radius-lg`,
`--dashboard-shadow`, `--dashboard-shadow-strong`, `--dashboard-primary-hover`,
`--accent-alpha`, `--line-height-base`, `--text-muted`): they had no referents
and were removed.

---

## 3. Semantic colours

One tone vocabulary, used by badges, totems, alerts and result strips:

| Tone | Dashboard token(s) |
| --- | --- |
| success | `--green`, `--green-bg` |
| warning | `--amber`, `--amber-bg` |
| danger / error | `--red`, `--red-bg` |
| info | `--blue`, `--blue-bg` |
| working / active | `--accent`, `--accent-subtle` |
| neutral / queued | `--text-3`, `--surface-3` |

Do not introduce a fifteenth red. Two places still carry a private palette that
shadows these tokens — the Data Quality module and the dashboard banner preview
(`.banner-preview-notice`) — because they render a *mock browser* and a dark
institutional field rather than the workspace itself. New work must use the tokens
above rather than copying those literals.

---

## 4. Dashboard primitives

Import the macro; do not hand-roll the markup:

```jinja
{% from "dashboard/_components.html" import page_header, section_header, empty_state,
     status_badge, form_section, export_dropdown, operation_totem %}
```

| Macro | Renders | Use for |
| --- | --- | --- |
| `page_header(kicker, title, subtitle='', compact=true, entity_type='')` | `.dash-hero` (`.compact-hero`, `.page-breadcrumb`, `.hero-actions`) | the single header of every dashboard page |
| `section_header(title, help_text='', icon='')` | `.panel-head` (+ `.panel-actions`) | a titled region inside a panel |
| `empty_state(title, help_text='', icon='bi-inbox', action_url='', action_label='')` | `.dash-empty-state` | "nothing here yet" |
| `status_badge(value, tone='neutral', label='')` | `.status-badge status-<tone>` | a state chip |
| `form_section(title, help_text='', icon='')` | `.editor-section` (+ `.editor-section-head`, `.editor-section-body`) | an editable group of fields |
| `export_dropdown(formats, section='')` | `.export-dropdown` | the standard export control |
| `operation_totem(...)` | `.operation-totem` | an operation in flight — see §5 |

Surfaces come from one shared layer, "Shared control-instrument surfaces", made
of three groups that must stay identical in edge, radius and elevation:

1. **Panels** — `.modern-card`, `.table-card`, `.ops-panel`, `.editor-section`,
   `.system-panel`, `.control-panel`, `.control-ledger`, `.storage-ledger`,
   `.quality-ledger`, `.search-results`, `.stats-panel`, `.dq-phase`,
   `.portability-panel`
2. **Status strips and summaries** — `.operations-status-strip`,
   `.control-snapshot`, `.stats-summary`, `.log-status-strip`, `.status-strip`,
   `.portability-summary`, `.transfer-workflow`
3. **Dark workflow headers** — `.control-quality-workflow`, `.safety-intro`
   (shell surface, accent left border)

`.panel-head` is the header primitive used *inside* a panel (rendered by
`section_header`), not a surface. Extend one of the three groups instead of
adding a new one-off panel class.

The shared group owns **edge, radius and elevation** only
(`border-color: var(--border); border-radius: var(--radius); box-shadow: var(--shadow)`).
A surface that also needs a filled background declares it as
`background: var(--surface); border: 1px solid var(--border)` — the same pair the
page header (`.dash-hero`) uses — rather than inventing a near-identical
declaration.

---

## 5. The operation totem contract

A totem answers "what is this operation doing right now?".

```html
<div class="operation-totem" data-state="working">
  <i class="bi bi-file-earmark-zip operation-totem__icon" aria-hidden="true"></i>
  <div class="operation-totem__body">
    <small class="operation-totem__eyebrow">Optional eyebrow</small>
    <b class="operation-totem__title" id="…">Title</b>
    <small class="operation-totem__meta" id="…">Secondary line</small>
  </div>
  <span class="operation-totem__state" id="…">Working</span>
  <div class="operation-totem__facts"><!-- optional label/value cells --></div>
</div>
```

* Base geometry is the compact form; `.operation-totem--transfer` is the only
  variant and adds comfortable metrics, the eyebrow, the pill state and the facts
  grid.
* **Tone is always `data-state`**, one of `idle | queued | working | success |
  warning | error`. Never add a one-off `.foo-totem-error` class.
* Keep the element `id`s: dashboard JS addresses totems by id, and writes both the
  label and `dataset.state` through one helper.
* There is exactly one totem implementation. The former `.archive-package-totem`
  and `.transfer-operation-totem` families were merged into this contract; do not
  reintroduce a third variant.

---

## 6. Buttons

| Class | Meaning |
| --- | --- |
| `.btn.btn-primary` | the main safe action |
| `.btn.btn-outline` | secondary action |
| `.btn.btn-outline-danger` | destructive action |
| `.btn.btn-sm`, `.btn.btn-mini` | smaller in dense rows/toolbars |
| `.btn-spinner` | in-progress affordance, driven by `setFormLoading()` |

Disabled styling comes from the shared `.btn:disabled, .btn.disabled` layer;
keyboard focus comes from the `:focus-visible` contract
(`outline: 2px solid var(--focus-ring); outline-offset: 2px`), with
`--focus-ring-shell` inside the sidebar and topbar where the background is dark.
Do not add a new button variant for a single page — the `btn-dq-*` family is the
documented exception for Data Quality review actions.

---

## 7. Status semantics

`status_badge(value, tone)` renders `status-badge status-<tone>`. Existing tones:
`success`, `warning`, `danger`, `info`, plus the literal state names
`pending`, `in_review`, `approved`, `rejected`. A status is text plus a tone;
never encode state only in colour, and always keep the text.

---

## 8. Modals, overlays and toasts

One overlay contract: `--dashboard-modal-gutter` is declared **once** as a
`clamp(...)` and is only narrowed by the responsive overrides; it drives the
modal width, max-height and margin together. `.dashboard-shell .modal-header
.modal-title` is the title rule, and the retired `.dashboard-document .modal`
override must not come back (a test pins its absence). Modal **bodies** may lay
out their own content freely, but the frame geometry must not be re-declared per
page.

Sizing/spacing boilerplate belongs to the contract, not to a page. Toasts use
`showToast(message, type)` (which mirrors to `MIFPLog`) and the
`toast-success|warning|error` tones; do not build a second notification widget.

`.dashboard-document` is the shell root (sidebar + main); `.dashboard-shell` is
the content shell. Both are append-only: `is-collapsed` and `is-mobile-open` on
`.dashboard-document` are the complete state vocabulary.

---

## 9. Responsive breakpoints

There is **no** canonical breakpoint scale. Both sheets carry many per-component
widths (`420 480 520 575.98 576 600 620 640 680 720 760 767 768 780 800 820 900
920 980 991 1050 1100`, plus one `min-width: 577px`, in the dashboard;
`360 420 480 600 640 680 720 760 768 900 980 1024 1040 1180 1280` in the public
sheet) and they are intentional: a rule that constrains one widget does not need
to share a number with a rule that constrains another.

Two rules follow:

* Do not merge two breakpoints just because the numbers are close — only merge
  when the rules inside describe the *same* component.
* **Never** add a breakpoint that is already superseded by a later, wider one. A
  media rule does not outrank a later non-media rule, so it is easy to create a
  dead viewport band by accident. Before deleting what looks like a duplicate,
  check what the *other* rule actually targets: the dashboard's `575.98px` block
  and the public sheet's `.section { padding: 56px 0 }` both look redundant next
  to a nearby `576px`/`640px` block, but each is the only override for its own
  component. Both were kept.

Reduced motion: the two universal-selector blocks (`.dashboard-shell *`,
`.public-site *`) cover transitions and animations globally. A few components
(toasts, drag targets, the hero entrance) additionally neutralise their own
keyframes in local `prefers-reduced-motion` blocks — keep that pattern for any
new animation that would still read as motion with the duration clamped.

---

## 10. JavaScript modules

| Module | Responsibility |
| --- | --- |
| `js/logger.js` | the only console writer: `MIFPLog`, level filtering, redaction, fetch/XHR diagnostics, global error handlers |
| `js/homepage.js` | public site behaviour only |
| `js/markdown-editor.js` | shared sanitised markdown preview |
| `js/dashboard/api.js` | `window.MIFP`: `request`, `once`, `csrfToken`, `formatBytes`, `safeMessage` |
| `js/dashboard/core.js` | `window.MIFPUI`: `showToast`, `setFormLoading`, `clearFormLoading`, `openAssetPicker`, `elapsedLabel`, `setProgressBar` — plus the internal confirm dialog, sidebar/shell behaviour and the page-loading overlay |
| `js/dashboard/<page>.js` | one page/feature each (content, conferences, archive-import, data-portability, data-quality, safety-operations, stats, site-copy, login) |

Rules:

* No framework, no bundler, no TypeScript. Files are loaded with `defer` in
  document order: `logger.js` → `api.js` → `core.js` → page module
  (`logger.js` is also loaded on the public and login pages, where the dashboard
  modules are absent).
* A helper belongs in `core.js` (UI) or `api.js` (network) when it is genuinely
  shared; page-specific DOM resets stay in the page module.
* Prefer `data-*` attributes and element `id`s as behaviour hooks over
  presentation classes.
* Keep ARIA state synchronised: `aria-expanded`, `aria-busy`, `aria-valuenow`,
  `disabled`, `hidden`.

---

## 11. Browser logging rules

* Only `logger.js` may touch `console`. Everything else calls
  `MIFPLog.debug|info|warn|error(event, details)`. A test enforces this.
* Event ids are dotted, lowercase, with `_` or `-` inside a segment:
  `api.request_failed`, `data-quality.bulk-accept.finished`. Use three segments
  (`<module>.<operation>.<event>`) for anything with a lifecycle. Never prose:
  `'Package upload started'` is a UI activity line, not a log event.
* A module may keep a local alias (`contentLog`, `eventLog`, `transferLog`) so
  the call is short; `dashboardLog(level, event, details)` in `core.js` is the
  same thing with a dynamic level. The alias must be assigned from
  `window.MIFPLog`.
* Default level is `warn`. It is set **before** the logger loads, by
  `<html data-mifp-log-level="debug">` (an unknown value falls back to `warn`);
  `MIFPLog.level` reports the resolved level. There is no runtime setter — the
  level is deliberately fixed for the life of the page.
* The logger redacts by key (password/secret/token/csrf/authorization/cookie/
  session/api key/private key/e-mail/phone), scrubs e-mail and `Basic`/`Bearer`
  credentials, bounds depth (3), arrays (20), object keys (40) and strings
  (1000), and logs URLs as `pathname` only — never a query string or fragment.
* **Never** log passwords, CSRF tokens, cookies, Authorization headers, request
  bodies, form contents, e-mail addresses, imported record contents or uploaded
  file contents. There is no browser-to-server log shipping; the console is a
  diagnostic, not analytics.

---

## 12. Backend logging

`utils/logger.py` is queue-backed and stream-separated: `mifp_app` (application,
≤ WARNING), `errors`, `access`, `audit`, `security`.

* `LOG_FORMAT` (`text`|`json`) is the single fallback for both destinations, so
  setting only it behaves exactly as before.
* `LOG_FILE_FORMAT` and `LOG_CONSOLE_FORMAT` optionally override one destination
  each. Production uses JSONL files and a compact text console; `.jsonl` vs
  `.log` file extensions follow the file format.
* Structural fields the logger sets itself: `event`, `stream`, `request_id`,
  `logger`, `level`, plus the client fingerprint on access records.
* Field names in use at call sites, in rough frequency order: `category`,
  `outcome`, `ip`, `username`, `site_id`, `error_type`, `severity`, `run_id`,
  `job_id`, `record_id`, `asset_id`, `bundle_id`, `filename`, `path`, `format`,
  `role`, `count`, `bytes`, `request_id`, `duration_ms`. Reuse these rather than
  inventing a synonym, and in particular use `duration_ms` for timing — never
  `duration`, `elapsed_ms` or `time_ms`.
* Error ownership: lower layers raise, the boundary/operation layer logs **once**
  with context, unexpected errors keep a stack trace. Do not log the same
  exception at three layers.
* Access logs carry exactly `endpoint`, `method`, `path`, `status`,
  `duration_ms`, `request_bytes`, `response_bytes`, `query_keys` (names only)
  and a privacy-safe client fingerprint. Never a body, and never a query
  *value* — that is why the field is `query_keys`.

---

## 13. Things that must not be duplicated

* A second notification/toast mechanism, confirm dialog or asset picker.
* A second progress implementation: use `window.MIFPUI.setProgressBar(track, fill, percentEl, value)`
  and `window.MIFPUI.elapsedLabel(ms)`.
* Another operation totem variant.
* Another modal viewport/gutter rule.
* A new `:root` block in `homepage.css` (exactly two are allowed) or a new
  dashboard surface class outside the shared surface group.
* A raw colour literal when a semantic token already exists.
* A `console.*` call outside `logger.js`.
* A page-local copy of `request`, `once`, `csrfToken`, `formatBytes`,
  `safeMessage`, `showToast`, `setFormLoading` or `clearFormLoading`.

---

## 14. Before you commit frontend changes

```bash
bash test_all.sh --suite webapp -- -q \
  TESTS/webapp/test_frontend_coherence.py \
  TESTS/webapp/test_revamp_contract.py

for f in $(find MIFPAPP/CORE/mifp_app/static/js -name '*.js' -not -path '*vendor*'); do
  node --check "$f" || exit 1
done

git grep -n 'console\.' -- MIFPAPP/CORE/mifp_app/static/js | grep -v logger.js
git diff --check
python3 tools/check_repo_hygiene.py
```

(The full `bash test_all.sh --suite quick` also runs the database and repository-tool
suites; run it before handing the change over.)

`test_revamp_contract.py` pins the visual identity (tokens, gradient counts,
`Georgia` count, pinned selectors); `test_frontend_coherence.py` pins the
contracts described here.

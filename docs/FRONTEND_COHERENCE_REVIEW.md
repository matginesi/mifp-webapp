# MIFP frontend coherence review

Scope: the CSS/JS/logging coherence pass over the MIFP public site and
dashboard. This is a **review of the work**, not a design proposal, and not a
re-audit of the platform's security posture (that lives in
[`audit/MIFP_PRODUCTION_SECURITY_AUDIT.md`](audit/MIFP_PRODUCTION_SECURITY_AUDIT.md)).

Everything below is stated as measured on the working tree, with the command
that produced it. Where a number is a judgement call, it says so.

Companion documents:

* [`FRONTEND_STYLE_GUIDE.md`](FRONTEND_STYLE_GUIDE.md) — the rules a future
  change must follow.
* `TESTS/webapp/test_frontend_coherence.py` — the machine-checkable half of
  those rules.
* `TESTS/webapp/test_revamp_contract.py` — the pre-existing visual-identity pin.

---

## 1. Method, and why it is trustworthy

| Question | How it was answered |
| --- | --- |
| Did anything change visually? | Compared **every selector → declaration body** pair between `HEAD` and the working tree with a real rule parser, not a line diff. |
| Is a removed rule actually dead? | Grepped each removed selector's classes against every template, every application JS file, and the other three stylesheets. |
| Is the identity intact? | `test_revamp_contract.py` (pinned tokens, gradient counts, `Georgia` count, pinned selectors, forbidden retired selectors), re-run unchanged. |
| Is the totem contract real? | Renders the shared Jinja macro and asserts the attribute output, then asserts the pages call it. |

The rule-parser comparison matters: a plain `git diff` on this stylesheet is
misleading because several long-standing rules share a physical line
(`.bar-row i b { … }` and `.chart-fallback { … }`, or
`.data-scroll { … }.system-secondary-grid{ … }`), so a reflow reads as a deletion
and a deletion reads as a reflow. Comparing *selector → declaration body* maps
instead gives: **60 selectors removed, 23 added, 0 pre-existing bodies changed.**

The comparator (~30 lines, run against `git show HEAD:<sheet>` and the working
tree) is reproduced here so the numbers can be re-derived rather than trusted:

```python
import re, subprocess, pathlib

def rules(text):
    """(selector, body) for every rule, tolerating many rules per line and
    descending into @media/@supports."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    out, i, start = [], 0, 0
    while True:
        b = text.find('{', i)
        if b == -1:
            break
        sel = text[start:b].strip()
        depth, j = 1, b + 1
        while j < len(text) and depth:
            depth += (text[j] == '{') - (text[j] == '}')
            j += 1
        body = text[b + 1:j - 1].strip()
        if sel and not sel.lstrip().startswith('@'):
            out.append((' '.join(sel.split()), ' '.join(body.split())))
        elif sel.lstrip().startswith(('@media', '@supports')):
            out.extend(rules(body))
        i = start = j
    return out

def simples(text):
    """selector -> list of bodies, so a base rule and its responsive override
    are two entries rather than one overwriting the other."""
    table = {}
    for selector, body in rules(text):
        for one in selector.split(','):
            one = ' '.join(one.split())
            if one:
                table.setdefault(one, []).append(body)
    return table

sheet = "MIFPAPP/CORE/mifp_app/static/css/dashboard.css"
old = simples(subprocess.run(["git", "show", f"HEAD:{sheet}"],
                             capture_output=True, text=True).stdout)
new = simples(pathlib.Path(sheet).read_text(encoding="utf-8"))
print("removed", len(set(old) - set(new)))
print("added  ", len(set(new) - set(old)))
print("changed", [k for k in old if k in new and old[k] != new[k]])
```

For the homepage sheet the same script prints `removed 2, added 0, changed []`.

Reproduce:

```bash
MIFP_LOAD_DOTENV=0 python3 -m pytest \
  TESTS/webapp/test_frontend_coherence.py TESTS/webapp/test_revamp_contract.py -q
bash test_all.sh --suite quick
```

---

## 2. What the identity pin says (unchanged)

| Invariant | dashboard.css | homepage.css |
| --- | --- | --- |
| `linear-gradient(` | **0** | **3** |
| `radial-gradient(` | 0 | 0 |
| `Georgia` (dashboard only is asserted) | 1 | 1 *(informational)* |
| `:root {` (`homepage` asserted; dashboard informational) | 3 *(informational)* | **2** |
| `--content-bg: #eef1f4` | present | — |
| `--focus-ring` | `#456f9d` | `#86a8eb` |
| lines (HEAD → now) | 4687 → 4603 | 3488 → 3466 |

Bold values are pinned by `test_revamp_contract.py`; the rest are reported for
context.

`test_revamp_contract.py` passed unchanged, in full, before and after. The two
systems remain deliberately distinct: the dashboard has **no gradients at all**
and a cold blue-grey workspace; the public sheet keeps its three scientific-field
gradients and its warmer editorial field.

---

## 3. CSS problems found — dashboard bundle

### 3.1 Dishonest provenance banners

Every section banner claimed the file was generated from source sections:

```
Source section: static/css/dashboard/10-core.css   ← the directory does not exist
```

The split source directory does not exist and (per `test_revamp_contract.py`)
must never come back, so the header actively instructed the next maintainer to
edit a tree that is not there. **Fixed:** three honest `SECTION 1/2/3` banners for
a 4.6k-line file, plus a file header stating that the banners are reading aids.

### 3.2 A de-facto duplicate architecture

Two components had grown in parallel and were drifting:

* `.archive-package-totem` — 5 rules, 2 of them responsive overrides
* `.transfer-operation-totem` / `.transfer-totem-*` — 17 rules, including six
  per-tone state rules (`.transfer-operation-totem[data-state="error"] .transfer-totem-icon`, …)

They expressed the same idea with two class vocabularies, two markup shapes and
two tone mechanisms. This is the single biggest coherence defect found and is
addressed in §5.

### 3.3 Dead selectors

**60 simple selectors removed**, each verified unreferenced across all templates,
all application JS and the other three stylesheets:

| Group | Count | Representative |
| --- | --- | --- |
| Legacy totem families | 22 | `.archive-package-totem`, `.transfer-totem-copy strong` |
| Unused status-strip widget | 10 | `.system-status-strip`, `.system-status-strip em` |
| Unused alert parts | 7 | `.alert-icon`, `.alert-body b` |
| Unused card/chart/table leftovers | 10 | `.mini-chart`, `.kv span`, `.empty-cell` |
| Unused layout helpers | 3 | `.inline-action-form`, `.inline-auto`, `.cell-text.is-expanded` |
| Other dead widgets | 8 | `.btn-warning:disabled`, `.dq-sep`, `.privacy-badge` |

All the borderline cases were re-checked against the *container* class rather
than the removed child class:

* `.alert-icon` / `.alert-body` — `alert-info`/`alert-warning` survive as
  containers, but no `.alert-icon` child exists anywhere → safe.
* `.btn-warning:disabled` / `.btn-warning.disabled` — `btn-warning` appears in
  **no** template or JS file, and the shared `.btn:disabled, .btn.disabled` layer
  (dashboard.css L183) covers every button anyway → safe.
* `.cell-text.is-expanded` — `is-expanded` has zero consumers, template, JS or
  CSS. This is the one removal that could have been a dropped feature rather than
  dead styling; it is listed in §10.
* `.table-card > .log-results-head + .data-scroll` — `.log-results-head` was
  itself dead and is gone.

### 3.4 Dead tokens

10 tokens had no referents and shadowed live ones. Removed:

```
--text-muted  --accent-alpha  --line-height-base
--dashboard-bg  --dashboard-card-bg  --dashboard-border
--dashboard-radius-lg  --dashboard-shadow  --dashboard-shadow-strong
--dashboard-primary-hover
```

### 3.5 Result

**60 selectors removed, 23 added (all totem), 0 pre-existing declaration bodies
changed.** That last number is the "no visual regression" claim: with the
exception of the totem merge, no rule that existed before behaves differently
now.

One candidate was examined and **left alone**: the `@media (max-width: 575.98px)`
toast block. It looks redundant next to the later `576px` blocks, but those
control different components (wizard steps, the event wizard grid, the create
record grid), so the toast rule is the only viewport override for
`#toastContainer` at that width. It stays.

---

## 4. CSS problems found — homepage bundle

The public sheet was much cleaner. Measured diff: **2 selectors removed, 0
added, 0 declaration bodies changed.**

* The same dishonest `Source section:` banners were rewritten as
  `SECTION — <description>` (10 banners).
* `.card-grid.cols-2` / `.card-grid.cols-4` had zero references; `.card-grid`
  itself covers the layout. Removed. *(Confidence: moderate — see §10.)*

Everything else in the homepage diff is comment text. No token, colour,
typography or layout rule was touched — in particular the several `.section`,
`.hero-*` and `56px` spacing rules that *look* duplicated were deliberately kept,
because each belongs to a different component or viewport band.

---

## 5. The operation-totem contract

### Before

| | Archive import | Data transfer |
| --- | --- | --- |
| Container | `.archive-package-totem` | `.transfer-operation-totem` |
| Title | `<b>` | `<strong>` |
| Meta | `<small>` | `<span>` |
| Tone | **no tone mechanism** | `data-state` + six per-tone descendant rules |
| Markup | hand-written | hand-written |

### After

One contract, one implementation, in CSS **and** in Jinja:

```
.operation-totem                        base geometry (compact)
  __icon  __body  __eyebrow  __title  __meta  __state  __facts
.operation-totem--transfer              the only variant (comfortable)
.operation-totem[data-state="…"]        the only tone mechanism
```

* `.operation-totem` base rule: 1. `--transfer` variant: 1. Every part: exactly
  one base rule.
* Tone is `data-state` only — a test asserts that no
  `.operation-totem--success` / `__error` / `--queued` / … class exists.
* The comfortable variant is a **modifier**, not a second component.
* Both pages now call the shared `operation_totem()` macro in
  `dashboard/_components.html` instead of hand-writing markup. The macro gained
  an `icon_id` parameter so the transfer variant's wrapper-style icon is
  expressible without a second code path.
* JS addresses the totem by **id plus `dataset.state`** only — no class coupling.
  `archive-import.js` gained one `setPackageState(label, state)` helper that
  writes both, so the label and the tone can no longer disagree.
* New states are now *visible* where they were not before: the archive totem
  moves through `queued → working → success | warning | error` (previously the
  package state label changed but the totem had no tone).

### Evidence the rendered markup is equivalent

Both pages were rendered through the real Flask app and the totem fragments
dumped. The **archive** totem is identical element-for-element to the markup it
replaced: same tag, same class list, same ids, same order, same text — only
insignificant whitespace between the tags differs, which HTML ignores.

The **transfer** totem differs in exactly two places, both introduced by routing
it through the shared macro: the title is `<b>` where it was `<strong>`, and the
meta line is `<small>` where it was `<span>`. Neither changes the computed style:
`.operation-totem__title` and `.operation-totem__meta` set `font-size`
explicitly, and a class selector (specificity 0,1,0) beats the user-agent
`small { font-size: smaller }` rule (0,0,1). Both `<b>` and `<strong>` are bold by
default and neither rule overrides `font-weight`. Element order, classes, ids and
text are unchanged.

---

## 6. Components unified

* **Totem** — see §5. Two implementations → one.
* **Progress bar** — `archive-import.js` and `data-portability.js` each carried a
  private `setProgress`/`formatElapsed`. Consolidated into
  `window.MIFPUI.setProgressBar(track, fill, percentEl, value)` and
  `window.MIFPUI.elapsedLabel(ms)`.
  This fixed a real accessibility bug: the transfer bar was not updating
  `aria-valuenow`, because only the archive copy did. One implementation keeps
  the ARIA contract identical everywhere.
* **Macro surface** — `page_header`, `section_header`, `empty_state`,
  `status_badge`, `form_section`, `export_dropdown`, `operation_totem`. No new
  one-off panel/notification/dialog mechanism was added; the existing shared
  ones were used.

---

## 7. JavaScript duplication

* Zero `console.*` calls outside `js/logger.js` (enforced).
* Zero page-local re-implementations of `formatBytes`, `csrfToken`,
  `safeMessage`, `showToast`, `setFormLoading` — they all live in `api.js`
  (`window.MIFP`) or `core.js` (`window.MIFPUI`). `escapeHtml` has no definition
  anywhere at all, i.e. no module ever hand-rolled it.
* The only duplication removed in this pass is the progress/elapsed pair above.
  The JS modules were already IIFE + shared-namespace; no framework was
  introduced and none is needed.

The shared error shape is `window.MIFP.request()`, which surfaces
`safeMessage(status, payload)`; page modules `try/catch` around stream parsing
and log once with a dotted event id rather than re-deriving messages per page.

---

## 8. Browser logging

The central logger (`js/logger.js`) was already the only console writer; this
pass **proved and pinned** that rather than rewriting it.

Verified contract:

| Property | Value |
| --- | --- |
| Raw `console.` call sites outside `logger.js` | 0 |
| `console.` call sites inside `logger.js` | 1 (`console.log`, reached through the bounded method map) |
| Default level | `warn`, set before load by `<html data-mifp-log-level>` |
| Distinct event ids | 48, across 14 namespaces |
| Malformed event ids | 0 |
| Redaction | sensitive-key match, e-mail, `Basic`/`Bearer`, secret query values, depth 3, 20 array items, 40 keys, 1000-char strings |
| URLs | reduced to `pathname` by `safePath()` |

Two normalisation decisions:

* Event ids are dotted and lowercase, with `_` or `-` **inside** a segment
  (`api.request_failed`, `data-quality.bulk-accept.finished`). Both spellings
  already existed; the rule now documents both rather than pretending one is
  universal.
* Modules keep short aliases (`contentLog`, `eventLog`, `transferLog`,
  `dashboardLog(level, …)`). The regression test resolves the aliases instead of
  matching only literal `MIFPLog.` calls — the previous, narrower check saw 2 of
  48 events and would have missed almost any regression.

Nothing logs passwords, CSRF tokens, cookies, form contents, e-mail addresses or
imported record contents. There is no browser-to-server log shipping.

---

## 9. Backend logging

`utils/logger.py` keeps its queue-backed, stream-separated design
(`mifp_app`/`errors`/`access`/`audit`/`security`) and its rotation and
fingerprinting. One normalisation was added:

**Destination-specific formats.** `LOG_FORMAT` remains the single fallback for
both destinations — an unchanged deployment keeps byte-identical behaviour.
`LOG_FILE_FORMAT` and `LOG_CONSOLE_FORMAT` now override one destination each:

| Variable | Default | Production |
| --- | --- | --- |
| `LOG_FORMAT` | `text` | — |
| `LOG_FILE_FORMAT` | `LOG_FORMAT` | `json` (JSONL files, durable record) |
| `LOG_CONSOLE_FORMAT` | `LOG_FORMAT` | `text` (readable `docker logs`) |

* An invalid value fails loudly and specifically:
  `LOG_FILE_FORMAT must be json or text` / `LOG_CONSOLE_FORMAT must be json or text`.
  Previously only `LOG_FORMAT` was validated, and only *after* an unrelated
  formatter had already been constructed and only on the path that missed the
  reconfigure-cache early return. All three are now resolved and validated up
  front, before anything is built.
* Both values participate in the reconfiguration signature, so changing one at
  runtime re-installs the handlers.
* The file extension follows the *file* format (`.jsonl` vs `.log`), which is
  what makes `LOG_FILE_FORMAT` independently useful.
* `deploy/compose.production.yaml`, `MIFPAPP/CORE/.env.example` and
  `deploy/.env.production.example` document the pair.

No logger call site, stream name, field name or redaction rule changed — that
would have invalidated existing log tooling for no benefit.

---

## 10. Remaining frontend debt (found, deliberately **not** fixed)

These are reported rather than fixed because fixing them means changing
appearance or behaviour, which is out of scope for a coherence pass.

1. **Duplicate selectors are still common.** Re-measured with the same parser,
   counting every declaration including intentional responsive overrides:
   **309** dashboard selectors and **168** homepage selectors are declared more
   than once (385 and 257 extra declarations). Worst dashboard offenders:
   `.dash-hero`, `.panel-head`, `.hero-actions`, `#toastContainer.toast-container`,
   `.ops-panel`, `.search-results` (×4 each). Worst homepage: `.hero h1` (×7),
   `.stats-inner` (×6), `.container`, `.section`, `.hero` (×5 each). Some of these
   are deliberate (a base rule plus its media override); the rest need
   per-component visual verification to merge safely, not a script.
2. **Raw colour literals outnumber tokens.** Dashboard: 218 occurrences / 137
   distinct (`#fff` ×50). Homepage: 175 occurrences / 121 distinct. The Data
   Quality module still carries a private Tailwind-derived palette that
   duplicates `--red/--amber/--green/--blue/--purple`. A token migration would
   change rendered colours wherever a literal is *not* exactly equal to its token.
3. **The asset picker is unstyled.** `_asset_picker.html` and `core.js` emit
   `.asset-picker-tabs`, `.asset-tab`, `.asset-tab-panel`, `.asset-picker-grid`,
   `.asset-picker-item`, `.asset-picker-empty`, `.asset-picker-search`,
   `.picker-create-form` and `.link-editor-form`; **none of them has a rule in any
   stylesheet** (confirmed against `HEAD` too — this is pre-existing, not caused
   by this pass). Only `.asset-picker-thumb` is styled. 72 class tokens across the
   app are in this state; most are intentional JS hooks (`js-lightbox`,
   `doc-type-hidden`) or Bootstrap-only helpers, but the picker's layout classes
   are not. Fixing it is a design task.
4. **Breakpoints are not on a scale.** Roughly twenty distinct widths per sheet.
   They are per-component, so **none were removed** in this pass: several that
   looked dead turned out to be the only override for their component (the
   `575.98px` toast block, the `56px` `.section` padding rule). Only the two
   zero-reference `cols-*` selectors went.
5. **`.is-expanded`** on `.cell-text` was a complete dead end (no CSS, template or
   JS consumer). It was removed, but if an expansion feature was planned, that
   intent now lives only in git history.
6. **`.cols-2` / `.cols-4`** had zero references; the removal is the
   lowest-confidence deletion in this pass, because a hand-authored class with no
   stylesheet rule is sometimes deliberate.
7. **`.operation-totem--transfer` remains a variant, not a merge.** The archive
   totem is compact and the transfer totem is comfortable; forcing one geometry
   would change the transfer modal. Two variants sharing one contract is the
   correct end state, but it is not literally "one rule".
8. **`status-neutral` / `status-badge` tone vocabulary** is partly literal:
   `archive.html` and `stats.html` use `status-neutral`, which has no rule of its
   own and falls back to the base badge. Cosmetic, and left alone.

---

## 11. Regression coverage added

`TESTS/webapp/test_frontend_coherence.py` (11 tests):

| Test | Pins |
| --- | --- |
| `test_raw_console_calls_exist_only_inside_the_central_logger` | one console writer; `logger.js` uses only its bounded method map |
| `test_application_js_uses_namespaced_log_events` | 40+ dotted ids, alias resolution, no prose, namespaced |
| `test_dashboard_has_one_operation_totem_contract` | one base rule per part, one variant, `data-state`-only tone |
| `test_operation_totem_macro_renders_every_id_the_dashboard_js_addresses` | renders the macro and asserts the ids the JS selects |
| `test_pages_render_the_totem_through_the_shared_macro` | pages call the macro; no hand-written totem markup |
| `test_shared_progress_primitive_is_the_only_implementation` | one `setProgressBar`/`elapsedLabel`; callers delegate |
| `test_css_section_markers_are_honest_reading_aids` | no `Source section:`; banner intent stated |
| `test_removed_dead_component_selectors_stay_removed` | the dead selectors do not come back |
| `test_browser_logger_redacts_secrets_and_url_values` | the redaction contract |
| `test_log_file_and_console_formats_can_differ` | per-destination formats |
| `test_log_format_fallback_preserves_legacy_single_format` | `LOG_FORMAT` fallback |

These test structure and contracts, never pixels — pixels are pinned by
`test_revamp_contract.py`, which is unchanged.

---

## 12. Verdict

* **Visual identity: preserved.** The identity pin passes unchanged; the only
  declaration changes are inside the new totem rules.
* **CSS coherence: materially improved.** 60 dead dashboard selectors and 10 dead
  tokens removed; the two-totem split collapsed into one documented contract;
  provenance banners now honest.
* **JS duplication: reduced where it was real** (progress/elapsed) and absent
  where it was not.
* **Logging: one contract each side.** Browser logging was already centralised
  and is now genuinely enforced; backend logging gained an opt-in split between
  the durable file record and the human console, with the old behaviour as the
  default.
* **Remaining debt is enumerated above** and is mostly about duplicate selectors
  and raw colour literals — real, measurable, and not safely mechanical.

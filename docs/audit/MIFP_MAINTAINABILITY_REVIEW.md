# MIFP Maintainability Review

**Repository:** `MifpWebNew`
**Date:** 2026-09-18
**Companion document:** [`MIFP_PRODUCTION_SECURITY_AUDIT.md`](MIFP_PRODUCTION_SECURITY_AUDIT.md)

---

## 1. Scope and guiding decision

The brief for this task was explicit: improve maintainability without
redesigning the application, without touching the UI, and preferring the
smallest robust change over a large risky rewrite. That constraint drove every
decision below.

The repository was already in good shape. The baseline was green
(`873` tests passing, repository hygiene clean), the layering
(`SCRAPERS` → canonical artifacts → `MIFPAPP/DATABASE` → `MIFPAPP/CORE` →
Docker → GHCR → VPS → Caddy) is respected throughout, and there was no
accumulated rot of the kind that normally justifies aggressive refactoring:
`ruff` (with `F401,F811,F821,F823,F841,E722`) reported only **nine** findings
across the entire Python tree.

Consequently this review is deliberately **not** a file-splitting exercise. It
removes the dead code that exists, adds one small shared module where three
call sites genuinely performed the same safety-critical operation, and
documents why the remaining large modules were left intact.

---

## 2. Largest production modules (before → after)

Python source, excluding tests and vendored assets:

| Module | Lines (before) | Lines (after) | Change |
| --- | ---: | ---: | --- |
| `services/data_portability.py` | 1737 | 1758 | +21 (security fixes + comments) |
| `routes/dashboard_portability.py` | 1462 | 1462 | unchanged |
| `services/importers.py` | 1365 | 1380 | +15 (path validation) |
| `services/data_quality/analyzer.py` | 1171 | 1183 | +12 (throttled logging) |
| `services/public_repository.py` | 1100 | 1100 | unchanged |
| `services/assets.py` | 1018 | 1091 | +73 (`_ip_literal`, deadline, comments) |
| `routes/dashboard_assets.py` | 966 | 975 | +9 (truthful delete) |
| `routes/dashboard_conferences.py` | 886 | 886 | unchanged |
| `routes/dashboard_content.py` | 809 | 809 | unchanged |
| `services/dashboard_repository.py` | 765 | 765 | unchanged |
| `routes/dashboard_control.py` | 733 | 729 | −4 (dead locals) |
| `utils/logger.py` | 730 | 738 | +8 (diagnosable flusher) |
| `deploy/deploy.sh` | ~1257 | ~1260 | +3 (SQLite busy timeout) |
| `deploy/backup.sh` | 207 | 230 | +23 (secrets fallback + restic retention) |

Nothing was split. One module was added:
`MIFPAPP/CORE/mifp_app/utils/file_safety.py` (35 lines).

### Explicitly out of scope

`mifp_app/static/css/dashboard.css`, `mifp_app/static/css/homepage.css`,
`static/css/archive.css`, the vendored Bootstrap/font assets and the dashboard
JavaScript bundles (`static/js/dashboard/*.js`) were **not** touched. They are
large by design, the UI is frozen for this task, and their size is not a
maintainability defect. No template, no CSS, no frontend component and no
responsive rule was modified; `git status` confirms zero changes under
`templates/` and `static/`.

---

## 3. Responsibilities identified per large module

Each candidate above ~1000 lines was examined for independent
responsibilities, dependency direction, cohesion, public API, callers, mutable
state and error handling.

| Module | Responsibilities found | Cohesion judgement |
| --- | --- | --- |
| `data_portability.py` | Bundle assembly, ZIP serialisation, ZIP parsing/validation, JSONL import, durable-state restore, provenance restore, asset path/link helpers | **High cohesion around one contract** (the portability format). The validation helpers form a clear internal layer and are only meaningful together with the manifest constants. |
| `dashboard_portability.py` | HTTP surface for export/import, upload staging, job orchestration, progress, caching | **Not cohesive in the classic sense**, but it is a thin orchestration layer over the service; splitting templates/routes from upload staging would move code without reducing coupling. |
| `importers.py` | Record schema validation, entity upsert/merge, link and asset merging, per-record savepoint transaction handling | **High cohesion**: one import pipeline with a single transaction contract. |
| `data_quality/analyzer.py` | Rule evaluation over content tables, progress persistence, run bookkeeping | Already a package (`analyzer`, `executor`, `planner`, `normalizer`, `cluster`, `policies`, `quarantine`, `models`); the analyzer is the rule engine and is legitimately large. |
| `public_repository.py` | Read models for every public surface + HTML sanitisation | **High cohesion** as the public read layer; splitting per entity would create many small modules sharing the same connection/media-url helpers. |
| `assets.py` | Asset storage/publication, validation, SSRF validation, pinned HTTP transport, downloads, recovery | Two real sub-responsibilities (local asset library vs remote fetch). The remote half is a genuine future extraction candidate; see §7. |
| `dashboard_assets.py`, `dashboard_content.py`, `dashboard_repository.py` | Dashboard CRUD surfaces and the shared repository with its table allowlist | Repository is cohesive; routes are thin. |
| `utils/logger.py` | Logging setup, redaction, request logging, audit/security streams, metrics retention | Cohesive but the largest utility; the redaction layer is a plausible future extraction. |
| `deploy/deploy.sh` | Bootstrap-side operator CLI: config, registry, deploy, upgrade, rollback, restore, events, PHP allow-list, security check | Large but **flat**: `do_*` functions with a dispatcher, strict mode, no `eval`, validated inputs. |

---

## 4. Modules decomposed

**One.** `utils/file_safety.py` was extracted because three archive extractors
performed the *same* safety-critical operation — writing extracted bytes to a
destination whose path is derived from untrusted archive data — and each got it
slightly wrong in the same way (plain `open(..., "wb")`, which follows a
symlink in the final path component).

```text
mifp_app/utils/file_safety.py
    open_write_no_follow(path, *, mode=0o640) -> IO[bytes]
```

Callers migrated:

* `services/asset_cleanup.py` — asset ZIP import
* `services/data_portability.py` — portability ZIP asset extraction
* `services/conference_packages.py` — Conference Editor package extraction

This is a genuine shared semantic operation (not a cosmetic helper), it has a
single documented contract, and it closes a real TOCTOU/containment gap.
17 lines replaced three near-identical unsafe writes.

### Why the other large modules were **not** decomposed

* **Compatibility risk without behavioural benefit.** Every large module is
  exercised through public functions by the test suite. Splitting them would
  require either compatibility façades (extra indirection with no reader
  benefit) or a coordinated rename across routes and tests — a large, risky
  diff for zero functional gain, which the brief explicitly discourages.
* **Cohesion, not size, is the signal.** These modules are large because their
  domain is large (a complete portability format, a complete import pipeline, a
  complete public read layer), not because they mix unrelated concerns.
* **The real defects were not structural.** Each HIGH/MEDIUM security finding
  was a missing validation or a wrong error-handling decision inside an
  otherwise well-organised module. Splitting files would not have prevented
  any of them.

---

## 5. Dead code removed

Verified unused by import search, route/CLI dispatch search, template search
and `ruff`, then removed:

| Location | What | Verification |
| --- | --- | --- |
| `mifp_app/__init__.py` | unused `connect` import in `create_app` | only `connect_readonly` referenced |
| `mifp_app/__init__.py` | unused `log_exception` import | no reference in the module |
| `utils/logger.py` | `rotation = max_bytes > 0` (assigned, never read) | the file handler switches on `max_bytes` directly |
| `routes/dashboard_control.py` | unused `app = current_app._get_current_object()` in two operation branches | `ruff` F841; closures use `current_app` |
| `routes/dashboard_data_quality.py` | unused `result = analyze(...)` assignment | side-effect call, return value unused |
| `routes/dashboard_data_quality.py` | unused `record_ids` list in the accept branch (a second, shadowed computation; the used one lives in `_finding_review_context`) | `ruff` F841 + scope check |
| `MIFPAPP/DATABASE/tools/build_database_pkg/runner.py` | dead `webapp_dir = Path(args.webapp_dir)` and its misleading comment (`db_path` is never derived from it) | grep across the module |
| `deploy/vps_config.py` | unused `stat` import | grep for `stat.` |

Deliberately **retained**:

* `stable_fingerprint` re-exported from
  `services/data_quality/normalizers.py` — `ruff` flagged it as unused, but
  `analyzer.py`/`executor.py` import it from there. It is now written with the
  explicit `as stable_fingerprint` re-export idiom plus a comment.
* `--webapp-dir` in the database builder CLI — its *variable* was dead, but the
  argument is part of the launcher's command surface and removing it could
  break callers.
* The `if status in {300, 301, …}` guard in `assets._download_with_retries` —
  unreachable with the default opener, but cheap defence in depth; kept and
  explained rather than deleted.

---

## 6. Duplication reduced

| Duplication | Action |
| --- | --- |
| Archive-destination write (3 sites, subtly different, all unsafe) | extracted `open_write_no_follow` |
| Env-file key lookup in `deploy/backup.sh` | split into `env_value_in(file, key)` + a thin `env_value(key)` wrapper so the secrets file can be read without copy-pasting the parser |
| `reset_rate_limits` / `ip_rate_allowed` rollback-and-close boilerplate in `utils/security.py` | reviewed; left as-is — the two call sites differ in transaction intent and an extraction would obscure the `BEGIN IMMEDIATE` semantics for marginal gain |

Duplication that was **examined and intentionally left alone**: the three
archive-name validators (`data_portability._validate_archive_name`,
`asset_cleanup._validate_asset_archive_path`,
`conference_packages._validate_zip_entries`) look similar but encode genuinely
different contracts (canonical portability layout vs generic asset archive vs
Conference Editor source tree, with different size, suffix and PHP rules).
Merging them would either weaken the strictest variant or add flag parameters
that hide the differences.

---

## 7. Architectural boundaries

Verified preserved end-to-end:

* **`SCRAPERS/`** — untouched; still produces canonical JSONL plus one import
  ZIP, never opens SQLite, never imports from `MIFPAPP/CORE/` or
  `MIFPAPP/DATABASE/`. No change in this task.
* **`MIFPAPP/DATABASE/`** — owns persistent state; only a dead local variable
  was removed from the offline builder. No runtime data was added to the source
  tree.
* **`MIFPAPP/CORE/`** — receives the security and maintainability changes; the
  Docker build context stays `MIFPAPP/CORE`; no `build:` was reintroduced into
  the production compose; no scraper, DB builder or local CLI was moved into the
  image.
* **Root `mifp` CLI** — unchanged; still the only local launcher and still
  contains no production start mode.
* **`deploy/`** — operator surface preserved: `sudo mifpctl <command>` and every
  documented command keep their names, arguments and exit semantics. The shell
  changes are additive (a secrets-file fallback, a restic retention policy, a
  SQLite busy timeout) and stay plain Bash with `set -Eeuo pipefail`, validated
  inputs, no `eval`, no `shell=True`.
* **Persistent data stays outside the image** — `/app/data` is a host volume in
  production; the new `.dockerignore` rules make an accidental bake-in of
  runtime directories impossible.
* **Caddy remains the only public entry point**; Flask stays loopback-only.

---

## 8. Documentation improved

* Module/function documentation added where a contract or a dangerous
  assumption needed stating, in preference to line-by-line commentary:
  * `_ip_literal` explains *why* non-canonical IP notations must be normalised
    before a range check (with the concrete notations).
  * `open_write_no_follow` documents the symlink hazard and the `O_NOFOLLOW`
    contract, including the degraded behaviour on non-POSIX systems.
  * `_download_with_retries` distinguishes the per-socket timeout from the new
    wall-clock budget.
  * `_delete_db_asset`, `_begin` (maintenance marker), `apply_bundle`
    (foreign-key baseline), `_save_progress`, `_metric_flusher_loop` and the
    mailer console provider each explain the failure mode they now surface.
  * Import path validation in `_validate_assets` / `_materialize_asset` states
    that a record path is a package-relative reference, never a local path.
  * `data_portability.parse_zip_payload` documents why `state.json` is only
    accepted from a signed full canonical export.
  * `ghcr-cleanup.yml` documents the container-digest/tag distinction that made
    the previous `ignore-versions` pattern useless.
  * `deploy/Caddyfile` documents why the depth-independent deny matchers exist.
* `.env.example` corrected: `PORTABLE_EXPORT_PRESERVE_DOMAINS` was documented as
  `mifp.eu` with "Empty disables", while the code default is `*` (every public
  source host). The comment now matches the implementation.
* `docs/audit/MIFP_PRODUCTION_SECURITY_AUDIT.md` and its PDF companion add the
  security and deployment architecture (Mermaid) that was previously only
  implicit in `DEPLOYMENT.md`.

No wording-only comments were added to code that is already self-explanatory.

---

## 9. Tests added

Focused regression tests, no broad new integration suites:

| Test | Covers |
| --- | --- |
| `TESTS/webapp/test_asset_ssrf.py::test_validate_external_asset_url_blocks_non_canonical_ip_literals` | decimal/hex/octal/short/IDN loopback and cloud-metadata notations |
| `TESTS/webapp/test_asset_ssrf.py::test_ip_literal_leaves_real_hostnames_unresolved` | `_ip_literal` does not misclassify hostnames or public addresses |
| `TESTS/webapp/test_asset_ssrf.py::test_refused_redirect_is_a_permanent_download_error` | 3xx refusal is not retried |
| `TESTS/webapp/test_asset_cleanup.py::test_import_assets_from_zip_does_not_write_through_symlink` | a pre-existing symlink never redirects extracted bytes; the outside file is untouched |

Existing tests were used as the regression net for the remaining fixes
(portability round-trips, archive tampering, auth, logging, backup operator,
operation recovery, data quality, deploy state machine, docker compose
contract). **No test was weakened, skipped or converted to an expected failure**;
no expected value was edited to make broken behaviour look correct.

---

## 10. Diff shape

28 files modified, 2 files added, no deletions. Nothing under `templates/` or
`static/`. No mass formatting, no renames, no reordering of unrelated code.
The diff is reviewable module by module and every hunk maps to a finding in the
security report except the dead-code removals and the documentation updates
listed above.

---

## 11. Recommended follow-up (largest value first)

1. Extract the remote-fetch half of `services/assets.py` (SSRF validation,
   pinned transport, download/retry, recovery) into a dedicated
   `services/remote_assets.py`. It is the one genuine sub-responsibility split
   in the tree, and the boundary is already clean.
2. Extract the redaction layer from `utils/logger.py` if it grows further; it is
   the most reused and least dependent part of that module.
3. Move the mutating repairs out of `data_quality/executor.verify_invariants`
   into an explicit, separately audited repair function (also noted as a
   security follow-up — the check currently deletes rows).
4. If `data_portability.py` grows again, split the *validation* half
   (`_validate_*`, manifest/state parsing) from the *serialisation* half before
   touching the import pipeline.

---

## 12. Second round (pre-deployment hardening)

The follow-up round added no new architecture. Its maintainability-relevant
changes were:

* **One new module:** `MIFPAPP/CORE/mifp_app/utils/file_safety.py`
  (`open_write_no_follow`) was already introduced in the first round; the second
  round reused it unchanged rather than adding a second helper.
* **`deploy/deploy.sh`**: three new self-contained functions — `do_ssh_harden`,
  `do_ssh_rollback`, `check_backup_health` — plus `do_config_check` gaining a
  loopback-port assertion. Each is a flat function next to the existing `do_*`
  commands; no dispatcher or option-parsing framework was introduced.
* **`deploy/backup.sh`**: four small guards (regular-DB check, stale-temp sweep,
  allow-list invariant, no-prune rotation guard) and validation moved *before*
  the operation it protects.
* **No module was split.** The largest production modules are unchanged in shape;
  the second round only added the previously documented checks and comments, plus
  `unicodedata`-based collision detection in the three extractors (three small
  additions, not a shared abstraction, because their naming rules differ).
* **No UI change whatsoever**: nothing under `templates/` or `static/` was
  touched in either round.
* **Dead code:** none added; the second round removed nothing further because the
  first round already cleared the tree (`ruff` F-rules clean).

Deliberately **not** done: extracting the three archive-name validators into a
shared module. They now differ on more axes (canonical portability layout,
generic asset archive, Conference Editor source tree with PHP/suffix rules), so a
merged helper would need flags that hide the differences — the opposite of the
project's "boring, explicit code" preference.

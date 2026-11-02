# Polls pre-release audit

Date: 2026-11-02. Scope: the actual working tree containing the three incremental Polls patches, compared with the tracked original editor. No commit, push, deployment, live data migration/deletion, real-user contact, or email delivery was performed. All executable tests used isolated synthetic runtime data.

## Executive summary and release verdict

**READY_WITH_CONDITIONS for a controlled deployment.** No critical or high software finding remains open within this bounded audit. The restored visual editor and shared-link lifecycle work in both anonymous and identified modes. Existing invitation-based data remains available to authenticated administrators through a read-only archive; its public collection and mutation routes are retired.

Before distributing a link, the administrator must approve the actual controller, contact, purpose, legal basis, recipients/processors, transfers, retention and any DPO information; establish the deletion/backup procedure; and perform the VPS checks below. Local test results do not establish live hosting permissions, proxy logging policy, disk durability/performance or legal compliance.

The initial three-patch tests all passed (14 tests), but missed a real browser submission failure, a published-to-draft schema-lock bypass, and the still-accessible parallel invitation editor. Those issues were reproduced and corrected during this audit.

## Findings and corrections

| ID | Initial severity | Evidence / root cause | Final disposition |
| --- | --- | --- | --- |
| P01 | High | `no-referrer` form navigation produced `Origin: null`; global origin checking rejected genuine browser submissions with HTTP 403. Flask tests without real browser headers missed this. | Only the shared-survey POST accepts an opaque origin, while still requiring the signed cookie-bound CSRF token. Explicit foreign origins, missing tokens and another browser's tokens remain rejected. See `mifp_app/__init__.py:418`; actual browser submissions now pass. |
| P02 | High | Published polls could become drafts, then change questions/mode before answers existed. Erasing all answers also removed the only evidence that collection had started. | Published-to-draft transitions are rejected; closed polls cannot reopen. Schema/mode freeze persists, and a durable `collection_started` flag survives identified erasure. Published question context and privacy are also frozen, protecting already-open participant forms. `services/simple_polls.py:283`. |
| P03 | High | Deletion unlinked the per-poll lock after releasing it: queued readers/writers could hold a stale lock inode or recreate files during removal. | A stable root lock coordinates poll lifetime: normal operations share it, deletion holds it exclusively, with per-poll locking underneath. Directory removal stays inside both locks and syncs the parent directory. Concurrent submit/close/delete and terminated-writer tests pass. `services/simple_polls.py:59`, `:510`. |
| P04 | Medium | The generic public body limit ran before route code, so increasing a limit inside the submission handler was too late; the route's 24 KiB limit was also incompatible with supported long answers. | The survey POST receives a bounded 2 MiB limit before body reading and CSRF parsing, respecting a smaller global ceiling. Other public routes retain their own limit. Oversized, chunked and valid >64 KiB requests are covered. |
| P05 | Medium | Old URLs still exposed a second editor, recipient imports and token-based responses despite navigation pointing to shared polls. Legacy list reads also invoked automatic retention cleanup. | One active editor. Legacy mutation/collection returns 410 after normal authorization/CSRF checks; archive reads and exports remain authenticated. Removed old editor/respond templates, scripts and respondent stylesheet. Removed implicit retention cleanup from archive listing. General Email/Batch email and shared recipient parsing remain intact. |
| P06 | Medium | CSV headers were not formula-escaped, and custom yes/no labels exported raw `yes`/`no`. Privacy version changed on unrelated saves. | Headers are spreadsheet-safe; custom response labels survive CSV export; privacy version remains stable on closure/deadline-only saves. All five question types are exercised through Flask routes. |
| P07 | Medium | JSON dictionaries with missing journal fields passed parsing and failed later with `KeyError`. Internal storage symlinks were followed; a newly recreated journal could inherit broad permissions. | Journal/config shape checks fail closed; internal symlinks are rejected; journal writes enforce mode 0600. Incomplete final records alone are recovered; invalid complete records are never silently removed. Corrupt surveys remain visible as unavailable library entries. |
| P08 | Medium | An IP ceiling of 500 attempts/10 minutes could block a legitimate university network. Application request logging also attached persistent IP hashes to survey traffic. | Coarse volatile limit is now 10,000 attempts/IP/worker/10 minutes, with Retry-After on 429. The 8,000-request same-IP run passed. Survey logs omit IPs, IP fingerprints and admin actor; URL keys are redacted, including malformed suffixes. |
| P09 | Low | Clipboard code used `event.currentTarget` after `await`; controls had ambiguous accessible names; option movement text overflowed icon-sized buttons; preview title inherited an unreadable color. | Capture the clipboard button before awaiting; explicit control names; icon buttons retain accessible labels; narrow editor layout and preview title corrected. Public controls have accessible names and modest standalone typography. Browser checks cover copy and editor operations. |
| P10 | Medium | Preview showed only questions, omitting named fields and the actual privacy notice. Privacy defaults also suggested unverified controller contact/processors. | Added authenticated, non-persisting preview of the real participant template with disabled submission, including unsaved settings/notice. Removed suggested legal contact/recipients; publication validates completeness, contact syntax and dates. |
| P11 | High | A failed journal fsync could leave a complete cached record; its retry returned success merely because that ID/email already existed, without establishing durability. | Both ID and email duplicate paths fsync the journal before acknowledging. An injected-failure regression proves retries continue to fail while journal fsync is unavailable, then recover without a second row. |

References to `mifp_app/` above are under `MIFPAPP/CORE/`. Regression tests are in `TESTS/webapp/test_polls_release.py`, the updated security suite, and `TESTS/browser/test_polls_release.py`.

## Original editor preservation

Compared the tracked original `templates/dashboard/polls.html`, `static/js/dashboard/polls.js`, renderer and dashboard CSS with the restored editor. The original sidebar/library, professional dashboard shell, question cards, five question types, add/edit/duplicate/delete/reorder controls, choice editing/reordering/removal, five starting templates, custom yes/no labels, text styles, required flags and live preview are preserved. The existing `poll-renderer.js` and dashboard design rules remain in use. No frontend framework, CDN, build tool or new dependency was added.

The restored editor is **not a pixel-identical copy**. Email recipient controls are replaced by the shared URL and copy button; privacy configuration has its own tab. Personal-invitation session settings and respondent answer revisions are retired because shared anonymous links do not authenticate a respondent. Results use counts and administrator CSV export; the previous chart/history UI is not carried into new polls. Archived polls retain CSV/XLSX/PDF exports. These are explicit scope differences, not claims that every old invitation feature survives.

Browser verification manipulated the actual cards and options, saved/reloaded them, reviewed the full public preview, published, copied the URL, submitted at a 390px mobile viewport, and saw the administrator response count update in **both modes**. The obsolete UI was removed only after the replacement completed that flow. Screenshots were inspected and stored under `/tmp/mifp-polls-editor-{anonymous,identified}-release.png` and `/tmp/mifp-polls-public-mobile-{anonymous,identified}-release.png`; these synthetic artifacts are not repository source.

## Security, privacy and persistence assessment

- Administration, archive data, analysis and exports require the existing administrator session. Writes retain global CSRF protection; whole-poll deletion and identified erasure additionally verify the administrator password. The endpoint authorization inventory was updated and tested.
- Public access needs no account. Only the supplied poll is rendered; no public results/identity/recipient API exists. The shared URL itself appears in its form action by design; it is not a per-person credential.
- Anonymous journals contain answers, a random **per-submission** retry ID, a UTC date and privacy version. The retry ID is not derived from an email, account, IP, cookie or browser fingerprint and does not link multiple submissions by a respondent. Anonymous identity fields sent by a caller are discarded. Named mode stores only declared first name, surname, unverified email, answers and submission/privacy metadata; consent mode also records consent time.
- A necessary HttpOnly, SameSite CSRF cookie binds public tokens; it is not written into responses. Production enables Secure cookies. Survey pages load only a local stylesheet, with no JavaScript, trackers or unrelated site navigation. Browser network checks found no external page requests.
- Jinja escaping protects public question/notice/answer rendering; the editor renderer uses DOM text APIs. Existing CSP, frame protection and MIME protection remain. Survey success **and error** responses use no-store/no-referrer/noindex. Application access/error/notification paths redact survey keys. Survey visits are excluded from aggregate site metrics.
- Absolute anonymity cannot be promised: respondents may identify themselves in free text; infrastructure can process transport metadata; response order, dates and external knowledge can allow inference. Application logs retain request times/statuses and poll IDs, although their survey IP fingerprints and actor fields are now omitted. Host proxy/error logs and backups require separate operator review.
- JSON/JSONL only under `RUNTIME_CONFIG_DIR/polls_v2/`; no Polls database, queue or worker was added. Directories are created mode 0700, files mode 0600. Path IDs and internal symlinks are checked. Local POSIX `flock`, atomic replacement, flush/fsync and directory fsync protect cooperating processes. Use a local filesystem with these semantics, not an unverified network mount.
- Metadata and answer operations lock consistently. Responses are acknowledged only after journal fsync. A retry with the same random ID does not append again. Named duplicate email is also suppressed; identities are self-declared and this is not authentication. Lost acknowledgements can be retried with the same form; revisiting a shared link creates a new retry ID and cannot ensure one response per person.
- A partial final JSONL record is ignored on read and truncated before the next append. Invalid complete JSON/record shapes fail closed. Abrupt process exit releases the lock; restart preserves acknowledged rows. Metadata replacement failure leaves the previous config in place. This does not claim fault tolerance against broken storage hardware, arbitrary manual edits or every possible power-loss point.
- Identified erasure atomically rewrites the journal; full deletion removes its folder, including interrupted-write temporary files. Neither erases downloaded exports, independent snapshots, off-site backups, SSD history or external copies. Retention cleanup rechecks expiry while holding the deletion lock, avoiding a stale precheck.

The implementation of request limits was checked against [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/); opaque form origins against [Mozilla's Origin documentation](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Origin); atomic replacement and fsync against [Python's OS documentation](https://docs.python.org/3/library/os.html). These support implementation choices, not a claim of certification.

## Test campaign and actual results

The matrix was declared before testing: lifecycle in both modes, original editor interactions, privacy/publication gates, authorization and CSRF, all question types/CSV, idempotency and concurrent routes, crash/restart/recovery, plus one 8,000-response load run. No live server or external recipients were used.

1. Initial patch-specific suite: **14 passed**. This was a baseline, not sufficient release evidence.
2. Final bounded integration/security/contracts/backup suite: **353 passed, 1 skipped**, 23.07s. The skipped test is the opt-in load test, run separately below. During consolidation, retired UI/SMTP expectations and the fail-closed dashboard endpoint/module inventories were updated; no tests were disabled to hide active behavior failures.
3. Browser matrix against isolated real Flask server and headless Chrome, including active CSRF: **2 passed**, 21.83s. Initial browser runs exposed the opaque-origin failure and control naming defects; the final run passed after corrections. Chrome required execution outside the filesystem/network sandbox because its startup socket was denied there.
4. Final focused regressions after log minimization, date normalization and retry-durability corrections: **58 passed, 1 skipped**, 3.37s. These overlap the bounded suite; counts must not be added as distinct tests.
5. Load: **8,000 successful actual Flask POSTs**, four concurrent clients, one poll, same source IP, actual validation/JSONL locking/fsync, no preseeded answers. One required yes/no question, anonymous mode; CSRF disabled for this load run only (real CSRF separately exercised above). **104.269s total; p95 0.115s; maximum 0.200s; summary 0.021s; administrator CSV export 0.025s.** Exactly 8,000 rows and 8,001 CSV records including the header. This is a local Flask-client campaign, not a VPS/Gunicorn/Caddy network benchmark or an 8,000-simultaneous-user guarantee. Per-submit journal scanning remains O(n), with no speculative index/cache added. The load run preceded the final log-minimization and retry-fsync corrections; those were covered by the final focused regressions, and the unique-submission write path was unchanged.
6. Repository hygiene: **passed** (527 tracked files); final `git diff --check`: **passed**.

Reproduce the bounded suite from the repository root:

```bash
.venv/bin/python -m pytest -n 0 \
  TESTS/webapp/test_simple_polls.py TESTS/webapp/test_polls.py \
  TESTS/webapp/test_polls_release.py TESTS/webapp/test_polls_visual_restore.py \
  TESTS/webapp/test_polls_original_editor_03.py \
  TESTS/webapp/test_targeted_security_hardening.py \
  TESTS/webapp/test_csrf.py TESTS/webapp/test_csrf_lazy_cookie.py \
  TESTS/webapp/test_headers.py TESTS/webapp/test_logging.py \
  TESTS/webapp/test_client_ip.py TESTS/webapp/test_dashboard_actions.py \
  TESTS/webapp/test_revamp_contract.py TESTS/webapp/test_architecture_boundaries.py \
  TESTS/webapp/test_docker_compose_contract.py TESTS/webapp/test_vps_config.py \
  TESTS/webapp/test_backup_operator.py -q
.venv/bin/python -m pytest -n 0 TESTS/browser/test_polls_release.py -q
MIFP_POLLS_LOAD_TEST=1 .venv/bin/python -m pytest -n 0 \
  TESTS/webapp/test_polls_release.py::test_shared_link_load_8000 -q -s
python3 tools/check_repo_hygiene.py
git diff --check
```

Do not repeat the load test unless reviewing a relevant change. The full project suite was not run; unrelated scraper/database functionality was not modified.

## Remaining findings and release conditions

| Severity | Remaining issue | Required owner/action |
| --- | --- | --- |
| Medium | Legal/controller/purpose/basis/retention information cannot be established from code. A checked confirmation box does not establish its legal accuracy. | Administrator/legal reviewer must approve each real notice, including self-declared identity and withdrawal/rights procedures. Anonymous consent is deliberately unsupported because individual withdrawal cannot be located reliably. |
| Medium | Retention removal is an explicit command, not an installed scheduler. Backups retain independent copies. Existing legacy data is intentionally not migrated or purged. | Assign an operator and cleanup schedule, verify backup retention and export disposal, and decide separately how long to retain legacy records. |
| Medium | Live VPS state and Caddy/global error logging were not inspected. The versioned Caddy application block has no access-log directive, but this does not prove the installed host logs never record URLs. | Verify installed Caddy, journald, monitoring and backup access; protect/redact survey URLs and transport metadata, including proxy errors. Keep the private tree outside public roots. |
| Low | Rate limiter is volatile and per web worker; it is a coarse abuse limit, not identity or distributed DDoS prevention. | Keep current small worker count; monitor operational resource usage. No new service is required by the tested workload. |
| Low | Full-storage failures produce failure responses; only partial-tail recovery is automatic. Draft save errors redirect and may require re-entering unsaved edits. | Investigate storage failures/corrupt library entries; retain private evidence and restore a verified copy if needed. Never discard complete journal records merely to make parsing succeed. |

## VPS checklist and exact deployment procedure

Repository inspection found these compatible existing settings:

- Image build context remains `MIFPAPP/CORE`; production compose has a GHCR image and **no build section**. The pipeline runs on `main` or manual dispatch. Production deployment remains manual through the installed `deploy/deploy.sh` operator (`mifpctl`).
- `/opt/mifp/data:/app/data`, with `RUNTIME_CONFIG_DIR=/app/data/config`, makes the host Polls tree **`/opt/mifp/data/config/polls_v2`** persistent. Legacy data stays in the adjacent `polls` directory. No Polls-specific environment variables are required.
- Image UID/GID is **10001:10001**, with read-only container root and a writable persistent data mount. The operator validates data ownership and prepares the config parent. Confirm this also holds after restore; private Polls directories/files should be 0700/0600. Generic `fix-permissions` and snapshot restores may broaden modes, so reapply private modes before reopening service if used.
- Only `127.0.0.1:8000` is published. Host Caddy provides HTTPS. Production enables Secure/SameSite session and CSRF cookies and trusts exactly one forwarded hop for address/scheme, not forwarded host. Trusted hosts must match the installed public domain. No private Polls directory is served by Caddy or the event publisher.
- Existing Gunicorn configuration defaults to one gthread worker, four threads, 120s timeout. No new process/service is needed. `/ready` stays host-local; Caddy blocks it externally.
- Existing `backup.sh` pauses application writers and snapshots the `config` tree, so Polls is included. Restore must preserve the entire poll metadata/journal pair and coordinate writers, not copy a live journal piecemeal.

The following commands are **instructions for a later explicitly authorized operator**, not actions performed in this audit. Prerequisites: independent review complete, an approved release already built successfully by CI, existing host tools installed/current, and all conditions above satisfied. Use the full CI commit tag, not an unreviewed floating image.

On the VPS:

```bash
sudo mifpctl config-check
sudo mifpctl status
sudo mifpctl security-check
sudo mifpctl backup
# Substitute the approved complete Git commit hash published by CI:
sudo mifpctl deploy sha-APPROVED_FULL_COMMIT_HASH
sudo mifpctl status
sudo mifpctl doctor
curl --fail --silent --show-error https://mifp.eu/health
```

`mifpctl` is the installed `deploy/deploy.sh`; no database build/import/migration, new volume, host service, production compose build, or root `./mifp` production command is part of this release. If the installed operator is too old, follow the existing [host update procedure](deployment/update-existing-vps.md) before deployment; do not overwrite host secrets/configuration with repository examples.

Before emailing/distributing anything, use the dashboard over HTTPS to create one synthetic anonymous and one synthetic named poll, review the real notice/preview, publish, copy each URL, submit from a separate logged-out browser, inspect/export the result, close, and delete the synthetic polls using administrator confirmation. Check valid and closed pages' cache/referrer headers and ensure the host folder is writable as 10001 and not web-accessible. Verify a backup contains private Polls data without publishing it. Then approve real notices and distribute shared links externally through Aruba.

### Retention and erasure operation

Deletion is not automatic. Retention dates are inclusive UTC dates; cleanup removes polls whose date is earlier than today. After administrator approval of the affected retention records, the existing container can run the supplied cleanup command as the application UID:

```bash
polls_container=$(sudo docker ps \
  --filter label=com.docker.compose.project=mifp \
  --filter label=com.docker.compose.service=web --format '{{.ID}}')
# Require exactly one running web container before proceeding.
sudo docker exec --user 10001:10001 "$polls_container" \
  python -m mifp_app.services.simple_polls /app/data/config
```

This command **deletes expired survey folders**, not backups. Choose an operator cadence that meets the approved notice. There is no pre-existing Polls cleanup timer to assume is installed, and this audit installs none. Individual named erasure and explicit whole-poll deletion are available in the dashboard. Verify backup/export disposal separately. After any full snapshot restore, reconcile erasure requests and expired retention before allowing public access; otherwise a restore can resurrect deleted personal data.

### Rollback

For an application-only problem, preserve current data and use the already recorded previous image:

```bash
sudo mifpctl backup
sudo mifpctl rollback
sudo mifpctl status
sudo mifpctl doctor
```

This release changes no SQLite schema. Image rollback does **not** undo collected Polls data; `polls_v2` remains private on the persistent volume. A pre-shared-link image may not expose new survey URLs, so halt link distribution and tell the operator before rolling back. Do not automatically restore an older data snapshot: it could lose acknowledged answers and resurrect erased data. If recovery truly requires a full snapshot, the existing explicit command is `sudo mifpctl restore-snapshot /var/backups/mifp/snapshots/snapshot-APPROVED_ID`; it restores broader application data and requires a separately approved recovery point and erasure reconciliation. Prefer image rollback with data preserved.

## Changed files / Git diff summary

This working tree already contained uncommitted patch changes before the audit. They were reviewed and consolidated in place; none was committed or staged. The three root `POLLS_*_PATCH_NOTES.md` files are pre-existing user artifacts and remain unchanged. This report supersedes their readiness claims.

| Area | Changed files |
| --- | --- |
| Active backend | `MIFPAPP/CORE/mifp_app/services/simple_polls.py`, `routes/simple_polls.py`, `__init__.py`, `routes/dashboard.py`, `utils/logger.py` |
| Legacy archive | `routes/dashboard_polls.py`, `routes/public_polls.py`, `services/polls.py`, new `templates/dashboard/polls_archive.html` |
| Preserved/restored UI | `templates/dashboard/polls_simple.html`, `static/js/dashboard/polls-simple.js`, `static/css/survey-simple.css`, `static/css/dashboard.css`, `templates/dashboard/layout.html`, `templates/dashboard/_notification_tabs.html` |
| Removed obsolete UI | `templates/dashboard/polls.html`, `templates/public/respond.html`, `static/js/dashboard/polls.js`, `static/js/respond.js`, `static/css/polls-public.css` |
| Tests | `TESTS/webapp/test_simple_polls.py`, `test_polls_original_editor_03.py`, `test_polls_visual_restore.py`, new `test_polls_release.py`, updated `test_polls.py`, `test_targeted_security_hardening.py`, `test_dashboard_actions.py`, `test_revamp_contract.py`, new `TESTS/browser/test_polls_release.py` |
| Report | `docs/POLLS_RELEASE_AUDIT.md` |

Tracked diff against HEAD at the end of consolidation: **18 files changed, 129 insertions, 1,879 deletions**, excluding untracked additions (including this report).

The diff removes the obsolete invitation/editor frontend and writable routes, retains legacy archive/export compatibility, hardens the existing JSONL shared-link implementation, and adds regression/browser evidence. New patch files are still untracked, so plain `git diff --stat` alone omits them; inspect `git status --short` and the new files as well. Scrapers, database runtime data, production Docker/compose, proxy configuration and CI workflows were not changed.

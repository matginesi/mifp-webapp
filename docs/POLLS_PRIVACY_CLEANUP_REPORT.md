# Polls: final canonical privacy / YAML implementation

**Latest verdict: BLOCKED — effective anonymity of the closed-question Winter School survey is not established; publication was not forced.**

## Latest request: closed-question Winter School root-cause verification

This finding supersedes the earlier software-only READY_FOR_CI conclusions below.
The current repository and the user file in Downloads were examined again; the
Downloads YAML is byte-identical to the canonical versioned fixture.

### Concrete obstacle, including closed questions

The anonymous engine saves the complete interest answer and eight-date vector in
one JSONL row. It omits names, email, IP, cookie values and user-agent information,
but retains the retry ID, submission day and notice reference. The journal retains
append order and its file has modification times. The retry ID is per submission,
not an Aruba invitation token, and the application does not keep a recipient map.

A new bounded test uses exactly the Winter School closed-question structure and
two synthetic known schedules. It submits both through the public Flask route,
then discards ALL response metadata and reverses the row order. Matching the
remaining availability vectors to the known schedules still identifies the two
synthetic people uniquely. This is a concrete counterexample to assuming that
closed questions alone establish anonymity; it does not claim actual members
have been reidentified or quantify the actual audience's reidentification risk.
The real audience and outside schedule knowledge have not been established here.

Application access logs omit IP/fingerprints and redact the shared-link secret,
but retain poll-specific timed request events, request sizes and durations.
Gunicorn's extra access log is disabled in the repository and the Caddy template
has no access-log directive. The actual hosting/proxy/security/backup logging and
access arrangements remain unverified; production was not accessed or changed.
They cannot be declared free of reasonably possible correlation from repository
configuration alone. The CSRF cookie remains necessary and outside the answer
journal; removing it would weaken security without solving answer linkage.

Removing the submission day, hashing retry IDs, shuffling records or suppressing
one log would not remove the demonstrated vector-to-schedule match. There is no
small, demonstrated fix that preserves this row-level response model and proves
the requested anonymity. A different collection/release design would need its
own assessment of aggregation, small groups, rare patterns and timing; it is not
implemented or asserted anonymous here. No framework, profile, approval state or
publication exception was introduced.

### Real template test and preserved gate

The canonical Winter School file is imported through the real Flask import and
confirmation routes, saved and reopened as a Draft with all eight dates. The test
uses explicitly synthetic closing/retention dates to isolate the legal error;
these dates were not written to the user file or approved for real operation.
Publish returns HTTP 400 naming `legal_basis, transfers`, with the Draft intact.
There is no final publication capability, published snapshot or share-link UI.
Direct GET/POST to the Draft's internal survey URL returns 404; a forged final
publication request fails. Its JSONL remains empty. A complete anonymous public
submission for this real incomplete notice was intentionally NOT forced.

Separate synthetic, legally complete test definitions exercise successful
anonymous and identified publication, Copy Link, public submission, actual saved
records, YAML round-trip, analytics and exports. They validate functioning code,
not the lawfulness or anonymity of the real MIFP survey.

Outstanding facts for real publication remain:

- The applicable response-processing legal basis, and the specific interest if
  legitimate interests is selected. No basis is chosen from Aruba membership.
- Actual transfers/access/hosting/backup arrangements and any safeguards. Italy
  hosting alone does not justify filling `transfers` with “None”.
- Recipients/processors, currently expressly a draft description, need confirmation.
- Actual closing and manual-deletion dates are still null in the user YAML.

For genuinely anonymous information a GDPR response-processing basis is not
required; the case-specific identifiability assessment is the unresolved condition
([GDPR recital 26](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679),
[CNIL: identification and anonymisation criteria](https://www.cnil.fr/en/sheet-ndeg1-identify-personal-data)).
This report does not turn a nonempty legal field into verified legal information.

### Changes and verification in this request

Only `TESTS/webapp/test_polls_anonymity.py`,
`TESTS/webapp/test_polls_privacy_cleanup.py` and this report changed in this
follow-up. Application code, policy text, the user YAML, existing answers and
publication gates were left unchanged. All seeded records are isolated test data.

Command:

```text
/tmp/mifp-hotfix-py312/bin/python -m pytest -n 0
  TESTS/webapp/test_polls_anonymity.py
  TESTS/webapp/test_polls_privacy_cleanup.py
  TESTS/webapp/test_polls_release.py -q
```

Result: **28 passed, 1 skipped** in 5.27 s. The skip is the existing opt-in load
benchmark. This includes named-mode full lifecycle, CSRF, retry/concurrency,
exports and unchanged historical notices. No full-suite/browser campaign was run.
`git diff --check` and repository hygiene also passed.

**Stopped at BLOCKED as requested.** No commit, push, deploy or real-data changes.

## Follow-up: no-name collection and effective anonymity (3 November 2026)

The response-mode selector is not evidence that data is effectively anonymous.
The GDPR exemption for anonymous information is conditional on respondents no
longer being identifiable using means reasonably likely to be used, including
indirect correlations ([GDPR recital 26](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679),
[EDPB anonymisation overview](https://www.edpb.europa.eu/topics/ai-and-technology/anonymisation-pseudonymisation_en)).
No automatic anonymous exemption or new approval state was added.

Actual code and Flask-request evidence:

- Anonymous JSONL records contain only `answers`, `request_id`, `submitted_at`
  and `privacy_version`. Names/email injected into identity fields are ignored;
  IP, user agent, cookies, CSRF tokens and HTTP request IDs are not attached.
- `request_id` is a random per-form retry identifier. A new visit creates another;
  it is not an individual invitation token or a mailing-list recipient reference.
  It remains visible to the submitting browser and in its saved answer record.
- New anonymous responses already store only the UTC calendar day, not the exact
  time. JSONL append order and file modification times still reveal sequencing.
  Historical records are not rewritten or scrubbed by this change.
- Free text is preserved verbatim (within existing bounds) and may explicitly
  name a person. Distinctive availability patterns or question contents may also
  identify someone using outside knowledge, especially in small groups.
- A necessary browser-bound CSRF cookie protects POSTs and is not saved with
  answers. It is a security mechanism, not proof of unlinkability.
- IP-based throttling uses a volatile per-worker bucket without an answer link.
  Application survey logs omit client IP/fingerprints/admin actor and redact the
  URL key. Logs still contain timed poll-specific requests and sizes/durations.
  Their HTTP request ID is different from the answer's retry ID.
- Versioned Gunicorn configuration disables its additional access log; the Caddy
  template has no access-log directive. Actual VPS/proxy/hosting/backup logging
  was not inspected or changed. No claim of absence of those logs is made.

The current row-level, text-capable model therefore cannot establish a blanket
anonymous-data exemption. Publication keeps applicable disclosure validation for
both modes; the missing-fields error now explains why no-name mode alone cannot
bypass it. Genuinely anonymous information does not need a response-processing
GDPR basis. Establishing that condition requires assessment of the actual
questions, audience and technical correlations; it cannot be inferred from Aruba
distribution or a selector. No fictitious legal basis was supplied to unblock
Winter School publication. Its existing YAML remains importable/saveable.

Changes in this follow-up:

- `services/simple_polls.py`: new Blank/built-in-template Drafts prefill only
  administrator-confirmed MIFP controller/address/contact. YAML facts and existing
  polls are not replaced; basis/recipients/transfers are never inferred. New
  publications snapshot notice renderer version 2. Missing anonymous disclosures
  explain the correlation limitation; Draft saving still accepts incompleteness.
- `routes/simple_polls.py`: stale-review error says notice, not obsolete profile.
- `templates/dashboard/polls_simple.html` and `static/js/dashboard/polls-simple.js`:
  mode-specific explanation, site-policy link, clearer no-name label. Existing
  legal fields remain in the single collapsible section because exemption is not
  established. No profiles, generic consent boxes or new approval states.
- New `templates/public/_poll_privacy_notice.html` selects snapshot rendering;
  new `_poll_privacy_notice_v2.html` describes metadata/correlation and separates
  Aruba mailing-list processing from response processing. The version-1 renderer
  is untouched. `survey.html` and `polls_publish_review.html` use that selector,
  as does the saved editor preview.
- `Privacy.md` and `docs/polls-yaml.md`: precise mode limits and separate treatments.
  These are source edits; a DB-managed policy body still requires existing content
  maintenance, and no live page was deployed.
- New `TESTS/webapp/test_polls_anonymity.py`: actual CSRF-protected requests,
  identity injection, cookies, logs, metadata, retry behavior, free-text and row
  correlation evidence, no legal bypass, defaults and unchanged old snapshots.
  Existing browser privacy matrix checks mode switching and final notice parity.
  The analytics test now checks the more precise no-name notice wording.

Verification for this follow-up (isolated data only):

- Focused Polls/YAML/date/concurrency/analytics/workflow/privacy/policy/release
  matrix: **116 passed, 1 skipped** (existing opt-in load benchmark), 11.19 s.
- After final wording and stale-review-message adjustment: **53 passed** in
  4.47 s (overlapping focused matrix).
- Actual browser Blank / old Draft / YAML, anonymous and identified, edit/save/
  reopen, single review/publication, public response, analytics and round-trip:
  **3 passed**. The final wording rerun also passed all three.
- `git diff --check` and repository hygiene passed.

No real responses were modified or deleted. JSON/JSONL, the original visual editor,
YAML, analytics, concurrency, idempotency and all existing question types remain.
No commit, push or deployment. Software is ready for CI; removing the response
legal-basis requirement is **not justified by the anonymity evidence**.

## Cause and final model

The previous worktree still had an organisation settings file, implicit inheritance,
read-only historic/custom notices, per-poll adoption rules, and a save operation
that wrote both survey and shared settings. Full YAML disclosures were accepted
but treated as historic overrides; ordinary saves edited only purpose/additional
text and could ignore the imported legal facts. This contradicted a complete YAML
survey definition and made the workflow depend on another configuration layer.

There is now one per-poll `privacy` object for YAML, editor, validation, JSON
storage and notice generation. Required publication fields are `controller`,
`contact`, `purpose`, `legal_basis`, `recipients`, `transfers`; optional fields are
`legitimate_interest`, `dpo`, `additional_disclosures`. An interest explanation is
required when that basis is selected. The complete schema and limits are in
[polls-yaml.md](polls-yaml.md).

Removed active mechanisms: shared organisation getter/writer, inheritance resolver,
custom-mode detection/validation branches, organisation revision dependencies,
profile/custom configuration handlers, selectors and their mutation inventory
entry. No shared settings file is created or changed by current Polls. The old
`poll_fixtures.py` shared-profile test helper was removed. Historical documentation
for that model was moved to ignored `docs/local/reports/polls/`; the obsolete
`POLLS_ORGANISATION_PRIVACY.md` wrapper was removed and its links consolidated.

## Save, preview and publication

All privacy fields imported by YAML appear prefilled in **Privacy details** in the
original editor and can be edited directly. Save Draft atomically stores valid
work in the poll's JSON file without requiring legal completeness or approval.
There is no second organisation-file write, settings page or approval gate.

Publish saves progress, validates questions, mode, title, schedules, retention and
the current privacy object, then renders the complete notice using the participant
renderer. **Publish poll** is the sole explicit administrator publication
confirmation. Its signed, expiring capability is session/poll-bound; it is not a
persisted approval state. Changed Drafts, invalid signatures and missing CSRF are
rejected. Final confirmation revalidates completeness under the poll lock and
stores the immutable notice snapshot. The public URL and Copy Public Link appear
immediately afterwards. The application cannot establish the accuracy of legal
facts merely from nonempty strings; the administrator reviews their applicability.

Validation errors name missing/invalid privacy fields and retain editor values.
An incomplete publication attempt reports that the Draft was saved, rather than
claiming the save failed. The real Winter School file specifically reports
`legal_basis` and `transfers` once its schedules are completed; legitimate-interest
details are required only if that basis is selected. Missing data cannot be
approved by flags in YAML or POST bodies.

## YAML and existing data

Schema remains version 1 with safe_load, byte/event/depth limits, no aliases,
anchors, explicit tags, duplicate keys or unknown fields. All six question types,
Date Availability categories, options, schedules and complete privacy text survive
semantic export/reimport. Null schedules are valid Draft inputs. Import always
creates a new Draft and fresh IDs; it never imports approval, tokens, responses or
identities. Canonical output has no profile or custom-mode field. Historic source
metadata is ignored at the boundary; old boolean approval flags are discarded.

Existing published JSON, notice snapshots and answer journals are not migrated.
Their original notice and legal basis remain frozen. Drafts normalize the same
canonical object on explicit saves. An old custom Draft can explicitly adopt its
existing facts. A small read-only recovery helper can read old organisation facts
for a purpose-only Draft, only when the administrator explicitly selects adoption;
this is not used by new-poll creation, import, preview, validation or publishing.
No registry, profile manager or new configuration hierarchy was introduced.

JSONL locking, concurrent submissions, idempotency, anonymous identity omission,
admin-only reporting, CSRF/XSS protection, the original visual cards, options,
duplication, reorder, templates, live preview, analytics and CSV/XLSX/PDF exports
remain intact. External Aruba distribution does not authenticate membership.
Italy-based storage alone does not prove absence of international transfers.
Deletion remains manual, with separate handling for downloads/exports/backups.

## Winter School facts and public policy

The administrator supplied controller name, registered office and contact, matching
[the public MIFP policy](https://mifp.eu/privacy), which was fetched read-only.
They are now in the canonical fixture and the user file in `~/Scaricati`. The
controller string includes the registered office without adding another schema.
Purpose is preliminary Winter School interest/availability, mode anonymous.
Legal basis, any legitimate interest and transfers remain unconfirmed and blank.
Recipients are proposed administrators/hosting/backup providers, expressly a draft
for review. No DPO appointment, legal basis or absence of transfers was invented.
The real file is importable and saveable; it is intentionally not publishable yet.

`MIFPAPP/CORE/Privacy.md` now describes `/survey/...` alongside `/join`, the two
response modes, purpose-specific legal bases, external invitations, unrestricted
shared URLs, snapshots, manual retention, processors/access/backups and transfers.
Cookie policy Markdown and HTML fallback describe Polls CSRF protection separately
from processing consent. Their revision date is 3 November 2026. The policy no
longer claims `/join` is the only public collection form.

These are repository edits, not a live-site update. The public renderer gives a
DB-managed institutional page priority over packaged Markdown. If the installation
has a stored Privacy Policy body, its owner must apply the updated text through
the existing institutional content editor; no real database or VPS data was changed
or overridden here. This is site-policy maintenance, not a Polls approval gate.

## Files

Production code/UI:

- `MIFPAPP/CORE/mifp_app/services/simple_polls.py`
- `MIFPAPP/CORE/mifp_app/services/poll_yaml.py`
- `MIFPAPP/CORE/mifp_app/routes/simple_polls.py`
- `MIFPAPP/CORE/mifp_app/templates/dashboard/polls_simple.html`

Public policies:

- `MIFPAPP/CORE/Privacy.md`
- `MIFPAPP/CORE/cookie-policy.md`
- `MIFPAPP/CORE/mifp_app/templates/public/cookie_policy.html`
- `docs/PRIVACY_DECISIONS_REQUIRED.md`

Tests / synthetic fixtures:

- `TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml`
- `TESTS/webapp/test_polls_privacy_cleanup.py` (canonical-object matrix)
- `TESTS/webapp/test_simple_polls.py`
- `TESTS/webapp/test_polls.py`
- `TESTS/webapp/test_polls_release.py`
- `TESTS/webapp/test_polls_workflow.py`
- `TESTS/webapp/test_targeted_security_hardening.py`
- `TESTS/webapp/test_dashboard_actions.py` (removed endpoint inventory entry)
- `TESTS/webapp/test_poll_yaml_end_to_end.py`
- `TESTS/browser/conftest.py` (isolated runtime fixture for old-Draft seeding)
- `TESTS/browser/poll_helpers.py`
- `TESTS/browser/test_polls_privacy_cleanup.py`
- `TESTS/browser/test_polls_release.py`
- `TESTS/browser/test_poll_yaml_dates.py`
- `TESTS/browser/test_poll_yaml_end_to_end.py`
- `TESTS/browser/test_polls_privacy_analytics.py`
- Deleted `TESTS/webapp/poll_fixtures.py`

Documentation:

- `README.md`, `docs/INDEX.md`, `docs/polls-yaml.md`, this report
- Deleted obsolete `docs/POLLS_ORGANISATION_PRIVACY.md`

The original legacy supplied fixture is preserved. The user YAML was updated at
`~/Scaricati/MIFP_Winter_School_2027_Poll.yaml`, preserving questions, dates and
schedules. No production answers, unrelated runtime modules, SMTP, Docker, VPS or
CI/CD configuration was changed by this task.

## Verification

Bounded checks only; no full-suite or large benchmark campaign:

- Focused Python 3.12 Polls/YAML/analytics/release/workflow/policy and endpoint
  inventory matrix: **121 passed, 1 skipped** in 11.70 s. The skip is the existing
  opt-in load benchmark.
- Final canonical privacy matrix with precise field-length feedback: **10 passed**
  in 2.04 s; overlaps the broader matrix.
- Actual browser: new anonymous poll, synthetic old custom Draft and full YAML
  (identified mode), edit/save/refresh, complete notice review, publication,
  public responses, Date Availability, analytics and export/reimport:
  **3 passed** in 32.11 s.
- Existing original-editor and exact YAML browser regressions:
  **4 passed** in 29.51 s, including options, duplication/reorder and copy link.
- Native tests compare reviewed and participant notices directly, verify XSS
  escaping, missing legal details, response retries, immutable published notices,
  unchanged response journals, all report formats and stale-review/CSRF rejection.
- Public `/privacy` Flask request verifies the new Polls explanation and absence
  of the old only-public-form claim. Existing policy contracts also pass.
- `git diff --check`, repository hygiene and removed-helper/reference checks:
  passed. Only the explicit old-facts recovery path reads the previous settings
  filename; no active profile getter/writer or route remains.

No commit, push, deployment, approval of real legal facts or deletion of real data
was performed.

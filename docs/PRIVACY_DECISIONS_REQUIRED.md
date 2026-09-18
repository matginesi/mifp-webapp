# Privacy decisions required

Internal working document. **Not** published on the public website.

Everything in `Privacy.md` and `cookie-policy.md` that could be verified from the
source code has been corrected to match the implementation. What remains below
**cannot** be established from the repository and must be decided and evidenced by
the MIFP controller (with legal advice where appropriate). Do not invent answers
for these items, and do not present them as settled in the public policies.

Status legend: **OPEN** — no decision recorded · **DECIDED** — decision recorded
here with its evidence.

---

## 1. Legal basis for membership applications — DECIDED IN POLICY, CONFIRM OPERATIONALLY

`Privacy.md` §6 now states Article 6(1)(b) GDPR for reviewing a membership
application and administering the resulting membership relationship. This matches
the public form: the applicant expressly asks MIFP to process the submitted details
to assess the request, and the application contains no consent checkbox or separate
optional purpose.

The controller should still confirm and evidence that this is its intended basis,
that the membership relationship has the necessary contractual character, and that
the stated retention schedule follows from it. If a genuinely optional purpose is
introduced later, document its distinct legal basis and add a consent control only
where consent is actually appropriate.

## 2. Historical and imported records — OPEN

`Privacy.md` §2 and §7 describe a legitimate-interest basis for historical and
imported institutional data, and §8 notes that erasure requests may be assessed
per record. Decide and record:

- the documented balancing test for that legitimate interest;
- how a data-subject request affecting an imported record is handled, including
  who decides between rectification, restriction and deletion;
- whether activity-specific notice (for example on archive pages) is needed for
  people named in historical records.

The repository cannot answer these questions, and deleting the provenance tables
(`source_records`, `import_records`, `raw` payloads) is **not** an acceptable
substitute: they are what makes the archive verifiable, and they may themselves
contain personal data. See §6 below.

## 3. Hosting / VPS processor — OPEN

The public policy refers to "hosting provider" without naming it. Decide and
record the provider, the region, the processing role and the signed data
processing agreement. `docs/DEPLOY_NEW_VPS.md` describes the deployment topology
but not the commercial relationship.

## 4. SMTP / e-mail processor — OPEN

Membership applications trigger a notification e-mail when `MAIL_PROVIDER` is
configured. The notification contains the applicant's name, e-mail address,
affiliation, country, field and free-text motivation, and sets the applicant's
address as `Reply-To`. Decide and record the SMTP provider, its region, and the
signed agreement. If the provider is outside the EEA, record the transfer
safeguard.

## 5. Backup / off-site storage processor — OPEN

If `RESTIC_*` off-site backup is configured, personal data (including the full
database) is replicated to that destination. Decide and record the provider,
region, encryption-at-rest posture, agreement status and transfer safeguard. If
no off-site destination is configured, record that fact instead.

## 6. Data processing agreements (DPAs) — OPEN

`Privacy.md` §9 deliberately says only that a transfer safeguard *will be* put in
place, and no longer claims that DPAs exist. Verify and record the DPA status for
every processor in items 3–5 before the deployment is treated as final.

## 7. Extra-EEA transfer assessment — OPEN

Required only if any processor in items 3–5 is outside the EEA. If so, record the
transfer mechanism (adequacy decision, standard contractual clauses) and the
transfer impact assessment.

## 8. Retention of import provenance — OPEN

The application stores import provenance (`source_records`, `import_records`,
`import_runs`, raw payloads, `entity_links` of role `source`). This is what makes
the archive auditable and reproducible, but it can contain personal data that is
also present in the canonical record — and, when a canonical record is removed,
may be the only remaining copy.

Decide and record:

- whether provenance rows carry a retention period of their own, and if so how it
  interacts with archive integrity;
- how a data-subject erasure request is applied to provenance rows;
- whether any accidental full-record copies into provenance can be avoided
  without losing the ability to verify an import.

**No broad provenance deletion was performed in this cleanup**, precisely because
that decision has data-integrity consequences beyond privacy.

## 9. Conference packages loaded in the future — OPEN

Uploaded Conference Editor packages are stored and published as immutable source
snapshots. MIFP does not inspect or rewrite them, so a package could contain its
own analytics, cookies or tracking code that is not covered by this repository's
policies.

Decide and record:

- the review step required before a third-party conference package is published
  on `events.mifp.eu`;
- whether a package privacy/consent declaration must be supplied with it.

The internal/legacy builder no longer makes any client-storage claim: the notice
that said the generated site stored theme and privacy preferences was false and
has been removed together with the unused `notice_storage_key` and
`remember_theme` settings.

## 10. Age / minors — OPEN

The membership application form does not ask for a date of birth and does not
implement any age check. Decide whether an age threshold statement is needed.

## 11. Retention schedule owner — OPEN

The retention periods in `Privacy.md` §7 are now technically accurate: technical
log retention and closed membership-application retention are applied by the
protected maintenance cleanup, which an operator runs deliberately. Confirm:

- who is responsible for running the maintenance cleanup and how often;
- whether the default 730-day application retention and 30-day log retention are
  the periods MIFP actually wants;
- whether an automatic schedule is required, in which case the policy wording
  must be updated to describe real automatic deletion rather than a manual
  procedure.

---

## Operational action required — the corrected policies must actually be published

**Status: OPEN — action on the deployment database.**

`Privacy.md` and `cookie-policy.md` were corrected in the repository, but a
policy page can also live in the database (`pages` table, types `privacy` and
`cookie_policy`). `_page_or_md_html()` in `routes/public.py` prefers the database
row and only falls back to the packaged Markdown:

```python
if page and page.get("body"):
    return _markdown_html(page.get("body"))
html, _ = _render_md(fallback_filename)   # Privacy.md / cookie-policy.md
```

Verified on the current local database: a published `pages` row
(`slug='privacy-policy'`, `type='privacy'`, ~36 kB) still shadows the corrected
`Privacy.md`, so `/privacy` serves that older text while `/cookie-policy` —
which has no database row — already serves the new Markdown. Because `page` is a
portable type, that row also travels inside data-portability bundles, so a
restore can reintroduce it.

Action, after deploying this change:

1. open `Dashboard → Privacy & Policies`;
2. paste the current `Privacy.md` into the Privacy Policy tab and save (same for
   `cookie-policy.md` if a `cookie_policy` row ever appears);
3. reload `/privacy` and `/cookie-policy` and confirm the text matches the
   repository files;
4. optionally run `SELECT slug, type, length(body) FROM pages WHERE type IN
   ('privacy','cookie_policy')` to see which pages are database-managed.

No database row was rewritten or deleted by the cleanup, because the database is
runtime data and a rewrite would not propagate to production.

## Not open — verified in this cleanup

Recorded here so the same questions are not re-opened without cause:

| Claim | Verified reality |
|-------|------------------|
| Password hashing | PBKDF2-HMAC-SHA256, 600,000 iterations, `pbkdf2:sha256` format generated by `MIFPAPP/CORE/manage.py` and verified by Werkzeug. **Not bcrypt.** |
| Cookie names | `mifp_admin_session` (administrator, browser-session cookie, server-side limit 8 h) and `mifp_csrf` (strictly necessary, only on pages rendering a CSRF-protected form, up to 2 h). |
| Cookies on ordinary public pages | None. The public base template no longer renders a global CSRF token. |
| Cookie notice | Present again, **informational only**: it lists the two strictly necessary cookies, links to the Cookie Policy, and collects no consent. Dismissing it stores nothing (no cookie, no localStorage, no server-side record), so the policy must never describe it as remembered. |
| Contact form / event registration | Do not exist in this application; the policy no longer describes them. |
| localStorage | Only `mifp-dashboard-sidebar-collapsed`, dashboard-only UI preference. The public site stores nothing. |
| Raw IP storage for applications | Disabled by default (`JOIN_STORE_RAW_IP=0`); IP addresses are still processed transiently for rate limiting. |
| Manual Event authoring | Removed. Canonical events are created only by ingestion pipelines; `POST /dashboard/events` is update-only. |

# MIFP Polls — Shared-link patch 001 (test candidate)

This is an **isolated first patch**, not a production-certified replacement. The prior Polls storage and legacy UI remain on disk for compatibility; the dashboard sidebar and Notifications tab now open the new shared-link Polls page. The legacy *single invitation SMTP endpoint* returns HTTP 410 to prevent accidental sends. Aruba mailing-list administration is external to MIFP.

## Functionality

- Per-poll **Anonymous** (default) or **Identified** (first name, surname, unverified email) mode; mode and questions frozen on publication.
- Single shared unguessable URL for Aruba mailing-list emails, no MIFP SMTP sending, imported recipient database or recipient list.
- Lightweight HTML form, no JavaScript or external assets required to answer; date, yes/no, choices, text; GDPR notice displayed directly before submission.
- JSON/JSONL storage in `RUNTIME_CONFIG_DIR/polls_v2/<poll-id>/`, private Unix permissions, locking, durable append, safe replay of the same submission ID and recovery of a truncated final JSONL record.
- Admin-only overview, CSV export, erasure of one identified response, complete deletion.
- Configurable GDPR notice with admin confirmation gate: controller's verified legal name, contact, specific purpose, appropriate legal basis, recipients/processors, international transfers/safeguards, legitimate interest description (if applicable), optional DPO, date of retention; separate explicit checkbox when identified processing uses consent.
- Optional CLI cleanup for expired polls. No analytics or tracking on the standalone public form; a technical CSRF cookie is used by MIFP.

## Important limitations / deployment blockers

1. **Test status:** 8 service tests pass, 1 Flask integration test is skipped in the current artifact environment because Flask/Werkzeug are unavailable. Run the HTTP/web test suite and browser smoke tests locally before deployment.
2. **Legal content:** The notice is a configurable template, *not a legal certification*. An authorised MIFP representative must confirm the correct legal controller, lawful basis, legitimate interest where relevant, provider/processor and international-transfer details, retention and any additional required disclosures. Verify or update the site's full Privacy Policy. The mailing-list activity in Aruba is a separate processing activity and requires its own proper information/legal basis.
3. **True anonymity:** No names/emails/IPs are stored *with* anonymous survey answers. However, free-text answers may contain identifying content and reverse proxies may keep IP access logs. Review access logs and backups before describing the processing as anonymous in a strict GDPR sense. Anonymous responses are stored with date precision only.
4. **One person, one response is NOT guaranteed:** A common URL cannot authenticate people. The hidden submission ID prevents a duplicate from re-posting the *same* form; a new visit can create another response. A nominative email is self-declared and cannot establish identity.
5. **Consent withdrawals in anonymous mode:** This version deliberately blocks selecting consent as the legal basis for anonymous polls because individual withdrawal/erasure is not possible. An appropriate legal basis must be assessed before publishing.
6. **Retention:** `retention_until` is an administrative commitment, not magic. Schedule a **daily** invocation of the cleanup command in the production runtime or manually monitor and delete at expiry. Backups and infrastructure logs are separate and must have their own documented retention. Confirm this before public use.
7. **Legacy code cleanup:** The old Polls mail frontend/services are not deleted in this first patch. Remove only after verifying that no saved polls/responses need migration. Their admin sending endpoint is already disabled by this patch.
8. **Abuse prevention:** The new public POST has a generous per-process in-memory throttle (500 attempts/IP/10 min). It is not a globally coordinated anti-bot solution, and institutional NAT traffic should be tested.
9. **No live mail was sent.** Real Aruba limits, list capacity and delivery rules have not been verified by this patch; MIFP does not send campaign mail anymore.
10. **CSV export:** This first version provides CSV only, not Excel/PDF. Reassess whether those are necessary before removing old services.

## Install into your Git working tree

From the MIFP repository root, keep a clean Git worktree (or backup any uncommitted changes) and extract the overlay ZIP (do **not** extract it into the deployment host data directory):

```bash
unzip -o ~/Scaricati/MIFP_Polls_SharedLink_PATCH_001.zip
```

The ZIP contains repo-relative paths (e.g. `MIFPAPP/CORE/mifp_app/services/simple_polls.py`) and new tests. Use VS Code to inspect the diff, commit and push after tests. **Do not deploy as-is without validating the real Flask app and the privacy notice.**

Run the focused service tests:

```bash
python -m pytest -q -o addopts='' TESTS/webapp/test_simple_polls.py
```

Run normal repository webapp tests in your configured `.venv` and browser smoke tests before production. Replace `/path/to/runtime-config` below with `RUNTIME_CONFIG_DIR` inside the running container and schedule this daily:

```bash
python -m mifp_app.services.simple_polls /path/to/runtime-config
```

The only new storage directory is `RUNTIME_CONFIG_DIR/polls_v2/`. The original `RUNTIME_CONFIG_DIR/polls/` remains untouched and is **not** deleted automatically.

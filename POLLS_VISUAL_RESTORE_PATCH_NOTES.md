# MIFP Polls — Visual Restore Patch 002

**Dependency:** Apply after `MIFP_Polls_SharedLink_PATCH_001.zip`. This overlay is **not** the old SMTP Polls application.

## Scope

- Restores the original **visual question cards**: add, delete, duplicate, move, question type, required, and per-option controls (add, remove, reorder). It also restores custom Yes/No labels and single-line vs multiline text responses, including backend saving and public rendering.
- Restores live questionnaire preview using MIFP's existing `poll-renderer.js` and dashboard CSS, while retaining the current simple shared-link/JSONL backend, privacy fields, anonymous/identified modes, CSV export and erasure operations.
- Includes a no-JavaScript fallback: the original server-rendered question controls remain available if the visual editor fails to load.
- Once a poll is published, the UI locks the questions to match the existing backend behavior. Creating or opening polls still uses the **new** routes; no SMTP or recipient list handling is reintroduced.
- **Privacy security fix**: redact public survey access keys in application access/error logs and application-error notifications; also exclude `/survey/` from general public-site analytics to prevent key-bearing paths becoming metric keys. The current Caddy configuration does not enable access logs, but verify independently on the VPS.

## Files changed

- `MIFPAPP/CORE/mifp_app/templates/dashboard/polls_simple.html`
- `MIFPAPP/CORE/mifp_app/static/js/dashboard/polls-simple.js`
- `MIFPAPP/CORE/mifp_app/static/css/dashboard.css`
- `MIFPAPP/CORE/mifp_app/services/simple_polls.py`
- `MIFPAPP/CORE/mifp_app/routes/simple_polls.py`
- `MIFPAPP/CORE/mifp_app/templates/public/survey.html`
- `MIFPAPP/CORE/mifp_app/utils/logger.py`
- `MIFPAPP/CORE/mifp_app/__init__.py`
- `TESTS/webapp/test_polls_visual_restore.py` (new)

## Tests / limitations

- Jinja template parsing and JavaScript syntax passed. Direct service persistence test passed for custom labels, text response style and stable question IDs across repeated saves.
- Playwright/Chromium static integration smoke test passed: visual question cards, preview, add/duplicate/reorder, and serialization of the rendered questions into backend form fields. No browser runtime errors were found in the synthetic fixture.
- The actual authenticated Flask/CSRF flow and full MIFP integration still **must be tested in the local app**. This environment does not have Flask/Werkzeug installed. No live email or public data processing was performed.

## Local install (repository root)

1. Back up/commit any unrelated changes first. Review the overlay in VS Code.
2. Extract the downloaded ZIP in the repository root:

```bash
unzip -o ~/Scaricati/MIFP_Polls_VisualRestore_PATCH_002.zip
```

3. Run your project's usual local app, then create a **draft** poll. Check creating, duplicating, reordering, choice options, preview and saving. Refresh and verify the questions persist. Then complete the privacy notice and test publication, public anonymous/identified response, and CSV export.
4. Run targeted tests:

```bash
python -m pytest -q -o addopts='' TESTS/webapp/test_simple_polls.py TESTS/webapp/test_polls_visual_restore.py
```

5. Do not deploy on the VPS before reviewing the privacy notice, public browser flow, logging and restore behavior on the **real** installed runtime.

## VPS requirements when ready

No new service, worker, port, database or SMTP configuration is needed by the shared-link Polls backend.

Production Compose already mounts `${MIFP_DATA_DIR:-/opt/mifp/data}:/app/data` and sets `RUNTIME_CONFIG_DIR=/app/data/config`, so data resides under `/opt/mifp/data/config/polls_v2/` on the VPS. Existing point-in-time snapshots include the `config/` tree. Confirm permissions, disk space, backup retention and off-site copies. Deleting the live poll folder does not remove earlier backups.

One **pending deployment operation** is a daily retention-cleanup job, preferably integrated into the existing `mifpctl`/systemd host maintenance rather than hand-created ad-hoc containers. The application provides `python -m mifp_app.services.simple_polls /app/data/config` in its container, but automatic scheduling is not bundled with this UI restore patch. Do not claim expired survey data is automatically purged until the timer has been created and verified.

Check that Caddy/reverse-proxy and external monitoring access logs do not record full `/survey/<id>/<key>` paths; the application logger now masks them, but third-party logs are independent. Do not disable admin SMTP secrets needed by other MIFP features, monitor alerts or registration forms merely because Polls no longer sends email.

The *legacy* poll files and pre-existing polls have **not been migrated or deleted**. They remain separate from `polls_v2` until an explicit migration/cleanup decision is made, because the old personal-link/SMTP semantics differ from new shared links.

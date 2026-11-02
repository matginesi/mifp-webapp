# MIFP Polls — PATCH 003: Original editor layout

Requires **PATCH 001** and **PATCH 002** already applied. This patch does not migrate or delete any poll data.

## Why

PATCH 002 restored basic visual question editing but did not restore the original dashboard experience. PATCH 003 restores the original Polls layout and question editor presentation while retaining the shared Aruba link, JSON/JSONL storage, and anonymous/identified modes.

## Changes

- Original poll library/selector sidebar, editor header, five-section tab bar, settings panel, visual question cards (up/down/duplicate/delete, option controls), and full-width respondent preview.
- Five original template starting points (Blank, Attendance, Event availability, Choose a date, Choose a location) without the legacy SMTP feature.
- Aruba link is copied from a small 'Share poll' panel, not from a recipient/invitation system.
- Privacy notice editor is a dedicated tab. Publishing still fails closed if the required information is not completed and confirmed.
- Responses, CSV export and data-management tabs stay admin-only.
- Does not change public survey routes, response storage, log redaction, or SMTP settings used by other MIFP features.
- Removes no legacy Polls files: this is a focused UI restoration, not final cleanup.

## Files included

1. `MIFPAPP/CORE/mifp_app/templates/dashboard/polls_simple.html`
2. `MIFPAPP/CORE/mifp_app/static/js/dashboard/polls-simple.js`
3. `MIFPAPP/CORE/mifp_app/static/css/dashboard.css`
4. `MIFPAPP/CORE/mifp_app/services/simple_polls.py`
5. `MIFPAPP/CORE/mifp_app/routes/simple_polls.py`
6. `TESTS/webapp/test_polls_original_editor_03.py` (new)

## Installation

Extract in the root of the MIFP repository after PATCH 002:

```bash
unzip -o ~/Scaricati/MIFP_Polls_OriginalEditor_PATCH_003.zip
```

Use VS Code Source Control to inspect the changes before committing. Run the local server (`./mifp local`) and verify poll creation from templates, changing question types, rearranging questions/options, live preview, saving and reloading, Privacy tab, anonymous submission and CSV export. **Do not publish to the VPS on this UI patch alone.**

Suggested targeted regression:

```bash
python -m pytest -q -o addopts='' TESTS/webapp/test_simple_polls.py TESTS/webapp/test_polls_visual_restore.py TESTS/webapp/test_polls_original_editor_03.py
```

## Tests performed here

- Jinja2 template compilation and JavaScript syntax validation: PASS.
- Service-level template creation for five presets: PASS.
- Chromium synthetic fixture: original layout, tab navigation, add/duplicate/reorder, live preview and POST serialization: PASS; no JS exceptions.
- Targeted template/starting-point checks: PASS.

Not verified in this environment: authenticated Flask/CSRF end-to-end and VPS behavior (Flask/Werkzeug not installed). This patch leaves all previous privacy/deployment verification needs in place.

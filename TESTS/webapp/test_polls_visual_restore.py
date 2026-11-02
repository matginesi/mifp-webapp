"""Regression checks for shared-link Polls UI and private URL logging.

Keep this small: the HTML/JS interaction is also smoke-tested in a browser.
"""
from pathlib import Path

from mifp_app.utils.logger import redact, safe_survey_path

ROOT = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app"


def test_shared_survey_link_key_is_removed_from_log_values():
    poll_id = "a" * 32
    key = "Abc_123-xYz" + "Q" * 21
    assert len(key) == 32
    url = f"https://mifp.eu/survey/{poll_id}/{key}"
    scrubbed = safe_survey_path(f"/survey/{poll_id}/{key}")
    assert key not in scrubbed
    assert poll_id in scrubbed
    assert key not in redact(f"Failed request to {url}")
    assert "[REDACTED_LINK]" in redact(url)


def test_visual_question_editor_is_bound_to_new_jsonl_form_only():
    html = (ROOT / "templates/dashboard/polls_simple.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/dashboard/polls-simple.js").read_text(encoding="utf-8")
    assert 'id="survey-editor-form"' in html
    assert 'id="survey-visual-preview"' in html
    assert 'id="survey-visual-questions"' in html
    assert 'id="survey-admin-questions"' in html  # no-JS fallback
    assert 'js/poll-renderer.js' in html
    assert "Move up" in js and "Duplicate" in js and "Add option" in js
    assert "serialize()" in js and "_options" in js
    assert "data-poll-invite" not in html and "MIFPRecipientFiles" not in js


def test_legacy_question_presentation_settings_are_preserved(tmp_path):
    from mifp_app.services import simple_polls as polls

    poll = polls.create(tmp_path)
    draft = polls.update(tmp_path, poll['id'], {
        'title': 'Availability', 'status': 'draft', 'response_mode': 'anonymous',
        'deadline': '2027-03-01', 'retention_until': '2027-04-01',
        'questions': [
            {'type': 'yes_no', 'question': 'Will you attend?', 'required': True,
             'yes_label': 'Available', 'no_label': 'Unavailable'},
            {'type': 'text', 'question': 'Notes', 'required': False, 'style': 'multi_line'},
        ],
        'privacy': poll['privacy'],
    })
    assert draft['questions'][0]['yes_label'] == 'Available'
    assert draft['questions'][0]['no_label'] == 'Unavailable'
    assert draft['questions'][1]['style'] == 'multi_line'
    # Reloading the already-normalized question records through a form-like
    # payload must not destroy labels or reassign stable question identities.
    saved = polls.update(tmp_path, poll['id'], {
        'title': 'Availability', 'status': 'draft', 'response_mode': 'anonymous',
        'deadline': '2027-03-01', 'retention_until': '2027-04-01',
        'questions': [
            {'type': 'yes_no', 'question': 'Will you attend?', 'required': True,
             'yes_label': 'Available', 'no_label': 'Unavailable'},
            {'type': 'text', 'question': 'Notes', 'required': False, 'style': 'multi_line'},
        ],
        'privacy': poll['privacy'],
    })
    assert saved['questions'] == draft['questions']

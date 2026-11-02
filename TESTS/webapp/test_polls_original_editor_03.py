"""Targeted regressions: original visual editor uses JSONL shared-link backend."""
from pathlib import Path

from mifp_app.services import simple_polls

ROOT = Path(__file__).resolve().parents[2] / 'MIFPAPP' / 'CORE' / 'mifp_app'


def test_original_editor_structure_is_back_with_no_smtp_controls():
    html = (ROOT / 'templates/dashboard/polls_simple.html').read_text(encoding='utf8')
    for selector in ('poll-workspace', 'poll-selector', 'poll-editor-head', 'poll-tabs',
                     'poll-editor-layout', 'poll-question-editor', 'poll-preview-panel',
                     'data-poll-new-form', 'survey-admin-questions', 'survey-visual-questions'):
        # JS-generated card uses poll-question-editor; it is not in the template.
        if selector == 'poll-question-editor':
            continue
        assert selector in html
    assert 'data-poll-invite' not in html
    assert 'data-recipient-file' not in html
    assert 'data-poll-copy-link' in html
    assert html.count('id="survey-admin-questions"') == 1
    assert html.count('id="survey-editor-form"') == 1
    assert 'poll-question-editor' in (ROOT / 'static/js/dashboard/polls-simple.js').read_text(encoding='utf8')


def test_original_starting_templates_are_available_in_jsonl_backend(tmp_path):
    expected = {
        'blank': 'yes_no', 'attendance': 'yes_no', 'availability': 'multiple_choice',
        'date': 'date', 'location': 'single_choice',
    }
    for name, qtype in expected.items():
        value = simple_polls.create(tmp_path, template=name)
        assert value['response_mode'] == 'anonymous'
        assert value['status'] == 'draft'
        assert value['questions'][0]['type'] == qtype
    try:
        simple_polls.create(tmp_path, template='unknown')
        raise AssertionError('Unknown templates must not silently create a poll')
    except simple_polls.SurveyError:
        pass

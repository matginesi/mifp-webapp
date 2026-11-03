"""Counts-only polls: privacy-minimal data, idempotency, concurrency, exports."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import io
import json

import pytest

from mifp_app.services import poll_yaml, simple_polls as polls, simple_poll_exports


def _config(*, aggregate=True):
    today = date.today()
    return {
        'title': 'Availability', 'description': 'Synthetic tests',
        'status': 'open', 'response_mode': 'anonymous', 'aggregate_only': aggregate,
        'notice_reviewed': True,
        'deadline': (today + timedelta(days=30)).isoformat(),
        'retention_until': (today + timedelta(days=60)).isoformat(),
        'questions': [
            {'type': 'single_choice', 'question': 'Interest?', 'required': True,
             'options': ['Yes', 'Maybe', 'No']},
            {'type': 'date_availability', 'question': 'Which days?', 'required': False,
             'dates': ['2027-02-21', '2027-02-22']},
        ],
        'privacy': {'controller': 'Synthetic Institute', 'contact': 'privacy@example.org',
                    'purpose': 'Measure interest with aggregate counts only',
                    'legal_basis': '', 'recipients': '', 'transfers': ''},
    }


def test_aggregate_only_publishes_without_nonapplicable_row_disclosures(tmp_path):
    poll = polls.create(tmp_path)
    opened = polls.update(tmp_path, poll['id'], _config())
    assert opened['notice_snapshot']['notice_version'] == 3
    assert opened['aggregate_only'] is True
    assert opened['privacy']['legal_basis'] == opened['privacy']['transfers'] == ''
    q1, q2 = opened['questions']
    selection = q1['options'][0]['id']
    answer = {q1['id']: selection, q2['id']: {'2027-02-21': 'maybe'}}
    assert polls.submit(tmp_path, poll['id'], poll['key'], request_id='a'*32, answers=answer)
    assert not polls.submit(tmp_path, poll['id'], poll['key'], request_id='a'*32, answers=answer)
    counts = polls.summary(tmp_path, poll['id'])
    assert counts['responses'] == 1
    assert counts['questions'][0]['distribution'][0]['count'] == 1
    assert counts['questions'][1]['date_counts']['2027-02-21']['maybe'] == 1
    assert counts['questions'][1]['date_counts']['2027-02-22']['unanswered'] == 1
    directory = tmp_path/'polls_v2'/poll['id']
    assert (directory/'answers.jsonl').read_bytes() == b''
    assert polls.results(tmp_path, poll['id'])[1] == []
    payload = json.loads((directory/'aggregates.json').read_bytes())
    assert 'answers' not in payload and 'submitted_at' not in payload
    assert 'email' not in payload and 'ip' not in payload
    assert payload['seen_ids'] == ['a'*32]
    assert b'Percent of submissions' in polls.export_csv(tmp_path, poll['id'])
    assert simple_poll_exports.export_xlsx(tmp_path, poll['id'])[:2] == b'PK'
    assert simple_poll_exports.export_pdf(tmp_path, poll['id'])[:4] == b'%PDF'
    definition = poll_yaml.export_template(tmp_path, poll['id'])
    assert poll_yaml.load_template(definition)['poll']['aggregate_only'] is True
    imported = poll_yaml.create_draft(tmp_path, poll_yaml.load_template(definition))
    assert imported['aggregate_only'] is True
    assert imported['id'] != poll['id']


def test_concurrent_counts_retry_and_restart(tmp_path):
    poll = polls.create(tmp_path)
    poll = polls.update(tmp_path, poll['id'], _config())
    q1, q2 = poll['questions']
    answers = {q1['id']: q1['options'][1]['id'], q2['id']: {'2027-02-21': 'yes'}}

    def submit(index):
        return polls.submit(tmp_path, poll['id'], poll['key'], request_id=f'{index:032x}', answers=answers)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, range(1, 33))) == 32
    # A reopened storage reader still sees the durable deduplication ledger.
    assert not submit(1)
    result = polls.summary(tmp_path, poll['id'])
    assert result['responses'] == 32
    assert result['questions'][1]['date_counts']['2027-02-21']['yes'] == 32
    with pytest.raises(polls.SurveyError, match='frozen|locked'):
        polls.update(tmp_path, poll['id'], {**_config(), 'aggregate_only': False})


def test_aggregate_rejects_free_text_and_never_relaxes_legacy_personal_rows(tmp_path):
    poll = polls.create(tmp_path)
    cfg = _config()
    cfg['questions'] = [{'type': 'text', 'question': 'What is your name?', 'required': False}]
    with pytest.raises(polls.SurveyError, match='free-text'):
        polls.update(tmp_path, poll['id'], cfg)
    cfg = _config(aggregate=False)
    with pytest.raises(polls.SurveyError, match='legal_basis'):
        polls.update(tmp_path, poll['id'], cfg)
    cfg['privacy'].update(legal_basis='legitimate_interest', legitimate_interest='Synthetic planning',
                          recipients='Synthetic recipients', transfers='None (synthetic)')
    opened = polls.update(tmp_path, poll['id'], cfg)
    question = opened['questions'][0]
    assert polls.submit(tmp_path, poll['id'], opened['key'], request_id='f'*32,
                        answers={question['id']: question['options'][0]['id']})
    assert len(polls.results(tmp_path, poll['id'])[1]) == 1
    assert not (tmp_path/'polls_v2'/poll['id']/'aggregates.json').exists()


def test_aggregate_corruption_does_not_silently_reset_counts(tmp_path):
    poll = polls.create(tmp_path)
    opened = polls.update(tmp_path, poll['id'], _config())
    q = opened['questions'][0]
    polls.submit(tmp_path, opened['id'], opened['key'], request_id='d'*32,
                 answers={q['id']: q['options'][0]['id']})
    (tmp_path/'polls_v2'/opened['id']/'aggregates.json').write_text('corrupt')
    with pytest.raises(polls.SurveyError, match='Aggregate counts unavailable'):
        polls.summary(tmp_path, opened['id'])
    with pytest.raises(polls.SurveyError, match='Aggregate counts unavailable'):
        polls.submit(tmp_path, opened['id'], opened['key'], request_id='e'*32,
                     answers={q['id']: q['options'][1]['id']})

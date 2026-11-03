"""Evidence for no-name collection; never equate the selector to anonymisation."""
import json
import logging
from copy import deepcopy

import pytest

from TESTS.webapp.test_polls import poll_app, logged_in_client
from TESTS.webapp.test_polls_release import form_data, token
from TESTS.webapp.test_simple_polls import survey_config
from mifp_app.services import simple_polls as store
from mifp_app.utils.logger import RequestContextFilter, get_logger


def test_new_drafts_reuse_only_verified_institutional_facts(poll_app):
    poll = store.create(poll_app.config['RUNTIME_CONFIG_DIR'])
    assert poll['privacy']['controller'].startswith('Mediterranean Institute')
    assert 'Via Appia Nuova 31' in poll['privacy']['controller']
    assert poll['privacy']['contact'] == 'secretary@mifp.eu'
    for field in ('legal_basis', 'legitimate_interest', 'recipients', 'transfers'):
        assert poll['privacy'][field] == ''


def test_real_anonymous_request_keeps_security_but_stores_no_network_or_identity_fields(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll = store.update(base, store.create(base)['id'], survey_config())
    poll_app.config['WTF_CSRF_ENABLED'] = True
    public = poll_app.test_client()
    url = f'/survey/{poll["id"]}/{poll["key"]}'
    page = public.get(url)
    cookie = public.get_cookie('mifp_csrf')
    assert cookie is not None and public.get_cookie('session') is None
    rid = token(page, 'submission_id')
    csrf = token(page, '_csrf_token')
    payload = {'submission_id': rid, '_csrf_token': csrf,
               'q_' + poll['questions'][0]['id']: 'yes',
               'first_name': 'Injected', 'last_name': 'Identity', 'email': 'injected@example.org'}
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    handler.addFilter(RequestContextFilter())
    logger = get_logger('access')
    logger.addHandler(handler)
    try:
        assert public.post(url, data=payload, environ_overrides={'REMOTE_ADDR': '192.0.2.90'},
                           headers={'User-Agent': 'Synthetic identifying browser'}).status_code == 200
        assert public.post(url, data=payload).status_code == 200
        rejected = public.post(url, data={**payload, '_csrf_token': 'invalid'})
        assert rejected.status_code == 400
    finally:
        logger.removeHandler(handler)
    rows = store.results(base, poll['id'])[1]
    assert len(rows) == 1
    assert set(rows[0]) == {'request_id', 'submitted_at', 'answers', 'privacy_version'}
    assert len(rows[0]['submitted_at']) == 10  # UTC day, no precise timestamp
    journal = (base / 'polls_v2' / poll['id'] / 'answers.jsonl').read_text()
    for value in ('injected@example.org', 'Injected', '192.0.2.90',
                  'Synthetic identifying browser', cookie.value, csrf):
        assert value not in journal
    assert records
    for record in records:
        assert record.client_ip is None and record.client_ip_hash is None and record.actor is None
        assert poll['key'] not in record.path
        encoded = json.dumps(record.extra_fields)
        assert rid not in encoded and csrf not in encoded and cookie.value not in encoded
    # A fresh page generates another retry ID, not an individual invitation ID.
    next_page = public.get(url)
    assert token(next_page, 'submission_id') != rid
    assert public.get_cookie('mifp_csrf').value == cookie.value


def test_anonymous_text_can_identify_and_rows_retain_sequence(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    config = survey_config(kind='text')
    poll = store.update(base, store.create(base)['id'], config)
    qid = poll['questions'][0]['id']
    first = 'I am Ada; contact ada@example.org'
    store.submit(base, poll['id'], poll['key'], request_id='a'*32, answers={qid: first})
    store.submit(base, poll['id'], poll['key'], request_id='b'*32, answers={qid: 'Later response'})
    rows = store.results(base, poll['id'])[1]
    assert [row['answers'][qid] for row in rows] == [first, 'Later response']
    # Text and append order are deliberately preserved features, not a tested
    # anonymisation mechanism. Their risks must not be hidden by publication UI.
    notice = poll_app.test_client().get(f'/survey/{poll["id"]}/{poll["key"]}').data
    assert b'does not guarantee effective anonymity' in notice
    assert b'free text' in notice and b'record order' in notice
    assert b'processing_consent' not in notice


def test_closed_availability_keeps_linkable_vectors_even_without_timing(poll_app):
    from TESTS.webapp.test_polls_privacy_cleanup import full_document

    base = poll_app.config['RUNTIME_CONFIG_DIR']
    # This is a synthetic, fully disclosed test survey, NOT publication of the
    # user's incomplete YAML. Use its actual closed-question structure only.
    cfg = {**full_document()['poll'], 'status': 'open', 'notice_reviewed': True}
    poll = store.update(base, store.create(base)['id'], cfg)
    interest, availability = poll['questions']
    assert {q['type'] for q in poll['questions']} == {'single_choice', 'date_availability'}
    dates = availability['dates']
    external_schedules = {
        'Synthetic person A': ('yes', 'yes', 'no', 'no', 'no', 'no', 'no', 'no'),
        'Synthetic person B': ('no', 'no', 'no', 'no', 'no', 'no', 'yes', 'yes'),
    }
    public = poll_app.test_client()
    url = f'/survey/{poll["id"]}/{poll["key"]}'
    for index, pattern in enumerate(external_schedules.values()):
        payload = {'submission_id': f'{index:032x}',
                   'q_' + interest['id']: interest['options'][0]['id']}
        payload.update({f'q_{availability["id"]}_{day}': choice for day, choice in zip(dates, pattern)})
        assert public.post(url, data=payload).status_code == 200
    rows = store.results(base, poll['id'])[1]
    # Strip ALL metadata and reverse the order. Matching still works whenever
    # such distinguishing schedules are known outside the survey. This is an
    # explicit counterexample, not a claim of having identified actual members.
    vectors = [tuple(row['answers'][availability['id']][day] for day in dates)
               for row in reversed(rows)]
    matches = [[name for name, pattern in external_schedules.items() if pattern == vector]
               for vector in vectors]
    assert matches == [['Synthetic person B'], ['Synthetic person A']]
    assert len(rows) == 2 and all('email' not in row for row in rows)


@pytest.mark.parametrize('mode', ['anonymous', 'identified'])
def test_incomplete_draft_saves_but_mode_does_not_bypass_disclosures(poll_app, mode):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    client = logged_in_client(poll_app)
    poll = store.create(base)
    cfg = survey_config(mode)
    cfg['privacy']['legal_basis'] = ''
    cfg['status'] = 'draft'
    url = '/dashboard/polls-simple/' + poll['id']
    assert client.post(url + '/save', data=form_data(cfg)).status_code == 302
    assert store.get(base, poll['id'])['privacy']['legal_basis'] == ''
    response = client.post(url + '/save', data={**form_data(cfg), 'action': 'publish'})
    assert response.status_code == 400 and b'legal_basis' in response.data
    assert b'Draft saved. Cannot publish yet' in response.data
    if mode == 'anonymous':
        assert b'No-name collection does not establish' in response.data
    assert store.get(base, poll['id'])['status'] == 'draft'


def test_previous_notice_renderer_and_answers_remain_unchanged(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll = store.update(base, store.create(base)['id'], survey_config())
    path = base / 'polls_v2' / poll['id'] / 'poll.json'
    raw = json.loads(path.read_text())
    raw['notice_snapshot']['notice_version'] = 1
    path.write_text(json.dumps(raw))  # synthetic historical record only
    before = deepcopy(raw['notice_snapshot'])
    store.submit(base, poll['id'], poll['key'], request_id='c'*32,
                 answers={poll['questions'][0]['id']: 'no'})
    journal = path.with_name('answers.jsonl').read_bytes()
    public = poll_app.test_client().get(f'/survey/{poll["id"]}/{poll["key"]}')
    assert b'Your responses only; no name or email' in public.data
    assert b'does not guarantee effective anonymity' not in public.data
    assert store.get(base, poll['id'])['notice_snapshot'] == before
    assert path.with_name('answers.jsonl').read_bytes() == journal

"""Focused YAML/template and date-availability route regressions."""
from __future__ import annotations

import csv
import io
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from TESTS.webapp.test_polls import poll_app, logged_in_client
from TESTS.webapp.test_polls_release import form_data, opened, token
from TESTS.webapp.test_simple_polls import survey_config
from mifp_app.services import poll_yaml, simple_polls as store

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/polls/MIFP_Winter_School_2027_poll_template.yaml'


def example():
    return yaml.safe_load(FIXTURE.read_bytes())


def upload(client, payload=None, **data):
    return client.post('/dashboard/polls-simple/import-yaml', data={
        **data, 'yaml_file': (io.BytesIO(FIXTURE.read_bytes() if payload is None else payload), 'poll.yaml')})


def confirmation(page):
    return re.search(rb'name="confirmation" value="([^"]+)"', page.data).group(1).decode()


def date_config(required=False, mode='anonymous'):
    config = survey_config(mode=mode)
    config['questions'] = [{'type': 'date_availability', 'question': 'Available days?',
                            'required': required, 'dates': ['2027-02-21', '2027-02-22']}]
    return config


def test_supplied_fixture_preview_confirm_export_preserves_existing_poll(poll_app):
    app, base = poll_app, poll_app.config['RUNTIME_CONFIG_DIR']
    existing, _, url = opened(app)
    app.test_client().post(url, data={'submission_id': 'a'*32, f'q_{existing["questions"][0]["id"]}': 'yes'})
    directory = base / 'polls_v2' / existing['id']
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    admin = logged_in_client(app)
    preview = upload(admin)
    assert preview.status_code == 200
    assert b'Winter School 2027' in preview.data and b'2027-02-28' in preview.data
    assert len(store.list_all(base)) == 1  # preview has not written a draft
    result = admin.post('/dashboard/polls-simple/import-yaml/confirm', data={
        'confirmation': confirmation(preview), 'confirm_import': 'yes'})
    assert result.status_code == 302
    pid = result.headers['Location'].rsplit('/', 1)[1]
    imported = store.get(base, pid)
    assert pid != existing['id'] and imported['key'] != existing['key']
    assert imported['status'] == 'draft' and imported['response_mode'] == 'anonymous'
    assert imported['questions'][1]['dates'] == [f'2027-02-{d}' for d in range(21, 29)]
    assert imported['privacy']['confirmed'] is False
    assert store.results(base, pid)[1] == []
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    exported = admin.get(f'/dashboard/polls-simple/{pid}/export.yaml')
    assert exported.status_code == 200 and exported.headers['Cache-Control'].startswith('no-store')
    assert exported.mimetype == 'application/yaml'
    assert poll_yaml.load_template(exported.data) == poll_yaml.load_template(FIXTURE.read_bytes())
    assert not any(secret.encode() in exported.data for secret in (existing['id'], existing['key'], imported['id'], imported['key']))
    assert set(yaml.safe_load(exported.data)) == {'schema_version', 'poll'}
    assert set(yaml.safe_load(exported.data)['poll']) == {'title', 'description', 'response_mode', 'questions'}
    assert app.test_client().get(f'/survey/{pid}/{imported["key"]}').status_code == 404
    assert app.test_client().get(f'/dashboard/polls-simple/{pid}/export.yaml').status_code == 302


def test_import_authorization_confirmation_session_expiry_and_csrf(poll_app, monkeypatch):
    app, base = poll_app, poll_app.config['RUNTIME_CONFIG_DIR']
    assert upload(app.test_client()).status_code == 302
    admin = logged_in_client(app)
    preview = upload(admin)
    pending = confirmation(preview)
    path = '/dashboard/polls-simple/import-yaml/confirm'
    assert admin.post(path, data={'confirmation': pending}).status_code == 400
    assert admin.post(path, data={'confirmation': pending+'x', 'confirm_import': 'yes'}).status_code == 400
    other = logged_in_client(app)
    assert other.post(path, data={'confirmation': pending, 'confirm_import': 'yes'}).status_code == 400
    from itsdangerous import TimestampSigner
    original = TimestampSigner.get_timestamp
    with monkeypatch.context() as shifted:
        shifted.setattr(TimestampSigner, 'get_timestamp', lambda self: original(self) + 1801)
        assert admin.post(path, data={'confirmation': pending, 'confirm_import': 'yes'}).status_code == 400
    assert not store.list_all(base)
    admin = logged_in_client(app)  # discard the cookie timestamped by the expiry probe
    app.config['WTF_CSRF_ENABLED'] = True
    assert upload(admin).status_code == 400
    page = admin.get('/dashboard/polls-simple')
    csrf = token(page, '_csrf_token')
    preview = upload(admin, _csrf_token=csrf)
    assert preview.status_code == 200
    assert admin.post(path, data={'confirmation': confirmation(preview), 'confirm_import': 'yes'}).status_code == 400
    # A failed admin CSRF check rotates the token: fetch the fresh token.
    csrf = token(admin.get('/dashboard/polls-simple'), '_csrf_token')
    preview = upload(admin, _csrf_token=csrf)
    created = admin.post(path, data={'confirmation': confirmation(preview), 'confirm_import': 'yes', '_csrf_token': csrf})
    assert created.status_code == 302 and len(store.list_all(base)) == 1


@pytest.mark.parametrize('payload', [
    b'', b'poll: [unterminated', b'\xff', b'---\n{}\n---\n{}',
    b'!!python/object/apply:os.system ["echo unsafe"]', b'poll: &p {questions: [*p]}',
    b'schema_version: 1\nschema_version: 1\npoll: {}',
    b'schema_version: 1\npoll: {title: A, title: B}',
    b'schema_version: 1\npoll: {2027-02-21: x}',
    br'poll: {title: "\ud800"}',
    b'['*9 + b'0' + b']'*9,
    b'[' + b'0,' * 6001 + b'0]',
    b'x' * (poll_yaml.MAX_YAML_BYTES + 1),
])
def test_invalid_yaml_is_rejected_without_writing(poll_app, payload):
    response = upload(logged_in_client(poll_app), payload)
    assert response.status_code == 400
    assert not store.list_all(poll_app.config['RUNTIME_CONFIG_DIR'])


@pytest.mark.parametrize('field', ['key', 'id', 'status', 'answers', 'responses', 'email', 'identity', 'deadline'])
def test_runtime_or_unsupported_fields_are_rejected(field):
    value = example()
    value['poll'][field] = 'must not be imported'
    with pytest.raises(store.SurveyError, match='Unsupported'):
        poll_yaml.load_template(yaml.safe_dump(value).encode())


@pytest.mark.parametrize('dates', [[], ['2027-02-29'], ['2027-02-21', '2027-02-21'],
                                   ['20270221'], ['2027-W07-1'], [True], [17],
                                   ['2027-02-21T10:00:00Z'], ['2027-02-21']*61])
def test_invalid_availability_dates(dates):
    value = example()
    value['poll']['questions'][1]['dates'] = dates
    with pytest.raises(store.SurveyError):
        poll_yaml.load_template(yaml.safe_dump(value).encode())


def test_yaml_date_scalars_bool_schema_and_unexpected_question_fields():
    value = example()
    value['poll']['questions'][1]['dates'] = [__import__('datetime').date(2028, 2, 29)]
    assert poll_yaml.load_template(yaml.safe_dump(value).encode())['poll']['questions'][1]['dates'] == ['2028-02-29']
    bad = yaml.safe_dump(value).replace('2028-02-29', '2027-02-29').encode()
    with pytest.raises(store.SurveyError):
        poll_yaml.load_template(bad)
    for change in [{'schema_version': True}, {'schema_version': '1'}, {'schema_version': 2}, {'identities': []}]:
        with pytest.raises(store.SurveyError):
            poll_yaml.validate_template({**value, **change})
    for change in [{'required': 'false'}, {'id': 'unsafe'}, {'dates': '2027-02-21'}]:
        altered = deepcopy(example())
        altered['poll']['questions'][1].update(change)
        with pytest.raises(store.SurveyError):
            poll_yaml.validate_template(altered)


def test_imported_privacy_never_counts_as_verified(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    value = example()
    cfg = survey_config()
    value['poll']['privacy'] = cfg['privacy']  # includes confirmed: true
    poll = poll_yaml.create_draft(base, value)
    assert poll['privacy']['controller'] == cfg['privacy']['controller']
    assert not poll['privacy']['confirmed']
    cfg.update(title=poll['title'], description=poll['description'], questions=value['poll']['questions'], privacy=poll['privacy'])
    with pytest.raises(store.SurveyError, match='privacy'):
        store.update(base, poll['id'], cfg)
    cfg['privacy']['confirmed'] = True
    assert store.update(base, poll['id'], cfg)['status'] == 'open'


@pytest.mark.parametrize('required', [True, False])
def test_availability_routes_persistence_summary_csv_and_schema_lock(poll_app, required):
    app, base = poll_app, poll_app.config['RUNTIME_CONFIG_DIR']
    cfg = date_config(required=required)
    poll = store.update(base, store.create(base)['id'], cfg)
    url = f'/survey/{poll["id"]}/{poll["key"]}'
    qid = poll['questions'][0]['id']
    public = app.test_client()
    page = public.get(url)
    assert page.status_code == 200 and b'poll-availability' in page.data
    assert b'2027-02-21: Maybe' in page.data
    assert b'2027-02-21: Unanswered' not in page.data if required else b'2027-02-21: Unanswered' in page.data
    first = f'q_{qid}_2027-02-21'
    second = f'q_{qid}_2027-02-22'
    payload = {'submission_id': '1'*32, first: 'yes', second: 'maybe'}
    for invalid in [{first: 'wrong'}, {first: ['yes', 'no']}, {f'q_{qid}_2027-02-30': 'yes'}]:
        assert public.post(url, data={**payload, **invalid}).status_code == 400
    partial = {'submission_id': '2'*32, first: 'no'}
    assert public.post(url, data=partial).status_code == (400 if required else 200)
    assert public.post(url, data=payload).status_code == public.post(url, data=payload).status_code == 200
    rows = store.results(base, poll['id'])[1]
    assert len(rows) == (1 if required else 2)
    assert rows[-1]['answers'][qid] == {'2027-02-21': 'yes', '2027-02-22': 'maybe'}
    counts = store.summary(base, poll['id'])['questions'][0]['date_counts']
    assert counts['2027-02-21']['yes'] == 1
    assert counts['2027-02-22']['maybe'] == 1
    assert counts['2027-02-22']['unanswered'] == (0 if required else 1)
    exported = list(csv.reader(io.StringIO(store.export_csv(base, poll['id']).decode('utf-8-sig'))))
    assert exported[0] == ['Available days? [2027-02-21]', 'Available days? [2027-02-22]']
    assert exported[-1] == ['yes', 'maybe']
    admin = logged_in_client(app)
    detail = admin.get(f'/dashboard/polls-simple/{poll["id"]}')
    assert detail.status_code == 200 and b'Responses by date' in detail.data
    frozen = deepcopy(cfg)
    frozen['questions'][0]['dates'].append('2027-02-23')
    with pytest.raises(store.SurveyError):
        store.update(base, poll['id'], frozen)
    before = (base / 'polls_v2' / poll['id'] / 'answers.jsonl').read_bytes()
    same = form_data(cfg)
    same['q_0_dates'] = '\n'.join(cfg['questions'][0]['dates'])
    assert admin.post(f'/dashboard/polls-simple/{poll["id"]}/save', data=same).status_code == 302
    assert store.get(base, poll['id'])['questions'] == poll['questions']
    assert before == (base / 'polls_v2' / poll['id'] / 'answers.jsonl').read_bytes()


def test_availability_concurrent_submissions_and_retry(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll = store.update(base, store.create(base)['id'], date_config())
    qid = poll['questions'][0]['id']
    url = f'/survey/{poll["id"]}/{poll["key"]}'
    def send(index):
        return poll_app.test_client().post(url, data={
            'submission_id': f'{index:032x}', f'q_{qid}_2027-02-21': ['yes', 'maybe', 'no'][index % 3]}).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert set(pool.map(send, list(range(24)) + [0]*6)) == {200}
    summary = store.summary(base, poll['id'])
    assert summary['responses'] == 24
    assert summary['questions'][0]['date_counts']['2027-02-21'] == {'yes': 8, 'maybe': 8, 'no': 8, 'unanswered': 0}
    assert summary['questions'][0]['date_counts']['2027-02-22']['unanswered'] == 24


def test_upload_limits_before_csrf_and_bad_extension(poll_app):
    client = logged_in_client(poll_app)
    poll_app.config['WTF_CSRF_ENABLED'] = True
    response = client.post('/dashboard/polls-simple/import-yaml', data=b'x' * (2*1024*1024 + 65537),
                           content_type='application/x-www-form-urlencoded')
    assert response.status_code == 413
    poll_app.config['WTF_CSRF_ENABLED'] = False
    response = client.post('/dashboard/polls-simple/import-yaml', data={'yaml_file': (io.BytesIO(b'{}'), 'poll.zip')})
    assert response.status_code == 400


def test_all_existing_question_types_yaml_roundtrip(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    cfg = survey_config()
    cfg['status'] = 'draft'
    cfg['questions'] = [
        {'type': 'text', 'question': 'Notes', 'style': 'multi_line', 'required': False},
        {'type': 'yes_no', 'question': 'Attend?', 'yes_label': 'Available', 'no_label': 'Unavailable', 'required': True},
        {'type': 'single_choice', 'question': 'Place?', 'options': ['A', 'B'], 'required': True},
        {'type': 'multiple_choice', 'question': 'Sessions?', 'options': ['C', 'D'], 'required': False},
        {'type': 'date', 'question': 'Preferred date?', 'required': False},
    ]
    original = store.update(base, store.create(base)['id'], cfg)
    copy = poll_yaml.create_draft(base, poll_yaml.load_template(poll_yaml.export_template(base, original['id'])))
    portable = lambda p: [poll_yaml._question_template(q) for q in p['questions']]
    assert portable(original) == portable(copy)
    assert original['questions'][0]['id'] != copy['questions'][0]['id']

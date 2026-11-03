"""Real import routes: canonical Winter School file and reusable definitions."""
from pathlib import Path

import pytest
import yaml

from TESTS.webapp.test_polls import poll_app, logged_in_client
from TESTS.webapp.test_poll_yaml_dates import upload, confirmation
from mifp_app.services import poll_yaml, simple_polls as store

EXAMPLE = Path(__file__).resolve().parents[2]/'TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml'
ORIGINAL = Path(__file__).resolve().parents[1]/'fixtures/polls/MIFP_Winter_School_2027_Poll.yaml'


def import_from_preview(client, payload):
    preview = upload(client, payload)
    assert preview.status_code == 200, preview.data.decode()
    created = client.post('/dashboard/polls-simple/import-yaml/confirm', data={
        'confirmation': confirmation(preview), 'confirm_import': 'yes'})
    assert created.status_code == 302, created.data.decode()
    return created.headers['Location']


@pytest.mark.parametrize('source', [EXAMPLE, ORIGINAL], ids=['canonical-example', 'original-download'])
def test_exact_winter_school_file_import_reopen_export_reimport(poll_app, source):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    client = logged_in_client(poll_app)
    document = yaml.safe_load(source.read_bytes())
    if source == ORIGINAL:
        document['poll']['privacy_source'] = 'custom'  # Explicit administrator intent.
    first_url = import_from_preview(client, yaml.safe_dump(document).encode())
    first = store.get(base, first_url.rsplit('/', 1)[1])
    assert first['status'] == 'draft' and first['response_mode'] == 'anonymous'
    assert first['deadline'] == first['retention_until'] == ''
    assert first['privacy']['contact'] == 'secretary@mifp.eu'
    assert first['privacy']['controller'] == ('' if source == ORIGINAL else 'Mediterranean Institute of Fundamental Physics (MIFP), Via Appia Nuova 31, 00047 Marino (Roma), Italy')
    assert 'confirmed' not in first['privacy']
    assert first['questions'][0]['type'] == 'single_choice'
    assert first['questions'][1]['dates'] == [f'2027-02-{day}' for day in range(21,29)]
    assert client.get(first_url).status_code == 200
    assert store.results(base, first['id'])[1] == []
    before = (base/'polls_v2'/first['id']/'poll.json').read_bytes()
    exported = client.get(first_url+'/export.yaml')
    second_url = import_from_preview(client, exported.data)
    second = store.get(base, second_url.rsplit('/',1)[1])
    assert first['id'] != second['id'] and first['key'] != second['key']
    assert len({q['id'] for p in (first, second) for q in p['questions']}) == 4
    assert poll_yaml.load_template(exported.data) == poll_yaml.load_template(client.get(second_url+'/export.yaml').data)
    assert before == (base/'polls_v2'/first['id']/'poll.json').read_bytes()
    assert 'confirmed' not in yaml.safe_load(exported.data)['poll']['privacy']


@pytest.mark.parametrize('payload, expected', [
    ({'schema_version': 1}, 'poll'),
    ({'schema_version': 1, 'poll': {'questions': []}}, 'poll.title'),
    ({'schema_version': 1, 'poll': {'title':'Test'}}, 'poll.questions'),
    ({'schema_version': 1, 'poll': {'title':'Test','questions':[{'type':'unknown','question':'Q'}]}}, 'poll.questions[0].type'),
])
def test_schema_errors_name_the_field_and_leave_existing_data(poll_app, payload, expected):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    existing = store.create(base)
    directory = base/'polls_v2'/existing['id']
    before = {p.name:p.read_bytes() for p in directory.iterdir()}
    response = upload(logged_in_client(poll_app), yaml.safe_dump(payload).encode())
    assert response.status_code == 400
    assert expected.encode() in response.data
    assert b'role="alert"' in response.data
    assert [p['id'] for p in store.list_all(base)] == [existing['id']]
    assert before == {p.name:p.read_bytes() for p in directory.iterdir()}


def test_malformed_yaml_has_line_location(poll_app):
    response = upload(logged_in_client(poll_app), b'schema_version: 1\npoll: [broken')
    assert response.status_code == 400
    assert b'line 2' in response.data


def test_all_supported_types_semantic_route_roundtrip(poll_app):
    document = yaml.safe_load(EXAMPLE.read_bytes())
    document['poll']['deadline'] = __import__('datetime').date(2027,2,28)
    document['poll']['retention_until'] = '2027-04-30'
    document['poll']['questions'] += [
        {'type':'text','question':'Notes','style':'multi_line','required':False},
        {'type':'yes_no','question':'Attend?','yes_label':'Available','no_label':'Unavailable','required':False},
        {'type':'multiple_choice','question':'Options?','options':['A','B'],'required':False},
        {'type':'date','question':'Preferred day?','required':False},
    ]
    client = logged_in_client(poll_app)
    first = import_from_preview(client, yaml.safe_dump(document).encode())
    result = client.get(first+'/export.yaml')
    second = import_from_preview(client, result.data)
    assert poll_yaml.load_template(yaml.safe_dump(document).encode()) == poll_yaml.load_template(client.get(second+'/export.yaml').data)


def test_failed_persistence_leaves_no_partial_draft(poll_app, monkeypatch):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    existing = store.create(base)
    before = (base/'polls_v2'/existing['id']/'poll.json').read_bytes()
    def fail_write(*args, **kwargs):
        raise OSError('Synthetic disk write failure')
    monkeypatch.setattr(store, '_atomic', fail_write)
    with pytest.raises((OSError, store.SurveyError)):
        poll_yaml.create_draft(base, poll_yaml.load_template(EXAMPLE.read_bytes()))
    assert [p['id'] for p in store.list_all(base)] == [existing['id']]
    assert before == (base/'polls_v2'/existing['id']/'poll.json').read_bytes()


def test_unquoted_invalid_calendar_date_names_its_field(poll_app):
    content = EXAMPLE.read_text().replace('"2027-02-21"', '2027-02-30')
    response = upload(logged_in_client(poll_app), content.encode())
    assert response.status_code == 400
    assert b'poll.questions[1].dates[0]' in response.data
    assert b'calendar date' in response.data


def test_confirmation_write_failure_is_visible_and_rolls_back_only_new_poll(poll_app, monkeypatch):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    existing = store.create(base)
    client = logged_in_client(poll_app)
    preview = upload(client, EXAMPLE.read_bytes())
    def fail_write(*args, **kwargs):
        raise OSError('Synthetic disk write failure')
    monkeypatch.setattr(store, '_atomic', fail_write)
    response = client.post('/dashboard/polls-simple/import-yaml/confirm', data={
        'confirmation':confirmation(preview), 'confirm_import':'yes'})
    assert response.status_code == 400 and b'role="alert"' in response.data
    assert b'No poll was imported' in response.data
    assert [p['id'] for p in store.list_all(base)] == [existing['id']]

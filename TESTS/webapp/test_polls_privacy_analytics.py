"""Targeted corrections: draft privacy, reusable configuration and analysis."""
from copy import deepcopy

import pytest

from TESTS.webapp.test_polls import poll_app, logged_in_client
from TESTS.webapp.test_polls_release import form_data
from TESTS.webapp.test_simple_polls import survey_config
from mifp_app.services import simple_polls as store


def test_yaml_privacy_dates_roundtrip_and_import_remains_draft(poll_app):
    import yaml
    from mifp_app.services import poll_yaml
    from TESTS.webapp.test_poll_yaml_dates import example, upload, confirmation
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    source = example()
    source['poll'].update(privacy_source='custom', privacy=survey_config()['privacy'], deadline='2027-02-28', retention_until='2027-04-30')
    client = logged_in_client(poll_app)
    preview = upload(client, yaml.safe_dump(source).encode())
    assert preview.status_code == 200
    created = client.post('/dashboard/polls-simple/import-yaml/confirm', data={'confirmation': confirmation(preview), 'confirm_import': 'yes'})
    pid = created.headers['Location'].rsplit('/', 1)[1]
    saved = store.get(base, pid)
    assert saved['status'] == 'draft' and 'confirmed' not in saved['privacy']
    assert saved['privacy']['controller'] == source['poll']['privacy']['controller']
    assert saved['deadline'] == source['poll']['deadline'] and saved['retention_until'] == source['poll']['retention_until']
    exported = client.get(f'/dashboard/polls-simple/{pid}/export.yaml')
    data = yaml.safe_load(exported.data)
    assert 'confirmed' not in data['poll']['privacy']
    assert data['poll']['privacy'] == {**{k:v for k,v in source['poll']['privacy'].items() if k != 'confirmed'},'additional_disclosures':''}
    assert poll_yaml.load_template(exported.data) == poll_yaml.load_template(yaml.safe_dump(source).encode())
    again = poll_yaml.create_draft(base, data)
    assert again['status'] == 'draft' and 'confirmed' not in again['privacy']
    assert again['key'] != saved['key'] and saved['key'].encode() not in exported.data


@pytest.mark.parametrize('mode', ['anonymous', 'identified'])
def test_analytics_and_exports_use_current_answers_without_mutating_them(poll_app, mode):
    import csv
    import io
    from openpyxl import load_workbook
    from pypdf import PdfReader
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    cfg = survey_config(mode)
    cfg['questions'] = [
        {'type': 'yes_no', 'question': 'Attend?', 'required': False, 'yes_label': 'Available', 'no_label': 'Unavailable'},
        {'type': 'single_choice', 'question': 'Venue?', 'required': False, 'options': ['A', 'B']},
        {'type': 'multiple_choice', 'question': 'Sessions?', 'required': False, 'options': ['C', 'D']},
        {'type': 'text', 'question': 'Notes', 'required': False},
        {'type': 'date', 'question': 'Preferred date', 'required': False},
        {'type': 'date_availability', 'question': 'Availability?', 'required': False, 'dates': ['2027-02-21', '2027-02-22']},
    ]
    poll = store.update(base, store.create(base)['id'], cfg)
    questions = poll['questions']
    url = f'/survey/{poll["id"]}/{poll["key"]}'
    for index in range(3):
        fields = {'submission_id': f'{index:032x}'}
        if index < 2:
            fields.update({f'q_{questions[0]["id"]}': 'yes' if index == 0 else 'no',
                f'q_{questions[1]["id"]}': questions[1]['options'][index]['id'],
                f'q_{questions[2]["id"]}': [opt['id'] for opt in questions[2]['options']],
                f'q_{questions[5]["id"]}_2027-02-21': 'yes' if index == 0 else 'no'})
        if index == 0:
            fields.update({f'q_{questions[3]["id"]}': '=SUM(1,1)', f'q_{questions[4]["id"]}': '2027-02-21',
                           f'q_{questions[5]["id"]}_2027-02-22': 'maybe'})
        fields.update(first_name='Synthetic', last_name='Participant', email=f'participant{index}@example.org')
        assert poll_app.test_client().post(url, data=fields).status_code == 200
    directory = base/'polls_v2'/poll['id']
    before = {name: (directory/name).read_bytes() for name in ('poll.json', 'answers.jsonl')}
    analysis = store.summary(base, poll['id'])
    assert analysis['responses'] == 3
    yes_no, single, multiple, text, day, availability = analysis['questions']
    assert single['chart_values'] == [1, 1]
    assert yes_no['answered'] == 2 and yes_no['unanswered'] == 1
    assert yes_no['distribution'][0]['percentage'] == 33.3
    assert multiple['chart_values'] == [2, 2]
    assert sum(item['percentage'] for item in multiple['distribution']) == 133.4
    assert text['answered'] == day['answered'] == 1
    assert availability['answered'] == 2 and availability['unanswered'] == 1
    assert availability['date_counts']['2027-02-22'] == {'yes': 0, 'maybe': 1, 'no': 0, 'unanswered': 2}
    assert availability['date_percentages']['2027-02-22']['unanswered'] == 66.7
    assert sum(bucket['count'] for bucket in analysis['activity']) == 3
    client = logged_in_client(poll_app)
    detail = f'/dashboard/polls-simple/{poll["id"]}'
    assert poll_app.test_client().get(detail).status_code == 302
    page = client.get(detail)
    assert b'Total submissions' in page.data and b'Percentages may total more than 100%' in page.data
    for fmt in ('csv', 'xlsx', 'pdf'):
        response = client.get(detail+'/export.'+fmt)
        assert response.status_code == 200 and response.headers['Cache-Control'].startswith('no-store')
        assert poll_app.test_client().get(detail+'/export.'+fmt).status_code == 302
        if fmt == 'csv':
            exported = list(csv.reader(io.StringIO(response.data.decode('utf-8-sig'))))
            assert 'Availability? [2027-02-21]' in exported[0]
            assert len(exported) == 4
        elif fmt == 'xlsx':
            book = load_workbook(io.BytesIO(response.data))
            assert book.sheetnames == ['Overview', 'Responses', 'Analysis']
            header = [cell.value for cell in book['Responses'][1]]
            assert ('email' in header) == (mode == 'identified')
            assert book['Responses'].max_row == 4
            assert any(cell.value == "'=SUM(1,1)" for row in book['Responses'] for cell in row)
            assert not any(cell.data_type == 'f' for sheet in book for row in sheet for cell in row)
            assert any(row[1] == '2027-02-22: Maybe' and row[2] == 1 for row in book['Analysis'].values)
        else:
            assert response.data.startswith(b'%PDF-')
            reader = PdfReader(io.BytesIO(response.data))
            text = '\n'.join(page.extract_text() for page in reader.pages)
            assert '2027-02-22: Maybe' in text and 'Total submissions' in text
    yaml_result = client.get(detail+'/export.yaml').data
    assert b'participant0@example.org' not in yaml_result and poll['key'].encode() not in yaml_result
    assert before == {name: (directory/name).read_bytes() for name in before}
    persisted = store.results(base, poll['id'])[1]
    if mode == 'anonymous':
        assert all(not {'email', 'first_name', 'last_name', 'ip', 'client_id'} & set(row) for row in persisted)
    # Existing timestamps can be grouped without changing stored precision.
    simulated = deepcopy(persisted)
    simulated[0]['submitted_at'] = '2027-01-01'
    simulated[1]['submitted_at'] = '2027-01-02T09:00:00+00:00'
    simulated[2]['submitted_at'] = '2027-01-02'
    assert store._summary(poll, simulated)['activity'] == [{'date': '2027-01-01', 'count': 1}, {'date': '2027-01-02', 'count': 2}]
    public_page = poll_app.test_client().get(url).data
    assert b'restriction of processing' in public_page and b'portability' in public_page
    assert (b'Answers collected without names cannot ordinarily' in public_page) == (mode == 'anonymous')


def test_empty_analytics_has_zero_denominators_and_valid_exports(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll = store.create(base)
    analysis = store.summary(base, poll['id'])
    assert analysis['responses'] == 0 and analysis['activity'] == []
    assert analysis['questions'][0]['answered_percentage'] == 0
    client = logged_in_client(poll_app)
    for fmt in ('xlsx', 'pdf'):
        assert client.get(f'/dashboard/polls-simple/{poll["id"]}/export.{fmt}').status_code == 200

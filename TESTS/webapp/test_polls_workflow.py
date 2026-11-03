"""Final workflow contract: single editor, explicit bound publication review."""
import io
from pathlib import Path

import pytest
import yaml

from TESTS.webapp.test_polls import poll_app, logged_in_client, archived_poll
from TESTS.webapp.test_polls_release import form_data, token
from TESTS.webapp.test_simple_polls import survey_config
from mifp_app.services import simple_polls as store


def draft_and_fields(app):
    base = app.config['RUNTIME_CONFIG_DIR']
    p = store.create(base)
    return p, form_data(survey_config())


@pytest.mark.parametrize('origin',['blank','attendance','existing','yaml'])
def test_all_starting_points_open_one_visual_editor(poll_app,origin):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    client = logged_in_client(poll_app)
    template = origin
    data = {}
    if origin == 'existing':
        old = store.update(base,store.create(base)['id'],{**survey_config(),'status':'draft'})
        template = 'poll:'+old['id']
    elif origin == 'yaml':
        example = Path(__file__).resolve().parents[2]/'TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml'
        data['yaml_file'] = (io.BytesIO(example.read_bytes()),example.name)
    response = client.post('/dashboard/polls-simple/create',data={**data,'template':template})
    if origin == 'yaml':
        assert response.status_code == 200
        assert len(store.list_all(base)) == 0
        response = client.post('/dashboard/polls-simple/import-yaml/confirm',data={
            'confirmation':token(response,'confirmation'),'confirm_import':'yes'})
    assert response.status_code == 302
    url = response.headers['Location']
    page = client.get(url)
    assert page.status_code == 200 and b'id="survey-visual-questions"' in page.data
    assert b'Privacy details' in page.data and b'name="privacy_confirmed"' not in page.data
    assert b'name="status"' not in page.data and b'name="privacy_source"' not in page.data
    p = store.get(base,url.rsplit('/',1)[1])
    assert p['status'] == 'draft' and 'confirmed' not in p['privacy']
    if origin == 'existing':
        assert p['id'] != old['id'] and p['key'] != old['key']


@pytest.mark.parametrize('change',['privacy','draft','session','poll'])
def test_publication_review_cannot_be_replayed_against_changed_context(poll_app,change):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    p,fields = draft_and_fields(poll_app)
    client = logged_in_client(poll_app)
    url = f'/dashboard/polls-simple/{p["id"]}'
    review = client.post(url+'/save',data={**fields,'action':'publish'})
    signed = token(review,'review')
    if change == 'privacy':
        config=survey_config()
        config['privacy']['controller']='Changed controller'
        store.update(base,p['id'],{**config,'status':'draft'})
    elif change == 'draft':
        store.update(base,p['id'],{**survey_config(),'status':'draft','title':'Changed title'})
    elif change == 'session':
        client = logged_in_client(poll_app)
    else:
        other = store.create(base)
        url = f'/dashboard/polls-simple/{other["id"]}'
    assert client.post(url+'/publish',data={'review':signed}).status_code == 400
    assert store.get(base,p['id'])['status'] == 'draft'


def test_archive_retains_read_only_records_and_all_exports(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    p = archived_poll(base)
    directory = base/'polls'/p['id']
    before = {f.name:(f.read_bytes(),f.stat().st_mode) for f in directory.iterdir()}
    url = f'/dashboard/notifications/polls/{p["id"]}'
    client = logged_in_client(poll_app)
    for suffix in ('','/responses','/responses/'+('2'*32)+'/history','/analysis','/export.csv','/export.xlsx','/export.pdf'):
        assert poll_app.test_client().get(url+suffix).status_code == 302
        response = client.get(url+suffix)
        assert response.status_code == 200, response.data[:300]
    for operation in ('invite','delete','reset','anonymize','clean'):
        assert client.post(url+'/'+operation).status_code == 410
    assert before == {f.name:(f.read_bytes(),f.stat().st_mode) for f in directory.iterdir()}
    assert list((base/'polls').iterdir()) == [directory]


def test_review_endpoint_preserves_csrf(poll_app):
    p,fields = draft_and_fields(poll_app)
    client = logged_in_client(poll_app)
    url = f'/dashboard/polls-simple/{p["id"]}'
    review = client.post(url+'/save',data=fields)
    signed = token(review,'review')
    poll_app.config['WTF_CSRF_ENABLED'] = True
    assert client.post(url+'/publish',data={'review':signed}).status_code == 400
    assert store.get(poll_app.config['RUNTIME_CONFIG_DIR'],p['id'])['status'] == 'draft'

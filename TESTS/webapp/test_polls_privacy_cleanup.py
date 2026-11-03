"""One canonical object: full YAML, editor, publication snapshot and responses."""
from copy import deepcopy
from pathlib import Path
import io
import json

import pytest
import yaml

from TESTS.webapp.test_polls import poll_app, logged_in_client
from TESTS.webapp.test_polls_release import form_data, token
from TESTS.webapp.test_simple_polls import survey_config
from mifp_app.services import simple_polls as store, poll_yaml

EXAMPLE=Path(__file__).resolve().parents[1]/'fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml'


def full_document(mode='anonymous'):
    doc=yaml.safe_load(EXAMPLE.read_bytes())
    # Complete synthetic legal facts test software; they are not MIFP declarations.
    doc['poll'].update(response_mode=mode,aggregate_only=(mode == 'anonymous'),deadline='2027-03-01',retention_until='2027-05-01')
    doc['poll']['privacy']={**survey_config(mode)['privacy'],
        'controller':'Synthetic Institute','contact':'privacy@example.org',
        'purpose':'Preliminary school interest','recipients':'Synthetic administrators',
        'transfers':'None (synthetic fixture)','dpo':'Synthetic DPO',
        'additional_disclosures':'Shared URL; membership is not authenticated.'}
    return doc


def import_document(client,document):
    response=client.post('/dashboard/polls-simple/import-yaml',data={
        'yaml_file':(io.BytesIO(yaml.safe_dump(document,allow_unicode=True).encode()),'full-poll.yaml')})
    assert response.status_code==200,response.data.decode()
    created=client.post('/dashboard/polls-simple/import-yaml/confirm',data={'confirmation':token(response,'confirmation'),'confirm_import':'yes'})
    assert created.status_code==302
    return created.headers['Location']


def notice(body):
    return body.split(b'<section class="survey-privacy"',1)[1].split(b'</section>',1)[0]


@pytest.mark.parametrize('mode',['anonymous','identified'])
def test_full_yaml_edit_refresh_review_publish_response_and_exports(poll_app,mode):
    base=poll_app.config['RUNTIME_CONFIG_DIR'];client=logged_in_client(poll_app)
    source=full_document(mode);source['poll']['privacy']['confirmed']=True
    url=import_document(client,source);pid=url.rsplit('/',1)[1]
    imported=store.get(base,pid)
    assert imported['status']=='draft' and 'confirmed' not in imported['privacy']
    assert imported['privacy']['controller']=='Synthetic Institute'
    page=client.get(url)
    assert b'<summary>Privacy details</summary>' in page.data
    assert b'name="privacy_controller"' in page.data and b'Synthetic DPO' in page.data
    assert b'name="privacy_source"' not in page.data and b'privacy_confirmed' not in page.data
    assert not (base/'polls_v2'/'privacy-profile.json').exists()
    cfg={**source['poll'],'status':'draft'}
    cfg['privacy']['purpose']='Updated purpose <script>unsafe()</script>'
    cfg['privacy']['recipients']='Updated synthetic recipients'
    assert client.post(url+'/save',data=form_data(cfg)).status_code==302
    refreshed=store.get(base,pid)
    assert refreshed['privacy']['purpose']==cfg['privacy']['purpose']
    assert refreshed['privacy']['recipients']==cfg['privacy']['recipients']
    raw=json.loads((base/'polls_v2'/pid/'poll.json').read_text())
    assert set(raw['privacy'])==set(store.PRIVACY_LIMITS)
    assert 'privacy_source' not in raw and 'confirmed' not in raw['privacy']
    review=client.post(url+'/save',data={**form_data(cfg),'action':'publish'})
    assert review.status_code==200 and b'Publish poll' in review.data
    assert b'&lt;script&gt;unsafe()&lt;/script&gt;' in review.data
    assert store.get(base,pid)['status']=='draft'
    published=client.post(url+'/publish',data={'review':token(review,'review')},follow_redirects=True)
    assert published.status_code==200 and b'data-poll-share-link' in published.data
    poll=store.get(base,pid);snapshot=deepcopy(poll['notice_snapshot'])
    public=poll_app.test_client();public_url=f'/survey/{pid}/{poll["key"]}'
    page=public.get(public_url);assert page.status_code==200
    assert notice(page.data)==notice(review.data)
    assert b'<script>unsafe()' not in page.data
    interest,availability=poll['questions']
    payload={'submission_id':'a'*32,f'q_{interest["id"]}':interest['options'][0]['id'],
        f'q_{availability["id"]}_2027-02-21':'maybe','first_name':'Test','last_name':'Person','email':'test@example.org'}
    assert public.post(public_url,data=payload).status_code==200
    assert public.post(public_url,data=payload).status_code==200
    rows=store.results(base,pid)[1]
    if mode=='identified':
        assert len(rows)==1 and rows[0]['email']=='test@example.org'
        assert not {'ip','user_agent'} & rows[0].keys()
        assert rows[0]['privacy_version']==snapshot['published_at']
    else:
        assert rows==[] and store.summary(base,pid)['responses']==1
        assert (base/'polls_v2'/pid/'answers.jsonl').read_bytes()==b''
    journal=(base/'polls_v2'/pid/'answers.jsonl').read_bytes()
    for fmt in ('csv','xlsx','pdf'):
        assert public.get(url+'/export.'+fmt).status_code==302
        assert client.get(url+'/export.'+fmt).status_code==200
    exported=client.get(url+'/export.yaml').data
    again=poll_yaml.create_draft(base,poll_yaml.load_template(exported))
    assert again['status']=='draft' and again['id']!=pid
    assert poll_yaml.load_template(exported)==poll_yaml.load_template(poll_yaml.export_template(base,again['id']))
    assert journal==(base/'polls_v2'/pid/'answers.jsonl').read_bytes()
    assert store.get(base,pid)['notice_snapshot']==snapshot


def test_winter_school_aggregate_only_can_publish_without_inventing_legal_facts(poll_app):
    base=poll_app.config['RUNTIME_CONFIG_DIR'];client=logged_in_client(poll_app)
    doc=yaml.safe_load(EXAMPLE.read_bytes());url=import_document(client,doc);pid=url.rsplit('/',1)[1]
    assert doc['poll']['privacy']['legal_basis']==doc['poll']['privacy']['transfers']==''
    cfg={**doc['poll'],'status':'draft','deadline':'2027-03-01','retention_until':'2027-05-01'}
    assert client.post(url+'/save',data=form_data(cfg)).status_code==302
    assert store.get(base,pid)['aggregate_only'] is True
    review=client.post(url+'/save',data={**form_data(cfg),'action':'publish'})
    assert review.status_code==200 and b'Publish poll' in review.data
    assert b'Aggregate responses' in review.data
    published=client.post(url+'/publish',data={'review':token(review,'review')},follow_redirects=True)
    assert published.status_code==200 and b'data-poll-share-link' in published.data
    saved=store.get(base,pid)
    assert saved['notice_snapshot']['notice_version']==3
    assert saved['questions'][1]['dates']==[f'2027-02-{day}' for day in range(21,29)]
    assert (base/'polls_v2'/pid/'answers.jsonl').read_bytes()==b''
    public=poll_app.test_client();url_public=f'/survey/{pid}/{saved["key"]}'
    assert public.get(url_public).status_code==200
    assert b'Legal basis for response processing:' not in public.get(url_public).data
    interest, availability=saved['questions']
    payload={'submission_id':'f'*32, f'q_{interest["id"]}':interest['options'][0]['id'],
             f'q_{availability["id"]}_2027-02-21':'yes'}
    assert public.post(url_public,data=payload).status_code==200
    assert public.post(url_public,data=payload).status_code==200
    assert store.summary(base,pid)['responses']==1
    assert store.results(base,pid)[1]==[]
    assert (base/'polls_v2'/pid/'answers.jsonl').read_bytes()==b''


@pytest.mark.parametrize('kind',['custom','organisation'])
def test_old_draft_adopts_canonical_privacy_explicitly_without_losing_questions(poll_app,kind):
    base=poll_app.config['RUNTIME_CONFIG_DIR'];client=logged_in_client(poll_app)
    p=store.create(base);directory=base/'polls_v2'/p['id'];path=directory/'poll.json'
    raw=json.loads(path.read_text());raw['privacy_source']='custom' if kind=='custom' else 'organization'
    facts=full_document()['poll']['privacy']
    raw['privacy']=deepcopy(facts) if kind=='custom' else {'purpose':'Old specific purpose'}
    path.write_text(json.dumps(raw))
    previous=base/'polls_v2'/'privacy-profile.json'
    previous.write_text(json.dumps({'privacy':{**facts,'confirmed':False},'revision':'c'*32}))
    old_profile=previous.read_bytes();questions=raw['questions'];journal=(directory/'answers.jsonl').read_bytes()
    url=f'/dashboard/polls-simple/{p["id"]}'
    assert b'Adopt privacy configuration' in client.get(url).data
    cfg={**raw,'status':'draft','questions':[poll_yaml._question_template(q) for q in questions]}
    assert client.post(url+'/save',data={**form_data(cfg),'action':'adopt_privacy'}).status_code==302
    saved=store.get(base,p['id'])
    assert saved['questions']==questions and saved['privacy']['controller']=='Synthetic Institute'
    assert saved['privacy']['purpose']==raw['privacy']['purpose']
    assert 'privacy_source' not in json.loads(path.read_text())
    assert 'confirmed' not in saved['privacy']
    assert journal==(directory/'answers.jsonl').read_bytes() and previous.read_bytes()==old_profile


def test_existing_published_notice_is_immutable_and_independent_of_old_profile(poll_app):
    base=poll_app.config['RUNTIME_CONFIG_DIR'];client=logged_in_client(poll_app)
    p=store.update(base,store.create(base)['id'],survey_config())
    path=base/'polls_v2'/p['id']/'poll.json'
    raw=json.loads(path.read_text());raw['privacy_source']='custom';raw['privacy']['confirmed']=True
    path.write_text(json.dumps(raw));snapshot=deepcopy(raw['notice_snapshot'])
    store.submit(base,p['id'],p['key'],request_id='b'*32,answers={p['questions'][0]['id']:'yes'})
    journal=(path.parent/'answers.jsonl').read_bytes()
    before=poll_app.test_client().get(f'/survey/{p["id"]}/{p["key"]}').data
    (base/'polls_v2'/'privacy-profile.json').write_text('{"privacy":{"controller":"DIFFERENT"}}')
    assert notice(poll_app.test_client().get(f'/survey/{p["id"]}/{p["key"]}').data)==notice(before)
    changed=survey_config();changed['privacy']['legal_basis']='contract'
    response=client.post(f'/dashboard/polls-simple/{p["id"]}/save',data={**form_data(changed),'action':'save'})
    assert response.status_code==400
    assert store.get(base,p['id'])['notice_snapshot']==snapshot
    assert journal==(path.parent/'answers.jsonl').read_bytes()


def test_review_rejects_changed_poll_tampering_and_missing_csrf(poll_app):
    base=poll_app.config['RUNTIME_CONFIG_DIR'];client=logged_in_client(poll_app)
    url=import_document(client,full_document());pid=url.rsplit('/',1)[1]
    fields=form_data({**full_document()['poll'],'status':'draft'})
    review=client.post(url+'/save',data={**fields,'action':'publish'});signed=token(review,'review')
    changed=full_document()['poll'];changed['privacy']['contact']='changed@example.org'
    assert client.post(url+'/save',data={**form_data(changed),'action':'save_draft'}).status_code==302
    assert client.post(url+'/publish',data={'review':signed}).status_code==400
    assert client.post(url+'/publish',data={'review':'tampered'}).status_code==400
    poll_app.config['WTF_CSRF_ENABLED']=True
    assert client.post(url+'/publish',data={'review':signed}).status_code==400
    assert store.get(base,pid)['status']=='draft'


def test_removed_privacy_routes_cannot_write_or_create_profiles(poll_app):
    client=logged_in_client(poll_app);p=store.create(poll_app.config['RUNTIME_CONFIG_DIR'])
    for old in ('/dashboard/polls-simple/privacy-profile',f'/dashboard/polls-simple/{p["id"]}/privacy'):
        assert client.get(old).status_code==404
        assert client.post(old,data={'privacy_confirmed':'yes'}).status_code in {404,405}
    assert not (poll_app.config['RUNTIME_CONFIG_DIR']/'polls_v2'/'privacy-profile.json').exists()



def test_public_policy_describes_polls_without_membership_or_transfer_assumptions(poll_app):
    response=poll_app.test_client().get('/privacy')
    assert response.status_code==200
    text=response.data.decode()
    assert 'Shared-link Polls' in text and 'anonymous mode' in text.lower()
    assert 'only public form' not in text
    assert 'membership-restricted' in text and 'does not establish the absence' in text
    assert 'specific basis' in text and 'manual deletion' in text


def test_editor_reports_the_precise_invalid_privacy_field_and_preserves_values(poll_app):
    client=logged_in_client(poll_app);base=poll_app.config['RUNTIME_CONFIG_DIR']
    url=import_document(client,full_document());pid=url.rsplit('/',1)[1]
    before=(base/'polls_v2'/pid/'poll.json').read_bytes()
    cfg={**full_document()['poll'],'status':'draft'}
    cfg['privacy']['controller']='X'*201
    response=client.post(url+'/save',data=form_data(cfg))
    assert response.status_code==400
    assert b'privacy.controller: maximum 200 characters' in response.data
    assert ('X'*201).encode() in response.data
    assert before==(base/'polls_v2'/pid/'poll.json').read_bytes()

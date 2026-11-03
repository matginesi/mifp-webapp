"""Full YAML, per-poll edits and a single final participant-notice confirmation."""
from pathlib import Path
import json
import re

import pytest
import yaml
from playwright.sync_api import expect

from TESTS.browser.poll_helpers import fill_privacy_details,start_yaml

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml'


def import_file(page,source):
    start_yaml(page)
    page.locator('[name="yaml_file"]').set_input_files(str(source))
    page.get_by_role('button',name='Preview import',exact=True).click()
    page.get_by_label('I have reviewed this template and want to create a new draft.').check()
    page.get_by_role('button',name='Create new draft',exact=True).click()
    page.wait_for_load_state('networkidle')


@pytest.mark.parametrize('origin',['blank','old-draft','yaml'])
def test_canonical_privacy_original_editor_publish_without_profile_pages(auth_page,live_server,browser_runtime_dir,origin,tmp_path):
    page=auth_page;errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(live_server+'/dashboard/polls-simple')
    if origin=='blank':
        page.locator('[data-poll-new-toggle]').click()
        page.get_by_role('button',name='Create Poll',exact=True).click()
        page.wait_for_load_state('networkidle')
    else:
        document=yaml.safe_load(EXAMPLE.read_bytes())
        document['poll'].update(deadline='2027-03-01',retention_until='2027-05-01')
        document['poll']['privacy'].update(controller='Synthetic YAML controller',contact='yaml@example.org',
            legal_basis='legitimate_interest',legitimate_interest='Synthetic school planning',
            recipients='Synthetic YAML recipients',transfers='None (synthetic test)')
        source=tmp_path/'complete-synthetic-poll.yaml';source.write_text(yaml.safe_dump(document,allow_unicode=True))
        import_file(page,source)
    editor=page.url
    if origin=='old-draft':
        if browser_runtime_dir is None:
            pytest.skip('Old-data seeding requires the isolated browser server')
        poll_id=editor.rsplit('/',1)[1]
        metadata=browser_runtime_dir/'config'/'polls_v2'/poll_id/'poll.json'
        old=json.loads(metadata.read_text());old['privacy_source']='custom';old['privacy']['confirmed']=False
        metadata.write_text(json.dumps(old));page.reload();page.wait_for_load_state('networkidle')
    page.get_by_role('tab',name='Privacy',exact=True).click()
    expect(page.locator('[data-anonymous-privacy-info]')).to_be_visible()
    expect(page.locator('[data-anonymous-privacy-info]')).to_contain_text('does not guarantee effective anonymity')
    expect(page.locator('[data-identified-privacy-info]')).to_be_hidden()
    if origin == 'blank':
        expect(page.locator('[name="privacy_controller"]')).to_have_value('Mediterranean Institute of Fundamental Physics (MIFP), Via Appia Nuova 31, 00047 Marino (Roma), Italy')
        expect(page.locator('[name="privacy_contact"]')).to_have_value('secretary@mifp.eu')
        expect(page.locator('[name="privacy_legal_basis"]')).to_have_value('')
    details=page.locator('details').filter(has=page.locator('[name="privacy_controller"]'))
    expect(details.locator('summary')).to_have_text('Privacy details')
    details.locator('summary').click()
    if origin!='blank':
        expect(page.locator('[name="privacy_controller"]')).to_have_value('Synthetic YAML controller')
        expect(page.locator('[name="privacy_recipients"]')).to_have_value('Synthetic YAML recipients')
    page.locator('[name="privacy_purpose"]').fill('Synthetic per-poll purpose')
    if origin=='old-draft':
        page.get_by_role('button',name='Adopt privacy configuration',exact=True).click()
    else:
        page.get_by_role('button',name='Save Draft',exact=True).click()
    page.wait_for_load_state('networkidle');page.reload();page.wait_for_load_state('networkidle')
    page.get_by_role('tab',name='Privacy',exact=True).click()
    expect(page.locator('[name="privacy_purpose"]')).to_have_value('Synthetic per-poll purpose')
    expect(page.locator('[name="privacy_source"]')).to_have_count(0)
    expect(page.locator('[name="privacy_confirmed"]')).to_have_count(0)
    if origin=='blank':
        with page.expect_response(lambda r:r.url.endswith('/save')) as refused:
            page.get_by_role('button',name='Publish',exact=True).click()
        assert refused.value.status==400
        expect(page.get_by_role('alert')).to_contain_text('Draft saved. Cannot publish yet')
        fill_privacy_details(page)
    else:
        # Legal facts imported from YAML are editable directly, not read-only overrides.
        details=page.locator('details').filter(has=page.locator('[name="privacy_controller"]'))
        if details.get_attribute('open') is None:details.locator('summary').click()
        page.locator('[name="privacy_controller"]').fill('Edited synthetic controller')
        page.locator('[name="privacy_recipients"]').fill('Edited recipients for this survey')
    page.get_by_role('tab',name='Editor',exact=True).click()
    page.locator('[name="deadline"]').fill('2027-03-01')
    page.locator('[name="retention_until"]').fill('2027-05-01')
    if origin=='yaml':
        page.locator('[name="response_mode"]').select_option('identified')
        page.get_by_role('tab',name='Privacy',exact=True).click()
        expect(page.locator('[data-anonymous-privacy-info]')).to_be_hidden()
        expect(page.locator('[data-identified-privacy-info]')).to_be_visible()
        page.get_by_role('tab',name='Editor',exact=True).click()
    if origin!='blank':
        dates=page.get_by_label('Dates (YYYY-MM-DD, one per line)',exact=True)
        expect(dates).to_have_value('\n'.join(f'2027-02-{d}' for d in range(21,29)))
        dates.fill(dates.input_value()+'\n2027-03-01')
        expect(page.locator('#survey-visual-preview')).to_contain_text('2027-03-01')
        dates.fill('\n'.join(f'2027-02-{d}' for d in range(21,29)))
    page.get_by_role('button',name='Save Draft',exact=True).click()
    page.wait_for_load_state('networkidle');page.reload();page.wait_for_load_state('networkidle')
    page.get_by_role('tab',name='Privacy',exact=True).click()
    if origin!='blank':
        expect(page.locator('[name="privacy_controller"]')).to_have_value('Edited synthetic controller')
        expect(page.locator('[name="privacy_recipients"]')).to_have_value('Edited recipients for this survey')
    with page.expect_popup() as event:
        page.get_by_role('button',name='Preview public page',exact=True).click()
    preview=event.value;preview.wait_for_load_state('networkidle')
    expect(preview.locator('.survey-privacy')).to_contain_text('Synthetic per-poll purpose')
    preview.close()
    page.get_by_role('button',name='Publish',exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('heading',name='Review and publish',exact=True)).to_be_visible()
    reviewed=page.locator('.survey-privacy').inner_html()
    expect(page.locator('[type="checkbox"]')).to_have_count(0)
    page.get_by_role('button',name='Publish poll',exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.locator('[data-poll-copy-link]')).to_be_visible()
    public_url=page.locator('[data-poll-share-link]').input_value()
    context=page.context.browser.new_context(viewport={'width':390,'height':844})
    participant=context.new_page();participant.goto(public_url)
    assert participant.locator('.survey-privacy').inner_html()==reviewed
    expect(participant.locator('[name="processing_consent"]')).to_have_count(0)
    expect(participant.locator('.survey-privacy')).to_contain_text('independent of Aruba mailing-list processing')
    if origin != 'yaml':
        expect(participant.locator('.survey-privacy')).to_contain_text('does not guarantee effective anonymity')
    if origin=='yaml':
        participant.get_by_label('First name *',exact=True).fill('Synthetic')
        participant.get_by_label('Last name *',exact=True).fill('Respondent')
        participant.get_by_label('Email *',exact=True).fill('respondent@example.org')
    participant.get_by_label('Yes' if origin=='blank' else 'Yes, I am interested',exact=True).check()
    if origin!='blank':participant.get_by_label('2027-02-21: Maybe',exact=True).check()
    participant.get_by_role('button',name='Submit response',exact=True).click()
    expect(participant.get_by_role('heading',name='Thank you',exact=True)).to_be_visible();context.close()
    page.reload();page.wait_for_load_state('networkidle')
    page.get_by_role('tab',name=re.compile('Responses')).click()
    expect(page.get_by_text('1 submissions recorded.',exact=False)).to_be_visible()
    page.get_by_role('tab',name='Export',exact=True).click()
    with page.expect_download() as event:
        page.get_by_role('link',name='Export YAML',exact=True).click()
    target=tmp_path/'roundtrip.yaml';event.value.save_as(target)
    definition=yaml.safe_load(target.read_bytes())
    assert 'privacy_source' not in definition['poll'] and 'confirmed' not in definition['poll']['privacy']
    page.goto(live_server+'/dashboard/polls-simple');import_file(page,target)
    assert page.url!=editor
    page.get_by_role('tab',name='Export',exact=True).click()
    with page.expect_download() as event:
        page.get_by_role('link',name='Export YAML',exact=True).click()
    again=tmp_path/'again.yaml';event.value.save_as(again)
    assert yaml.safe_load(again.read_bytes())==definition
    assert not errors

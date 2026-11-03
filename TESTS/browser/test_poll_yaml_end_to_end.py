"""Upload, confirm, reopen, export and reupload the actual Winter School file."""
from TESTS.browser.poll_helpers import start_yaml
from pathlib import Path
import re

import pytest
import yaml
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('source', [
    ROOT/'TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll.yaml',
    ROOT/'TESTS/fixtures/polls/MIFP_Winter_School_2027_Poll_canonical.yaml',
], ids=['original-download', 'canonical-example'])
def test_winter_school_dashboard_import_export_reimport(auth_page, live_server, source, tmp_path):
    page = auth_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def upload_file(path):
        page.goto(live_server+'/dashboard/polls-simple')
        page.wait_for_load_state('networkidle')
        start_yaml(page)
        page.locator('[name="yaml_file"]').set_input_files(str(path))
        with page.expect_response(lambda r: r.url.endswith('/create')) as request:
            page.get_by_role('button', name='Preview import', exact=True).click()
        assert request.value.status == 200
        expect(page.get_by_role('heading', name='MIFP Winter School 2027 — Interest and Availability')).to_be_visible()
        page.get_by_label('I have reviewed this template and want to create a new draft.').check()
        with page.expect_response(lambda r: r.url.endswith('/import-yaml/confirm')) as confirmed:
            page.get_by_role('button', name='Create new draft', exact=True).click()
        assert confirmed.value.status == 302
        page.wait_for_load_state('networkidle')
        assert re.search(r'/dashboard/polls-simple/[a-f0-9]{32}$', page.url)
        return page.url
    def exported():
        page.get_by_role('tab', name='Export', exact=True).click()
        with page.expect_download() as event:
            page.get_by_role('link', name='Export YAML', exact=True).click()
        target = tmp_path / event.value.suggested_filename
        event.value.save_as(target)
        return target, yaml.safe_load(target.read_bytes())
    upload_source = source
    if source.name == 'MIFP_Winter_School_2027_Poll.yaml':
        document = yaml.safe_load(source.read_bytes())
        document['poll']['privacy_source'] = 'custom'
        upload_source = tmp_path/'explicit-custom.yaml'
        upload_source.write_text(yaml.safe_dump(document,allow_unicode=True))
    first = upload_file(upload_source)
    page.reload()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('button', name='Publish', exact=True)).to_be_visible()
    expect(page.locator('[name="response_mode"]')).to_have_value('anonymous')
    expect(page.locator('[name="deadline"]')).to_have_value('')
    expect(page.locator('[name="retention_until"]')).to_have_value('')
    expect(page.locator('#survey-visual-questions .poll-question-editor')).to_have_count(2)
    expect(page.get_by_label('Dates (YYYY-MM-DD, one per line)', exact=True)).to_have_value('\n'.join(f'2027-02-{d}' for d in range(21,29)))
    for day in range(21,29):
        for label in ('Yes','Maybe','No'):
            expect(page.locator('#survey-visual-preview').get_by_label(f'2027-02-{day}: {label}', exact=True)).to_be_disabled()
    page.get_by_role('tab', name='Privacy', exact=True).click()
    expect(page.locator('[name="privacy_contact"]')).to_have_count(1)
    if source.name == 'MIFP_Winter_School_2027_Poll.yaml':
        expect(page.locator('[aria-label="Saved privacy notice preview"]')).to_contain_text('secretary@mifp.eu')
    assert 'Winter School' in page.locator('[name="privacy_purpose"]').input_value()
    expect(page.locator('[name="privacy_confirmed"]')).to_have_count(0)
    path, first_definition = exported()
    second = upload_file(path)
    assert second != first
    _, second_definition = exported()
    assert second_definition == first_definition
    assert 'confirmed' not in second_definition['poll']['privacy']
    assert set(second_definition['poll']) == {'title','description','response_mode','aggregate_only','questions','privacy','deadline','retention_until'}
    page.goto(first)
    page.wait_for_load_state('networkidle')
    expect(page.get_by_label('Dates (YYYY-MM-DD, one per line)', exact=True)).to_have_value('\n'.join(f'2027-02-{d}' for d in range(21,29)))
    assert not errors

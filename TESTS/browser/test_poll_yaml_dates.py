"""Original editor + YAML confirmation + responsive date matrix, no live data."""
from TESTS.browser.poll_helpers import fill_privacy_details, publish, start_yaml
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import expect

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/polls/MIFP_Winter_School_2027_poll_template.yaml'


@pytest.mark.parametrize('mode', ['anonymous', 'identified'])
def test_yaml_import_original_editor_dates_public_and_export(auth_page, live_server, mode):
    page = auth_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/dashboard/polls-simple')
    page.wait_for_load_state('networkidle')
    count = page.locator('.poll-list-item').count()
    start_yaml(page)
    page.locator('[name="yaml_file"]').set_input_files(str(FIXTURE))
    page.get_by_role('button', name='Preview import', exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('heading', name='MIFP Winter School 2027 — Interest and Availability')).to_be_visible()
    expect(page.get_by_text('2027-02-28', exact=True)).to_be_visible()
    # Canceling the preview has not created anything.
    page.get_by_role('link', name='Cancel', exact=True).click()
    page.wait_for_load_state('networkidle')
    assert page.locator('.poll-list-item').count() == count
    start_yaml(page)
    page.locator('[name="yaml_file"]').set_input_files(str(FIXTURE))
    page.get_by_role('button', name='Preview import', exact=True).click()
    page.get_by_label('I have reviewed this template and want to create a new draft.').check()
    page.get_by_role('button', name='Create new draft', exact=True).click()
    page.wait_for_load_state('networkidle')
    cards = page.locator('#survey-visual-questions .poll-question-editor')
    expect(cards).to_have_count(2)
    dates = cards.nth(1).get_by_label('Dates (YYYY-MM-DD, one per line)', exact=True)
    expect(dates).to_have_value('\n'.join(f'2027-02-{day}' for day in range(21, 29)))
    # Exercise the new visual type control and edits while retaining all dates.
    cards.nth(1).get_by_label('Response type', exact=True).select_option('date_availability')
    dates.fill(dates.input_value() + '\n2027-03-01')
    expect(page.locator('#survey-visual-preview')).to_contain_text('2027-03-01')
    dates.fill('\n'.join(f'2027-02-{day}' for day in range(21, 29)))
    page.locator('[name="response_mode"]').select_option(mode)
    page.locator('[name="deadline"]').fill('2027-02-28')
    page.locator('[name="retention_until"]').fill('2027-03-31')
    page.get_by_role('tab', name='Privacy', exact=True).click()
    fill_privacy_details(page)
    expect(page.locator('[name="privacy_confirmed"]')).to_have_count(0)
    page.locator('[name="privacy_purpose"]').fill('Synthetic schedule')
    page.get_by_role('button', name='Save Draft', exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(dates).to_have_value('\n'.join(f'2027-02-{day}' for day in range(21, 29)))
    # Full public-page preview uses the same date fields and cannot submit.
    with page.expect_popup() as popup:
        page.get_by_role('button', name='Preview public page', exact=True).click()
    preview = popup.value
    preview.wait_for_load_state('networkidle')
    expect(preview.get_by_label('2027-02-21: Maybe', exact=True)).to_be_disabled()
    preview.close()
    publish(page)
    url = page.locator('[data-poll-share-link]').input_value()
    public_context = page.context.browser.new_context(viewport={'width': 320, 'height': 800})
    public = public_context.new_page()
    public.goto(url)
    public.wait_for_load_state('networkidle')
    for width in (320, 768, 1024, 1440):
        public.set_viewport_size({'width': width, 'height': 800})
        assert public.evaluate('document.documentElement.scrollWidth <= innerWidth')
        expect(public.get_by_label('2027-02-21: Maybe', exact=True)).to_be_visible()
    public.set_viewport_size({'width': 320, 'height': 800})
    if mode == 'identified':
        public.get_by_label('First name *', exact=True).fill('Synthetic')
        public.get_by_label('Last name *', exact=True).fill('Participant')
        public.get_by_label('Email *', exact=True).fill('synthetic@example.org')
    public.get_by_label('Yes, I am interested', exact=True).check()
    public.get_by_label('2027-02-21: Yes', exact=True).check()
    public.get_by_label('2027-02-21: Yes', exact=True).press('ArrowRight')
    expect(public.get_by_label('2027-02-21: Maybe', exact=True)).to_be_checked()
    public.get_by_label('2027-02-21: Maybe', exact=True).press('ArrowLeft')
    public.get_by_label('2027-02-22: Maybe', exact=True).check()
    public.get_by_label('2027-02-23: No', exact=True).check()
    public.get_by_label('2027-02-24: Yes', exact=True).check()
    public.get_by_label('2027-02-24: Unanswered', exact=True).check()
    public.screenshot(path=f'/tmp/mifp-date-availability-{mode}.png', full_page=True)
    public.get_by_role('button', name='Submit response', exact=True).click()
    expect(public.get_by_role('heading', name='Thank you', exact=True)).to_be_visible()
    public_context.close()
    page.reload()
    page.wait_for_load_state('networkidle')
    page.get_by_role('tab', name='Responses 1', exact=False).click()
    table = page.locator('[data-shared-poll-panel="responses"] .poll-availability')
    expect(table.get_by_role('row').filter(has_text='2027-02-21').locator('td strong')).to_have_text(['1', '0', '0', '0'])
    expect(table.get_by_role('row').filter(has_text='2027-02-24').locator('td strong')).to_have_text(['0', '0', '0', '1'])
    page.get_by_role('tab', name='Export', exact=True).click()
    with page.expect_download() as download:
        page.get_by_role('link', name='Export YAML', exact=True).click()
    reusable = yaml.safe_load(Path(download.value.path()).read_bytes())
    assert reusable['schema_version'] == 1
    assert reusable['poll']['response_mode'] == mode
    assert reusable['poll']['questions'][1]['dates'][-1] == '2027-02-28'
    assert set(reusable['poll']) == {'title', 'description', 'response_mode', 'questions', 'deadline', 'retention_until', 'privacy'}
    assert 'confirmed' not in reusable['poll']['privacy']
    assert not errors

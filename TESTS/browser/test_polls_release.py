"""Synthetic browser release smoke test using the isolated Flask server."""
import re
from TESTS.browser.poll_helpers import fill_privacy_details, publish
import pytest

from playwright.sync_api import expect


@pytest.mark.parametrize('mode', ['anonymous', 'identified'])
def test_original_editor_shared_link_and_mobile(auth_page, live_server, mode):
    page = auth_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/dashboard/polls-simple')
    page.wait_for_load_state('networkidle')
    page.locator('[data-poll-new-toggle]').click()
    page.locator('[data-poll-new-form] select').select_option('blank')
    page.get_by_role('button', name='Create Poll', exact=True).click()
    page.wait_for_load_state('networkidle')
    page.get_by_label('Title', exact=True).fill('Synthetic browser release poll')
    cards = page.locator('#survey-visual-questions .poll-question-editor')
    expect(cards).to_have_count(1)
    cards.nth(0).get_by_label('Question', exact=True).fill('Synthetic attendance?')
    cards.nth(0).get_by_role('button', name='Duplicate', exact=True).click()
    expect(cards).to_have_count(2)
    cards.nth(1).get_by_label('Question', exact=True).fill('Synthetic venue?')
    cards.nth(1).get_by_label('Response type', exact=True).select_option('single_choice')
    cards.nth(1).get_by_label('Option 1', exact=True).fill('Room A')
    cards.nth(1).get_by_label('Option 2', exact=True).fill('Room B')
    cards.nth(1).get_by_role('button', name='Add option', exact=True).click()
    cards.nth(1).get_by_label('Option 3', exact=True).fill('Room C')
    cards.nth(1).get_by_role('button', name='Move up', exact=True).nth(2).click()
    expect(cards.nth(1).get_by_label('Option 2', exact=True)).to_have_value('Room C')
    cards.nth(1).get_by_role('button', name='Remove option', exact=True).nth(1).click()
    cards.nth(1).get_by_role('button', name='Up', exact=True).click()
    expect(cards.nth(0).get_by_label('Question', exact=True)).to_have_value('Synthetic venue?')
    page.get_by_role('button', name='Add question', exact=True).click()
    expect(cards).to_have_count(3)
    cards.nth(2).get_by_role('button', name='Delete', exact=True).click()
    expect(cards).to_have_count(2)
    expect(page.locator('#survey-visual-preview')).to_contain_text('Room A')
    page.locator('[name="response_mode"]').select_option(mode)
    page.get_by_label('Deadline', exact=True).fill('2027-03-01')
    page.get_by_label('Retain data until', exact=True).fill('2027-05-01')
    page.get_by_role('tab', name='Privacy', exact=True).click()
    fill_privacy_details(page)
    page.locator('[name="privacy_purpose"]').fill('Synthetic testing')
    page.get_by_role('button', name='Save Draft', exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_text('Survey saved.', exact=True)).to_be_visible()
    expect(cards).to_have_count(2)
    expect(cards.nth(0).get_by_label('Question', exact=True)).to_have_value('Synthetic venue?')
    with page.expect_popup() as popup_info:
        page.get_by_role('button', name='Preview public page', exact=True).click()
    preview = popup_info.value
    preview.wait_for_load_state('networkidle')
    expect(preview.get_by_text('Administrator preview — responses are not saved.')).to_be_visible()
    expect(preview.get_by_role('button', name='Submit response')).to_be_disabled()
    preview.close()
    publish(page)
    link = page.locator('[data-poll-share-link]').input_value()
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.locator('[data-poll-copy-link]').click()
    expect(page.locator('[data-poll-copy-link]')).to_have_text('Copied')
    expect(cards.nth(0).get_by_label('Question', exact=True)).to_be_disabled()
    page.screenshot(path=f'/tmp/mifp-polls-editor-{mode}-release.png', full_page=True)
    context = page.context.browser.new_context(viewport={'width': 390, 'height': 844})
    public = context.new_page()
    external = []
    public.on('request', lambda request: external.append(request.url) if not request.url.startswith(live_server) else None)
    public.goto(link)
    public.wait_for_load_state('networkidle')
    if mode == 'identified':
        public.get_by_label('First name *', exact=True).fill('Synthetic')
        public.get_by_label('Last name *', exact=True).fill('Respondent')
        public.get_by_label('Email *', exact=True).fill('synthetic@example.org')
    else:
        assert not public.locator('[name="email"]').count()
    assert public.evaluate('document.documentElement.scrollWidth <= innerWidth')
    public.get_by_label('Room A', exact=True).check()
    public.get_by_label('Yes', exact=True).check()
    public.screenshot(path=f'/tmp/mifp-polls-public-mobile-{mode}-release.png', full_page=True)
    public.get_by_role('button', name='Submit response', exact=True).click()
    expect(public.get_by_role('heading', name='Thank you', exact=True)).to_be_visible()
    assert not external
    context.close()
    page.reload()
    page.wait_for_load_state('networkidle')
    page.get_by_role('tab', name=re.compile('Responses')).click()
    expect(page.get_by_text('1 submissions recorded.', exact=False)).to_be_visible()
    assert not errors

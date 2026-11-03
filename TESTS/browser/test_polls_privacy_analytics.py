"""Actual editor reproduction and restored analysis against isolated Flask."""
from TESTS.browser.poll_helpers import fill_privacy_details, publish

from playwright.sync_api import expect


def create_poll(page, base):
    page.goto(base + '/dashboard/polls-simple')
    page.wait_for_load_state('networkidle')
    page.locator('[data-poll-new-toggle]').click()
    page.get_by_role('button', name='Create Poll', exact=True).click()
    page.wait_for_load_state('networkidle')


def test_failed_publish_retains_privacy_fields(auth_page, live_server):
    page = auth_page
    create_poll(page,live_server)
    page.get_by_role('tab',name='Privacy',exact=True).click()
    page.locator('[name="privacy_purpose"]').fill('Synthetic planning')
    with page.expect_response(lambda r:r.url.endswith('/save')) as refused:
        page.get_by_role('button',name='Publish',exact=True).click()
    assert refused.value.status == 400
    page.get_by_role('tab',name='Privacy',exact=True).click()
    expect(page.locator('[name="privacy_purpose"]')).to_have_value('Synthetic planning')
    expect(page.locator('[name="privacy_confirmed"]')).to_have_count(0)


def test_progressive_draft_publish_and_real_analytics_charts(auth_page, live_server):
    import re
    page = auth_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    create_poll(page, live_server)
    page.locator('[name="title"]').fill('Synthetic privacy and analytics')
    page.get_by_role('button', name='Add question', exact=True).click()
    cards = page.locator('#survey-visual-questions .poll-question-editor')
    cards.nth(1).get_by_label('Response type', exact=True).select_option('multiple_choice')
    cards.nth(1).get_by_label('Question', exact=True).fill('Synthetic sessions')
    page.get_by_role('button', name='Add question', exact=True).click()
    cards.nth(2).get_by_label('Response type', exact=True).select_option('date_availability')
    cards.nth(2).get_by_label('Dates (YYYY-MM-DD, one per line)', exact=True).fill('2027-02-21\n2027-02-22')
    cards.nth(2).locator('.toggle-row input').uncheck()
    page.get_by_role('tab', name='Privacy', exact=True).click()
    fill_privacy_details(page)
    page.locator('[name="privacy_purpose"]').fill('Synthetic planning')
    page.get_by_role('button',name='Save Draft',exact=True).click()
    page.wait_for_load_state('networkidle')
    page.reload()
    page.get_by_role('tab',name='Privacy',exact=True).click()
    expect(page.locator('[name="privacy_purpose"]')).to_have_value('Synthetic planning')
    with page.expect_response(lambda r: r.url.endswith('/save')) as refused:
        page.get_by_role('button', name='Publish', exact=True).click()
    assert refused.value.status == 400
    expect(page.get_by_role('alert')).to_contain_text('Draft saved. Cannot publish yet')
    page.get_by_role('tab', name='Privacy', exact=True).click()
    expect(page.locator('[name="privacy_purpose"]')).to_have_value('Synthetic planning')
    page.get_by_role('tab', name='Editor', exact=True).click()
    page.locator('[name="deadline"]').fill('2027-03-01')
    page.locator('[name="retention_until"]').fill('2027-04-01')
    publish(page)
    expect(page.locator('[data-poll-share-link]')).to_be_visible()
    url = page.locator('[data-poll-share-link]').input_value()
    context = page.context.browser.new_context()
    public = context.new_page()
    public.goto(url)
    public.get_by_label('Yes', exact=True).check()
    public.get_by_label('Option 1', exact=True).check()
    public.get_by_label('Option 2', exact=True).check()
    public.get_by_label('2027-02-21: Yes', exact=True).check()
    public.get_by_role('button', name='Submit response', exact=True).click()
    expect(public.get_by_role('heading', name='Thank you', exact=True)).to_be_visible()
    context.close()
    page.reload()
    page.wait_for_load_state('networkidle')
    page.get_by_role('tab', name=re.compile('Responses')).click()
    expect(page.get_by_role('heading', name='Poll analytics', exact=True)).to_be_visible()
    page.wait_for_function("""() => [...document.querySelectorAll('canvas[data-chart]')].length === 4 &&
        [...document.querySelectorAll('canvas[data-chart]')].every(c => window.Chart.getChart(c)?.width > 0 && window.Chart.getChart(c)?.height >= 200 && window.Chart.getChart(c)?.chartArea?.height > 20)""")
    charts = page.evaluate("""() => [...document.querySelectorAll('canvas[data-chart]')].map(c => ({
        type: window.Chart.getChart(c).config.type, values: window.Chart.getChart(c).data.datasets[0].data }))""")
    assert charts[0] == {'type': 'line', 'values': [1]}
    assert charts[-1]['values'] == [1, 0]
    expect(page.get_by_text('Respondents may choose several options.', exact=False)).to_be_visible()
    for width in [390, 1280]:
        page.set_viewport_size({'width': width, 'height': 900})
        page.wait_for_function('() => document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=f'/tmp/mifp-polls-analytics-{width}.png', full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('''() => ({width:innerWidth, scroll:document.documentElement.scrollWidth, elements:[...document.querySelectorAll('main *, .poll-editor-head *')].filter(e => e.getBoundingClientRect().right > innerWidth + 2).slice(0,20).map(e => ({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))})''')
    page.screenshot(path='/tmp/mifp-polls-restored-analytics.png', full_page=True)
    page.get_by_role('tab', name='Export', exact=True).click()
    expect(page.get_by_role('link', name='Download XLSX', exact=True)).to_be_visible()
    expect(page.get_by_role('link', name='Download PDF analytics', exact=True)).to_be_visible()
    assert not errors

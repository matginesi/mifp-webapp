"""Browser actions for per-poll canonical privacy and final notice review."""
from playwright.sync_api import expect


def fill_privacy_details(page):
    page.get_by_role('tab',name='Privacy',exact=True).click()
    details=page.locator('details').filter(has=page.locator('[name="privacy_controller"]'))
    if details.get_attribute('open') is None:
        details.locator('summary').click()
    for field,value in {'controller':'Synthetic Institute','contact':'privacy@example.org','recipients':'Synthetic administrators',
                        'transfers':'None (synthetic test)','legitimate_interest':'Synthetic event planning','dpo':''}.items():
        page.locator(f'[name="privacy_{field}"]').fill(value)
    page.locator('[name="privacy_legal_basis"]').select_option('legitimate_interest')


def publish(page):
    page.get_by_role('button',name='Publish',exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.get_by_role('heading',name='Review and publish',exact=True)).to_be_visible()
    expect(page.locator('.survey-privacy')).to_be_visible()
    page.get_by_role('button',name='Publish poll',exact=True).click()
    page.wait_for_load_state('networkidle')
    expect(page.locator('[data-poll-share-link]')).to_be_visible()


def start_yaml(page):
    page.locator('[data-poll-new-toggle]').click()
    page.locator('[data-poll-start]').select_option('yaml')

"""Registration UI contracts on synthetic data; no mail or host operations."""
import csv
import json
import os

import pytest

from TESTS.webapp.test_editor_browser import browser_page, editor_client  # noqa: F401
from TESTS.webapp.test_registrations import registration_client, storage, EVENT, ID, FIELDS  # noqa: F401

pytestmark = pytest.mark.skipif(os.environ.get('MIFP_TEST_EDITOR_BROWSER') != '1', reason='Opt-in local Chromium')
BASE = '/dashboard/conference-sites/1/registrations'


@pytest.fixture
def registration_page(browser_page, editor_client, registration_client):
    page, _, origin = browser_page
    app, _ = editor_client
    source, _ = registration_client
    for key in ('DATABASE_PATH', 'CONFERENCES_DIR', 'REGISTRATIONS_PRIVATE_ROOT', 'ADMIN_PASSWORD_HASH'):
        app.config[key] = source.config[key]
    app.config['REGISTRATIONS_WRITE_ENABLED'] = True
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' and 'Failed to load resource' not in message.text else None)
    yield page, origin, app
    assert not errors


def test_many_long_records_filters_pagination_and_mobile_table(registration_page, storage, tmp_path):
    from playwright.sync_api import expect
    page, origin, _ = registration_page
    root, original = storage
    path = root/'registrations'/EVENT/'registrations.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for index in range(57):
            writer.writerow(dict(original, receipt_id=f'REG-20261009-{index:08X}',
                                 first_name='Alexandra ' * 8, last_name=f'Participant {index:02}',
                                 email='long-participant-address-' * 4 + '@example.invalid'))
    page.goto(origin + BASE + '?status=received&type=student&sort=last_name&order=asc')
    expect(page.get_by_text('57 matching / 57 saved.', exact=True)).to_be_visible()
    expect(page.locator('.stats-metric strong').first).to_have_text('57')
    expect(page.locator('.registrations-table tbody tr')).to_have_count(25)
    expect(page.get_by_text('Advanced filters & sorting', exact=True).locator('..')).to_have_attribute('open', '')
    for width in (320, 768, 1024, 1440):
        page.set_viewport_size(dict(width=width, height=900))
        page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('[aria-label="Registration table"]').evaluate('(e)=>e.scrollWidth <= e.clientWidth'), width
        expect(page.locator('.registrations-table td[data-label="Email notifications"]').first).to_be_visible()
        assert page.get_by_role('table').count() >= 1
        page.screenshot(path=str(tmp_path/f'registration-list-{width}.png'), full_page=True)
    page.get_by_role('link', name='Next', exact=True).click()
    expect(page.get_by_role('navigation', name='Registration pages')).to_contain_text('Page 2 / 3')
    assert 'sort=last_name' in page.url and 'type=student' in page.url
    expect(page.locator('.stats-metric strong').first).to_have_text('57')
    page.locator('input[name=q]').fill('no-matching-record')
    page.get_by_role('button', name='Apply filters', exact=True).click()
    expect(page.get_by_text('No matching registrations.', exact=True)).to_be_visible()
    expect(page.locator('.stats-metric strong').first).to_have_text('0')
    expect(page.get_by_text('0 matching / 57 saved.', exact=True)).to_be_visible()
    page.get_by_role('link', name='Reset', exact=True).click()
    expect(page.locator('.registrations-table tbody tr')).to_have_count(25)


def test_detail_metadata_navigation_and_keyboard_disclosure(registration_page, storage, tmp_path):
    from playwright.sync_api import expect
    page, origin, _ = registration_page
    root, _ = storage
    csv_path = root/'registrations'/EVENT/'registrations.csv'
    original = csv_path.read_bytes()
    page.goto(origin + BASE + '/' + ID)
    expect(page.get_by_role('heading', name='Administrative review')).to_be_visible()
    expect(page.get_by_role('link', name='Download payment receipt')).to_be_visible()
    for width in (320, 768, 1024, 1440):
        page.set_viewport_size(dict(width=width, height=900))
        page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path/f'registration-detail-{width}.png'), full_page=True)
    page.get_by_label('Status', exact=True).select_option('confirmed')
    page.get_by_label('Internal notes', exact=True).fill('Synthetic administrative note')
    page.get_by_role('button', name='Save administrative metadata', exact=True).click()
    expect(page.get_by_label('Status', exact=True)).to_have_value('confirmed')
    expect(page.get_by_role('heading', name='History')).to_be_visible()
    assert csv_path.read_bytes() == original
    # Native details and shared focus ring work without a disclosure script.
    summary = page.get_by_text('Delete this registration', exact=True)
    summary.focus()
    assert summary.evaluate('(e)=>getComputedStyle(e).outlineStyle') != 'none'
    page.keyboard.press('Enter')
    expect(page.get_by_label('Administrator password', exact=True)).to_be_visible()
    expect(page.locator('[data-registration-operation="mutation"] input[name="receipt"]')).to_have_value(ID)
    page.get_by_role('link', name='Back to registrations', exact=True).click()
    expect(page.get_by_role('link', name='Conference site', exact=True)).to_be_visible()
    expect(page.get_by_role('navigation', name='Breadcrumb')).to_be_visible()


@pytest.mark.parametrize('failure', ['password', 'csrf'])
def test_real_export_authorization_failure_keeps_list_and_clears_password(registration_page, failure):
    from playwright.sync_api import expect
    page, origin, _ = registration_page
    page.goto(origin + BASE)
    page.get_by_text('Export filtered registrations', exact=True).click()
    form = page.get_by_role('form', name='Registration export')
    form.get_by_label('Administrator password').fill('wrong' if failure == 'password' else 'synthetic-operation-password')
    if failure == 'csrf':
        form.locator('input[name="_csrf_token"]').evaluate('(e)=>e.value="invalid"')
    form.get_by_role('button', name='CSV', exact=True).click()
    expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'error')
    expect(form.get_by_label('Administrator password')).to_have_value('')
    expect(form.get_by_role('button', name='CSV', exact=True)).to_be_enabled()
    expect(page.locator('.registrations-table tbody tr')).to_have_count(1)


def test_unavailable_storage_is_distinct_from_empty_list(registration_page, tmp_path):
    from playwright.sync_api import expect
    page, origin, app = registration_page
    app.config['REGISTRATIONS_PRIVATE_ROOT'] = str(tmp_path/'missing-storage')
    response = page.goto(origin + BASE)
    assert response.status == 503
    expect(page.get_by_text('not_connected', exact=True)).to_be_visible()
    expect(page.get_by_text('No matching registrations.', exact=True)).to_have_count(0)
    expect(page.get_by_role('link', name='Try again', exact=True)).to_be_visible()
    expect(page.get_by_role('link', name='Back to conference site', exact=True)).to_be_visible()
    for width in (320, 768, 1440):
        page.set_viewport_size(dict(width=width, height=900))
        page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('claim', ['enabled', 'disabled', 'unknown'])
def test_php_package_facts_never_claim_host_authorization(registration_page, claim):
    from playwright.sync_api import expect
    from mifp_app.db.connection import connect
    page, origin, app = registration_page
    with connect(app.config['DATABASE_PATH']) as conn:
        conn.execute("UPDATE conference_sites SET deploy_status='published',package_manifest_json=? WHERE id=1",
                     (json.dumps(dict(php_files=5, regform_php_files=4, php_execution=claim)),))
        conn.commit()
    page.goto(origin + '/dashboard/conferences')
    expect(page.get_by_text('5 PHP files', exact=True)).to_be_visible()
    expect(page.get_by_text('Website published', exact=True)).to_be_visible()
    expect(page.get_by_text('PHP 5 / host status unavailable', exact=True)).to_have_count(0)
    assert 'PHP execution not verified' in page.locator('main').inner_text()
    assert 'PHP disabled' not in page.locator('main').inner_text()
    assert 'PHP enabled' not in page.locator('main').inner_text()


def test_shared_dashboard_pages_keep_layout_and_no_script_errors(registration_page):
    page, origin, _ = registration_page
    for path in ('conferences', 'events', 'stats', 'logs'):
        response = page.goto(origin + '/dashboard/' + path)
        assert response.status == 200
        for width in (768, 1440):
            page.set_viewport_size(dict(width=width, height=900))
            page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')

"""Opt-in real Chromium checks, using temporary state and no external services."""
from __future__ import annotations

import io
import os
import threading
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from TESTS.webapp.test_conference_editor import editor_client, authenticate  # noqa: F401
from mifp_app.routes.dashboard_editor import template_archive

pytestmark = pytest.mark.skipif(os.environ.get('MIFP_TEST_EDITOR_BROWSER') != '1', reason='Set MIFP_TEST_EDITOR_BROWSER=1 for isolated Chromium checks')


@pytest.fixture
def browser_page(editor_client):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    app, client = editor_client
    authenticate(client)
    cookie_name = app.config['SESSION_COOKIE_NAME']
    cookie = client.get_cookie(cookie_name)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies([dict(name=cookie_name, value=cookie.value, url=origin)])
            page = context.new_page()
            yield page, context, origin
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def synthetic_archive():
    output = io.BytesIO()
    with ZipFile(io.BytesIO(template_archive('conference-template.zip'))) as source, ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith('conference.yaml'):
                data = data.replace(b'NEW-CONFERENCE', b'SYNTHETIC-2027')
            archive.writestr(name.replace('TEMPLATE/', 'SYNTHETIC-2027/', 1), data)
    return output.getvalue()


def test_open_export_preview_and_asset_security(browser_page, tmp_path):
    from playwright.sync_api import expect
    page, context, origin = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/dashboard/conference-editor/')
    page.wait_for_load_state('networkidle')
    expect(page.locator('#saveState')).to_contain_text('No project')
    page.locator('#projectZipInput').set_input_files(dict(name='SYNTHETIC-2027_WEBSITE.zip', mimeType='application/zip', buffer=synthetic_archive()))
    expect(page.locator('#previewSiteBtn')).to_be_enabled()
    with page.expect_download() as download:
        page.locator('#exportZipBtn').click()
    from mifp_app.services.event_import import inspect_website, inspect_info
    website = tmp_path / 'website.zip'
    download.value.save_as(website)
    assert inspect_website(website, 'SYNTHETIC-2027_WEBSITE.zip').kind == 'website'
    with ZipFile(website) as archive:
        assert any(name.endswith('regform/src/bootstrap.php') for name in archive.namelist())
        assert not any('registrations.csv' in name for name in archive.namelist())
    with page.expect_download() as download:
        page.locator('#exportMifpZipTopBtn').click()
    info = tmp_path / 'info.zip'
    download.value.save_as(info)
    assert inspect_info(info, 'SYNTHETIC-2027_INFO.zip').kind == 'info'
    with ZipFile(info) as archive:
        assert 'manifest.json' in archive.namelist()
        assert any(name.endswith('.jsonl') for name in archive.namelist())
        assert not any(name.endswith('.php') for name in archive.namelist())
    with page.expect_popup() as popup:
        page.locator('#previewSiteBtn').click()
    preview = popup.value
    preview.wait_for_selector('iframe')
    expect(preview.locator('iframe')).to_have_attribute('src', __import__('re').compile(r'^blob:'))
    expect(preview.frame_locator('iframe').locator('.hero')).to_contain_text('SYNTHETIC-2027', timeout=10000)
    frame = preview.locator('iframe').element_handle().content_frame()
    assert frame.evaluate("(()=>{try{void parent.document;return false}catch(_){return true}})()")
    # Even code executing in the imported page cannot use the admin origin.
    assert frame.evaluate("async()=>{try{await fetch('/dashboard/',{credentials:'include'});return false}catch(_){return true}}")
    for width in (320, 768, 1024, 1440):
        page.set_viewport_size(dict(width=width, height=900))
        expect(page.locator('#newConferenceBtn')).to_be_visible()
    page.screenshot(path=str(tmp_path / 'editor.png'), full_page=True)
    assert not errors


def test_new_conference_uses_canonical_scaffold_and_local_folder_api(browser_page):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    page.add_init_script('''
    function directory(name) {
      const children=new Map();
      return {kind:'directory',name,
        queryPermission:async()=> 'granted',requestPermission:async()=> 'granted',
        async *entries(){yield* children.entries()},
        async getDirectoryHandle(name,options={}){if(!children.has(name)){if(!options.create)throw new DOMException('Missing','NotFoundError');children.set(name,directory(name));}return children.get(name)},
        async getFileHandle(name,options={}){if(!children.has(name)){if(!options.create)throw new DOMException('Missing','NotFoundError');let blob=new Blob([]);children.set(name,{kind:'file',name,getFile:async()=>new File([blob],name),createWritable:async()=>({write:async data=>{blob=new Blob([data])},close:async()=>{}})});}return children.get(name)},
        async removeEntry(name){children.delete(name)}
      };
    }
    const root=directory('synthetic-workspace');
    window.showDirectoryPicker=async()=>root;
    ''')
    page.goto(origin + '/dashboard/conference-editor/')
    page.wait_for_load_state('networkidle')
    page.locator('#newConferenceBtn').click()
    page.locator('#totemInput').fill('SYNTHETIC-2028')
    page.locator('#totemConfirmBtn').click()
    expect(page.locator('#previewSiteBtn')).to_be_enabled(timeout=15000)
    expect(page.locator('#projectName')).to_contain_text('SYNTHETIC-2028')
    with page.expect_download() as download:
        page.locator('#exportZipBtn').click()
    assert download.value.suggested_filename == 'SYNTHETIC-2028_WEBSITE.zip'


def test_document_and_spreadsheet_tools_in_flask(browser_page):
    page, _, origin = browser_page
    page.goto(origin + '/dashboard/conference-editor/')
    page.wait_for_load_state('networkidle')
    result = page.evaluate('''async()=>{
      const person={'First Name':'Synthetic','Last Name':'Example',Affiliation:'Synthetic institute',Country:'Synthetic'};
      const {pages}=DocumentTools.renderBadgePages([person],{scale:1},{shortName:'Synthetic conference'});
      const certificates=DocumentTools.renderCertificatePages([person],{scale:1},{shortName:'Synthetic conference'});
      if(certificates.length!==1)throw new Error('Certificate rendering failed');
      const pdf=await DocumentTools.buildPdfFromCanvases(pages);
      const docx=await DocumentTools.buildDocxFromCanvases(pages);
      const xlsx=await XlsxLite.build([person],Object.keys(person),{sheetName:'People'});
      const parsed=await XlsxLite.parse(xlsx);
      const csv=CsvUtil.serialize([person],Object.keys(person));
      const templates=[];
      for(const name of ['badges_template.docx','certificate_template.docx']){
        const source=await (await fetch('templates/'+name)).blob();
        const filled=await DocumentTools.fillDocxTemplate(source,{'{{NAME}}':'Synthetic Example'},{});
        templates.push(filled.size);
      }
      return {pdf:await pdf.slice(0,8).text(),docx:Array.from(new Uint8Array(await docx.arrayBuffer())),parsed,csv,templates,pages:pages.length};
    }''')
    assert result['pdf'].startswith('%PDF-') and result['pages'] == 1
    with ZipFile(io.BytesIO(bytes(result['docx']))) as archive:
        assert 'word/document.xml' in archive.namelist()
        assert archive.testzip() is None
    assert 'Synthetic' in str(result['parsed']) and 'Synthetic' in result['csv']
    assert all(size > 1000 for size in result['templates'])


from TESTS.webapp.test_registrations import registration_client, storage  # noqa: E402,F401


def test_registration_table_charts_and_filters_are_responsive(browser_page, editor_client, registration_client, tmp_path):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    app, _ = editor_client
    registration_app, _ = registration_client
    for key in ('DATABASE_PATH','CONFERENCES_DIR','REGISTRATIONS_PRIVATE_ROOT','ADMIN_PASSWORD_HASH'):
        app.config[key] = registration_app.config[key]
    app.config['REGISTRATIONS_WRITE_ENABLED'] = True
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/dashboard/conference-sites/1/registrations')
    page.wait_for_load_state('networkidle')
    expect(page.locator('canvas[data-chart-ready="1"]')).to_have_count(5)
    assert page.locator('.registrations-table tbody td').first.evaluate('(e)=>getComputedStyle(e).whiteSpace') == 'normal'
    for width in (320,768,1024,1440):
        page.set_viewport_size(dict(width=width,height=1000))
        expect(page.get_by_role('button',name='Apply filters')).to_be_visible()
        # The shared sidebar animates after crossing its responsive breakpoint.
        page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')
        page.evaluate('()=>window.scrollTo(0,0)')
        page.screenshot(path=str(tmp_path/f'registrations-{width}.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        if width == 320:
            assert page.locator('.data-scroll').evaluate('(e)=>e.scrollWidth>e.clientWidth')
        operations = page.locator('.registrations-operations').filter(has=page.get_by_text('Delete registrations or recover notifications', exact=True))
        operations.locator('summary').click()
        expect(page.get_by_role('button', name='Request host approval')).to_be_visible()
        for control in ('input[name="password"]', 'select[name="channel"]', 'input[name="confirm"]'):
            assert operations.locator(control).evaluate('(e)=>e.getBoundingClientRect().width >= 150'), width
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        operations.locator('summary').click()
    page.screenshot(path=str(tmp_path/'registrations.png'),full_page=True)
    page.get_by_text('Export filtered registrations', exact=True).click()
    export = page.get_by_role('form', name='Registration export')
    export.get_by_label('Administrator password').fill('synthetic-operation-password')
    with page.expect_download() as result:
        export.get_by_role('button', name='CSV', exact=True).click()
    download = result.value
    assert download.suggested_filename.endswith('.csv')
    from pathlib import Path
    assert b'REG-20261009-AABBCCDD' in Path(download.path()).read_bytes()
    expect(export.get_by_role('button', name='CSV', exact=True)).not_to_have_class('is-loading')
    expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'success')
    for width in (320, 1440):
        page.set_viewport_size(dict(width=width, height=1000))
        page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path/f'registration-totem-{width}.png'), full_page=True)
    page.locator('input[name="q"]').fill('no-matching-record')
    page.get_by_role('button',name='Apply filters').click()
    expect(page.get_by_text('No matching registrations.')).to_be_visible()
    assert not errors


def test_editor_console_and_log_panel_redact_sensitive_details(browser_page):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    messages = []
    page.on('console', lambda message: messages.append((message.type, message.text)))
    page.goto(origin + '/dashboard/conference-editor/')
    page.locator('#projectZipInput').set_input_files(dict(
        name='sensitive@example.invalid.zip', mimeType='application/zip', buffer=b'not a zip'))
    page.locator('[data-view="logs"]').click()
    expect(page.locator('#logList')).to_contain_text('workspace.zip_import_failed')
    assert 'sensitive@example.invalid' not in page.locator('#logList').inner_text()
    page.evaluate("""()=>{
      MIFPLog.info('editor.test_info', {count: 1});
      MIFPLog.warn('editor.test_warning', {email:'person@example.invalid',token:'secret-token',message:'Bearer secret-auth'});
      dispatchEvent(new ErrorEvent('error',{message:'synthetic failure token=secret-error',filename:location.origin+'/synthetic.js'}));
    }""")
    output = '\n'.join(text for _, text in messages)
    assert 'editor.test_info' not in output  # Production console threshold.
    assert 'editor.test_warning' in output and 'javascript.error' in output
    assert 'editor.workspace.zip_import_failed' in output
    assert all(secret not in output for secret in (
        'sensitive@example.invalid','person@example.invalid','secret-token','secret-auth','secret-error'))


@pytest.mark.parametrize('failure', ['missing', 'render'])
def test_chart_failures_log_safe_events_and_keep_a_readable_page(browser_page, editor_client, registration_client, failure):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    app, _ = editor_client
    registration_app, _ = registration_client
    for key in ('DATABASE_PATH', 'CONFERENCES_DIR', 'REGISTRATIONS_PRIVATE_ROOT'):
        app.config[key] = registration_app.config[key]
    messages = []
    page.on('console', lambda message: messages.append(message.text))
    script = ('// synthetic missing Chart.js' if failure == 'missing' else
              "window.Chart=class {static getChart(){return null} constructor(){throw new Error('private@example.invalid token=private-token')}};")
    page.route('**/chart.umd.js', lambda route: route.fulfill(content_type='application/javascript', body=script))
    page.goto(origin + '/dashboard/conference-sites/1/registrations')
    expect(page.locator('.chart-fallback')).to_have_count(5)
    expect(page.get_by_role('button', name='Apply filters')).to_be_visible()
    output = '\n'.join(messages)
    expected = 'charts.library_unavailable' if failure == 'missing' else 'charts.render_failed'
    assert expected in output
    assert 'private@example.invalid' not in output and 'private-token' not in output


@pytest.mark.skipif(not os.environ.get('MIFP_TEST_EDITOR_PROJECT'), reason='Optional authorized local conference source')
@pytest.mark.parametrize('mode', ['native', 'compatible'])
def test_open_authorized_local_conference_folder(browser_page, tmp_path, mode):
    """Read-only native-folder simulation; never copies private submissions."""
    import base64
    import json
    from pathlib import Path
    from playwright.sync_api import expect
    root = Path(os.environ['MIFP_TEST_EDITOR_PROJECT']).resolve()
    files = {p.relative_to(root).as_posix(): p for p in root.rglob('*')
             if p.is_file() and not p.is_symlink()
             and not any(part in {'registrations','uploads','.git','__pycache__'} for part in p.relative_to(root).parts)}
    page, _, origin = browser_page
    page.expose_function('readAuthorizedFile', lambda path: base64.b64encode(files[path].read_bytes()).decode())
    page.add_init_script('''
      const files = new Set(FILES);
      function directory(prefix,name){return {kind:'directory',name,
        queryPermission:async()=> 'granted',requestPermission:async()=> 'granted',
        async getFileHandle(name){const path=prefix+name;if(!files.has(path))throw new DOMException('Missing','NotFoundError');return {kind:'file',name,getFile:async()=>new File([Uint8Array.from(atob(await readAuthorizedFile(path)),c=>c.charCodeAt(0))],name)}} ,
        async getDirectoryHandle(name){const next=prefix+name+'/';if(![...files].some(p=>p.startsWith(next)))throw new DOMException('Missing','NotFoundError');return directory(next,name)},
        async *entries(){const names=new Set([...files].filter(p=>p.startsWith(prefix)).map(p=>p.slice(prefix.length).split('/')[0]));for(const name of names){yield [name,files.has(prefix+name)?await this.getFileHandle(name):await this.getDirectoryHandle(name)]}}
      }};
      window.showDirectoryPicker=async()=>directory('',NAME);
    '''.replace('FILES',json.dumps(list(files))).replace('NAME',json.dumps(root.name)))
    page.goto(origin + '/dashboard/conference-editor/')
    page.wait_for_load_state('networkidle')
    if mode == 'native':
        page.locator('#openWorkspaceBtn').click()
    else:
        import shutil
        copy = tmp_path/root.name
        for path, source in files.items():
            destination = copy/path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        with page.expect_file_chooser() as chooser:
            page.locator('#openFolderCopyBtn').click()
        chooser.value.set_files(copy)
    try:
        expect(page.locator('#previewSiteBtn')).to_be_enabled(timeout=15000)
    except AssertionError:
        page.locator('[data-view="logs"]').click()
        # The Editor logger redacts details before they enter this panel.
        pytest.fail(page.locator('#logList').inner_text())
    expect(page.locator('#projectName')).to_contain_text(root.name)
    if mode == 'compatible':
        with page.expect_popup() as popup:
            page.locator('#previewSiteBtn').click()
        preview = popup.value
        link = preview.frame_locator('iframe').get_by_role('link', name='Download Template', exact=True)
        expect(link).to_be_visible(timeout=15000)
        expect(preview.frame_locator('iframe').locator('.submission-template .resource-badge')).to_have_text('DOC')
        with preview.expect_download() as download:
            link.click()
        document = tmp_path/'downloaded-template.doc'
        download.value.save_as(document)
        assert document.read_bytes() == (root/'assets/documents/PLMCN-2027_Abstract_Template.doc').read_bytes()


@pytest.mark.parametrize('failure', ['cancelled', 'denied', 'empty'])
def test_native_folder_errors_are_visible_and_keep_the_open_project(browser_page, failure):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    page.goto(origin + '/dashboard/conference-editor/')
    page.locator('#projectZipInput').set_input_files(dict(
        name='SYNTHETIC-2027_WEBSITE.zip', mimeType='application/zip', buffer=synthetic_archive()))
    expect(page.locator('#previewSiteBtn')).to_be_enabled()
    page.evaluate('''failure=>{window.showDirectoryPicker=async()=>{
      if(failure==='cancelled')throw new DOMException('Selection cancelled','AbortError');
      return {name:'unavailable',getFileHandle:async()=>{throw new DOMException('No access',failure==='denied'?'NotAllowedError':'NotFoundError')},async *entries(){}};
    }}''', failure)
    page.locator('#openWorkspaceBtn').click()
    page.locator('[data-view="logs"]').click()
    event = 'workspace.picker_cancelled' if failure == 'cancelled' else 'workspace.open_failed'
    expect(page.locator('#logList')).to_contain_text(event)
    expect(page.locator('#projectName')).to_contain_text('SYNTHETIC-2027')
    expect(page.locator('#previewSiteBtn')).to_be_enabled()


@pytest.mark.parametrize('status', [200, 403, 503, 0])
def test_registration_export_totem_waits_and_recovers(browser_page, editor_client, registration_client, status):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    app, _ = editor_client
    registration_app, _ = registration_client
    for key in ('DATABASE_PATH', 'CONFERENCES_DIR', 'REGISTRATIONS_PRIVATE_ROOT', 'ADMIN_PASSWORD_HASH'):
        app.config[key] = registration_app.config[key]
    page.goto(origin + '/dashboard/conference-sites/1/registrations')
    page.wait_for_load_state('networkidle')
    page.get_by_text('Export filtered registrations', exact=True).click()
    form = page.get_by_role('form', name='Registration export')
    password = form.get_by_label('Administrator password')
    password.fill('synthetic-operation-password')
    pending = []
    page.route('**/registrations-export/csv*', lambda route: pending.append(route))
    form.get_by_role('button', name='CSV', exact=True).click()
    expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'working')
    bar = page.locator('#registrationOperation [role="progressbar"]')
    expect(bar).not_to_have_attribute('aria-valuenow', '0')
    expect(bar).to_have_class(__import__('re').compile('is-loading'))
    expect(form.get_by_role('button', name='CSV', exact=True)).to_be_disabled()
    page.wait_for_function('()=>document.querySelector("#registrationOperation").getAttribute("aria-busy")==="true"')
    assert len(pending) == 1
    headers = {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename="synthetic.csv"'} if status == 200 else {}
    if status == 200:
        with page.expect_download() as result:
            pending[0].fulfill(status=status, headers=headers, body='receipt_id\nSYNTHETIC\n')
        assert result.value.suggested_filename == 'synthetic.csv'
        expect(bar).to_have_attribute('aria-valuenow', '100')
        expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'success')
    else:
        if status:
            pending[0].fulfill(status=status, headers=headers, body='Private server details must not be displayed')
        else:
            pending[0].abort('failed')
        expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'error')
        expect(page.locator('#registrationOperationMeta')).not_to_contain_text('Private server details')
    expect(form.get_by_role('button', name='CSV', exact=True)).to_be_enabled()
    expect(password).to_have_value('')
    expect(page.locator('#registrationOperation')).to_have_attribute('aria-busy', 'false')
    expect(bar).not_to_have_class(__import__('re').compile('is-loading'))


@pytest.mark.parametrize('detail', [False, True])
def test_registration_deletion_totem_does_not_repeat_request(browser_page, editor_client, registration_client, detail):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    app, _ = editor_client
    registration_app, _ = registration_client
    for key in ('DATABASE_PATH', 'CONFERENCES_DIR', 'REGISTRATIONS_PRIVATE_ROOT', 'ADMIN_PASSWORD_HASH'):
        app.config[key] = registration_app.config[key]
    app.config['REGISTRATIONS_WRITE_ENABLED'] = True
    path = '/dashboard/conference-sites/1/registrations'
    if detail:
        path += '/REG-20261009-AABBCCDD'
    page.goto(origin + path)
    page.wait_for_load_state('networkidle')
    page.get_by_text('Delete this registration' if detail else 'Delete registrations or recover notifications', exact=True).click()
    form = page.locator('form[data-registration-operation="mutation"]')
    form.locator('input[name="password"]').fill('synthetic-operation-password')
    form.locator('input[name="confirm"]').fill('SYNTHETIC-2027')
    if not detail:
        form.locator('input[name="receipt"]').first.check()
    # Stop navigation after the application handler: inspect submission state
    # without holding a browser navigation open or mutating registration data.
    page.evaluate('''()=>{
      window.registrationSubmitChecks=[];
      document.addEventListener('submit', event=>{
        if(!event.target.matches('[data-registration-operation=mutation]'))return;
        window.registrationSubmitChecks.push({
          blocked:event.defaultPrevented,
          receipts:new FormData(event.target).getAll('receipt')
        });
        event.preventDefault();
      });
    }''')
    form.get_by_role('button', name='Delete registration', exact=detail).click()
    expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'working')
    expect(page.locator('#registrationOperation [role="progressbar"]')).to_have_class(__import__('re').compile('is-loading'))
    form.evaluate('(form)=>form.requestSubmit(form.querySelector("button[type=submit]"))')
    checks = page.evaluate('window.registrationSubmitChecks')
    assert len(checks) == 2
    assert checks[0] == dict(blocked=False, receipts=['REG-20261009-AABBCCDD'])
    assert checks[1]['blocked'] is True


from TESTS.webapp.test_registration_operations import deletion_client  # noqa: E402,F401


def test_registration_zip_download_and_import_from_dashboard(browser_page, editor_client, deletion_client, tmp_path):
    from playwright.sync_api import expect
    from mifp_app.services.registrations import read_records
    from pathlib import Path
    page, _, origin = browser_page
    app, _ = editor_client
    registration_app, _, alerts = deletion_client
    for key in ('DATABASE_PATH', 'CONFERENCES_DIR', 'REGISTRATIONS_PRIVATE_ROOT', 'ADMIN_PASSWORD_HASH', 'MAIL_PROVIDER'):
        app.config[key] = registration_app.config[key]
    app.config['REGISTRATIONS_WRITE_ENABLED'] = True
    page.goto(origin + '/dashboard/conference-sites/1/registrations')
    page.wait_for_load_state('networkidle')
    page.get_by_text('Export filtered registrations', exact=True).click()
    export = page.get_by_role('form', name='Registration export')
    export.get_by_label('Administrator password').fill('synthetic-operation-password')
    with page.expect_download() as result:
        export.get_by_role('button', name='Recovery ZIP + originals', exact=True).click()
    archive = tmp_path/'registrations.zip'
    result.value.save_as(archive)
    expect(page.locator('#registrationOperationTotem')).to_have_attribute('data-state', 'success')
    page.locator('summary').filter(has_text='Import registrations').click()
    form = page.get_by_role('form', name='Registration import')
    for width in (320, 768, 1024, 1440):
        page.set_viewport_size(dict(width=width, height=1000))
        page.wait_for_function('()=>document.documentElement.scrollWidth <= innerWidth')
        expect(form.get_by_role('button', name='Import registrations', exact=True)).to_be_visible()
    page.screenshot(path=str(tmp_path/'registration-import.png'), full_page=True)
    form.locator('input[type=file]').set_input_files(archive)
    form.get_by_label('Administrator password').fill('synthetic-operation-password')
    form.locator('input[name=confirm]').fill('SYNTHETIC-2027')
    form.get_by_role('button', name='Import registrations', exact=True).click()
    expect(page.get_by_text('Imported 0 registrations; skipped 1 identical records; restored 0 receipts. No participant emails were sent.', exact=True)).to_be_visible()
    assert len(read_records(Path(app.config['REGISTRATIONS_PRIVATE_ROOT']), 'SYNTHETIC-2027')) == 1
    assert [alert['event'] for alert in alerts] == ['registrations.import_authorized', 'registrations.import_completed']



def test_divergent_runtime_review_and_export_preserve_customizations(browser_page, tmp_path):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    output = io.BytesIO()
    custom = b"<?php // intentional synthetic customization\n"
    with ZipFile(io.BytesIO(synthetic_archive())) as source, ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        settings = source.read('SYNTHETIC-2027/regform/settings.yaml')
        for name in source.namelist():
            archive.writestr(name, custom if name.endswith('regform/src/mailer.php') else source.read(name))
    page.goto(origin + '/dashboard/conference-editor/')
    page.locator('#projectZipInput').set_input_files(dict(name='synthetic.zip', mimeType='application/zip', buffer=output.getvalue()))
    expect(page.locator('#exportZipBtn')).to_be_enabled()
    page.locator('[data-view="regform"]').click()
    dialogs = []
    def accept(dialog):
        dialogs.append(dialog.message)
        dialog.accept()
    page.on('dialog', accept)
    with page.expect_download() as download:
        page.get_by_role('button', name='Review RegForm runtime', exact=True).click()
    review = tmp_path/'review.zip'
    download.value.save_as(review)
    with ZipFile(review) as archive:
        assert set(archive.namelist()) == {'current/regform/src/mailer.php', 'canonical/regform/src/mailer.php'}
        assert archive.read('current/regform/src/mailer.php') == custom
        assert archive.read('canonical/regform/src/mailer.php') != custom
    assert 'possibly intentional customizations' in dialogs[-1]
    # Save All must not turn a comparison into an edit.
    page.locator('#saveAllBtn').click()
    with page.expect_download() as download:
        page.locator('#exportZipBtn').click()
    website = tmp_path/'website.zip'
    download.value.save_as(website)
    assert 'Export these project files unchanged?' in dialogs[-1]
    with ZipFile(website) as archive:
        assert archive.read('SYNTHETIC-2027/regform/src/mailer.php') == custom
        assert archive.read('SYNTHETIC-2027/regform/settings.yaml') == settings
    # Reopening the exported state still detects exactly the same customization.
    page.locator('#projectZipInput').set_input_files(website)
    page.locator('[data-view="regform"]').click()
    with page.expect_download() as download:
        page.get_by_role('button', name='Review RegForm runtime', exact=True).click()
    assert download.value.suggested_filename == 'regform-runtime-review.zip'


def test_import_wizard_warns_for_host_only_destination(browser_page, editor_client, tmp_path):
    from playwright.sync_api import expect
    from TESTS.webapp.test_event_import_wizard import _website
    app, _ = editor_client
    app.config['EVENTS_PUBLISH_BACKEND'] = 'local'
    app.config['EVENTS_LOCAL_ROOT'] = str(tmp_path/'public')
    site = tmp_path/'public'/'BROWSER-2027'
    site.mkdir(parents=True)
    (site/'index.html').write_text('previous website')
    page, _, origin = browser_page
    page.goto(origin + '/dashboard/conferences/import')
    page.locator('[name="website_package"]').set_input_files(dict(
        name='browser_WEBSITE.zip', mimeType='application/zip', buffer=_website(root='BROWSER-2027')))
    page.locator('[data-upload-form] button[type=submit]').click()
    page.locator('[data-wizard-panel="2"] [data-wizard-next]').click()
    expect(page.locator('[data-destination-warning]')).to_contain_text('Destination already exists')
    expect(page.locator('[data-destination-warning]')).to_contain_text('does not delete its public website')
    page.locator('[data-wizard-panel="3"] [data-wizard-next]').click()
    expect(page.locator('[name="existing_mode"]')).to_have_value('reject')
    page.locator('[name="existing_mode"]').select_option('replace')
    expect(page.locator('[name="existing_mode"]')).to_have_value('replace')
    expect(page.locator('[data-wizard-panel="4"]')).to_contain_text('must revoke approved RegForm PHP')
    assert (site/'index.html').read_text() == 'previous website'


def test_offline_runtime_comparison_requires_explicit_unverified_export(browser_page, tmp_path):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    page.goto(origin + '/dashboard/conference-editor/')
    page.locator('#projectZipInput').set_input_files(dict(name='synthetic.zip', mimeType='application/zip', buffer=synthetic_archive()))
    expect(page.locator('#exportZipBtn')).to_be_enabled()
    page.route('**/templates/conference-template.zip', lambda route: route.abort())
    messages = []
    def accept(dialog):
        messages.append(dialog.message)
        dialog.accept()
    page.on('dialog', accept)
    with page.expect_download() as download:
        page.locator('#exportZipBtn').click()
    assert 'Export current project files without runtime verification?' in messages[-1]
    target = tmp_path/'website.zip'
    download.value.save_as(target)
    with ZipFile(target) as archive, ZipFile(io.BytesIO(synthetic_archive())) as source:
        assert archive.read('SYNTHETIC-2027/regform/src/bootstrap.php') == source.read('SYNTHETIC-2027/regform/src/bootstrap.php')



def test_content_check_detects_broken_document_reference(browser_page):
    from playwright.sync_api import expect
    page, _, origin = browser_page
    output = io.BytesIO()
    with ZipFile(io.BytesIO(synthetic_archive())) as source, ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith('conference.yaml'):
                data = __import__('re').sub(rb'(?m)^  template_file:.*$', b'  template_file: assets/documents/missing-template.docx', data)
            archive.writestr(name, data)
    page.goto(origin + '/dashboard/conference-editor/')
    page.locator('#projectZipInput').set_input_files(dict(name='synthetic.zip', mimeType='application/zip', buffer=output.getvalue()))
    expect(page.locator('#exportZipBtn')).to_be_enabled()
    page.locator('[data-view="checks"]').click()
    expect(page.locator('.check-list')).to_contain_text('Missing asset')
    expect(page.locator('.check-list')).to_contain_text('assets/documents/missing-template.docx')

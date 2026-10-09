from __future__ import annotations

import io
import re
from pathlib import Path
from zipfile import ZipFile

import pytest

from mifp_app import create_app
from mifp_app.routes.dashboard_editor import EDITOR_ROOT, template_archive


@pytest.fixture
def editor_client():
    app = create_app()
    app.config.update(TESTING=True)
    return app, app.test_client()


def authenticate(client):
    with client.session_transaction() as session:
        session['admin_logged_in'] = True
        session['admin_username'] = 'synthetic-editor'


@pytest.mark.parametrize('path', ['', 'assets/js/editor/app.js', 'templates/conference-template.zip',
                                  'templates/badges_template.docx', 'preview/'])
def test_editor_and_all_resources_require_authentication(editor_client, path):
    _, client = editor_client
    response = client.get('/dashboard/conference-editor/' + path)
    assert response.status_code in (302, 401)
    assert 'no-store' in response.headers['Cache-Control']


def test_editor_loads_local_assets_with_nonce_and_denies_source_files(editor_client):
    _, client = editor_client
    authenticate(client)
    response = client.get('/dashboard/conference-editor/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'github.io' not in html
    assert re.search(r'<script nonce="[^"]+">', html)
    assert '/static/js/logger.js' in html
    assert 'data-mifp-log-level="warn"' in html
    for path in re.findall(r'(?:src|href)="\./([^"?]+)', html):
        asset = client.get('/dashboard/conference-editor/' + path)
        assert asset.status_code == 200, path
        assert 'no-store' in asset.headers['Cache-Control']
    for path in ['TEMPLATE/regform/src/bootstrap.php', 'assets/../TEMPLATE/regform/src/bootstrap.php', 'LICENSE', '../TEMPLATE/conference.yaml',
                 'templates/unknown.zip', 'document_templates/badges_template/[Content_Types].xml']:
        assert client.get('/dashboard/conference-editor/' + path).status_code == 404
    preview = client.get('/dashboard/conference-editor/preview/')
    assert 'sandbox="allow-scripts allow-downloads allow-modals"' in preview.text
    assert 'allow-same-origin' not in preview.text
    assert "connect-src blob:" in preview.headers['Content-Security-Policy']
    assert "'unsafe-inline'" not in response.headers['Content-Security-Policy'].split('script-src')[1].split(';')[0]


def test_generated_scaffold_contains_exact_canonical_files_and_closed_form():
    with ZipFile(io.BytesIO(template_archive('conference-template.zip'))) as archive:
        expected = {p.relative_to(EDITOR_ROOT).as_posix(): p.read_bytes()
                    for p in (EDITOR_ROOT / 'TEMPLATE').rglob('*') if p.is_file()}
        assert set(archive.namelist()) == set(expected)
        for name, content in expected.items():
            assert archive.read(name) == content
        assert b'submit_enabled: false' in archive.read('TEMPLATE/regform/settings.yaml')
        assert b'MIFP_REGISTRATION_READ_GROUP' in archive.read('TEMPLATE/regform/src/bootstrap.php')
    for name in ('badges_template.docx', 'certificate_template.docx'):
        with ZipFile(io.BytesIO(template_archive(name))) as archive:
            assert archive.testzip() is None
            assert '[Content_Types].xml' in archive.namelist()
            assert 'word/document.xml' in archive.namelist()


from __future__ import annotations

import io
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def members_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP/CORE/mifp_app/db/schema.sql"
    conn.executescript(schema.read_text())
    conn.execute("INSERT INTO roles(id,name,label) VALUES(1,'member','Member')")
    conn.execute("INSERT INTO roles(id,name,label) VALUES(2,'fellow','Fellow')")
    cases = [(1, 'published'), (0, 'published'), (1, 'draft'),
             (1, 'review'), (1, 'quarantined'), (1, 'duplicate')]
    for index, (active, status) in enumerate(cases, 1):
        conn.execute(
            "INSERT INTO members(id,display_name,country,role_id,is_active,review_status) VALUES(?,?,?,?,?,?)",
            (index, f"Scientist {index}", f"Country {index}", 1, active, status),
        )
    conn.execute("INSERT INTO members(id,display_name,country,role_id) VALUES(7,'Scientist 7','Country 1',2)")
    yield conn
    conn.close()


def test_public_member_states_counts_pagination_search_and_roles(members_db):
    from mifp_app.services.public_repository import get_home_context, list_members_page

    def page(search=None, role=None, number=1, size=20):
        return list_members_page(members_db, str, search, role, number, size)

    assert {r['id'] for r in page()['members']} == {1, 7}
    assert page(size=1)['total'] == 2
    assert page(size=1)['total_pages'] == 2
    assert len(page(number=2, size=1)['members']) == 1
    assert page(number=3, size=1)['members'] == []
    assert [r['id'] for r in page(role='member')['members']] == [1]
    assert page(search='Scientist 3')['total'] == 0
    assert page(search='Scientist')['total'] == 2
    assert {r['name']: r['member_count'] for r in page()['roles']} == {'member': 1, 'fellow': 1}
    context = get_home_context(members_db, str)
    assert context['member_count'] == 2
    assert context['country_count'] == 1


def test_dashboard_member_states_are_preserved(members_db):
    from mifp_app.services.dashboard_repository import list_records_paginated

    result = list_records_paginated(members_db, 'members')
    assert result['total'] == 7
    assert {r['id'] for r in result['records']} == set(range(1, 8))


def test_public_and_dashboard_global_member_search(app, members_db):
    from mifp_app.services.search import run_search

    with app.test_request_context('/'):
        public = run_search(members_db, 'Scientist', scope='public')
        admin = run_search(members_db, 'Scientist', scope='dashboard')
    assert {r['title'] for r in public['results']} == {'Scientist 1', 'Scientist 7'}
    assert len(admin['results']) == 7


def test_about_and_research_statistics_exclude_unpublished_members(app, members_db, monkeypatch):
    from flask import jsonify
    from mifp_app.routes import public

    with sqlite3.connect(app.config['DATABASE_PATH']) as conn:
        conn.execute("INSERT OR IGNORE INTO roles(id,name,label) VALUES(1,'member','Member')")
        conn.execute("INSERT OR IGNORE INTO roles(id,name,label) VALUES(2,'fellow','Fellow')")
        for row in members_db.execute('SELECT * FROM members'):
            conn.execute(
                'INSERT INTO members(display_name,country,role_id,is_active,review_status) VALUES(?,?,?,?,?)',
                tuple(row[key] for key in ('display_name', 'country', 'role_id', 'is_active', 'review_status')),
            )
    monkeypatch.setattr(public, 'render_template', lambda template, **context: jsonify(context))
    client = app.test_client()
    assert client.get('/about').json['stats']['members'] == 2
    research = client.get('/research').json
    assert research['research_stats']['active_members'] == 2
    assert research['research_stats']['countries'] == 1
    assert research['members_by_country'] == [{'country': 'Country 1', 'total': 2}]


def test_markdown_unavailable_escapes_html(monkeypatch):
    from mifp_app.routes.public import _markdown_html

    monkeypatch.setitem(sys.modules, 'markdown', None)
    assert _markdown_html('<script>alert(1)</script><img src=x onerror=alert(1)>') == (
        '&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;'
    )
    assert _markdown_html(None) is None
    assert _markdown_html('') is None


def test_markdown_available_renders_and_sanitizes():
    from mifp_app.routes.public import _markdown_html

    html = _markdown_html('**Hello** <script>alert(1)</script><img src=x onerror=alert(1)>')
    assert '<strong>Hello</strong>' in html
    assert '<script' not in html
    assert 'onerror' not in html


@pytest.fixture
def app(tmp_path, monkeypatch):
    from mifp_app import create_app
    from mifp_app.config import Config
    from mifp_app.db.manage import init_database

    for key, name in [('DATABASE_PATH', 'test.db'), ('ASSETS_DIR', 'assets'),
                      ('EXPORT_DIR', 'exports'), ('LOG_DIR', 'logs'),
                      ('RUNTIME_CONFIG_DIR', 'config')]:
        monkeypatch.setattr(Config, key, tmp_path / name)
    init_database(Config.DATABASE_PATH)
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, SECRET_KEY='test-only-hardening',
                      ADMIN_USERNAME='admin', ADMIN_PASSWORD_HASH=generate_password_hash('password'),
                      MAX_CONTENT_LENGTH=2 * 1024 * 1024)

    # Exercise the real hooks with a synthetic dashboard upload sink, without
    # importing data or changing persistent state.
    from flask import jsonify, request

    def upload_probe():
        uploaded = request.files['file']
        return jsonify(size=len(uploaded.read()))

    app.add_url_rule('/dashboard/upload-probe', endpoint='dashboard.upload_probe',
                     view_func=upload_probe, methods=['POST'])
    return app


def _token(client, path='/login'):
    page = client.get(path)
    assert page.status_code == 200
    return re.search(rb'name="_csrf_token" value="([^"]+)"', page.data).group(1).decode()


@pytest.mark.parametrize('path', ['/login', '/join', '/logout', '/dashboard/upload-probe', '/missing'])
@pytest.mark.parametrize('valid_csrf', [True, False])
def test_oversized_public_body_is_rejected_before_csrf(app, path, valid_csrf):
    client = app.test_client()
    token = _token(client) if valid_csrf else 'invalid'
    response = client.post(path, data={'_csrf_token': token, 'padding': 'x' * 65536},
                           headers={'Accept': 'application/json'})
    assert response.status_code == 413
    assert response.json['error'] == 'file_too_large'
    assert response.json['max_bytes'] == 65536
    assert response.json['request_id']


@pytest.mark.parametrize('content_type', ['application/json', 'application/x-www-form-urlencoded',
                                        'multipart/form-data; boundary=unused'])
def test_unknown_length_terminated_body_is_bounded(app, content_type):
    response = app.test_client().open('/join', method='POST', environ_overrides={
        'CONTENT_LENGTH': None, 'CONTENT_TYPE': content_type,
        'wsgi.input': io.BytesIO(b'x' * 65537), 'wsgi.input_terminated': True,
    })
    assert response.status_code == 413


def test_unknown_length_without_termination_does_not_read_unbounded_stream(app):
    class Unreadable:
        def read(self, *args):
            pytest.fail('unbounded stream was read')

    response = app.test_client().open('/join', method='POST', environ_overrides={
        'CONTENT_LENGTH': None, 'wsgi.input': Unreadable(),
    })
    assert response.status_code == 400  # empty form cannot pass CSRF


def test_declared_oversize_is_rejected_without_reading_or_parsing(app, monkeypatch):
    from flask import Request

    def unexpected_parse(self):
        pytest.fail('oversized body was parsed')

    monkeypatch.setattr(Request, '_load_form_data', unexpected_parse)

    class Unreadable:
        def read(self, *args):
            pytest.fail('declared oversized body was read')

    response = app.test_client().open('/login', method='POST', environ_overrides={
        'CONTENT_LENGTH': '65537', 'wsgi.input': Unreadable(),
    })
    assert response.status_code == 413


@pytest.mark.parametrize('terminated', [False, True])
@pytest.mark.parametrize('size', [65535, 65536, 65537])
def test_public_body_limit_boundary(app, size, terminated):
    overrides = {'wsgi.input': io.BytesIO(b'x' * size),
                 'CONTENT_LENGTH': None if terminated else str(size)}
    if terminated:
        overrides['wsgi.input_terminated'] = True
    response = app.test_client().open('/join', method='POST', environ_overrides=overrides)
    # Unknown length streams fail closed at the cap because EOF is unproven.
    too_large = size > 65536 or (terminated and size == 65536)
    assert response.status_code == (413 if too_large else 400)


def test_terminated_multipart_cannot_hide_oversized_tail(app):
    client = app.test_client()
    token = _token(client, '/join')
    body = (f'--boundary\r\nContent-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{token}\r\n--boundary--\r\n').encode() + b'x' * 65536
    response = client.open('/join', method='POST', environ_overrides={
        'CONTENT_LENGTH': None, 'CONTENT_TYPE': 'multipart/form-data; boundary=boundary',
        'wsgi.input': io.BytesIO(body), 'wsgi.input_terminated': True,
    })
    assert response.status_code == 413


def test_normal_login_and_join_with_csrf(app):
    client = app.test_client()
    token = _token(client)
    response = client.post('/login', data={'_csrf_token': token, 'login_username': 'admin',
                                          'login_password': 'password'})
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess['admin_logged_in']

    anonymous = app.test_client()
    token = _token(anonymous, '/join')
    # Honeypot path exercises successful public form parsing without email delivery.
    response = anonymous.post('/join', data={'_csrf_token': token, 'website': 'bot'})
    assert response.status_code == 200
    assert b'Thank' in response.data


def test_authenticated_dashboard_upload_keeps_large_limit(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(admin_logged_in=True, admin_username='admin', _csrf_token='test-token',
                    admin_login_at=time.time())
    response = client.post('/dashboard/upload-probe', data={
        '_csrf_token': 'test-token', 'file': (io.BytesIO(b'x' * 131072), 'synthetic.bin'),
    })
    assert response.status_code == 200
    assert response.json['size'] == 131072
    response = client.post('/login', data={'padding': 'x' * 65536})
    assert response.status_code == 413  # public routes stay bounded for admins too


@pytest.mark.parametrize('path,limit', [('/respond/exchange', 4096),
                                     ('/respond/submit', 512 * 1024)])
@pytest.mark.parametrize('terminated', [False, True])
def test_poll_limits_apply_before_csrf_with_valid_json_token(app, monkeypatch, path, limit, terminated):
    from flask import Request

    client = app.test_client()
    token = _token(client)
    body = json.dumps({'padding': 'x' * limit}).encode()

    def unexpected_parse(self):
        pytest.fail('oversized poll body reached CSRF form parsing')

    monkeypatch.setattr(Request, '_load_form_data', unexpected_parse)
    overrides = {'CONTENT_LENGTH': None if terminated else str(len(body)),
                 'wsgi.input': io.BytesIO(body)}
    if terminated:
        overrides.update({'wsgi.input_terminated': True, 'HTTP_TRANSFER_ENCODING': 'chunked'})
    response = client.open(path, method='POST', content_type='application/json',
                           headers={'X-CSRF-Token': token, 'Accept': 'application/json'},
                           environ_overrides=overrides)
    assert response.status_code == 413
    assert response.json['max_bytes'] == limit


@pytest.mark.parametrize('path', ['/respond/exchange', '/respond/submit'])
def test_poll_without_length_or_termination_does_not_read_unbounded_stream(app, path):
    class Unreadable:
        def read(self, *args):
            pytest.fail('unbounded poll stream was read')

    client = app.test_client()
    token = _token(client)
    response = client.open(path, method='POST', content_type='application/json',
                           headers={'X-CSRF-Token': token}, environ_overrides={
                               'CONTENT_LENGTH': None, 'wsgi.input': Unreadable(),
                           })
    assert response.status_code == (400 if path.endswith('exchange') else 403)


@pytest.mark.parametrize('path,limit', [('/respond/exchange', 4096),
                                     ('/respond/submit', 512 * 1024)])
def test_poll_limits_respect_lower_global_ceiling(app, path, limit):
    app.config['MAX_CONTENT_LENGTH'] = 2048
    client = app.test_client()
    token = _token(client)
    response = client.post(path, json={'padding': 'x' * 2048},
                           headers={'X-CSRF-Token': token, 'Accept': 'application/json'})
    assert response.status_code == 413
    assert response.json['max_bytes'] == 2048


def test_poll_submission_allowance_does_not_extend_to_other_endpoints(app):
    client = app.test_client()
    token = _token(client)
    response = client.post('/respond/submit-extra', json={'padding': 'x' * 65536},
                           headers={'X-CSRF-Token': token, 'Accept': 'application/json'})
    assert response.status_code == 413
    assert response.json['max_bytes'] == 65536


@pytest.mark.parametrize('terminated', [False, True])
def test_valid_poll_submission_above_64_kib_is_saved(app, terminated):
    from mifp_app.services.polls import (create_poll, save_poll, create_invitation,
                                       set_invitation_status, current_responses)

    runtime = app.config['RUNTIME_CONFIG_DIR']
    poll = create_poll(runtime, 'blank')
    poll.update(status='open', questions=[{
        'id': f'{index:016x}', 'type': 'text', 'question': f'Comment {index}',
        'required': True, 'style': 'multi_line',
    } for index in range(1, 15)])
    poll = save_poll(runtime, poll['id'], poll)
    invitation, invitation_token = create_invitation(runtime, poll['id'])
    set_invitation_status(runtime, poll['id'], invitation['invitation_id'], 'sent')
    client = app.test_client()
    page = client.get('/respond')
    token = re.search(rb'data-csrf-token="([^"]+)"', page.data).group(1).decode()
    headers = {'X-CSRF-Token': token}
    assert client.post('/respond/exchange', json={
        'poll_id': poll['id'], 'token': invitation_token,
    }, headers=headers).status_code == 200
    answers = {question['id']: 'x' * 5000 for question in poll['questions']}
    body = json.dumps({'answers': answers}).encode()
    assert 64 * 1024 < len(body) < 512 * 1024
    overrides = {'CONTENT_LENGTH': None if terminated else str(len(body)),
                 'wsgi.input': io.BytesIO(body)}
    if terminated:
        overrides.update({'wsgi.input_terminated': True, 'HTTP_TRANSFER_ENCODING': 'chunked'})
    response = client.open('/respond/submit', method='POST', content_type='application/json',
                           headers=headers, environ_overrides=overrides)
    assert response.status_code == 200
    assert response.json['ok'] is True
    assert current_responses(runtime, poll['id'])[0]['answers'] == answers


def test_expired_admin_session_keeps_public_limit_and_auth_handles_expiration(app):
    app.config['ADMIN_SESSION_HOURS'] = 2
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(admin_logged_in=True, admin_username='admin', _csrf_token='test-token',
                    admin_login_at=time.time() - 2 * 3600 - 10)
    response = client.post('/dashboard/upload-probe', data={
        '_csrf_token': 'test-token', 'file': (io.BytesIO(b'x' * 131072), 'synthetic.bin'),
    }, headers={'Accept': 'application/json'})
    assert response.status_code == 413
    assert response.json['max_bytes'] == 65536
    with client.session_transaction() as sess:
        assert sess['admin_logged_in']  # preflight does not clear/alter authentication
    response = client.post('/dashboard/assets', data={'_csrf_token': 'test-token'},
                           headers={'Accept': 'application/json'})
    assert response.status_code == 401
    assert response.json['error'] == 'session_expired'


@pytest.mark.parametrize('path', ['/media/escape.pdf', '/dashboard/assets/escape.pdf'])
def test_media_symlink_outside_root_stays_blocked(app, tmp_path, path):
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.4\nprivate')
    (app.config['ASSETS_DIR'] / 'escape.pdf').symlink_to(outside)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(admin_logged_in=True, admin_username='admin')
    assert client.get(path).status_code == 400


def test_dashboard_asset_authentication_and_pdf_headers_are_preserved(app):
    (app.config['ASSETS_DIR'] / 'document.pdf').write_bytes(b'%PDF-1.4\nsynthetic')
    client = app.test_client()
    assert client.get('/dashboard/assets/document.pdf').status_code == 302
    with client.session_transaction() as sess:
        sess.update(admin_logged_in=True, admin_username='admin')
    response = client.get('/dashboard/assets/document.pdf')
    assert response.status_code == 200
    assert response.headers['Content-Disposition'] == 'attachment'
    assert 'no-store' in response.headers['Cache-Control']
    response = client.get('/media/document.pdf')
    assert response.status_code == 200
    assert response.headers['Content-Disposition'] == 'attachment'

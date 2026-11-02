"""Bounded release matrix: actual Flask routes and synthetic private journals."""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from TESTS.webapp.test_polls import poll_app, logged_in_client
from TESTS.webapp.test_simple_polls import survey_config
from mifp_app.services import simple_polls as store


def form_data(config):
    data = {k: v for k, v in config.items() if k not in {'questions', 'privacy'}}
    for k, v in config['privacy'].items():
        data['privacy_' + k] = 'yes' if k == 'confirmed' and v else v
    for i, q in enumerate(config['questions']):
        for k, v in q.items():
            data[f'q_{i}_{k}'] = ('\n'.join(v) if k == 'options' else 'on' if k == 'required' and v else v)
    return data


def opened(app, mode='anonymous'):
    base = app.config['RUNTIME_CONFIG_DIR']
    poll = store.create(base)
    cfg = survey_config(mode)
    poll = store.update(base, poll['id'], cfg)
    return poll, cfg, f"/survey/{poll['id']}/{poll['key']}"


def token(page, name):
    return re.search(fr'name="{name}" value="([^"]+)"', page.get_data(as_text=True)).group(1)


@pytest.mark.parametrize('mode', ['anonymous', 'identified'])
def test_route_lifecycle_all_types(poll_app, mode):
    app, base = poll_app, poll_app.config['RUNTIME_CONFIG_DIR']
    admin = logged_in_client(app)
    created = admin.post('/dashboard/polls-simple/create', data={'template': 'availability'})
    detail = created.headers['Location']
    pid = detail.rsplit('/', 1)[1]
    cfg = survey_config(mode)
    cfg['questions'] = [
        {'type': 'yes_no', 'question': 'Attendance?', 'required': True, 'yes_label': 'Available', 'no_label': 'Unavailable'},
        {'type': 'text', 'question': '=A1', 'required': True, 'style': 'multi_line'},
        {'type': 'date', 'question': 'Day?', 'required': True},
        {'type': 'single_choice', 'question': 'Venue?', 'required': True, 'options': ['A', 'B']},
        {'type': 'multiple_choice', 'question': 'Days?', 'required': True, 'options': ['C', 'D']},
    ]
    data = form_data(cfg)
    before = store.get(base, pid)
    preview = admin.post(detail + '/preview', data=data)
    assert preview.status_code == 200 and b'Administrator preview' in preview.data
    assert b'<fieldset disabled' in preview.data and b'Example Institute' in preview.data
    assert store.get(base, pid) == before
    saved = admin.post(detail + '/save', data=data, follow_redirects=True)
    assert b'Survey saved.' in saved.data
    poll = store.get(base, pid)
    url = f"/survey/{pid}/{poll['key']}"
    assert url.encode() in saved.data
    public = app.test_client()
    page = public.get(url)
    assert page.status_code == 200 and b'mainNav' not in page.data
    assert b'<script' not in page.data and b'https://cdn' not in page.data
    assert b'name="email"' in page.data if mode == 'identified' else b'name="email"' not in page.data
    questions = poll['questions']
    values = ['yes', '<script>alert(1)</script> =test', '2027-02-01', questions[3]['options'][0]['id'], [q['id'] for q in questions[4]['options']]]
    payload = {f'q_{q["id"]}': v for q, v in zip(questions, values)}
    payload.update(submission_id=secrets.token_hex(16), first_name='Synthetic', last_name='Person', email='synthetic@example.org')
    assert public.post(url, data={'submission_id': payload['submission_id']}).status_code == 400
    assert public.post(url, data=payload).status_code == 200
    assert public.post(url, data=payload).status_code == 200
    _, rows = store.results(base, pid)
    assert len(rows) == 1
    assert ('email' in rows[0]) == (mode == 'identified')
    if mode == 'anonymous':
        assert not {'email', 'first_name', 'last_name', 'ip', 'user_agent', 'cookie'} & rows[0].keys()
        assert len(rows[0]['submitted_at']) == 10
    exported = admin.get(detail + '/export.csv')
    assert exported.status_code == 200 and exported.headers['Cache-Control'].startswith('no-store')
    csv_rows = list(csv.reader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert "'=A1" in csv_rows[0] and 'Available' in csv_rows[1]
    assert 'A' in csv_rows[1] and 'C, D' in csv_rows[1]
    assert app.test_client().get(detail + '/export.csv').status_code in {302, 401}
    cfg['status'] = 'closed'
    assert admin.post(detail + '/save', data=form_data(cfg)).status_code == 302
    assert store.get(base, pid)['status'] == 'closed'
    assert public.get(url).status_code == public.post(url, data=payload).status_code == 404
    assert b'Survey closed or unavailable' in public.get(url).data
    admin.post(detail + '/delete', data={'admin_password': 'wrong'})
    assert store.get(base, pid)
    admin.post(detail + '/delete', data={'admin_password': 'secret123'})
    assert public.get(url).status_code == 404


def test_real_csrf_origin_cookie_headers_and_errors(poll_app):
    poll, cfg, url = opened(poll_app)
    poll_app.config['WTF_CSRF_ENABLED'] = True
    client = poll_app.test_client()
    page = client.get(url)
    assert 'mifp_csrf=' in page.headers['Set-Cookie']
    assert 'HttpOnly' in page.headers['Set-Cookie'] and 'SameSite=Lax' in page.headers['Set-Cookie']
    assert not any('session=' in h for h in page.headers.getlist('Set-Cookie'))
    data = {'submission_id': token(page, 'submission_id'), f'q_{poll["questions"][0]["id"]}': 'yes'}
    assert client.post(url, data=data).status_code == 400
    data['_csrf_token'] = token(page, '_csrf_token')
    assert client.post(url, data=data, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert poll_app.test_client().post(url, data=data).status_code == 400
    assert client.post(url, data=data, headers={'Origin': 'null'}).status_code == 200
    assert client.post(url, data={'submission_id': 'd'*32}, headers={'Origin': 'null'}).status_code == 400
    for response in [page, client.get(url + 'wrong'), client.post(url, data={})]:
        assert response.headers['Cache-Control'].startswith('no-store')
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        assert response.headers['X-Robots-Tag'] == 'noindex, nofollow'
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert "form-action 'self'" in response.headers['Content-Security-Policy']
    assert client.post(url, data=b'a' * (2 * 1024 * 1024 + 1), content_type='application/x-www-form-urlencoded').status_code == 413
    assert len(store.results(poll_app.config['RUNTIME_CONFIG_DIR'], poll['id'])[1]) == 1


def test_publication_and_collection_freeze_survive_erasure(poll_app):
    poll, cfg, url = opened(poll_app, 'identified')
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    for changes in [{'status': 'draft'}, {'title': 'Changed context'}, {'response_mode': 'anonymous'}]:
        with pytest.raises(store.SurveyError):
            store.update(base, poll['id'], {**cfg, **changes})
    store.submit(base, poll['id'], poll['key'], request_id='a'*32,
                 answers={poll['questions'][0]['id']: 'yes'}, identity={'first_name': 'Test', 'last_name': 'User', 'email': 'test@example.org'})
    store.erase_identified_email(base, poll['id'], 'test@example.org')
    changed = {**cfg, 'privacy': {**cfg['privacy'], 'purpose': 'New purpose'}}
    with pytest.raises(store.SurveyError):
        store.update(base, poll['id'], changed)
    assert store.get(base, poll['id'])['collection_started']
    version = store.get(base, poll['id'])['privacy_version']
    store.update(base, poll['id'], {**cfg, 'status': 'closed'})
    assert store.get(base, poll['id'])['privacy_version'] == version


def test_corruption_recovery_permissions_symlinks_and_restart(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll, _, url = opened(poll_app)
    directory = base / 'polls_v2' / poll['id']
    client = poll_app.test_client()
    data = {'submission_id': '1'*32, f'q_{poll["questions"][0]["id"]}': 'yes'}
    assert client.post(url, data=data).status_code == 200
    original = (directory / 'answers.jsonl').read_bytes()
    with (directory / 'answers.jsonl').open('ab') as stream:
        stream.write(b'{"incomplete":')
    assert len(store.results(base, poll['id'])[1]) == 1
    data['submission_id'] = '2'*32
    assert client.post(url, data=data).status_code == 200
    assert len(store.results(base, poll['id'])[1]) == 2
    from mifp_app import create_app
    restarted = create_app()
    restarted.config.update(poll_app.config)
    assert restarted.test_client().post(url, data=data).status_code == 200
    assert len(store.results(base, poll['id'])[1]) == 2
    for path in [directory / 'poll.json', directory / 'answers.jsonl', directory / '.lock']:
        assert path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    journal = directory / 'answers.jsonl'
    journal.write_bytes(original + b'{}\n')
    with pytest.raises(store.SurveyError, match='Corrupted'):
        store.results(base, poll['id'])
    assert client.post(url, data={**data, 'submission_id': '3'*32}).status_code == 400
    assert journal.read_bytes() == original + b'{}\n'
    journal.unlink()
    outside = directory.parent / 'outside.jsonl'
    outside.write_bytes(original)
    journal.symlink_to(outside)
    assert client.post(url, data=data).status_code == 404
    assert outside.read_bytes() == original
    with pytest.raises(store.SurveyError):
        store.get(base, '../outside')


def test_concurrent_routes_retries_close_and_delete(poll_app):
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll, cfg, url = opened(poll_app)
    def submit(index):
        return poll_app.test_client().post(url, data={'submission_id': f'{index:032x}', f'q_{poll["questions"][0]["id"]}': 'yes'}).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(submit, [1]*16 + list(range(2, 66)))) == {200}
    assert store.summary(base, poll['id'])['responses'] == 65
    def close(_):
        store.update(base, poll['id'], {**cfg, 'status': 'closed'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = [pool.submit(submit, i) for i in range(66, 90)]
        done = pool.submit(close, 0)
        assert all(f.result() in {200, 400, 404} for f in pending)
        done.result()
    assert submit(100) == 404
    # Readers/submissions queued around deletion must not recreate lock files.
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = [pool.submit(submit, i) for i in range(101, 125)]
        deleted = pool.submit(store.delete, base, poll['id'])
        assert all(f.result() == 404 for f in pending)
        deleted.result()
    assert not (base / 'polls_v2' / poll['id']).exists()


def test_legacy_is_read_only_without_purging_data(poll_app):
    from mifp_app.services.polls import create_poll
    old = create_poll(poll_app.config['RUNTIME_CONFIG_DIR'])
    directory = poll_app.config['RUNTIME_CONFIG_DIR'] / 'polls' / old['id']
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != '.lock'}
    admin = logged_in_client(poll_app)
    assert admin.get('/dashboard/notifications/polls').status_code == 302
    archive = admin.get(f'/dashboard/notifications/polls/{old["id"]}')
    assert archive.status_code == 200 and b'read-only' in archive.data.lower()
    for operation in ('invite', 'delete', 'reset', 'anonymize', 'clean'):
        assert admin.post(f'/dashboard/notifications/polls/{old["id"]}/{operation}').status_code == 410
    assert admin.post('/dashboard/notifications/polls/new').status_code == 410
    assert admin.get(f'/dashboard/notifications/polls/{old["id"]}/export.csv').status_code == 200
    assert poll_app.test_client().get('/respond').status_code == 410
    assert poll_app.test_client().post('/respond/exchange', json={}).status_code == 410
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.name != '.lock'}


def test_shared_link_load_8000(poll_app, monkeypatch):
    """Opt-in, single bounded run; no repeated benchmark in ordinary CI."""
    import os
    if os.environ.get('MIFP_POLLS_LOAD_TEST') != '1':
        pytest.skip('Run once with MIFP_POLLS_LOAD_TEST=1')
    poll, _, url = opened(poll_app)
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    from mifp_app.routes.simple_polls import _survey_rate, _survey_rate_lock
    with _survey_rate_lock:
        _survey_rate.clear()
    qid = poll['questions'][0]['id']
    durations = []
    started = time.perf_counter()
    def batch(worker):
        client = poll_app.test_client()
        for index in range(worker, 8000, 4):
            before = time.perf_counter()
            response = client.post(url, data={'submission_id': f'{index:032x}', f'q_{qid}': 'yes'})
            assert response.status_code == 200
            durations.append(time.perf_counter() - before)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(batch, range(4)))
    elapsed = time.perf_counter() - started
    start_summary = time.perf_counter()
    assert store.summary(base, poll['id'])['responses'] == 8000
    summary_seconds = time.perf_counter() - start_summary
    admin = logged_in_client(poll_app)
    start_export = time.perf_counter()
    exported = admin.get(f'/dashboard/polls-simple/{poll["id"]}/export.csv')
    export_seconds = time.perf_counter() - start_export
    assert exported.status_code == 200
    assert len(list(csv.reader(io.StringIO(exported.data.decode('utf-8-sig'))))) == 8001
    result = {'submissions': 8000, 'workers': 4, 'elapsed_seconds': round(elapsed, 3),
              'p95_seconds': round(sorted(durations)[int(len(durations)*.95)], 3),
              'max_seconds': round(max(durations), 3), 'summary_seconds': round(summary_seconds, 3),
              'export_seconds': round(export_seconds, 3)}
    print('POLLS_LOAD_RESULT=' + json.dumps(result))


def test_terminated_writer_releases_lock_and_preserves_acknowledged_rows(poll_app):
    import subprocess
    import sys
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll, _, url = opened(poll_app)
    client = poll_app.test_client()
    payload = {'submission_id': 'a'*32, f'q_{poll["questions"][0]["id"]}': 'yes'}
    assert client.post(url, data=payload).status_code == 200
    code = '''import os, sys
from pathlib import Path
from mifp_app.services.simple_polls import _locked
with _locked(Path(sys.argv[1]), exclusive=True):
    with (Path(sys.argv[1]) / "answers.jsonl").open("ab", buffering=0) as stream:
        stream.write(b'{"interrupted":')
        os.fsync(stream.fileno())
    os._exit(17)
'''
    import os
    env = {**os.environ, 'PYTHONPATH': str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'MIFPAPP/CORE')}
    directory = base / 'polls_v2' / poll['id']
    crashed = subprocess.run([sys.executable, '-c', code, str(directory)], env=env, timeout=10)
    assert crashed.returncode == 17
    assert client.post(url, data={**payload, 'submission_id': 'b'*32}).status_code == 200
    assert len(store.results(base, poll['id'])[1]) == 2


def test_interrupted_metadata_replace_preserves_old_poll(poll_app, monkeypatch):
    import os
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll = store.create(base)
    old = store.get(base, poll['id'])
    def fail_replace(*args, **kwargs):
        raise OSError('Synthetic interrupted metadata replacement')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError):
        store.update(base, poll['id'], survey_config())
    assert store.get(base, poll['id']) == old
    assert not list((base / 'polls_v2' / poll['id']).glob('*.tmp'))


def test_survey_logs_omit_client_fingerprints_and_admin_actor(poll_app):
    import logging
    from flask import session
    from mifp_app.utils.logger import _client_fingerprint, RequestContextFilter, safe_survey_path
    poll_app.config.update(LOG_INCLUDE_CLIENT_IP=True, LOG_HASH_CLIENT_IP=True)
    path = '/survey/' + 'a'*32 + '/' + 'b'*32
    with poll_app.test_request_context(path):
        session['admin_username'] = 'synthetic-admin'
        assert _client_fingerprint() == (None, None)
        record = logging.LogRecord('mifp.access', logging.INFO, __file__, 1, 'test', (), None)
        RequestContextFilter().filter(record)
        assert record.client_ip is None and record.client_ip_hash is None and record.actor is None
        assert 'b'*32 not in record.path
    assert 'b'*32 not in safe_survey_path(path + 'malformed-suffix')


def test_retry_does_not_acknowledge_a_journal_that_cannot_fsync(poll_app, monkeypatch):
    import os
    base = poll_app.config['RUNTIME_CONFIG_DIR']
    poll, _, url = opened(poll_app)
    fsync = os.fsync
    failures = []
    def unavailable(fd):
        if os.readlink(f'/proc/self/fd/{fd}').endswith('/answers.jsonl'):
            failures.append(fd)
            raise OSError('Synthetic journal fsync failure')
        fsync(fd)
    monkeypatch.setattr(os, 'fsync', unavailable)
    args = dict(request_id='f'*32, answers={poll['questions'][0]['id']: 'yes'})
    with pytest.raises(OSError):
        store.submit(base, poll['id'], poll['key'], **args)
    # The complete line exists, but the retry must still fail while durability
    # cannot be established; otherwise the route would falsely return success.
    with pytest.raises(OSError):
        store.submit(base, poll['id'], poll['key'], **args)
    assert len(failures) == 2
    monkeypatch.setattr(os, 'fsync', fsync)
    assert not store.submit(base, poll['id'], poll['key'], **args)
    assert len(store.results(base, poll['id'])[1]) == 1

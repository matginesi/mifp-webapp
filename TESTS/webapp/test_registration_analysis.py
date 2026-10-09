from __future__ import annotations

import fcntl
from datetime import date
from threading import Thread
import time

import pytest

from mifp_app.services.registrations import registration_summary, mark_potential_duplicates, filtered, read_records, prepare_local_archive
from TESTS.webapp.test_registrations import storage, registration_client, authenticate, EVENT, ID  # noqa: F401


def test_aggregations_account_for_missing_fields_dates_and_attachments():
    rows = mark_potential_duplicates([
        dict(email=' User@example.invalid ', submitted_at='2026-10-09T01:00:00Z', country='IT', affiliation='A', proof_file='proof.pdf', proof_size_bytes='12'),
        dict(email='user@example.invalid', submitted_at='2026-10-08T23:30:00-02:00', country='IT', registration_type='student'),
        dict(email='', submitted_at='bad', proof_file='proof.pdf', proof_size_bytes='bad'),
        dict(email='', submitted_at='2026-09-01', country='FR'),
    ])
    summary = registration_summary(rows, date(2026, 10, 9))
    assert summary['total'] == 4 and summary['recent'] == 2
    assert summary['invalid_dates'] == 1
    assert summary['potential_duplicates'] == 2
    assert summary['proof_count'] == 2 and summary['proof_bytes'] == 12 and summary['known_sizes'] == 1
    assert dict(summary['charts'][0][1])['2026-10-09'] == 2
    for _, groups in summary['charts'][2:]:
        assert sum(n for _, n in groups) == 4
    assert len(filtered(rows, dict(country='IT', duplicates='1'))) == 2
    assert len(filtered(rows, dict(affiliation='A'))) == 1
    empty = registration_summary([], date(2026,10,9))
    assert empty['total'] == 0 and all(not items for _, items in empty['charts'])


def test_large_distributions_keep_counts_and_escape_is_left_to_template():
    rows = [dict(country=f'Country {i}', affiliation='<script>', submitted_at='') for i in range(20)]
    summary = registration_summary(rows)
    country = summary['charts'][3][1]
    assert len(country) == 9 and sum(n for _, n in country) == 20


def test_missing_mount_is_not_an_empty_registration_repository(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_records(tmp_path / 'missing', EVENT)
    (tmp_path / 'registrations').mkdir()
    assert read_records(tmp_path, EVENT) == []


def test_unconfigured_archive_is_informational_without_faking_empty_records(registration_client):
    app, client = registration_client
    authenticate(client)
    app.config['REGISTRATIONS_PRIVATE_ROOT'] = ''
    app.config['ENV'] = 'production'
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 200
    assert b'Where are the registrations?' in response.data
    assert b'does not automatically connect to the VPS' in response.data
    assert b'cannot determine how many exist' in response.data
    assert b'HTTP 503' not in response.data and b'0 saved' not in response.data
    assert client.post('/dashboard/conference-sites/1/registrations-export/csv', data={'_csrf_token':'synthetic-csrf', 'password':'synthetic-operation-password'}).status_code == 503
    assert client.post(f'/dashboard/conference-sites/1/registrations/{ID}', data={
        '_csrf_token': 'synthetic-csrf', 'status': 'confirmed'}).status_code == 503


def test_configured_but_missing_archive_remains_an_infrastructure_error(registration_client, tmp_path):
    app, client = registration_client
    authenticate(client)
    app.config['REGISTRATIONS_PRIVATE_ROOT'] = str(tmp_path/'missing-archive')
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 503 and b'not_connected' in response.data
    assert b'is configured, but its directory could not be found' in response.data


def test_local_archive_is_ready_without_configuration_and_reuses_existing_files(registration_client, storage):
    import shutil
    from pathlib import Path
    app, client = registration_client
    authenticate(client)
    app.config.update(ENV='development', REGISTRATIONS_PRIVATE_ROOT='')
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 200 and b'0 matching / 0 saved' in response.data
    assert b'local registration files, separate from the VPS' in response.data
    root = Path(app.config['CONFERENCES_DIR']) / '.registrations-private'
    for directory in (root, root/'registrations', root/'uploads'):
        assert directory.stat().st_mode & 0o777 == 0o700
    source = storage[0]/'registrations'/EVENT
    shutil.copytree(source, root/'registrations'/EVENT)
    csv = root/'registrations'/EVENT/'registrations.csv'
    original = csv.read_bytes()
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 200 and b'1 matching / 1 saved' in response.data
    assert csv.read_bytes() == original
    assert client.post('/dashboard/conference-sites/1/registrations-export/csv', data={'_csrf_token':'synthetic-csrf', 'password':'synthetic-operation-password'}).status_code == 200


@pytest.mark.parametrize('target', ['.registrations-private', '.registrations-private/registrations', '.registrations-private/uploads'])
def test_default_local_archive_rejects_symlinks_without_writing_outside(tmp_path, target):
    conferences, outside = tmp_path/'conferences', tmp_path/'outside'
    outside.mkdir()
    link = conferences/target
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        prepare_local_archive(conferences)
    assert list(outside.iterdir()) == []


def test_csv_waits_for_short_writer_and_bounds_a_stalled_writer(storage):
    root, _ = storage
    path = root / 'registrations' / EVENT / 'registrations.csv'
    with path.open('rb') as writer:
        fcntl.flock(writer, fcntl.LOCK_EX)
        result = []
        thread = Thread(target=lambda: result.extend(read_records(root, EVENT)))
        thread.start()
        time.sleep(0.05)
        assert thread.is_alive()
        fcntl.flock(writer, fcntl.LOCK_UN)
        thread.join(timeout=2)
        assert len(result) == 1
        fcntl.flock(writer, fcntl.LOCK_EX)
        start = time.monotonic()
        with pytest.raises(BlockingIOError):
            read_records(root, EVENT)
        assert time.monotonic() - start < 2


def test_storage_error_retains_dashboard_and_emits_only_safe_reason(registration_client, monkeypatch):
    app, client = registration_client
    authenticate(client)
    from mifp_app.routes import dashboard_registrations as route
    captured = []
    monkeypatch.setattr(route, 'audit_log', lambda *args, **fields: captured.append(fields))
    def denied(*args):
        raise PermissionError('secret@example.invalid /private/path')
    monkeypatch.setattr(route, 'read_records', denied)
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 503
    assert b'permission_denied' in response.data and b'dashboardSidebar' in response.data
    assert b'secret@example.invalid' not in response.data
    assert captured[-1]['reason'] == 'permission_denied'
    assert 'secret@example.invalid' not in str(captured)


def test_empty_analytics_and_chart_values_render(registration_client):
    _, client = registration_client
    authenticate(client)
    response = client.get('/dashboard/conference-sites/1/registrations?q=not-found')
    assert response.status_code == 200
    assert b'No matching registrations' in response.data
    assert b'No data available' in response.data
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 200 and b'data-chart=' in response.data


def test_busy_storage_returns_retry_hint_without_exception_details(registration_client, monkeypatch):
    _, client = registration_client
    authenticate(client)
    from mifp_app.routes import dashboard_registrations as route
    def busy(*args):
        raise BlockingIOError('private storage details')
    monkeypatch.setattr(route, 'read_records', busy)
    response = client.get('/dashboard/conference-sites/1/registrations')
    assert response.status_code == 503 and response.headers['Retry-After'] == '5'
    assert b'private storage details' not in response.data


def test_storage_failure_console_has_event_request_id_and_safe_reason(registration_client, monkeypatch, tmp_path):
    import io
    from mifp_app.routes import dashboard_registrations as route
    from mifp_app.utils.logger import setup_logging, shutdown_logging
    _, client = registration_client
    authenticate(client)
    output = io.StringIO()
    monkeypatch.setattr('sys.stdout', output)
    def denied(*args):
        raise PermissionError('private@example.invalid secret-token')
    monkeypatch.setattr(route, 'read_records', denied)
    setup_logging(tmp_path/'logs', output='stdout', console_format='text', colors=False)
    try:
        response = client.get('/dashboard/conference-sites/1/registrations')
    finally:
        shutdown_logging()
    text = output.getvalue()
    assert response.status_code == 503
    assert 'event=registrations.storage_unavailable' in text
    assert 'permission_denied' in text and response.headers['X-Request-ID'] in text
    assert 'private@example.invalid' not in text and 'secret-token' not in text


def test_attachment_preview_requires_authentication_image_signature_and_safe_headers(registration_client, storage):
    import csv
    _, client = registration_client
    root, row = storage
    endpoint = f'/dashboard/conference-sites/1/registrations/{ID}/proof?preview=1'
    assert client.get(endpoint).status_code in (302, 401)
    authenticate(client)
    assert client.get(endpoint).status_code == 404  # PDF stays download-only.
    row['proof_file'] = 'proof-' + ID + '.png'
    with (root/'registrations'/EVENT/'registrations.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    proof = root/'uploads'/EVENT/row['proof_file']
    proof.write_bytes(b'<html>not an image</html>')
    assert client.get(endpoint).status_code == 404
    proof.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic')
    response = client.get(endpoint)
    assert response.status_code == 200 and response.mimetype == 'image/png'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.cache_control.no_store
    assert response.headers['Content-Disposition'].startswith('inline;')

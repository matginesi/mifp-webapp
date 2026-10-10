"""Canonical PHP runtime: only synthetic records, private temp storage, no email."""
from __future__ import annotations

import base64
import csv
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.request

import pytest

EDITOR_ROOT = Path(__file__).resolve().parents[2] / "MIFPAPP/CORE/CONFERENCE_EDITOR"
from mifp_app.services.registrations import read_records

PHP = os.environ.get('MIFP_TEST_PHP', shutil.which('php') or '')
pytestmark = pytest.mark.skipif(not PHP, reason='PHP CLI required')


@pytest.mark.parametrize('read_group,write_group,dir_mode,file_mode', [('1','0',0o750,0o640), ('0','0',0o700,0o600), ('1','1',0o2770,0o660), ('0','1',0o2770,0o660)])
def test_parallel_csv_writers_and_read_group_survive_later_writes(tmp_path, read_group, write_group, dir_mode, file_mode):
    storage = tmp_path / 'registrations' / 'SYNTHETIC-2027'
    storage.mkdir(parents=True)
    script = tmp_path / 'write.php'
    script.write_text('''<?php
require $argv[1];
ensure_storage($argv[2]);
persist_submission_csv($argv[2], ['receipt_id'=>$argv[3],'submitted_at'=>'2026-10-09T00:00:00Z','email'=>'synthetic@example.invalid','first_name'=>'Synthetic','last_name'=>'Example']);
''')
    env = dict(os.environ, MIFP_REGISTRATION_READ_GROUP=read_group, MIFP_REGISTRATION_WRITE_GROUP=write_group)
    def write(index):
        return subprocess.run([PHP, str(script), str(EDITOR_ROOT/'TEMPLATE/regform/src/bootstrap.php'), str(storage), f'REG-20261009-{index:08X}'], env=env, capture_output=True, text=True)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(write, range(20)))
    assert all(result.returncode == 0 for result in results), results
    records = read_records(tmp_path, 'SYNTHETIC-2027')
    assert len(records) == 20 and len({r['receipt_id'] for r in records}) == 20
    assert storage.stat().st_mode & 0o7777 == dir_mode
    assert (storage/'registrations.csv').stat().st_mode & 0o777 == file_mode


@pytest.mark.parametrize('read_group,write_group', [('0', '0'), ('1', '0'), ('0', '1'), ('1', '1')])
def test_http_upload_email_failure_replay_and_later_read_access(tmp_path, read_group, write_group):
    organizer = 'synthetic@example.invalid'
    site = tmp_path / 'SYNTHETIC-2027'
    shutil.copytree(EDITOR_ROOT/'TEMPLATE', site)
    settings = site/'regform/settings.yaml'
    settings.write_text(settings.read_text().replace('submit_enabled: false','submit_enabled: true')
                        .replace('minimum_fill_seconds: 2','minimum_fill_seconds: 0')
                        .replace('rate_limit_requests: 5','rate_limit_requests: 100'))
    conference = site/'conference.yaml'
    text = conference.read_text()
    text = re.sub(r'(?m)^registration:\n  enabled: false', 'registration:\n  enabled: true', text)
    conference.write_text(text.replace('contact_email: TBC', 'contact_email: synthetic@example.invalid'))
    settings.write_text(settings.read_text().replace('reply_to_email: TBC','reply_to_email: synthetic@example.invalid')
                        .replace('from_email: TBC','from_email: synthetic@example.invalid')
                        .replace('      - TBC', '      - ' + organizer))
    private = tmp_path/'private'
    private.mkdir()
    records = private/'registrations'
    uploads = private/'uploads'
    records.mkdir(); uploads.mkdir()
    env = dict(os.environ, MIFP_REGISTRATION_DIR=str(records), MIFP_UPLOAD_DIR=str(uploads),
               MIFP_REGISTRATION_READ_GROUP=read_group, MIFP_REGISTRATION_WRITE_GROUP=write_group, PHP_CLI_SERVER_WORKERS='4')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}/regform/'
    with (tmp_path/'php.log').open('w+') as log:
        process = subprocess.Popen([PHP,'-d','upload_max_filesize=10M','-d','post_max_size=12M',
                                    '-d','sendmail_path=/bin/false','-S',f'127.0.0.1:{port}','-t',str(site)],
                                   env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            jar = http.cookiejar.CookieJar()
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            for _ in range(100):
                try:
                    html = client.open(url,timeout=1).read().decode()
                    break
                except OSError:
                    time.sleep(.03)
            else:
                pytest.fail('PHP server did not start')
            token = re.search(r'name="csrf_token" value="([a-f0-9]+)"',html).group(1)
            fields = dict(csrf_token=token,first_name='Synthetic',last_name='Example',email='synthetic@example.invalid',
                          affiliation='Synthetic',country='Synthetic',address='Synthetic',arrival_date='2027-04-07',
                          departure_date='2027-04-10',tshirt_size='M',registration_type='Participant',
                          dietary_choice='No special requirements',payment_method='Bank Transfer',privacy_acceptance='1',company_website='')
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
            def payload(values, proof=png):
                chunks = [f'--test-boundary\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k,v in values.items()]
                chunks += [b'--test-boundary\r\nContent-Disposition: form-data; name="proof_of_payment"; filename="proof.png"\r\nContent-Type: image/png\r\n\r\n'+proof+b'\r\n',b'--test-boundary--\r\n']
                return b''.join(chunks)
            cookie = '; '.join(f'{c.name}={c.value}' for c in jar)
            def post(values, proof=png):
                req = urllib.request.Request(url,data=payload(values,proof),headers={'Content-Type':'multipart/form-data; boundary=test-boundary','Cookie':cookie})
                return urllib.request.urlopen(req,timeout=15).read().decode()
            assert 'session has expired' in post(dict(fields,csrf_token='invalid'))
            assert 'Only PDF, JPEG and PNG' in post(fields,b'not an image')
            assert read_records(private,'SYNTHETIC-2027') == []
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _:post(fields),range(2)))
            rows = read_records(private,'SYNTHETIC-2027')
            assert len(rows) == 1, results
            assert len(list(uploads.rglob('proof-*.png'))) == 1
            # Even subsequent GETs must not restore modes that hide the CSV.
            client.open(url,timeout=3).read()
            assert (records/'SYNTHETIC-2027').stat().st_mode & 0o777 == (0o770 if write_group == '1' else (0o750 if read_group == '1' else 0o700))
            assert (records/'SYNTHETIC-2027/registrations.csv').stat().st_mode & 0o777 == (0o660 if write_group == '1' else (0o640 if read_group == '1' else 0o600))
            assert (records/'SYNTHETIC-2027/.operations.lock').stat().st_mode & 0o777 == (0o660 if write_group == '1' else (0o640 if read_group == '1' else 0o600))
            notification_files = list((records/'SYNTHETIC-2027').glob('*.notifications.json'))
            assert len(notification_files) == 1
            expected = 0o660 if write_group == '1' else (0o640 if read_group == '1' else 0o600)
            for resource in [*notification_files, *records.rglob('*.notifications.lock'), *uploads.rglob('proof-*.png')]:
                assert resource.stat().st_mode & 0o7777 == expected
            if write_group == '1':
                assert (records/'SYNTHETIC-2027').stat().st_mode & 0o2000
                assert (uploads/'SYNTHETIC-2027').stat().st_mode & 0o2000
            notification = json.loads(notification_files[0].read_text())
            assert notification['participant']['status'] == 'unknown'
            assert notification['organizers']['status'] == ('unknown' if organizer != 'TBC' else 'pending')
            assert not list((site/'regform').rglob('registrations.csv'))
            assert 'session has expired' in post(fields)
            assert len(read_records(private,'SYNTHETIC-2027')) == 1
        finally:
            os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=5)
        log.seek(0)
        text = log.read()
        assert 'PHP Fatal' not in text


@pytest.mark.parametrize('resource', ['directory', 'csv'])
def test_failed_permissions_stop_before_appending(tmp_path, resource):
    storage = tmp_path/'registrations'/'SYNTHETIC-2027'
    storage.mkdir(parents=True, mode=0o700)
    csv_path = storage/'registrations.csv'
    csv_path.write_text('original bytes')
    csv_path.chmod(0o600)
    if resource == 'csv':
        storage.chmod(0o750)
    script = tmp_path/'permissions.php'
    script.write_text("""<?php
require $argv[1];
try {
    persist_submission_csv($argv[2], ['receipt_id'=>'REG-20261009-00000001']);
    exit(2);
} catch (Throwable $e) { echo get_class($e); }
""")
    env = dict(os.environ, MIFP_REGISTRATION_READ_GROUP='1', MIFP_REGISTRATION_WRITE_GROUP='0')
    result = subprocess.run([PHP, '-d', 'disable_functions=chmod', str(script),
                             str(EDITOR_ROOT/'TEMPLATE/regform/src/bootstrap.php'), str(storage)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout in {'Error', 'RuntimeException'}
    assert csv_path.read_text() == 'original bytes'

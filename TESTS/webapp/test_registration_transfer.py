"""Registration restore tests use temporary records and stubbed admin alerts."""
import csv
import io
import json
import os
import stat
from zipfile import ZipFile, ZipInfo

import pytest

from TESTS.webapp.test_registration_operations import (deletion_client, storage, registration_client,
    BASE, PASSWORD, EVENT, ID, FIELDS, post_data, add_record)  # noqa: F401
from mifp_app.services.registrations import read_records
from mifp_app.services.registration_transfer import recovery_package, read_import, import_records, csv_bytes
from mifp_app.services.registration_deletion import delete_registrations, DeletionRecoveryRequired

OPERATION = '1' * 32


def upload_data(payload, filename='registrations.zip', **changes):
    data = post_data(registrations_file=(io.BytesIO(payload), filename))
    data.update(changes)
    return data


def test_zip_roundtrip_keeps_new_submissions_and_is_idempotent(deletion_client, storage):
    app, client, alerts = deletion_client
    root, row = storage
    response = client.post(BASE+'/registrations-export/zip', data=post_data())
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.data)) as archive:
        assert set(archive.namelist()) == {'manifest.json','registrations.csv','proofs/'+row['proof_file']}
        assert b'=1+1' in archive.read('registrations.csv')  # raw values, not spreadsheet escaping
    assert client.post(BASE+'/registrations-delete', data=post_data(receipt=ID)).status_code == 302
    # A subsequent PHP submission must survive restore of the earlier package.
    later = dict(row, receipt_id='REG-20261010-00000002', proof_file='')
    (root/'registrations'/EVENT/'registrations.csv').write_bytes(csv_bytes([later], FIELDS))
    result = client.post(BASE+'/registrations-import', data=upload_data(response.data))
    assert result.status_code == 302
    assert read_records(root, EVENT) == [later, row]
    assert (root/'uploads'/EVENT/row['proof_file']).read_bytes() == b'%PDF-synthetic'
    assert client.post(BASE+'/registrations-import', data=upload_data(response.data)).status_code == 302
    assert read_records(root, EVENT) == [later, row]
    assert [item['event'] for item in alerts][-4:] == [
        'registrations.import_authorized', 'registrations.import_completed'] * 2
    assert all(PASSWORD not in item['body'] and row['email'] not in item['body'] for item in alerts)
    assert not list(__import__('pathlib').Path(app.config['CONFERENCES_DIR']).rglob('*.zip'))


@pytest.mark.parametrize('change,status', [({'password':'wrong'},403), ({'confirm':'wrong'},400),
                                          ({'_csrf_token':''},400)])
def test_import_authorization_precedes_mutation(deletion_client, storage, change, status):
    _, client, alerts = deletion_client
    root, row = storage
    payload = recovery_package(root, EVENT, [ID])
    assert client.post(BASE+'/registrations-import', data=upload_data(payload, **change)).status_code == status
    assert read_records(root, EVENT) == [row]
    assert not alerts
    assert client.get(BASE+'/registrations-import').status_code == 405


def test_import_requires_write_authorization_and_smtp_alert(deletion_client, storage, monkeypatch):
    from mifp_app.routes import dashboard_registrations as route
    app, client, _ = deletion_client
    root, row = storage
    payload = recovery_package(root, EVENT, [ID])
    app.config['REGISTRATIONS_WRITE_ENABLED'] = False
    assert client.post(BASE+'/registrations-import', data=upload_data(payload)).status_code == 409
    app.config['REGISTRATIONS_WRITE_ENABLED'] = True
    monkeypatch.setattr(route, 'notify', lambda *a, **k: __import__('types').SimpleNamespace(sent=False))
    assert client.post(BASE+'/registrations-import', data=upload_data(payload)).status_code == 503
    assert read_records(root, EVENT) == [row]


def test_csv_adds_only_missing_rows_and_rejects_conflicts(storage, tmp_path):
    root, row = storage
    new = dict(row, receipt_id='REG-20261010-00000002', proof_file='')
    package = read_import(csv_bytes([new], FIELDS), 'records.csv', EVENT)
    result = import_records(root, EVENT, package, tmp_path/'operations', OPERATION)
    assert result == dict(added=1, skipped=0, restored_proofs=0)
    assert read_records(root, EVENT) == [row, new]
    conflict = read_import(csv_bytes([dict(row, first_name='Changed')], FIELDS), 'records.csv', EVENT)
    with pytest.raises(ValueError, match='conflicting'):
        import_records(root, EVENT, conflict, tmp_path/'operations', '2'*32)
    assert read_records(root, EVENT) == [row, new]


def test_csv_missing_original_receipt_is_rejected(storage, tmp_path):
    root, row = storage
    package = read_import(csv_bytes([row], FIELDS), 'records.csv', EVENT)
    (root/'uploads'/EVENT/row['proof_file']).unlink()
    with pytest.raises(ValueError, match='missing receipts'):
        import_records(root, EVENT, package, tmp_path/'operations', OPERATION)
    assert read_records(root, EVENT) == [row]


def test_zip_restores_missing_receipt_but_never_overwrites_one(storage, tmp_path):
    root, row = storage
    package = read_import(recovery_package(root, EVENT, [ID]), 'records.zip', EVENT)
    proof = root/'uploads'/EVENT/row['proof_file']
    proof.unlink()
    assert import_records(root, EVENT, package, tmp_path/'operations', OPERATION)['restored_proofs'] == 1
    proof.write_bytes(b'%PDF-different')
    with pytest.raises(ValueError, match='conflicts'):
        import_records(root, EVENT, package, tmp_path/'operations', '2'*32)
    assert proof.read_bytes() == b'%PDF-different'


@pytest.mark.parametrize('unsafe', ['event','checksum','traversal','php','duplicate','symlink','missing','invalid'])
def test_untrusted_archives_are_rejected(storage, unsafe):
    root, row = storage
    payload = recovery_package(root, EVENT, [ID])
    with ZipFile(io.BytesIO(payload)) as archive:
        files = {name:archive.read(name) for name in archive.namelist()}
    if unsafe == 'event':
        manifest = json.loads(files['manifest.json']);manifest['event']='OTHER-2027'
        files['manifest.json']=json.dumps(manifest).encode()
    elif unsafe == 'checksum':
        files['registrations.csv'] += b'\n'
    elif unsafe in {'traversal','php'}:
        files['../settings.yaml' if unsafe == 'traversal' else 'proofs/evil.php']=b'unsafe'
    elif unsafe == 'missing':
        del files['proofs/'+row['proof_file']]
    elif unsafe == 'invalid':
        with pytest.raises(ValueError):
            read_import(b'not a zip', 'records.zip', EVENT)
        return
    output=io.BytesIO()
    with ZipFile(output,'w') as archive:
        for name,data in files.items():
            info=ZipInfo(name)
            if unsafe == 'symlink' and name.startswith('proofs/'):
                info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
            archive.writestr(info,data)
        if unsafe == 'duplicate':
            with pytest.warns(UserWarning):
                archive.writestr('manifest.json',files['manifest.json'])
    with pytest.raises(ValueError):
        read_import(output.getvalue(), 'records.zip', EVENT)


def test_zip_export_fails_on_missing_original_and_limits_are_bounded(storage, monkeypatch):
    from mifp_app.services import registration_transfer as service
    root,row=storage
    (root/'uploads'/EVENT/row['proof_file']).unlink()
    with pytest.raises(FileNotFoundError):
        recovery_package(root, EVENT, [ID])
    monkeypatch.setattr(service,'MAX_PACKAGE_BYTES',8)
    with pytest.raises(OverflowError):
        read_import(b'123456789','records.csv',EVENT)


@pytest.mark.parametrize('target', ['symlink','hardlink'])
def test_import_never_follows_unsafe_receipt_files(storage,tmp_path,target):
    root,row=storage
    package=read_import(recovery_package(root,EVENT,[ID]),'records.zip',EVENT)
    proof=root/'uploads'/EVENT/row['proof_file']
    if target=='symlink':
        proof.unlink();proof.symlink_to('/etc/passwd')
    else:
        os.link(proof,tmp_path/'other')
    with pytest.raises((OSError,ValueError)):
        import_records(root,EVENT,package,tmp_path/'operations',OPERATION)
    assert read_records(root,EVENT)==[row]


def test_import_write_error_rolls_back_without_persistent_copies(storage,tmp_path,monkeypatch):
    from mifp_app.services import registration_transfer as service
    root,row=storage
    package=read_import(recovery_package(root,EVENT,[ID]),'records.zip',EVENT)
    delete_registrations(root,EVENT,[ID],tmp_path/'operations',operation='2'*32)
    original=service._write_csv
    calls=[]
    def fail_once(stream,payload):
        calls.append(1)
        if len(calls)==1:
            stream.seek(0);stream.write(b'broken');stream.flush();raise OSError('synthetic failure')
        return original(stream,payload)
    monkeypatch.setattr(service,'_write_csv',fail_once)
    with pytest.raises(OSError):
        import_records(root,EVENT,package,tmp_path/'operations',OPERATION)
    assert read_records(root,EVENT)==[]
    assert not (root/'uploads'/EVENT/row['proof_file']).exists()
    assert not list(tmp_path.rglob('*.zip'))
    assert json.loads((tmp_path/'operations'/(OPERATION+'.finished.json')).read_text())['status']=='rolled_back'


def test_interrupted_operation_blocks_import(storage,tmp_path):
    root,row=storage
    operations=tmp_path/'operations';operations.mkdir(mode=0o700)
    (operations/'old.started.json').write_text('{}')
    package=read_import(csv_bytes([row],FIELDS),'records.csv',EVENT)
    with pytest.raises(DeletionRecoveryRequired):
        import_records(root,EVENT,package,operations,OPERATION)
    assert read_records(root,EVENT)==[row]


@pytest.mark.parametrize('finished', ['{', '{}', '{"status":"imported"}'])
def test_truncated_or_inconsistent_outcome_blocks_import(storage,tmp_path,finished):
    root,row=storage
    operations=tmp_path/'operations';operations.mkdir(mode=0o700)
    (operations/(OPERATION+'.started.json')).write_text(json.dumps(dict(operation=OPERATION,event=EVENT,count=1)))
    (operations/(OPERATION+'.finished.json')).write_text(finished)
    package=read_import(csv_bytes([row],FIELDS),'records.csv',EVENT)
    with pytest.raises(DeletionRecoveryRequired):
        import_records(root,EVENT,package,operations,'2'*32)
    assert read_records(root,EVENT)==[row]


def test_zip_record_limit_is_rejected_before_export(storage, monkeypatch):
    from mifp_app.services import registration_transfer as service
    root,_=storage
    monkeypatch.setattr(service,'MAX_ROWS',0)
    with pytest.raises(OverflowError):
        recovery_package(root,EVENT,[ID])

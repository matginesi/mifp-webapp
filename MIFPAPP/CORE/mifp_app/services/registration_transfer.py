"""Explicit downloadable recovery packages and additive registration imports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from PIL import Image

from .registrations import (MAX_CSV_BYTES, component, parse_records, private_directory,
                            private_file, proof_filename, registration_lock)
from .registration_deletion import operation_guard, _journal, _write_csv, DeletionRecoveryRequired

MAX_PACKAGE_BYTES = 96 * 1024 * 1024
MAX_ROWS = 5000
ADMIN_FIELDS = {'admin_status', 'internal_note', 'archived', 'potential_duplicates'}


def csv_bytes(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode('utf-8')


def _headers(payload):
    return next(csv.reader(io.StringIO(payload.decode('utf-8-sig'))))


def recovery_package(root, event, receipts):
    """Read a consistent snapshot under the same lock used by PHP/writers."""
    with private_file(root, 'registrations', event, '.operations.lock') as lock, registration_lock(lock):
        with private_file(root, 'registrations', event, 'registrations.csv') as stream, registration_lock(stream):
            original = stream.read(MAX_CSV_BYTES + 1)
        requested = set(receipts)
        if len(requested) > MAX_ROWS:
            raise OverflowError('Recovery ZIP supports at most 5000 registrations.')
        selected = [row for row in parse_records(original) if row['receipt_id'] in requested]
        if len(selected) != len(requested) or len(selected) > MAX_ROWS:
            raise ValueError('Selection changed or exceeds 5000 records. Reload before exporting.')
        files = {'registrations.csv': csv_bytes(selected, _headers(original))}
        total = len(files['registrations.csv'])
        for row in selected:
            name = proof_filename(row)
            if name:
                with private_file(root, 'uploads', event, name) as stream:
                    payload = stream.read(10 * 1024 * 1024 + 1)
                if len(payload) > 10 * 1024 * 1024:
                    raise OverflowError('A receipt exceeds 10 MiB.')
                total += len(payload)
                if total > MAX_PACKAGE_BYTES:
                    raise OverflowError('Recovery package exceeds 96 MiB. Apply narrower filters.')
                files['proofs/' + name] = payload
        manifest = dict(format='mifp-registrations', version=1, event=event,
                        sha256={name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()})
        manifest_payload = json.dumps(manifest).encode('utf-8')
        if total + len(manifest_payload) > MAX_PACKAGE_BYTES:
            raise OverflowError('Recovery package exceeds 96 MiB. Apply narrower filters.')
        output = io.BytesIO()
        with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', manifest_payload)
            for name, payload in files.items():
                archive.writestr(name, payload)
        if output.tell() > MAX_PACKAGE_BYTES:
            raise OverflowError('Recovery package exceeds 96 MiB. Apply narrower filters.')
        return output.getvalue()


def read_import(payload, filename, event):
    """Never extract an archive; allow only named CSV and matching receipts."""
    if len(payload) > MAX_PACKAGE_BYTES:
        raise OverflowError('Import exceeds 96 MiB.')
    proofs = {}
    if filename.lower().endswith('.csv'):
        original = payload
    elif filename.lower().endswith('.zip'):
        try:
            with ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if (len(entries) > MAX_ROWS + 2 or len(names) != len(set(names)) or
                    sum(entry.file_size for entry in entries) > MAX_PACKAGE_BYTES or
                    any(entry.flag_bits & 1 or entry.is_dir() or
                        stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG} for entry in entries)):
                    raise ValueError('Unsafe or oversized registration archive.')
                if 'manifest.json' not in names or 'registrations.csv' not in names:
                    raise ValueError('Use the registration recovery ZIP exported by this dashboard.')
                if archive.getinfo('manifest.json').file_size > 1024 * 1024:
                    raise ValueError('Registration manifest is too large.')
                manifest = json.loads(archive.read('manifest.json'))
                if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in
                        dict(format='mifp-registrations', version=1, event=event).items()):
                    raise ValueError('Registration package belongs to another event or format.')
                original = archive.read('registrations.csv')
                rows = parse_records(original)
                expected = {'registrations.csv'} | {'proofs/' + proof_filename(row) for row in rows if proof_filename(row)}
                if set(names) != expected | {'manifest.json'} or not isinstance(manifest.get('sha256'), dict) or set(manifest['sha256']) != expected:
                    raise ValueError('Archive contains unexpected or missing files.')
                for name in expected:
                    if name != 'registrations.csv' and archive.getinfo(name).file_size > 10 * 1024 * 1024:
                        raise OverflowError('A receipt exceeds 10 MiB.')
                    data = original if name == 'registrations.csv' else archive.read(name)
                    if hashlib.sha256(data).hexdigest() != manifest['sha256'][name]:
                        raise ValueError('Registration package checksum mismatch.')
                    if name != 'registrations.csv':
                        proofs[name.removeprefix('proofs/')] = data
        except (BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError, KeyError) as exc:
            raise ValueError('Invalid registration archive.') from exc
    else:
        raise ValueError('Choose a CSV or a registration recovery ZIP.')
    rows = parse_records(original)
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError('Import between 1 and 5000 registrations.')
    fields = [name for name in _headers(original) if name not in ADMIN_FIELDS]
    rows = [{key: row[key] for key in fields} for row in rows]
    for row in rows:
        name = proof_filename(row)
        if name in proofs:
            data = proofs[name]
            if name.endswith('.pdf'):
                if not data.startswith(b'%PDF-'):
                    raise ValueError('Invalid PDF receipt.')
            else:
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        if image.format not in ({'PNG'} if name.endswith('.png') else {'JPEG'}):
                            raise ValueError('Receipt content does not match its extension.')
                        if image.width * image.height > 40_000_000:
                            raise ValueError('Receipt image exceeds the pixel limit.')
                        image.verify()
                except (OSError, Image.DecompressionBombError) as exc:
                    raise ValueError('Invalid image receipt.') from exc
    return fields, rows, proofs


def import_records(root, event, package, operation_root, operation):
    component(event)
    if len(operation) != 32 or any(char not in '0123456789abcdef' for char in operation):
        raise ValueError('Invalid import operation ID.')
    fields, incoming, proofs = package
    with operation_guard(operation_root):
        with private_file(root, 'registrations', event, '.operations.lock') as lock, registration_lock(lock, exclusive=True):
            with private_file(root, 'registrations', event, 'registrations.csv', writable=True) as stream, registration_lock(stream, exclusive=True):
                original = stream.read(MAX_CSV_BYTES + 1)
                existing = parse_records(original)
                target_fields = _headers(original)
                if set(fields) != set(target_fields):
                    raise ValueError('CSV columns must match the current registration form.')
                by_id = {row['receipt_id']: row for row in existing}
                added, new_files, total = [], {}, len(original)
                for row in incoming:
                    if row['receipt_id'] in by_id:
                        if row != by_id[row['receipt_id']]:
                            raise ValueError('An existing receipt ID has conflicting data. No records were overwritten.')
                    else:
                        added.append(row)
                    name = proof_filename(row)
                    if not name:
                        continue
                    try:
                        with private_file(root, 'uploads', event, name) as receipt:
                            current = receipt.read(10 * 1024 * 1024 + 1)
                        if len(current) > 10 * 1024 * 1024 or name in proofs and current != proofs[name]:
                            raise ValueError('An existing receipt file conflicts with the import.')
                    except FileNotFoundError:
                        if name not in proofs:
                            raise ValueError('CSV refers to missing receipts. Import the recovery ZIP with originals.')
                        new_files[name] = proofs[name]
                        total += len(proofs[name])
                output = csv_bytes(existing + added, target_fields)
                if len(output) > MAX_CSV_BYTES or total + len(output) > MAX_PACKAGE_BYTES:
                    raise OverflowError('Result exceeds registration import limits.')
                if not added and not new_files:
                    return dict(added=0, skipped=len(incoming), restored_proofs=0)
                with private_directory(root, 'uploads', event) as uploads:
                    journal = dict(event=event, count=len(added), operation=operation, action='import')
                    _journal(operation_root / (operation + '.started.json'), journal)
                    created = []
                    try:
                        for name, data in new_files.items():
                            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o660, dir_fd=uploads)
                            created.append(name)
                            with os.fdopen(fd, 'wb') as receipt:
                                os.fchmod(receipt.fileno(), 0o660)
                                receipt.write(data)
                                receipt.flush()
                                os.fsync(receipt.fileno())
                        os.fsync(uploads)
                        _write_csv(stream, output)
                        _journal(operation_root / (operation + '.finished.json'), dict(journal, status='imported'))
                    except OSError:
                        try:
                            _write_csv(stream, original)
                            for name in created:
                                os.unlink(name, dir_fd=uploads)
                            os.fsync(uploads)
                            _journal(operation_root / (operation + '.finished.json'), dict(journal, status='rolled_back'))
                        except OSError as exc:
                            raise DeletionRecoveryRequired('Manual reconciliation required; imports and deletions blocked.') from exc
                        raise
    return dict(added=len(added), skipped=len(incoming)-len(added), restored_proofs=len(new_files))

"""Password-authorized deletions, coordinated with the PHP operation lock.

No persistent copies of CSV or attachments are created. Temporary in-memory
buffers allow rollback of caught errors; a small journal detects interruption.
"""
from __future__ import annotations

import csv
import io
import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

from .registrations import (MAX_CSV_BYTES, RECEIPT, component, parse_records,
                            private_directory, private_file, proof_filename, registration_lock)

MAX_ROLLBACK_BYTES = 96 * 1024 * 1024


class DeletionRecoveryRequired(OSError):
    """A partial operation requires manual reconciliation before another deletion."""


def _write_csv(stream, payload):
    stream.seek(0)
    stream.write(payload)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def _journal(path, value):
    # The directory is application-owned; never follow a journal symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    with private_directory(path.parent) as parent:
        os.fsync(parent)


@contextmanager
def operation_guard(operation_root: Path):
    with private_directory(operation_root.parent) as parent:
        try:
            os.mkdir(component(operation_root.name), mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    with private_directory(operation_root) as operation_fd, registration_lock(operation_fd, exclusive=True):
        info = os.fstat(operation_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe registration operation directory.')
        # Missing, truncated or inconsistent completion markers fail closed.
        for name in os.listdir(operation_fd):
            if not name.endswith('.started.json'):
                continue
            try:
                with private_file(operation_root, name) as stream:
                    started = json.loads(stream.read(4097))
                with private_file(operation_root, name.replace('.started.json', '.finished.json')) as stream:
                    finished = json.loads(stream.read(4097))
                if (not isinstance(started, dict) or not isinstance(finished, dict) or
                    not started.get('operation') or
                    any(finished.get(key) != started.get(key) for key in ('operation', 'event', 'count')) or
                    finished.get('status') not in {'deleted', 'imported', 'rolled_back'}):
                    raise ValueError('Invalid operation outcome.')
            except (OSError, ValueError) as exc:
                raise DeletionRecoveryRequired('A previous registration operation requires manual reconciliation.') from exc
        yield


def delete_registrations(root: Path, event: str, receipts: list[str], operation_root: Path,
                         *, all_records=False, operation=None):
    component(event)
    operation = operation or uuid4().hex
    if len(operation) != 32 or any(char not in "0123456789abcdef" for char in operation):
        raise ValueError("Invalid deletion operation ID.")
    if not all_records and (not receipts or len(receipts) > 5000 or
                            any(not RECEIPT.fullmatch(value) for value in receipts)):
        raise ValueError('Select between 1 and 5000 valid registrations.')
    with operation_guard(operation_root):
        with private_file(root, 'registrations', event, '.operations.lock') as lock, registration_lock(lock, exclusive=True):
            with private_file(root, 'registrations', event, 'registrations.csv', writable=True) as stream, registration_lock(stream, exclusive=True):
                original = stream.read(MAX_CSV_BYTES + 1)
                rows = parse_records(original)
                ids = {row['receipt_id'] for row in rows} if all_records else set(receipts)
                if not ids or not ids.issubset({row['receipt_id'] for row in rows}):
                    raise ValueError('Selection is empty or stale. Reload registrations first.')
                if len(ids) > 5000:
                    raise ValueError('Delete at most 5000 registrations per operation.')
                selected = [row for row in rows if row['receipt_id'] in ids]
                removed, total_bytes = [], len(original)
                with ExitStack() as stack:
                    parents = {tree: stack.enter_context(private_directory(root, tree, event))
                               for tree in ('registrations', 'uploads')}
                    for row in selected:
                        name = proof_filename(row)
                        targets = [('uploads', name)] if name else []
                        targets += [('registrations', row['receipt_id'] + suffix)
                                    for suffix in ('.notifications.json',)]
                        for tree, name in targets:
                            try:
                                file = stack.enter_context(private_file(root, tree, event, name))
                            except FileNotFoundError:
                                continue
                            info = os.fstat(file.fileno())
                            total_bytes += info.st_size
                            if total_bytes > MAX_ROLLBACK_BYTES:
                                raise ValueError('Selected files exceed the 96 MiB operation limit. Select a smaller group.')
                            # Preflight directory access before writing CSV. Mount
                            # and POSIX permissions still fail closed at mutation.
                            if not os.access('.', os.W_OK, dir_fd=parents[tree]):
                                raise PermissionError('Registration directory is read-only.')
                            removed.append((tree, name, file.read(info.st_size + 1), info.st_mode & 0o777))
                    output = io.StringIO(newline='')
                    fields = next(csv.reader(io.StringIO(original.decode('utf-8-sig'))))
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(row for row in rows if row['receipt_id'] not in ids)
                    journal = dict(event=event, count=len(ids), operation=operation)
                    _journal(operation_root / (operation + '.started.json'), journal)
                    unlinked = []
                    try:
                        _write_csv(stream, output.getvalue().encode('utf-8'))
                        for tree, name, payload, mode in removed:
                            os.unlink(name, dir_fd=parents[tree])
                            unlinked.append((tree, name, payload, mode))
                        for parent in parents.values():
                            os.fsync(parent)
                        _journal(operation_root / (operation + '.finished.json'), dict(journal, status='deleted'))
                    except OSError:
                        try:
                            for tree, name, payload, mode in unlinked:
                                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                             mode, dir_fd=parents[tree])
                                with os.fdopen(fd, 'wb') as file:
                                    os.fchmod(file.fileno(), mode)
                                    file.write(payload)
                                    file.flush()
                                    os.fsync(file.fileno())
                            _write_csv(stream, original)
                            _journal(operation_root / (operation + '.finished.json'), dict(journal, status='rolled_back'))
                        except OSError as exc:
                            raise DeletionRecoveryRequired('Manual reconciliation required; further deletion blocked.') from exc
                        raise
    return dict(operation=operation, count=len(ids))

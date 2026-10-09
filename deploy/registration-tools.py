#!/usr/bin/env python3
"""Explicit host approval for bounded registration requests. No background worker.

Request contents are untrusted. Paths, commands and transport credentials never
come from the dashboard. CSV writes preserve its inode and PHP's flock lock.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import io
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

KEY = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")
RECEIPT = re.compile(r"REG-[0-9]{8}-[A-F0-9]{8}\Z")
REQUEST = re.compile(r"[a-f0-9]{32}\Z")
LIMIT = 32 * 1024 * 1024


def key(value):
    if not KEY.fullmatch(value) or value in {'.', '..'}:
        raise ValueError('Invalid storage identifier')
    return value


@contextmanager
def directory(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Absolute directory required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(key(part), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def opened(path, write=False):
    with directory(Path(path).parent) as parent:
        fd = os.open(key(Path(path).name), (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    stream = os.fdopen(fd, 'r+b' if write else 'rb')
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Only regular, unlinked storage files are allowed')
        yield stream
    finally:
        stream.close()



@contextmanager
def notification_lock(path, owner):
    with directory(path.parent) as parent:
        fd = os.open(key(path.name), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o640, dir_fd=parent)
    with os.fdopen(fd, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unsafe notification lock')
        os.fchown(stream.fileno(), *owner)
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield stream


def read_json(path):
    with opened(path) as stream:
        payload = stream.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError('State or request is too large')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('Invalid state or request')
    return value


def atomic_json(path, value, owner=None):
    payload = json.dumps(value, sort_keys=True).encode()
    with directory(Path(path).parent) as parent:
        temporary = '.registration-' + os.urandom(12).hex()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=parent)
        try:
            if owner:
                os.fchown(fd, *owner)
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(fd)
            os.rename(temporary, Path(path).name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def rows_from(payload):
    reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')), strict=True)
    fields = reader.fieldnames or []
    if not {'receipt_id', 'email'}.issubset(fields) or len(set(fields)) != len(fields):
        raise ValueError('Invalid CSV headers')
    rows = list(reader)
    ids = [row.get('receipt_id', '') for row in rows]
    if len(ids) != len(set(ids)) or any(not RECEIPT.fullmatch(value) for value in ids):
        raise ValueError('Duplicate or invalid receipt ID')
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('Malformed CSV row')
    return fields, rows


def write_rows(stream, fields, rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode('utf-8')
    stream.seek(0)
    stream.write(payload)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def proof_name(row):
    name = row.get('proof_file', '')
    if name and (name != 'proof-' + row['receipt_id'] + '.' + name.rsplit('.', 1)[-1] or name.rsplit('.', 1)[-1].lower() not in {'pdf', 'jpg', 'jpeg', 'png'}):
        raise ValueError('Invalid proof filename')
    return key(name) if name else ''


def mailbox(value):
    if not re.fullmatch(r"[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+", value):
        raise ValueError('Invalid mail address')
    return value


def backup(path, event, fields, selected, private):
    # No credential/config files, only the selected canonical records and proofs.
    with path.open('xb') as output:
        os.chmod(path, 0o600)
        with tarfile.open(fileobj=output, mode='w') as archive:
            manifest = json.dumps(dict(event=event, fields=fields, rows=selected)).encode()
            info = tarfile.TarInfo('records.json')
            info.size = len(manifest)
            archive.addfile(info, io.BytesIO(manifest))
            for row in selected:
                name = proof_name(row)
                if name:
                    with opened(private / 'uploads' / event / name) as proof:
                        info = tarfile.TarInfo('uploads/' + name)
                        info.size = os.fstat(proof.fileno()).st_size
                        if info.size > 10 * 1024 * 1024:
                            raise ValueError('Proof too large')
                        archive.addfile(info, proof)
                notification = private / 'registrations' / event / (row['receipt_id'] + '.notifications.json')
                try:
                    state = read_json(notification)
                except FileNotFoundError:
                    continue
                payload = json.dumps(state).encode()
                info = tarfile.TarInfo('notifications/' + notification.name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        output.flush()
        os.fsync(output.fileno())




def file_digest(path):
    digest = hashlib.sha256()
    with opened(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prune_backups(directory_path, keep):
    if keep < 2:
        raise ValueError('Keep at least two operation backups')
    completed = []
    with directory(directory_path):
        for candidate in directory_path.glob('*.tar'):
            if not REQUEST.fullmatch(candidate.stem):
                continue
            with opened(candidate) as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != os.geteuid():
                    raise ValueError('Unexpected backup owner')
            try:
                result = read_json(directory_path / (candidate.stem + '.result.json'))
            except FileNotFoundError:
                continue
            if result.get('status') == 'completed' and result.get('action') == 'delete':
                completed.append((info.st_mtime_ns, candidate.name))
        with directory(directory_path) as parent:
            for _, name in sorted(completed, reverse=True)[keep:]:
                os.unlink(name, dir_fd=parent)
            os.fsync(parent)


def apply(args):
    if getattr(args, 'keep', 14) < 2:
        raise ValueError('Keep at least two operation backups')
    if not REQUEST.fullmatch(args.request):
        raise ValueError('Invalid request ID')
    request = read_json(args.requests / (args.request + '.json'))
    event = key(str(request.get('event', '')))
    ids = request.get('receipts')
    if request.get('id') != args.request or not isinstance(ids, list) or not 0 < len(ids) <= 5000 or any(not isinstance(v, str) or not RECEIPT.fullmatch(v) for v in ids) or len(set(ids)) != len(ids):
        raise ValueError('Invalid request records')
    action = request.get('action')
    channel = request.get('channel')
    if action == 'retry' and len(ids) > 25:
        raise ValueError('Retry batches are limited to 25 registrations')
    if action not in {'delete', 'retry'} or (action == 'retry' and channel not in {'organizers', 'participant'}):
        raise ValueError('Invalid request action')
    print(f"Request {args.request}: {action}, event {event}, count {len(ids)}, channel {channel if action == 'retry' else '-'}")
    approval = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    print('Approval SHA-256: ' + approval)
    if args.confirm != event or getattr(args, 'approve', '') != approval:
        raise ValueError('Review and supply --confirm EVENT --approve SHA256; no action performed')
    if action == 'delete':
        with opened(args.allowlist) as allowlist:
            approved = allowlist.read(1024 * 1024).decode().splitlines()
        if any(line.strip('/').split('/')[-2:] == [event, 'regform'] for line in approved):
            raise ValueError('Revoke conference PHP authorization before cleanup')
    # This path is host-owned, never a directory supplied by a queued request.
    args.backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    with directory(args.backups) as backup_dir:
        owner = os.fstat(backup_dir)
        if owner.st_uid != os.geteuid() or owner.st_mode & 0o022:
            raise ValueError('Unsafe host backup directory')
    with opened(args.private / 'registrations' / event / '.operations.lock') as operation_lock, opened(args.private / 'registrations' / event / 'registrations.csv', write=True) as stream:
        fcntl.flock(operation_lock, fcntl.LOCK_EX)
        fcntl.flock(stream, fcntl.LOCK_EX)
        marker = args.backups / (args.request + '.result.json')
        if marker.exists():
            print('Request already processed; no action performed')
            return
        payload = stream.read(LIMIT + 1)
        if len(payload) > LIMIT:
            raise ValueError('CSV too large')
        fields, rows = rows_from(payload)
        selected = [row for row in rows if row['receipt_id'] in ids]
        if len(selected) != len(ids):
            raise ValueError('Request is stale; one or more records no longer exist')
        if action == 'delete':
            archive = args.backups / (args.request + '.tar')
            backup(archive, event, fields, selected, args.private)
            backup_digest = file_digest(archive)
            # Journal the start before any destructive mutation. An interrupted
            # operation requires inspection/restore, never blind replay.
            atomic_json(marker, dict(status='started', action=action, event=event, count=len(ids), approval=approval, backup_sha256=backup_digest))
            write_rows(stream, fields, [row for row in rows if row['receipt_id'] not in ids])
            for row in selected:
                name = proof_name(row)
                if name:
                    with directory(args.private / 'uploads' / event) as parent:
                        os.unlink(name, dir_fd=parent)
                with directory(args.private / 'registrations' / event) as parent:
                    try:
                        os.unlink(row['receipt_id'] + '.notifications.json', dir_fd=parent)
                    except FileNotFoundError:
                        pass
            result = dict(status='completed', action=action, event=event, count=len(ids), backup_sha256=backup_digest)
        else:
            outcomes = []
            with ExitStack() as locks:
                for row in sorted(selected, key=lambda item: item['receipt_id']):
                    info = os.fstat(stream.fileno())
                    locks.enter_context(notification_lock(args.private / 'registrations' / event / (row['receipt_id'] + '.notifications.lock'), (info.st_uid, info.st_gid)))
                for row in selected:
                    state_path = args.private / 'registrations' / event / (row['receipt_id'] + '.notifications.json')
                    state = read_json(state_path)
                    previous = state.get(channel, {}).get('status')
                    if previous not in {'failed', 'pending'}:
                        raise ValueError('Retry blocked: notification was accepted, attempted or has unknown outcome')
                for row in selected:
                    state_path = args.private / 'registrations' / event / (row['receipt_id'] + '.notifications.json')
                    with opened(state_path) as state_stream:
                        info = os.fstat(state_stream.fileno())
                    state = read_json(state_path)
                    recipient = mailbox(row['email']) if channel == 'participant' else mailbox(args.organizer_to or '')
                    message = EmailMessage()
                    message['From'] = mailbox(args.from_address)
                    message['To'] = recipient
                    message['Subject'] = f'[MIFP] {event} registration {row["receipt_id"]}'
                    message['Message-ID'] = f'<{row["receipt_id"]}.{channel}@mifp.eu>'
                    message.set_content('Registration saved. Receipt ID: ' + row['receipt_id'] + '\n' +
                                        '\n'.join(f'{field}: {row[field]}' for field in fields if field not in {'proof_file', 'proof_size_bytes'}))
                    if channel == 'organizers' and proof_name(row):
                        with opened(args.private / 'uploads' / event / proof_name(row)) as proof:
                            content = proof.read(10 * 1024 * 1024 + 1)
                        if len(content) > 10 * 1024 * 1024:
                            raise ValueError('Proof too large')
                        message.add_attachment(content, maintype='application', subtype='octet-stream', filename=proof_name(row))
                    state[channel] = dict(status='sending', request=args.request, updated_at=datetime.now(timezone.utc).isoformat())
                    atomic_json(state_path, state, (info.st_uid, info.st_gid))
                    try:
                        completed = subprocess.run(['/usr/bin/msmtp', '--file=/dev/null', '--account=default', '--read-envelope-from', '--', recipient],
                                                   input=message.as_bytes(), capture_output=True, timeout=60, check=False)
                        # A failed SMTP transaction can have an ambiguous outcome.
                        outcome = 'accepted' if completed.returncode == 0 else 'unknown'
                    except (OSError, subprocess.TimeoutExpired):
                        outcome = 'unknown'
                    state[channel]['status'] = outcome
                    atomic_json(state_path, state, (info.st_uid, info.st_gid))
                    outcomes.append(outcome)
            result = dict(status='completed', action=action, event=event, count=len(ids), outcomes=outcomes)
        result['approval'] = approval
        atomic_json(marker, result)
        if action == 'delete':
            prune_backups(args.backups, getattr(args, 'keep', 14))
        print(json.dumps(result, sort_keys=True))



def restore(args):
    if not REQUEST.fullmatch(args.request):
        raise ValueError('Invalid backup ID')
    journal = read_json(args.backups / (args.request + '.result.json'))
    if file_digest(args.backups / (args.request + '.tar')) != journal.get('backup_sha256'):
        raise ValueError('Backup integrity check failed')
    with opened(args.backups / (args.request + '.tar')) as source:
        with tarfile.open(fileobj=source, mode='r:') as archive:
            member = archive.getmember('records.json')
            if not member.isfile() or member.size > LIMIT:
                raise ValueError('Invalid backup manifest')
            manifest = json.load(archive.extractfile(member))
            event = key(manifest['event'])
            if args.confirm != event:
                raise ValueError('Explicit conference confirmation required')
            with opened(args.allowlist) as allowlist:
                if any(line.strip('/').split('/')[-2:] == [event, 'regform'] for line in allowlist.read(1024 * 1024).decode().splitlines()):
                    raise ValueError('Revoke PHP authorization before restoring')
            with opened(args.private / 'registrations' / event / '.operations.lock') as operation_lock, opened(args.private / 'registrations' / event / 'registrations.csv', write=True) as stream:
                fcntl.flock(operation_lock, fcntl.LOCK_EX)
                fcntl.flock(stream, fcntl.LOCK_EX)
                info = os.fstat(stream.fileno())
                fields, rows = rows_from(stream.read(LIMIT + 1))
                selected = manifest['rows']
                if manifest['fields'] != fields or any(not RECEIPT.fullmatch(row.get('receipt_id', '')) for row in selected):
                    raise ValueError('Backup schema mismatch')
                ids = [row['receipt_id'] for row in selected]
                if len(ids) != len(set(ids)) or any(set(row) != set(fields) for row in selected):
                    raise ValueError('Backup has duplicate or invalid records')
                existing = {row['receipt_id'] for row in rows}
                if any(row['receipt_id'] in existing for row in selected):
                    raise ValueError('Restore would duplicate existing receipts')
                files = []
                for row in selected:
                    name = proof_name(row)
                    names = [('uploads/' + name, args.private / 'uploads' / event / name)] if name else []
                    notification = row['receipt_id'] + '.notifications.json'
                    if 'notifications/' + notification in archive.getnames():
                        names.append(('notifications/' + notification, args.private / 'registrations' / event / notification))
                    for archived, target in names:
                        item = archive.getmember(archived)
                        if not item.isfile() or item.size > 10 * 1024 * 1024:
                            raise ValueError('Invalid backup attachment')
                        with directory(target.parent) as parent:
                            try:
                                os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                            except FileNotFoundError:
                                pass
                            else:
                                raise ValueError('Restore target already exists')
                        files.append((item, target))
                for item, target in files:
                    with directory(target.parent) as parent:
                        fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as output:
                        os.fchown(output.fileno(), info.st_uid, info.st_gid)
                        output.write(archive.extractfile(item).read())
                        output.flush()
                        os.fsync(output.fileno())
                write_rows(stream, fields, rows + selected)
    atomic_json(args.backups / (args.request + '.restore.json'), dict(event=event, count=len(selected), status='restored'))
    print('Restored selected registrations and private attachments; PHP remains revoked')



def grant_read(args):
    event = key(args.request)
    if args.confirm != event:
        raise ValueError('Explicit conference confirmation required')
    with opened(args.allowlist) as allowlist:
        if any(line.strip('/').split('/')[-2:] == [event, 'regform'] for line in allowlist.read(1024 * 1024).decode().splitlines()):
            raise ValueError('Revoke PHP authorization before changing access')
    with opened(args.private / 'registrations' / event / 'registrations.csv') as stream:
        _, rows = rows_from(stream.read(LIMIT + 1))
    # Validate every target before changing permissions. Never recurse into
    # sessions, temporary files, arbitrary names or other event directories.
    targets = [args.private / 'registrations' / event / 'registrations.csv']
    for row in rows:
        if proof_name(row):
            targets.append(args.private / 'uploads' / event / proof_name(row))
        for suffix in ('.notifications.json', '.notifications.lock'):
            target = args.private / 'registrations' / event / (row['receipt_id'] + suffix)
            try:
                with opened(target):
                    pass
            except FileNotFoundError:
                continue
            targets.append(target)
    for target in targets:
        with opened(target) as stream:
            if os.fstat(stream.fileno()).st_gid != args.php_gid:
                raise ValueError('Unexpected PHP storage group')
    for target in targets:
        with opened(target) as stream:
            os.fchmod(stream.fileno(), 0o640)
    for tree in ('registrations', 'uploads'):
        for target in (args.private / tree, args.private / tree / event):
            with directory(target) as fd:
                if os.fstat(fd).st_gid != args.php_gid:
                    raise ValueError('Unexpected PHP storage directory group')
                os.fchmod(fd, 0o750)
    with directory(args.private / 'registrations' / event) as parent:
        fd = os.open('.operations.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o640, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Unsafe operation lock')
            os.fchown(fd, args.php_uid, args.php_gid)
        finally:
            os.close(fd)
    print('Read-only dashboard access prepared for this conference; PHP remains revoked')



def resolve_notification(args):
    event = key(args.request)
    receipt = args.resolve_receipt
    if args.confirm != event or not args.not_accepted or not RECEIPT.fullmatch(receipt) or args.channel not in {'organizers', 'participant'}:
        raise ValueError('Explicit evidence of non-acceptance and valid identifiers are required')
    with opened(args.private / 'registrations' / event / '.operations.lock') as operation_lock:
        fcntl.flock(operation_lock, fcntl.LOCK_EX)
        with opened(args.private / 'registrations' / event / 'registrations.csv') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            info = os.fstat(stream.fileno())
            _, rows = rows_from(stream.read(LIMIT + 1))
        if receipt not in {row['receipt_id'] for row in rows}:
            raise ValueError('Unknown receipt')
        path = args.private / 'registrations' / event / (receipt + '.notifications.json')
        with notification_lock(path.with_suffix('.lock'), (info.st_uid, info.st_gid)):
            try:
                state = read_json(path)
            except FileNotFoundError:
                state = {}
            previous = state.get(args.channel, {}).get('status', 'unavailable')
            if previous in {'accepted', 'not_requested'}:
                raise ValueError('Accepted or intentionally disabled notifications cannot be reset')
            resolution = os.urandom(16).hex()
            args.backups.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_json(args.backups / (resolution + '.resolution.json'),
                        dict(event=event, channel=args.channel, operation='operator attested non-acceptance', previous=previous))
            state[args.channel] = dict(status='failed', resolution=resolution,
                                      updated_at=datetime.now(timezone.utc).isoformat())
            atomic_json(path, state, (info.st_uid, info.st_gid))
    print('Non-acceptance attested by host operator; a separate dashboard retry request is now permitted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request')
    parser.add_argument('--confirm')
    parser.add_argument('--approve')
    parser.add_argument('--keep', type=int, default=14)
    parser.add_argument('--requests', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--backups', type=Path, required=True)
    parser.add_argument('--allowlist', type=Path, required=True)
    parser.add_argument('--resolve-receipt')
    parser.add_argument('--channel')
    parser.add_argument('--not-accepted', action='store_true')
    parser.add_argument('--grant-read', action='store_true')
    parser.add_argument('--php-uid', type=int)
    parser.add_argument('--php-gid', type=int)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--relay', type=Path, default=Path('/etc/msmtprc'))
    parser.add_argument('--from-address', default='')
    parser.add_argument('--organizer-to', default='')
    args = parser.parse_args()
    try:
        if args.resolve_receipt:
            resolve_notification(args)
        elif args.grant_read:
            grant_read(args)
        elif args.restore:
            restore(args)
        else:
            apply(args)
    except (ValueError, OSError, csv.Error, UnicodeError) as exc:
        # Deliberately do not include transport stderr, CSV content or addresses.
        print('Registration operation refused: ' + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

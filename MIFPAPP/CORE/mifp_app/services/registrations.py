"""Read host-owned submissions; store only operator metadata and explicit requests.

No write operation in this module touches existing PHP registration records.
"""
from __future__ import annotations

import csv
import fcntl
import io
import json
import os
import re
import sqlite3
import stat
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

STATUSES = ("received", "pending verification", "confirmed", "cancelled")
KEY = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")
RECEIPT = re.compile(r"REG-[0-9]{8}-[A-F0-9]{8}\Z")
MAX_CSV_BYTES = 32 * 1024 * 1024


def prepare_local_archive(conferences: Path) -> Path:
    """Prepare private development directories, never records or host storage."""
    conferences = Path(conferences)
    conferences.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = os.open(conferences, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir('.registrations-private', mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        root = os.open('.registrations-private', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for name in ('registrations', 'uploads'):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=root)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                os.close(child)
        finally:
            os.close(root)
    finally:
        os.close(parent)
    return conferences / '.registrations-private'


def component(value: str) -> str:
    if not KEY.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Invalid private storage identifier.")
    return value


@contextmanager
def private_directory(root: Path, *parts: str):
    """Keep a validated directory descriptor for race-safe file operations."""
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("Private storage must use an absolute path.")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.parts[1:], *parts):
            component(part)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def private_file(root: Path, *parts: str, writable=False):
    """No symlink, traversal, device or hard link at any level."""
    with private_directory(root, *parts[:-1]) as parent:
        fd = os.open(component(parts[-1]), (os.O_RDWR if writable else os.O_RDONLY)
                     | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "r+b" if writable else "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Private storage entry must be a regular file without hard links.")
        yield stream


def proof_filename(row):
    name = row.get("proof_file", "")
    if name:
        suffix = name.rsplit(".", 1)[-1]
        if suffix not in {"pdf", "jpg", "jpeg", "png"} or name != f"proof-{row['receipt_id']}.{suffix}":
            raise ValueError("Invalid proof filename.")
    return name


@contextmanager
def registration_lock(stream, exclusive=False):
    deadline = time.monotonic() + 1.0
    while True:
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    try:
        yield
    finally:
        fcntl.flock(stream, fcntl.LOCK_UN)


def event_key(public_path: str) -> str:
    # The portable PHP runtime derives its storage key from the event directory.
    return component(public_path.rstrip("/").split("/")[-1])


def read_records(root: Path, event: str) -> list[dict]:
    component(event)
    # A missing mount is not an empty conference. Only an absent per-event CSV
    # below connected storage may mean that no registrations have been saved.
    for directory in (root, root / "registrations"):
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ValueError("Registration storage is not a directory.")
    try:
        with private_file(root, "registrations", event, "registrations.csv") as stream:
            with registration_lock(stream):
                payload = stream.read(MAX_CSV_BYTES + 1)
    except FileNotFoundError:
        return []
    return parse_records(payload)


def parse_records(payload):
    if len(payload) > MAX_CSV_BYTES:
        raise ValueError("Registration CSV exceeds the supported size.")
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
        headers = reader.fieldnames or []
        required = {"receipt_id", "submitted_at", "first_name", "last_name", "email"}
        if not required.issubset(headers) or len(headers) != len(set(headers)):
            raise ValueError("Registration CSV has invalid or duplicate columns.")
        records, seen = [], set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Registration CSV contains an incomplete row.")
            receipt = row["receipt_id"]
            if not RECEIPT.fullmatch(receipt) or receipt in seen:
                raise ValueError("Registration CSV has an invalid or duplicate receipt ID.")
            seen.add(receipt)
            records.append(row)
        return records
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Registration CSV is malformed.") from exc


def notification_state(root: Path, event: str, receipt: str) -> dict:
    try:
        with private_file(root, "registrations", event, f"{receipt}.notifications.json") as stream:
            payload = stream.read(16385)
        if len(payload) > 16384:
            raise ValueError("Invalid notification state.")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Invalid notification state.")
        allowed = {"pending", "sending", "accepted", "failed", "unknown", "not_requested"}
        return {channel: value.get(channel, {}).get("status", "unknown")
                if isinstance(value.get(channel), dict) and value[channel].get("status") in allowed
                else "unknown" for channel in ("organizers", "participant")}
    except FileNotFoundError:
        return {"organizers": "unavailable", "participant": "unavailable"}
    except (ValueError, OSError):
        return {"organizers": "unknown", "participant": "unknown"}


class RegistrationAdmin:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "registrations-admin.sqlite3"
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (
                    event TEXT NOT NULL, receipt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'received', note TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(event, receipt));
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, event TEXT, receipt TEXT, timestamp TEXT,
                    actor TEXT, operation TEXT);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        try:
            conn.execute("PRAGMA trusted_schema=OFF")
            with conn:
                yield conn
        finally:
            conn.close()

    def enrich(self, event: str, records: list[dict]) -> list[dict]:
        with self.connection() as conn:
            metadata = {row[0]: row[1:] for row in conn.execute(
                "SELECT receipt,status,note,archived FROM metadata WHERE event=?", (event,))}
        enriched = []
        for row in records:
            status, note, archived = metadata.get(row['receipt_id'], ('received', '', 0))
            enriched.append(dict(row, admin_status=status, internal_note=note, archived=bool(archived)))
        return enriched

    def update(self, event: str, receipt: str, status: str, note: str, archived: bool, actor: str):
        if status not in STATUSES or len(note) > 4000:
            raise ValueError("Invalid administrative status or note length.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT status,note,archived FROM metadata WHERE event=? AND receipt=?", (event, receipt)).fetchone() or ('received', '', 0)
            operation = f"status {previous[0]} -> {status}; archived {bool(previous[2])} -> {archived}"
            if previous[1] != note:
                operation += "; note updated"
            conn.execute("INSERT INTO metadata VALUES(?,?,?,?,?) ON CONFLICT(event,receipt) DO UPDATE SET status=excluded.status,note=excluded.note,archived=excluded.archived",
                         (event, receipt, status, note, int(archived)))
            conn.execute("INSERT INTO history(event,receipt,timestamp,actor,operation) VALUES(?,?,?,?,?)",
                         (event, receipt, datetime.now(timezone.utc).isoformat(), actor, operation))

    def history(self, event: str, receipt: str) -> list:
        with self.connection() as conn:
            return conn.execute("SELECT timestamp,actor,operation FROM history WHERE event=? AND receipt=? ORDER BY id DESC LIMIT 100", (event, receipt)).fetchall()

    def queue(self, event: str, receipts: list[str], action: str, actor: str, channel: str = "") -> str:
        if action not in {"delete", "retry"} or not receipts or len(receipts) > 5000:
            raise ValueError("Invalid host request.")
        if action == "retry" and len(receipts) > 25:
            raise ValueError("Retry at most 25 notifications per request.")
        if action == "retry" and channel not in {"organizers", "participant"}:
            raise ValueError("Select a notification channel.")
        if any(not RECEIPT.fullmatch(value) for value in receipts):
            raise ValueError("Invalid receipt ID.")
        request_id = uuid4().hex
        directory = self.directory / "requests"
        directory.mkdir(mode=0o700, exist_ok=True)
        with (directory / f"{request_id}.json").open("x", encoding="utf-8") as stream:
            json.dump(dict(id=request_id, event=component(event), receipts=sorted(set(receipts)),
                           action=action, channel=channel, actor=actor), stream)
        (directory / f"{request_id}.json").chmod(0o600)
        return request_id


def filtered(records: list[dict], values) -> list[dict]:
    query = str(values.get("q", "")).strip().casefold()
    rows = []
    for row in records:
        if query and not any(query in row.get(key, "").casefold() for key in ("first_name", "last_name", "email", "receipt_id")):
            continue
        if any(values.get(field) and row.get(key, "") != values[field] for field, key in (
                ("status", "admin_status"), ("type", "registration_type"), ("payment", "payment_method"),
                ("country", "country"), ("affiliation", "affiliation"))):
            continue
        if values.get("duplicates") == "1" and row.get("potential_duplicates", 0) < 2:
            continue
        if values.get("from") and row["submitted_at"][:10] < values["from"]:
            continue
        if values.get("to") and row["submitted_at"][:10] > values["to"]:
            continue
        if row.get("archived", False) and values.get("archived") != "1":
            continue
        rows.append(row)
    sort = values.get("sort", "submitted_at")
    if sort not in {"submitted_at", "first_name", "last_name", "receipt_id", "admin_status"}:
        sort = "submitted_at"
    return sorted(rows, key=lambda row: row.get(sort, "").casefold(), reverse=values.get("order", "desc") == "desc")


def mark_potential_duplicates(records: list[dict]) -> list[dict]:
    """Same nonempty email is a review hint, never proof of a duplicate."""
    counts = Counter(row.get("email", "").strip().casefold() for row in records if row.get("email", "").strip())
    return [dict(row, potential_duplicates=counts.get(row.get("email", "").strip().casefold(), 0)) for row in records]


def registration_summary(records: list[dict], today=None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    days, weeks = Counter(), Counter()
    recent, invalid_dates, proof_count, proof_bytes, known_sizes = 0, 0, 0, 0, 0
    for row in records:
        try:
            stamp = datetime.fromisoformat(row.get("submitted_at", "").replace("Z", "+00:00"))
            day = stamp.astimezone(timezone.utc).date() if stamp.tzinfo else stamp.date()
            days[day.isoformat()] += 1
            weeks[(day - timedelta(days=day.weekday())).isoformat()] += 1
            recent += today - timedelta(days=6) <= day <= today
        except ValueError:
            invalid_dates += 1
        if row.get("proof_file", "").strip():
            proof_count += 1
            try:
                size = int(row.get("proof_size_bytes", ""))
                if size >= 0:
                    proof_bytes += size
                    known_sizes += 1
            except (TypeError, ValueError):
                pass
    def distribution(field):
        counts = Counter(row.get(field, "").strip() or "Not provided" for row in records)
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if len(top) > 8:
            top = top[:8] + [("Other entries (combined)", sum(count for _, count in top[8:]))]
        return top
    return dict(total=len(records), recent=recent, invalid_dates=invalid_dates,
                proof_count=proof_count, proof_bytes=proof_bytes, known_sizes=known_sizes,
                potential_duplicates=sum(row.get("potential_duplicates", 0) > 1 for row in records),
                charts=[("Daily submissions (UTC)", sorted(days.items())),
                        ("Weekly submissions (Monday, UTC)", sorted(weeks.items())),
                        ("Registration type", distribution("registration_type")),
                        ("Country", distribution("country")), ("Affiliation", distribution("affiliation"))])


def spreadsheet_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) or text.startswith(("\t", "\r", "\n")) else text

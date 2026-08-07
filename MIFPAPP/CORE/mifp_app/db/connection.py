from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

_LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    """Return current UTC time as ISO string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    """Return SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
    return conn


def connect_readonly(db_path: Path, *, timeout: float = 0.25) -> sqlite3.Connection:
    """Open a non-mutating connection without renegotiating WAL mode.

    Public requests use this path so a dashboard writer cannot make them wait
    on ``PRAGMA journal_mode`` or accidentally start a write transaction.
    """
    resolved = Path(db_path).resolve()
    uri = f"file:{quote(str(resolved))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {max(1, int(timeout * 1000))}")
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def begin_immediate(
    conn: sqlite3.Connection,
    *,
    operation: str = "database write",
    timeout: float | None = None,
) -> None:
    """Acquire SQLite's write lock while tolerating short-lived contention.

    Only lock acquisition is retried. Callers can then execute and commit their
    mutation exactly once, avoiding duplicate inserts after ambiguous failures.
    """
    if conn.in_transaction:
        return
    if timeout is None:
        try:
            timeout = float(os.getenv("SQLITE_WRITE_LOCK_TIMEOUT_SECONDS", "20"))
        except ValueError:
            timeout = 20.0
    timeout = min(60.0, max(1.0, timeout))
    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        attempts += 1
        try:
            conn.execute("BEGIN IMMEDIATE")
            if attempts > 1:
                _LOGGER.info(
                    "database write lock acquired operation=%s attempts=%d",
                    operation,
                    attempts,
                )
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            _LOGGER.warning(
                "waiting for database write lock operation=%s attempt=%d",
                operation,
                attempts,
            )
            time.sleep(min(0.25 * attempts, 1.0))


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

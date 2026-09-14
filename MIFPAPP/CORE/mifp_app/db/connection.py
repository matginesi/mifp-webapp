from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
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


def _sqlite_file_uri(db_path: Path, *, mode: str) -> str:
    resolved = Path(db_path).resolve()
    return f"file:{quote(str(resolved), safe='/')}?mode={mode}"


def connect(db_path: Path) -> sqlite3.Connection:
    """Open an existing runtime database for read/write access.

    Runtime code is deliberately fail-closed: this helper never creates the
    database file or its parent directory. Database creation belongs only to
    the explicit ``db-init``/``mifpctl first-deploy`` lifecycle.
    """
    path = Path(db_path)
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"database must be an existing regular file: {path}")
    conn = sqlite3.connect(
        _sqlite_file_uri(path, mode="rw"),
        uri=True,
        timeout=10,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_readonly(db_path: Path, *, timeout: float = 0.25) -> sqlite3.Connection:
    """Open a non-mutating connection without renegotiating WAL mode.

    Public requests use this path so a dashboard writer cannot make them wait
    on ``PRAGMA journal_mode`` or accidentally start a write transaction.
    """
    uri = _sqlite_file_uri(Path(db_path), mode="ro")
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




@contextmanager
def write_transaction(
    conn: sqlite3.Connection,
    *,
    operation: str = "database write",
    timeout: float | None = None,
):
    """Run one explicit write transaction with automatic rollback.

    Existing transactions are respected, so services can safely compose this
    helper without committing work owned by an outer operation.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        begin_immediate(conn, operation=operation, timeout=timeout)
    try:
        yield conn
        if owns_transaction:
            conn.commit()
    except Exception:
        if owns_transaction and conn.in_transaction:
            conn.rollback()
        raise


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

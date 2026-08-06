from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from flask import has_request_context, request


def get_client_ip() -> str:
    """Return the real client IP, respecting TRUST_PROXY.

    When TRUST_PROXY is enabled (VPS behind reverse proxy), the IP is
    read from the first entry in the X-Forwarded-For header.

    When TRUST_PROXY is disabled (VM, dev, or direct-connect), only
    request.remote_addr (the TCP peer) is trusted — X-Forwarded-For
    is ignored to prevent spoofing.
    """
    if not has_request_context():
        return "unknown"
    # ProxyFix normalizes remote_addr when TRUST_PROXY is enabled. Reading the
    # first X-Forwarded-For value here would trust a value supplied by clients.
    return request.remote_addr or "unknown"


def prune_ip_rate_bucket(
    bucket: OrderedDict[str, list[float]],
    window_seconds: float,
    *,
    max_clients: int = 10_000,
) -> None:
    """Drop expired attempts and cap an in-memory per-IP rate-limit bucket."""
    now = time.time()
    stale: list[str] = []
    for ip, attempts in bucket.items():
        bucket[ip] = [attempt for attempt in attempts if now - attempt < window_seconds]
        if not bucket[ip]:
            stale.append(ip)
    for ip in stale:
        del bucket[ip]
    while len(bucket) > max_clients:
        bucket.popitem(last=False)


# ---------------------------------------------------------------------------
# Shared sliding-window rate limiter.
#
# A small SQLite store next to the application database keeps rate-limit state
# visible to every gunicorn worker (in-memory buckets are per-process and would
# double the effective limits under >1 workers). Fail-open on storage errors:
# a transient "database is locked" must never lock legitimate users out.
# ---------------------------------------------------------------------------

_RATE_LIMIT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS rate_limits ("
    " action TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL)"
)
_RATE_LIMIT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_rate_limits_action ON rate_limits(action, ts)"
)
_SETUP_LOCK = threading.Lock()


def _store_path(db_path: str | None) -> str:
    if not db_path and has_request_context():
        from flask import current_app

        configured = current_app.config.get("DATABASE_PATH")
        if configured:
            db_path = str(configured)
    if not db_path:
        db_path = os.getenv("DATABASE_PATH", "")
    if db_path:
        return str(Path(db_path).parent / "rate_limit.sqlite3")
    return str(Path("rate_limit.sqlite3").resolve())


def _connect_store(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(_RATE_LIMIT_SCHEMA)
    conn.execute(_RATE_LIMIT_INDEX)
    return conn


def ip_rate_allowed(
    action: str,
    key: str,
    *,
    limit: int,
    window_seconds: float,
    db_path: str | None = None,
    now: float | None = None,
) -> bool:
    """Record an attempt and report whether it is within the allowed window.

    Shared across processes via a SQLite store. ``True`` means the attempt is
    allowed (and is recorded); ``False`` means the limit has been reached.
    """
    if limit <= 0 or window_seconds <= 0:
        return True
    ts = now if now is not None else time.time()
    path = _store_path(db_path)
    with _SETUP_LOCK:
        conn = _connect_store(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM rate_limits WHERE action = ? AND ts < ?",
                (action, ts - window_seconds),
            )
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_limits WHERE action = ? AND key = ?",
                (action, key),
            ).fetchone()
            if row[0] >= limit:
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO rate_limits(action, key, ts) VALUES (?, ?, ?)",
                (action, key, ts),
            )
            conn.commit()
            return True
        except sqlite3.OperationalError:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return True
        finally:
            conn.close()


def reset_rate_limits(action: str | None = None, db_path: str | None = None) -> bool:
    """Clear rate-limit state (used by the console and the test suite)."""
    path = _store_path(db_path)
    with _SETUP_LOCK:
        conn = _connect_store(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if action is None:
                conn.execute("DELETE FROM rate_limits")
            else:
                conn.execute("DELETE FROM rate_limits WHERE action = ?", (action,))
            conn.commit()
            return True
        except sqlite3.Error:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return False
        finally:
            conn.close()

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

from flask import has_request_context, request


def admin_password_matches(password: str, configured_hash: str | None = None) -> bool:
    """Verify the configured administrator password without leaking hash errors.

    Sensitive dashboard actions reuse this helper so a missing or malformed
    deployment hash fails closed consistently instead of raising a 500 from a
    route-specific password check.
    """
    if configured_hash is None and has_request_context():
        from flask import current_app

        configured_hash = str(current_app.config.get("ADMIN_PASSWORD_HASH") or "")
    configured_hash = str(configured_hash or "")
    if not configured_hash or not password:
        return False
    try:
        from werkzeug.security import check_password_hash

        return bool(check_password_hash(configured_hash, password))
    except (TypeError, ValueError):
        _LOGGER.error("administrator password hash is invalid")
        return False


def get_client_ip() -> str:
    """Return the real client IP, respecting TRUST_PROXY.

    ``ProxyFix`` is configured by the application only when TRUST_PROXY is
    enabled, so ``request.remote_addr`` is already the normalized client IP in
    that mode. Without ProxyFix it remains the direct TCP peer and untrusted
    forwarded headers are ignored.
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
    now: float | None = None,
) -> None:
    """Drop expired attempts and cap an in-memory per-IP rate-limit bucket."""
    now = time.time() if now is None else now
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
# double the effective limits under >1 workers). On storage errors a bounded
# per-process fallback preserves throttling without making SQLite availability a
# hard dependency for legitimate users.
# ---------------------------------------------------------------------------

_RATE_LIMIT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS rate_limits ("
    " action TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL)"
)
_RATE_LIMIT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_rate_limits_action ON rate_limits(action, ts)"
)
_SETUP_LOCK = threading.Lock()
_FALLBACK_LOCK = threading.Lock()
_FALLBACK_BUCKET: OrderedDict[str, list[float]] = OrderedDict()
_LOGGER = logging.getLogger(__name__)


def _fallback_rate_allowed(
    action: str, key: str, *, limit: int, window_seconds: float, now: float
) -> bool:
    """Per-process safety net used only when the shared SQLite store fails."""
    bucket_key = f"{action}\0{key}"
    with _FALLBACK_LOCK:
        prune_ip_rate_bucket(_FALLBACK_BUCKET, window_seconds, now=now)
        attempts = _FALLBACK_BUCKET.setdefault(bucket_key, [])
        _FALLBACK_BUCKET.move_to_end(bucket_key)
        if len(attempts) >= limit:
            return False
        attempts.append(now)
        return True


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
        conn: sqlite3.Connection | None = None
        try:
            conn = _connect_store(path)
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
        except (sqlite3.Error, OSError) as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            _LOGGER.warning(
                "shared rate-limit store unavailable; using in-memory fallback action=%s error=%s",
                action,
                type(exc).__name__,
            )
            return _fallback_rate_allowed(
                action, key, limit=limit, window_seconds=window_seconds, now=ts
            )
        finally:
            if conn is not None:
                conn.close()


def reset_rate_limits(action: str | None = None, db_path: str | None = None) -> bool:
    """Clear shared and fallback rate-limit state."""
    with _FALLBACK_LOCK:
        if action is None:
            _FALLBACK_BUCKET.clear()
        else:
            prefix = f"{action}\0"
            for bucket_key in [key for key in _FALLBACK_BUCKET if key.startswith(prefix)]:
                del _FALLBACK_BUCKET[bucket_key]
    path = _store_path(db_path)
    with _SETUP_LOCK:
        conn: sqlite3.Connection | None = None
        try:
            conn = _connect_store(path)
            conn.execute("BEGIN IMMEDIATE")
            if action is None:
                conn.execute("DELETE FROM rate_limits")
            else:
                conn.execute("DELETE FROM rate_limits WHERE action = ?", (action,))
            conn.commit()
            return True
        except (sqlite3.Error, OSError):
            if conn is not None:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            return False
        finally:
            if conn is not None:
                conn.close()

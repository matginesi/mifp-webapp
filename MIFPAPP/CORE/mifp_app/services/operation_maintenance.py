from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from ..db.connection import begin_immediate, connect
from ..utils.logger import audit_log

F = TypeVar("F", bound=Callable)


_COUNT_KEY = "maintenance_operation_count"
_PREVIOUS_ENABLED_KEY = "maintenance_operation_previous_enabled"
_PREVIOUS_MESSAGE_KEY = "maintenance_operation_previous_message"
_PID_KEY = "maintenance_operation_pid"
_STARTED_KEY = "maintenance_operation_started_at"
def _lock_timeout_seconds() -> float:
    try:
        value = float(os.getenv("MAINTENANCE_LOCK_TIMEOUT_SECONDS", "20"))
    except ValueError:
        value = 20.0
    return min(60.0, max(1.0, value))


def maintenance_marker_path(database_path: str | Path) -> Path:
    path = Path(database_path)
    return path.parent / f".{path.name}.work-in-progress"


def _write_marker(database_path: Path, operation: str) -> None:
    marker = maintenance_marker_path(database_path)
    temporary = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            "Secure maintenance in progress. Please try again shortly.",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o640)
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_marker(database_path: Path) -> None:
    maintenance_marker_path(database_path).unlink(missing_ok=True)


def _crash_timeout_seconds() -> float:
    try:
        value = float(os.getenv("MAINTENANCE_CRASH_TIMEOUT_SECONDS", "21600"))
    except ValueError:
        value = 21600.0
    return max(0.0, value)


def _pid_alive(pid: int) -> bool:
    """Return True only when ``pid`` refers to a running process.

    ``PermissionError`` means the process exists but belongs to another user,
    so it must be treated as alive. ``ProcessLookupError`` is the definitive
    dead signal; any other OSError is resolved conservatively as dead so the
    gate can recover rather than stay stuck forever.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _started_age_seconds(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        started = datetime.fromisoformat(value)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (datetime.now(UTC) - started).total_seconds()


def _reset_operation_state(conn: sqlite3.Connection) -> None:
    """Restore the administrator's prior mode and drop the operation guard."""
    previous_enabled = _setting(conn, _PREVIOUS_ENABLED_KEY, "0")
    previous_message = _setting(conn, _PREVIOUS_MESSAGE_KEY, "")
    _write(conn, "maintenance_enabled", previous_enabled)
    _write(conn, "maintenance_message", previous_message)
    conn.execute(
        "DELETE FROM settings WHERE key IN (?,?,?,?,?)",
        (_COUNT_KEY, _PREVIOUS_ENABLED_KEY, _PREVIOUS_MESSAGE_KEY, _PID_KEY, _STARTED_KEY),
    )
    conn.commit()


def reap_crashed_operation(
    database_path: str | Path,
    *,
    logger: logging.Logger | None = None,
) -> bool:
    """Clear a protected operation whose owning process is gone and expired.

    A hard crash (SIGKILL/OOM) can leave ``maintenance_enabled=1`` plus an
    operation counter in the settings table without anyone ever running
    ``_finish``. This reaper only acts when all of the following hold:

    * the operation counter is positive and maintenance is enabled;
    * a owning PID was recorded for the first protected operation;
    * that PID is no longer alive;
    * the operation started longer ago than ``MAINTENANCE_CRASH_TIMEOUT_SECONDS``
      (default 6 hours) so a recently restarted worker is never disturbed.

    When those conditions are met the guard is reset to the administrator's
    previous mode, the counter and owner keys are removed, and the stale marker
    file is deleted. Returns ``True`` when a crashed operation was reaped.
    """
    path = Path(database_path)
    try:
        with connect(path) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
            ).fetchone():
                return False
            if _setting(conn, "maintenance_enabled", "0") != "1":
                return False
            count = max(0, int(_setting(conn, _COUNT_KEY, "0") or 0))
            if count <= 0:
                return False
            pid = int(_setting(conn, _PID_KEY, "0") or 0)
            if pid <= 0 or _pid_alive(pid):
                return False
            started = _setting(conn, _STARTED_KEY, "")
            age = _started_age_seconds(started)
            if age is None or age < _crash_timeout_seconds():
                return False
            _reset_operation_state(conn)
        _remove_marker(path)
        marker = maintenance_marker_path(path)
        if logger:
            logger.warning(
                "crashed operation reaped pid=%d age_seconds=%.0f marker=%s",
                pid,
                age,
                marker.name,
            )
        audit_log(
            "maintenance.crashed_operation_reaped",
            "stale operation from a crashed process was cleared",
            category="admin",
            owner_pid=pid,
            age_seconds=round(age),
            marker_name=marker.name,
        )
        return True
    except (OSError, sqlite3.Error, ValueError):
        # On uncertainty, preserve the safety gate.
        if logger:
            logger.exception("crashed operation reaper check failed")
        return False


def force_clear_maintenance(
    database_path: str | Path,
    *,
    logger: logging.Logger | None = None,
) -> bool:
    """Manually clear a stuck operation guard (admin recovery)."""
    path = Path(database_path)
    try:
        with connect(path) as conn:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
            ).fetchone():
                return False
            if max(0, int(_setting(conn, _COUNT_KEY, "0") or 0)) <= 0:
                return False
            _reset_operation_state(conn)
        _remove_marker(path)
        marker = maintenance_marker_path(path)
        if logger:
            logger.warning("maintenance operation force-cleared marker=%s", marker.name)
        audit_log(
            "maintenance.force_cleared",
            "stuck maintenance operation force-cleared by administrator",
            category="admin",
            marker_name=marker.name,
        )
        return True
    except (OSError, sqlite3.Error, ValueError):
        if logger:
            logger.exception("force clear maintenance failed")
        return False


def clear_stale_operation_marker(
    database_path: str | Path,
    *,
    logger: logging.Logger | None = None,
) -> bool:
    """Remove an orphan marker only when no protected operation is active.

    A crashed operation is reaped first so a hard crash is recovered on the
    next boot or the next protected operation instead of requiring manual
    database editing.
    """
    path = Path(database_path)
    if reap_crashed_operation(path, logger=logger):
        return True
    marker = maintenance_marker_path(path)
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        with connect(path) as conn:
            settings_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
            ).fetchone()
            active_count = (
                max(0, int(_setting(conn, _COUNT_KEY, "0") or 0))
                if settings_exists
                else 0
            )
    except (OSError, sqlite3.Error, ValueError):
        # On uncertainty, preserve the safety gate.
        if logger:
            logger.exception("stale work_in_progress marker check failed")
        return False
    if active_count:
        return False
    _remove_marker(path)
    if logger:
        logger.warning("stale work_in_progress marker removed path=%s", marker)
    audit_log(
        "maintenance.stale_marker_removed",
        "orphan work in progress marker removed",
        category="admin",
        marker_name=marker.name,
    )
    return True


def _setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row["value"] or "") if row else default


def _write(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
        (key, value),
    )


def _begin(
    database_path: Path,
    operation: str,
    *,
    logger: logging.Logger | None = None,
) -> int | None:
    reap_crashed_operation(database_path, logger=logger)
    with connect(database_path) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone():
            return None
        begin_immediate(conn, operation=operation, timeout=_lock_timeout_seconds())
        count = max(0, int(_setting(conn, _COUNT_KEY, "0") or 0))
        if count == 0:
            _write(conn, _PREVIOUS_ENABLED_KEY, _setting(conn, "maintenance_enabled", "0"))
            _write(conn, _PREVIOUS_MESSAGE_KEY, _setting(conn, "maintenance_message", ""))
            _write(conn, _PID_KEY, str(os.getpid()))
            _write(conn, _STARTED_KEY, datetime.now(UTC).isoformat(timespec="seconds"))
        count += 1
        _write(conn, _COUNT_KEY, str(count))
        _write(conn, "maintenance_enabled", "1")
        _write(
            conn,
            "maintenance_message",
            "Secure maintenance in progress. Please try again shortly.",
        )
        conn.commit()
        _write_marker(database_path, operation)
        return count


def _finish(database_path: Path, operation: str) -> tuple[int, str]:
    with connect(database_path) as conn:
        begin_immediate(
            conn,
            operation=f"{operation}: restore",
            timeout=_lock_timeout_seconds(),
        )
        count = max(0, int(_setting(conn, _COUNT_KEY, "1") or 1) - 1)
        if count:
            _write(conn, _COUNT_KEY, str(count))
            conn.commit()
            return count, "1"
        previous_enabled = _setting(conn, _PREVIOUS_ENABLED_KEY, "0")
        previous_message = _setting(conn, _PREVIOUS_MESSAGE_KEY, "")
        _write(conn, "maintenance_enabled", previous_enabled)
        _write(conn, "maintenance_message", previous_message)
        conn.execute(
            "DELETE FROM settings WHERE key IN (?,?,?,?,?)",
            (_COUNT_KEY, _PREVIOUS_ENABLED_KEY, _PREVIOUS_MESSAGE_KEY, _PID_KEY, _STARTED_KEY),
        )
        conn.commit()
        _remove_marker(database_path)
        return 0, previous_enabled


@contextmanager
def operation_maintenance(
    database_path: str | Path,
    operation: str,
    *,
    logger: logging.Logger | None = None,
) -> Iterator[dict[str, Any]]:
    """Temporarily gate the public site for a protected storage operation.

    Nested/concurrent operations use a database-backed reference count. The
    administrator's previous Work in Progress setting and message are restored
    after the final operation, including exceptional exits.
    """
    path = Path(database_path)
    label = str(operation or "operation").strip()[:100] or "operation"
    count = _begin(path, label, logger=logger)
    if count is None:
        # Standalone/minimal SQLite files can still use the backup helper even
        # when they are not a migrated MIFP database.
        yield {}
        return
    if logger:
        logger.info("work_in_progress enabled operation=%s active_operations=%d", label, count)
    audit_log(
        "maintenance.operation_enabled",
        "work in progress enabled for protected operation",
        category="admin",
        operation=label,
        active_operations=count,
    )
    error: BaseException | None = None
    state: dict[str, Any] = {}
    try:
        yield state
    except BaseException as exc:
        error = exc
        raise
    finally:
        remaining, restored = _finish(path, label)
        failed = error is not None or state.get("outcome") == "failure"
        error_type = type(error).__name__ if error else state.get("error_type")
        if logger:
            logger.info(
                "work_in_progress operation finished operation=%s active_operations=%d restored=%s outcome=%s status_code=%s",
                label,
                remaining,
                restored,
                "failure" if failed else "success",
                state.get("status_code"),
            )
        audit_log(
            "maintenance.operation_disabled" if remaining == 0 else "maintenance.operation_finished",
            "protected operation released work in progress mode",
            category="admin",
            operation=label,
            active_operations=remaining,
            restored_enabled=restored,
            outcome="failure" if failed else "success",
            error_type=error_type,
            status_code=state.get("status_code"),
        )


def maintenance_guarded(operation: str, *, methods: tuple[str, ...] = ("POST",)):
    """Flask route decorator for protected operations."""
    def decorator(view: F) -> F:
        @wraps(view)
        def wrapped(*args, **kwargs):
            from flask import current_app, request

            if request.method not in methods:
                return view(*args, **kwargs)
            with operation_maintenance(
                current_app.config["DATABASE_PATH"],
                operation,
                logger=current_app.logger,
            ) as operation_state:
                result = view(*args, **kwargs)
                status_code = getattr(result, "status_code", None)
                if status_code is None and isinstance(result, tuple) and len(result) > 1:
                    status_code = result[1]
                try:
                    normalized_status = int(status_code or 200)
                except (TypeError, ValueError):
                    normalized_status = 500
                operation_state["status_code"] = normalized_status
                if normalized_status >= 400:
                    operation_state["outcome"] = "failure"
                    operation_state["error_type"] = f"HTTP {normalized_status}"
                return result
        return wrapped  # type: ignore[return-value]
    return decorator

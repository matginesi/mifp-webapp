from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..db.connection import connect
from ..runtime_storage import (
    available_bytes,
    prune_runtime_exports,
    prune_runtime_logs,
    runtime_export_retention_plan,
    runtime_log_retention_plan,
)
from ..utils.logger import cleanup_metrics_daily
from .admin_safety import (
    DATABASE_BACKUP_LIMIT,
    automatic_sqlite_backups,
    backup_sqlite_database,
    prune_sqlite_backups,
)


def _size(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


CLOSED_JOIN_REQUEST_STATES = ("rejected", "archived")


def eligible_join_request_ids(conn: sqlite3.Connection, retention_days: int) -> list[int]:
    """Ids of closed membership applications past their retention period.

    Only ``rejected`` and ``archived`` requests are eligible. ``pending`` and
    ``in_review`` are work in progress, and ``approved`` requests are the audit
    trail of a member record that still exists, so neither may be deleted by a
    storage-maintenance pass.
    """
    if retention_days <= 0:
        return []
    placeholders = ",".join("?" for _ in CLOSED_JOIN_REQUEST_STATES)
    rows = conn.execute(
        f"SELECT id FROM join_requests WHERE status IN ({placeholders}) "
        "AND COALESCE(reviewed_at, created_at) < datetime('now', ?) ORDER BY id",
        (*CLOSED_JOIN_REQUEST_STATES, f"-{int(retention_days)} days"),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _join_request_retention_days(config: dict[str, Any]) -> int:
    try:
        return max(0, int(config.get("JOIN_REQUEST_RETENTION_DAYS", 730)))
    except (TypeError, ValueError):
        return 730


def _log_retention_days(config: dict[str, Any]) -> int:
    try:
        return max(0, int(config.get("LOG_RETENTION_DAYS", 30)))
    except (TypeError, ValueError):
        return 30


def safety_operations_preview(config: dict[str, Any]) -> dict[str, Any]:
    db_path = Path(config["DATABASE_PATH"])
    export_dir = Path(config["EXPORT_DIR"])
    exports = runtime_export_retention_plan(
        export_dir,
        max_files=int(config["EXPORT_MAX_FILES"]),
        max_bytes=int(config["EXPORT_MAX_BYTES"]),
        max_age_days=int(config["EXPORT_RETENTION_DAYS"]),
    )
    backups = automatic_sqlite_backups(db_path)
    old_backups = backups[DATABASE_BACKUP_LIMIT:]
    join_retention_days = _join_request_retention_days(config)
    with connect(db_path) as conn:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        member_count = int(conn.execute("SELECT COUNT(*) FROM members").fetchone()[0])
        retention = int(config.get("PRIVACY_SAFE_METRICS_RETENTION_DAYS", 730))
        expired_metrics = int(conn.execute(
            "SELECT COUNT(*) FROM metrics_daily WHERE date < date('now', ?)",
            (f"-{retention} days",),
        ).fetchone()[0]) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metrics_daily'"
        ).fetchone() else 0
        eligible_join_requests = len(eligible_join_request_ids(conn, join_retention_days))
    log_retention_days = _log_retention_days(config)
    prunable_logs = runtime_log_retention_plan(
        Path(config["LOG_DIR"]), max_age_days=log_retention_days
    )
    reclaimable = (
        _size(exports) + _size(old_backups) + _size(prunable_logs) + page_size * free_pages
    )
    return {
        "database": {
            "path": str(db_path),
            "size": db_path.stat().st_size,
            "quick_check": quick_check,
            "free_pages_bytes": page_size * free_pages,
            "expired_metrics": expired_metrics,
        },
        "backup": {
            "retained": min(len(backups), DATABASE_BACKUP_LIMIT),
            "prunable": len(old_backups),
            "prunable_bytes": _size(old_backups),
        },
        "join_requests": {
            "retention_days": join_retention_days,
            "eligible": eligible_join_requests,
        },
        "logs": {
            "retention_days": log_retention_days,
            "prunable": len(prunable_logs),
            "prunable_bytes": _size(prunable_logs),
        },
        "export": {
            "scope": "all",
            "format": "mifp-jsonl-v2 ZIP",
            "includes_assets": True,
        },
        "members": member_count,
        "storage": {
            "exports_prunable": len(exports),
            "exports_prunable_bytes": _size(exports),
            "reclaimable_bytes": reclaimable,
            "free_bytes": available_bytes(db_path.parent),
            "reserve_bytes": int(config.get("STORAGE_MIN_FREE_BYTES", 0)),
        },
    }


def execute_safe_cleanup(config: dict[str, Any]) -> dict[str, Any]:
    db_path = Path(config["DATABASE_PATH"])
    backup = backup_sqlite_database(
        db_path,
        label="safety-cleanup",
        reserve_bytes=int(config.get("STORAGE_MIN_FREE_BYTES", 0)),
    )
    if backup is None:
        raise RuntimeError("A verified safety backup could not be created")

    metrics_deleted = cleanup_metrics_daily(
        str(db_path),
        int(config.get("PRIVACY_SAFE_METRICS_RETENTION_DAYS", 730)),
    )
    join_retention_days = _join_request_retention_days(config)
    log_retention_days = _log_retention_days(config)
    join_requests_deleted = 0
    with connect(db_path) as conn:
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if quick != "ok" or foreign_keys:
            raise sqlite3.DatabaseError("Database verification failed before cleanup")
        eligible = eligible_join_request_ids(conn, join_retention_days)
        if eligible:
            placeholders = ",".join("?" for _ in eligible)
            # Re-check the status predicate inside the DELETE so a request that
            # changed state between the SELECT and the DELETE is never removed.
            status_placeholders = ",".join("?" for _ in CLOSED_JOIN_REQUEST_STATES)
            cursor = conn.execute(
                f"DELETE FROM join_requests WHERE id IN ({placeholders}) "
                f"AND status IN ({status_placeholders})",
                (*eligible, *CLOSED_JOIN_REQUEST_STATES),
            )
            join_requests_deleted = int(cursor.rowcount or 0)
            # VACUUM cannot run inside a transaction, so the deletion is committed
            # before compaction starts.
            conn.commit()
        before = db_path.stat().st_size
        conn.execute("VACUUM")
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES"
            "('last_safety_cleanup',datetime('now'),CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP"
        )
        conn.commit()
        if str(conn.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise sqlite3.DatabaseError("Database verification failed after cleanup")
    removed_exports = prune_runtime_exports(
        Path(config["EXPORT_DIR"]),
        max_files=int(config["EXPORT_MAX_FILES"]),
        max_bytes=int(config["EXPORT_MAX_BYTES"]),
        max_age_days=int(config["EXPORT_RETENTION_DAYS"]),
    )
    removed_backups = prune_sqlite_backups(db_path)
    removed_logs = prune_runtime_logs(
        Path(config["LOG_DIR"]), max_age_days=log_retention_days
    )
    return {
        "backup": backup.name,
        "exports_removed": len(removed_exports),
        "backups_removed": len(removed_backups),
        "metrics_deleted": metrics_deleted,
        "log_files_removed": len(removed_logs),
        "log_retention_days": log_retention_days,
        # Aggregate counts only: the audit record must not name applicants.
        "join_requests_deleted": join_requests_deleted,
        "join_request_retention_days": join_retention_days,
        "database_bytes_reclaimed": max(0, before - db_path.stat().st_size),
    }

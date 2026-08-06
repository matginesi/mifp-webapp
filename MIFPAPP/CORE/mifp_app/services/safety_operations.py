from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..db.connection import connect
from ..runtime_storage import (
    available_bytes,
    prune_runtime_exports,
    runtime_export_retention_plan,
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
    with connect(db_path) as conn:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        retention = int(config.get("PRIVACY_SAFE_METRICS_RETENTION_DAYS", 730))
        expired_metrics = int(conn.execute(
            "SELECT COUNT(*) FROM metrics_daily WHERE date < date('now', ?)",
            (f"-{retention} days",),
        ).fetchone()[0]) if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metrics_daily'"
        ).fetchone() else 0
    reclaimable = _size(exports) + _size(old_backups) + page_size * free_pages
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
        "export": {
            "scope": "all",
            "format": "mifp-export-v1 ZIP",
            "includes_assets": True,
        },
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
    with connect(db_path) as conn:
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if quick != "ok" or foreign_keys:
            raise sqlite3.DatabaseError("Database verification failed before cleanup")
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
    return {
        "backup": backup.name,
        "exports_removed": len(removed_exports),
        "backups_removed": len(removed_backups),
        "metrics_deleted": metrics_deleted,
        "database_bytes_reclaimed": max(0, before - db_path.stat().st_size),
    }

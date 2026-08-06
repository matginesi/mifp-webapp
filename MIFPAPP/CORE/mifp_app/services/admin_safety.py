from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..runtime_storage import require_free_space
from .operation_maintenance import operation_maintenance

DATABASE_BACKUP_LIMIT = 2


def _automatic_backup_pattern(db_path: Path) -> re.Pattern[str]:
    """Match only snapshots created by :func:`backup_sqlite_database`."""
    return re.compile(
        rf"^{re.escape(db_path.stem)}-[A-Za-z0-9_-]+-"
        rf"\d{{8}}-\d{{6}}-\d{{6}}{re.escape(db_path.suffix)}$"
    )


def automatic_sqlite_backups(db_path: Path) -> list[Path]:
    db_path = Path(db_path)
    backup_dir = db_path.parent / "backups"
    if not backup_dir.is_dir():
        return []
    pattern = _automatic_backup_pattern(db_path)
    return sorted(
        (
            path
            for path in backup_dir.iterdir()
            if path.is_file() and not path.is_symlink() and pattern.fullmatch(path.name)
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def prune_sqlite_backups(db_path: Path, *, keep: int = DATABASE_BACKUP_LIMIT) -> list[Path]:
    """Keep the newest automatic snapshots without touching manual files."""
    if keep < 0:
        raise ValueError("backup retention cannot be negative")

    snapshots = automatic_sqlite_backups(db_path)
    removed: list[Path] = []
    for snapshot in snapshots[keep:]:
        try:
            snapshot.unlink()
        except FileNotFoundError:
            continue
        removed.append(snapshot)
    return removed


def backup_sqlite_database(
    db_path: Path,
    *,
    label: str = "admin",
    reserve_bytes: int | None = None,
    _maintenance_guard: bool = True,
) -> Path | None:
    """Create a verified SQLite backup and retain the newest two snapshots."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    if _maintenance_guard:
        with operation_maintenance(
            db_path,
            f"database backup: {label}",
            logger=logging.getLogger(__name__),
        ):
            return backup_sqlite_database(
                db_path,
                label=label,
                reserve_bytes=reserve_bytes,
                _maintenance_guard=False,
            )
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if reserve_bytes is None:
        reserve_bytes = max(0, int(os.getenv("STORAGE_MIN_FREE_MB", "0"))) * 1024 * 1024
    require_free_space(
        backup_dir,
        operation_bytes=max(db_path.stat().st_size, 1),
        reserve_bytes=reserve_bytes,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label)[:48] or "admin"
    target = backup_dir / f"{db_path.stem}-{safe_label}-{stamp}{db_path.suffix}"
    source = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=10)
    destination = sqlite3.connect(target, timeout=10)
    try:
        source.backup(destination)
        # Do not persist the temporary operation guard inside a recovery
        # snapshot. A restored backup must inherit the administrator's prior
        # maintenance preference, not look like an interrupted operation.
        settings_exists = destination.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
        ).fetchone()
        if settings_exists:
            previous_enabled = destination.execute(
                "SELECT value FROM settings WHERE key='maintenance_operation_previous_enabled'"
            ).fetchone()
            previous_message = destination.execute(
                "SELECT value FROM settings WHERE key='maintenance_operation_previous_message'"
            ).fetchone()
            if previous_enabled:
                destination.execute(
                    "UPDATE settings SET value=? WHERE key='maintenance_enabled'",
                    (previous_enabled[0],),
                )
            if previous_message:
                destination.execute(
                    "UPDATE settings SET value=? WHERE key='maintenance_message'",
                    (previous_message[0],),
                )
            destination.execute(
                "DELETE FROM settings WHERE key IN ("
                "'maintenance_operation_count',"
                "'maintenance_operation_previous_enabled',"
                "'maintenance_operation_previous_message'"
                ")"
            )
        destination.commit()
        check = destination.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("backup integrity verification failed")
    except Exception:
        destination.close()
        source.close()
        target.unlink(missing_ok=True)
        raise
    else:
        destination.close()
        source.close()
    prune_sqlite_backups(db_path)
    return target

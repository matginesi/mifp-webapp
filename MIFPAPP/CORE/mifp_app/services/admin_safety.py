from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime_storage import require_free_space
from .operation_maintenance import operation_maintenance

DATABASE_BACKUP_LIMIT = 2
PORTABILITY_CACHE_PREFIX = ".portability-"
PORTABILITY_CACHE_TTL_SECONDS = 300
PORTABILITY_IN_PROGRESS_TTL_SECONDS = 24 * 60 * 60
PORTABILITY_INCOMPLETE_TTL_SECONDS = 60 * 60

_PORTABILITY_PAIR = re.compile(r"^\.portability-([0-9a-f]{64})\.(json|bin)$")
_PORTABILITY_CLAIM = re.compile(
    r"^\.portability-([0-9a-f]{64})\.[0-9a-f]{8}\.claim$"
)
_PORTABILITY_TEMP = re.compile(
    r"^\.portability-(?:write-[A-Za-z0-9_-]+|[0-9a-f]{64}\.[0-9a-f]{8})\.tmp$"
)


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


def _read_portability_metadata(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _portability_group_key(name: str) -> str | None:
    match = _PORTABILITY_PAIR.fullmatch(name) or _PORTABILITY_CLAIM.fullmatch(name)
    if match:
        return match.group(1)
    if _PORTABILITY_TEMP.fullmatch(name):
        return name
    return None


def portability_copy_inventory(
    export_dir: Path,
    *,
    now: float | None = None,
    ttl_seconds: int = PORTABILITY_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """Inventory only app-owned portability cache artifacts.

    Unknown files, directories and symlinks are deliberately invisible to the
    cleanup plan. A group is removable only after every known artifact in it
    is older than the relevant safety window.
    """
    root = Path(export_dir)
    current = time.time() if now is None else float(now)
    groups: dict[str, list[Path]] = {}
    if root.is_dir():
        for path in root.iterdir():
            if not path.is_file() or path.is_symlink():
                continue
            key = _portability_group_key(path.name)
            if key is not None:
                groups.setdefault(key, []).append(path)

    items: list[dict[str, Any]] = []
    for key, paths in groups.items():
        size = 0
        activity_times: list[float] = []
        display_name = "Incomplete portability export"
        has_claim = False
        has_metadata = False
        has_temporary = False
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            size += stat.st_size
            activity_times.append(stat.st_mtime)
            if path.suffix in {".json", ".claim"}:
                metadata = _read_portability_metadata(path)
                if metadata:
                    filename = Path(str(metadata.get("filename") or "")).name
                    if filename:
                        display_name = filename[:180]
                    try:
                        activity_times.append(float(metadata.get("created_at") or 0))
                    except (TypeError, ValueError):
                        pass
            if path.suffix == ".claim":
                has_claim = True
            elif path.suffix == ".json":
                has_metadata = True
            elif path.suffix == ".tmp":
                has_temporary = True

        newest_activity = max(activity_times, default=current)
        age_seconds = max(0.0, current - newest_activity)
        if has_claim:
            removal_age = PORTABILITY_IN_PROGRESS_TTL_SECONDS
        elif has_temporary or not has_metadata:
            removal_age = PORTABILITY_INCOMPLETE_TTL_SECONDS
        else:
            removal_age = max(0, int(ttl_seconds))
        removable = age_seconds > removal_age
        items.append(
            {
                "key": key,
                "name": display_name,
                "size": size,
                "files": len(paths),
                "modified_at": datetime.fromtimestamp(newest_activity, UTC).isoformat(timespec="seconds"),
                "age_minutes": round(age_seconds / 60, 1),
                "status": "expired" if removable else (
                    "downloading" if has_claim else ("building" if has_temporary else "ready")
                ),
                "removable": removable,
                "_names": tuple(path.name for path in paths),
            }
        )

    items.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    removable = [item for item in items if item["removable"]]
    return {
        "directory": str(root),
        "items": items,
        "total": len(items),
        "active": len(items) - len(removable),
        "removable": len(removable),
        "removable_bytes": sum(int(item["size"]) for item in removable),
    }


def database_copy_inventory(database_path: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for index, path in enumerate(automatic_sqlite_backups(Path(database_path))):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
                "protected": index == 0,
                "removable": True,
            }
        )
    return {
        "items": items,
        "total": len(items),
        "removable": len(items),
        "removable_bytes": sum(int(item["size"]) for item in items),
    }


def backup_cleanup_inventory(database_path: Path, export_dir: Path) -> dict[str, Any]:
    database = database_copy_inventory(database_path)
    portability = portability_copy_inventory(export_dir)
    return {
        "database": database,
        "portability": portability,
        "removable_bytes": database["removable_bytes"] + portability["removable_bytes"],
    }


def _remove_expired_portability_copies(export_dir: Path) -> dict[str, Any]:
    root = Path(export_dir).resolve()
    inventory = portability_copy_inventory(root)
    removed_files = 0
    removed_copies = 0
    removed_bytes = 0
    for item in inventory["items"]:
        if not item["removable"]:
            continue
        names = list(item["_names"])
        metadata_names = [name for name in names if name.endswith(".json")]
        if metadata_names:
            metadata = root / metadata_names[0]
            claimed = metadata.with_suffix(f".{secrets.token_hex(4)}.tmp")
            try:
                metadata.rename(claimed)
            except OSError:
                # A downloader may have atomically claimed the metadata first.
                continue
            names[names.index(metadata_names[0])] = claimed.name
        copy_removed = False
        for name in names:
            candidate = root / Path(name).name
            try:
                if candidate.parent != root or candidate.is_symlink() or not candidate.is_file():
                    continue
                size = candidate.stat().st_size
                candidate.unlink()
            except FileNotFoundError:
                continue
            removed_files += 1
            removed_bytes += size
            copy_removed = True
        if copy_removed:
            removed_copies += 1
    return {"copies": removed_copies, "files": removed_files, "bytes": removed_bytes}


def _replace_database_snapshots(database_path: Path, *, reserve_bytes: int) -> dict[str, Any]:
    db_path = Path(database_path)
    previous = {
        snapshot.name: snapshot.stat().st_size
        for snapshot in automatic_sqlite_backups(db_path)
        if snapshot.is_file() and not snapshot.is_symlink()
    }
    replacement = backup_sqlite_database(
        db_path,
        label="retention-cleanup",
        reserve_bytes=reserve_bytes,
    )
    if replacement is None:
        raise RuntimeError("A fresh verified database snapshot could not be created")
    for snapshot in automatic_sqlite_backups(db_path):
        if snapshot.name not in previous:
            continue
        try:
            snapshot.unlink()
        except FileNotFoundError:
            continue
    remaining = {path.name for path in automatic_sqlite_backups(db_path)}
    removed_names = set(previous) - remaining
    return {
        "created": replacement.name,
        "copies": len(removed_names),
        "bytes": sum(previous[name] for name in removed_names),
    }


def cleanup_backup_copies(
    database_path: Path,
    export_dir: Path,
    *,
    database: bool,
    portability: bool,
    reserve_bytes: int = 0,
) -> dict[str, Any]:
    """Apply the conservative cleanup selected by an authenticated operator."""
    report: dict[str, Any] = {
        "database": {"created": None, "copies": 0, "bytes": 0},
        "portability": {"copies": 0, "files": 0, "bytes": 0},
    }
    if database:
        report["database"] = _replace_database_snapshots(
            Path(database_path), reserve_bytes=max(0, int(reserve_bytes))
        )
    if portability:
        report["portability"] = _remove_expired_portability_copies(Path(export_dir))
    report["bytes"] = report["database"]["bytes"] + report["portability"]["bytes"]
    return report

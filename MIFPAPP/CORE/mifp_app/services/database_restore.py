from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from ..db.contract import RUNTIME_REQUIRED_TABLES
from ..db.runtime_check import validate_runtime_database
from .admin_safety import backup_sqlite_database

SQLITE_HEADER = b"SQLite format 3\x00"


class DatabaseRestoreError(ValueError):
    pass


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _verify_database(path: Path) -> dict[str, int]:
    """Accept only a complete database matching the current runtime contract."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 100:
        raise DatabaseRestoreError("The uploaded database is empty, incomplete, or unsafe.")
    with path.open("rb") as stream:
        if stream.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise DatabaseRestoreError("The uploaded file is not a SQLite database.")
    try:
        validate_runtime_database(path)
        with _readonly_connection(path) as source:
            return {
                table: int(source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                for table in sorted(RUNTIME_REQUIRED_TABLES)
            }
    except RuntimeError as exc:
        raise DatabaseRestoreError(
            "The uploaded database does not match the current MIFP schema. "
            "For historical data, create a fresh database and import a portable ZIP. "
            f"Details: {exc}"
        ) from exc
    except sqlite3.Error as exc:
        raise DatabaseRestoreError("The SQLite database cannot be read safely.") from exc


def _finalize_staging(path: Path) -> None:
    """Make the staged SQLite file self-contained before an atomic swap."""
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()
    Path(str(path) + "-wal").unlink(missing_ok=True)
    Path(str(path) + "-shm").unlink(missing_ok=True)


def _restore_backup(backup_path: Path, db_path: Path) -> None:
    src = _readonly_connection(backup_path)
    destination = sqlite3.connect(db_path, timeout=30)
    try:
        src.backup(destination)
        destination.commit()
    finally:
        destination.close()
        src.close()


def restore_sqlite_database(
    db_path: Path, payload: bytes | Path | BinaryIO
) -> dict:
    """Validate and atomically restore a *current-schema* MIFP SQLite snapshot.

    Historical database migration is deliberately not part of restore. Restore
    accepts only current-schema snapshots; content migration must use a current
    versioned mifp-content or mifp-jsonl-v2 package.
    """
    db_path = Path(db_path)
    if payload is None:
        raise DatabaseRestoreError("No database file was uploaded.")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    backup_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".mifp-restore-",
            suffix=".sqlite",
            dir=db_path.parent,
            delete=False,
        ) as temporary:
            if isinstance(payload, bytes):
                if not payload:
                    raise DatabaseRestoreError("No database file was uploaded.")
                temporary.write(payload)
                byte_count = len(payload)
            elif isinstance(payload, Path):
                if not payload.is_file() or payload.is_symlink() or payload.stat().st_size < 100:
                    raise DatabaseRestoreError("The uploaded database is empty or incomplete.")
                with payload.open("rb") as source:
                    shutil.copyfileobj(source, temporary, length=1024 * 1024)
                byte_count = payload.stat().st_size
            else:
                stream = payload
                stream.seek(0, os.SEEK_END)
                byte_count = stream.tell()
                if byte_count < 100:
                    raise DatabaseRestoreError("The uploaded database is empty or incomplete.")
                stream.seek(0)
                shutil.copyfileobj(stream, temporary, length=1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name

        incoming_path = Path(temporary_name)
        _finalize_staging(incoming_path)
        counts = _verify_database(incoming_path)
        backup_path = backup_sqlite_database(
            db_path, label="before-restore", _maintenance_guard=False
        )
        os.replace(incoming_path, db_path)
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        return {
            "backup_path": str(backup_path) if backup_path else None,
            "bytes": byte_count,
            "counts": counts,
        }
    except Exception:
        if backup_path and backup_path.is_file():
            _restore_backup(backup_path, db_path)
        raise
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
            Path(temporary_name + "-wal").unlink(missing_ok=True)
            Path(temporary_name + "-shm").unlink(missing_ok=True)

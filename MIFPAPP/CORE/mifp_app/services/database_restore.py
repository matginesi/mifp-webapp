from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import BinaryIO

from ..db.connection import connect
from ..db.migrations import migrate_content_schema
from .admin_safety import backup_sqlite_database

SQLITE_HEADER = b"SQLite format 3\x00"
REQUIRED_TABLES = {
    "settings",
    "events",
    "news",
    "members",
    "publications",
    "research_areas",
    "sponsors",
    "assets",
}


class DatabaseRestoreError(ValueError):
    pass


def _verify_database(path: Path) -> dict[str, int]:
    if not path.is_file() or path.stat().st_size < 100:
        raise DatabaseRestoreError("The uploaded database is empty or incomplete.")
    with path.open("rb") as stream:
        if stream.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise DatabaseRestoreError("The uploaded file is not a SQLite database.")

    try:
        source = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10)
        source.row_factory = sqlite3.Row
        check = source.execute("PRAGMA integrity_check").fetchall()
        if not check or any(str(row[0]).lower() != "ok" for row in check):
            raise DatabaseRestoreError("SQLite integrity verification failed.")
        tables = {
            str(row[0])
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise DatabaseRestoreError(
                "This is not a complete MIFP database; missing tables: "
                + ", ".join(missing)
            )
        counts = {
            table: int(source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in REQUIRED_TABLES
        }
        source.close()
        return counts
    except DatabaseRestoreError:
        raise
    except sqlite3.Error as exc:
        raise DatabaseRestoreError("The SQLite database cannot be read safely.") from exc


def _finalize_staging(path: Path) -> None:
    """Checkpoint and convert the staging file to a self-contained rollback DB.

    The migration and integrity checks run against a WAL-mode connection, so
    the main file may still be missing committed frames until the write-ahead
    log is checkpointed. Forcing ``wal_checkpoint(TRUNCATE)`` followed by
    ``journal_mode=DELETE`` guarantees ``path`` alone holds every byte, which is
    what makes the later ``os.replace`` swap atomic.
    """
    conn = sqlite3.connect(path, timeout=30)
    try:
        for _ in range(3):
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if not row or row[0] == 0:
                break
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()
    Path(str(path) + "-wal").unlink(missing_ok=True)
    Path(str(path) + "-shm").unlink(missing_ok=True)


def restore_sqlite_database(
    db_path: Path, payload: bytes | Path | BinaryIO
) -> dict:
    """Validate and atomically restore a complete MIFP SQLite snapshot.

    The uploaded database is never written directly over the live file. It is
    verified, migrated, and integrity-checked in a staging file in the same
    directory, then swapped into place with ``os.replace`` (an atomic rename on
    POSIX). Stale ``-wal``/``-shm`` sidecars left over from the pre-restore
    database are removed so they cannot replay old frames into the new file.

    ``payload`` may be raw bytes, a filesystem path, or a binary file object;
    bytes and file objects are streamed into the staging file so large uploads
    are not held in memory.
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
                if not payload.is_file() or payload.stat().st_size < 100:
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
            temporary_name = temporary.name
        incoming_path = Path(temporary_name)
        counts = _verify_database(incoming_path)
        backup_path = backup_sqlite_database(
            db_path, label="before-restore", _maintenance_guard=False
        )

        restored = connect(incoming_path)
        try:
            migration = migrate_content_schema(restored)
            check = restored.execute("PRAGMA integrity_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise DatabaseRestoreError(
                    "The restored database failed its final integrity check."
                )
        finally:
            # The WAL-mode connection must be closed before the staging file is
            # checkpointed below: an open connection keeps WAL semantics active
            # and would make ``journal_mode=DELETE`` fail with "database is
            # locked".
            restored.close()
        _finalize_staging(incoming_path)
        os.replace(incoming_path, db_path)
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        return {
            "backup_path": str(backup_path) if backup_path else None,
            "bytes": byte_count,
            "counts": counts,
            "migration": migration,
        }
    except Exception:
        if backup_path and backup_path.is_file():
            src = sqlite3.connect(
                f"file:{backup_path.resolve()}?mode=ro", uri=True, timeout=30
            )
            destination = sqlite3.connect(db_path, timeout=30)
            try:
                src.backup(destination)
                destination.commit()
            finally:
                destination.close()
                src.close()
        raise
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
            Path(temporary_name + "-wal").unlink(missing_ok=True)
            Path(temporary_name + "-shm").unlink(missing_ok=True)

"""Explicit SQLite schema initialization and forward migration registry.

MIFP supports one historical interchange path: old portable ZIP imports. Old
runtime database layouts are deliberately not auto-repaired. Create a fresh
current database and import a ZIP instead. Future database versions are upgraded
only through explicit, versioned migration functions registered here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from .contract import SCHEMA_VERSION
from .schema_fingerprint import canonical_schema_fingerprint

Migration = Callable[[sqlite3.Connection], None]

# Target-version -> migration from the immediately preceding supported version.
# Example for a future v10: MIGRATIONS[10] = _migrate_v9_to_v10.
MIGRATIONS: dict[int, Migration] = {}
MIN_UPGRADABLE_VERSION = SCHEMA_VERSION


def _schema_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _current_version(conn: sqlite3.Connection) -> int | None:
    if "schema_migrations" not in _user_tables(conn):
        return None
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _execute_schema(conn: sqlite3.Connection) -> None:
    """Execute schema.sql statement by statement inside the caller transaction."""
    buffer = ""
    for line in _schema_path().read_text(encoding="utf-8").splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                conn.execute(statement)
    if buffer.strip():
        raise RuntimeError("schema.sql ends with an incomplete SQL statement")


def _initialize_fresh(conn: sqlite3.Connection) -> dict[str, Any]:
    before = _user_tables(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _execute_schema(conn)
        conn.execute("INSERT OR IGNORE INTO roles(name,label) VALUES('staff','Staff')")
        fingerprint = canonical_schema_fingerprint()
        conn.execute(
            "UPDATE schema_migrations SET checksum=? WHERE version=?",
            (fingerprint, SCHEMA_VERSION),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    after = _user_tables(conn)
    return {
        "schema_version": SCHEMA_VERSION,
        "initialized": True,
        "created_tables": sorted(after - before),
        "migrations_applied": [],
    }


def migrate_content_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Initialize a new DB or apply explicit *supported* forward migrations.

    This function intentionally refuses unversioned/legacy database layouts.
    Historical data should be brought into a fresh current DB through the
    portable ZIP importer. Runtime startup never calls this function.
    """
    if conn.in_transaction:
        conn.commit()
    tables = _user_tables(conn)
    if not tables:
        return _initialize_fresh(conn)

    version = _current_version(conn)
    if version is None:
        raise RuntimeError(
            "unsupported unversioned/legacy MIFP database; create a fresh database "
            "and import a portable ZIP"
        )
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema {version} is newer than application schema {SCHEMA_VERSION}"
        )
    if version < MIN_UPGRADABLE_VERSION:
        raise RuntimeError(
            f"database schema {version} is no longer directly upgradeable; "
            "create a fresh database and import a portable ZIP"
        )

    applied: list[int] = []
    while version < SCHEMA_VERSION:
        target = version + 1
        migration = MIGRATIONS.get(target)
        if migration is None:
            raise RuntimeError(f"missing migration {version} -> {target}")
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration(conn)
            checksum = canonical_schema_fingerprint() if target == SCHEMA_VERSION else f"mifp-schema-v{target}"
            conn.execute(
                "INSERT INTO schema_migrations(version,name,checksum) VALUES(?,?,?)",
                (target, f"schema v{target}", checksum),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(target)
        version = target

    return {
        "schema_version": version,
        "initialized": False,
        "created_tables": [],
        "migrations_applied": applied,
    }

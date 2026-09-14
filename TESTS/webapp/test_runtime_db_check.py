from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mifp_app.db.migrations import SCHEMA_VERSION
from mifp_app.db.runtime_check import validate_runtime_database


def _database(path: Path, version: int = SCHEMA_VERSION) -> Path:
    # Build the real runtime schema: the production preflight intentionally
    # rejects snapshots that only advertise the expected schema version while
    # missing required tables.
    from mifp_app.db.migrations import migrate_content_schema

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        migrate_content_schema(conn)
        if version != SCHEMA_VERSION:
            conn.execute("DELETE FROM schema_migrations")
            conn.execute(
                "INSERT INTO schema_migrations(version,name,checksum) VALUES(?,?,?)",
                (version, "test", "test"),
            )
        conn.commit()
    return path


def test_runtime_database_preflight_accepts_expected_schema(tmp_path: Path) -> None:
    validate_runtime_database(_database(tmp_path / "mifp.db"))



def test_runtime_database_preflight_rejects_incomplete_expected_schema(tmp_path: Path) -> None:
    path = _database(tmp_path / "mifp.db")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE conference_assets")
        conn.commit()
    with pytest.raises(RuntimeError, match="missing tables: conference_assets"):
        validate_runtime_database(path)

def test_runtime_database_preflight_rejects_wrong_schema(tmp_path: Path) -> None:
    path = _database(tmp_path / "mifp.db", SCHEMA_VERSION - 1)
    with pytest.raises(RuntimeError, match="schema version"):
        validate_runtime_database(path)


def test_runtime_database_preflight_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="existing regular file"):
        validate_runtime_database(tmp_path / "missing.db")

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

from .contract import (
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
    REQUIRED_TRIGGERS,
    RUNTIME_REQUIRED_TABLES,
    SCHEMA_VERSION,
)
from .schema_fingerprint import canonical_schema_fingerprint, schema_fingerprint



def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _names(conn: sqlite3.Connection, object_type: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'",
            (object_type,),
        )
    }


def validate_runtime_database(path: Path) -> None:
    """Fail closed when a runtime database is missing or structurally incompatible."""
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"database must be an existing regular file: {path}")

    try:
        with _connect_readonly(path) as conn:
            tables = _names(conn, "table")
            missing_tables = sorted(RUNTIME_REQUIRED_TABLES - tables)
            if missing_tables:
                raise RuntimeError("database schema is incomplete; missing tables: " + ", ".join(missing_tables))

            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            version = int(row[0]) if row and row[0] is not None else 0
            if version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {version} is incompatible; application requires {SCHEMA_VERSION}"
                )

            for table, required in REQUIRED_COLUMNS.items():
                columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
                missing = sorted(required - columns)
                if missing:
                    raise RuntimeError(f"database table {table} is incomplete; missing columns: {', '.join(missing)}")

            missing_indexes = sorted(REQUIRED_INDEXES - _names(conn, "index"))
            if missing_indexes:
                raise RuntimeError("database schema is incomplete; missing indexes: " + ", ".join(missing_indexes))

            missing_triggers = sorted(REQUIRED_TRIGGERS - _names(conn, "trigger"))
            if missing_triggers:
                raise RuntimeError("database schema is incomplete; missing triggers: " + ", ".join(missing_triggers))

            expected_fingerprint = canonical_schema_fingerprint()
            actual_fingerprint = schema_fingerprint(conn)
            if actual_fingerprint != expected_fingerprint:
                raise RuntimeError(
                    "database schema fingerprint is incompatible; "
                    f"expected {expected_fingerprint[:12]}, got {actual_fingerprint[:12]}"
                )
            migration = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version=?",
                (SCHEMA_VERSION,),
            ).fetchone()
            recorded = str(migration[0] or "") if migration else ""
            if recorded != expected_fingerprint:
                raise RuntimeError(
                    "database schema fingerprint metadata is invalid; "
                    "rebuild/upgrade the database through the supported lifecycle"
                )

            quick = conn.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]).lower() != "ok":
                raise RuntimeError(f"SQLite quick_check failed: {quick[0] if quick else 'no result'}")

            fk = conn.execute("PRAGMA foreign_key_check").fetchone()
            if fk is not None:
                raise RuntimeError("SQLite foreign_key_check failed: " + ", ".join(str(value) for value in fk))
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot validate database {path}: {exc}") from exc


def main() -> int:
    path = Path(os.getenv("DATABASE_PATH", "/app/data/mifp.db"))
    try:
        validate_runtime_database(path)
    except RuntimeError as exc:
        print(f"MIFP database preflight failed: {exc}", file=sys.stderr)
        return 1
    print(f"MIFP database preflight OK: schema v{SCHEMA_VERSION} ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic structural fingerprint for the canonical SQLite schema."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SPACE_RE = re.compile(r"\s+")


def _normalize_sql(sql: str | None) -> str:
    return _SPACE_RE.sub(" ", str(sql or "").strip())


def schema_descriptor(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Return a stable description of every application-owned schema object."""
    rows = conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('table','index','trigger') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return [
        {
            "type": str(row[0]),
            "name": str(row[1]),
            "table": str(row[2]),
            "sql": _normalize_sql(row[3]),
        }
        for row in rows
    ]


def schema_fingerprint(conn: sqlite3.Connection) -> str:
    payload = json.dumps(
        schema_descriptor(conn),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_schema_fingerprint() -> str:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        return schema_fingerprint(conn)
    finally:
        conn.close()

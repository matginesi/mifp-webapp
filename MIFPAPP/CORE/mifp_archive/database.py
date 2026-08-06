from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable


def connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def rows(conn: sqlite3.Connection, table: str, *, order_by: str = "id") -> list[dict]:
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order_by}')]


def placeholders(items: Iterable[object]) -> str:
    values = list(items)
    return ",".join("?" for _ in values)

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any


def _migration_module():
    path = Path(__file__).resolve().parents[1] / "mifp_app" / "db" / "migrations.py"
    spec = importlib.util.spec_from_file_location("mifp_archive._webapp_migrations", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(conn: sqlite3.Connection) -> dict[str, Any]:
    """Run the shared SQLite schema migration without importing Flask."""
    return _migration_module().migrate_content_schema(conn)

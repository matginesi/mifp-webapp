"""Explicit SQLite schema initialization and forward migration registry.

Legacy or unversioned runtime database layouts are deliberately not auto-repaired.
Supported adjacent schema versions are upgraded only through explicit migration
functions registered here; older data must be imported into a fresh current DB
through the supported portable interchange formats.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .contract import SCHEMA_VERSION
from .schema_fingerprint import canonical_schema_fingerprint

Migration = Callable[[sqlite3.Connection], None]


def _historic_public_path(slug: str, canonical_url: str | None) -> str:
    """Recover an existing events.mifp.eu path without changing its casing."""
    raw_url = str(canonical_url or "").strip()
    if raw_url:
        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            parsed = None
        if parsed is not None and (parsed.hostname or "").casefold() == "events.mifp.eu":
            candidate = parsed.path.strip("/")
            parts = candidate.split("/") if candidate else []
            if parts and all(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~-]*", part)
                for part in parts
            ):
                return candidate
    return slug


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Add conference-package deployment metadata without rewriting content.

    The new columns are appended in the same order used by ``schema.sql`` so a
    migrated database has the same structural fingerprint as a fresh database.
    Existing conference workspaces recover the case-sensitive path from an
    events.mifp.eu canonical URL when available, otherwise they fall back to
    the lower-case internal slug.
    """
    additions = (
        "event_id INTEGER REFERENCES events(id) ON DELETE SET NULL",
        "public_path TEXT NOT NULL DEFAULT ''",
        "source_format TEXT NOT NULL DEFAULT 'internal' "
        "CHECK(source_format IN ('internal','legacy-static','conference-editor'))",
        "source_version TEXT",
        "package_schema_version INTEGER",
        "package_sha256 TEXT",
        "package_manifest_json TEXT NOT NULL DEFAULT '{}'",
        "deploy_status TEXT NOT NULL DEFAULT 'unpublished' "
        "CHECK(deploy_status IN ('unpublished','staged','published','failed'))",
        "imported_at TEXT",
        "published_at TEXT",
    )
    for definition in additions:
        conn.execute(f"ALTER TABLE conference_sites ADD COLUMN {definition}")
    rows = conn.execute(
        "SELECT id,slug,canonical_url FROM conference_sites WHERE TRIM(public_path)=''"
    ).fetchall()
    for site_id, slug, canonical_url in rows:
        conn.execute(
            "UPDATE conference_sites SET public_path=? WHERE id=?",
            (_historic_public_path(str(slug), canonical_url), site_id),
        )
    conn.execute(
        "CREATE UNIQUE INDEX idx_conference_sites_event "
        "ON conference_sites(event_id) WHERE event_id IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_conference_sites_public_path "
        "ON conference_sites(public_path) WHERE TRIM(public_path) <> ''"
    )
    conn.execute(
        "CREATE INDEX idx_conference_sites_package_sha256 "
        "ON conference_sites(package_sha256) WHERE package_sha256 IS NOT NULL"
    )


# Target-version -> migration from the immediately preceding supported version.
MIGRATIONS: dict[int, Migration] = {10: _migrate_v9_to_v10}
MIN_UPGRADABLE_VERSION = 9


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
            checksum = (
                canonical_schema_fingerprint()
                if target == SCHEMA_VERSION
                else f"mifp-schema-v{target}"
            )
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

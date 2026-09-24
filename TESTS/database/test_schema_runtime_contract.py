from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mifp_app.db.contract import REQUIRED_INDEXES, REQUIRED_TRIGGERS, RUNTIME_REQUIRED_TABLES, SCHEMA_VERSION
from mifp_app.db.manage import init_database, upgrade_copy
from mifp_app.db.migrations import migrate_content_schema
from mifp_app.db.runtime_check import validate_runtime_database
from mifp_app.db.schema_fingerprint import canonical_schema_fingerprint, schema_descriptor, schema_fingerprint

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"


def _objects(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT type,name FROM sqlite_master "
            "WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def test_fresh_schema_and_initializer_are_structurally_identical(tmp_path: Path) -> None:
    direct = sqlite3.connect(":memory:")
    direct.executescript(SCHEMA.read_text(encoding="utf-8"))

    initialized_path = tmp_path / "initialized.db"
    init_database(initialized_path)
    initialized = sqlite3.connect(initialized_path)
    try:
        assert _objects(initialized) == _objects(direct)
        assert schema_descriptor(initialized) == schema_descriptor(direct)
        assert schema_fingerprint(initialized) == canonical_schema_fingerprint()
        row = initialized.execute(
            "SELECT version,checksum FROM schema_migrations WHERE version=?",
            (SCHEMA_VERSION,),
        ).fetchone()
        assert row[0] == SCHEMA_VERSION
        assert row[1] == canonical_schema_fingerprint()
    finally:
        direct.close()
        initialized.close()


def test_runtime_contract_names_are_present_in_fresh_schema(tmp_path: Path) -> None:
    path = tmp_path / "mifp.db"
    init_database(path)
    validate_runtime_database(path)
    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert RUNTIME_REQUIRED_TABLES <= tables
    assert REQUIRED_INDEXES <= indexes
    assert REQUIRED_TRIGGERS <= triggers
    assert "page_views" not in tables


def test_runtime_check_rejects_missing_canonical_index(tmp_path: Path) -> None:
    path = tmp_path / "mifp.db"
    init_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX idx_news_uid")
    with pytest.raises(RuntimeError, match="missing indexes"):
        validate_runtime_database(path)


def test_unversioned_database_is_not_auto_repaired(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE news(id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO news(title) VALUES('historical')")
        with pytest.raises(RuntimeError, match="portable ZIP"):
            migrate_content_schema(conn)
        assert conn.execute("SELECT title FROM news").fetchone()[0] == "historical"
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='members'").fetchone()[0] == 0


def test_old_versioned_database_is_not_directly_upgradeable(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT,applied_at TEXT)")
        conn.execute("INSERT INTO schema_migrations(version,name) VALUES(8,'old')")
        with pytest.raises(RuntimeError, match="portable ZIP"):
            migrate_content_schema(conn)


def test_upgrade_copy_never_modifies_source_and_validates_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "copy.db"
    init_database(source)
    before = source.read_bytes()
    upgrade_copy(source, destination)
    assert source.read_bytes() == before
    validate_runtime_database(destination)


def test_runtime_check_rejects_schema_definition_drift(tmp_path: Path) -> None:
    path = tmp_path / "mifp.db"
    init_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER assign_news_uid")
        conn.execute(
            "CREATE TRIGGER assign_news_uid AFTER INSERT ON news "
            "WHEN NEW.uid IS NULL OR TRIM(NEW.uid)='' BEGIN "
            "UPDATE news SET uid='tampered_' || lower(hex(randomblob(16))) WHERE id=NEW.id; END"
        )
    with pytest.raises(RuntimeError, match="fingerprint"):
        validate_runtime_database(path)


def test_v9_conference_metadata_migrates_to_v10_contract(tmp_path: Path) -> None:
    path = tmp_path / "v9.db"
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT,"
            "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("INSERT INTO schema_migrations(version,name) VALUES(9,'schema v9')")
        conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
        conn.execute(
            """CREATE TABLE conference_sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                acronym TEXT,
                year INTEGER,
                status TEXT NOT NULL DEFAULT 'draft',
                start_date TEXT,
                end_date TEXT,
                venue TEXT,
                city TEXT,
                country TEXT,
                canonical_url TEXT,
                deploy_base_path TEXT NOT NULL DEFAULT '/',
                registration_url TEXT,
                contact_email TEXT,
                description TEXT,
                config_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "INSERT INTO conference_sites(slug,title,canonical_url) VALUES(?,?,?)",
            ("plmcn-2025", "PLMCN 2025", "https://events.mifp.eu/PLMCN-2025/"),
        )
        result = migrate_content_schema(conn)
        row = conn.execute(
            """SELECT public_path,source_format,deploy_status,event_id,package_sha256
               FROM conference_sites WHERE slug='plmcn-2025'"""
        ).fetchone()
        indexes = {item[0] for item in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}

    assert result["migrations_applied"] == [10, 11, 12, 13]
    assert row["public_path"] == "PLMCN-2025"
    assert row["source_format"] == "internal"
    assert row["deploy_status"] == "unpublished"
    assert row["event_id"] is None
    assert row["package_sha256"] is None
    assert {
        "idx_conference_sites_event",
        "idx_conference_sites_public_path",
        "idx_conference_sites_package_sha256",
    } <= indexes

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"


def _database(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path]:
    db_path = tmp_path / "mifp.db"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    return conn, db_path, assets_dir


def test_database_maintenance_preview_is_read_only(tmp_path: Path) -> None:
    from mifp_app.services.database_maintenance import analyze_database

    conn, _db_path, assets_dir = _database(tmp_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO entity_links(id,entity_type,entity_id,url,role) VALUES(1,'news',999,'https://example.test','source')"
    )
    conn.commit()

    plan = analyze_database(conn, assets_dir)

    assert any(item.code == "orphan_entity_link" for item in plan.safe_fixes)
    assert conn.execute("SELECT COUNT(*) FROM entity_links").fetchone()[0] == 1
    assert plan.checks["quick_check"] == ["ok"]


def test_database_maintenance_applies_safe_fixes_with_backup_and_is_idempotent(tmp_path: Path) -> None:
    from mifp_app.services.database_maintenance import analyze_database, apply_safe_fixes

    conn, db_path, assets_dir = _database(tmp_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT INTO entity_links(id,entity_type,entity_id,url,role) VALUES(1,'news',999,'https://example.test','source')"
    )
    conn.commit()

    result = apply_safe_fixes(conn, db_path, assets_dir, confirmed=True)

    assert result["applied"]["orphan_entity_link"] == 1
    assert (db_path.parent / "backups" / result["backup"]).is_file()
    assert analyze_database(conn, assets_dir).safe_fixes == []
    second = apply_safe_fixes(conn, db_path, assets_dir, confirmed=True)
    assert second["applied"] == {}


def test_database_maintenance_flags_unsafe_asset_path_for_review(tmp_path: Path) -> None:
    from mifp_app.services.database_maintenance import analyze_database

    conn, _db_path, assets_dir = _database(tmp_path)
    conn.execute(
        "INSERT INTO assets(filename,path,kind,storage_status,is_external) VALUES('bad','../outside.txt','other','local',0)"
    )
    conn.commit()

    plan = analyze_database(conn, assets_dir)

    assert any(item.code == "unsafe_asset_path" for item in plan.review_required)

from __future__ import annotations

import sqlite3
from pathlib import Path

from mifp_app.services.entity_references import delete_entity_references, move_entity_references

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO news(id,title,slug) VALUES(1,'Old','old')")
    conn.execute("INSERT INTO news(id,title,slug) VALUES(2,'Canonical','canonical')")
    conn.execute("INSERT INTO events(id,title,slug) VALUES(10,'Event','event')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES(5,'x.pdf','x.pdf','pdf')")
    conn.execute("INSERT INTO entity_links(entity_type,entity_id,url,role) VALUES('news',1,'https://example.test','source')")
    conn.execute("INSERT INTO asset_links(asset_id,entity_type,entity_id,role) VALUES(5,'news',1,'document')")
    conn.execute("INSERT INTO entity_relations(source_type,source_id,target_type,target_id,role) VALUES('news',1,'event',10,'related')")
    run = conn.execute("INSERT INTO import_runs(name,status) VALUES('test','completed')").lastrowid
    conn.execute("INSERT INTO import_records(import_run_id,entity_type,entity_id) VALUES(?,?,?)", (run,'news',1))


def test_move_entity_references_preserves_links_and_import_audit() -> None:
    conn = _conn(); _seed(conn)
    move_entity_references(conn, "news", 1, 2)
    assert conn.execute("SELECT COUNT(*) FROM entity_links WHERE entity_id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM entity_links WHERE entity_id=2").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM asset_links WHERE entity_id=2").fetchone()[0] == 1
    assert conn.execute("SELECT source_id FROM entity_relations WHERE source_type='news'").fetchone()[0] == 2
    assert conn.execute("SELECT entity_id FROM import_records WHERE entity_type='news'").fetchone()[0] == 2


def test_delete_entity_references_removes_dangling_links_but_keeps_import_audit() -> None:
    conn = _conn(); _seed(conn)
    delete_entity_references(conn, "news", 1)
    assert conn.execute("SELECT COUNT(*) FROM entity_links WHERE entity_id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM asset_links WHERE entity_id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM entity_relations WHERE source_type='news' AND source_id=1").fetchone()[0] == 0
    assert conn.execute("SELECT entity_id FROM import_records WHERE entity_type='news'").fetchone()[0] is None

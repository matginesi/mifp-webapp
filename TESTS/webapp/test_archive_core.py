from __future__ import annotations

import hashlib
from pathlib import Path

from mifp_archive.database import connect
from mifp_archive.health import archive_health
from mifp_archive.migrate import migrate
from mifp_archive.package import export_archive, import_archive, validate_archive


def test_archive_roundtrip_preserves_uids_and_assets(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    source_assets = tmp_path / "source-assets"
    file_path = source_assets / "document" / "paper.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("portable", encoding="utf-8")
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()

    with connect(source_db) as conn:
        migrate(conn)
        conn.execute("INSERT INTO news(slug,title,body) VALUES('portable-news','Portable news','Body')")
        conn.execute(
            "INSERT INTO assets(filename,path,kind,storage_status,checksum,content_sha256) "
            "VALUES('paper.txt','document/paper.txt','document','local',?,?)",
            (digest, digest),
        )
        news = conn.execute("SELECT id,uid FROM news").fetchone()
        asset = conn.execute("SELECT id,uid FROM assets").fetchone()
        conn.execute(
            "INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary) VALUES(?,?,?,?,1)",
            (asset["id"], "news", news["id"], "attachment"),
        )
        source_uid = str(news["uid"])
        conn.commit()
        export_archive(conn, source_assets, tmp_path / "archive.zip")

    assert validate_archive(tmp_path / "archive.zip")["valid"] is True

    target_db = tmp_path / "target.db"
    target_assets = tmp_path / "target-assets"
    with connect(target_db) as conn:
        dry = import_archive(conn, target_assets, tmp_path / "archive.zip", dry_run=True)
        assert dry["dry_run"] is True
        assert conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 0
        imported = import_archive(conn, target_assets, tmp_path / "archive.zip")
        assert imported["assets_copied"] == 1
        assert conn.execute("SELECT uid FROM news").fetchone()[0] == source_uid
        assert archive_health(conn, target_assets)["summary"]["critical"] == 0

    assert (target_assets / "document" / "paper.txt").read_text(encoding="utf-8") == "portable"

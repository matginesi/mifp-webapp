from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


def _conn_with_migrations() -> sqlite3.Connection:
    conn = _conn()
    from mifp_app.db.migrations import migrate_content_schema
    migrate_content_schema(conn)
    return conn


def _link_asset(conn: sqlite3.Connection, asset_id: int, entity_type: str, entity_id: int, role: str = "cover") -> None:
    conn.execute(
        "INSERT INTO asset_links(asset_id, entity_type, entity_id, role) VALUES (?,?,?,?)",
        (asset_id, entity_type, entity_id, role),
    )


def test_asset_usage_counts_only_real_content_links():
    from mifp_app.services.dashboard_repository import asset_usage, unused_assets

    conn = _conn()
    conn.execute("INSERT INTO assets(id, filename, path, kind) VALUES (1,'n.png','image/n.png','image')")
    conn.execute("INSERT INTO assets(id, filename, path, kind) VALUES (2,'e.png','image/e.png','image')")
    conn.execute("INSERT INTO assets(id, filename, path, kind) VALUES (3,'s.png','image/s.png','image')")
    conn.execute("INSERT INTO assets(id, filename, path, kind) VALUES (4,'unused.png','image/unused.png','image')")
    conn.execute("INSERT INTO news(id, title, slug, review_status) VALUES (1,'News','news','published')")
    conn.execute("INSERT INTO events(id, title, slug, review_status) VALUES (1,'Event','event','published')")
    conn.execute("INSERT INTO sponsors(id, name, slug, is_active) VALUES (1,'Sponsor','sponsor',1)")
    _link_asset(conn, 1, "news", 1)
    _link_asset(conn, 2, "event", 1)
    _link_asset(conn, 3, "sponsor", 1)
    _link_asset(conn, 4, "news", 999)
    usage = {row["id"]: row["usage_count"] for row in asset_usage(conn)}
    assert usage[1] == 1
    assert usage[2] == 1
    assert usage[3] == 1
    assert usage[4] == 0
    assert [row["id"] for row in unused_assets(conn)] == [4]


def test_event_uses_exact_slug_source_image_when_asset_link_is_missing(tmp_path, monkeypatch):
    from mifp_app.config import Config
    from mifp_app.services.public_repository import enrich_event

    assets_dir = tmp_path / "assets"
    image = assets_dir / "image" / "event.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    monkeypatch.setattr(Config, "ASSETS_DIR", assets_dir)

    conn = _conn()
    conn.execute(
        """
        INSERT INTO assets(id,filename,path,kind,source_url)
        VALUES(1,'event.png','image/event.png','image',
               'https://events.example.org/example-2027/images/logo.png')
        """
    )
    conn.execute(
        """
        INSERT INTO events(id,title,slug,review_status)
        VALUES(1,'Example 2027','example-2027','published')
        """
    )

    event = dict(conn.execute("SELECT * FROM events WHERE id=1").fetchone())
    enriched = enrich_event(conn, event, lambda path: f"/media/{path}")

    assert enriched["cover_url"] == "/media/image/event.png"


def test_cleanup_plan_reports_unused_missing_and_orphan_files(tmp_path):
    from mifp_app.services.asset_cleanup import build_asset_cleanup_plan

    conn = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "image").mkdir(parents=True)
    (assets_dir / "image" / "used.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    (assets_dir / "image" / "orphan.png").write_bytes(b"\x89PNG\r\n\x1a\ny")
    conn.execute("INSERT INTO assets(id, filename, path, kind) VALUES (1,'used.png','image/used.png','image')")
    conn.execute("INSERT INTO assets(id, filename, path, kind) VALUES (2,'missing.png','image/missing.png','image')")
    conn.execute("INSERT INTO news(id, title, slug, review_status) VALUES (1,'News','news','published')")
    _link_asset(conn, 1, "news", 1)
    plan = build_asset_cleanup_plan(conn, assets_dir)
    assert [row["id"] for row in plan.unused_db_assets] == [2]
    assert [row["id"] for row in plan.missing_file_assets] == [2]
    assert [row["path"] for row in plan.orphan_files] == ["image/orphan.png"]


def test_news_pdf_names_are_clean_and_deduplicated():
    from mifp_app.services.public_repository import enrich_news

    conn = _conn()
    pdf_url = "https://example.org/files/19824_abcdabcdabcdabcdabcd.pdf/o/science_kavokina.pdf?download=1"
    conn.execute(
        """
        INSERT INTO assets(id, filename, original_filename, path, kind, source_url, is_external, caption, checksum)
        VALUES (1, 'abcdabcdabcdabcdabcd.pdf', NULL, 'pdf/abcdabcdabcdabcdabcd.pdf', 'pdf', ?, 1, 'Download the paper here.', 'sha')
        """,
        (pdf_url,),
    )
    conn.execute("INSERT INTO news(id, title, slug, body, review_status) VALUES (1,'News','news','Body','published')")
    _link_asset(conn, 1, "news", 1, role="document")
    row = dict(conn.execute("SELECT * FROM news WHERE id=1").fetchone())
    enriched = enrich_news(conn, row, lambda path: f"/media/{path}")
    assert len(enriched["documents"]) == 1
    assert enriched["documents"][0]["filename"] == "Download the paper here"


def test_news_document_exposes_publication_link_when_asset_is_shared():
    from mifp_app.services.public_repository import enrich_news

    conn = _conn()
    conn.execute(
        "INSERT INTO assets(id,filename,path,kind,checksum,is_external,source_url) "
        "VALUES(1,'paper.pdf','pdf/paper.pdf','pdf','paper-sha',1,'https://docs.example/paper.pdf')"
    )
    conn.execute("INSERT INTO news(id,title,slug,review_status) VALUES(1,'Paper announced','paper-announced','published')")
    conn.execute("INSERT INTO publications(id,title,slug,year,review_status) VALUES(1,'Complete paper title','complete-paper',2025,'published')")
    _link_asset(conn, 1, "news", 1, role="document")
    _link_asset(conn, 1, "publication", 1, role="document")

    article = enrich_news(conn, dict(conn.execute("SELECT * FROM news WHERE id=1").fetchone()), lambda path: f"/media/{path}")
    assert article["documents"][0]["related_publications"] == [{
        "title": "Complete paper title", "slug": "complete-paper", "year": 2025, "doi": None,
    }]


def test_clean_asset_display_name_falls_back_to_decoded_filename():
    from mifp_app.services.public_repository import _clean_asset_display_name

    assert _clean_asset_display_name(
        None,
        "https://example.org/files/19824_deadbeefdeadbeef.pdf/o/science%20paper.pdf?x=1",
        kind="pdf",
    ) == "science paper.pdf"


def test_news_page_view_model_handles_images_pdfs_and_filters():
    from mifp_app.services.public_repository import list_news_page

    conn = _conn_with_migrations()
    conn.execute(
        "INSERT INTO assets(id, filename, path, kind, is_external, source_url) VALUES (1,'hero.png','image/hero.png','image',1,'https://img.example/hero.png')"
    )
    conn.execute(
        "INSERT INTO assets(id, filename, path, kind, is_external, source_url, checksum) VALUES (2,'paper.pdf','pdf/paper.pdf','pdf',1,'https://docs.example/paper.pdf?x=1','sha')"
    )
    conn.execute(
        "INSERT INTO news(id, title, slug, date, summary, body, review_status) VALUES (1,'With image','with-image','2026-05-01','Summary','Body','published')"
    )
    conn.execute(
        "INSERT INTO news(id, title, slug, date, body, review_status) VALUES (2,'With pdf','with-pdf','2025-02-01','PDF body','published')"
    )
    conn.execute(
        "INSERT INTO news(id, title, slug, date, body, review_status) VALUES (3,'Plain','plain','2024-01-01','Plain body','published')"
    )
    _link_asset(conn, 1, "news", 1, role="cover")
    _link_asset(conn, 2, "news", 2, role="document")
    result = list_news_page(conn, lambda path: f"/media/{path}", None, None, 1, 12)
    by_slug = {row["slug"]: row for row in result["news"]}
    assert by_slug["with-image"]["primary_image"] == "https://img.example/hero.png"
    assert by_slug["plain"]["primary_image"] is None
    assert len(by_slug["with-pdf"]["documents"]) == 1
    assert "2026" in result["years"]

    pdf_result = list_news_page(conn, lambda path: f"/media/{path}", None, None, 1, 12, content_filter="pdf")
    assert [row["slug"] for row in pdf_result["news"]] == ["with-pdf"]


def test_news_ordering_by_source_then_date_desc():
    from mifp_app.services.public_repository import list_news_page

    conn = _conn_with_migrations()
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,date,review_status) VALUES (1,'Remote old','r-old','remote',20,'2020-01-01','published')"
    )
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,date,review_status) VALUES (2,'Local new','l-new','local',10,'2025-12-31','published')"
    )
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,date,review_status) VALUES (3,'Local old','l-old','local',10,'2020-01-01','published')"
    )
    result = list_news_page(conn, lambda p: f"/media/{p}", None, None, 1, 12)
    titles = [row["title"] for row in result["news"]]
    assert titles == ["Local new", "Local old", "Remote old"]


def test_news_ordering_display_order_overrides_source_priority():
    from mifp_app.services.public_repository import list_news_page

    conn = _conn_with_migrations()
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,source_order,display_order,review_status) VALUES (1,'Promoted remote','promoted','remote',20,1,1,'published')"
    )
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,source_order,review_status) VALUES (2,'Local','local','local',10,1,'published')"
    )
    result = list_news_page(conn, lambda p: f"/media/{p}", None, None, 1, 12)
    titles = [row["title"] for row in result["news"]]
    assert titles == ["Promoted remote", "Local"]


def test_news_ordering_same_source_tiebreak_by_id():
    from mifp_app.services.public_repository import list_news_page

    conn = _conn_with_migrations()
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,date,review_status) VALUES (2,'Older id','older','local',10,'2020-01-01','published')"
    )
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,date,review_status) VALUES (1,'Newer id','newer','local',10,'2020-01-01','published')"
    )
    result = list_news_page(conn, lambda p: f"/media/{p}", None, None, 1, 12)
    titles = [row["title"] for row in result["news"]]
    assert titles == ["Older id", "Newer id"]


def test_list_recent_news_includes_source_fields():
    from mifp_app.services.public_repository import list_recent_news

    conn = _conn_with_migrations()
    conn.execute(
        "INSERT INTO news(id,title,slug,source_kind,source_priority,source_order,review_status) VALUES (1,'Local','local','local',10,1,'published')"
    )
    rows = list_recent_news(conn, lambda p: f"/media/{p}", limit=5)
    assert len(rows) == 1
    assert rows[0]["source_kind"] == "local"
    assert rows[0]["source_priority"] == 10


def test_build_asset_export_plan_kind_filter():
    from mifp_app.services.asset_cleanup import build_asset_export_plan

    conn = _conn()
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (1,'a.png','image/a.png','image')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (2,'b.pdf','pdf/b.pdf','pdf')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (3,'c.jpg','image/c.jpg','image')")
    local, missing = build_asset_export_plan(conn, Path("/nonexistent"), kind_filter=["image"])
    ids = {d["id"] for d in local + missing}
    assert ids == {1, 3}


def test_build_asset_export_plan_status_filter_used(tmp_path):
    from mifp_app.services.asset_cleanup import build_asset_export_plan

    conn = _conn()
    (tmp_path / "image").mkdir()
    (tmp_path / "image" / "a.png").write_bytes(b"1")
    (tmp_path / "image" / "b.png").write_bytes(b"2")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (1,'a.png','image/a.png','image')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (2,'b.png','image/b.png','image')")
    conn.execute("INSERT INTO news(id,title,slug,review_status) VALUES (1,'N','n','published')")
    _link_asset(conn, 1, "news", 1)
    local, missing = build_asset_export_plan(conn, tmp_path, kind_filter=None, status_filter=["used"])
    assert [d["id"] for d in local + missing] == [1]


def test_build_asset_export_plan_status_filter_unused(tmp_path):
    from mifp_app.services.asset_cleanup import build_asset_export_plan

    conn = _conn()
    (tmp_path / "image").mkdir()
    (tmp_path / "image" / "a.png").write_bytes(b"1")
    (tmp_path / "image" / "b.png").write_bytes(b"2")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (1,'a.png','image/a.png','image')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (2,'b.png','image/b.png','image')")
    conn.execute("INSERT INTO news(id,title,slug,review_status) VALUES (1,'N','n','published')")
    _link_asset(conn, 1, "news", 1)
    local, missing = build_asset_export_plan(conn, tmp_path, status_filter=["unused"])
    assert [d["id"] for d in local + missing] == [2]


def test_build_asset_export_plan_status_filter_missing():
    from mifp_app.services.asset_cleanup import build_asset_export_plan

    conn = _conn()
    conn.execute("INSERT INTO assets(id,filename,path,kind,storage_status) VALUES (1,'a.png','image/a.png','image','missing')")
    conn.execute("INSERT INTO assets(id,filename,path,kind,storage_status) VALUES (2,'b.png','image/b.png','image','local')")
    local, missing = build_asset_export_plan(conn, Path("/nonexistent"), status_filter=["missing"])
    assert [d["id"] for d in local + missing] == [1]


def test_build_asset_export_plan_status_filter_missing_with_used(tmp_path):
    from mifp_app.services.asset_cleanup import build_asset_export_plan

    conn = _conn()
    (tmp_path / "image").mkdir()
    (tmp_path / "image" / "a.png").write_bytes(b"1")
    conn.execute("INSERT INTO assets(id,filename,path,kind,storage_status) VALUES (1,'a.png','image/a.png','image','missing')")
    conn.execute("INSERT INTO assets(id,filename,path,kind,storage_status) VALUES (2,'b.png','image/b.png','image','local')")
    conn.execute("INSERT INTO news(id,title,slug,review_status) VALUES (1,'N','n','published')")
    _link_asset(conn, 2, "news", 1)
    local, missing = build_asset_export_plan(conn, tmp_path, status_filter=["missing", "used"])
    assert [d["id"] for d in local + missing] == [1]


def test_export_assets_to_zip_with_kind_filter(tmp_path):
    from mifp_app.services.asset_cleanup import export_assets_to_zip

    conn = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "image").mkdir(parents=True)
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "image" / "a.png").write_bytes(b"1")
    (assets_dir / "image" / "b.png").write_bytes(b"2")
    (assets_dir / "pdf" / "c.pdf").write_bytes(b"pdf")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (1,'a.png','image/a.png','image')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (2,'b.png','image/b.png','image')")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (3,'c.pdf','pdf/c.pdf','pdf')")
    zip_path = export_assets_to_zip(conn, assets_dir, kind_filter=["image"])
    assert zip_path is not None
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert [a["id"] for a in manifest["assets"]] == [1, 2]


def test_import_assets_from_zip_dry_run_does_not_insert(tmp_path):
    from mifp_app.services.asset_cleanup import export_assets_to_zip, import_assets_from_zip

    conn = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "image").mkdir(parents=True)
    (assets_dir / "image" / "a.png").write_bytes(b"png")
    conn.execute("INSERT INTO assets(id,filename,path,kind) VALUES (1,'a.png','image/a.png','image')")
    zip_path = export_assets_to_zip(conn, assets_dir)
    assert zip_path is not None
    target = _conn()
    result = import_assets_from_zip(target, tmp_path / "target_assets", zip_path, dry_run=True)
    assert result["dry_run"] is True
    assert result["inserted"] >= 1
    assert result["asset_files_missing"] == []
    count = target.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    assert count == 0


def test_import_assets_from_jsonl_basic(tmp_path):
    from mifp_app.services.asset_cleanup import import_assets_from_jsonl

    conn = _conn()
    jsonl_path = tmp_path / "assets.jsonl"
    jsonl_path.write_text(
        json.dumps({"filename": "doc.pdf", "path": "pdf/doc.pdf", "kind": "pdf", "checksum": "abc123"}) + "\n"
    )
    result = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
    assert result["inserted"] == 1
    row = conn.execute("SELECT filename, kind, storage_status, is_external FROM assets WHERE id=1").fetchone()
    assert row["filename"] == "doc.pdf"
    assert row["kind"] == "pdf"
    assert row["storage_status"] == "missing"
    assert row["is_external"] == 0


def test_import_assets_from_jsonl_dry_run(tmp_path):
    from mifp_app.services.asset_cleanup import import_assets_from_jsonl

    conn = _conn()
    jsonl_path = tmp_path / "assets.jsonl"
    jsonl_path.write_text(
        json.dumps({"filename": "doc.pdf", "path": "pdf/doc.pdf", "kind": "pdf"}) + "\n"
    )
    result = import_assets_from_jsonl(conn, jsonl_path, dry_run=True)
    assert result["dry_run"] is True
    assert result["inserted"] == 1
    count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    assert count == 0


def test_import_assets_from_jsonl_skips_duplicates_by_checksum(tmp_path):
    from mifp_app.services.asset_cleanup import import_assets_from_jsonl

    conn = _conn()
    conn.execute("INSERT INTO assets(id,filename,path,kind,checksum) VALUES (1,'exist.pdf','pdf/exist.pdf','pdf','dup')")
    jsonl_path = tmp_path / "assets.jsonl"
    jsonl_path.write_text(
        json.dumps({"filename": "dup.pdf", "path": "pdf/dup.pdf", "kind": "pdf", "checksum": "dup"}) + "\n"
    )
    result = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
    assert result["skipped"] == 1
    assert result["inserted"] == 0


def test_import_assets_from_jsonl_invalid_json_is_error(tmp_path):
    from mifp_app.services.asset_cleanup import import_assets_from_jsonl

    conn = _conn()
    jsonl_path = tmp_path / "assets.jsonl"
    jsonl_path.write_text("not valid json\n")
    result = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
    assert len(result["errors"]) == 1
    assert result["inserted"] == 0


def test_import_assets_from_jsonl_with_source_url_is_recoverable_external(tmp_path):
    from mifp_app.services.asset_cleanup import import_assets_from_jsonl

    conn = _conn()
    jsonl_path = tmp_path / "assets.jsonl"
    jsonl_path.write_text(
        json.dumps({"filename": "doc.pdf", "path": "pdf/doc.pdf", "kind": "pdf", "source_url": "https://example.com/doc.pdf"}) + "\n"
    )
    result = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
    assert result["inserted"] == 1
    row = conn.execute("SELECT is_external, storage_status FROM assets WHERE id=1").fetchone()
    assert row["is_external"] == 1
    assert row["storage_status"] == "external"
    conn = _conn()
    jsonl_path = tmp_path / "assets.jsonl"
    jsonl_path.write_text(
        json.dumps({"filename": "ext.png", "path": "img/ext.png", "kind": "image"}) + "\n"
    )
    result = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
    assert result["inserted"] == 1
    row = conn.execute("SELECT is_external, storage_status FROM assets WHERE id=1").fetchone()
    assert row["is_external"] == 0
    assert row["storage_status"] == "missing"

from __future__ import annotations

import json
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import yaml


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


def _insert_news_with_document(conn: sqlite3.Connection, news_id: int = 1, asset_id: int = 1) -> None:
    conn.execute(
        "INSERT INTO assets(id, filename, path, kind, checksum) VALUES (?,?,?,?,?)",
        (asset_id, "paper.pdf", "pdf/paper.pdf", "pdf", "sha"),
    )
    conn.execute(
        "INSERT INTO news(id, title, slug, review_status) VALUES (?,?,?,?)",
        (news_id, "News", "news", "published"),
    )
    conn.execute(
        "INSERT INTO asset_links(asset_id, entity_type, entity_id, role) VALUES (?,?,?,?)",
        (asset_id, "news", news_id, "document"),
    )


def test_export_zip_contains_manifest_jsonl_and_asset_file(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip

    conn = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    _insert_news_with_document(conn)

    payload = bundle_to_zip(conn, "news", assets_dir)
    with zipfile.ZipFile(BytesIO(payload), "r") as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
        records_raw = zf.read("records.jsonl").decode("utf-8")

    assert {"manifest.json", "records.jsonl", "assets/pdf/paper.pdf"} <= names
    assert manifest["format"] == "mifp-jsonl-v2"
    assert manifest["format_version"] == 2
    assert manifest["schema_version"] >= 1
    assert len(manifest["records_sha256"]) == 64
    assert manifest["scope"] == "news"
    assert manifest["records"] == 1
    records = [json.loads(line) for line in records_raw.strip().splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["type"] == "news"
    assert records[0]["data"]["slug"] == "news"
    assert records[0]["assets"][0]["path"] == "pdf/paper.pdf"
    assert manifest["files"][0]["size"] == len(b"%PDF-1.4\n")
    assert len(manifest["files"][0]["sha256"]) == 64


def test_export_zip_file_writer_writes_valid_archive(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip_file, parse_zip_payload

    conn = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    _insert_news_with_document(conn)

    destination = tmp_path / "export.zip"
    written = bundle_to_zip_file(conn, "news", assets_dir, destination)
    package = parse_zip_payload(destination)

    assert written == destination.stat().st_size
    assert package["manifest"]["format"] == "mifp-jsonl-v2"
    assert package["manifest"]["scope"] == "news"
    assert package["record_count"] == 1


def test_roundtrip_uses_portable_role_and_parent_event_references(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    source.execute("INSERT INTO roles(id, name, label) VALUES(42, 'board_member', 'Board member')")
    source.execute(
        "INSERT INTO members(id, slug, display_name, role_id) VALUES(90, 'alice', 'Alice Example', 42)"
    )
    source.execute(
        "INSERT INTO events(id, slug, title) VALUES(80, 'parent-event', 'Parent event')"
    )
    source.execute(
        "INSERT INTO events(id, slug, title, parent_event_id) VALUES(7, 'child-event', 'Child event', 80)"
    )

    payload = bundle_to_zip(source, "all", tmp_path / "source-assets")
    target = _conn()
    # Deliberately occupy the source IDs to prove that numeric IDs are not reused.
    target.execute("INSERT INTO roles(id, name, label) VALUES(42, 'other', 'Other')")
    target.execute("INSERT INTO events(id, slug, title) VALUES(80, 'unrelated', 'Unrelated')")
    summary = import_zip_payload(target, payload, "all", tmp_path / "target-assets")

    assert summary["errors"] == []
    member = target.execute(
        "SELECT r.name FROM members m JOIN roles r ON r.id=m.role_id WHERE m.slug='alice'"
    ).fetchone()
    child = target.execute(
        """
        SELECT parent.slug
        FROM events child JOIN events parent ON parent.id=child.parent_event_id
        WHERE child.slug='child-event'
        """
    ).fetchone()
    assert member["name"] == "board_member"
    assert child["slug"] == "parent-event"


def test_complete_roundtrip_restores_durable_state_and_unlinked_assets(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    source_assets = tmp_path / "source-assets"
    (source_assets / "uploads").mkdir(parents=True)
    (source_assets / "uploads" / "orphan.txt").write_bytes(b"not linked yet")
    source.execute("INSERT INTO roles(name,label) VALUES('editor','Editorial board')")
    role_id = source.execute("SELECT id FROM roles WHERE name='editor'").fetchone()[0]
    source.execute(
        "INSERT INTO members(slug,display_name,email,role_id) VALUES('alice','Alice','a@example.test',?)",
        (role_id,),
    )
    member_id = source.execute("SELECT id FROM members WHERE slug='alice'").fetchone()[0]
    source.execute(
        "INSERT INTO pages(slug,title,type,body,review_status) "
        "VALUES('privacy','Privacy','privacy','Complete body','published')"
    )
    source.execute("INSERT INTO settings(key,value) VALUES('cookie_banner_force_version','7')")
    source.execute(
        "INSERT INTO join_requests(first_name,last_name,email,status,member_id,created_at) "
        "VALUES('Alice','Example','join@example.test','approved',?,'2026-07-28 10:00:00')",
        (member_id,),
    )
    source.execute(
        "INSERT INTO assets(filename,path,kind,checksum,alt_text) "
        "VALUES('orphan.txt','uploads/orphan.txt','document','orphan-sha','Pending document')"
    )
    source.execute(
        "INSERT INTO merge_exclusions(entity_type,record_fingerprint,decision,note) "
        "VALUES('member','stable-record','keep_separate','Reviewed manually')"
    )
    source.execute(
        "INSERT INTO resolved_pairs(entity_type,left_fingerprint,right_fingerprint,action) "
        "VALUES('member','left','right','rejected')"
    )
    run_id = source.execute(
        "INSERT INTO quality_runs(status,fingerprint,summary_json,completed_at) "
        "VALUES('completed','run','{}',CURRENT_TIMESTAMP)"
    ).lastrowid
    source.execute(
        "INSERT INTO quality_findings("
        "run_id,action_type,entity_type,record_ids_json,classification,score,"
        "evidence_json,contradictions_json,plan_json,fingerprint,status"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, "clean_record", "member", "[1]", "needs_cleaning", 0.7,
            "[]", "[]", "{}", "portable-finding", "rejected",
        ),
    )

    payload = bundle_to_zip(source, "all", source_assets)
    with zipfile.ZipFile(BytesIO(payload), "r") as zf:
        assert "state.json" in zf.namelist()
        assert "assets/uploads/orphan.txt" in zf.namelist()

    target = _conn()
    target_assets = tmp_path / "target-assets"
    first = import_zip_payload(target, payload, "all", target_assets)
    second = import_zip_payload(target, payload, "all", target_assets)

    assert first["errors"] == second["errors"] == []
    assert target.execute("SELECT body FROM pages WHERE slug='privacy'").fetchone()[0] == "Complete body"
    assert target.execute(
        "SELECT value FROM settings WHERE key='cookie_banner_force_version'"
    ).fetchone()[0] == "7"
    assert target.execute("SELECT label FROM roles WHERE name='editor'").fetchone()[0] == "Editorial board"
    assert target.execute("SELECT COUNT(*) FROM join_requests WHERE email='join@example.test'").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM merge_exclusions").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM resolved_pairs").fetchone()[0] == 1
    assert target.execute(
        "SELECT COUNT(*) FROM quality_findings WHERE fingerprint='portable-finding' AND status='rejected'"
    ).fetchone()[0] == 1
    assert target.execute("SELECT alt_text FROM assets WHERE path='uploads/orphan.txt'").fetchone()[0] == "Pending document"
    assert (target_assets / "uploads" / "orphan.txt").read_bytes() == b"not linked yet"


def test_data_quality_fingerprint_ignores_database_identity_and_timestamps() -> None:
    from mifp_app.services.data_quality.normalizers import stable_fingerprint

    before = [{"id": 1, "slug": "same", "title": "Same", "created_at": "yesterday"}]
    after = [{"id": 999, "slug": "same", "title": "Same", "created_at": "today"}]

    assert stable_fingerprint("news", before, action="clean_record") == stable_fingerprint(
        "news", after, action="clean_record"
    )


def test_export_upgrades_legacy_quality_decision_fingerprint(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import (
        _legacy_quality_fingerprint,
        bundle_to_zip,
        parse_zip_payload,
    )
    from mifp_app.services.data_quality.normalizers import stable_fingerprint

    source = _conn()
    source.execute(
        "INSERT INTO news(id,title,slug,review_status,created_at) "
        "VALUES(81,'News','news','published','2020-01-01')"
    )
    record = dict(source.execute("SELECT * FROM news WHERE id=81").fetchone())
    legacy = _legacy_quality_fingerprint("news", [record], "clean_record")
    run_id = source.execute(
        "INSERT INTO quality_runs(status,fingerprint,summary_json,completed_at) "
        "VALUES('completed','legacy-run','{}',CURRENT_TIMESTAMP)"
    ).lastrowid
    source.execute(
        "INSERT INTO quality_findings("
        "run_id,action_type,entity_type,record_ids_json,classification,score,"
        "evidence_json,contradictions_json,plan_json,fingerprint,status"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, "clean_record", "news", "[81]", "needs_cleaning", 0.7,
            "[]", "[]", "{}", legacy, "rejected",
        ),
    )

    package = parse_zip_payload(bundle_to_zip(source, "all", tmp_path / "assets"))

    assert package["durable_state"]["quality_decisions"][0]["fingerprint"] == stable_fingerprint(
        "news", [record], action="clean_record"
    )


def test_parse_zip_rejects_tampered_records(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, parse_zip_payload

    source = _conn()
    source.execute("INSERT INTO news(title, slug) VALUES('Original', 'original')")
    payload = bundle_to_zip(source, "news", tmp_path / "assets")
    changed = BytesIO()
    with zipfile.ZipFile(BytesIO(payload), "r") as src, zipfile.ZipFile(
        changed, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "records.jsonl":
                body = body.replace(b"Original", b"Tampered")
            dst.writestr(info.filename, body)

    with pytest.raises(ValueError, match="integrity"):
        parse_zip_payload(changed.getvalue())


def test_import_rejects_review_status_not_supported_by_content_tables(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    source = tmp_path / "unsupported-status.jsonl"
    source.write_text(json.dumps({
        "type": "news",
        "data": {"title": "Archived is not importable", "review_status": "archived"},
    }) + "\n", encoding="utf-8")

    summary = import_jsonl(_conn(), source)

    assert summary["inserted"] == {}
    assert summary["skipped"] == 1
    assert "Invalid review_status: archived" in summary["errors"][0]["error"]


def test_parse_zip_rejects_tampered_durable_state(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, parse_zip_payload

    source = _conn()
    source.execute("INSERT INTO settings(key, value) VALUES('portable-test', 'original')")
    payload = bundle_to_zip(source, "all", tmp_path / "assets")
    changed = BytesIO()
    with zipfile.ZipFile(BytesIO(payload), "r") as src, zipfile.ZipFile(
        changed, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "state.json":
                body = body.replace(b'"original"', b'"tampered"')
            dst.writestr(info.filename, body)

    with pytest.raises(ValueError, match="state.json does not match.*manifest.json"):
        parse_zip_payload(changed.getvalue())


def test_parse_zip_rejects_tampered_asset(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, parse_zip_payload

    source = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    _insert_news_with_document(source)
    payload = bundle_to_zip(source, "news", assets_dir)
    changed = BytesIO()
    with zipfile.ZipFile(BytesIO(payload), "r") as src, zipfile.ZipFile(
        changed, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "assets/pdf/paper.pdf":
                body = b"%PDF-tampered"
            dst.writestr(info.filename, body)

    with pytest.raises(ValueError, match="verification"):
        parse_zip_payload(changed.getvalue())


def test_import_rejects_scope_mismatch(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    payload = bundle_to_zip(_conn(), "news", tmp_path / "assets")
    with pytest.raises(ValueError, match="does not match"):
        import_zip_payload(_conn(), payload, "events", tmp_path / "target-assets")


def test_import_zip_dry_run_does_not_write(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    _insert_news_with_document(source)

    payload = bundle_to_zip(source, "news", assets_dir)

    target = _conn()
    import_root = tmp_path / "import"
    target_assets = import_root / "target_assets"
    summary = import_zip_payload(target, payload, "news", target_assets, dry_run=True)
    assert summary["dry_run"] is True
    assert target.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 0
    assert not (target_assets / "pdf" / "paper.pdf").exists()
    assert not (import_root / "assets" / "pdf" / "paper.pdf").exists()


def test_export_zip_only_contains_assets_referenced_by_scope(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip

    conn = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (assets_dir / "pdf" / "unused.pdf").write_bytes(b"%PDF-1.4\n")
    _insert_news_with_document(conn)
    conn.execute(
        "INSERT INTO assets(id, filename, path, kind, checksum) VALUES (?,?,?,?,?)",
        (2, "unused.pdf", "pdf/unused.pdf", "pdf", "sha-unused"),
    )

    payload = bundle_to_zip(conn, "news", assets_dir)

    with zipfile.ZipFile(BytesIO(payload), "r") as zf:
        names = set(zf.namelist())
    assert "assets/pdf/paper.pdf" in names
    assert "assets/pdf/unused.pdf" not in names


def test_parse_zip_rejects_unsafe_asset_path(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    payload = _raw_zip(
        manifest_files=[{"archive_path": "assets/../evil.txt"}],
        files={"assets/../evil.txt": b"bad"},
    )

    with pytest.raises(ValueError, match="unsafe"):
        parse_zip_payload(payload)


def test_parse_zip_rejects_undeclared_asset_file(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    payload = _raw_zip(files={"assets/pdf/paper.pdf": b"%PDF-1.4\n"})

    with pytest.raises(ValueError, match="not declared"):
        parse_zip_payload(payload)


def test_import_zip_requires_declared_asset_unless_skipped(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import import_zip_payload, parse_zip_payload

    payload = _raw_zip(manifest_files=[{"archive_path": "assets/pdf/missing.pdf"}])
    package = parse_zip_payload(payload)
    assert package["missing_assets"] == ["assets/pdf/missing.pdf"]

    with pytest.raises(ValueError, match="missing 1 declared asset"):
        import_zip_payload(_conn(), payload, "news", tmp_path / "target_assets")

    summary = import_zip_payload(_conn(), payload, "news", tmp_path / "target_assets", skip_assets=True)
    assert summary["errors"] == []


def test_parse_zip_rejects_duplicate_members(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    manifest = {
        "scope": "news",
        "records": 0,
        "files": [],
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "")
        with pytest.warns(UserWarning, match="Duplicate name"):
            zf.writestr("records.jsonl", "")
        zf.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="duplicate"):
        parse_zip_payload(out.getvalue())


def test_import_zip_second_import_updates_not_duplicates(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    assets_dir = tmp_path / "assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    _insert_news_with_document(source)

    payload = bundle_to_zip(source, "news", assets_dir)

    target = _conn()
    target_assets = tmp_path / "target_assets"
    first = import_zip_payload(target, payload, "news", target_assets, dry_run=False)
    second = import_zip_payload(target, payload, "news", target_assets, dry_run=False, skip_assets=True)
    assert first["inserted"].get("news", 0) >= 1
    assert second["updated"].get("news", 0) >= 1
    assert target.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 1


def test_roundtrip_restores_asset_identity_and_db_path(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    assets_dir = tmp_path / "source-assets"
    (assets_dir / "pdf").mkdir(parents=True)
    (assets_dir / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    source.execute(
        "INSERT INTO assets(id, uid, filename, original_filename, path, mime_type, size, kind, "
        "storage_status, checksum, content_sha256, source_url_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            1, "uid-paper-1", "paper.pdf", "report.pdf", "pdf/paper.pdf",
            "application/pdf", 9, "pdf", "local", "paper-sha", "paper-content", None,
        ),
    )
    source.execute(
        "INSERT INTO news(id, title, slug, review_status) VALUES(1, 'News', 'news', 'published')"
    )
    source.execute(
        "INSERT INTO asset_links(asset_id, entity_type, entity_id, role) VALUES(1, 'news', 1, 'document')"
    )

    payload = bundle_to_zip(source, "news", assets_dir)
    with zipfile.ZipFile(BytesIO(payload), "r") as zf:
        exported = json.loads(zf.read("records.jsonl").decode("utf-8").strip())
    assert exported["assets"][0]["uid"] == "uid-paper-1"
    assert exported["assets"][0]["checksum"] == "paper-sha"
    assert exported["assets"][0]["path"] == "pdf/paper.pdf"

    target = _conn()
    target_assets = tmp_path / "target-assets"
    first = import_zip_payload(target, payload, "news", target_assets)
    second = import_zip_payload(target, payload, "news", target_assets)

    assert first["errors"] == second["errors"] == []
    row = target.execute("SELECT uid, checksum, path, storage_status, filename FROM assets").fetchone()
    assert row["uid"] == "uid-paper-1"
    assert row["checksum"] == "paper-sha"
    assert row["path"] == "pdf/paper.pdf"
    assert row["storage_status"] == "local"
    assert row["filename"] == "paper.pdf"
    assert target.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 1
    assert (target_assets / "pdf" / "paper.pdf").read_bytes() == b"%PDF-1.4\n"


def test_roundtrip_external_asset_preserves_uid_and_storage_status(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    source.execute(
        "INSERT INTO assets(id, uid, filename, path, kind, source_url, storage_status, checksum, "
        "source_url_sha256) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            1, "uid-ext-1", "external-f.pdf", "external/external-f-abc123.pdf",
            "pdf", "https://www.mifp.eu/files/f.pdf", "external", "url-hash", "url-hash",
        ),
    )
    source.execute(
        "INSERT INTO news(id, title, slug, review_status) VALUES(1, 'News', 'news', 'published')"
    )
    source.execute(
        "INSERT INTO asset_links(asset_id, entity_type, entity_id, role) VALUES(1, 'news', 1, 'document')"
    )

    payload = bundle_to_zip(source, "news", tmp_path / "source-assets")
    target = _conn()
    summary = import_zip_payload(target, payload, "news", tmp_path / "target-assets")

    assert summary["errors"] == []
    row = target.execute(
        "SELECT uid, checksum, storage_status, source_url, is_external FROM assets"
    ).fetchone()
    assert row["uid"] == "uid-ext-1"
    assert row["checksum"] == "url-hash"
    assert row["storage_status"] == "external"
    assert row["source_url"] == "https://www.mifp.eu/files/f.pdf"
    assert row["is_external"] == 1
    assert target.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 1


def test_import_keeps_valid_record_when_packaged_asset_is_invalid(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    source_assets = tmp_path / "package-assets"
    source_assets.mkdir()
    (source_assets / "broken.svg").write_text("<html>not an image</html>", encoding="utf-8")
    records = tmp_path / "records.jsonl"
    records.write_text(json.dumps({
        "type": "news",
        "data": {"title": "Valid news", "slug": "valid-news", "review_status": "published"},
        "assets": [{"path": "broken.svg", "role": "cover", "kind": "image"}],
    }) + "\n", encoding="utf-8")

    summary = import_jsonl(
        conn,
        records,
        assets_dir=tmp_path / "stored-assets",
        asset_source_dir=source_assets,
    )

    assert summary["errors"] == []
    assert len(summary["asset_errors"]) == 1
    assert summary["inserted"]["news"] == 1
    assert conn.execute("SELECT COUNT(*) FROM news WHERE slug='valid-news'").fetchone()[0] == 1



def _raw_zip(*, manifest_files: list[dict] | None = None, files: dict[str, bytes] | None = None) -> bytes:
    manifest = {
        "scope": "news",
        "records": 0,
        "files": manifest_files or [],
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "")
        for name, payload in (files or {}).items():
            zf.writestr(name, payload)
        zf.writestr("manifest.json", json.dumps(manifest))
    return out.getvalue()


def test_import_jsonl_publishes_event_with_review_status(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "type": "event",
            "data": {
                "title": "Past Workshop",
                "slug": "past-workshop",
                "start_date": "2020-02-10",
                "review_status": "published",
            },
            "links": [],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    summary = import_jsonl(conn, path, dry_run=False)

    assert summary["errors"] == []
    row = conn.execute("SELECT review_status, is_featured FROM events WHERE slug='past-workshop'").fetchone()
    assert row["review_status"] == "published"
    assert row["is_featured"] == 0


def test_import_jsonl_publication_upserts_by_slug(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    conn.execute(
        "INSERT INTO publications(title, slug, year, abstract) VALUES('Old title','same-slug',20,'x')"
    )
    path = tmp_path / "upload.jsonl"
    path.write_text(
        json.dumps({
            "type": "publication",
            "data": {
                "title": "Updated title",
                "slug": "same-slug",
                "year": 2026,
                "abstract": "new detailed abstract here",
                "review_status": "published",
            },
            "links": [],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    summary = import_jsonl(conn, path, dry_run=False)

    assert summary["errors"] == []
    assert conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1
    row = conn.execute("SELECT year, abstract FROM publications WHERE slug='same-slug'").fetchone()
    assert row["year"] == 2026
    assert row["abstract"] == "new detailed abstract here"


def test_import_jsonl_member_update_does_not_steal_existing_slug(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    conn.execute(
        "INSERT INTO members(display_name, slug, affiliation) VALUES('Alice Smith','alice-smith','University A')"
    )
    conn.execute(
        "INSERT INTO members(display_name, slug, affiliation) VALUES('Alice S.','alice-s','University A')"
    )
    path = tmp_path / "members.jsonl"
    path.write_text(
        json.dumps({
            "type": "member",
            "data": {
                "display_name": "Alice Smith",
                "slug": "alice-s",
                "affiliation": "University A",
                "review_status": "published",
            },
            "links": [],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    summary = import_jsonl(conn, path, dry_run=False)

    assert summary["errors"] == []
    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 2
    rows = conn.execute("SELECT slug FROM members ORDER BY id").fetchall()
    assert [r["slug"] for r in rows] == ["alice-smith", "alice-s"]


def test_repeated_member_import_matches_reversed_name_without_email(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    first_path = tmp_path / "member-first.jsonl"
    second_path = tmp_path / "member-second.jsonl"
    first_path.write_text(json.dumps({
        "type": "member",
        "data": {"display_name": "Alexey Kavokin", "slug": "alexey-kavokin"},
    }) + "\n", encoding="utf-8")
    second_path.write_text(json.dumps({
        "type": "member",
        "data": {
            "display_name": "Kavokin Alexey",
            "slug": "kavokin-alexey-old",
            "affiliation": "University of Southampton",
        },
    }) + "\n", encoding="utf-8")

    first = import_jsonl(conn, first_path)
    second = import_jsonl(conn, second_path)

    assert first["inserted"]["member"] == 1
    assert second["updated"]["member"] == 1
    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1
    row = conn.execute("SELECT display_name,slug,affiliation FROM members").fetchone()
    assert row["display_name"] == "Alexey Kavokin"
    assert row["slug"] == "alexey-kavokin"
    assert row["affiliation"] == "University of Southampton"


def test_member_import_does_not_merge_same_name_with_conflicting_emails(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    conn.execute(
        "INSERT INTO members(display_name,slug,email) VALUES('Alex Smith','alex-smith-one','one@example.test')"
    )
    path = tmp_path / "homonym.jsonl"
    path.write_text(json.dumps({
        "type": "member",
        "data": {
            "display_name": "Alex Smith",
            "slug": "alex-smith-two",
            "email": "two@example.test",
        },
    }) + "\n", encoding="utf-8")

    summary = import_jsonl(conn, path)

    assert summary["inserted"]["member"] == 1
    assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 2


def test_import_jsonl_event_url_becomes_link(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "type": "event",
            "data": {
                "title": "Linked Event",
                "slug": "linked-event",
                "start_date": "2026-06-01",
                "review_status": "published",
                "location": "Online",
            },
            "links": [{"url": "https://example.com/event", "role": "primary"}],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    summary = import_jsonl(conn, path, dry_run=False)

    assert summary["errors"] == []
    links = conn.execute(
        "SELECT url, role FROM entity_links WHERE entity_type='event' AND entity_id=1"
    ).fetchall()
    assert len(links) == 1
    assert links[0]["url"] == "https://example.com/event"


def test_import_jsonl_enriches_without_replacing_existing_links_and_assets(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    assets_root = tmp_path / "assets-root"
    (assets_root / "assets" / "pdf").mkdir(parents=True)
    existing_pdf = assets_root / "assets" / "pdf" / "existing.pdf"
    imported_pdf = assets_root / "assets" / "pdf" / "imported.pdf"
    existing_pdf.write_bytes(b"%PDF-1.4 existing\n")
    imported_pdf.write_bytes(b"%PDF-1.4 imported\n")

    conn.execute(
        "INSERT INTO assets(id, filename, path, kind, checksum) VALUES (?,?,?,?,?)",
        (1, "existing.pdf", "pdf/existing.pdf", "pdf", "existing-sha"),
    )
    conn.execute(
        "INSERT INTO news(id, title, slug, summary, review_status) VALUES (?,?,?,?,?)",
        (1, "Original title", "same-news", "Curated summary", "published"),
    )
    conn.execute(
        "INSERT INTO asset_links(asset_id, entity_type, entity_id, role, is_primary) VALUES (?,?,?,?,?)",
        (1, "news", 1, "document", 1),
    )
    conn.execute(
        "INSERT INTO entity_links(entity_type, entity_id, url, label, role, is_primary) VALUES (?,?,?,?,?,?)",
        ("news", 1, "https://existing.example", "Existing", "primary", 1),
    )

    path = tmp_path / "news.jsonl"
    path.write_text(
        json.dumps({
            "type": "news",
            "data": {
                "title": "Imported title should not replace",
                "slug": "same-news",
                "summary": "Imported summary should not replace curated one",
                "body": "New body fills an empty field",
                "review_status": "published",
            },
            "links": [{"url": "https://new.example", "role": "source", "label": "Source"}],
            "assets": [{"path": "assets/pdf/imported.pdf", "role": "document", "kind": "pdf"}],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    import os
    old_assets_dir = os.environ.get("ASSETS_DIR")
    os.environ["ASSETS_DIR"] = str(assets_root / "assets")
    try:
        from mifp_app.config import Config
        Config.ASSETS_DIR = assets_root / "assets"
        summary = import_jsonl(conn, path, dry_run=False)
    finally:
        if old_assets_dir is None:
            os.environ.pop("ASSETS_DIR", None)
        else:
            os.environ["ASSETS_DIR"] = old_assets_dir

    assert summary["errors"] == []
    row = conn.execute("SELECT title, summary, body FROM news WHERE slug='same-news'").fetchone()
    assert row["title"] == "Original title"
    assert row["summary"] == "Curated summary"
    assert row["body"] == "New body fills an empty field"
    assert conn.execute("SELECT COUNT(*) FROM entity_links WHERE entity_type='news' AND entity_id=1").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM asset_links WHERE entity_type='news' AND entity_id=1").fetchone()[0] == 2


def test_import_updates_same_event_series_and_year_instead_of_inserting_duplicate(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    conn.execute(
        "INSERT INTO events(title,slug,start_date,description,review_status) VALUES(?,?,?,?,?)",
        ("PLMCN-2023", "plmcn-2023", "2023-01-01", "", "published"),
    )
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({
        "type": "event",
        "data": {
            "title": "PLMCN 2023 International Conference on Physics of Light-Matter Coupling in Nanostructures",
            "slug": "plmcn-2023-international-conference",
            "start_date": "2023-06-12",
            "description": "Complete conference description.",
        },
    }) + "\n", encoding="utf-8")

    result = import_jsonl(conn, path)
    assert result["inserted"] == {}
    assert result["updated"] == {"event": 1}
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert conn.execute("SELECT description FROM events").fetchone()[0] == "Complete conference description."


def test_export_empty_scope(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip

    conn = _conn()
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)

    payload = bundle_to_zip(conn, "members", assets_dir)
    with zipfile.ZipFile(BytesIO(payload), "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        records = zf.read("records.jsonl").decode("utf-8").strip()

    assert manifest["scope"] == "members"
    assert manifest["records"] == 0
    assert records == ""


def test_export_scope_all(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_zip

    conn = _conn()
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)

    conn.execute(
        "INSERT INTO news(id, title, slug, review_status) VALUES (1, 'News Item', 'news-1', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, title, slug, start_date, review_status) VALUES (1, 'Event', 'event-1', '2024-01-01', 'published')"
    )

    payload = bundle_to_zip(conn, "all", assets_dir)
    with zipfile.ZipFile(BytesIO(payload), "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        raw = zf.read("records.jsonl").decode("utf-8")
        lines = [json.loads(l) for l in raw.strip().splitlines() if l.strip()]
        types = {l.get("type") for l in lines}

    assert manifest["records"] >= 2
    assert "news" in types
    assert "event" in types


def test_parse_zip_rejects_oversized_payload() -> None:
    from mifp_app.config import Config
    import os

    orig = Config.IMPORT_MAX_ZIP_BYTES
    Config.IMPORT_MAX_ZIP_BYTES = 1024
    try:
        from mifp_app.services.data_portability import parse_zip_payload

        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", '{"scope":"news","records":0,"files":[]}')
            zf.writestr("records.jsonl", "")
            zf.writestr("padding.bin", os.urandom(2048), compress_type=zipfile.ZIP_STORED)

        with pytest.raises(ValueError, match="exceeds maximum size"):
            parse_zip_payload(out.getvalue())
    finally:
        Config.IMPORT_MAX_ZIP_BYTES = orig


def test_parse_zip_rejects_malformed_manifest() -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "")
    with pytest.raises(ValueError, match="missing manifest.json"):
        parse_zip_payload(out.getvalue())

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", "{corrupt")
        zf.writestr("records.jsonl", "")
    with pytest.raises(ValueError, match="manifest"):
        parse_zip_payload(out.getvalue())



def test_parse_zip_rejects_manifest_asset_path_mismatch() -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    payload = _raw_zip(
        manifest_files=[{
            "path": "pdf/paper.pdf",
            "archive_path": "assets/pdf/other.pdf",
            "size": 4,
            "sha256": "0" * 64,
        }],
        files={"assets/pdf/other.pdf": b"data"},
    )

    with pytest.raises(ValueError, match="does not match archive_path"):
        parse_zip_payload(payload)


def test_parse_zip_rejects_duplicate_manifest_asset_entry() -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    payload = _raw_zip(manifest_files=[
        {"path": "pdf/paper.pdf", "archive_path": "assets/pdf/paper.pdf"},
        {"path": "pdf/paper.pdf", "archive_path": "assets/pdf/paper.pdf"},
    ])

    with pytest.raises(ValueError, match="duplicate archive_path"):
        parse_zip_payload(payload)


def test_canonical_zip_requires_version_and_records_hash() -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    manifest = {
        "format": "mifp-jsonl-v2",
        "scope": "news",
        "records": 0,
        "files": [],
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "")
        zf.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="format_version=2"):
        parse_zip_payload(out.getvalue())

    manifest["format_version"] = 2
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "")
        zf.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValueError, match="records_sha256"):
        parse_zip_payload(out.getvalue())


def test_canonical_zip_requires_complete_asset_integrity_metadata() -> None:
    from mifp_app.services.data_portability import parse_zip_payload

    manifest = {
        "format": "mifp-jsonl-v2",
        "format_version": 2,
        "scope": "news",
        "records": 0,
        "records_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "files": [{"archive_path": "assets/pdf/paper.pdf"}],
    }
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "")
        zf.writestr("assets/pdf/paper.pdf", b"data")
        zf.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="path"):
        parse_zip_payload(out.getvalue())


def test_parse_zip_rejects_oversized_records_member(monkeypatch: pytest.MonkeyPatch) -> None:
    from mifp_app.config import Config
    from mifp_app.services.data_portability import parse_zip_payload

    monkeypatch.setattr(Config, "IMPORT_MAX_JSONL_BYTES", 4)
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("records.jsonl", "12345")
        zf.writestr("manifest.json", json.dumps({"scope": "news", "records": 0, "files": []}))

    with pytest.raises(ValueError, match="records.jsonl exceeds maximum size"):
        parse_zip_payload(out.getvalue())


def test_import_jsonl_rejects_oversized_file_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mifp_app.config import Config
    from mifp_app.services.importers import ImportValidationError, import_jsonl

    monkeypatch.setattr(Config, "IMPORT_MAX_JSONL_BYTES", 8)
    path = tmp_path / "too-large.jsonl"
    path.write_bytes(b"{" + b"x" * 32 + b"}")

    with pytest.raises(ImportValidationError, match="exceeds maximum size"):
        import_jsonl(_conn(), path)


def test_import_jsonl_event_upsert_by_slug(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    conn.execute(
        "INSERT INTO events(title, slug, start_date, review_status) VALUES('Old event','same-event','2024-01-01','published')"
    )
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({
            "type": "event",
            "data": {
                "title": "Updated event",
                "slug": "same-event",
                "start_date": "2026-06-15",
                "location": "New Location",
                "review_status": "published",
            },
            "links": [],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    summary = import_jsonl(conn, path, dry_run=False)

    assert summary["errors"] == []
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    row = conn.execute("SELECT title, location, start_date FROM events WHERE slug='same-event'").fetchone()
    assert row["title"] == "Old event"  # existing non-null preserved by _merge_fields
    assert row["location"] == "New Location"   # filled (was NULL)
    assert row["start_date"] == "2024-01-01"   # existing non-null preserved


def test_force_import_assigns_a_unique_slug_instead_of_clearing_it(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    path = tmp_path / "sponsors.jsonl"
    path.write_text(
        json.dumps({
            "type": "sponsor",
            "data": {
                "name": "Sample Sponsor",
                "slug": "sample-sponsor",
                "is_active": 1,
            },
        }) + "\n",
        encoding="utf-8",
    )

    first = import_jsonl(conn, path)
    forced = import_jsonl(conn, path, force_import=True)

    assert first["errors"] == []
    assert forced["errors"] == []
    slugs = [
        row["slug"]
        for row in conn.execute("SELECT slug FROM sponsors ORDER BY id").fetchall()
    ]
    assert slugs == ["sample-sponsor", "sample-sponsor-2"]


def test_import_jsonl_reports_invalid_utf8(tmp_path: Path) -> None:
    from mifp_app.services.importers import ImportValidationError, import_jsonl

    path = tmp_path / "invalid.jsonl"
    path.write_bytes(b'{"type":"news","data":{"title":"caf\xe9"}}\n')

    with pytest.raises(ImportValidationError, match="UTF-8"):
        import_jsonl(_conn(), path)


def test_import_jsonl_rolls_back_failed_record(tmp_path: Path) -> None:
    from mifp_app.services.importers import import_jsonl

    conn = _conn()
    conn.execute(
        """
        CREATE TRIGGER reject_import_record
        BEFORE INSERT ON import_records
        BEGIN
          SELECT RAISE(ABORT, 'simulated import failure');
        END
        """
    )
    path = tmp_path / "rollback.jsonl"
    path.write_text(
        json.dumps({
            "type": "news",
            "data": {"title": "Must roll back", "slug": "must-roll-back"},
        }) + "\n",
        encoding="utf-8",
    )

    summary = import_jsonl(conn, path)

    assert summary["inserted"] == {}
    assert summary["skipped"] == 1
    assert summary["rolled_back"] == 1


def test_roundtrip_preserves_pdf_entity_link_not_promoted_to_asset(tmp_path: Path) -> None:
    """A portable export stores file-like URLs as entity_links, so re-importing
    must restore them as entity_links and NOT promote them into assets."""
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    source.execute(
        "INSERT INTO news(id, title, slug, review_status) VALUES (1, 'News', 'news', 'draft')"
    )
    source.execute(
        "INSERT INTO entity_links(entity_type, entity_id, url, role, is_primary, sort_order) "
        "VALUES ('news', 1, 'https://example.test/notes.pdf', 'document', 1, 1)"
    )

    src_assets = tmp_path / "assets"
    src_assets.mkdir()
    payload = bundle_to_zip(source, "news", src_assets)

    target_assets = tmp_path / "restored-assets"
    target_assets.mkdir()
    fresh = _conn()
    summary = import_zip_payload(fresh, payload, "news", assets_dir=target_assets)

    assert summary["errors"] == []
    assert summary["asset_errors"] == []
    link = fresh.execute(
        "SELECT role, url FROM entity_links WHERE entity_type='news' AND entity_id=?"
        " ORDER BY sort_order",
        (1,),
    ).fetchone()
    assert link is not None
    assert link["url"] == "https://example.test/notes.pdf"
    assert link["role"] == "document"
    assert fresh.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    assert fresh.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 0


def test_roundtrip_preserves_scraper_provenance(tmp_path: Path) -> None:
    """Full-database export/import keeps source_systems/runs/records and
    canonical_mappings (scraper lineage), re-linking references by uid."""
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn()
    source.execute(
        "INSERT INTO source_systems(id, uid, name, kind, base_url, description) "
        "VALUES (1, 'sys-1', 'Aruba', 'scraper', 'https://example.test', 'site')"
    )
    source.execute(
        "INSERT INTO source_runs(id, uid, source_system_id, scraper_version, parser_version, "
        "status, source_snapshot_sha256, notes) "
        "VALUES (1, 'run-1', 1, '2.0', '1.3', 'completed', 'abc123', NULL)"
    )
    source.execute(
        "INSERT INTO source_records(id, uid, source_run_id, source_system_id, external_id, "
        "source_url, record_type, mapping_status, raw_payload) "
        "VALUES (1, 'rec-1', 1, 1, 'ext-1', 'https://example.test/1', 'news', 'mapped', '{}')"
    )
    source.execute(
        "INSERT INTO canonical_mappings(id, source_record_id, entity_type, entity_uid, "
        "mapping_kind, confidence, decision_note) "
        "VALUES (1, 1, 'news', 'news-1', 'canonical', 0.95, NULL)"
    )
    source.commit()

    src_assets = tmp_path / "assets"
    src_assets.mkdir()
    payload = bundle_to_zip(source, "all", src_assets)

    target_assets = tmp_path / "restored-assets"
    target_assets.mkdir()
    fresh = _conn()
    summary = import_zip_payload(fresh, payload, "all", assets_dir=target_assets)
    assert summary["errors"] == []
    assert summary["asset_errors"] == []

    assert fresh.execute("SELECT COUNT(*) FROM source_systems").fetchone()[0] == 1
    assert fresh.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0] == 1
    assert fresh.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 1
    assert fresh.execute("SELECT COUNT(*) FROM canonical_mappings").fetchone()[0] == 1

    rec = fresh.execute(
        "SELECT r.uid AS rec_uid, run.uid AS run_uid, sys.uid AS sys_uid "
        "FROM source_records r "
        "JOIN source_runs run ON run.id = r.source_run_id "
        "JOIN source_systems sys ON sys.id = r.source_system_id "
        "WHERE r.uid = 'rec-1'"
    ).fetchone()
    assert rec is not None
    assert rec["run_uid"] == "run-1"
    assert rec["sys_uid"] == "sys-1"
    mapping = fresh.execute(
        "SELECT entity_type, entity_uid, mapping_kind, confidence "
        "FROM canonical_mappings LIMIT 1"
    ).fetchone()
    assert mapping is not None
    assert mapping["entity_uid"] == "news-1"
    assert mapping["confidence"] == 0.95
    assert mapping["mapping_kind"] == "canonical"


def test_jsonl_package_roundtrip_matches_zip_for_records_state_and_assets(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import (
        bundle_to_jsonl_file,
        bundle_to_zip,
        import_jsonl_payload,
        import_zip_payload,
    )

    source = _conn()
    source_assets = tmp_path / "source-assets"
    (source_assets / "pdf").mkdir(parents=True)
    (source_assets / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\nportable\n")
    _insert_news_with_document(source)
    source.execute("INSERT INTO settings(key,value) VALUES('portable-test','yes')")

    jsonl_path = tmp_path / "export.jsonl"
    manifest = bundle_to_jsonl_file(source, "all", source_assets, jsonl_path)
    zip_payload = bundle_to_zip(source, "all", source_assets)

    assert manifest["format"] == "mifp-jsonl-v2"
    assert manifest["container"] == "jsonl"
    assert manifest["files"]
    text = jsonl_path.read_text(encoding="utf-8")
    assert '"kind": "asset_chunk"' in text

    jsonl_target = _conn()
    zip_target = _conn()
    jsonl_assets = tmp_path / "jsonl-assets"
    zip_assets = tmp_path / "zip-assets"
    jsonl_summary = import_jsonl_payload(jsonl_target, jsonl_path, "all", jsonl_assets)
    zip_summary = import_zip_payload(zip_target, zip_payload, "all", zip_assets)

    assert jsonl_summary["errors"] == zip_summary["errors"] == []
    for conn in (jsonl_target, zip_target):
        assert conn.execute("SELECT title FROM news WHERE slug='news'").fetchone()[0] == "News"
        assert conn.execute("SELECT value FROM settings WHERE key='portable-test'").fetchone()[0] == "yes"
    assert (jsonl_assets / "pdf" / "paper.pdf").read_bytes() == b"%PDF-1.4\nportable\n"
    assert (zip_assets / "pdf" / "paper.pdf").read_bytes() == b"%PDF-1.4\nportable\n"


def test_jsonl_package_detects_tampered_asset_chunk(tmp_path: Path) -> None:
    from mifp_app.services.data_portability import bundle_to_jsonl_file, import_jsonl_payload

    source = _conn()
    source_assets = tmp_path / "source-assets"
    (source_assets / "pdf").mkdir(parents=True)
    (source_assets / "pdf" / "paper.pdf").write_bytes(b"%PDF-1.4\nportable\n")
    _insert_news_with_document(source)
    package = tmp_path / "export.jsonl"
    bundle_to_jsonl_file(source, "news", source_assets, package)

    lines = package.read_text(encoding="utf-8").splitlines()
    for index, raw in enumerate(lines):
        item = json.loads(raw)
        meta = item.get("_mifp") or {}
        if meta.get("kind") == "asset_chunk":
            meta["data"] = "AAAA"
            lines[index] = json.dumps(item, sort_keys=True)
            break
    package.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="integrity|declared size"):
        import_jsonl_payload(_conn(), package, "news", tmp_path / "target-assets")

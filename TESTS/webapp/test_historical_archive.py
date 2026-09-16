from __future__ import annotations

import json
import io
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest


SCHEMA = Path(__file__).resolve().parents[2] / "MIFPAPP/CORE/mifp_app/db/schema.sql"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def _event(*, uid="event_archive_one", slug="archive-one", schema="mifp-historical-event-v1"):
    return {
        "schema": schema, "uid": uid, "slug": slug,
        "public_path": f"archive/conferences/2014/{slug}",
        "public_url": f"https://events.mifp.eu/archive/conferences/2014/{slug}/",
        "title": "Archive One", "acronym": "AO1", "category": "conferences",
        "event_type": "conference", "series_key": "archive-series",
        "dates": {"start": "2014-05-01", "end": "2014-05-02", "text": "1–2 May 2014", "precision": "range"},
        "location": {"display": "Rome, Italy"},
        "summary": "Recovered event summary.",
        "description": "Recovered description. This site uses cookies Joomla boilerplate.",
        "topics": ["Quantum physics"], "topics_note": "Recovered from the programme.",
        "people": {"chairs": [{"name": "Ada Example", "affiliation": "MIFP"}], "participants": []},
        "program": [{"time": "09:00", "title": "Opening"}],
        "documents": [{"label": "Programme", "path": "assets/documents/programme.pdf", "kind": "pdf", "source_url": "https://old.mifp.eu/programme.pdf"}],
        "images": [{"caption": "Event logo", "alt": "AO1 logo", "path": "assets/images/logo.png", "source_url": "https://old.mifp.eu/logo.png"}],
        "sources": ["https://www.mifp.eu/archive-one.html"],
        "recovery": {"confidence": "high", "notes": ["Recovered from a static backup."]},
        "not_recovered": ["Participant affiliations were not recovered."],
    }


def _package(path: Path, event=None, *, unsafe=False) -> Path:
    event = event or _event()
    base = f"archive/conferences/2014/{event['slug']}"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{base}/event.json", json.dumps(event))
        zf.writestr(f"{base}/assets/documents/programme.pdf", b"%PDF-1.4\narchive")
        zf.writestr(f"{base}/assets/images/logo.png", b"\x89PNG\r\n\x1a\narchive")
        zf.writestr(f"{base}/index.html", "ignored")
        zf.writestr("assets/archive.css", "ignored")
        if unsafe:
            zf.writestr("../escape.txt", "bad")
    return path


def test_archive_package_import_is_canonical_local_and_idempotent(tmp_path: Path):
    from mifp_app.services.historical_archive import import_historical_archive

    conn = _conn(); package = _package(tmp_path / "archive.zip"); assets = tmp_path / "assets"
    preview = import_historical_archive(conn, package, assets, dry_run=True)
    assert preview["actions"] == {"create": 1}
    first = import_historical_archive(conn, package, assets)
    second = import_historical_archive(conn, package, assets)
    assert first["events"] == second["events"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_archive_entries").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 3
    event = conn.execute("SELECT * FROM events").fetchone()
    assert event["remote_url"] is None
    assert event["is_featured"] == 0
    assert conn.execute("SELECT role FROM entity_links").fetchone()[0] == "source"
    assert all(path.is_file() for path in assets.rglob("*") if not path.is_dir())


def test_archive_matches_uid_then_slug_and_reports_identity_conflict(tmp_path: Path):
    from mifp_app.services.historical_archive import inspect_historical_archive

    conn = _conn()
    conn.execute("INSERT INTO events(uid,slug,title) VALUES('event_archive_one','uid-match','Existing UID')")
    assert inspect_historical_archive(_package(tmp_path / "uid.zip"), conn)["actions"] == {"fill missing fields": 1}
    conn.execute("INSERT INTO events(uid,slug,title) VALUES('other','archive-one','Existing slug')")
    result = inspect_historical_archive(_package(tmp_path / "conflict.zip"), conn)
    assert result["actions"] == {"conflict": 1}


def test_archive_rejects_wrong_schema_and_unsafe_member(tmp_path: Path):
    from mifp_app.services.historical_archive import HistoricalArchiveError, inspect_historical_archive

    conn = _conn()
    with pytest.raises(HistoricalArchiveError, match="Unsupported archive schema"):
        inspect_historical_archive(_package(tmp_path / "wrong.zip", _event(schema="unknown")), conn)
    with pytest.raises(HistoricalArchiveError, match="Unsafe ZIP member"):
        inspect_historical_archive(_package(tmp_path / "unsafe.zip", unsafe=True), conn)


def test_archive_repository_filter_destination_and_detail(tmp_path: Path):
    from mifp_app.services.historical_archive import import_historical_archive
    from mifp_app.services.public_repository import event_public_destination, get_archive_entry, list_archive_entries, list_public_events

    conn = _conn(); import_historical_archive(conn, _package(tmp_path / "archive.zip"), tmp_path / "assets")
    result = list_archive_entries(conn, lambda value: f"/media/{value}", search="Archive", category="conferences", year="2014", series="archive-series")
    assert len(result["entries"]) == 1 and result["stats"]["people"] == 1
    detail = get_archive_entry(conn, "conferences", 2014, "archive-one", lambda value: f"/media/{value}")
    assert detail and detail["topics"] == ["Quantum physics"] and detail["gallery"]
    _, past = list_public_events(conn, lambda value: f"/media/{value}")
    assert event_public_destination(past[0]) == ("public.archive_detail", {"category": "conferences", "year": 2014, "slug": "archive-one"})


@pytest.mark.parametrize("scope", ["events", "all"])
def test_portable_roundtrip_preserves_archive_and_files(tmp_path: Path, scope: str):
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload
    from mifp_app.services.historical_archive import import_historical_archive

    source = _conn(); source_assets = tmp_path / "source-assets"
    import_historical_archive(source, _package(tmp_path / "archive.zip"), source_assets)
    payload = bundle_to_zip(source, scope, source_assets)
    target = _conn(); target_assets = tmp_path / "target-assets"
    import_zip_payload(target, payload, scope, target_assets)
    assert target.execute("SELECT COUNT(*) FROM event_archive_entries").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2
    for row in target.execute("SELECT path FROM assets"):
        assert (target_assets / str(row["path"]).removeprefix("assets/")).is_file()


def test_old_portable_event_without_archive_metadata_remains_valid(tmp_path: Path):
    from mifp_app.services.data_portability import bundle_to_zip, import_zip_payload

    source = _conn(); source.execute("INSERT INTO events(uid,slug,title) VALUES('ordinary','ordinary','Ordinary')")
    target = _conn()
    import_zip_payload(target, bundle_to_zip(source, "events", tmp_path / "a"), "events", tmp_path / "b")
    assert target.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert target.execute("SELECT COUNT(*) FROM event_archive_entries").fetchone()[0] == 0


def test_v10_archive_migration_converges_to_fresh_fingerprint(tmp_path: Path):
    from mifp_app.db.migrations import migrate_content_schema
    from mifp_app.db.schema_fingerprint import canonical_schema_fingerprint, schema_fingerprint

    conn = _conn()
    conn.execute("DROP TABLE event_archive_entries")
    conn.execute("DELETE FROM schema_migrations")
    conn.execute("INSERT INTO schema_migrations(version,name) VALUES(10,'schema v10')")
    result = migrate_content_schema(conn)
    assert result["migrations_applied"] == [11]
    assert schema_fingerprint(conn) == canonical_schema_fingerprint()


def test_public_routes_redirect_event_and_dashboard_import_requires_auth(tmp_path: Path):
    from mifp_app import create_app
    from mifp_app.db.manage import init_database
    from mifp_app.services.historical_archive import import_historical_archive
    from werkzeug.security import generate_password_hash

    db_path = tmp_path / "web.db"; assets = tmp_path / "assets"
    os.environ["TESTING"] = "1"
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, DATABASE_PATH=db_path,
                      ASSETS_DIR=assets, EXPORT_DIR=tmp_path / "exports", LOG_DIR=tmp_path / "logs",
                      ADMIN_PASSWORD_HASH=generate_password_hash("secret123"))
    for key in ("ASSETS_DIR", "EXPORT_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    init_database(db_path)
    package = _package(tmp_path / "archive.zip")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        import_historical_archive(conn, package, assets)
    anonymous = app.test_client()
    assert anonymous.get("/dashboard/archive").status_code == 302
    index = anonymous.get("/archive/")
    assert index.status_code == 200 and "Archive One" in index.get_data(as_text=True)
    detail = anonymous.get("/archive/conferences/2014/archive-one/")
    assert detail.status_code == 200 and "Quantum physics" in detail.get_data(as_text=True)
    redirect_response = anonymous.get("/events/archive-one")
    assert redirect_response.status_code == 308
    assert redirect_response.headers["Location"].endswith("/archive/conferences/2014/archive-one/")
    with anonymous.session_transaction() as sess:
        sess["admin_logged_in"] = True; sess["admin_username"] = "admin"
    dashboard = anonymous.get("/dashboard/archive")
    dashboard_html = dashboard.get_data(as_text=True)
    assert dashboard.status_code == 200 and "Historical archive" in dashboard_html
    assert 'id="archiveTransferModal"' in dashboard_html
    assert 'id="archiveAuthModal"' in dashboard_html
    assert "js/dashboard/archive-import.js" in dashboard_html
    denied = anonymous.post("/dashboard/archive/import", data={
        "dry_run": "1", "archive_zip": (io.BytesIO(package.read_bytes()), "archive.zip")
    }, content_type="multipart/form-data", headers={"X-Requested-With": "XMLHttpRequest"})
    assert denied.status_code == 403
    assert "No archive file was processed" in denied.get_json()["message"]
    streamed = anonymous.post("/dashboard/archive/import", data={
        "dry_run": "1", "password": "secret123",
        "archive_zip": (io.BytesIO(package.read_bytes()), "archive.zip")
    }, content_type="multipart/form-data", headers={"X-Requested-With": "XMLHttpRequest"})
    stream_events = [json.loads(line) for line in streamed.get_data(as_text=True).splitlines()]
    assert streamed.status_code == 200
    assert streamed.mimetype == "application/x-ndjson"
    assert stream_events[0]["event"] == "queued"
    assert stream_events[-1]["event"] == "result"
    assert stream_events[-1]["ok"] is True
    assert stream_events[-1]["result"]["dry_run"] is True
    dry_run = anonymous.post("/dashboard/archive/import", data={
        "dry_run": "1", "password": "secret123",
        "archive_zip": (io.BytesIO(package.read_bytes()), "archive.zip")
    }, content_type="multipart/form-data")
    assert dry_run.status_code == 302
    actual = anonymous.post("/dashboard/archive/import", data={
        "dry_run": "0", "password": "secret123",
        "archive_zip": (io.BytesIO(package.read_bytes()), "archive.zip")
    }, content_type="multipart/form-data")
    assert actual.status_code == 302
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM import_runs WHERE source_kind='historical-archive'").fetchone()[0] == 2

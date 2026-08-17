from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "CONFERENCES_DIR": str(tmp_path / "conferences"),
            "LOG_DIR": str(tmp_path / "logs"),
            "SECRET_KEY": "dashboard-action-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
        ALLOW_DB_DUMP=True,
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    with connect(app.config["DATABASE_PATH"]) as conn:
        migrate_content_schema(conn)
    yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "dashboard-action-csrf"
    return client


def _db(app) -> sqlite3.Connection:
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(app, sql: str, params: tuple = ()):
    with _db(app) as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None


def test_conference_wizard_people_exports_assets_and_deploy_zip(app, client):
    created = client.post("/dashboard/conferences", data={
        "title": "Quantum Mediterranean 2027",
        "acronym": "QM27",
        "year": "2027",
        "slug": "quantum-mediterranean-2027",
    })
    assert created.status_code == 302
    site_id = _scalar(app, "SELECT id FROM conference_sites WHERE slug=?", ("quantum-mediterranean-2027",))
    assert site_id
    listing = client.get("/dashboard/conferences")
    listing_body = listing.get_data(as_text=True)
    assert listing.status_code == 200
    assert 'class="toolbar"' in listing_body
    assert 'class="table-card compact-table-card"' in listing_body
    assert 'class="data-table"' in listing_body
    assert 'name="q"' in listing_body
    assert f'id="editConference{site_id}"' in listing_body
    assert f'href="/dashboard/conferences/{site_id}"' in listing_body
    assert "Delete conference and storage" in listing_body
    filtered = client.get("/dashboard/conferences?q=Quantum")
    assert "Quantum Mediterranean 2027" in filtered.get_data(as_text=True)
    assert "Reset" in filtered.get_data(as_text=True)
    assert "Quantum Mediterranean 2027" not in client.get(
        "/dashboard/conferences?q=missing-conference"
    ).get_data(as_text=True)

    saved = client.post(f"/dashboard/conferences/{site_id}", data={
        "title": "Quantum Mediterranean 2027",
        "acronym": "QM27",
        "year": "2027",
        "status": "ready",
        "start_date": "2027-05-10",
        "end_date": "2027-05-12",
        "venue": "Physics Centre",
        "city": "Rome",
        "country": "Italy",
        "canonical_url": "https://events.example.org/qm27/",
        "deploy_base_path": "/qm27/",
        "registration_url": "https://register.example.org/qm27",
        "contact_email": "team@example.org",
        "description": "A focused international meeting.",
    })
    assert saved.status_code == 302
    configured = client.post(f"/dashboard/conferences/{site_id}/config", data={
        "config__deployment__environment": "nginx",
        "config__deployment__localhost_base_path": "./preview/",
        "config__deployment__nginx_base_path": "/qm27/",
        "config__runtime__debug": "1",
        "config__runtime__console_log_level": "warn",
        "config__appearance__default_mode": "light",
        "config__appearance__default_palette": "2",
        "config__appearance__remember_theme": "1",
        "config__privacy__show_notice": "1",
        "config__privacy__notice_storage_key": "qm27-privacy",
        "config__registration__enabled": "1",
        "config__registration__section_anchor": "registration",
        "config__registration__nav_label": "Register",
        "config__registration__topbar_label": "Join QM27",
        "config__registration__button_label": "Register for QM27",
        "config__registration__open_in_new_tab": "1",
        "config__registration__plan_button_label": "Choose pass",
        "config__registration__participant_url": "https://register.example.org/qm27",
        "config__registration__student_url": "https://register.example.org/qm27/student",
        "config__registration__accompanying_url": "",
        "config__countdown__enabled": "1",
        "config__countdown__show_in_sidebar": "1",
        "config__countdown__show_on_home": "1",
        "config__countdown__update_interval_seconds": "30",
        "countdown_label": "Abstract deadline",
        "countdown_date": "2027-02-01T23:59:00+01:00",
        "countdown_end_date": "",
        "countdown_type": "deadline",
    })
    assert configured.status_code == 302

    people_csv = (
        "name,email,affiliation,country,role,contribution_title,bio,website_url,sort_order\n"
        "Ada Scientist,ada@example.org,MIFP,Italy,speaker,Quantum networks,,https://example.org/ada,1\n"
    ).encode()
    imported = client.post(
        f"/dashboard/conferences/{site_id}/people/import",
        data={"people_file": (io.BytesIO(people_csv), "people.csv", "text/csv")},
        content_type="multipart/form-data",
    )
    assert imported.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM conference_people WHERE conference_id=?", (site_id,)) == 1
    person_id = _scalar(app, "SELECT id FROM conference_people WHERE conference_id=?", (site_id,))

    asset = client.post(
        f"/dashboard/conferences/{site_id}/assets",
        data={
            "assets": [
                (io.BytesIO(b"%PDF-1.4\n"), "programme.pdf", "application/pdf"),
                (
                    io.BytesIO(b"day,time,title\nMonday,09:00,Opening\n"),
                    "program.csv",
                    "text/csv",
                ),
                (io.BytesIO(b"\x89PNG\r\n\x1a\nlogo"), "logo.png", "image/png"),
                (io.BytesIO(b"\x89PNG\r\n\x1a\nperson"), "ada.png", "image/png"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert asset.status_code == 302
    assignments = (
        ("programme.pdf", "document", "Full programme", ""),
        ("program.csv", "program_source", "Programme data", ""),
        ("logo.png", "hero_logo", "QM27 logo", ""),
        ("ada.png", "speaker_photo", "Ada Scientist", str(person_id)),
    )
    for filename, role, label, linked_person in assignments:
        assigned = client.post(
            f"/dashboard/conferences/{site_id}/assets/{filename}/metadata",
            data={"role": role, "label": label, "person_id": linked_person},
        )
        assert assigned.status_code == 302

    for fmt, signature in (("xlsx", b"PK"), ("pdf", b"%PDF"), ("json", b"{")):
        exported = client.get(f"/dashboard/conferences/{site_id}/people/export.{fmt}")
        assert exported.status_code == 200
        assert exported.data.startswith(signature)
        assert "no-store" in exported.headers["Cache-Control"]
    assert client.get(f"/dashboard/conferences/{site_id}/people/export.csv").status_code == 400

    package = client.get(f"/dashboard/conferences/{site_id}/build.zip")
    assert package.status_code == 200
    assert package.data.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        names = set(archive.namelist())
        assert {"index.html", "people.html", "program.html", "venue.html", "people.json", "program.json", "conference.json", "config.yaml", "manifest.json", "assets/site.css", "assets/programme.pdf", "assets/logo.png", "assets/ada.png"} <= names
        assert not any(name.lower().endswith(".csv") for name in names)
        homepage = archive.read("index.html").decode()
        assert 'href="/qm27/people.html"' in homepage
        assert "https://events.example.org/qm27/" in homepage
        assert "assets/logo.png" in homepage
        assert "Abstract deadline" in homepage
        assert "Full programme" in homepage
        assert "assets/ada.png" in archive.read("people.html").decode()
        assert "Opening" in archive.read("program.html").decode()
        yaml_config = archive.read("config.yaml").decode()
        assert 'console_log_level: "warn"' in yaml_config
        assert 'default_mode: "light"' in yaml_config
        assert 'participant_url: "https://register.example.org/qm27"' in yaml_config
        assert 'label: "Abstract deadline"' in yaml_config

    preview = client.get(f"/dashboard/conferences/{site_id}/assets/programme.pdf")
    assert preview.status_code == 200
    assert "no-store" in preview.headers["Cache-Control"]
    deleted = client.post(f"/dashboard/conferences/{site_id}/assets/programme.pdf/delete")
    assert deleted.status_code == 302
    assert client.get(f"/dashboard/conferences/{site_id}/assets/programme.pdf").status_code == 404


def test_conference_delete_removes_database_relations_and_storage(app, client):
    created = client.post("/dashboard/conferences", data={
        "title": "Disposable Conference",
        "slug": "disposable-conference",
    })
    assert created.status_code == 302
    site_id = _scalar(
        app,
        "SELECT id FROM conference_sites WHERE slug=?",
        ("disposable-conference",),
    )
    asset_dir = Path(app.config["CONFERENCES_DIR"]) / "disposable-conference" / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with _db(app) as conn:
        person_id = conn.execute(
            "INSERT INTO conference_people(conference_id,name) VALUES(?,?)",
            (site_id, "Temporary Person"),
        ).lastrowid
        conn.execute(
            """INSERT INTO conference_assets(
                   conference_id,filename,role,person_id
               ) VALUES(?,?,?,?)""",
            (site_id, "logo.png", "speaker_photo", person_id),
        )
        conn.commit()

    rejected = client.post(
        f"/dashboard/conferences/{site_id}/delete",
        data={"confirm_title": "wrong"},
    )
    assert rejected.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM conference_sites WHERE id=?", (site_id,)) == 1
    assert asset_dir.exists()

    deleted = client.post(
        f"/dashboard/conferences/{site_id}/delete",
        data={"confirm_title": "Disposable Conference"},
    )
    assert deleted.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM conference_sites WHERE id=?", (site_id,)) == 0
    assert _scalar(app, "SELECT COUNT(*) FROM conference_people WHERE conference_id=?", (site_id,)) == 0
    assert _scalar(app, "SELECT COUNT(*) FROM conference_assets WHERE conference_id=?", (site_id,)) == 0
    assert not asset_dir.parent.exists()
    empty_page = client.get("/dashboard/conferences").get_data(as_text=True)
    assert 'class="data-table"' in empty_page
    assert 'class="empty">No records. Use "New" above.' in empty_page
    assert "No conference sites" not in empty_page


def test_conference_yaml_and_zip_import_are_safe_and_complete(app, client):
    client.post("/dashboard/conferences", data={
        "title": "Imported Conference",
        "slug": "imported-conference",
    })
    site_id = _scalar(
        app, "SELECT id FROM conference_sites WHERE slug='imported-conference'"
    )
    yaml_data = b"""
deployment:
  environment: nginx
  nginx_base_path: /imported/
appearance:
  default_mode: light
registration:
  enabled: true
  participant_url: https://register.example.org/imported
"""
    yaml_import = client.post(
        f"/dashboard/conferences/{site_id}/import",
        data={"config_file": (io.BytesIO(yaml_data), "config.yaml", "application/yaml")},
        content_type="multipart/form-data",
    )
    assert yaml_import.status_code == 302
    with _db(app) as conn:
        site = conn.execute(
            "SELECT config_json,deploy_base_path FROM conference_sites WHERE id=?",
            (site_id,),
        ).fetchone()
    assert json.loads(site["config_json"])["appearance"]["default_mode"] == "light"
    assert site["deploy_base_path"] == "/imported"

    package_buffer = io.BytesIO()
    with zipfile.ZipFile(package_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("config.yaml", yaml_data)
        archive.writestr("assets/logo.png", b"\x89PNG\r\n\x1a\n")
        archive.writestr("index.html", b"ignored")
    package_import = client.post(
        f"/dashboard/conferences/{site_id}/import",
        data={"package_file": (
            io.BytesIO(package_buffer.getvalue()),
            "conference.zip",
            "application/zip",
        )},
        content_type="multipart/form-data",
    )
    assert package_import.status_code == 302
    asset_path = (
        Path(app.config["CONFERENCES_DIR"])
        / "imported-conference"
        / "assets"
        / "logo.png"
    )
    assert asset_path.is_file()
    assert _scalar(
        app,
        "SELECT COUNT(*) FROM conference_assets WHERE conference_id=? AND filename='logo.png'",
        (site_id,),
    ) == 1
    assert not (asset_path.parent.parent / "index.html").exists()

    unsafe_buffer = io.BytesIO()
    with zipfile.ZipFile(unsafe_buffer, "w") as archive:
        archive.writestr("config.yaml", yaml_data)
        archive.writestr("../escape.png", b"bad")
    unsafe_import = client.post(
        f"/dashboard/conferences/{site_id}/import",
        data={"package_file": (
            io.BytesIO(unsafe_buffer.getvalue()),
            "unsafe.zip",
            "application/zip",
        )},
        content_type="multipart/form-data",
    )
    assert unsafe_import.status_code == 302
    assert not (Path(app.config["CONFERENCES_DIR"]).parent / "escape.png").exists()


def test_data_portability_page_links_import_to_duplicate_cleanup(client):
    response = client.get("/dashboard/data-portability")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Validate only" in body
    assert "Complete backup" in body
    assert "Recommended" in body
    assert "Portable JSONL" in body
    assert "Content Archive" not in body
    assert "legacy" not in body.lower()
    assert "Analyze imported data" in body
    assert "/dashboard/data-quality" in body
    assert "merge_modal" not in body


def test_data_portability_page_explains_canonical_import_and_shows_import_history(app, client):
    with _db(app) as conn:
        conn.execute(
            """
            INSERT INTO import_runs(name, source_kind, source_path, status, stats_json, completed_at)
            VALUES('first-batch.jsonl', 'jsonl-v2', '/tmp/first-batch.jsonl', 'completed', ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps({"records": 3, "inserted": {"news": 2}, "updated": {"event": 1}, "errors": []}),),
        )
        conn.commit()

    response = client.get("/dashboard/data-portability")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Import canonical data" in body
    assert "records.jsonl" in body
    assert "canonical JSONL v2" in body
    assert "Recent imports" in body
    assert "first-batch.jsonl" in body
    assert "<b>2</b> added" in body
    assert "<b>1</b> updated" in body


def test_separate_import_requests_accumulate_records(app, client):
    def import_record(record, filename):
        response = client.post(
            "/dashboard/data-portability/import",
            data={
                "password": "secret123",
                "data_file": (
                    io.BytesIO((json.dumps(record) + "\n").encode()),
                    filename,
                    "application/x-ndjson",
                ),
            },
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.data.decode().splitlines() if line.strip()]
        result = next(event for event in events if event.get("event") == "result")
        assert result["ok"] is True

    import_record(
        {"type": "news", "data": {"title": "First batch", "slug": "first-batch"}, "links": [], "assets": []},
        "first.jsonl",
    )
    import_record(
        {"type": "news", "data": {"title": "Second batch", "slug": "second-batch"}, "links": [], "assets": []},
        "second.jsonl",
    )

    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug IN ('first-batch', 'second-batch')") == 2
    assert _scalar(app, "SELECT COUNT(*) FROM import_runs WHERE name IN ('first.jsonl', 'second.jsonl')") == 2


def test_mixed_zip_and_jsonl_queue_is_processed_in_one_request(app, client):
    from mifp_app.services.data_portability import bundle_to_zip

    with _db(app) as conn:
        zip_payload = bundle_to_zip(conn, "all", Path(app.config["ASSETS_DIR"]))
    record = {
        "type": "news",
        "data": {"title": "Mixed queue record", "slug": "mixed-queue-record"},
        "links": [],
        "assets": [],
    }

    response = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "data_file": [
                (io.BytesIO(zip_payload), "portable.zip", "application/zip"),
                (io.BytesIO((json.dumps(record) + "\n").encode()), "additional.jsonl", "application/x-ndjson"),
            ],
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.data.decode().splitlines() if line.strip()]
    result = next(event for event in events if event.get("event") == "result")
    assert result["ok"] is True
    assert result["inserted"] == 1
    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug='mixed-queue-record'") == 1
    assert _scalar(app, "SELECT COUNT(*) FROM import_runs WHERE name IN ('portable.zip', 'additional.jsonl')") == 2


def test_force_cookie_banner_publishes_new_global_revision(app, client, monkeypatch, tmp_path):
    config_path = tmp_path / "banner_settings.json"
    config_path.write_text(
        json.dumps({"cookie_banner_enabled": "0", "banner_force_show": "old-revision"}),
        encoding="utf-8",
    )
    app.config["BANNER_SETTINGS_PATH"] = config_path

    response = client.post(
        "/dashboard/institutional/privacy/banner/force",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Cookie banner forced" in response.data
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["cookie_banner_enabled"] == "1"
    assert saved["banner_force_show"] != "old-revision"
    assert saved["banner_force_show"].isdigit()


def test_privacy_workspace_has_force_banner_action(client):
    response = client.get("/dashboard/institutional/privacy")

    assert response.status_code == 200
    assert b"Force show to everyone" in response.data
    assert b"/dashboard/institutional/privacy/banner/force" in response.data
    assert b"banner-preview-icon" in response.data
    assert b"data-banner-dismiss" in response.data
    assert b"Appearance and behaviour" in response.data


def test_cookie_workspace_endpoint_redirects_to_consolidated_editor(client):
    response = client.get("/dashboard/institutional/cookie")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard/institutional/privacy?tab=cookie")


def test_homepage_tolerates_active_sponsor_without_slug(app, client):
    with _db(app) as conn:
        conn.execute(
            "INSERT INTO sponsors(name, slug, is_active) VALUES(?, NULL, 1)",
            ("Sponsor Without Slug",),
        )
        conn.commit()

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Sponsor Without Slug" in body
    assert 'href="#"' in body
    assert "sponsor-modal-" in body


def test_settings_save_uses_allowlist_and_commits_site_copy_atomically(client, app):
    response = client.post(
        "/dashboard/settings",
        data={
            "hero_lead": "Updated public introduction",
            "SECRET_KEY": "must-not-be-stored",
            "unknown_setting": "must-not-be-stored",
        },
    )

    assert response.status_code == 302
    with _db(app) as conn:
        rows = dict(conn.execute("SELECT key,value FROM settings").fetchall())
    assert rows["hero_lead"] == "Updated public introduction"
    assert "SECRET_KEY" not in rows
    assert "unknown_setting" not in rows


@pytest.mark.parametrize(
    ("section", "table", "payload", "updated_value"),
    [
        ("members", "members", {"display_name": "Test Member", "slug": "test-member"}, "Updated Member"),
        ("news", "news", {"title": "Test News", "slug": "test-news"}, "Updated News"),
        ("events", "events", {"title": "Test Event", "slug": "test-event"}, "Updated Event"),
        ("publications", "publications", {"title": "Test Publication", "slug": "test-publication"}, "Updated Publication"),
        ("research", "research_areas", {"title": "Test Research", "slug": "test-research"}, "Updated Research"),
        ("sponsors", "sponsors", {"name": "Test Sponsor", "slug": "test-sponsor"}, "Updated Sponsor"),
    ],
)
def test_content_create_update_delete_actions(app, client, section, table, payload, updated_value):
    create = client.post(f"/dashboard/content/{section}", data=payload)
    assert create.status_code == 302
    with _db(app) as conn:
        row = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        record_id = row["id"]

    update_payload = dict(payload)
    update_payload["id"] = str(record_id)
    update_payload["display_name" if table == "members" else "name" if table == "sponsors" else "title"] = updated_value
    update = client.post(f"/dashboard/content/{section}", data=update_payload)
    assert update.status_code == 302
    label_col = "display_name" if table == "members" else "name" if table == "sponsors" else "title"
    assert _scalar(app, f"SELECT {label_col} FROM {table} WHERE id=?", (record_id,)) == updated_value

    delete = client.post(f"/dashboard/content/{section}/{record_id}/delete")
    assert delete.status_code == 302
    assert _scalar(app, f"SELECT COUNT(*) FROM {table} WHERE id=?", (record_id,)) == 0


def test_content_save_waits_for_a_transient_database_writer(app, monkeypatch):
    from mifp_app.db.connection import connect
    from mifp_app.services.dashboard_repository import save_record

    monkeypatch.setenv("SQLITE_WRITE_LOCK_TIMEOUT_SECONDS", "2")
    writer = sqlite3.connect(app.config["DATABASE_PATH"], timeout=1, check_same_thread=False)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")

    release = threading.Thread(target=lambda: (time.sleep(0.2), writer.rollback(), writer.close()))
    release.start()
    try:
        with connect(app.config["DATABASE_PATH"]) as conn:
            record_id = save_record(
                conn,
                "events",
                {"title": "Concurrent event", "slug": "concurrent-event"},
            )
    finally:
        release.join(timeout=2)

    assert not release.is_alive()
    assert _scalar(app, "SELECT COUNT(*) FROM events WHERE id=?", (record_id,)) == 1


def test_event_wizard_advertises_pdf_doc_and_docx_uploads(client):
    response = client.get("/dashboard/events")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Upload a PDF, DOC or DOCX file." in body
    javascript = (
        Path(__file__).resolve().parents[2]
        / "MIFPAPP/CORE/mifp_app/static/js/dashboard/events.js"
    ).read_text(encoding="utf-8")
    assert 'accept=".pdf,.doc,.docx,' in javascript
    assert "['pdf', 'doc', 'docx'].includes(extension)" in javascript
    assert "event.asset.upload_started" in javascript
    assert "event.create.submit" in javascript


def test_event_wizard_saves_uploaded_document_with_database_safe_role(app, client):
    with _db(app) as conn:
        document_id = conn.execute(
            """
            INSERT INTO assets(filename,original_filename,path,kind,mime_type)
            VALUES('program.docx','program.docx','document/program.docx','document',
                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            """
        ).lastrowid
        conn.commit()

    response = client.post(
        "/dashboard/events",
        data={
            "title": "Document wizard event",
            "slug": "document-wizard-event",
            "review_status": "draft",
            "manage_event_assets": "1",
            "doc_asset_id": str(document_id),
            "doc_type": "Program",
            "doc_label": "Event programme",
        },
    )

    assert response.status_code == 302
    with _db(app) as conn:
        event_id = conn.execute(
            "SELECT id FROM events WHERE slug='document-wizard-event'"
        ).fetchone()["id"]
        link = conn.execute(
            """
            SELECT role,is_primary FROM asset_links
            WHERE asset_id=? AND entity_type='event' AND entity_id=?
            """,
            (document_id, event_id),
        ).fetchone()
    assert dict(link) == {"role": "document", "is_primary": 0}


def test_event_wizard_invalid_document_rolls_back_new_event(app, client):
    response = client.post(
        "/dashboard/events",
        data={
            "title": "Invalid document event",
            "slug": "invalid-document-event",
            "manage_event_assets": "1",
            "doc_asset_id": "999999",
            "doc_type": "Program",
        },
    )

    assert response.status_code == 302
    assert _scalar(
        app,
        "SELECT COUNT(*) FROM events WHERE slug='invalid-document-event'",
    ) == 0


@pytest.mark.parametrize(
    ("section", "table"),
    [
        ("members", "members"),
        ("publications", "publications"),
        ("research", "research_areas"),
        ("sponsors", "sponsors"),
    ],
)
def test_new_content_rejects_missing_required_label(app, client, section, table):
    response = client.post(f"/dashboard/content/{section}", data={"id": ""})

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/dashboard/content/{section}?new=1"
    )
    assert _scalar(app, f"SELECT COUNT(*) FROM {table}") == 0


def test_new_member_can_derive_display_name_from_names(app, client):
    response = client.post(
        "/dashboard/content/members",
        data={"id": "", "first_name": "Ada", "last_name": "Lovelace"},
    )

    assert response.status_code == 302
    assert _scalar(
        app,
        "SELECT display_name FROM members ORDER BY id DESC LIMIT 1",
    ) == "Ada Lovelace"
    assert _scalar(
        app,
        "SELECT slug FROM members ORDER BY id DESC LIMIT 1",
    ) == "ada-lovelace"


def test_new_active_sponsor_is_created_atomically_with_logo(app, client):
    response = client.post(
        "/dashboard/content/sponsors",
        data={
            "name": "Complete Sponsor",
            "description": "Programme sponsor",
            "tier": "gold",
            "is_active": "1",
            "primary_asset": (
                io.BytesIO(b"\x89PNG\r\n\x1a\nwizard"),
                "complete-sponsor.png",
                "image/png",
            ),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    with _db(app) as conn:
        sponsor = conn.execute(
            "SELECT id,slug,is_active FROM sponsors WHERE name='Complete Sponsor'"
        ).fetchone()
        link = conn.execute(
            """
            SELECT role,is_primary FROM asset_links
            WHERE entity_type='sponsor' AND entity_id=?
            """,
            (sponsor["id"],),
        ).fetchone()
    assert dict(sponsor) == {
        "id": sponsor["id"],
        "slug": "complete-sponsor",
        "is_active": 1,
    }
    assert dict(link) == {"role": "logo", "is_primary": 1}


def test_generic_new_content_forms_emit_safe_console_events():
    javascript = (
        Path(__file__).resolve().parents[2]
        / "MIFPAPP/CORE/mifp_app/static/js/dashboard/content.js"
    ).read_text(encoding="utf-8")

    assert "content.create.open" in javascript
    assert "content.create.invalid" in javascript
    assert "content.create.submit" in javascript


@pytest.mark.parametrize("asset_role", ["cover", "logo"])
def test_inline_event_edit_preserves_existing_cover_and_saves_changes(app, client, asset_role):
    with _db(app) as conn:
        event_id = conn.execute(
            """
            INSERT INTO events(
                title,slug,start_date,description,location,review_status,is_featured
            ) VALUES(
                'Asset event','asset-event','2030-06-10',
                'Complete event description','Rome','published',0
            )
            """
        ).lastrowid
        asset_id = conn.execute(
            """
            INSERT INTO assets(filename,original_filename,path,kind,mime_type)
            VALUES('cover.png','cover.png','image/cover.png','image','image/png')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary)
            VALUES(?,'event',?,?,1)
            """,
            (asset_id, event_id, asset_role),
        )
        conn.commit()

    response = client.post(
        "/dashboard/events",
        data={
            "id": str(event_id),
            "title": "Asset event updated",
            "slug": "asset-event",
            "start_date": "2030-06-10",
            "description": "Complete event description",
            "location": "Rome",
            "review_status": "published",
            "is_featured": "1",
        },
    )

    assert response.status_code == 302
    with _db(app) as conn:
        event = conn.execute(
            "SELECT title,is_featured FROM events WHERE id=?", (event_id,)
        ).fetchone()
        link_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM asset_links
            WHERE asset_id=? AND entity_type='event' AND entity_id=?
            """,
            (asset_id, event_id),
        ).fetchone()["c"]
    assert dict(event) == {"title": "Asset event updated", "is_featured": 1}
    assert link_count == 1


def test_setting_existing_event_cover_promotes_asset_and_preserves_previous(app, client):
    with _db(app) as conn:
        event_id = conn.execute(
            "INSERT INTO events(title,slug,review_status) VALUES('Cover event','cover-event','published')"
        ).lastrowid
        first = conn.execute(
            "INSERT INTO assets(filename,path,kind) VALUES('first.png','image/first.png','image')"
        ).lastrowid
        second = conn.execute(
            "INSERT INTO assets(filename,path,kind) VALUES('second.png','image/second.png','image')"
        ).lastrowid
        conn.execute(
            "INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary) VALUES(?,'event',?,'cover',1)",
            (first, event_id),
        )
        conn.execute(
            "INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary) VALUES(?,'event',?,'gallery',0)",
            (second, event_id),
        )
        conn.commit()

    response = client.post(
        f"/dashboard/content/events/{event_id}/assets/link",
        data={"asset_id": str(second), "role": "cover"},
    )

    assert response.status_code == 200
    with _db(app) as conn:
        roles = {
            row["asset_id"]: (row["role"], row["is_primary"])
            for row in conn.execute(
                "SELECT asset_id,role,is_primary FROM asset_links WHERE entity_type='event' AND entity_id=?",
                (event_id,),
            )
        }
    assert roles[first] == ("gallery", 0)
    assert roles[second] == ("cover", 1)


def test_site_pages_dashboard_section_is_removed(client):
    assert client.get("/dashboard/pages").status_code == 404
    assert client.post("/dashboard/pages").status_code == 404
    assert client.post("/dashboard/pages/save-layout").status_code == 404
    assert client.post("/dashboard/pages/1/delete").status_code == 404

    dashboard = client.get("/dashboard/")
    assert dashboard.status_code == 200
    assert b'href="/dashboard/pages"' not in dashboard.data


def test_settings_vacuum_integrity_and_database_dump_actions(app, client):
    saved = client.post(
        "/dashboard/settings",
        data={
            "privacy_contact_email": "privacy@example.org",
            "cookie_banner_text": "obsolete and ignored",
        },
    )
    assert saved.status_code == 302
    assert _scalar(app, "SELECT value FROM settings WHERE key='privacy_contact_email'") == "privacy@example.org"
    assert _scalar(app, "SELECT value FROM settings WHERE key='cookie_banner_text'") is None

    integrity = client.post("/dashboard/server/integrity-check", follow_redirects=True)
    assert integrity.status_code == 200
    assert b"Integrity check passed" in integrity.data

    vacuum = client.post("/dashboard/server/vacuum", follow_redirects=True)
    assert vacuum.status_code == 200
    assert b"requires the password-protected operations wizard" in vacuum.data
    assert _scalar(app, "SELECT value FROM settings WHERE key='last_vacuum'") is None

    dump = client.post("/dashboard/server/db-dump", data={"password": "secret123"})
    assert dump.status_code == 200
    assert dump.mimetype in {"application/vnd.sqlite3", "application/octet-stream"}
    assert dump.data.startswith(b"SQLite format 3")


def _insert_join_request(app, suffix: str) -> int:
    with _db(app) as conn:
        cursor = conn.execute(
            """
            INSERT INTO join_requests(first_name,last_name,email,affiliation,country,field,motivation)
            VALUES(?,?,?,?,?,?,?)
            """,
            ("Ada", suffix, f"ada-{suffix.lower()}@example.org", "MIFP", "Italy", "Physics", "Testing"),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_join_request_update_approve_reject_archive_delete_actions(app, client):
    update_id = _insert_join_request(app, "Update")
    response = client.post(
        f"/dashboard/join-requests/{update_id}/update",
        data={"status": "in_review", "admin_notes": "Reviewing", "decision_note": "Pending"},
    )
    assert response.status_code == 302
    assert _scalar(app, "SELECT status FROM join_requests WHERE id=?", (update_id,)) == "in_review"

    approve_id = _insert_join_request(app, "Approve")
    response = client.post(f"/dashboard/join-requests/{approve_id}/approve", data={"create_member": "1"})
    assert response.status_code == 302
    with _db(app) as conn:
        approved = conn.execute("SELECT status, member_id FROM join_requests WHERE id=?", (approve_id,)).fetchone()
        assert approved["status"] == "approved"
        assert approved["member_id"] is not None
        assert conn.execute("SELECT COUNT(*) FROM members WHERE id=?", (approved["member_id"],)).fetchone()[0] == 1

    reject_id = _insert_join_request(app, "Reject")
    response = client.post(
        f"/dashboard/join-requests/{reject_id}/reject",
        data={"decision_note": "Not in scope"},
    )
    assert response.status_code == 302
    assert _scalar(app, "SELECT status FROM join_requests WHERE id=?", (reject_id,)) == "rejected"

    archive_id = _insert_join_request(app, "Archive")
    response = client.post(f"/dashboard/join-requests/{archive_id}/archive")
    assert response.status_code == 302
    assert _scalar(app, "SELECT status FROM join_requests WHERE id=?", (archive_id,)) == "archived"

    delete_id = _insert_join_request(app, "Delete")
    response = client.post(f"/dashboard/join-requests/{delete_id}/delete")
    assert response.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM join_requests WHERE id=?", (delete_id,)) == 0


def test_asset_upload_update_search_download_export_import_and_delete_actions(app, client):
    uploaded = client.post(
        "/dashboard/assets",
        data={
            "action": "upload",
            "kind": "image",
            "alt_text": "Initial alt",
            "file": (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "action.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 302
    asset_id = _scalar(app, "SELECT id FROM assets WHERE original_filename='action.png'")
    path = _scalar(app, "SELECT path FROM assets WHERE id=?", (asset_id,))
    assert asset_id and path

    updated = client.post(
        "/dashboard/assets",
        data={"action": "update", "id": asset_id, "kind": "image", "alt_text": "Updated alt", "caption": "Caption"},
    )
    assert updated.status_code == 302
    assert _scalar(app, "SELECT alt_text FROM assets WHERE id=?", (asset_id,)) == "Updated alt"

    search = client.get("/dashboard/assets/search.json?q=action&kind=image")
    assert search.status_code == 200
    assert any(row["id"] == asset_id for row in search.get_json())

    filename = str(path).split("/", 1)[1] if "/" in str(path) else str(path)
    downloaded = client.get(f"/dashboard/assets/{filename}")
    assert downloaded.status_code == 200
    assert downloaded.data.startswith(b"\x89PNG")

    exported = client.post("/dashboard/assets", data={"action": "export_all"})
    assert exported.status_code == 302
    export_name = next(Path(app.config["EXPORT_DIR"]).glob("*.zip")).name
    export_download = client.get(f"/dashboard/assets/exports/{export_name}")
    assert export_download.status_code == 200
    assert export_download.data.startswith(b"PK")
    assert export_download.headers["X-MIFP-Export-Retention"] == "delete-after-download"

    dry_zip = client.post(
        "/dashboard/assets",
        data={
            "action": "import_zip",
            "dry_run": "1",
            "zip_file": (io.BytesIO(export_download.data), "assets.zip", "application/zip"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert dry_zip.status_code == 200
    assert b"Dry-run" in dry_zip.data

    jsonl = json.dumps(
        {
            "filename": "metadata.pdf",
            "path": "pdf/metadata.pdf",
            "kind": "pdf",
            "checksum": "dashboard-action-metadata",
            "storage_status": "missing",
        }
    )
    imported_jsonl = client.post(
        "/dashboard/assets",
        data={
            "action": "import_jsonl",
            "jsonl_file": (io.BytesIO((jsonl + "\n").encode()), "assets.jsonl", "application/x-ndjson"),
        },
        content_type="multipart/form-data",
    )
    export_download.close()
    assert not (Path(app.config["EXPORT_DIR"]) / export_name).exists()
    assert imported_jsonl.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM assets WHERE checksum='dashboard-action-metadata'") == 1

    deleted = client.post("/dashboard/assets", data={"action": "delete", "id": asset_id})
    assert deleted.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM assets WHERE id=?", (asset_id,)) == 0


def test_asset_external_filtered_unused_and_zip_restore_actions(app, client):
    external = client.post(
        "/dashboard/assets",
        data={
            "action": "external",
            "source_url": "https://example.org/action-document.pdf",
            "kind": "pdf",
            "caption": "Remote action document",
        },
    )
    assert external.status_code == 302
    external_id = _scalar(app, "SELECT id FROM assets WHERE source_url='https://example.org/action-document.pdf'")
    assert external_id

    local = client.post(
        "/dashboard/assets",
        data={
            "action": "upload",
            "kind": "image",
            "file": (io.BytesIO(b"\x89PNG\r\n\x1a\nrestore"), "restore.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    assert local.status_code == 302
    local_id = _scalar(app, "SELECT id FROM assets WHERE original_filename='restore.png'")

    filtered = client.post(
        "/dashboard/assets",
        data={"action": "export_filtered", "kind_filter": "image", "status_filter": "local"},
    )
    unused = client.post("/dashboard/assets", data={"action": "export_unused"})
    assert filtered.status_code == 302
    assert unused.status_code == 302
    exports = list(Path(app.config["EXPORT_DIR"]).glob("*.zip"))
    assert len(exports) >= 2

    restore_zip = next(path for path in exports if "filtered" in path.name or "full" in path.name)
    with _db(app) as conn:
        conn.execute("DELETE FROM assets WHERE id=?", (local_id,))
        conn.commit()
    restored = client.post(
        "/dashboard/assets",
        data={
            "action": "import_zip",
            "zip_file": (io.BytesIO(restore_zip.read_bytes()), "restore.zip", "application/zip"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert restored.status_code == 200
    assert b"Imported" in restored.data
    assert _scalar(app, "SELECT COUNT(*) FROM assets WHERE original_filename='restore.png'") == 1


def test_asset_picker_create_and_content_link_upload_unlink_actions(app, client):
    with _db(app) as conn:
        record_id = conn.execute("INSERT INTO news(title,slug) VALUES('Linked News','linked-news')").lastrowid
        conn.commit()

    created = client.post(
        "/dashboard/assets/create.json",
        data={"file": (io.BytesIO(b"%PDF-1.4\n"), "picker.pdf", "application/pdf"), "kind": "pdf"},
        content_type="multipart/form-data",
    )
    assert created.status_code == 200
    picker_asset_id = created.get_json()["id"]

    external_created = client.post(
        "/dashboard/assets/create.json",
        data={"source_url": "https://example.org/picker-action.pdf", "kind": "pdf"},
    )
    assert external_created.status_code == 200
    assert external_created.get_json()["id"]

    linked = client.post(
        f"/dashboard/content/news/{record_id}/assets/link",
        data={"asset_id": picker_asset_id, "role": "document"},
    )
    assert linked.status_code == 200
    assert linked.get_json()["success"] is True

    unlinked = client.post(
        f"/dashboard/content/news/{record_id}/assets/unlink",
        data={"asset_id": picker_asset_id},
    )
    assert unlinked.status_code == 200
    assert _scalar(
        app,
        "SELECT COUNT(*) FROM asset_links WHERE asset_id=? AND entity_type='news' AND entity_id=?",
        (picker_asset_id, record_id),
    ) == 0
    repeated_unlink = client.post(
        f"/dashboard/content/news/{record_id}/assets/unlink",
        data={"asset_id": picker_asset_id},
    )
    assert repeated_unlink.status_code == 200
    assert repeated_unlink.get_json()["already_unlinked"] is True

    uploaded = client.post(
        f"/dashboard/content/news/{record_id}/assets/upload",
        data={"file": (io.BytesIO(b"%PDF-1.4\n"), "attached.pdf", "application/pdf"), "role": "document"},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    uploaded_id = uploaded.get_json()["asset_id"]
    assert _scalar(
        app,
        "SELECT COUNT(*) FROM asset_links WHERE asset_id=? AND entity_type='news' AND entity_id=?",
        (uploaded_id, record_id),
    ) == 1

    external_link = client.post(
        f"/dashboard/content/news/{record_id}/assets/link",
        data={"source_url": "https://example.org/direct-link.pdf", "kind": "pdf", "role": "document"},
    )
    assert external_link.status_code == 200
    assert external_link.get_json()["success"] is True

    force_delete_blocked = client.post(
        "/dashboard/assets",
        data={"action": "delete", "id": uploaded_id, "force": "1"},
    )
    assert force_delete_blocked.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM assets WHERE id=?", (uploaded_id,)) == 1


def test_asset_picker_unexpected_error_is_redacted(app, client, monkeypatch):
    from mifp_app.routes import dashboard_assets

    monkeypatch.setattr(
        dashboard_assets,
        "store_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("SQL failure at /srv/private/mifp.db")
        ),
    )
    response = client.post(
        "/dashboard/assets/create.json",
        data={"file": (io.BytesIO(b"%PDF-1.4\n"), "failure.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["error"] == "The asset could not be created."
    assert payload["request_id"]
    assert "private" not in response.get_data(as_text=True)
    assert "SQL" not in response.get_data(as_text=True)


def test_asset_import_temp_file_is_removed_after_validation_error(app, client, monkeypatch):
    from mifp_app.routes import dashboard_assets

    captured: list[Path] = []

    def reject_import(_conn, _assets_dir, path, *, dry_run=False):
        captured.append(Path(path))
        raise ValueError("invalid archive")

    monkeypatch.setattr(dashboard_assets, "import_assets_from_zip", reject_import)
    response = client.post(
        "/dashboard/assets",
        data={
            "action": "import_zip",
            "zip_file": (io.BytesIO(b"not-a-zip"), "broken.zip", "application/zip"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Asset input rejected" in response.data
    assert len(captured) == 1
    assert not captured[0].exists()


def test_unused_asset_cleanup_action(app, client):
    with _db(app) as conn:
        asset_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,checksum) VALUES('unused.txt','other/unused.txt','other','unused-action')"
        ).lastrowid
        conn.commit()
    response = client.post(
        "/dashboard/database-assets/cleanup",
        data={"apply": "1", "asset_ids": str(asset_id)},
    )
    assert response.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM assets WHERE id=?", (asset_id,)) == 0
    assert list((Path(app.config["DATABASE_PATH"]).parent / "backups").glob("*asset-cleanup*.db"))
    assert list(Path(app.config["EXPORT_DIR"]).glob("*unused.zip"))


def test_assets_page_missing_filter_and_banner(app, client):
    with _db(app) as conn:
        conn.execute(
            """
            INSERT INTO assets(filename,path,kind,storage_status,source_url)
            VALUES('recoverable.jpg','image/recoverable.jpg','image','missing','https://example.test/recoverable.jpg')
            """
        )
        conn.commit()

    response = client.get("/dashboard/assets?status=missing")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "recoverable.jpg" in body
    assert "files are absent locally" in body
    assert "Asset health shortcuts" not in body
    assert "Missing locally" not in body
    assert 'option value="missing" selected' in body


def test_data_portability_jsonl_zip_merge_and_export_actions(app, client):
    record = {
        "type": "news",
        "data": {"title": "Imported Action News", "slug": "imported-action-news", "review_status": "published"},
        "links": [],
        "assets": [],
        "meta": {},
    }
    dry_run = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "dry_run": "1",
            "data_file": (io.BytesIO((json.dumps(record) + "\n").encode()), "news.jsonl", "application/x-ndjson"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert dry_run.status_code == 200
    lines = [json.loads(l) for l in dry_run.data.decode("utf-8").strip().splitlines() if l.strip()]
    result = next(e for e in lines if e.get("event") == "result")
    assert result.get("dry_run") is True
    assert result.get("inserted") == 1
    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug='imported-action-news'") == 0

    imported = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "data_file": (io.BytesIO((json.dumps(record) + "\n").encode()), "news.jsonl", "application/x-ndjson"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert imported.status_code == 200
    lines = [json.loads(l) for l in imported.data.decode("utf-8").strip().splitlines() if l.strip()]
    result = next(e for e in lines if e.get("event") == "result")
    assert result.get("ok") is True
    assert result.get("inserted") == 1
    assert result.get("backup_created") is True
    assert "backup_path" not in result
    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug='imported-action-news'") == 1

    for fmt in ("jsonl", "zip"):
        resp = client.post(
            f"/dashboard/data-portability/export/{fmt}",
            data={"_csrf_token": "x", "password": "secret123"},
        )
        assert resp.status_code == 200
        lines = [json.loads(l) for l in resp.data.decode("utf-8").strip().splitlines() if l.strip()]
        result = next(e for e in lines if e.get("event") == "result")
        assert result.get("ok") is True
        assert "no-store" in resp.headers.get("Cache-Control", "")
        token = result.get("download_token")
        assert token
        dl = client.get(f"/dashboard/data-portability/export-dl/{token}")
        assert dl.status_code == 200
        assert "no-store" in dl.headers.get("Cache-Control", "")
        assert dl.headers.get("X-Content-Type-Options") == "nosniff"
        assert dl.headers.get("Referrer-Policy") == "no-referrer"
        assert dl.headers.get("Cross-Origin-Resource-Policy") == "same-origin"
        if fmt != "jsonl":
            assert dl.data
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(dl.data)) as archive:
                assert {"manifest.json", "records.jsonl"} <= set(archive.namelist())

    zip_resp2 = client.post(
        "/dashboard/data-portability/export/zip",
        data={"_csrf_token": "x", "password": "secret123"},
    )
    zip_lines = [json.loads(l) for l in zip_resp2.data.decode("utf-8").strip().splitlines() if l.strip()]
    zip_result = next(e for e in zip_lines if e.get("event") == "result")
    zip_token = zip_result.get("download_token")
    zip_dl = client.get(f"/dashboard/data-portability/export-dl/{zip_token}")
    zip_dry_run = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "dry_run": "1",
            "data_file": (io.BytesIO(zip_dl.data), "news.zip", "application/zip"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert zip_dry_run.status_code == 200
    lines = [json.loads(l) for l in zip_dry_run.data.decode("utf-8").strip().splitlines() if l.strip()]
    zip_result = next(e for e in lines if e.get("event") == "result")
    assert zip_result.get("dry_run") is True


def _legacy_data_portability_staging_review_apply_and_reports(app, client):
    record = {
        "type": "news",
        "data": {"title": "Staged Review News", "slug": "staged-review-news", "review_status": "published"},
        "links": [],
        "assets": [],
        "meta": {"source_url": "https://example.test/staged-review-news"},
    }

    dry_run = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "dry_run": "1",
            "data_file": (io.BytesIO((json.dumps(record) + "\n").encode()), "stage-news.jsonl", "application/x-ndjson"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert dry_run.status_code == 200
    dry_run_json = dry_run.get_json()
    assert dry_run_json["dry_run"] is True
    assert dry_run_json["staged"] == 1
    assert dry_run_json["review_url"] == "/dashboard/data-portability/review"
    assert _scalar(app, "SELECT COUNT(*) FROM import_review_records") == 0
    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug='staged-review-news'") == 0

    staged = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "data_file": (io.BytesIO((json.dumps(record) + "\n").encode()), "stage-news.jsonl", "application/x-ndjson"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert staged.status_code == 200
    staged_json = staged.get_json()
    assert staged_json["ok"] is True
    assert staged_json["staged"] == 1
    assert staged_json["in_review"] == 0
    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug='staged-review-news'") == 0

    row_id = _scalar(app, "SELECT id FROM import_review_records WHERE canonical_id='news:staged-review-news'")
    assert row_id
    review_page = client.get("/dashboard/data-portability/review")
    assert review_page.status_code == 200
    assert b"Staged Review News" in review_page.data
    assert b"Import Review" in review_page.data

    report_json = client.get("/dashboard/data-portability/review/report.json")
    assert report_json.status_code == 200
    assert report_json.mimetype == "application/json"
    assert "staged-review-news" in report_json.get_data(as_text=True)
    report_csv = client.get("/dashboard/data-portability/review/report.csv")
    assert report_csv.status_code == 200
    assert report_csv.mimetype == "text/csv"

    approved = client.post(f"/dashboard/data-portability/review/{row_id}/action", data={"action": "approve"})
    assert approved.status_code == 302
    applied = client.post("/dashboard/data-portability/review/apply-approved")
    assert applied.status_code == 302
    assert _scalar(app, "SELECT COUNT(*) FROM news WHERE slug='staged-review-news'") == 1
    assert _scalar(app, "SELECT status FROM import_review_records WHERE id=?", (row_id,)) == "applied"


def _legacy_data_portability_staging_duplicate_ambiguous_and_invalid_records(app, client):
    with _db(app) as conn:
        conn.execute("INSERT INTO news(title,slug,review_status) VALUES('Existing Duplicate','existing-duplicate','published')")
        conn.commit()

    duplicate = {
        "type": "news",
        "data": {"title": "Existing Duplicate Updated", "slug": "existing-duplicate", "review_status": "published"},
        "links": [],
        "assets": [],
        "meta": {},
    }
    ambiguous = {
        "type": "sponsor",
        "data": {"name": "Ambiguous Sponsor", "slug": "ambiguous-sponsor"},
        "links": [],
        "assets": [],
        "meta": {},
    }
    invalid = {
        "type": "news",
        "data": {"title": "Invalid Unknown", "slug": "invalid-unknown", "surprise_field": "boom"},
        "links": [],
        "assets": [],
        "meta": {},
    }
    body = "\n".join(json.dumps(item) for item in [duplicate, ambiguous, ambiguous, invalid]) + "\n"
    response = client.post(
        "/dashboard/data-portability/import",
        data={
            "password": "secret123",
            "data_file": (io.BytesIO(body.encode()), "mixed-stage.jsonl", "application/x-ndjson"),
        },
        content_type="multipart/form-data",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["staged"] == 3
    assert payload["possible_duplicates"] == 1
    assert payload["errors"] == 1
    assert payload["error_details"][0]["kind"] == "unknown_field"

    assert _scalar(app, "SELECT COUNT(*) FROM import_review_records WHERE status='duplicate'") == 1
    assert _scalar(app, "SELECT COUNT(*) FROM import_review_records WHERE status='ambiguous'") == 1
    assert _scalar(app, "SELECT COUNT(*) FROM import_review_records WHERE status='error'") == 1
    assert _scalar(app, "SELECT COUNT(*) FROM sponsors WHERE slug='ambiguous-sponsor'") == 0

    duplicate_id = _scalar(app, "SELECT id FROM import_review_records WHERE status='duplicate'")
    rejected_approve = client.post(f"/dashboard/data-portability/review/{duplicate_id}/action", data={"action": "approve"})
    assert rejected_approve.status_code == 302
    assert _scalar(app, "SELECT status FROM import_review_records WHERE id=?", (duplicate_id,)) == "duplicate"

    target_id = _scalar(app, "SELECT id FROM news WHERE slug='existing-duplicate'")
    merged = client.post(
        f"/dashboard/data-portability/review/{duplicate_id}/action",
        data={"action": "merge", "target_id": str(target_id)},
    )
    assert merged.status_code == 302
    assert _scalar(app, "SELECT status FROM import_review_records WHERE id=?", (duplicate_id,)) == "merged"
    assert _scalar(app, "SELECT title FROM news WHERE id=?", (target_id,)) == "Existing Duplicate Updated"


@pytest.mark.parametrize("fmt", ["jsonl", "zip"])
def test_every_data_portability_export_button(app, client, fmt):
    resp = client.post(
        f"/dashboard/data-portability/export/{fmt}",
        data={"_csrf_token": "x", "password": "secret123"},
    )
    assert resp.status_code == 200
    lines = [json.loads(l) for l in resp.data.decode("utf-8").strip().splitlines() if l.strip()]
    result = next(e for e in lines if e.get("event") == "result")
    assert result.get("ok") is True
    assert result.get("download_token")
    assert result.get("filename", "").endswith(f".{fmt}")
    dl = client.get(f"/dashboard/data-portability/export-dl/{result['download_token']}")
    assert dl.status_code == 200
    if fmt != "jsonl":
        assert dl.data


@pytest.mark.parametrize("fmt", ["json", "jsonl", "csv", "xlsx", "docx", "pdf"])
def test_content_export_formats(app, client, fmt):
    with _db(app) as conn:
        conn.execute("INSERT OR IGNORE INTO events(title,slug) VALUES('Export Action Event','export-action-event')")
        conn.commit()
    response = client.get(f"/dashboard/export/events.{fmt}")
    assert response.status_code == 200
    assert response.data
    assert "attachment" in response.headers.get("Content-Disposition", "")


@pytest.mark.parametrize("fmt", ["json", "csv", "xlsx", "docx", "pdf"])
def test_stats_export_formats(client, fmt):
    stats_response = client.get(f"/dashboard/export/stats.{fmt}")
    assert stats_response.status_code == 200
    assert stats_response.data


def test_log_export_and_cleanup_actions(app, client):
    log_dir = Path(app.config["LOG_DIR"])
    current = log_dir / "mifp_app.log"
    current.write_text("2026-01-01 00:00:00 | ERROR | test | action error\n", encoding="utf-8")
    old = log_dir / "old.log.1"
    old.write_text("old\n", encoding="utf-8")
    old_time = time.time() - 90 * 86400
    os.utime(old, (old_time, old_time))

    for fmt in ("json", "csv", "txt"):
        response = client.get(f"/dashboard/logs/export/{fmt}?show_all=1")
        assert response.status_code == 200
        assert "attachment" in response.headers.get("Content-Disposition", "")

    cleanup = client.post("/dashboard/logs/cleanup", data={"days": "30"})
    assert cleanup.status_code == 302
    assert not old.exists()
    assert current.exists()


MUTATING_DASHBOARD_ENDPOINTS = {
    "dashboard.assets_page",
    "dashboard.asset_create_json",
    "dashboard.cleanup_unused_assets",
    "dashboard.content",
    "dashboard.content_asset_link",
    "dashboard.content_asset_unlink",
    "dashboard.content_asset_upload",
    "dashboard.content_delete",
    "dashboard.content_external_link_add",
    "dashboard.content_external_link_delete",
    "dashboard.conference_create",
    "dashboard.conference_delete",
    "dashboard.conference_import",
    "dashboard.conference_edit",
    "dashboard.conference_config_save",
    "dashboard.conference_person_save",
    "dashboard.conference_person_delete",
    "dashboard.conference_people_import",
    "dashboard.conference_asset_upload",
    "dashboard.conference_asset_metadata",
    "dashboard.conference_asset_delete",
    "dashboard.data_portability_export_post",
    "dashboard.events",
    "dashboard.data_quality_analyze",
    "dashboard.data_quality_bulk_decision",
    "dashboard.data_quality_decision",
    "dashboard.data_quality_bundle_create",
    "dashboard.data_quality_bundle_add",
    "dashboard.data_quality_bundle_remove",
    "dashboard.data_quality_bundle_apply",
    "dashboard.data_quality_bundle_delete",
    "dashboard.data_quality_quarantine_action",
    "dashboard.data_portability_import",
    "dashboard.data_portability_import_cancel",
    "dashboard.join_approve",
    "dashboard.join_archive",
    "dashboard.join_delete",
    "dashboard.join_reject",
    "dashboard.join_update",
    "dashboard.logs_cleanup",
    "dashboard.server_db_dump",
    "dashboard.server_db_restore",
    "dashboard.server_integrity_check",
    "dashboard.server_vacuum",
    "dashboard.settings_save",
    "dashboard.assets_retry_external",
    "dashboard.institutional",
    "dashboard.institutional_cookie",
    "dashboard.institutional_privacy",
    "dashboard.institutional_privacy_force_banner",
    "dashboard.control_site_maintenance",
    "dashboard.control_site_force_clear_maintenance",
    "dashboard.control_backups_cleanup",
    "dashboard.control_safety_operations_run",
}


def test_mutating_dashboard_endpoint_inventory_is_complete(app):
    actual = {
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("dashboard.")
        and set(rule.methods or ()) & {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert actual == MUTATING_DASHBOARD_ENDPOINTS


def test_data_quality_page_exposes_distinct_actions_and_bundle(client, app):
    with _db(app) as conn:
        conn.execute("""INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
                        VALUES('completed','test-fingerprint',CURRENT_TIMESTAMP,'{}')""")
        conn.execute("""INSERT INTO quality_findings(run_id,action_type,entity_type,record_ids_json,classification,score,status,fingerprint)
                        VALUES(1,'clean_record','member','[1]','needs_cleaning',1.0,'open','test-fp')""")
        conn.commit()

    response = client.get("/dashboard/data-quality")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Scan" in body
    assert "A scan alone never changes record counts" in body
    assert "Review &amp; Apply" in body
    assert "Automatic fixes" in body
    assert "Needs decision" in body
    assert "Informational" in body
    assert "Clean record" in body
    assert "Split aggregated" in body
    assert "Apply changes" in body
    assert "Merge candidates" in body
    assert "Queue all automatic fixes" in body
    # Manual work is a first-class workflow, not hidden behind a generic filter.
    assert "Review decisions" in body
    assert "Accept all" not in body
    assert "Approve all" not in body


def test_data_quality_page_manages_quarantined_records(client, app):
    with _db(app) as conn:
        news_id = conn.execute(
            """INSERT INTO news(title,slug,review_status)
               VALUES('XHR News','xhr-news-quarantine','quarantined')"""
        ).lastrowid
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','quarantine-ui',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,classification,score,
                   evidence_json,plan_json,status,fingerprint
               ) VALUES(?,'clean_record','news',?,'invalid_record',1.0,?,?,'resolved','quarantine-ui-finding')""",
            (
                run_id,
                json.dumps([news_id]),
                json.dumps([{"explanation": "Technical response or explicit test record"}]),
                json.dumps({"operation": "quarantine", "previous_review_status": "published"}),
            ),
        )
        conn.commit()

    response = client.get("/dashboard/data-quality")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Quarantine <span>1</span>" in body
    assert "XHR News" in body
    assert "Technical response or explicit test record" in body
    assert "Restore as draft" in body
    assert f"/dashboard/content/news?edit={news_id}" in body

    restored = client.post(
        f"/dashboard/data-quality/quarantine/news/{news_id}",
        data={"decision": "restore"},
    )
    assert restored.status_code == 302
    assert restored.headers["Location"].endswith("/dashboard/data-quality#dqQuarantine")
    assert _scalar(app, "SELECT review_status FROM news WHERE id=?", (news_id,)) == "draft"


def test_data_quality_bulk_accept_server_filters_out_manual_findings(client, app, monkeypatch):
    from mifp_app.routes import dashboard_data_quality

    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','bulk-auto-only',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        auto_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,classification,score,status,fingerprint,plan_json
               ) VALUES(?,'clean_record','member','[1]','needs_cleaning',1.0,'open','bulk-auto','{}')""",
            (run_id,),
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,classification,score,status,fingerprint,plan_json
               ) VALUES(?,'split_aggregated_record','event','[2]','aggregated_record',0.9,'open','bulk-manual',
                        '{"action_type":"split_aggregated_record","proposed_records":[{"title_hint":"A"},{"title_hint":"B"}]}')""",
            (run_id,),
        )
        conn.commit()

    accepted: list[int] = []

    def fake_add_to_bundle(conn, bundle_id, finding_id, payload):
        accepted.append(int(finding_id))
        conn.execute("UPDATE quality_findings SET status='bundled' WHERE id=?", (finding_id,))

    monkeypatch.setattr(dashboard_data_quality, "add_to_bundle", fake_add_to_bundle)
    response = client.post(
        "/dashboard/data-quality/bulk-decision",
        json={"decision": "accept", "run_id": run_id, "all_run": True},
    )

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["matched"] == 1
    assert result["applied"] == 1
    assert result["skipped_review"] == 0
    assert accepted == [auto_id]


def test_data_quality_bulk_accept_does_not_create_empty_bundle_for_manual_only_run(client, app):
    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','bulk-manual-only',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,classification,score,status,fingerprint,plan_json
               ) VALUES(?,'split_aggregated_record','event','[2]','aggregated_record',0.9,'open','bulk-manual-only-finding',
                        '{"action_type":"split_aggregated_record","proposed_records":[{"title_hint":"A"},{"title_hint":"B"}]}')""",
            (run_id,),
        )
        conn.commit()

    response = client.post(
        "/dashboard/data-quality/bulk-decision",
        json={"decision": "accept", "run_id": run_id, "all_run": True},
    )

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["matched"] == 0
    assert result["applied"] == 0
    assert result["bundle_id"] is None
    assert _scalar(app, "SELECT COUNT(*) FROM quality_bundles") == 0


def test_data_quality_accept_all_includes_findings_beyond_first_page(
    client, app, monkeypatch
):
    from mifp_app.routes import dashboard_data_quality

    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','bulk-test',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        conn.executemany(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'clean_record','member','[1]','needs_cleaning',1.0,'open',?,'{}')""",
            [(run_id, f"finding-{index}") for index in range(501)],
        )
        conn.commit()

    accepted: list[int] = []

    def fake_add_to_bundle(conn, bundle_id, finding_id, payload):
        accepted.append(int(finding_id))
        conn.execute(
            "UPDATE quality_findings SET status='bundled' WHERE id=?",
            (finding_id,),
        )

    monkeypatch.setattr(dashboard_data_quality, "add_to_bundle", fake_add_to_bundle)
    response = client.post(
        "/dashboard/data-quality/bulk-decision",
        json={"decision": "accept", "run_id": run_id, "all_run": True},
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["applied"] == 501
    assert len(accepted) == 501


def test_data_quality_manual_review_acceptance_is_terminal(client, app):
    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','manual-review',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        finding_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'merge_records','event','[1]','page_fragment_attached',
                        0.5,'open','manual-finding',
                        '{"action_type":"merge_records","record_ids":[1],"records":[]}')""",
            (run_id,),
        ).lastrowid
        conn.commit()

    response = client.post(
        f"/dashboard/data-quality/findings/{finding_id}/decision",
        json={"decision": "accept"},
    )

    assert response.status_code == 200
    assert response.get_json()["reviewed_without_change"] is True
    assert _scalar(
        app, "SELECT status FROM quality_findings WHERE id=?", (finding_id,)
    ) == "resolved"
    assert _scalar(app, "SELECT COUNT(*) FROM quality_bundles") == 0


def test_data_quality_manual_without_editable_plan_can_be_marked_reviewed(client, app):
    with _db(app) as conn:
        asset_id = conn.execute(
            "INSERT INTO assets(filename,path,storage_status) VALUES('missing.pdf','missing.pdf','missing')"
        ).lastrowid
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','manual-review-generic',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        finding_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'repair_relations_or_assets','asset',?,'blocked',
                        1.0,'open','manual-review-generic-finding',?)""",
            (run_id, json.dumps([asset_id]), json.dumps({
                "action_type": "repair_relations_or_assets",
                "entity_type": "asset",
                "record_ids": [asset_id],
                "operation": "recover_or_relink_missing_asset",
                "requires_review": True,
            })),
        ).lastrowid
        conn.commit()

    listing = client.get(
        f"/dashboard/data-quality/findings?run_id={run_id}&classification=manual"
    )
    assert listing.status_code == 200
    assert "Mark reviewed" in listing.get_json()["items_html"]
    assert "Review & queue" not in listing.get_json()["items_html"]

    response = client.post(
        f"/dashboard/data-quality/findings/{finding_id}/decision",
        json={"decision": "accept"},
    )
    assert response.status_code == 200
    assert response.get_json()["reviewed_without_change"] is True
    assert _scalar(app, "SELECT status FROM quality_findings WHERE id=?", (finding_id,)) == "resolved"


def test_data_quality_actionable_manual_still_requires_reviewed_plan(client, app):
    with _db(app) as conn:
        conn.executemany(
            "INSERT INTO events(id,slug,title) VALUES(?,?,?)",
            [(101, "manual-a", "Manual A"), (102, "manual-b", "Manual B")],
        )
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','manual-actionable',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        finding_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'merge_records','event','[101,102]','ambiguous',
                        0.5,'open','manual-actionable-finding',?)""",
            (run_id, json.dumps({
                "action_type": "merge_records",
                "entity_type": "event",
                "record_ids": [101, 102],
                "canonical_id": 101,
            })),
        ).lastrowid
        conn.commit()

    response = client.post(
        f"/dashboard/data-quality/findings/{finding_id}/decision",
        json={"decision": "accept"},
    )
    assert response.status_code == 409
    detail = client.get(f"/dashboard/data-quality/findings/{finding_id}").get_json()["detail_html"]
    assert "Queue reviewed fix" in detail


def test_data_quality_review_list_only_contains_open_findings(app):
    from mifp_app.services.data_quality.analyzer import count_findings, list_findings

    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','state-filter',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        conn.executemany(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'clean_record','member','[1]','needs_cleaning',
                        1.0,?,?,'{}')""",
            [
                (run_id, "open", "state-open"),
                (run_id, "bundled", "state-bundled"),
                (run_id, "deferred", "state-deferred"),
                (run_id, "resolved", "state-resolved"),
            ],
        )
        conn.commit()

        findings = list_findings(conn, run_id)
        total = count_findings(conn, run_id)

    assert [finding["status"] for finding in findings] == ["open"]
    assert total == 1


def test_data_quality_state_keeps_filters_and_queue_independent(client, app):
    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','workspace-state',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        open_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'clean_record','member','[1]','needs_cleaning',
                        1.0,'open','workspace-open','{}')""",
            (run_id,),
        ).lastrowid
        bundled_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'clean_record','member','[2]','needs_cleaning',
                        1.0,'bundled','workspace-bundled','{}')""",
            (run_id,),
        ).lastrowid
        bundle_id = conn.execute(
            "INSERT INTO quality_bundles(status,created_by) VALUES('draft','admin')"
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_bundle_items(
                   bundle_id,finding_id,action_type,payload_json,status
               ) VALUES(?,?,'clean_record','{}','pending')""",
            (bundle_id, bundled_id),
        )
        conn.commit()

    state = client.get("/dashboard/data-quality/state").get_json()
    filtered = client.get(
        f"/dashboard/data-quality/findings?run_id={run_id}&entity_type=event"
    ).get_json()

    assert state["open_total"] == 1
    assert state["queue_count"] == 1
    assert state["bundle_id"] == bundle_id
    assert "dqFilteredCount" not in state["queue_html"]
    assert filtered["total"] == 0
    # An empty filter result must not mutate or hide the independent queue.
    assert _scalar(
        app,
        "SELECT COUNT(*) FROM quality_bundle_items WHERE bundle_id=?",
        (bundle_id,),
    ) == 1
    assert _scalar(
        app, "SELECT status FROM quality_findings WHERE id=?", (open_id,)
    ) == "open"


def test_data_quality_manual_editor_can_choose_only_a_finding_record(client, app):
    with _db(app) as conn:
        conn.executemany(
            "INSERT INTO events(id,slug,title) VALUES(?,?,?)",
            [(1, "first", "First event"), (2, "second", "Second event")],
        )
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','manual-editor',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        plan = {
            "action_type": "merge_records",
            "entity_type": "event",
            "record_ids": [1, 2],
            "records": [
                {"id": 1, "title": "First event"},
                {"id": 2, "title": "Second event"},
            ],
            "canonical_id": 1,
        }
        finding_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'merge_records','event','[1,2]','ambiguous',
                        0.5,'open','manual-editor-finding',?)""",
            (run_id, json.dumps(plan)),
        ).lastrowid
        bundle_id = conn.execute(
            "INSERT INTO quality_bundles(status,created_by) VALUES('draft','admin')"
        ).lastrowid
        conn.commit()

    detail = client.get(f"/dashboard/data-quality/findings/{finding_id}")
    assert detail.status_code == 200
    assert "Queue reviewed fix" in detail.get_json()["detail_html"]

    accepted = client.post(
        f"/dashboard/data-quality/bundles/{bundle_id}/items",
        json={"finding_id": finding_id, "plan": {"canonical_id": 2}},
    )
    assert accepted.status_code == 200
    with _db(app) as conn:
        payload = json.loads(conn.execute(
            """SELECT payload_json FROM quality_bundle_items
               WHERE bundle_id=? AND finding_id=?""",
            (bundle_id, finding_id),
        ).fetchone()["payload_json"])
    assert payload["plan"]["canonical_id"] == 2
    assert _scalar(
        app, "SELECT status FROM quality_findings WHERE id=?", (finding_id,)
    ) == "bundled"

    with _db(app) as conn:
        conn.execute(
            "UPDATE quality_findings SET status='open' WHERE id=?", (finding_id,)
        )
        conn.execute(
            "DELETE FROM quality_bundle_items WHERE finding_id=?", (finding_id,)
        )
        conn.commit()
    rejected = client.post(
        f"/dashboard/data-quality/bundles/{bundle_id}/items",
        json={"finding_id": finding_id, "plan": {"canonical_id": 999}},
    )
    assert rejected.status_code == 409
    assert "must belong to this finding" in rejected.get_json()["message"]
    assert _scalar(
        app, "SELECT status FROM quality_findings WHERE id=?", (finding_id,)
    ) == "open"


def test_data_quality_manual_clean_review_shows_original_and_similar_records(client, app):
    with _db(app) as conn:
        source_id = conn.execute(
            """
            INSERT INTO events(title,slug,date_precision,review_status)
            VALUES('International School on Nanophotonics','school-undated','unknown','review')
            """
        ).lastrowid
        similar_id = conn.execute(
            """
            INSERT INTO events(title,slug,start_date,end_date,date_text,review_status)
            VALUES('International School on Nanophotonics 2024','school-2024',
                   '2024-09-10','2024-09-15','10–15 September 2024','published')
            """
        ).lastrowid
        run_id = conn.execute(
            """
            INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
            VALUES('completed','manual-clean',CURRENT_TIMESTAMP,'{}')
            """
        ).lastrowid
        plan = {
            "action_type": "clean_record",
            "entity_type": "event",
            "record_ids": [source_id],
            "fields": [{
                "field": "start_date",
                "proposed_value": None,
                "action": "fill_missing",
                "requires_review": True,
                "reason": "start_date is empty.",
            }],
        }
        evidence = [{
            "code": "missing_event_date",
            "strength": "critical",
            "explanation": "Event has no start or end date",
            "values": ["start_date", "end_date"],
        }]
        finding_id = conn.execute(
            """
            INSERT INTO quality_findings(
                run_id,action_type,entity_type,record_ids_json,
                classification,score,status,fingerprint,plan_json,evidence_json
            ) VALUES(?,'clean_record','event',?,'needs_cleaning',0.95,'open',
                     'manual-clean-finding',?,?)
            """,
            (
                run_id,
                json.dumps([source_id]),
                json.dumps(plan),
                json.dumps(evidence),
            ),
        ).lastrowid
        conn.commit()

    response = client.get(f"/dashboard/data-quality/findings/{finding_id}")

    assert response.status_code == 200
    payload = response.get_json()
    html = payload["detail_html"]
    assert "International School on Nanophotonics" in html
    assert "Current database value" in html
    assert "Possible comparisons" in html
    assert "10–15 September 2024" in html
    assert "95% confidence" in html
    assert payload["finding"]["source_records"][0]["id"] == source_id
    assert payload["finding"]["similar_records"][0]["id"] == similar_id


def test_data_quality_frontend_does_not_auto_accept_after_scan():
    script = (
        Path(__file__).resolve().parents[2]
        / "MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js"
    ).read_text(encoding="utf-8")

    scan_section = script[script.index("async function analyze()"):script.index("async function applyAll()")]
    assert "bulkDecisionUrl" not in scan_section
    assert "Scanning is read-only" in scan_section
    assert "AbortController" in script
    template = (Path(__file__).resolve().parents[2] / "MIFPAPP/CORE/mifp_app/templates/dashboard/data_quality.html").read_text(encoding="utf-8")
    assert "v=static_version" in template
    assert "20260729-07" not in template


def test_control_content_quality_links_current_data_quality_workflow(client, app):
    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','control-quality',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        finding_ids = []
        for index, status in enumerate(("open", "bundled", "resolved"), start=1):
            finding_ids.append(conn.execute(
                """INSERT INTO quality_findings(
                       run_id,action_type,entity_type,record_ids_json,
                       classification,score,status,fingerprint,plan_json
                   ) VALUES(?,'clean_record','member','[1]','needs_cleaning',
                            1.0,?,?,'{}')""",
                (run_id, status, f"control-quality-{index}"),
            ).lastrowid)
        bundle_id = conn.execute(
            "INSERT INTO quality_bundles(status,created_by) VALUES('draft','admin')"
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_bundle_items(
                   bundle_id,finding_id,action_type,payload_json,status
               ) VALUES(?,?,'clean_record','{}','pending')""",
            (bundle_id, finding_ids[1]),
        )
        conn.commit()

    response = client.get("/dashboard/control/quality")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Latest scan" in body
    assert "1 awaiting review" in body
    assert "1 queued for application" in body
    assert "1 completed decisions" in body
    assert 'href="/dashboard/data-quality"' in body
    assert "Editorial completeness" in body


def test_data_quality_analysis_is_read_only(client, app):
    with _db(app) as conn:
        conn.executemany(
            "INSERT INTO sponsors(slug,name,is_active) VALUES(?,?,1)",
            [("first", "Test Foundation"), ("second", "Test Foundation")],
        )
        conn.commit()

    response = client.post("/dashboard/data-quality/analyze")

    assert response.status_code == 200
    data = response.get_json()
    assert data.get("run_id", 0) > 0
    assert _scalar(app, "SELECT COUNT(*) FROM sponsors") == 2
    assert _scalar(app, "SELECT COUNT(*) FROM quality_bundles") == 0


def test_data_quality_page_renders_latest_bundle(client, app):
    with _db(app) as conn:
        run_id = conn.execute(
            """INSERT INTO quality_runs(status,fingerprint,completed_at,summary_json)
               VALUES('completed','test-fingerprint',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        finding_id = conn.execute(
            """INSERT INTO quality_findings(
                   run_id,action_type,entity_type,record_ids_json,
                   classification,score,status,fingerprint,plan_json
               ) VALUES(?,'clean_record','member','[1]','needs_cleaning',
                        1.0,'bundled','test-bundled','{}')""",
            (run_id,),
        ).lastrowid
        bundle_id = conn.execute(
            "INSERT INTO quality_bundles(status,created_by) VALUES('draft','admin')"
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_bundle_items(
                   bundle_id,finding_id,action_type,payload_json,status
               ) VALUES(?,?,'clean_record','{}','pending')""",
            (bundle_id, finding_id),
        )
        conn.commit()

    response = client.get("/dashboard/data-quality")

    assert response.status_code == 200
    assert "queued" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("path", "payload", "message"),
    [
        ("/dashboard/data-quality/bundles/999/items", {"finding_id": 1}, "editable bundle not found"),
        ("/dashboard/data-quality/bundles/999/items/1/remove", {}, "bundle item not found"),
    ],
)
def test_data_quality_invalid_actions_return_clear_conflict(client, path, payload, message):
    response = client.post(path, json=payload)
    assert response.status_code == 409
    assert response.get_json()["message"] == message


def test_conference_site_management_and_public_conference_events_are_separate(client, app):
    with _db(app) as conn:
        conn.execute(
            """INSERT INTO events(slug,title,event_type,start_date,remote_url,review_status)
               VALUES('historic-conference','Historic Conference','conference','2020-05-01',
                      'https://events.example/historic','published')"""
        )
        conn.commit()

    dashboard_page = client.get("/dashboard/conferences")
    assert dashboard_page.status_code == 200
    assert "Conference sites" in dashboard_page.get_data(as_text=True)
    page = client.get("/events/historic-conference")
    assert page.status_code == 200
    assert "https://events.example/historic" in page.get_data(as_text=True)

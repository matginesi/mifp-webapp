"""Canonical Events are ingested, never hand-authored from the dashboard.

The dashboard reviews and edits existing canonical events. Creation belongs to
the ingestion pipelines (JSONL/ZIP data portability, historical archive import,
conference/institutional importers), so these tests pin the boundary in both
directions: no manual authoring surface, and no regression in the pipelines that
are allowed to create events.
"""
from __future__ import annotations

import json
import os
import sqlite3
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
            "SECRET_KEY": "event-boundary-test-secret",
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
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.manage import init_database

    init_database(Path(app.config["DATABASE_PATH"]))
    yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "event-boundary-csrf"
    return client


APP_ROOT = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app"
TEMPLATES = APP_ROOT / "templates"
STATIC_JS = APP_ROOT / "static" / "js"


def _db(app) -> sqlite3.Connection:
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _count(app, table: str, where: str = "", params: tuple = ()) -> int:
    with _db(app) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0])


def _insert_event(app, *, title: str, slug: str, review_status: str = "draft") -> int:
    with _db(app) as conn:
        cur = conn.execute(
            "INSERT INTO events(title, slug, review_status) VALUES(?,?,?)",
            (title, slug, review_status),
        )
        conn.commit()
        return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# 1. No manual authoring surface remains
# ---------------------------------------------------------------------------

def test_events_page_has_no_new_event_action(client):
    body = client.get("/dashboard/events").get_data(as_text=True)

    assert "New Event" not in body
    assert "data-event-wizard" not in body
    assert 'id="eventWizard"' not in body
    assert "_event_wizard.html" not in body
    # The import entry point stays: ingestion is the supported creation route.
    assert "Import bundle" in body


def test_event_wizard_template_and_script_are_gone():
    assert not (TEMPLATES / "dashboard" / "_event_wizard.html").exists()
    assert not (STATIC_JS / "dashboard" / "events.js").exists()

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in list(TEMPLATES.rglob("*.html")) + list(STATIC_JS.rglob("*.js"))
        if "vendor" not in path.parts
    )
    for dead in ("_event_wizard.html", "events.js", "data-event-wizard", "eventWizard", "event-wizard"):
        assert dead not in sources, dead


def test_generic_content_workspace_cannot_create_events(app, client):
    """No hidden POST path may insert a canonical event through the content route."""
    before = _count(app, "events")
    response = client.post(
        "/dashboard/content/events",
        data={"title": "Smuggled event", "slug": "smuggled-event"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard/events")
    assert _count(app, "events") == before


# ---------------------------------------------------------------------------
# 2. POST is an update-only contract
# ---------------------------------------------------------------------------

def test_post_without_id_does_not_create_an_event(app, client):
    before = _count(app, "events")
    response = client.post(
        "/dashboard/events",
        data={"title": "Wizardless event", "slug": "wizardless-event", "review_status": "draft"},
    )

    assert response.status_code == 302
    assert _count(app, "events") == before
    with _db(app) as conn:
        assert conn.execute("SELECT 1 FROM events WHERE slug='wizardless-event'").fetchone() is None


def test_post_with_blank_id_does_not_create_an_event(app, client):
    before = _count(app, "events")
    client.post("/dashboard/events", data={"id": "", "title": "Blank id", "slug": "blank-id"})

    assert _count(app, "events") == before


def test_post_with_unknown_id_does_not_create_an_event(app, client):
    before = _count(app, "events")
    client.post(
        "/dashboard/events",
        data={"id": "999999", "title": "Ghost", "slug": "ghost-event"},
    )

    assert _count(app, "events") == before


def test_asset_management_fields_cannot_create_an_event(app, client):
    """The retired wizard's asset payload must not resurrect the create path."""
    before = _count(app, "events")
    client.post(
        "/dashboard/events",
        data={
            "title": "Asset payload",
            "slug": "asset-payload",
            "manage_event_assets": "1",
            "doc_asset_id": "1",
            "cover_asset_id": "1",
        },
    )

    assert _count(app, "events") == before


def test_post_without_id_reports_a_useful_error(app, client):
    response = client.post(
        "/dashboard/events",
        data={"title": "No id", "slug": "no-id-event"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "cannot be created from the dashboard" in body


# ---------------------------------------------------------------------------
# 3. Editing an existing event still works
# ---------------------------------------------------------------------------

def test_existing_event_can_still_be_edited(app, client):
    event_id = _insert_event(app, title="Original title", slug="editable-event")

    response = client.post(
        "/dashboard/events",
        data={
            "id": str(event_id),
            "title": "Updated title",
            "slug": "editable-event",
            "review_status": "draft",
            "location": "Rome",
        },
    )

    assert response.status_code == 302
    with _db(app) as conn:
        row = conn.execute("SELECT title, location FROM events WHERE id=?", (event_id,)).fetchone()
    assert row["title"] == "Updated title"
    assert row["location"] == "Rome"


def test_editing_existing_event_keeps_its_asset_links(app, client):
    event_id = _insert_event(app, title="Covered event", slug="covered-event")
    with _db(app) as conn:
        asset_id = conn.execute(
            "INSERT INTO assets(filename,original_filename,path,kind,mime_type)"
            " VALUES('cover.png','cover.png','image/cover.png','image','image/png')"
        ).lastrowid
        conn.execute(
            "INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order)"
            " VALUES(?,?,?,?,?,?)",
            (asset_id, "event", event_id, "cover", 1, 0),
        )
        conn.commit()

    client.post(
        "/dashboard/events",
        data={"id": str(event_id), "title": "Covered event v2", "slug": "covered-event",
              "review_status": "draft"},
    )

    with _db(app) as conn:
        links = conn.execute(
            "SELECT asset_id, role FROM asset_links WHERE entity_type='event' AND entity_id=?",
            (event_id,),
        ).fetchall()
    assert [(row["asset_id"], row["role"]) for row in links] == [(asset_id, "cover")]


def test_existing_event_can_still_be_deleted(app, client):
    event_id = _insert_event(app, title="Doomed event", slug="doomed-event")

    response = client.post(f"/dashboard/content/events/{event_id}/delete")

    assert response.status_code == 302
    assert _count(app, "events", "WHERE id=?", (event_id,)) == 0


# ---------------------------------------------------------------------------
# 4. Ingestion pipelines keep their own creation contract
# ---------------------------------------------------------------------------

def test_jsonl_import_can_still_create_events(app, tmp_path):
    """The data-portability ingestion contract still creates canonical events."""
    from mifp_app.services.importers import import_jsonl

    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "event",
                "data": {
                    "title": "Ingested event",
                    "slug": "ingested-event",
                    "start_date": "2026-04-01",
                    "review_status": "draft",
                },
                "links": [],
                "assets": [],
                "meta": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with _db(app) as conn:
        summary = import_jsonl(conn, path, dry_run=False)

    assert summary["errors"] == []
    assert _count(app, "events", "WHERE slug=?", ("ingested-event",)) == 1


def test_historical_archive_import_can_still_create_events(app, tmp_path):
    """Historical archive import keeps its own event-creation contract."""
    import zipfile

    from mifp_app.services.historical_archive import import_historical_archive

    event = {
        "schema": "mifp-historical-event-v1",
        "uid": "archive-event-1",
        "slug": "archive-event-one",
        "public_path": "archive/conferences/2014/archive-event-one",
        "public_url": "https://events.mifp.eu/archive/conferences/2014/archive-event-one/",
        "title": "Archive event",
        "acronym": "AE1",
        "category": "conferences",
        "event_type": "conference",
        "dates": {"start": "2014-05-01", "end": "2014-05-02", "text": "1-2 May 2014", "precision": "range"},
        "location": {"display": "Rome, Italy"},
        "summary": "Imported summary.",
        "description": "Imported description.",
        "topics": ["Quantum physics"],
        "people": {"participants": [], "chairs": [], "speakers": [], "committee": []},
        "program": [],
        "documents": [],
        "images": [],
        "sources": [],
        "recovery": {"confidence": "high", "notes": []},
        "not_recovered": [],
    }
    package = tmp_path / "archive.zip"
    base = "archive/conferences/2014/archive-event-one"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{base}/event.json", json.dumps(event))

    assets_dir = Path(app.config["ASSETS_DIR"])
    with _db(app) as conn:
        result = import_historical_archive(conn, package, assets_dir)

    assert result["events"] == 1
    assert _count(app, "events", "WHERE slug=?", ("archive-event-one",)) == 1


def test_conference_site_can_still_link_an_existing_event(app, client):
    """Conference packages keep linking to existing institutional events."""
    event_id = _insert_event(app, title="Linked event", slug="linked-event")
    with _db(app) as conn:
        conf_id = conn.execute(
            "INSERT INTO conference_sites(slug, title, event_id) VALUES(?,?,?)",
            ("plmcn-2026", "PLMCN 2026", event_id),
        ).lastrowid
        conn.commit()

    response = client.get("/dashboard/conferences")

    assert response.status_code == 200
    with _db(app) as conn:
        row = conn.execute("SELECT event_id FROM conference_sites WHERE id=?", (conf_id,)).fetchone()
    assert row["event_id"] == event_id


def test_public_event_pages_still_render(client):
    """Removing manual authoring must not change public event rendering."""
    response = client.get("/events")

    assert response.status_code == 200
    assert "Events" in response.get_data(as_text=True)

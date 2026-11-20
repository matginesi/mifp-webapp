"""Programme editing for legacy/internal conference workspaces."""
from __future__ import annotations

import io
import json
import os
import sqlite3
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
            "SECRET_KEY": "conference-program-test-secret",
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
        session["_csrf_token"] = "program-csrf"
    return client


def _db(app) -> sqlite3.Connection:
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(app, sql: str, params: tuple = ()):
    with _db(app) as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None


def _create_site(client) -> int:
    response = client.post(
        "/dashboard/conferences",
        data={
            "title": "Program Test 2027",
            "acronym": "PT27",
            "year": "2027",
            "slug": "program-test-2027",
            "status": "draft",
        },
    )
    assert response.status_code == 302
    return int(response.headers["Location"].rsplit("/", 1)[1])


def _program_csv(rows: str = "") -> bytes:
    header = (
        "Day,Date,Start Time,End Time,Type,Title,Speaker,Affiliation,Chair,Location,Notes,Visible\r\n"
    )
    return (header + rows).encode("utf-8")


def test_program_import_edit_add_and_export_roundtrip(app, client):
    site_id = _create_site(client)

    uploaded = _program_csv(
        "Day 1,2027-09-20,09:00,10:00,talk,Opening,Ada Lovelace,MIFP,,Main hall,,true\r\n"
    )
    response = client.post(
        f"/dashboard/conferences/{site_id}/program/import",
        data={
            "program_file": (io.BytesIO(uploaded), "program.csv"),
            "replace": "1",
            "_csrf_token": "program-csrf",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302

    with _db(app) as conn:
        asset = conn.execute(
            "SELECT filename,role FROM conference_assets WHERE conference_id=?",
            (site_id,),
        ).fetchone()
    assert asset is not None
    assert asset["role"] == "program_source"
    assert asset["filename"].endswith(".csv")

    page = client.get(f"/dashboard/conferences/{site_id}").get_data(as_text=True)
    assert "program[0][Day]" in page
    assert "Opening" in page

    response = client.post(
        f"/dashboard/conferences/{site_id}/program/save",
        data={
            "_csrf_token": "program-csrf",
            "program[0][Day]": "Day 1",
            "program[0][Date]": "2027-09-20",
            "program[0][Start Time]": "09:00",
            "program[0][End Time]": "10:30",
            "program[0][Type]": "talk",
            "program[0][Title]": "Opening edited",
            "program[0][Speaker]": "Ada Lovelace",
            "program[0][Affiliation]": "MIFP",
            "program[0][Chair]": "",
            "program[0][Location]": "Main hall",
            "program[0][Notes]": "",
            "program[0][Visible]": "true",
        },
    )
    assert response.status_code == 302

    exported = client.get(f"/dashboard/conferences/{site_id}/program.csv")
    assert exported.status_code == 200
    exported_text = exported.get_data(as_text=True)
    assert "Opening edited" in exported_text
    assert "10:30" in exported_text

    built = client.get(f"/dashboard/conferences/{site_id}/build.zip")
    assert built.status_code == 200
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        program_json = json.loads(archive.read("program.json").decode())
        assert any(row.get("Title") == "Opening edited" for row in program_json)
        assert any(row.get("End Time") == "10:30" for row in program_json)
        assert "Opening edited" in archive.read("program.html").decode()

    response = client.post(
        f"/dashboard/conferences/{site_id}/program/add",
        data={
            "_csrf_token": "program-csrf",
            "Day": "Day 2",
            "Date": "2027-09-21",
            "Start Time": "09:00",
            "End Time": "10:00",
            "Type": "talk",
            "Title": "Second talk",
            "Speaker": "",
            "Affiliation": "",
            "Chair": "",
            "Location": "",
            "Notes": "",
            "Visible": "true",
        },
    )
    assert response.status_code == 302

    exported_after_add = client.get(f"/dashboard/conferences/{site_id}/program.csv").get_data(as_text=True)
    assert "Second talk" in exported_after_add
    assert "Opening edited" in exported_after_add

    # A single programme source stays canonical even after importing a new CSV.
    replacement = _program_csv(
        "Day 3,2027-09-22,11:00,12:00,break,Coffee break,,,,Lobby,,true\r\n"
    )
    response = client.post(
        f"/dashboard/conferences/{site_id}/program/import",
        data={
            "program_file": (io.BytesIO(replacement), "program-v2.csv"),
            "replace": "1",
            "_csrf_token": "program-csrf",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with _db(app) as conn:
        program_sources = conn.execute(
            "SELECT COUNT(*) FROM conference_assets WHERE conference_id=? AND role='program_source'",
            (site_id,),
        ).fetchone()[0]
    assert program_sources == 1
    final = client.get(f"/dashboard/conferences/{site_id}/program.csv").get_data(as_text=True)
    assert "Coffee break" in final
    assert "Second talk" not in final

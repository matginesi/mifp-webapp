from __future__ import annotations

import io
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
            "SECRET_KEY": "assets-page-test-secret",
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
        session["_csrf_token"] = "assets-page-csrf"
    return client


def _db(app) -> sqlite3.Connection:
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _insert_asset(app, *, filename, path, kind, checksum, storage_status="local",
                  is_external=0, source_url="", caption="", alt_text=""):
    with _db(app) as conn:
        conn.execute(
            """
            INSERT INTO assets(filename, original_filename, path, kind, size,
                               checksum, is_external, source_url, storage_status,
                               caption, alt_text, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """,
            (filename, filename, path, kind, 2048, checksum, is_external, source_url,
             storage_status, caption, alt_text),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def test_strip_is_summary_not_filters(app, client):
    resp = client.get("/dashboard/assets")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'aria-label="Asset library status"' in body
    assert "operations-status-strip" in body
    assert "assets-status-strip" not in body
    assert "Asset health shortcuts" not in body
    assert '<a href="/dashboard/assets?status=' not in body


def test_single_status_filter_via_toolbar(app, client):
    resp = client.get("/dashboard/assets?status=missing")
    body = resp.get_data(as_text=True)
    assert 'option value="missing" selected' in body


def test_actions_dropdown_conditional_items(app, client):
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Reconcile storage status" in body
    assert "Export unused" not in body
    assert "Recover missing" not in body


def test_actions_dropdown_shows_recover_when_recoverable(app, client):
    _insert_asset(
        app, filename="recoverable.jpg", path="image/recoverable.jpg", kind="image",
        checksum="assets-page-recoverable", storage_status="missing",
        source_url="https://example.test/recoverable.jpg",
    )
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Recover missing" in body


def test_cleanup_panel_only_when_unused(app, client):
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Unused assets" not in body
    assert "Archive &amp; clean unused" not in body
    _insert_asset(app, filename="unused.txt", path="other/unused.txt", kind="other",
                  checksum="assets-page-unused")
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "Unused assets" in body
    assert "Archive &amp; clean unused" in body


def test_table_uses_standard_record_rows(app, client):
    _insert_asset(app, filename="page.png", path="image/page.png", kind="image",
                  checksum="assets-page-table")
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert "record-row" in body
    assert "inline-editor-row" in body
    assert "expandable-row" not in body
    assert "asset-row-inner" not in body


def test_view_button_and_external_aria_preserved(app, client):
    _insert_asset(
        app, filename="doc.pdf", path="external/doc.pdf", kind="pdf",
        checksum="assets-page-external", storage_status="external", is_external=1,
        source_url="https://example.test/doc.pdf",
    )
    resp = client.get("/dashboard/assets")
    body = resp.get_data(as_text=True)
    assert 'aria-label="Open external asset doc.pdf"' in body
    assert "asset-view-btn" in body
    assert "Esterno" in body
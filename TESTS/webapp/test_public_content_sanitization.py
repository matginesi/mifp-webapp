from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


@pytest.fixture
def app(tmp_path):
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from werkzeug.security import generate_password_hash
    from mifp_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    yield app


# ── S1: unsanitized DB text rendered on public pages ──


def test_list_home_sponsors_sanitizes_body():
    from mifp_app.services.public_repository import sanitize_html, list_home_sponsors

    raw_body = '<script>alert(1)</script><p>Acme <b>supports</b> MIFP</p><img src="x" onerror="alert(2)">'
    conn = _conn()
    conn.execute("ALTER TABLE sponsors ADD COLUMN body TEXT")
    conn.execute(
        "INSERT INTO sponsors(id, slug, name, body, is_active) VALUES (1, 'acme', 'Acme', ?, 1)",
        (raw_body,),
    )
    conn.commit()

    sponsor = list_home_sponsors(conn, lambda url: url)[0]

    assert sponsor["body"] == sanitize_html(raw_body)
    assert "<script" not in sponsor["body"]
    assert "onerror" not in sponsor["body"]


def test_list_public_research_sanitizes_description_and_summary():
    from mifp_app.services.public_repository import sanitize_html, list_public_research

    raw_summary = "<script>alert(1)</script>Gravity and cosmology studies"
    raw_description = '<p>Focus on <b>fundamental</b> physics</p><img src="x" onerror="alert(2)">'
    conn = _conn()
    conn.execute(
        "INSERT INTO research_areas(id, slug, title, summary, description, review_status) "
        "VALUES (1, 'gravity', 'Gravity', ?, ?, 'published')",
        (raw_summary, raw_description),
    )
    conn.commit()

    area = list_public_research(conn, lambda url: url)[0]

    assert area["description"] == sanitize_html(raw_description)
    assert area["summary"] == sanitize_html(raw_summary)
    assert "<script" not in area["description"]
    assert "onerror" not in area["summary"]


def test_home_page_strips_script_from_sponsor_modal(app, tmp_path):
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    db_path = tmp_path / "sanitized-home.db"
    with connect(db_path) as conn:
        migrate_content_schema(conn)
        conn.execute("ALTER TABLE sponsors ADD COLUMN body TEXT")
        conn.execute(
            "INSERT INTO sponsors(id, slug, name, body, is_active) VALUES (1, 'acme', 'Acme', ?, 1)",
            ('<script>alert(1)</script><p>Hello MIFP</p>',),
        )
        conn.commit()
    app.config["DATABASE_PATH"] = db_path

    response = app.test_client().get("/")

    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "<p>Hello MIFP</p>" in html


# ── S8: table-name SQL helpers must reject unexpected table names ──


def test_dashboard_repository_helpers_reject_unexpected_table_names():
    from mifp_app.services import dashboard_repository as repo

    conn = _conn()
    bad = "members; DROP TABLE news"

    with pytest.raises(AssertionError):
        repo.list_records(conn, bad)
    with pytest.raises(AssertionError):
        repo.list_records_paginated(conn, bad)
    with pytest.raises(AssertionError):
        repo.get_record(conn, bad, 1)
    with pytest.raises(AssertionError):
        repo.save_record(conn, bad, {"title": "x"})
    with pytest.raises(AssertionError):
        repo.count_table(conn, bad)
    with pytest.raises(AssertionError):
        repo.recent_rows(conn, bad)
    with pytest.raises(AssertionError):
        repo.table_schema(conn, bad)


def test_public_repository_table_columns_rejects_unexpected_table_name():
    from mifp_app.services.public_repository import _table_columns

    conn = _conn()

    with pytest.raises(AssertionError):
        _table_columns(conn, "members; DROP TABLE news")
    with pytest.raises(AssertionError):
        _table_columns(conn, "user")


def test_dashboard_repository_helpers_accept_known_tables():
    from mifp_app.services import dashboard_repository as repo

    conn = _conn()

    assert repo.list_records(conn, "news") == []
    assert repo.list_records_paginated(conn, "members")["total"] == 0
    assert repo.get_record(conn, "events", 1) is None
    assert repo.recent_rows(conn, "sponsors") == []
    assert repo.count_table(conn, "members") == 0
    assert repo.count_table(conn, "assets") == 0
    assert [c["name"] for c in repo.table_schema(conn, "pages")]

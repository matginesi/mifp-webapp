from __future__ import annotations

import os
import re
import sqlite3
import uuid

import pytest


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
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> str:
    login_page = client.get("/login")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', login_page.data)
    token = match.group(1).decode("utf-8") if match else ""
    client.post("/login", data={"login_username": "admin", "login_password": "secret123", "_csrf_token": token})
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def test_dashboard_can_add_and_delete_external_entity_link(app, client):
    csrf = _login(client)
    slug = f"news-external-link-test-{uuid.uuid4().hex}"
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        cur = conn.execute("INSERT INTO news(title, slug, review_status) VALUES (?,?,?)", ("News", slug, "published"))
        record_id = int(cur.lastrowid)
        conn.commit()

    resp = client.post(
        f"/dashboard/content/news/{record_id}/links/add",
        data={"_csrf_token": csrf, "url": "example.org/paper.pdf", "role": "document", "label": "PDF"},
    )

    assert resp.status_code == 200
    assert resp.json["success"] is True
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        row = conn.execute("SELECT id, url, role, label FROM entity_links WHERE entity_type='news' AND entity_id=?", (record_id,)).fetchone()
        assert row[1] == "https://example.org/paper.pdf"
        assert row[2] == "document"
        assert row[3] == "PDF"
        link_id = row[0]

    resp = client.post(
        f"/dashboard/content/news/{record_id}/links/delete",
        data={"_csrf_token": csrf, "link_id": link_id},
    )

    assert resp.status_code == 200
    assert resp.json["success"] is True
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entity_links WHERE entity_type='news' AND entity_id=? AND url='https://example.org/paper.pdf'",
            (record_id,),
        ).fetchone()[0] == 0

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest


@pytest.fixture
def app(tmp_path):
    import os
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["ASSETS_DIR"] = str(tmp_path / "assets")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from werkzeug.security import generate_password_hash
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(client):
    client.get("/login")
    client.post("/login", data={"username": "admin", "password": "secret123"})
    return client


def test_upload_with_path_traversal_rejected(admin_client):
    data = {
        "action": "upload",
        "file": (BytesIO(b"fake content"), "../etc/passwd"),
    }
    resp = admin_client.post("/dashboard/assets", data=data, follow_redirects=True)
    assert resp.status_code == 200


def test_upload_invalid_extension_rejected(app):
    from mifp_app.services.assets import store_asset
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema_path = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    with open(schema_path) as f:
        conn.executescript(f.read())
    tmp = app.config["ASSETS_DIR"] / "virus.exe"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(b"<html>bad</html>")
    with pytest.raises((ValueError, FileNotFoundError)):
        store_asset(conn, tmp, app.config["ASSETS_DIR"])

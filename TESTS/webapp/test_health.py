from __future__ import annotations

import json

import pytest


@pytest.fixture
def app(tmp_path):
    import os
    os.environ["TESTING"] = "1"
    db_path = tmp_path / "test.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        " key TEXT PRIMARY KEY, value TEXT,"
        " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.close()
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_returns_json(client):
    resp = client.get("/health")
    data = json.loads(resp.data)
    assert "status" in data


def test_health_has_db_fields(client):
    resp = client.get("/health")
    data = json.loads(resp.data)
    assert "database_exists" in data
    assert "database_ok" in data

    assert "assets_dir_exists" in data
    assert "exports_dir_exists" in data
    assert "log_dir_exists" in data


def test_ready_returns_200_when_db_ok(client):
    resp = client.get("/ready")
    data = json.loads(resp.data)
    assert resp.status_code == 200
    assert data["status"] == "ok"
    resp = client.get("/health")
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["database_ok"] is True
    assert data["database_exists"] is True


def test_ready_returns_503_before_persistent_storage_is_exhausted(client, monkeypatch):
    from mifp_app.config import Config

    monkeypatch.setattr(Config, "STORAGE_MIN_FREE_BYTES", 1024)
    monkeypatch.setattr("mifp_app.runtime_storage.available_bytes", lambda _path: 512)

    resp = client.get("/ready")
    data = json.loads(resp.data)

    assert resp.status_code == 503
    assert data["database"] == "ok"
    assert data["storage"] == "low"
    assert set(data["storage_free_bytes"]) == {"database", "assets", "exports", "logs"}

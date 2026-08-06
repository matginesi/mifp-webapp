from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def app(tmp_path, monkeypatch):
    os.environ["TESTING"] = "1"
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        " key TEXT PRIMARY KEY, value TEXT,"
        " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.close()
    from mifp_app.config import Config

    monkeypatch.setattr(Config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(Config, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(Config, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")
    from mifp_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _webapp_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE"


def _production_env(tmp_path: Path) -> dict:
    db_path = tmp_path / "mifp.db"
    sqlite3.connect(str(db_path)).close()
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(_webapp_dir()),
            "FLASK_ENV": "production",
            "FLASK_DEBUG": "0",
            "SECRET_KEY": "x" * 64,
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD_HASH": "scrypt:hash",
            "DATABASE_PATH": str(db_path),
            "ASSETS_DIR": str(assets),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "LOG_DIR": str(tmp_path / "logs"),
            "STORAGE_MIN_FREE_MB": "0",
            "LOG_ACCESS_ENABLED": "0",
            "TESTING": "0",
        }
    )
    env["MIFP_CONFIG"] = str(_webapp_dir() / "config" / "webapp.json")
    # Explicitly isolate this production subprocess from MIFPAPP/CORE/.env.
    # Otherwise a developer-local TRUSTED_HOSTS value could make the negative
    # startup test pass unexpectedly.
    env["MIFP_LOAD_DOTENV"] = "0"
    env.pop("TRUSTED_HOSTS", None)
    return env


def _run_create_app(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from mifp_app import create_app; create_app()"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_ready_200_when_only_tmpfs_dirs_are_below_reserve(app, monkeypatch):
    from mifp_app.config import Config

    reserve = 1024
    low = 512
    high = reserve * 10
    tmpfs_names = {"exports", "logs"}

    def fake_available_bytes(path):
        return low if Path(path).name in tmpfs_names else high

    def fake_is_tmpfs(path):
        return Path(path).name in tmpfs_names

    monkeypatch.setattr(Config, "STORAGE_MIN_FREE_BYTES", reserve)
    monkeypatch.setattr("mifp_app.runtime_storage.available_bytes", fake_available_bytes)
    monkeypatch.setattr("mifp_app._is_tmpfs", fake_is_tmpfs)

    resp = app.test_client().get("/ready")
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert data["storage_free_bytes"]["exports"] == low
    assert data["storage_free_bytes"]["logs"] == low


def test_ready_503_when_durable_dir_below_reserve(app, monkeypatch):
    from mifp_app.config import Config

    reserve = 1024
    low = 512
    high = reserve * 10
    tmpfs_names = {"exports", "logs"}

    def fake_available_bytes(path):
        return low if Path(path).name == "assets" else high

    def fake_is_tmpfs(path):
        return Path(path).name in tmpfs_names

    monkeypatch.setattr(Config, "STORAGE_MIN_FREE_BYTES", reserve)
    monkeypatch.setattr("mifp_app.runtime_storage.available_bytes", fake_available_bytes)
    monkeypatch.setattr("mifp_app._is_tmpfs", fake_is_tmpfs)

    resp = app.test_client().get("/ready")
    data = resp.get_json()

    assert resp.status_code == 503
    assert data["status"] == "error"
    assert data["storage"] == "low"
    assert set(data["storage_free_bytes"]) == {"database", "assets", "exports", "logs"}


def test_health_payload_does_not_expose_absolute_paths(app):
    from mifp_app.config import Config

    resp = app.test_client().get("/health")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "storage_free_bytes" in payload
    assert "storage_reserve_bytes" in payload

    text = resp.get_data(as_text=True)
    for path in (
        Config.DATABASE_PATH,
        Config.ASSETS_DIR,
        Config.EXPORT_DIR,
        Config.LOG_DIR,
    ):
        assert str(path) not in text


def test_production_startup_raises_without_trusted_hosts(tmp_path):
    env = _production_env(tmp_path)

    result = _run_create_app(env)

    assert result.returncode != 0
    assert "TRUSTED_HOSTS" in result.stderr


def test_production_startup_accepts_trusted_hosts(tmp_path):
    env = _production_env(tmp_path)
    env["TRUSTED_HOSTS"] = "mifp.eu,www.mifp.eu,127.0.0.1"

    result = _run_create_app(env)

    assert result.returncode == 0, result.stderr

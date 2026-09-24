from __future__ import annotations

import json
import io
import os
import sqlite3
import time
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update({
        "TESTING": "1",
        "DATABASE_PATH": str(tmp_path / "mifp.db"),
        "ASSETS_DIR": str(tmp_path / "assets"),
        "EXPORT_DIR": str(tmp_path / "exports"),
        "CONFERENCES_DIR": str(tmp_path / "conferences"),
        "LOG_DIR": str(tmp_path / "logs"),
        "SECRET_KEY": "security-dashboard-test-key",
        "LOG_ACCESS_ENABLED": "0",
    })
    from mifp_app import create_app
    from mifp_app.db.manage import init_database

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
        SECRET_KEY="html-secret-that-must-never-render",
        SMTP_PASSWORD="smtp-secret-that-must-never-render",
        EVENTS_REMOTE_PASSWORD="remote-secret-that-must-never-render",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    init_database(Path(app.config["DATABASE_PATH"]))
    yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_login_at"] = time.time()
        session["_csrf_token"] = "security-dashboard-csrf"
    return client


def test_security_dashboard_requires_authentication(app):
    response = app.test_client().get("/dashboard/security")
    assert response.status_code == 302
    assert "/login" in response.location


def test_security_dashboard_renders_checks_without_secret_values(app, client):
    response = client.get("/dashboard/security", headers={"User-Agent": "Security dashboard test"})
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Security overview" in body
    assert "Application signing key" in body
    assert "Current administrator session" in body
    assert "Signed browser cookie" in body
    assert "server-side session enumeration" in body.lower()
    assert "html-secret-that-must-never-render" not in body
    assert "smtp-secret-that-must-never-render" not in body
    assert "remote-secret-that-must-never-render" not in body


def test_security_dashboard_has_safe_empty_event_state(client):
    body = client.get("/dashboard/security").get_data(as_text=True)
    assert "No recent security or audit event is available" in body


def test_security_event_projection_is_allowlisted_and_redacted(tmp_path: Path):
    from mifp_app.services.security_status import recent_security_events

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "security.jsonl").write_text(json.dumps({
        "timestamp": "2026-09-25T12:00:00+00:00",
        "level": "WARNING",
        "logger": "mifp.security",
        "stream": "security",
        "event": "upload.rejected",
        "message": "password=super-sensitive",
        "request_id": "request-1",
        "outcome": "failure",
        "password": "super-sensitive",
        "internal_path": "/private/data",
    }) + "\n", encoding="utf-8")

    events = recent_security_events(log_dir)
    serialized = json.dumps(events)
    assert len(events) == 1
    assert events[0]["event"] == "upload.rejected"
    assert events[0]["details"] == {"outcome": "failure"}
    assert "super-sensitive" not in serialized
    assert "/private/data" not in serialized


def test_rejected_asset_upload_emits_structured_security_event(app, client, monkeypatch):
    from mifp_app.routes import dashboard_assets

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        record_id = conn.execute(
            "INSERT INTO news(title,slug,review_status) VALUES(?,?,?)",
            ("Security upload", "security-upload", "draft"),
        ).lastrowid
        conn.commit()
    captured = []
    monkeypatch.setattr(
        dashboard_assets,
        "security_event",
        lambda event_type, action, **fields: captured.append((event_type, action, fields)),
    )

    response = client.post(
        f"/dashboard/content/news/{record_id}/assets/upload",
        data={
            "_csrf_token": "security-dashboard-csrf",
            "file": (io.BytesIO(b"<?php echo 1;"), "payload.php", "application/x-php"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert captured[0][0] == "upload.rejected"
    assert captured[0][2]["reason"] == "extension"
    assert "payload.php" not in json.dumps(captured)

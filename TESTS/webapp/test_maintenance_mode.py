from __future__ import annotations

import os
import sqlite3
import threading
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
        "LOG_DIR": str(tmp_path / "logs"),
        "SECRET_KEY": "maintenance-test-secret",
        "LOG_ACCESS_ENABLED": "0",
    })
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("admin-secret"),
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    with connect(app.config["DATABASE_PATH"]) as conn:
        migrate_content_schema(conn)
    yield app
    from mifp_app.config import Config

    for database_path in {Path(app.config["DATABASE_PATH"]), Path(Config.DATABASE_PATH)}:
        if not database_path.exists():
            continue
        with connect(database_path) as conn:
            conn.execute("DELETE FROM settings WHERE key LIKE 'maintenance_%'")
            conn.commit()


def _settings(app, **values):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            values.items(),
        )
        conn.commit()


def _admin(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "test-csrf"
    return client


def test_public_site_is_replaced_by_single_page_with_admin_login(app):
    _settings(
        app,
        maintenance_enabled="1",
        maintenance_message="New site incoming.",
    )
    client = app.test_client()

    blocked = client.get("/")
    assert blocked.status_code == 503
    second_page = client.get("/news")
    assert second_page.status_code == 503
    assert b"New site incoming." in blocked.data
    assert b"Administrator login" in blocked.data
    assert b"Site password" not in blocked.data
    assert b'img/logo-mifp.png' in blocked.data
    assert b'alt="MIFP' in blocked.data

    denied = client.post(
        "/login?next=/dashboard/&source=maintenance",
        data={"login_username": "admin", "login_password": "wrong"},
        follow_redirects=True,
    )
    assert denied.status_code == 503
    assert b"Invalid credentials." in denied.data
    granted = client.post(
        "/login?next=/dashboard/&source=maintenance",
        data={"login_username": "admin", "login_password": "admin-secret"},
    )
    assert granted.status_code == 302
    assert granted.headers["Location"] == "/dashboard/"
    assert client.get("/dashboard/").status_code == 200


def test_admin_can_enable_maintenance_without_a_second_password(app):
    client = _admin(app)

    response = client.post("/dashboard/control/site/maintenance", data={
        "maintenance_enabled": "1",
        "maintenance_message": "Updating our public presence.",
    })

    assert response.status_code == 302
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        values = dict(conn.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'maintenance_%'"
        ).fetchall())
    assert values["maintenance_enabled"] == "1"
    assert values["maintenance_message"] == "Updating our public presence."
    assert "maintenance_password_hash" not in values


def test_disabling_manual_maintenance_removes_orphan_operation_marker(app):
    from mifp_app.services.operation_maintenance import maintenance_marker_path

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("stale", encoding="utf-8")
    client = _admin(app)

    response = client.post(
        "/dashboard/control/site/maintenance",
        data={"maintenance_message": ""},
    )

    assert response.status_code == 302
    assert not marker.exists()
    assert app.test_client().get("/").status_code == 200


def test_startup_recovery_preserves_marker_for_active_operation(app):
    from mifp_app.services.operation_maintenance import (
        clear_stale_operation_marker,
        maintenance_marker_path,
    )

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("active", encoding="utf-8")
    _settings(app, maintenance_operation_count="1")

    assert clear_stale_operation_marker(app.config["DATABASE_PATH"]) is False
    assert marker.exists()


def test_admin_routes_and_health_bypass_maintenance(app):
    _settings(
        app,
        maintenance_enabled="1",
    )

    assert app.test_client().get("/health").status_code == 200
    admin = _admin(app)
    assert admin.get("/dashboard/control/site").status_code == 200
    public_home = admin.get("/")
    assert public_home.status_code == 503
    assert b"Work in progress is active" in public_home.data
    assert b"Open dashboard" in public_home.data


def test_media_bypasses_operation_maintenance_page(app):
    from mifp_app.services.operation_maintenance import operation_maintenance

    assets_dir = Path(app.config["ASSETS_DIR"])
    pdf = assets_dir / "pdf" / "document.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4\nmaintenance-safe\n")

    with operation_maintenance(app.config["DATABASE_PATH"], "test media"):
        response = app.test_client().get("/media/pdf/document.pdf")
        blocked = app.test_client().get("/")

    assert response.status_code == 200
    assert response.data.startswith(b"%PDF-1.4")
    assert b"Work in progress" not in response.data
    assert blocked.status_code == 503
    assert blocked.headers["Cache-Control"] == "no-store, max-age=0"


def test_enabling_ignores_obsolete_preview_password_field(app):
    response = _admin(app).post("/dashboard/control/site/maintenance", data={
        "maintenance_enabled": "1",
        "maintenance_password": "short",
    }, follow_redirects=True)

    assert response.status_code == 200
    assert b"Work in progress mode updated." in response.data
    assert b"Preview password" not in response.data


def test_operation_guard_enables_and_restores_work_in_progress(app):
    from mifp_app.services.operation_maintenance import maintenance_marker_path, operation_maintenance

    _settings(app, maintenance_enabled="0", maintenance_message="Original message")
    with operation_maintenance(app.config["DATABASE_PATH"], "test import"):
        marker = maintenance_marker_path(app.config["DATABASE_PATH"])
        assert marker.is_file()
        started = time.monotonic()
        blocked = app.test_client().get("/")
        assert blocked.status_code == 503
        assert time.monotonic() - started < 0.5
        assert b"Secure maintenance in progress" in blocked.data
        assert b"test import" not in blocked.data
        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            values = dict(conn.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'maintenance_%'"
            ).fetchall())
        assert values["maintenance_enabled"] == "1"
        assert values["maintenance_operation_count"] == "1"
        assert values["maintenance_message"] == (
            "Secure maintenance in progress. Please try again shortly."
        )

        with operation_maintenance(app.config["DATABASE_PATH"], "nested backup"):
            with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
                assert conn.execute(
                    "SELECT value FROM settings WHERE key='maintenance_operation_count'"
                ).fetchone()[0] == "2"

        with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
            assert conn.execute(
                "SELECT value FROM settings WHERE key='maintenance_enabled'"
            ).fetchone()[0] == "1"

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        values = dict(conn.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'maintenance_%'"
        ).fetchall())
    assert values["maintenance_enabled"] == "0"
    assert values["maintenance_message"] == "Original message"
    assert "maintenance_operation_count" not in values
    assert not maintenance_marker_path(app.config["DATABASE_PATH"]).exists()


def test_operation_guard_restores_manual_mode_after_failure(app):
    from mifp_app.services.operation_maintenance import operation_maintenance

    _settings(app, maintenance_enabled="1", maintenance_message="Manual maintenance")
    with pytest.raises(RuntimeError, match="boom"):
        with operation_maintenance(app.config["DATABASE_PATH"], "failing cleanup"):
            raise RuntimeError("boom")

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        values = dict(conn.execute(
            "SELECT key,value FROM settings WHERE key IN ('maintenance_enabled','maintenance_message')"
        ).fetchall())
    assert values == {
        "maintenance_enabled": "1",
        "maintenance_message": "Manual maintenance",
    }


def test_operation_guard_waits_for_a_transient_database_writer(app, monkeypatch):
    from mifp_app.services.operation_maintenance import operation_maintenance

    monkeypatch.setenv("MAINTENANCE_LOCK_TIMEOUT_SECONDS", "2")
    writer = sqlite3.connect(app.config["DATABASE_PATH"], timeout=1, check_same_thread=False)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")

    release = threading.Thread(target=lambda: (time.sleep(0.2), writer.rollback(), writer.close()))
    release.start()
    try:
        with operation_maintenance(app.config["DATABASE_PATH"], "transient writer"):
            pass
    finally:
        release.join(timeout=2)

    assert not release.is_alive()


def test_home_read_path_does_not_wait_for_dashboard_writer(app):
    writer = sqlite3.connect(app.config["DATABASE_PATH"], timeout=1)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO settings(key,value) VALUES('writer_probe','1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )

        started = time.monotonic()
        response = app.test_client().get("/")
        elapsed = time.monotonic() - started

        assert response.status_code == 200
        assert elapsed < 0.5
    finally:
        writer.rollback()
        writer.close()

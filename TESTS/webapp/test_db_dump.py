from __future__ import annotations

import pytest


@pytest.fixture
def app_with_admin(tmp_path):
    import os
    from werkzeug.security import generate_password_hash
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-pass")
    yield app


def _login(client):
    return client.post("/login", data={
        "login_username": "admin",
        "login_password": "test-pass",
    })


def test_db_dump_blocked_when_disabled(app_with_admin):
    from mifp_app.utils.security import get_client_ip
    app_with_admin.config["ALLOW_DB_DUMP"] = False
    with app_with_admin.test_client() as client:
        login_resp = _login(client)
        resp = client.post("/dashboard/server/db-dump", data={
            "password": "test-pass",
        })
        assert resp.status_code == 302
        assert b"Database dump is disabled" in resp.data or resp.location == "/dashboard/server"


def test_db_dump_requires_password(app_with_admin):
    app_with_admin.config["ALLOW_DB_DUMP"] = True
    with app_with_admin.test_client() as client:
        _login(client)
        resp = client.post("/dashboard/server/db-dump", data={
            "password": "wrong-password",
        })
        assert resp.status_code == 302
        assert b"Invalid password" in resp.data or resp.location == "/dashboard/server"


def test_db_dump_sets_no_cache_headers(app_with_admin):
    import sqlite3
    conn = sqlite3.connect(app_with_admin.config["DATABASE_PATH"])
    conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER)")
    conn.commit()
    conn.close()
    app_with_admin.config["ALLOW_DB_DUMP"] = True
    with app_with_admin.test_client() as client:
        _login(client)
        resp = client.post("/dashboard/server/db-dump", data={
            "password": "test-pass",
        })
        assert resp.status_code == 200
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc


def test_full_database_download_and_restore_round_trip(app_with_admin):
    import io
    import sqlite3

    app_with_admin.config["ALLOW_DB_DUMP"] = True
    app_with_admin.config["ALLOW_DB_RESTORE"] = True
    db_path = app_with_admin.config["DATABASE_PATH"]
    wal_writer = sqlite3.connect(db_path)
    wal_writer.execute("PRAGMA journal_mode=WAL")
    wal_writer.execute("DELETE FROM news WHERE slug IN ('snapshot-record','later-record')")
    wal_writer.execute(
        "INSERT INTO news(title,slug,review_status) VALUES('Snapshot record','snapshot-record','published')"
    )
    wal_writer.commit()

    with app_with_admin.test_client() as client:
        _login(client)
        downloaded = client.post(
            "/dashboard/server/db-dump", data={"password": "test-pass"}
        )
        wal_writer.close()
        assert downloaded.status_code == 200
        assert downloaded.data.startswith(b"SQLite format 3")
        assert downloaded.headers["X-MIFP-Backup-Type"] == "full-sqlite-snapshot"

        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM news WHERE slug='snapshot-record'")
            conn.execute(
                "INSERT INTO news(title,slug,review_status) VALUES('Later record','later-record','published')"
            )
            conn.commit()

        restored = client.post(
            "/dashboard/server/db-restore",
            data={
                "password": "test-pass",
                "confirmation": "RESTORE DATABASE",
                "database_file": (
                    io.BytesIO(downloaded.data),
                    "mifp_full_database.sqlite",
                    "application/vnd.sqlite3",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert restored.status_code == 200
        assert b"Full database restored successfully" in restored.data

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM news WHERE slug='snapshot-record'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM news WHERE slug='later-record'"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_database_restore_rejects_non_sqlite_without_changing_data(app_with_admin):
    import io
    import sqlite3

    app_with_admin.config["ALLOW_DB_RESTORE"] = True
    db_path = app_with_admin.config["DATABASE_PATH"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM news WHERE slug='keep-me'")
        conn.execute(
            "INSERT INTO news(title,slug,review_status) VALUES('Keep me','keep-me','published')"
        )
        conn.commit()

    with app_with_admin.test_client() as client:
        _login(client)
        response = client.post(
            "/dashboard/server/db-restore",
            data={
                "password": "test-pass",
                "confirmation": "RESTORE DATABASE",
                "database_file": (
                    io.BytesIO(b"not a sqlite database"),
                    "broken.sqlite",
                    "application/vnd.sqlite3",
                ),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"empty or incomplete" in response.data

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM news WHERE slug='keep-me'"
        ).fetchone()[0] == 1

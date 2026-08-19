from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update({
        "TESTING": "1",
        "DATABASE_PATH": str(tmp_path / "mifp.db"),
        "ASSETS_DIR": str(tmp_path / "assets"),
        "EXPORT_DIR": str(tmp_path / "exports"),
        "LOG_DIR": str(tmp_path / "logs"),
        "SECRET_KEY": "operation-recovery-test-secret",
        "LOG_ACCESS_ENABLED": "0",
        "MAINTENANCE_CRASH_TIMEOUT_SECONDS": "60",
        "STORAGE_MIN_FREE_MB": "0",
    })
    from mifp_app import create_app
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

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
        ALLOW_DB_RESTORE=True,
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    with connect(app.config["DATABASE_PATH"]) as conn:
        migrate_content_schema(conn)
    return app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "test-csrf"
    return client


def _setting(app, **values):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            values.items(),
        )
        conn.commit()


def _maintenance_settings(app):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        return dict(conn.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'maintenance_%'"
        ).fetchall())


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


def _old_timestamp() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stuck_operation(app, *, recent: bool = False):
    _setting(app,
        maintenance_enabled="1",
        maintenance_operation_count="1",
        maintenance_operation_previous_enabled="0",
        maintenance_operation_previous_message="",
        maintenance_operation_pid=str(_dead_pid()),
        maintenance_operation_started_at=_now_timestamp() if recent else _old_timestamp(),
    )


# ---------------------------------------------------------------------------
# R1: crash reaper for a stuck operation (owner PID gone + past timeout)
# ---------------------------------------------------------------------------


def test_startup_reaper_clears_crashed_operation_and_restores_public_site(app):
    from mifp_app.services.operation_maintenance import (
        clear_stale_operation_marker,
        maintenance_marker_path,
    )

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("stale", encoding="utf-8")
    _stuck_operation(app)

    assert clear_stale_operation_marker(app.config["DATABASE_PATH"]) is True
    assert not marker.exists()
    values = _maintenance_settings(app)
    assert values.get("maintenance_enabled") == "0"
    assert "maintenance_operation_count" not in values
    assert "maintenance_operation_pid" not in values
    assert "maintenance_operation_started_at" not in values
    assert app.test_client().get("/").status_code == 200


def test_reaper_preserves_operation_owned_by_live_pid(app):
    from mifp_app.services.operation_maintenance import (
        clear_stale_operation_marker,
        maintenance_marker_path,
    )

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("active", encoding="utf-8")
    _setting(app,
        maintenance_enabled="1",
        maintenance_operation_count="1",
        maintenance_operation_previous_enabled="0",
        maintenance_operation_pid=str(os.getpid()),
        maintenance_operation_started_at=_old_timestamp(),
    )

    assert clear_stale_operation_marker(app.config["DATABASE_PATH"]) is False
    assert marker.exists()
    assert _maintenance_settings(app)["maintenance_enabled"] == "1"


def test_reaper_preserves_crash_within_bounded_timeout(app):
    from mifp_app.services.operation_maintenance import (
        clear_stale_operation_marker,
        maintenance_marker_path,
    )

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("recent", encoding="utf-8")
    _stuck_operation(app, recent=True)

    assert clear_stale_operation_marker(app.config["DATABASE_PATH"]) is False
    assert marker.exists()
    assert _maintenance_settings(app)["maintenance_enabled"] == "1"


def test_reaper_preserves_manual_maintenance_without_operation_owner(app):
    from mifp_app.services.operation_maintenance import clear_stale_operation_marker

    _setting(app, maintenance_enabled="1", maintenance_message="Manual work")
    assert clear_stale_operation_marker(app.config["DATABASE_PATH"]) is False
    assert _maintenance_settings(app)["maintenance_enabled"] == "1"


def test_public_gate_self_heals_orphaned_operation_after_timeout(app, monkeypatch):
    """A public request must reap a crashed operation once the crash timeout
    has passed, without waiting for a restart or another protected op."""
    import mifp_app.routes.maintenance as maintenance_module

    from mifp_app.services.operation_maintenance import maintenance_marker_path

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("stale", encoding="utf-8")
    _stuck_operation(app, recent=True)

    assert app.test_client().get("/").status_code == 503
    assert marker.exists()

    # Once the (60s) crash timeout passes, the next public request self-heals.
    _setting(
        app,
        maintenance_operation_started_at=(
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat(),
    )
    monkeypatch.setattr(maintenance_module, "_last_reap_attempt", 0.0)
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert not marker.exists()
    values = _maintenance_settings(app)
    assert values.get("maintenance_enabled") == "0"
    assert "maintenance_operation_count" not in values


def test_begin_records_owner_pid_and_reaps_stale_operation_before_starting(app):
    from mifp_app.services.operation_maintenance import operation_maintenance

    _stuck_operation(app)

    with operation_maintenance(app.config["DATABASE_PATH"], "recovery import"):
        values = _maintenance_settings(app)
        assert values["maintenance_operation_count"] == "1"
        assert values["maintenance_operation_pid"] == str(os.getpid())
        assert "maintenance_operation_started_at" in values
        assert values["maintenance_enabled"] == "1"

    values = _maintenance_settings(app)
    assert values.get("maintenance_enabled") == "0"
    assert "maintenance_operation_count" not in values


def test_force_clear_maintenance_route_recovers_stuck_public_site(app, client):
    from mifp_app.services.operation_maintenance import maintenance_marker_path

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("stale", encoding="utf-8")
    _stuck_operation(app)

    assert client.get("/").status_code == 503
    response = client.post(
        "/dashboard/control/site/force-clear-maintenance",
        data={"password": "admin-secret"},
    )
    assert response.status_code == 302
    values = _maintenance_settings(app)
    assert values.get("maintenance_enabled") == "0"
    assert "maintenance_operation_count" not in values
    assert not marker.exists()
    assert client.get("/").status_code == 200


def test_force_clear_maintenance_requires_admin_password(app, client):
    from mifp_app.services.operation_maintenance import maintenance_marker_path

    marker = maintenance_marker_path(app.config["DATABASE_PATH"])
    marker.write_text("stale", encoding="utf-8")
    _stuck_operation(app)

    response = client.post(
        "/dashboard/control/site/force-clear-maintenance",
        data={"password": "wrong-password"},
    )
    assert response.status_code == 302
    assert _maintenance_settings(app)["maintenance_enabled"] == "1"
    assert marker.exists()


# ---------------------------------------------------------------------------
# R2: atomic database restore (staged + verified + os.replace)
# ---------------------------------------------------------------------------


def _snapshot_bytes(db_path: Path, target: Path) -> bytes:
    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return target.read_bytes()


def test_restore_is_atomic_and_removes_stale_wal_shm(tmp_path):
    """Restore must stage + verify the payload, swap atomically, and drop the
    stale -wal/-shm sidecars from the pre-restore database."""
    from mifp_app.db.connection import connect as app_connect
    from mifp_app.db.migrations import migrate_content_schema
    from mifp_app.services.database_restore import restore_sqlite_database

    db_path = tmp_path / "live.db"

    def build(state: str) -> None:
        conn = app_connect(db_path)
        try:
            migrate_content_schema(conn)
            conn.execute("DELETE FROM news")
            conn.execute(
                "INSERT INTO news(title,slug,review_status) VALUES(?,?,'published')",
                (state, state.lower()),
            )
            conn.commit()
        finally:
            conn.close()

    build("Snapshot")
    snapshot = _snapshot_bytes(db_path, tmp_path / "snapshot.sqlite")
    build("Later")

    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    wal.write_bytes(b"")
    shm.write_bytes(b"")

    report = restore_sqlite_database(db_path, snapshot)

    assert report["counts"]["news"] >= 1
    assert not wal.exists()
    assert not shm.exists()
    leftovers = [
        p for p in db_path.parent.iterdir()
        if ".mifp-restore-" in p.name or p.name.startswith(".mifp-")
    ]
    assert leftovers == []
    check = app_connect(db_path)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute(
            "SELECT COUNT(*) FROM news WHERE slug='snapshot'"
        ).fetchone()[0] == 1
        assert check.execute(
            "SELECT COUNT(*) FROM news WHERE slug='later'"
        ).fetchone()[0] == 0
    finally:
        check.close()


def test_restore_rejects_invalid_payload_without_touching_live_db(app):
    from mifp_app.services.database_restore import DatabaseRestoreError, restore_sqlite_database

    db_path = Path(app.config["DATABASE_PATH"])
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM news WHERE slug='keep-me'")
        conn.execute(
            "INSERT INTO news(title,slug,review_status) VALUES('Keep me','keep-me','published')"
        )
        conn.commit()

    with pytest.raises(DatabaseRestoreError):
        restore_sqlite_database(db_path, b"not a sqlite database")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM news WHERE slug='keep-me'"
        ).fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ---------------------------------------------------------------------------
# R3: a mid-migration failure rolls the whole step back
# ---------------------------------------------------------------------------


def _legacy_database(tmp_path: Path, *, extra_column: bool = True) -> Path:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("ALTER TABLE events DROP COLUMN remote_url")
    conn.execute("DROP TABLE content_aliases")
    conn.execute("DROP TABLE quality_findings")
    conn.executescript("""
        CREATE TABLE quality_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN ('clean_record','enrich_record','merge_records','split_aggregated_record','repair_relations_or_assets')),
            entity_type TEXT NOT NULL CHECK(entity_type IN ('member','event','news','publication','page','sponsor')),
            record_ids_json TEXT NOT NULL,
            classification TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            contradictions_json TEXT NOT NULL DEFAULT '[]',
            plan_json TEXT NOT NULL DEFAULT '{}',
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE content_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL CHECK(entity_type IN ('member','event','news','publication','page','sponsor')),
            old_slug TEXT NOT NULL,
            canonical_entity_id INTEGER NOT NULL,
            canonical_slug TEXT NOT NULL,
            bundle_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_type, old_slug)
        );
    """)
    if extra_column:
        conn.execute("ALTER TABLE quality_findings ADD COLUMN extra_column TEXT")
    conn.execute(
        "INSERT INTO quality_runs(id,status,fingerprint) VALUES(1,'completed','run-1')"
    )
    conn.execute(
        "INSERT INTO quality_findings(id,run_id,action_type,entity_type,record_ids_json,classification,fingerprint) "
        "VALUES(1,1,'clean_record','member','[1]','exact_duplicate','fp-1')"
    )
    conn.commit()
    conn.close()
    return db_path


def test_migration_partial_failure_rolls_back_to_pre_step_schema(tmp_path):
    from mifp_app.db.migrations import migrate_content_schema

    db_path = _legacy_database(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.Error):
        migrate_content_schema(conn)
    conn.close()

    with sqlite3.connect(db_path) as check:
        events_columns = {row[1] for row in check.execute("PRAGMA table_info(events)")}
        runs_columns = {row[1] for row in check.execute("PRAGMA table_info(quality_runs)")}
        assert "remote_url" not in events_columns
        assert "progress_pct" not in runs_columns
        assert "progress_message" not in runs_columns
        assert check.execute(
            "SELECT COUNT(*) FROM quality_findings"
        ).fetchone()[0] == 1
        assert check.execute(
            "SELECT COUNT(*) FROM content_aliases"
        ).fetchone()[0] == 0


def test_migration_rebuild_succeeds_on_compatible_legacy_db(tmp_path):
    from mifp_app.db.migrations import SCHEMA_VERSION, migrate_content_schema

    db_path = _legacy_database(tmp_path, extra_column=False)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    report = migrate_content_schema(conn)
    conn.commit()
    conn.close()

    assert report["schema_version"] == SCHEMA_VERSION
    with sqlite3.connect(db_path) as check:
        finding_sql = check.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='quality_findings'"
        ).fetchone()[0]
        alias_sql = check.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_aliases'"
        ).fetchone()[0]
        assert "research_area" in finding_sql
        assert "research_area" in alias_sql
        assert check.execute(
            "SELECT COUNT(*) FROM quality_findings"
        ).fetchone()[0] == 1
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ---------------------------------------------------------------------------
# R4: metrics are accumulated in memory and flushed to the database
# ---------------------------------------------------------------------------


def test_metric_increments_are_batched_until_explicit_flush(app, client):
    app.config["TESTING"] = False
    client.get("/events")

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM metrics_daily").fetchone()[0] == 0

    from mifp_app.utils.logger import flush_metric_buffer

    assert flush_metric_buffer() > 0
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        row = conn.execute(
            "SELECT metric_value FROM metrics_daily "
            "WHERE scope='public_site' AND metric_name='page_view' AND metric_key='/events'"
        ).fetchone()
    assert row is not None
    assert row[0] == 1


def test_metric_accumulator_aggregates_before_flush(app, client):
    app.config["TESTING"] = False
    client.get("/events")
    client.get("/events")

    from mifp_app.utils.logger import flush_metric_buffer

    flush_metric_buffer()
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        row = conn.execute(
            "SELECT metric_value FROM metrics_daily "
            "WHERE scope='public_site' AND metric_name='page_view' AND metric_key='/events'"
        ).fetchone()
    assert row is not None
    assert row[0] == 2


def test_metric_flush_is_idempotent_and_tolerates_missing_table(app, client):
    from mifp_app.utils.logger import flush_metric_buffer

    app.config["TESTING"] = False
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute("DROP TABLE metrics_daily")
        conn.commit()
    client.get("/events")
    assert flush_metric_buffer() == 0  # dropped, not lost forever / no crash
    assert flush_metric_buffer() == 0
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "CREATE TABLE metrics_daily("
            "date TEXT, scope TEXT, metric_name TEXT, metric_key TEXT, metric_value INTEGER, "
            "updated_at TEXT, "
            "UNIQUE(date, scope, metric_name, metric_key))"
        )
        conn.commit()
    client.get("/events")
    assert flush_metric_buffer() > 0


# ---------------------------------------------------------------------------
# S3-dashboard: open-redirect guard on the settings _redirect field
# ---------------------------------------------------------------------------


def test_settings_redirect_rejects_backslash_open_redirect(app, client):
    response = client.post(
        "/dashboard/settings",
        data={"_redirect": "/\\evil.example.com"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard/server"


def test_settings_redirect_allows_relative_paths(app, client):
    response = client.post(
        "/dashboard/settings",
        data={"_redirect": "/events?year=2026"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/events?year=2026"


def test_settings_redirect_rejects_scheme_relative_url(app, client):
    response = client.post(
        "/dashboard/settings",
        data={"_redirect": "//evil.example.com/phish"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard/server"


def test_settings_redirect_rejects_absolute_url(app, client):
    response = client.post(
        "/dashboard/settings",
        data={"_redirect": "https://evil.example.com/phish"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard/server"

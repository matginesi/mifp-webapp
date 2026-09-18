"""Retention promises must match what the application actually does.

The privacy policy states that closed membership applications (rejected or
archived) are not kept indefinitely. That promise is kept by the existing
protected safety-cleanup procedure — there is no background scheduler — so these
tests pin both the selection rule and the safety guarantees around it.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from mifp_app.services.safety_operations import (
    eligible_join_request_ids,
    execute_safe_cleanup,
    safety_operations_preview,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "mifp.db"
    from mifp_app.db.manage import init_database

    init_database(path)
    return path


def _conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _add(path: Path, *, status: str, age_days: int, email: str) -> int:
    with _conn(path) as conn:
        cur = conn.execute(
            "INSERT INTO join_requests(first_name,last_name,email,status,created_at,reviewed_at) "
            "VALUES(?,?,?,?,datetime('now', ?), datetime('now', ?))",
            ("Ada", "Example", email, status, f"-{age_days} days", f"-{age_days} days"),
        )
        conn.commit()
        return int(cur.lastrowid)


def _statuses(path: Path) -> dict[str, str]:
    with _conn(path) as conn:
        return {row["email"]: row["status"] for row in conn.execute("SELECT email, status FROM join_requests")}


def _config(path: Path, tmp_path: Path, days: int) -> dict:
    for name in ("assets", "exports", "logs", "conferences"):
        (tmp_path / name).mkdir(exist_ok=True)
    return {
        "DATABASE_PATH": path,
        "LOG_DIR": tmp_path / "logs",
        "LOG_RETENTION_DAYS": 30,
        "EXPORT_DIR": tmp_path / "exports",
        "EXPORT_MAX_FILES": 30,
        "EXPORT_MAX_BYTES": 2048 * 1024 * 1024,
        "EXPORT_RETENTION_DAYS": 1,
        "STORAGE_MIN_FREE_BYTES": 0,
        "PRIVACY_SAFE_METRICS_RETENTION_DAYS": 730,
        "JOIN_REQUEST_RETENTION_DAYS": days,
    }


# ---------------------------------------------------------------------------
# Selection rule
# ---------------------------------------------------------------------------

def test_only_closed_requests_past_retention_are_eligible(db):
    old_rejected = _add(db, status="rejected", age_days=800, email="old-rejected@example.org")
    old_archived = _add(db, status="archived", age_days=900, email="old-archived@example.org")
    recent_rejected = _add(db, status="rejected", age_days=30, email="recent-rejected@example.org")
    old_pending = _add(db, status="pending", age_days=900, email="old-pending@example.org")
    old_in_review = _add(db, status="in_review", age_days=900, email="old-in-review@example.org")
    old_approved = _add(db, status="approved", age_days=900, email="old-approved@example.org")

    with _conn(db) as conn:
        eligible = eligible_join_request_ids(conn, 730)

    assert eligible == sorted([old_rejected, old_archived])
    for excluded in (recent_rejected, old_pending, old_in_review, old_approved):
        assert excluded not in eligible


def test_zero_retention_disables_the_rule(db):
    _add(db, status="rejected", age_days=5000, email="ancient@example.org")

    with _conn(db) as conn:
        assert eligible_join_request_ids(conn, 0) == []


def test_preview_reports_the_eligible_count_without_deleting(db):
    _add(db, status="rejected", age_days=900, email="a@example.org")
    _add(db, status="archived", age_days=900, email="b@example.org")
    _add(db, status="pending", age_days=900, email="c@example.org")

    preview = safety_operations_preview(_config(db, db.parent, 730))

    assert preview["join_requests"] == {"retention_days": 730, "eligible": 2}
    assert len(_statuses(db)) == 3, "the preview must not delete anything"


# ---------------------------------------------------------------------------
# Cleanup behaviour and safety guarantees
# ---------------------------------------------------------------------------

def test_safe_cleanup_deletes_only_eligible_rows_and_leaves_members(db, monkeypatch):
    from werkzeug.security import generate_password_hash

    with _conn(db) as conn:
        member_id = conn.execute(
            "INSERT INTO members(first_name,last_name,display_name,email) VALUES('Ada','Example','Ada Example','member@example.org')"
        ).lastrowid
        conn.commit()
    approved = _add(db, status="approved", age_days=900, email="approved@example.org")
    with _conn(db) as conn:
        conn.execute("UPDATE join_requests SET member_id=? WHERE id=?", (member_id, approved))
        conn.commit()
    _add(db, status="rejected", age_days=900, email="old-rejected@example.org")
    _add(db, status="archived", age_days=900, email="old-archived@example.org")
    _add(db, status="rejected", age_days=10, email="recent-rejected@example.org")
    _add(db, status="in_review", age_days=900, email="open@example.org")

    report = execute_safe_cleanup(_config(db, db.parent, 730))

    assert report["join_requests_deleted"] == 2
    assert report["join_request_retention_days"] == 730
    remaining = _statuses(db)
    assert remaining == {
        "approved@example.org": "approved",
        "recent-rejected@example.org": "rejected",
        "open@example.org": "in_review",
    }
    with _conn(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM members WHERE id=?", (member_id,)).fetchone()[0] == 1
        assert conn.execute(
            "SELECT member_id FROM join_requests WHERE id=?", (approved,)
        ).fetchone()[0] == member_id


def test_safe_cleanup_keeps_a_verified_backup_before_deleting_join_requests(db, monkeypatch):
    """Deletion must still run behind the normal snapshot guarantee."""
    _add(db, status="rejected", age_days=900, email="old@example.org")
    created: list[str] = []

    from mifp_app.services import safety_operations

    real_backup = safety_operations.backup_sqlite_database

    def spy_backup(*args, **kwargs):
        result = real_backup(*args, **kwargs)
        created.append(str(result))
        return result

    monkeypatch.setattr(safety_operations, "backup_sqlite_database", spy_backup)

    report = execute_safe_cleanup(_config(db, db.parent, 730))

    assert created, "cleanup must create a verified snapshot first"
    assert Path(created[0]).is_file()
    assert report["backup"] == Path(created[0]).name
    assert report["join_requests_deleted"] == 1


def test_safe_cleanup_aborts_when_no_backup_can_be_created(db, monkeypatch):
    _add(db, status="rejected", age_days=900, email="old@example.org")

    from mifp_app.services import safety_operations

    monkeypatch.setattr(safety_operations, "backup_sqlite_database", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="verified safety backup"):
        execute_safe_cleanup(_config(db, db.parent, 730))

    assert len(_statuses(db)) == 1, "nothing may be deleted without a snapshot"


def test_safe_cleanup_is_idempotent(db):
    _add(db, status="rejected", age_days=900, email="old@example.org")
    config = _config(db, db.parent, 730)

    first = execute_safe_cleanup(config)
    second = execute_safe_cleanup(config)

    assert first["join_requests_deleted"] == 1
    assert second["join_requests_deleted"] == 0


def test_cleanup_audit_record_carries_aggregate_counts_only(db, monkeypatch):
    """The operator-facing report must not name applicants."""
    _add(db, status="rejected", age_days=900, email="identifiable@example.org")

    report = execute_safe_cleanup(_config(db, db.parent, 730))

    serialized = repr(report)
    assert "identifiable@example.org" not in serialized
    assert "Ada" not in serialized
    assert isinstance(report["join_requests_deleted"], int)


# ---------------------------------------------------------------------------
# Log retention uses the same protected procedure
# ---------------------------------------------------------------------------

def _log_dir(tmp_path: Path) -> Path:
    path = tmp_path / "logs"
    path.mkdir(exist_ok=True)
    return path


def _write_log(path: Path, age_days: float) -> Path:
    path.write_text("2026-01-01 INFO nothing personal\n", encoding="utf-8")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_only_rotated_application_and_access_logs_are_prunable(tmp_path):
    from mifp_app.runtime_storage import runtime_log_retention_plan

    logs = _log_dir(tmp_path)
    old_rotated_app = _write_log(logs / "mifp_app.log.3", 90)
    old_rotated_jsonl = _write_log(logs / "mifp_app.jsonl.2", 90)
    old_rotated_gz = _write_log(logs / "access.log.4.gz", 90)
    recent_rotated = _write_log(logs / "mifp_app.log.1", 2)
    active = _write_log(logs / "mifp_app.log", 900)
    active_access = _write_log(logs / "access.log", 900)
    exempt = [
        _write_log(logs / f"{stream}.{suffix}.9", 900)
        for stream, suffix in (("errors", "log"), ("audit", "jsonl"), ("security", "log"))
    ]

    victims = runtime_log_retention_plan(logs, max_age_days=30)

    assert set(victims) == {old_rotated_app, old_rotated_jsonl, old_rotated_gz}
    for untouched in (recent_rotated, active, active_access, *exempt):
        assert untouched not in victims


def test_symlinked_log_files_are_never_followed(tmp_path):
    from mifp_app.runtime_storage import runtime_log_retention_plan

    logs = _log_dir(tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text("do not delete\n", encoding="utf-8")
    link = logs / "mifp_app.log.7"
    link.symlink_to(outside)

    assert runtime_log_retention_plan(logs, max_age_days=0) == []
    assert runtime_log_retention_plan(logs, max_age_days=1) == []
    assert outside.is_file()


def test_safe_cleanup_prunes_aged_logs_and_keeps_exempt_streams(db, tmp_path):
    logs = _log_dir(tmp_path)
    old_app = _write_log(logs / "mifp_app.log.5", 90)
    old_access = _write_log(logs / "access.log.5", 90)
    active = _write_log(logs / "mifp_app.log", 900)
    audit = _write_log(logs / "audit.jsonl.5", 900)
    _add(db, status="pending", age_days=1, email="keep@example.org")

    config = _config(db, tmp_path, 730)
    config["LOG_DIR"] = logs
    config["LOG_RETENTION_DAYS"] = 30

    preview = safety_operations_preview(config)
    assert preview["logs"]["prunable"] == 2

    report = execute_safe_cleanup(config)

    assert report["log_files_removed"] == 2
    assert report["log_retention_days"] == 30
    assert not old_app.exists() and not old_access.exists()
    assert active.is_file(), "the active log file must never be deleted"
    assert audit.is_file(), "security-relevant streams are preserved"


def test_zero_log_retention_is_documented_as_disabled(db, tmp_path):
    logs = _log_dir(tmp_path)
    aged = _write_log(logs / "mifp_app.log.4", 5000)
    config = _config(db, tmp_path, 730)
    config["LOG_DIR"] = logs
    config["LOG_RETENTION_DAYS"] = 0

    report = execute_safe_cleanup(config)

    assert report["log_files_removed"] == 0
    assert aged.is_file()

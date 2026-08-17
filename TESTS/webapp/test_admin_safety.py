from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from mifp_app.services.admin_safety import (
    backup_sqlite_database,
    cleanup_backup_copies,
    portability_copy_inventory,
    prune_sqlite_backups,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample (value) VALUES ('ready')")


def test_database_backups_are_limited_to_two(tmp_path: Path) -> None:
    db_path = tmp_path / "mifp.db"
    _database(db_path)

    created = [backup_sqlite_database(db_path, label="test") for _ in range(5)]

    snapshots = sorted((tmp_path / "backups").glob("mifp-test-*.db"))
    assert len(snapshots) == 2
    assert set(snapshots) == set(created[-2:])
    for snapshot in snapshots:
        with sqlite3.connect(snapshot) as conn:
            assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_backup_retention_preserves_unrecognized_and_other_database_files(tmp_path: Path) -> None:
    db_path = tmp_path / "mifp.db"
    _database(db_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    manual = backup_dir / "manual-before-release.db"
    manual.touch()
    other_database = backup_dir / "other-admin-20260728-120000-000000.db"
    other_database.touch()

    for _ in range(4):
        backup_sqlite_database(db_path, label="import")

    assert manual.exists()
    assert other_database.exists()
    assert len(list(backup_dir.glob("mifp-import-*.db"))) == 2


def test_backup_pruning_can_remove_all_automatic_snapshots(tmp_path: Path) -> None:
    db_path = tmp_path / "mifp.db"
    _database(db_path)
    backup = backup_sqlite_database(db_path)

    removed = prune_sqlite_backups(db_path, keep=0)

    assert removed == [backup]
    assert not backup.exists()


def test_backup_is_refused_when_database_copy_would_cross_reserve(tmp_path: Path) -> None:
    db_path = tmp_path / "mifp.db"
    _database(db_path)

    with pytest.raises(RuntimeError, match="Insufficient storage space"):
        backup_sqlite_database(db_path, reserve_bytes=10**18)

    assert not list((tmp_path / "backups").glob("*.db"))


def _portability_copy(root: Path, digest: str, *, created_at: float) -> tuple[Path, Path]:
    data = root / f".portability-{digest}.bin"
    metadata = root / f".portability-{digest}.json"
    data.write_bytes(b"portable-data")
    metadata.write_text(
        json.dumps({
            "data_name": data.name,
            "filename": "MIFP_EXPORT.zip",
            "mimetype": "application/zip",
            "bytes": data.stat().st_size,
            "created_at": created_at,
        }),
        encoding="utf-8",
    )
    os.utime(data, (created_at, created_at))
    os.utime(metadata, (created_at, created_at))
    return metadata, data


def test_portability_cleanup_removes_only_expired_app_owned_copies(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    now = time.time()
    expired = _portability_copy(export_dir, "a" * 64, created_at=now - 600)
    active = _portability_copy(export_dir, "b" * 64, created_at=now)
    unknown = export_dir / "manual-export.zip"
    unknown.write_bytes(b"keep")

    inventory = portability_copy_inventory(export_dir, now=now)
    report = cleanup_backup_copies(
        tmp_path / "mifp.db",
        export_dir,
        database=False,
        portability=True,
    )

    assert inventory["total"] == 2
    assert inventory["active"] == 1
    assert inventory["removable"] == 1
    assert report["portability"]["copies"] == 1
    assert all(not path.exists() for path in expired)
    assert all(path.exists() for path in active)
    assert unknown.exists()


def test_recently_stale_download_claim_is_preserved_for_slow_clients(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    now = time.time()
    digest = "d" * 64
    data = export_dir / f".portability-{digest}.bin"
    claim = export_dir / f".portability-{digest}.1234abcd.claim"
    data.write_bytes(b"download-in-progress")
    claim.write_text(json.dumps({
        "data_name": data.name,
        "filename": "large-export.zip",
        "created_at": now - 600,
    }), encoding="utf-8")
    os.utime(data, (now - 600, now - 600))
    os.utime(claim, (now - 600, now - 600))

    inventory = portability_copy_inventory(export_dir, now=now)
    report = cleanup_backup_copies(
        tmp_path / "mifp.db",
        export_dir,
        database=False,
        portability=True,
    )

    assert inventory["items"][0]["status"] == "downloading"
    assert inventory["removable"] == 0
    assert report["portability"]["copies"] == 0
    assert data.exists()
    assert claim.exists()


def test_database_cleanup_creates_verified_replacement_before_removal(tmp_path: Path) -> None:
    db_path = tmp_path / "mifp.db"
    _database(db_path)
    first = backup_sqlite_database(db_path, label="first")
    second = backup_sqlite_database(db_path, label="second")
    assert first and second

    report = cleanup_backup_copies(
        db_path,
        tmp_path / "exports",
        database=True,
        portability=False,
    )

    snapshots = list((tmp_path / "backups").glob("*.db"))
    assert len(snapshots) == 1
    assert snapshots[0].name == report["database"]["created"]
    assert report["database"]["copies"] == 2
    with sqlite3.connect(f"file:{snapshots[0]}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert conn.execute("SELECT value FROM sample").fetchone() == ("ready",)

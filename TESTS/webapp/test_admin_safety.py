from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mifp_app.services.admin_safety import backup_sqlite_database, prune_sqlite_backups


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

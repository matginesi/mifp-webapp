from __future__ import annotations

import os
from pathlib import Path

import pytest


def _layout(root: Path):
    from mifp_app.runtime_storage import RuntimeStorage

    return RuntimeStorage(
        database=root / "data" / "mifp.db",
        assets=root / "data" / "assets",
        exports=root / "data" / "exports",
        logs=root / "logs",
    )


def test_runtime_storage_prepares_all_directories_and_sqlite_wal(tmp_path: Path) -> None:
    from mifp_app.runtime_storage import prepare_runtime_storage

    layout = _layout(tmp_path)
    prepare_runtime_storage(layout, require_database=False)

    assert all(path.is_dir() for path in layout.directories())
    assert not layout.database.exists()
    assert not list(layout.database.parent.glob(".mifp-*"))


def test_runtime_storage_requires_existing_production_database(tmp_path: Path) -> None:
    from mifp_app.runtime_storage import prepare_runtime_storage

    with pytest.raises(RuntimeError, match="Production database is missing"):
        prepare_runtime_storage(_layout(tmp_path), require_database=True)


def test_runtime_storage_accepts_existing_database_without_modifying_it(tmp_path: Path) -> None:
    import sqlite3

    from mifp_app.runtime_storage import prepare_runtime_storage

    layout = _layout(tmp_path)
    layout.database.parent.mkdir(parents=True)
    with sqlite3.connect(layout.database) as connection:
        connection.execute("CREATE TABLE preserved(value TEXT)")
        connection.execute("INSERT INTO preserved(value) VALUES ('yes')")

    prepare_runtime_storage(layout, require_database=True)

    with sqlite3.connect(layout.database) as connection:
        assert connection.execute("SELECT value FROM preserved").fetchone()[0] == "yes"


def test_runtime_storage_rejects_file_in_place_of_directory(tmp_path: Path) -> None:
    from mifp_app.runtime_storage import prepare_runtime_storage

    layout = _layout(tmp_path)
    layout.logs.parent.mkdir(parents=True, exist_ok=True)
    layout.logs.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="is not a directory"):
        prepare_runtime_storage(layout, require_database=False)


def test_production_storage_permissions_are_hardened(tmp_path: Path) -> None:
    import sqlite3

    from mifp_app.runtime_storage import prepare_runtime_storage

    layout = _layout(tmp_path)
    for directory in layout.directories():
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o777)
    with sqlite3.connect(layout.database) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
    export = layout.exports / "private.zip"
    export.write_bytes(b"private")
    export.chmod(0o666)

    prepare_runtime_storage(
        layout,
        require_database=True,
        probe_writes=False,
        harden_permissions=True,
    )

    assert layout.database.stat().st_mode & 0o777 == 0o640
    assert export.stat().st_mode & 0o777 == 0o640
    assert all(path.stat().st_mode & 0o777 == 0o750 for path in layout.directories())


def test_storage_reserve_blocks_writes_before_disk_is_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import namedtuple

    from mifp_app.runtime_storage import require_free_space

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("mifp_app.runtime_storage.shutil.disk_usage", lambda _path: usage(1000, 900, 100))

    with pytest.raises(RuntimeError, match="Insufficient storage space"):
        require_free_space(tmp_path, operation_bytes=30, reserve_bytes=80)


def test_export_retention_keeps_newest_files_within_all_limits(tmp_path: Path) -> None:
    from mifp_app.runtime_storage import prune_runtime_exports

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    files = []
    for index in range(4):
        path = export_dir / f"export-{index}.zip"
        path.write_bytes(b"x" * 10)
        os.utime(path, (100 + index, 100 + index))
        files.append(path)
    unrelated_directory = export_dir / "preserved"
    unrelated_directory.mkdir()

    removed = prune_runtime_exports(
        export_dir,
        max_files=2,
        max_bytes=25,
        max_age_days=0,
    )

    assert set(removed) == {files[0], files[1]}
    assert files[2].exists() and files[3].exists()
    assert unrelated_directory.is_dir()

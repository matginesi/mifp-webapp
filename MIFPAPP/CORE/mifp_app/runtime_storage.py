from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeStorage:
    database: Path
    assets: Path
    exports: Path
    logs: Path

    @property
    def backups(self) -> Path:
        return self.database.parent / "backups"

    def directories(self) -> tuple[Path, ...]:
        return (
            self.database.parent,
            self.assets,
            self.exports,
            self.backups,
            self.logs,
        )


def available_bytes(path: Path) -> int:
    """Return space available to the current user on the containing filesystem."""
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return int(shutil.disk_usage(candidate).free)


def require_free_space(path: Path, *, operation_bytes: int = 0, reserve_bytes: int = 0) -> int:
    """Fail before a write if it would consume the configured safety reserve."""
    free = available_bytes(path)
    required = max(0, int(operation_bytes)) + max(0, int(reserve_bytes))
    if free < required:
        raise RuntimeError(
            "Insufficient storage space: "
            f"{free // (1024 * 1024)} MiB available, "
            f"{required // (1024 * 1024)} MiB required including safety reserve"
        )
    return free


def runtime_export_retention_plan(
    export_dir: Path,
    *,
    max_files: int,
    max_bytes: int,
    max_age_days: int,
) -> list[Path]:
    """List generated exports outside the configured retention policy."""
    export_dir = Path(export_dir)
    if not export_dir.is_dir():
        return []
    now = time.time()
    entries = sorted(
        (
            path
            for path in export_dir.iterdir()
            if path.is_file() and not path.is_symlink()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    kept_bytes = 0
    kept_files = 0
    candidates: list[Path] = []
    for path in entries:
        stat = path.stat()
        expired = max_age_days > 0 and now - stat.st_mtime > max_age_days * 86400
        over_count = max_files >= 0 and kept_files >= max_files
        over_size = max_bytes >= 0 and kept_bytes + stat.st_size > max_bytes
        if expired or over_count or over_size:
            candidates.append(path)
        else:
            kept_bytes += stat.st_size
            kept_files += 1
    return candidates


def prune_runtime_exports(
    export_dir: Path,
    *,
    max_files: int,
    max_bytes: int,
    max_age_days: int,
) -> list[Path]:
    """Bound generated exports by age, count and total size."""
    removed: list[Path] = []
    for path in runtime_export_retention_plan(
        export_dir,
        max_files=max_files,
        max_bytes=max_bytes,
        max_age_days=max_age_days,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(path)
    return removed


def _assert_safe_directory(path: Path, label: str) -> None:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise RuntimeError(f"{label} cannot be the filesystem root: {path}")
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"{label} is not a directory: {path}")


def _probe_directory(path: Path, label: str) -> None:
    try:
        descriptor, probe_name = tempfile.mkstemp(prefix=".mifp-write-", dir=path)
        try:
            os.write(descriptor, b"ok")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            Path(probe_name).unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"{label} is not writable: {path}") from exc


def _probe_sqlite_wal(directory: Path) -> None:
    descriptor, probe_name = tempfile.mkstemp(prefix=".mifp-sqlite-", suffix=".db", dir=directory)
    os.close(descriptor)
    probe = Path(probe_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(probe, timeout=5)
        journal_mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if journal_mode != "wal":
            raise RuntimeError(
                f"SQLite WAL is not supported in the database directory: {directory}"
            )
        connection.execute("CREATE TABLE storage_probe(value INTEGER NOT NULL)")
        connection.execute("INSERT INTO storage_probe(value) VALUES (1)")
        connection.commit()
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError(f"SQLite storage probe failed in: {directory}")
    except sqlite3.Error as exc:
        raise RuntimeError(f"SQLite cannot write safely in: {directory}") from exc
    finally:
        if connection is not None:
            connection.close()
        probe.unlink(missing_ok=True)
        Path(f"{probe}-wal").unlink(missing_ok=True)
        Path(f"{probe}-shm").unlink(missing_ok=True)


def prepare_runtime_storage(
    storage: RuntimeStorage,
    *,
    require_database: bool,
    probe_writes: bool = True,
    harden_permissions: bool = False,
    minimum_free_bytes: int = 0,
    export_max_files: int | None = None,
    export_max_bytes: int | None = None,
    export_retention_days: int | None = None,
) -> None:
    """Create and validate every persistent runtime path before Flask starts.

    The SQLite parent must be writable because WAL and SHM files live beside
    the database. Probes use disposable files and never mutate the real DB.
    """
    if storage.database.exists() and not storage.database.is_file():
        raise RuntimeError(f"DATABASE_PATH is not a regular file: {storage.database}")
    if require_database and not storage.database.is_file():
        raise RuntimeError(
            f"Production database is missing: {storage.database}. "
            "Restore or provision it before starting the application."
        )

    unique_directories: list[Path] = []
    seen: set[Path] = set()
    for directory in storage.directories():
        _assert_safe_directory(directory, "Runtime storage path")
        resolved = directory.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_directories.append(directory)

    for directory in unique_directories:
        try:
            directory.mkdir(mode=0o750, parents=True, exist_ok=True)
            if harden_permissions and not directory.is_symlink():
                directory.chmod(0o750)
        except OSError as exc:
            raise RuntimeError(f"Cannot create runtime storage directory: {directory}") from exc

    if export_max_files is not None and export_max_bytes is not None and export_retention_days is not None:
        prune_runtime_exports(
            storage.exports,
            max_files=export_max_files,
            max_bytes=export_max_bytes,
            max_age_days=export_retention_days,
        )

    # The safety reserve protects durable data. Temporary export/log tmpfs
    # mounts can intentionally be smaller and are bounded independently.
    durable_directories = {
        storage.database.parent.resolve(),
        storage.assets.resolve(),
        storage.backups.resolve(),
    }
    for directory in unique_directories:
        if directory.resolve() in durable_directories:
            require_free_space(directory, reserve_bytes=minimum_free_bytes)

    if harden_permissions:
        protected_roots = (storage.exports, storage.backups, storage.logs)
        protected_files = [storage.database] if storage.database.is_file() else []
        for root in protected_roots:
            protected_files.extend(
                path for path in root.iterdir()
                if path.is_file() and not path.is_symlink()
            )
        try:
            for protected_file in protected_files:
                protected_file.chmod(0o640)
        except OSError as exc:
            raise RuntimeError(f"Cannot harden runtime file permissions: {protected_file}") from exc

    if not probe_writes:
        return

    for directory in unique_directories:
        _probe_directory(directory, "Runtime storage directory")
    _probe_sqlite_wal(storage.database.parent)

    if storage.database.exists():
        try:
            descriptor = os.open(storage.database, os.O_RDWR)
        except OSError as exc:
            raise RuntimeError(f"Database is not writable: {storage.database}") from exc
        else:
            os.close(descriptor)

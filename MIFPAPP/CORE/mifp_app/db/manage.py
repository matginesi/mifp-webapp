"""Small offline SQLite maintenance CLI used by local tools and production preflight."""
from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path

from .migrations import migrate_content_schema
from .runtime_check import validate_runtime_database


def _self_contained(path: Path) -> None:
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()
    Path(str(path) + "-wal").unlink(missing_ok=True)
    Path(str(path) + "-shm").unlink(missing_ok=True)


def init_database(path: Path) -> None:
    path = path.resolve()
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        conn = sqlite3.connect(temporary, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            migrate_content_schema(conn)
        finally:
            conn.close()
        _self_contained(temporary)
        validate_runtime_database(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def upgrade_copy(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"source database is not a regular file: {source}")
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(temporary, timeout=30)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()
        conn = sqlite3.connect(temporary, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            migrate_content_schema(conn)
        finally:
            conn.close()
        _self_contained(temporary)
        validate_runtime_database(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="create a fresh schema-only MIFP database")
    p_init.add_argument("path", type=Path)
    p_check = sub.add_parser("check", help="validate a runtime database")
    p_check.add_argument("path", type=Path)
    p_upgrade = sub.add_parser("upgrade-copy", help="migrate a copy, never the source database")
    p_upgrade.add_argument("source", type=Path)
    p_upgrade.add_argument("destination", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        init_database(args.path)
        print(f"Created: {args.path}")
    elif args.command == "check":
        validate_runtime_database(args.path)
        print(f"Database OK: {args.path}")
    else:
        upgrade_copy(args.source, args.destination)
        print(f"Upgraded copy: {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

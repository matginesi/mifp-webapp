from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mifp_app.db.connection import connect, write_transaction
from mifp_app.db.manage import init_database


def test_runtime_connect_never_creates_missing_database(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    with pytest.raises(RuntimeError, match="existing regular file"):
        connect(path)
    assert not path.exists()


def test_runtime_connect_opens_existing_database_read_write(tmp_path: Path) -> None:
    path = tmp_path / "mifp.db"
    init_database(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES('runtime-test','ok')")
        conn.commit()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='runtime-test'").fetchone()[0] == "ok"


def test_write_transaction_commits_and_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "mifp.db"
    init_database(path)
    with connect(path) as conn:
        with write_transaction(conn, operation="test commit"):
            conn.execute("INSERT INTO settings(key,value) VALUES('committed','yes')")
        with pytest.raises(ValueError):
            with write_transaction(conn, operation="test rollback"):
                conn.execute("INSERT INTO settings(key,value) VALUES('rolled-back','no')")
                raise ValueError("boom")
        assert conn.execute("SELECT value FROM settings WHERE key='committed'").fetchone()[0] == "yes"
        assert conn.execute("SELECT 1 FROM settings WHERE key='rolled-back'").fetchone() is None

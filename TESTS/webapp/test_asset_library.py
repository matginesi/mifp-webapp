from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


def _insert_asset(conn, asset_id, *, path, kind="image", storage_status="local", is_external=0, source_url=None, checksum=None, original_filename=None, size=None):
    conn.execute(
        """INSERT INTO assets(id, filename, original_filename, path, kind, storage_status, is_external, source_url, checksum, size)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (asset_id, f"file-{asset_id}", original_filename or f"file-{asset_id}", path, kind, storage_status, is_external, source_url, checksum, size),
    )


class TestReconcileAssetStorageStatus:
    def test_marks_local_when_file_present(self, tmp_path):
        from mifp_app.services.assets import reconcile_asset_storage_status

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "image").mkdir(parents=True)
        (assets_dir / "image" / "logo.png").write_bytes(b"png-bytes")
        _insert_asset(conn, 1, path="image/logo.png", storage_status="missing")

        result = reconcile_asset_storage_status(conn, assets_dir)

        assert result["updated"] == 1
        assert result["local"] == 1
        assert result["missing"] == 0
        assert conn.execute("SELECT storage_status FROM assets WHERE id=1").fetchone()[0] == "local"

    def test_marks_external_when_is_external_flag(self, tmp_path):
        from mifp_app.services.assets import reconcile_asset_storage_status

        conn = _conn()
        assets_dir = tmp_path / "assets"
        _insert_asset(conn, 1, path="external/ref.pdf", kind="pdf", storage_status="missing", is_external=1)

        result = reconcile_asset_storage_status(conn, assets_dir)

        assert result["external"] == 1
        assert conn.execute("SELECT storage_status FROM assets WHERE id=1").fetchone()[0] == "external"

    def test_marks_external_when_path_has_external_prefix(self, tmp_path):
        from mifp_app.services.assets import reconcile_asset_storage_status

        conn = _conn()
        assets_dir = tmp_path / "assets"
        _insert_asset(conn, 1, path="external/ref.pdf", kind="pdf", storage_status="missing", is_external=0)

        result = reconcile_asset_storage_status(conn, assets_dir)

        assert result["external"] == 1
        assert conn.execute("SELECT storage_status FROM assets WHERE id=1").fetchone()[0] == "external"

    def test_marks_missing_when_file_absent(self, tmp_path):
        from mifp_app.services.assets import reconcile_asset_storage_status

        conn = _conn()
        assets_dir = tmp_path / "assets"
        _insert_asset(conn, 1, path="image/gone.png", storage_status="local")

        result = reconcile_asset_storage_status(conn, assets_dir)

        assert result["missing"] == 1
        assert conn.execute("SELECT storage_status FROM assets WHERE id=1").fetchone()[0] == "missing"

    def test_clears_recovery_state_for_now_local_assets(self, tmp_path):
        from mifp_app.services.assets import reconcile_asset_storage_status

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "image").mkdir(parents=True)
        (assets_dir / "image" / "logo.png").write_bytes(b"png-bytes")
        _insert_asset(conn, 1, path="image/logo.png", storage_status="missing")
        conn.execute("INSERT INTO asset_recovery_state(asset_id, attempts, terminal) VALUES(1, 3, 1)")

        reconcile_asset_storage_status(conn, assets_dir)

        assert conn.execute("SELECT COUNT(*) FROM asset_recovery_state WHERE asset_id=1").fetchone()[0] == 0

    def test_leaves_correct_rows_untouched(self, tmp_path):
        from mifp_app.services.assets import reconcile_asset_storage_status

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "image").mkdir(parents=True)
        (assets_dir / "image" / "ok.png").write_bytes(b"png-bytes")
        _insert_asset(conn, 1, path="image/ok.png", storage_status="local")
        _insert_asset(conn, 2, path="external/ref.pdf", kind="pdf", storage_status="external", is_external=1)

        result = reconcile_asset_storage_status(conn, assets_dir)

        assert result["updated"] == 0
        assert result["local"] == 1
        assert result["external"] == 1


class TestAssetLibrarySummary:
    def test_counts_used_unused_and_external(self, tmp_path):
        from mifp_app.services.asset_cleanup import asset_library_summary

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "image").mkdir(parents=True)
        (assets_dir / "image" / "a.png").write_bytes(b"a")
        _insert_asset(conn, 1, path="image/a.png", storage_status="local", source_url="https://src/a.png")
        conn.execute("INSERT INTO news(id, slug, title) VALUES(1, 'n', 'News')")
        conn.execute("INSERT INTO asset_links(asset_id, entity_type, entity_id, role) VALUES(1, 'news', 1, 'cover')")
        _insert_asset(conn, 2, path="external/ref.pdf", kind="pdf", storage_status="missing", is_external=1, source_url="https://x/ref.pdf")

        summary = asset_library_summary(conn, assets_dir)

        assert summary["total"] == 2
        assert summary["used"] == 1
        assert summary["unused"] == 1
        assert summary["external"] == 1
        assert summary["missing"] == 0

    def test_missing_split_into_recoverable_and_errors(self, tmp_path):
        from mifp_app.services.asset_cleanup import asset_library_summary

        conn = _conn()
        assets_dir = tmp_path / "assets"
        _insert_asset(conn, 1, path="image/gone1.png", storage_status="local", source_url="https://src/gone1.png")
        _insert_asset(conn, 2, path="image/gone2.png", storage_status="local", source_url=None)
        conn.execute("INSERT INTO asset_recovery_state(asset_id, terminal) VALUES(1, 0)")

        summary = asset_library_summary(conn, assets_dir)

        assert summary["missing"] == 2
        assert summary["recoverable"] == 1
        assert summary["errors"] == 1
        assert summary["recoverable_ids"] == {1}
        assert summary["error_ids"] == {2}

    def test_terminal_missing_is_error_and_deferred_tracked(self, tmp_path):
        from mifp_app.services.asset_cleanup import asset_library_summary

        conn = _conn()
        assets_dir = tmp_path / "assets"
        future = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        _insert_asset(conn, 1, path="image/term.png", storage_status="local", source_url="https://src/term.png")
        conn.execute("INSERT INTO asset_recovery_state(asset_id, terminal) VALUES(1, 1)")
        _insert_asset(conn, 2, path="image/defer.png", storage_status="local", source_url="https://src/defer.png")
        conn.execute("INSERT INTO asset_recovery_state(asset_id, terminal, next_attempt_at) VALUES(2, 0, ?)", (future,))

        summary = asset_library_summary(conn, assets_dir)

        assert summary["missing"] == 2
        assert summary["errors"] == 1
        assert summary["recoverable"] == 1
        assert summary["terminal"] == 1
        assert summary["deferred"] == 1

    def test_counts_metadata_and_duplicates(self, tmp_path):
        from mifp_app.services.asset_cleanup import asset_library_summary

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "image").mkdir(parents=True)
        _insert_asset(conn, 1, path="image/a.png", storage_status="local", original_filename="same.png", size=100)
        _insert_asset(conn, 2, path="image/b.png", storage_status="local", original_filename="same.png", size=100)
        _insert_asset(conn, 3, path="image/c.png", storage_status="local", original_filename="same.png", size=200)
        _insert_asset(conn, 4, path="image/d.png", storage_status="local", checksum=None)

        summary = asset_library_summary(conn, assets_dir)

        assert 1 in summary["duplicate_ids"]
        assert 2 in summary["duplicate_ids"]
        assert summary["duplicates"] == 2
        assert 4 in summary["metadata_ids"]
        assert summary["metadata"] >= 1

    def test_counts_orphan_files_on_disk(self, tmp_path):
        from mifp_app.services.asset_cleanup import asset_library_summary

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "image").mkdir(parents=True)
        (assets_dir / "image" / "a.png").write_bytes(b"a")
        (assets_dir / "orphan.txt").write_bytes(b"orphan")
        _insert_asset(conn, 1, path="image/a.png", storage_status="local")

        summary = asset_library_summary(conn, assets_dir)

        assert summary["orphan_count"] == 1
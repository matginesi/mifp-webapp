from __future__ import annotations

import json
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import pytest


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


class TestAssetCleanup:
    def test_build_asset_export_plan(self, tmp_path):
        from mifp_app.services.asset_cleanup import build_asset_export_plan

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "pdf").mkdir(parents=True)
        (assets_dir / "pdf" / "paper.pdf").write_bytes(b"pdf-content")
        conn.execute("INSERT INTO assets(id, filename, path, kind, checksum) VALUES(1,'paper.pdf','pdf/paper.pdf','pdf','sha1')")
        conn.execute("INSERT INTO assets(id, filename, path, kind, checksum, storage_status) VALUES(2,'missing.pdf','pdf/missing.pdf','pdf','sha2','missing')")

        local_files, missing = build_asset_export_plan(conn, assets_dir)
        assert len(local_files) == 1
        assert local_files[0]["filename"] == "paper.pdf"
        assert local_files[0]["file_included"] is True
        assert len(missing) == 1  # the missing one + external

    def test_export_assets_to_zip_roundtrip(self, tmp_path):
        from mifp_app.services.asset_cleanup import export_assets_to_zip, import_assets_from_zip, extract_zip_manifest

        conn = _conn()
        assets_dir = tmp_path / "assets"
        (assets_dir / "pdf").mkdir(parents=True)
        (assets_dir / "pdf" / "paper.pdf").write_bytes(b"pdf-content")
        conn.execute("INSERT INTO assets(id, filename, path, kind, checksum, storage_status) VALUES(1,'paper.pdf','pdf/paper.pdf','pdf','sha1','local')")

        zip_path = export_assets_to_zip(conn, assets_dir)
        assert zip_path is not None
        assert zip_path.exists()

        # Verify manifest
        manifest = extract_zip_manifest(zip_path)
        assert manifest.version == 1
        assert len(manifest.assets) == 1
        assert manifest.assets[0]["filename"] == "paper.pdf"

        # Import into clean DB
        target_conn = _conn()
        target_assets = tmp_path / "imported"
        target_assets.mkdir(parents=True)
        result = import_assets_from_zip(target_conn, target_assets, zip_path, dry_run=False)
        assert result["inserted"] == 1
        assert result["errors"] == []
        # Re-import should skip
        result2 = import_assets_from_zip(target_conn, target_assets, zip_path, dry_run=False)
        assert result2["skipped"] == 1

    def test_import_assets_from_zip_zip_slip_rejected(self, tmp_path):
        from mifp_app.services.asset_cleanup import import_assets_from_zip

        conn = _conn()
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir(parents=True)

        # Create a ZIP with path traversal in manifest
        manifest = {
            "version": 1,
            "exported_at": "2024-01-01T00:00:00",
            "export_type": "full",
            "assets": [{"filename": "evil.txt", "path": "../evil.txt", "kind": "other", "file_included": True}],
        }
        zip_path = tmp_path / "slip.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("files/../evil.txt", b"evil")

        with pytest.raises(ValueError, match="Zip Slip"):
            import_assets_from_zip(conn, assets_dir, zip_path, dry_run=False)

    def test_import_assets_from_zip_rejects_duplicate_members(self, tmp_path):
        from mifp_app.services.asset_cleanup import extract_zip_manifest

        zip_path = tmp_path / "duplicate.zip"
        manifest = json.dumps({"version": 1, "assets": []})
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", manifest)
            with pytest.warns(UserWarning, match="Duplicate name: 'manifest.json'"):
                zf.writestr("manifest.json", manifest)

        with pytest.raises(ValueError, match="duplicate file name"):
            extract_zip_manifest(zip_path)

    def test_import_assets_from_jsonl_metadata(self, tmp_path):
        from mifp_app.services.asset_cleanup import import_assets_from_jsonl

        conn = _conn()
        jsonl_path = tmp_path / "assets.jsonl"
        jsonl_path.write_text(
            json.dumps({"filename": "logo.png", "path": "image/logo.png", "kind": "image", "checksum": "logo-sha", "source_url": ""}) + "\n",
            encoding="utf-8",
        )

        result = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
        assert result["inserted"] == 1
        assert result["errors"] == []

        # Re-import skips
        result2 = import_assets_from_jsonl(conn, jsonl_path, dry_run=False)
        assert result2["skipped"] == 1

    def test_import_assets_from_jsonl_rejects_unsafe_path(self, tmp_path):
        from mifp_app.services.asset_cleanup import import_assets_from_jsonl

        jsonl_path = tmp_path / "unsafe.jsonl"
        jsonl_path.write_text(json.dumps({"path": "../outside.pdf"}) + "\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Zip Slip"):
            import_assets_from_jsonl(_conn(), jsonl_path)

    def test_build_asset_cleanup_plan(self, tmp_path):
        from mifp_app.services.asset_cleanup import build_asset_cleanup_plan

        conn = _conn()
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir(parents=True)
        (assets_dir / "used.pdf").write_bytes(b"used")
        (assets_dir / "orphan.txt").write_bytes(b"orphan")
        conn.execute("INSERT INTO assets(id, filename, path, kind, checksum, storage_status) VALUES(1,'used.pdf','used.pdf','pdf','sha1','local')")
        conn.execute("INSERT INTO assets(id, filename, path, kind, checksum, storage_status) VALUES(2,'missing.pdf','missing.pdf','pdf','sha2','local')")

        plan = build_asset_cleanup_plan(conn, assets_dir)
        # Asset 2 is missing from disk but in DB
        missing_ids = [a["id"] for a in plan.missing_file_assets]
        assert 2 in missing_ids
        # orphan.txt is on disk but not in DB
        orphan_paths = [o["path"] for o in plan.orphan_files]
        assert "orphan.txt" in orphan_paths

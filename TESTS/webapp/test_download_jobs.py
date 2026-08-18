from __future__ import annotations

from pathlib import Path
import time
import json

import pytest

from mifp_app.services.download_jobs import (
    claim_download,
    get_download_job_status,
    prune,
    submit_download_job,
)


@pytest.fixture
def app(tmp_path: Path):
    import os
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "CONFERENCES_DIR": str(tmp_path / "conferences"),
            "LOG_DIR": str(tmp_path / "logs"),
            "SECRET_KEY": "assets-page-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH="pbkdf2:sha256:260000$example_hash",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    with connect(app.config["DATABASE_PATH"]) as conn:
        migrate_content_schema(conn)
    yield app


def _build(path: Path) -> dict:
    path.write_bytes(b"test-payload")
    return {
        "filename": "test-artifact.bin",
        "mimetype": "application/octet-stream",
        "bytes": len(path.read_bytes()),
    }


def test_submit_and_download_roundtrip(app, tmp_path):
    from mifp_app.services.job_manager import reset_job_manager, get_job_manager
    import time
    with app.app_context():
        job_id, token = submit_download_job(
            name="test-job",
            owner="test-owner",
            session_key="test-session",
            build=lambda path, progress: _build(path),
        )
        time.sleep(0.2)
        status = get_download_job_status(job_id)
        assert status["status"] == "ready"
        assert status["percent"] == 100

        meta, data_path = claim_download(token, owner="test-owner", session_key="test-session")
        assert meta["filename"] == "test-artifact.bin"
        assert meta["bytes"] == 12
        assert data_path.exists()
        assert data_path.read_bytes() == b"test-payload"

        result = claim_download(token, owner="test-owner", session_key="test-session")
        assert result is None


def test_claim_rejects_wrong_owner_and_session(app, tmp_path):
    from mifp_app.services.job_manager import reset_job_manager
    with app.app_context():
        job_id, token = submit_download_job(
            name="test-job",
            owner="test-owner",
            session_key="test-session",
            build=lambda path, progress: _build(tmp_path),
        )
        result = claim_download(token, owner="wrong-owner", session_key="test-session")
        assert result is None

        result = claim_download(token, owner="test-owner", session_key="wrong-session")
        assert result is None


def test_failed_build_reports_failure(app, tmp_path):
    from mifp_app.services.job_manager import reset_job_manager
    with app.app_context():
        def failing_build(path, progress):
            raise RuntimeError("build failed")

        job_id, token = submit_download_job(
            name="failing-job",
            owner="test-owner",
            session_key="test-session",
            build=failing_build,
        )
        import time
        time.sleep(0.2)
        status = get_download_job_status(job_id)
        assert status["status"] == "failed"
        assert "build failed" in status["message"]

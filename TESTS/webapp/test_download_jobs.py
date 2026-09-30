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
import mifp_app.services.download_jobs as download_jobs


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
    from mifp_app.db.manage import init_database

    init_database(Path(app.config["DATABASE_PATH"]))
    yield app


def _build(path: Path) -> dict:
    path.write_bytes(b"test-payload")
    return {
        "filename": "test-artifact.bin",
        "mimetype": "application/octet-stream",
        "bytes": len(path.read_bytes()),
    }


def test_submit_and_download_roundtrip(app, tmp_path):
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


def test_prune_caps_unclaimed_download_artifacts(app):
    with app.app_context():
        root = Path(app.config["EXPORT_DIR"])
        now = time.time()
        for index in range(download_jobs._DL_MAX_CACHED + 3):
            stem = f"{download_jobs._DL_CACHE_PREFIX}{index}"
            (root / f"{stem}.bin").write_bytes(b"x")
            (root / f"{stem}.json").write_text(
                json.dumps({"created_at": now + index}), encoding="utf-8"
            )

        removed = prune()

        assert removed == 3
        assert len(list(root.glob(f"{download_jobs._DL_CACHE_PREFIX}*.json"))) == download_jobs._DL_MAX_CACHED
        assert len(list(root.glob(f"{download_jobs._DL_CACHE_PREFIX}*.bin"))) == download_jobs._DL_MAX_CACHED


def test_control_download_removes_claimed_artifact_when_response_closes(app, monkeypatch):
    artifact = Path(app.config["EXPORT_DIR"]) / "claimed.bin"
    artifact.write_bytes(b"download")
    monkeypatch.setattr(
        "mifp_app.routes.dashboard_control.download_jobs.claim_download",
        lambda **_kwargs: (
            {"filename": "download.bin", "mimetype": "application/octet-stream"},
            artifact,
        ),
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"

    response = client.get("/dashboard/control/safety-operations/dl/token")
    assert response.status_code == 200
    assert response.data == b"download"
    response.close()

    assert not artifact.exists()

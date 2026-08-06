from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_local_docker_uses_host_data_and_source_mounts() -> None:
    root = _repo_root()
    compose = yaml.safe_load((root / "MIFPAPP/CORE/compose.yaml").read_text(encoding="utf-8"))
    web = compose["services"]["web"]
    volumes = web["volumes"]

    assert web["environment"]["FLASK_ENV"] == "development"
    assert web["environment"]["DATABASE_PATH"] == "/app/data/mifp.db"
    assert any(volume.get("target") == "/app/data" for volume in volumes)
    assert any(volume.get("target") == "/app/mifp_app" for volume in volumes)
    source_mounts = [v for v in volumes if v.get("target", "").startswith("/app/") and v.get("target") != "/app/data"]
    assert source_mounts
    assert all(volume.get("read_only") is True for volume in source_mounts)


def test_production_uses_named_volume_and_non_root_web() -> None:
    root = _repo_root()
    compose = yaml.safe_load(
        (root / "MIFPAPP/CORE/compose.production.yaml").read_text(encoding="utf-8")
    )
    web = compose["services"]["web"]
    init = compose["services"]["storage-init"]

    assert init["user"] == "0:0"
    assert "mifp-data:/data" in init["volumes"]
    assert "mifp-data:/app/data" in web["volumes"]
    assert web["read_only"] is True
    assert web["environment"]["FLASK_ENV"] == "production"
    assert web["environment"]["AUTO_MIGRATE_ON_STARTUP"] == "0"


def test_container_entrypoint_migrates_before_startup() -> None:
    root = _repo_root()
    entrypoint = (root / "MIFPAPP/CORE/docker-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (root / "MIFPAPP/CORE/Dockerfile").read_text(encoding="utf-8")

    assert "mifp_archive.cli migrate" in entrypoint
    assert "mifp_archive.cli health" in entrypoint
    assert 'COPY --chown=10001:10001 mifp_archive ./mifp_archive' in dockerfile
    assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in dockerfile


def test_launcher_has_exactly_three_start_commands() -> None:
    root = _repo_root()
    launcher = (root / "mifp").read_text(encoding="utf-8")

    assert "local|start) start_local" in launcher
    assert "docker) start_docker" in launcher
    assert "production|prod) start_production" in launcher
    assert "docker dev" not in launcher
    assert "docker prod" not in launcher


def test_admin_change_recreates_running_docker_web_service() -> None:
    root = _repo_root()
    launcher = (root / "mifp").read_text(encoding="utf-8")

    assert "apply_admin_to_running_services" in launcher
    assert "compose_local up -d --no-deps --force-recreate web" in launcher
    assert "compose_production up -d --no-deps --force-recreate web" in launcher
    assert "compose_public up -d --no-deps --force-recreate web" in launcher

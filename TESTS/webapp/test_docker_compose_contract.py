from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_local_docker_uses_host_data_and_source_mounts() -> None:
    root = _repo_root()
    compose = yaml.safe_load((root / "MIFPAPP/CORE/compose.local.yaml").read_text(encoding="utf-8"))
    web = compose["services"]["web"]
    volumes = web["volumes"]

    assert web["environment"]["FLASK_ENV"] == "development"
    assert web["environment"]["DATABASE_PATH"] == "/app/data/mifp.db"
    assert any(volume.get("target") == "/app/data" for volume in volumes)
    assert web["environment"]["TMPDIR"] == "/app/data/tmp"
    assert any(volume.get("target") == "/app/mifp_app" for volume in volumes)
    source_mounts = [v for v in volumes if v.get("target", "").startswith("/app/") and v.get("target") != "/app/data"]
    assert source_mounts
    assert all(volume.get("read_only") is True for volume in source_mounts)


def test_production_compose_has_no_build_and_uses_registry_image() -> None:
    root = _repo_root()
    compose = yaml.safe_load(
        (root / "deploy/compose.production.yaml").read_text(encoding="utf-8")
    )
    web = compose["services"]["web"]
    init = compose["services"]["storage-init"]

    assert "build" not in web
    assert web["image"] == "${MIFP_IMAGE:-ghcr.io/matginesi/mifp-webapp:latest}"
    assert "ghcr.io/" in web["image"]
    assert init["user"] == "0:0"
    assert "/opt/mifp/data:/app/data" in init["volumes"]
    assert "/opt/mifp/data:/app/data" in web["volumes"]
    assert web["read_only"] is True
    assert web["environment"]["FLASK_ENV"] == "production"
    assert web["environment"]["AUTO_MIGRATE_ON_STARTUP"] == "0"
    assert web["environment"]["TMPDIR"] == "/app/data/tmp"
    assert web["environment"]["SESSION_COOKIE_SECURE"] == "1"
    assert web["environment"]["TRUST_PROXY"] == "1"
    assert "127.0.0.1:8000:8000" in web["ports"]
    assert web["cap_drop"] == ["ALL"]
    assert "healthcheck" in web
    assert "/data/tmp" in " ".join(init["command"])


def test_production_compose_never_builds_an_image() -> None:
    root = _repo_root()
    text = (root / "deploy/compose.production.yaml").read_text(encoding="utf-8")
    assert "build:" not in text
    assert "target: runtime" not in text


def test_container_entrypoint_migrates_before_startup() -> None:
    root = _repo_root()
    entrypoint = (root / "MIFPAPP/CORE/docker-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (root / "MIFPAPP/CORE/Dockerfile").read_text(encoding="utf-8")

    assert "flask db-upgrade" in entrypoint
    assert 'COPY --chown=10001:10001 mifp_archive ./mifp_archive' not in dockerfile
    assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in dockerfile


def test_launcher_has_only_local_start_commands() -> None:
    root = _repo_root()
    launcher = (root / "mifp").read_text(encoding="utf-8")

    assert "local|start) start_local" in launcher
    assert "docker-local|docker) docker_local" in launcher
    assert "production|prod)" not in launcher
    assert "start_production" not in launcher
    assert "docker dev" not in launcher
    assert "docker prod" not in launcher


def test_admin_change_recreates_only_local_docker_web_service() -> None:
    root = _repo_root()
    launcher = (root / "mifp").read_text(encoding="utf-8")

    assert "apply_admin_to_running_services" in launcher
    assert "compose_local up -d --no-deps --force-recreate web" in launcher
    assert "compose_production up -d --no-deps --force-recreate web" not in launcher
    assert "compose_public up -d --no-deps --force-recreate web" not in launcher

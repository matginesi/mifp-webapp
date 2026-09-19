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

    assert set(compose["services"]) == {"web"}
    assert "build" not in web
    assert web["image"] == "${MIFP_IMAGE:?MIFP_IMAGE must be supplied by deploy.sh}"
    assert "${MIFP_DATA_DIR:-/opt/mifp/data}:/app/data" in web["volumes"]
    assert "${MIFP_EVENTS_DIR:-/opt/mifp/events}:/app/events" in web["volumes"]
    assert web["environment"]["EVENTS_ROOT"] == "/app/events"
    assert web["read_only"] is True
    assert web["environment"]["FLASK_ENV"] == "production"
    assert "AUTO_MIGRATE_ON_STARTUP" not in web["environment"]
    assert web["environment"]["TMPDIR"] == "/app/data/tmp"
    assert web["environment"]["SESSION_COOKIE_SECURE"] == "1"
    assert web["environment"]["TRUST_PROXY"] == "1"
    assert web["environment"]["GUNICORN_BIND"] == "0.0.0.0:8000"
    assert web["environment"]["LOG_OUTPUT"] == "stdout"
    assert web["environment"]["RESTIC_PASSWORD"] == ""
    assert "127.0.0.1:8000:8000" in web["ports"]
    assert web["cap_drop"] == ["ALL"]
    assert web["security_opt"] == ["no-new-privileges:true"]
    assert web["read_only"] is True
    assert all("docker.sock" not in str(volume) for volume in web.get("volumes", []))
    assert "healthcheck" in web


def test_production_compose_never_builds_an_image() -> None:
    root = _repo_root()
    text = (root / "deploy/compose.production.yaml").read_text(encoding="utf-8")
    assert "build:" not in text
    assert "target: runtime" not in text


def test_production_compose_publishes_only_loopback_ports() -> None:
    """Docker publishes ports through its own iptables rules, so a UFW rule
    cannot contain a bad mapping: every published port must be bound to
    loopback.

    This is the authoritative check for the invariant because it parses the file
    as YAML. The fast fail-closed gate in the CI `hygiene` job enforces the same
    rule with the standard library only (that job installs no dependencies);
    keep the two in step if the contract ever changes.
    """
    root = _repo_root()
    compose = yaml.safe_load(
        (root / "deploy/compose.production.yaml").read_text(encoding="utf-8")
    )

    for name, service in compose["services"].items():
        assert service.get("network_mode") != "host", f"service {name} uses host networking"

        ports = service.get("ports") or []
        assert ports, f"service {name} publishes no port; Caddy could not reach the webapp"

        for entry in ports:
            if isinstance(entry, str):
                # Short syntax: [HOST_IP:]HOST_PORT:CONTAINER_PORT[/PROTOCOL]
                host_ip = entry.split(":", 1)[0]
            elif isinstance(entry, dict):
                host_ip = entry.get("host_ip")
            else:  # pragma: no cover - defensive
                raise AssertionError(f"unexpected port entry {entry!r} in service {name}")
            assert host_ip in {"127.0.0.1", "::1"}, (
                f"service {name} publishes {entry!r} on {host_ip!r}; Docker would expose it "
                "to the Internet regardless of UFW"
            )


def test_container_entrypoint_verifies_production_database_without_migrating() -> None:
    root = _repo_root()
    entrypoint = (root / "MIFPAPP/CORE/docker-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (root / "MIFPAPP/CORE/Dockerfile").read_text(encoding="utf-8")

    assert "python -m mifp_app.db.runtime_check" in entrypoint
    assert "flask db-upgrade" not in entrypoint
    assert "AUTO_MIGRATE_ON_STARTUP" not in entrypoint
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

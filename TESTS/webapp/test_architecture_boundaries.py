from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (_repo_root().joinpath(*parts)).read_text(encoding="utf-8")


def test_deploy_artifacts_are_complete() -> None:
    root = _repo_root()
    required = (
        "deploy/compose.production.yaml",
        "deploy/Caddyfile",
        "deploy/.env.production.example",
        "deploy/deploy.sh",
        "deploy/bootstrap-vps.sh",
    )
    for relative in required:
        assert (root / relative).is_file(), f"missing deploy artifact: {relative}"


def test_production_compose_is_inside_deploy_not_core() -> None:
    root = _repo_root()
    assert not (root / "MIFPAPP/CORE/compose.production.yaml").exists()
    assert not (root / "MIFPAPP/CORE/compose.public.yaml").exists()
    assert not (root / "MIFPAPP/CORE/Caddyfile").exists()
    assert (root / "MIFPAPP/CORE/compose.local.yaml").is_file()


def test_deploy_compose_binds_only_loopback_and_host_data() -> None:
    import yaml

    compose = yaml.safe_load(_read("deploy", "compose.production.yaml"))
    web = compose["services"]["web"]
    ports = [str(port) for port in web["ports"]]
    assert "127.0.0.1:8000:8000" in ports
    assert not any(not port.startswith("127.0.0.1:") for port in ports)
    assert all(volume.startswith("/opt/mifp/data:/app/data") for volume in web["volumes"])
    assert "image" in web
    assert "build" not in web


def test_deploy_caddyfile_proxies_to_localhost() -> None:
    caddyfile = _read("deploy", "Caddyfile")
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert "web:8000" not in caddyfile


def test_production_env_template_is_committed_and_secret_safe() -> None:
    template = _read("deploy", ".env.production.example")
    assert "SECRET_KEY" in template
    assert "ADMIN_PASSWORD_HASH" in template
    assert "MIFP_IMAGE" in template
    assert "TRUSTED_HOSTS" in template
    assert "=" in template
    assert "$" not in template or "secrets" in template


def test_local_and_production_env_templates_are_separate() -> None:
    local = _read("MIFPAPP/CORE/.env.example")
    production = _read("deploy", ".env.production.example")
    assert production != local
    assert "DATABASE_PATH" in local
    assert "MIFP_PORT" in local


def test_ci_cd_workflow_tests_builds_only() -> None:
    root = _repo_root()
    workflow = root / ".github/workflows/ci-cd.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    assert "test" in text.lower()
    assert "ghcr.io" in text
    assert "docker/build-push-action" in text
    # Deployment to VPS has been removed - users deploy manually


def test_ci_workflow_runs_the_versioned_webapp_suite() -> None:
    text = _read(".github", "workflows", "ci-cd.yml")
    assert "test_all.sh" in text
    assert "--suite webapp" in text
    assert "TESTS/scraper" not in text.split("--suite webapp")[0]


def test_docker_build_context_stays_in_core() -> None:
    dockerfile = _read("MIFPAPP/CORE/Dockerfile")
    assert "FROM" in dockerfile
    root = _repo_root()
    assert not (root / "Dockerfile").is_file()
    assert not (root / "compose.yaml").is_file()


def test_tracked_log_files_are_removed_from_index() -> None:
    root = _repo_root()
    result = subprocess.run(
        ["git", "ls-files", "MIFPAPP/DATABASE/logs"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    tracked = result.stdout.splitlines()
    assert all(name.endswith(".gitkeep") for name in tracked), f"tracked logs: {tracked}"


def test_manage_py_exposes_password_hash_for_production_rotation() -> None:
    manage = _read("MIFPAPP/CORE/manage.py")
    assert "password-hash" in manage


def test_launcher_exposes_hash_and_init_commands() -> None:
    launcher = _read("mifp")
    assert "hash) print_password_hash" in launcher
    assert "init|setup) ensure_venv" in launcher


def test_readme_keeps_required_architecture_statements() -> None:
    readme = _read("README.md")
    assert "Non eseguire script Python ad hoc contro il database" in readme
    assert "L'immagine Docker ha come contesto `MIFPAPP/CORE`" in readme


def test_readme_and_deployment_docs_do_not_reference_native_runner() -> None:
    readme = _read("README.md")
    deployment = _read("DEPLOYMENT.md")
    assert "deploy.sh native" not in readme
    assert "deploy.sh native" not in deployment
    assert "nginx" not in deployment.lower()

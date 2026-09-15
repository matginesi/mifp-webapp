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
        "deploy/configure.py",
        "deploy/backup.sh",
        "deploy/mifpctl",
        "deploy/mifp-backup.service",
        "deploy/mifp-backup.timer",
        "deploy/local-hosts.sh",
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
    assert all("MIFP_DATA_DIR" in volume and volume.endswith(":/app/data") for volume in web["volumes"])
    assert "image" in web
    assert "build" not in web


def test_deploy_caddyfile_proxies_to_localhost() -> None:
    caddyfile = _read("deploy", "Caddyfile")
    assert "__MIFP_DOMAIN__" in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert "@ready path /ready" in caddyfile
    assert "respond @ready 404" in caddyfile
    assert "__MIFP_EVENTS_DOMAIN__" in caddyfile
    assert "root * /opt/mifp/events" in caddyfile
    assert "mifp-events-php.caddy" in caddyfile
    assert "@events_php_source" in caddyfile
    assert "respond @events_php_source 404" in caddyfile
    assert "web:8000" not in caddyfile


def test_production_env_template_is_committed_and_secret_safe() -> None:
    template = _read("deploy", ".env.production.example")
    assert "SECRET_KEY" in template
    assert "ADMIN_PASSWORD_HASH" in template
    assert "MIFP_IMAGE=" not in template
    assert "deploy.sh" in template
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
    assert "build-pr:" in text
    assert "push: false" in text
    assert "verify-image:" in text
    assert "needs: build" in text
    assert "needs.build.outputs.digest" in text
    assert 'mifp_app.db.manage init /app/data/mifp.db' in text
    assert 'http://127.0.0.1:${port}/ready' in text
    assert 'http://127.0.0.1:${port}/health' in text
    assert "promote-latest:" in text
    assert "needs: [build, verify-image]" in text
    assert 'imagetools create --tag "$REPOSITORY:latest" "$REPOSITORY@$DIGEST"' in text
    # Deployment to VPS has been removed - users deploy manually
    assert "ssh-action" not in text
    assert "appleboy" not in text
    assert "ssh_deploy" not in text


def test_ci_workflow_runs_the_non_browser_repository_suite() -> None:
    text = _read(".github", "workflows", "ci-cd.yml")
    assert "test_all.sh" in text
    assert "--suite quick" in text
    assert "requirements.lock" in text
    assert "pip-audit" in text


def test_docker_build_context_stays_in_core() -> None:
    dockerfile = _read("MIFPAPP/CORE/Dockerfile")
    assert "FROM" in dockerfile
    root = _repo_root()
    assert not (root / "Dockerfile").is_file()
    assert not (root / "compose.yaml").is_file()


def test_tracked_log_files_are_removed_from_index() -> None:
    root = _repo_root()

    # Runtime logging is expected to create files here while tests or the local
    # application are running.  The source-control contract is that those files
    # are ignored and never tracked; requiring the directory itself to stay empty
    # makes the test fail simply because logging works.
    gitignore = _read(".gitignore")
    assert "*.log" in gitignore
    assert "MIFPAPP/DATABASE/logs/" in gitignore

    # Source ZIPs intentionally do not ship .git metadata.  In a real checkout
    # additionally verify that Git tracks at most the placeholder file.
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        return

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
    assert "setup) ensure_venv" in launcher
    assert "init) init_local" in launcher


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


def test_release_script_tracks_current_and_previous_images() -> None:
    script = _read("deploy", "deploy.sh")
    assert "CURRENT_IMAGE=" in script
    assert "PREVIOUS_IMAGE=" in script
    assert '@sha256:' in script
    assert "flock -n 9" in script
    assert "preflight_image_db" in script
    assert 'MIFP_IMAGE="$image"' in script
    assert "sudo -u mifp" not in script
    assert "Esegui deploy.sh come root" in script


def test_bootstrap_uses_fixed_runtime_uid_and_packaged_caddy_service() -> None:
    script = _read("deploy", "bootstrap-vps.sh")
    assert 'MIFP_UID="10001"' in script
    assert 'MIFP_GID="10001"' in script
    assert "apt-get install -y docker-ce" in script
    assert "docker-compose-plugin caddy" in script
    assert "php-fpm php-cli php-mbstring php-curl" in script
    assert "mifp-events.conf" in script
    assert "/run/php/mifp-events.sock" in script
    assert 'EVENTS_PHP_USER="mifp-events"' in script
    assert "/etc/systemd/system/caddy.service" not in script
    assert 'install -o root -g root -m 0750 "$SCRIPT_DIR/configure.py"' in script
    assert 'install -o root -g root -m 0755 "$SCRIPT_DIR/mifpctl" /usr/local/sbin/mifpctl' in script
    assert "mifp-backup.timer" in script
    assert '[[ -f "$MIFP_HOME/data/mifp.db"' in script
    assert "systemctl disable --now mifp-backup.timer" in script
    assert "--admin-if-missing" not in script
    assert 'install -o root -g root -m 0750 "$SCRIPT_DIR/vps_config.py"' in script
    assert "Host bootstrap completed" in script
    assert "caddy fmt --overwrite /etc/caddy/Caddyfile" in script
    assert 'if [[ "$DOMAIN" == *.home.arpa ]]' in script
    assert 'MIFP_TLS_DIRECTIVE="tls internal"' in script
    assert 'MIFP_TLS_DIRECTIVE=""' in script
    assert 'bash "$SCRIPT_DIR/local-hosts.sh" "$DOMAIN"' in script


def test_repository_hygiene_is_enforced_by_git_ci_and_packaging() -> None:
    root = _repo_root()
    gitignore = _read(".gitignore")
    workflow = _read(".github", "workflows", "ci-cd.yml")
    packager = _read("zip_it.sh")
    checker = root / "tools" / "check_repo_hygiene.py"

    assert checker.is_file()
    assert "SCRAPERS/OUTPUTS/" in gitignore
    for relative in (
        "assets",
        "backups",
        "config",
        "conferences",
        "exports",
        "logs",
        "tmp",
        "uploads",
    ):
        assert f"MIFPAPP/DATABASE/{relative}/" in gitignore

    assert "hygiene:" in workflow
    assert "python tools/check_repo_hygiene.py" in workflow
    assert workflow.count("needs: [hygiene, test, audit]") == 2

    assert '"SCRAPERS/OUTPUTS"' in packager
    assert '"MIFPAPP/DATABASE/uploads"' in packager


def test_deploy_security_hardening_contract() -> None:
    deploy = _read("deploy", "deploy.sh")
    compose = _read("deploy", "compose.production.yaml")
    caddy = _read("deploy", "Caddyfile")
    backup_service = _read("deploy", "mifp-backup.service")

    assert "security-check" in deploy
    assert "World-writable MIFP paths" in deploy
    assert "Unexpected public TCP listeners" in deploy
    assert "Docker socket is mounted" in deploy
    assert "RESTIC_PASSWORD is exposed" in deploy
    assert 'RESTIC_PASSWORD: ""' in compose
    assert "*.sqlite3" in caddy and "*.db" in caddy and "*.sql" in caddy
    assert "UnsetEnvironment=SECRET_KEY ADMIN_PASSWORD_HASH SMTP_PASSWORD" in backup_service
    assert "UMask=0077" in backup_service


def test_local_ca_certificates_are_not_source_artifacts() -> None:
    root = _repo_root()
    assert not list(root.glob("*-caddy-root.crt"))
    assert "*.crt" in _read(".gitignore")
    assert '"*.crt"' in _read("tools", "check_repo_hygiene.py")
    assert '"*.crt"' in _read("zip_it.sh")


def test_repository_hygiene_checker_accepts_the_source_tree() -> None:
    root = _repo_root()
    result = subprocess.run(
        ["python3", "tools/check_repo_hygiene.py"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Repository hygiene OK" in result.stdout

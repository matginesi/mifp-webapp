from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest


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
    assert "${MIFP_DATA_DIR:-/opt/mifp/data}:/app/data" in web["volumes"]
    assert all("/app/events" not in str(volume) for volume in web["volumes"])
    assert "${EVENTS_LOCAL_ROOT:-/srv/mifp-events}:/app/event-sites" in web["volumes"]
    assert all("/srv:/" not in str(volume) for volume in web["volumes"])
    assert all("docker.sock" not in volume and "/etc/caddy" not in volume for volume in web["volumes"])
    assert "image" in web
    assert "build" not in web


def test_deploy_caddyfile_proxies_to_localhost() -> None:
    caddyfile = _read("deploy", "Caddyfile")
    assert "__MIFP_DOMAIN__" in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert "__MIFP_WWW_DOMAIN__" in caddyfile
    assert "redir https://__MIFP_DOMAIN__{uri} permanent" in caddyfile
    assert "@ready path /ready" in caddyfile
    assert "respond @ready 404" in caddyfile
    assert "__MIFP_EVENTS_DOMAIN__" not in caddyfile
    assert "root * /opt/mifp/events" not in caddyfile
    assert "mifp-events-php.caddy" not in caddyfile
    assert "events.mifp.eu" not in caddyfile
    assert "local-vps" in caddyfile and "Remote/disabled" in caddyfile
    assert "web:8000" not in caddyfile

    bootstrap = _read("deploy", "bootstrap-vps.sh")
    assert "__MIFP_EVENTS_DOMAIN__" not in bootstrap
    assert '"$DOMAIN" "$WWW_DOMAIN" "$EVENTS_DOMAIN"' not in bootstrap
    deploy = _read("deploy", "deploy.sh")
    assert '[[ "$events_backend" == "local-vps" ]]' in deploy
    assert "root * $events_root" in deploy
    assert "file_server" in deploy
    assert "respond @event_php 404" in deploy
    assert "respond @event_hidden 404" in deploy
    assert "event_sensitive_tree" in deploy
    assert "event_database_backup" in deploy
    assert "event_sensitive_file" in deploy
    assert "event_sensitive_extension" in deploy
    assert "not path /.well-known /.well-known/*" in deploy
    assert "file_server @event_public_conference_yaml" in deploy


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
    assert "pull_request:" not in text
    assert "build-pr:" not in text
    assert "release:" in text
    assert "needs: [hygiene, test, audit, secrets]" in text
    assert "load: true" in text
    assert "push: false" in text
    assert "push: true" not in text
    assert 'mifp_app.db.manage init /app/data/mifp.db' in text
    assert 'http://127.0.0.1:${port}/ready' in text
    assert 'http://127.0.0.1:${port}/health' in text
    assert 'org.mifp.deploy-contract' in text
    assert 'docker image inspect "$LOCAL_IMAGE"' in text
    assert '--env MIFP_DEPLOY_CONTRACT="$contract"' in text
    assert "trivy image" in text
    assert "--ignore-unfixed" in text
    assert 'docker push "$SHA_IMAGE"' in text
    assert 'docker push "$LATEST_IMAGE"' in text
    # No application image reaches GHCR until the local runtime and Trivy
    # verification steps have succeeded.
    assert text.index("Scan verified local image") < text.index("docker/login-action")
    assert text.index("docker/login-action") < text.index('docker push "$SHA_IMAGE"')
    assert text.index('docker push "$SHA_IMAGE"') < text.index('docker push "$LATEST_IMAGE"')
    # Deployment to VPS has been removed - users deploy manually
    assert "ssh-action" not in text
    assert "appleboy" not in text
    assert "ssh_deploy" not in text


def test_ci_workflow_runs_the_complete_non_browser_repository_suite_in_parallel() -> None:
    text = _read(".github", "workflows", "ci-cd.yml")
    assert "test_all.sh" in text
    assert "label: webapp 1/2" in text
    assert "label: webapp 2/2" in text
    assert "label: data" in text
    assert "MIFP_TEST_SHARD_INDEX" in text
    assert "MIFP_TEST_SHARD_TOTAL" in text
    assert "--suite webapp" in text
    assert "--suite scraper" not in text
    assert "SCRAPERS/requirements.txt" not in text
    assert "--suite database" in text
    assert "--suite tools" in text
    test_requirements = _read("TESTS", "requirements.txt")
    assert "pytest==9.1.1" in test_requirements
    assert "pytest-xdist==3.8.0" in test_requirements
    assert "TESTS/requirements.txt" in text
    assert "-n 2 --dist=worksteal --durations=20" in text
    assert "--dist=loadfile" not in text
    assert "cache: pip" in text
    assert "cache-dependency-path:" in text
    assert "requirements.lock" in text
    assert "pip-audit" in text


def test_default_pytest_is_parallel_non_browser_and_full_browser_remains_explicit() -> None:
    pytest_ini = _read("pytest.ini")
    runner = _read("test_all.sh")
    assert "addopts = -n auto --dist=worksteal" in pytest_ini
    assert "TESTS/webapp" in pytest_ini
    assert "TESTS/database" in pytest_ini
    assert "TESTS/tools" in pytest_ini
    assert "TESTS/browser" not in pytest_ini
    assert 'run_webapp()' in runner
    assert '-n auto --dist=worksteal TESTS/webapp' in runner
    assert '-n 0 TESTS/browser' in runner
    assert 'run_quick; run_browser' in runner
    assert "SCRAPERS" not in runner



def test_github_automation_is_main_only_and_has_no_branch_bots() -> None:
    root = _repo_root()
    github = root / ".github"
    ci_text = (github / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    cleanup_text = (github / "workflows" / "ghcr-cleanup.yml").read_text(encoding="utf-8")

    # No repository-managed dependency bot may create PRs/branches. Security
    # alerts remain a GitHub repository setting, outside the source tree.
    assert not (github / "dependabot.yml").exists()

    # CI is intentionally main-only (plus explicit manual dispatch). It does
    # not react to PRs and therefore does not require development branches.
    assert "branches: [main]" in ci_text
    assert "pull_request:" not in ci_text
    assert "pull_request_target:" not in ci_text
    assert "workflow_dispatch:" in ci_text

    # Retention maintenance is both operator-triggered and scheduled, but it
    # only deletes old Actions history / package versions; it never mutates
    # source branches, issues or pull requests.
    assert "workflow_dispatch:" in cleanup_text
    assert "workflow_run:" in cleanup_text
    assert 'workflows: ["CI/CD"]' in cleanup_text
    assert "schedule:" in cleanup_text
    assert "prune_ghcr_versions.py" in cleanup_text
    assert "prune_actions_runs.py" in cleanup_text
    assert "actions: write" in cleanup_text
    assert "packages: write" in cleanup_text

    # No workflow may mutate repository contents, issues or pull requests.
    all_workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((github / "workflows").glob("*.yml"))
    )
    assert "contents: write" not in all_workflows
    assert "pull-requests: write" not in all_workflows
    assert "issues: write" not in all_workflows


def test_github_actions_are_bounded_and_cancel_stale_runs() -> None:
    import yaml

    root = _repo_root()
    ci_path = root / ".github" / "workflows" / "ci-cd.yml"
    cleanup_path = root / ".github" / "workflows" / "ghcr-cleanup.yml"
    ci = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    cleanup = yaml.safe_load(cleanup_path.read_text(encoding="utf-8"))

    for workflow, path in ((ci, ci_path), (cleanup, cleanup_path)):
        jobs = workflow.get("jobs") or {}
        assert jobs, f"no jobs in {path}"
        for name, job in jobs.items():
            timeout = job.get("timeout-minutes")
            assert isinstance(timeout, int) and 1 <= timeout <= 60, (path, name, timeout)

    assert ci["concurrency"]["cancel-in-progress"] is True
    assert "${{ github.event_name }}" in ci["concurrency"]["group"]
    assert cleanup["concurrency"]["cancel-in-progress"] is True

    ci_text = ci_path.read_text(encoding="utf-8")
    assert "--connect-timeout 10 --max-time 120 --retry 3" in ci_text
    assert "timeout --foreground 20m bash test_all.sh --suite webapp" in ci_text
    assert "--suite scraper" not in ci_text
    assert "SCRAPERS/requirements.txt" not in ci_text
    assert "timeout --foreground 8m bash test_all.sh --suite database" in ci_text
    assert "timeout --foreground 4m bash test_all.sh --suite tools" in ci_text
    assert "timeout --foreground 12m ./trivy image" in ci_text
    assert "workflow_dispatch:" in ci_text

    cleanup_text = cleanup_path.read_text(encoding="utf-8")
    assert "actions/delete-package-versions" not in cleanup_text
    assert ".github/scripts/prune_ghcr_versions.py" in cleanup_text
    assert ".github/scripts/prune_actions_runs.py" in cleanup_text
    assert '--min-versions-to-keep "$IMAGE_KEEP"' in cleanup_text
    assert 'default: "3"' in cleanup_text
    assert "timeout --foreground 10m python3" in cleanup_text


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
    assert "do_update_check" in script
    assert "do_update" in script
    assert "latest_available_image" in script
    assert "docker buildx imagetools inspect" in script
    assert "{{json .Manifest}}" in script
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
    assert "security.limit_extensions = .php" in script
    assert "php_admin_value[open_basedir]" in script
    assert "php_admin_value[user_ini.filename]" in script
    assert "php_admin_value[disable_functions]" in script
    assert "/etc/systemd/system/caddy.service" not in script
    assert 'install -o root -g root -m 0750 "$SCRIPT_DIR/configure.py"' in script
    assert 'install -o root -g root -m 0755 "$SCRIPT_DIR/mifpctl" /usr/local/sbin/mifpctl' in script
    assert "mifp-backup.timer" in script
    assert '[[ -f "$MIFP_HOME/data/mifp.db"' in script
    assert "systemctl disable --now mifp-backup.timer" in script
    assert "--admin-if-missing" not in script
    assert 'install -o root -g root -m 0750 "$SCRIPT_DIR/vps_config.py"' in script
    assert 'install -o root -g root -m 0750 "$SCRIPT_DIR/check-events-archive.py"' not in script
    assert "Host bootstrap completed" in script
    # The live Caddyfile must never be written before it validates: render to a
    # temp file, format+validate it, then publish atomically.
    assert 'mktemp /etc/caddy/.mifp-caddy.XXXXXX' in script
    assert 'caddy fmt --overwrite "$CADDY_TMP"' in script
    assert 'caddy validate --config "$CADDY_TMP" --adapter caddyfile' in script
    assert 'mv -f "$CADDY_TMP" /etc/caddy/Caddyfile' in script
    # Host security baseline: security-only unattended upgrades (never an
    # automatic reboot), SSH brute-force protection, and pinned apt keys.
    assert "unattended-upgrades" in script
    assert 'Unattended-Upgrade::Automatic-Reboot "false"' in script
    assert "fail2ban" in script
    assert "MIFP_DOCKER_KEY_FINGERPRINT" in script
    assert "MIFP_CADDY_KEY_FINGERPRINT" in script
    assert "ufw limit" in script
    assert 'if [[ "$DOMAIN" == *.home.arpa ]]' in script
    assert 'MIFP_TLS_DIRECTIVE="tls internal"' in script
    assert 'MIFP_TLS_DIRECTIVE=""' in script
    assert 'bash "$SCRIPT_DIR/local-hosts.sh" "$DOMAIN"' in script


@pytest.mark.parametrize(
    ("os_id", "version", "supported"),
    [
        ("ubuntu", "22.04", True),
        ("ubuntu", "24.04", True),
        ("ubuntu", "20.04", False),
        ("ubuntu", "26.04", False),
        ("debian", "12", False),
    ],
)
def test_bootstrap_validates_supported_ubuntu_releases(
    os_id: str, version: str, supported: bool
) -> None:
    script = _read("deploy", "bootstrap-vps.sh")
    function = script.split("is_supported_ubuntu_release() {", 1)[1].split("\n}", 1)[0]
    probe = "is_supported_ubuntu_release() {" + function + "\n}\n" + (
        f"is_supported_ubuntu_release {os_id!r} {version!r}"
    )
    result = subprocess.run(["bash", "-c", probe], check=False)
    assert (result.returncode == 0) is supported
    assert "Ubuntu 22.04 e Ubuntu 24.04" in script
    assert "target di produzione corrente: 24.04" in script


def test_ssh_hardening_remains_explicit_and_opt_in() -> None:
    bootstrap = _read("deploy", "bootstrap-vps.sh")
    deploy = _read("deploy", "deploy.sh")
    assert "mifpctl ssh-harden" not in bootstrap
    assert "sshd_config" not in bootstrap
    assert "PasswordAuthentication" not in bootstrap
    assert "PermitRootLogin" not in bootstrap
    assert "do_ssh_harden" in deploy
    assert "ssh-harden) shift; do_ssh_harden" in deploy
    assert "PasswordAuthentication no" in deploy
    assert "PermitRootLogin prohibit-password" in deploy
    assert "do_ssh_rollback" in deploy


def test_production_docs_match_ssh_cutover_and_schema_policy() -> None:
    paths = (
        "README.md",
        "DEPLOYMENT.md",
        "docs/DEPLOY_NEW_VPS.md",
        "docs/deployment/vps-installation.md",
        "docs/deployment/hardening.md",
    )
    docs = "\n".join(_read(*path.split("/")) for path in paths)
    assert "no production VPS exists" not in docs.lower()
    assert "schema-only v10" not in docs.lower()
    assert "staging.mifp.eu" not in docs
    assert "--domain mifp.eu" in docs
    assert "mifp.eu" in docs and "www.mifp.eu" in docs
    assert "ssh-harden" in docs
    assert "optional" in _read("docs", "DEPLOY_NEW_VPS.md").lower()
    assert "WARN" in _read("docs", "deployment", "hardening.md")


def test_events_domain_checks_are_backend_aware() -> None:
    caddy = _read("deploy", "Caddyfile")
    bootstrap = _read("deploy", "bootstrap-vps.sh")
    deploy = _read("deploy", "deploy.sh")
    config = _read("deploy", "vps_config.py")
    assert "__MIFP_EVENTS_DOMAIN__" not in caddy
    assert "https://$events_domain/.mifp-events-health" not in deploy
    assert 'config_cli get EVENTS_DOMAIN' not in deploy.split("do_doctor() {", 1)[1].split("\n}", 1)[0]
    assert 'if values.get("EVENTS_PUBLISH_BACKEND") == "local-vps"' in config
    assert 'dns_hosts.append(urlsplit(values.get("EVENTS_PUBLIC_BASE_URL", "")).hostname or "")' in config
    assert 'for key in ("DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN")' not in config
    assert '"$DOMAIN" "$WWW_DOMAIN" "$EVENTS_DOMAIN"' not in bootstrap


def test_repository_tests_do_not_import_local_only_scraper_modules() -> None:
    root = _repo_root()
    local_only_modules = {
        "_remote_aruba",
        "_remote_events",
        "artifact_normalizer",
        "import_artifacts",
        "scrape_local",
        "scrape_remote",
        "validate_artifacts",
        "validate_import_data",
    }
    offenders: list[str] = []

    for test_file in sorted((root / "TESTS").rglob("*.py")):
        tree = ast.parse(test_file.read_text(encoding="utf-8"), filename=str(test_file))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        for module in imported_modules:
            root_module = module.split(".", 1)[0]
            if root_module == "SCRAPERS" or root_module in local_only_modules:
                offenders.append(f"{test_file.relative_to(root)} imports {module}")

    assert not offenders, "local-only scraper imports found in repository tests: " + "; ".join(offenders)


def test_repository_hygiene_is_enforced_by_git_ci_and_packaging() -> None:
    root = _repo_root()
    gitignore = _read(".gitignore")
    workflow = _read(".github", "workflows", "ci-cd.yml")
    packager = _read("zip_it.sh")
    checker = root / "tools" / "check_repo_hygiene.py"

    assert checker.is_file()
    assert "SCRAPERS/" in {line.strip() for line in gitignore.splitlines()}
    for relative in (
        "assets",
        "backups",
        "config",
        "conferences",
        "exports",
        "events",
        "logs",
        "tmp",
        "uploads",
    ):
        assert f"MIFPAPP/DATABASE/{relative}/" in gitignore

    assert "hygiene:" in workflow
    assert "python tools/check_repo_hygiene.py" in workflow
    # The single main-branch release path is gated on secret scan, tests and
    # dependency audit. The image is built and verified locally before any
    # registry login or push occurs.
    assert workflow.count("needs: [hygiene, test, audit, secrets]") == 1
    assert "release:" in workflow
    assert "load: true" in workflow
    assert "push: false" in workflow
    assert "push: true" not in workflow
    assert workflow.index("Scan verified local image") < workflow.index("docker/login-action")
    assert workflow.index("docker/login-action") < workflow.index('docker push "$SHA_IMAGE"')
    assert "secrets:" in workflow
    assert "gitleaks git ." in workflow

    assert '"MIFPAPP/DATABASE/uploads"' in packager
    assert '"MIFPAPP/DATABASE/events"' in packager
    assert '"MIFPAPP/DATABASE/events-php-enabled.txt"' in packager
    checker_text = checker.read_text(encoding="utf-8")
    assert '"MIFPAPP/DATABASE/events/"' in checker_text
    assert '"SCRAPERS/"' in checker_text
    assert '"TESTS/scraper/"' in checker_text
    assert '"MIFPAPP/DATABASE/events-php-enabled.txt"' in checker_text
    gitignore_lines = {line.strip() for line in gitignore.splitlines()}
    assert "MIFPAPP/DATABASE/events/" in gitignore_lines
    assert "MIFPAPP/DATABASE/events-php-enabled.txt" in gitignore_lines


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
    # The base template has no unconditional event surface; local-vps appends
    # its hardened event site dynamically and remote mode appends nothing.
    assert "file_server" not in caddy
    assert "@events_" not in caddy
    assert "/opt/mifp/events" not in caddy
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


def test_compose_uses_canonical_public_config_for_interpolation() -> None:
    deploy = _read("deploy", "deploy.sh")
    assert '--env-file "$ENV_FILE" --env-file "$PUBLIC_CONFIG_FILE"' in deploy


def test_production_container_receives_application_credentials_as_docker_secrets() -> None:
    import yaml

    compose = yaml.safe_load(_read("deploy", "compose.production.yaml"))
    web = compose["services"]["web"]
    env_files = web.get("env_file") or []
    assert not any("secrets.env" in str(item) for item in env_files)
    assert set(web.get("secrets") or []) >= {
        "mifp_secret_key",
        "mifp_admin_password_hash",
        "mifp_smtp_password",
        "mifp_events_remote_password",
    }
    environment = web["environment"]
    assert environment["SMTP_PASSWORD_FILE"] == "/run/secrets/mifp_smtp_password"
    assert environment["SECRET_KEY_FILE"] == "/run/secrets/mifp_secret_key"
    assert "SMTP_PASSWORD" not in environment

    secrets = compose["secrets"]
    expected_secret_files = {
        "mifp_secret_key": "${MIFP_SECRETS_DIR:-/etc/mifp/secrets}/mifp_secret_key",
        "mifp_admin_password_hash": "${MIFP_SECRETS_DIR:-/etc/mifp/secrets}/mifp_admin_password_hash",
        "mifp_smtp_password": "${MIFP_SECRETS_DIR:-/etc/mifp/secrets}/mifp_smtp_password",
        "mifp_events_remote_password": "${MIFP_SECRETS_DIR:-/etc/mifp/secrets}/mifp_events_remote_password",
    }
    for secret_name, expected_file in expected_secret_files.items():
        assert secrets[secret_name] == {"file": expected_file}

    deploy = _read("deploy", "deploy.sh")
    prepare_fn = deploy.split("prepare_compose_secrets() {", 1)[1].split("\n}", 1)[0]
    compose_fn = deploy.split("compose_with_image() {", 1)[1].split("\n}", 1)[0]
    assert "materialize-secrets" in prepare_fn
    assert 'MIFP_SECRETS_DIR="$SECRET_MATERIAL_DIR"' in compose_fn
    assert 'source "$SECRETS_FILE"' not in compose_fn
    assert "export SECRET_KEY ADMIN_PASSWORD_HASH SMTP_PASSWORD EVENTS_REMOTE_PASSWORD" not in compose_fn
    assert 'MIFP_DEPLOY_CONTRACT="$DEPLOY_CONTRACT_VERSION"' in compose_fn

    dockerfile = _read("MIFPAPP/CORE/Dockerfile")
    assert 'LABEL org.mifp.deploy-contract="2"' in dockerfile
    assert 'MIFP_DEPLOY_CONTRACT_REQUIRED=2' in dockerfile
    assert web["environment"]["MIFP_DEPLOY_CONTRACT"].startswith("${MIFP_DEPLOY_CONTRACT:")

    entrypoint = _read("MIFPAPP/CORE/docker-entrypoint.sh")
    assert "MIFP_DEPLOY_CONTRACT_REQUIRED" in entrypoint
    assert "MIFP deploy contract mismatch" in entrypoint

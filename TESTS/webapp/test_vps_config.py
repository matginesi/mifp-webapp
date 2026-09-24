from __future__ import annotations

import builtins
import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "deploy" / "vps_config.py"
VALID_HASH = "pbkdf2:sha256:600000$abcd1234$" + "a" * 64


def _module():
    spec = importlib.util.spec_from_file_location("mifp_vps_config_test", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _store(tmp_path: Path):
    module = _module()
    example = tmp_path / ".env.example"
    example.write_text("MIFP_DOMAIN=''\nSMTP_PASSWORD=''\nSECRET_KEY=''\n", encoding="utf-8")
    return module, module.Store(
        tmp_path / "etc" / "config.env",
        tmp_path / "etc" / "secrets.env",
        tmp_path / "opt" / ".env",
        example,
    )


def _ready_values() -> dict[str, str]:
    return {
        "ENVIRONMENT": "local",
        "DOMAIN": "vpsbox.home.arpa",
        "WWW_DOMAIN": "www.vpsbox.home.arpa",
        "EVENTS_PUBLISH_BACKEND": "disabled",
        "EVENTS_PUBLIC_BASE_URL": "https://events.mifp.eu",
        "IMAGE_REPOSITORY": "ghcr.io/matginesi/mifp-webapp",
        "REGISTRY": "ghcr.io",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": VALID_HASH,
        "SECRET_KEY": "s" * 64,
        "BACKUP_ENABLED": "true",
        "BACKUP_LOCAL_RETENTION": "14",
    }


def test_bootstrap_accepts_missing_domain_and_does_not_prompt_for_admin() -> None:
    script = (ROOT / "deploy" / "bootstrap-vps.sh").read_text(encoding="utf-8")
    assert "--domain è obbligatorio" not in script
    assert "--admin-if-missing" not in script
    assert "MIFP host ready; run sudo mifpctl configure" in script
    assert "Host bootstrap completed" in script


def test_migration_preserves_legacy_admin_and_moves_secrets(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.runtime.parent.mkdir(parents=True)
    store.runtime.write_text(
        "MIFP_DOMAIN='mifp.eu'\nMIFP_IMAGE_REPOSITORY='ghcr.io/example/mifp'\n"
        "ADMIN_USERNAME='old-admin'\n"
        f"ADMIN_PASSWORD_HASH='{VALID_HASH}'\nSECRET_KEY='{'x' * 64}'\n"
        "SMTP_PASSWORD='smtp-secret'\n",
        encoding="utf-8",
    )

    values = store.migrate()

    assert values["ADMIN_USERNAME"] == "old-admin"
    assert values["ADMIN_PASSWORD_HASH"] == VALID_HASH
    assert values["SMTP_PASSWORD"] == "smtp-secret"
    assert stat.S_IMODE(store.config.stat().st_mode) == 0o640
    assert stat.S_IMODE(store.secrets_path.stat().st_mode) == 0o600
    runtime = module.read_env(store.runtime)
    assert runtime["ADMIN_PASSWORD_HASH"] == ""
    assert runtime["SMTP_PASSWORD"] == ""
    assert runtime["SECRET_KEY"] == ""



def test_runtime_env_keeps_event_publisher_settings_for_compose_interpolation(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values())
    runtime = module.read_env(store.runtime)
    assert runtime["EVENTS_PUBLISH_BACKEND"] == "disabled"
    assert runtime["EVENTS_PUBLIC_BASE_URL"] == "https://events.mifp.eu"
    assert runtime["MIFP_IMAGE_REPOSITORY"] == "ghcr.io/matginesi/mifp-webapp"


def test_production_domain_keeps_stable_events_public_url(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    values = store.migrate(domain="mifp.eu")
    assert values["WWW_DOMAIN"] == "www.mifp.eu"
    assert values["EVENTS_PUBLIC_BASE_URL"] == "https://events.mifp.eu"
    assert module.read_env(store.runtime)["EVENTS_PUBLIC_BASE_URL"] == "https://events.mifp.eu"


def test_local_home_arpa_configuration_accepts_derived_http_events_url(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    values = store.migrate(domain="vpsbox.home.arpa")
    values["ADMIN_PASSWORD_HASH"] = VALID_HASH
    store.save(values)
    docker_config = tmp_path / "docker.json"
    docker_config.write_text("{}", encoding="utf-8")

    errors, notes = module.check_configuration(values, docker_config=docker_config)

    assert values["ENVIRONMENT"] == "local"
    assert values["EVENTS_PUBLIC_BASE_URL"] == "http://events.vpsbox.home.arpa"
    assert errors == []
    assert "DNS: local mode; public DNS checks skipped" in notes
    result = subprocess.run(
        [
            "python3", str(HELPER),
            "--config-file", str(store.config),
            "--secrets-file", str(store.secrets_path),
            "--runtime-env", str(store.runtime),
            "--docker-config", str(docker_config),
            "check",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Result: READY" in result.stdout


def test_existing_local_config_missing_events_url_derives_http_origin(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    values = _ready_values()
    values.pop("EVENTS_PUBLIC_BASE_URL")
    store.save(values)
    for path in (store.config, store.runtime):
        path.write_text(
            "\n".join(
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("EVENTS_PUBLIC_BASE_URL=")
            )
            + "\n",
            encoding="utf-8",
        )

    migrated = store.migrate()

    assert migrated["EVENTS_PUBLIC_BASE_URL"] == "http://events.vpsbox.home.arpa"
    assert module.read_env(store.runtime)["EVENTS_PUBLIC_BASE_URL"] == (
        "http://events.vpsbox.home.arpa"
    )


def test_production_rejects_http_events_url() -> None:
    module = _module()
    values = _ready_values() | {
        "ENVIRONMENT": "production",
        "DOMAIN": "mifp.eu",
        "WWW_DOMAIN": "www.mifp.eu",
        "EVENTS_PUBLIC_BASE_URL": "http://events.mifp.eu",
    }

    assert any("HTTPS" in error for error in module.validate_configuration(values))

def test_legacy_werkzeug_scrypt_admin_hash_is_accepted() -> None:
    module = _module()
    values = _ready_values() | {
        "ADMIN_PASSWORD_HASH": "scrypt:32768:8:1$F8JPGfIsY0gilnax$" + "a" * 128
    }
    assert module.validate_configuration(values) == []


def test_deterministic_atomic_saves_have_no_duplicate_keys(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    values = _ready_values()
    store.save(values)
    values["PUBLIC_IPV4"] = "192.0.2.10"
    store.save(values)
    text = store.config.read_text(encoding="utf-8")
    assert text.count("PUBLIC_IPV4=") == 1
    assert not list(store.config.parent.glob(".config.env.*"))
    assert module.read_env(store.config)["PUBLIC_IPV4"] == "192.0.2.10"


@pytest.mark.parametrize(
    ("key", "value"),
    [("DOMAIN", "bad domain"), ("PUBLIC_IPV4", "999.1.2.3"),
     ("SMTP_PORT", "70000"), ("SMTP_SECURITY", "sometimes")],
)
def test_invalid_values_are_rejected(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        _module().normalize_value(key, value)


def test_show_redacts_every_secret_and_check_is_read_only(tmp_path: Path, capsys) -> None:
    module, store = _store(tmp_path)
    values = _ready_values() | {"SMTP_PASSWORD": "smtp-top-secret", "RESTIC_PASSWORD": "backup-top-secret"}
    store.save(values)
    docker_config = tmp_path / "docker.json"
    docker_config.write_text(json.dumps({"auths": {"ghcr.io": {"auth": "opaque"}}}), encoding="utf-8")
    before = (store.config.read_bytes(), store.secrets_path.read_bytes(), store.runtime.read_bytes())

    module.print_show(store.values(), docker_config)
    output = capsys.readouterr().out
    assert "smtp-top-secret" not in output
    assert "backup-top-secret" not in output
    assert output.count("configured") >= 4
    errors, notes = module.check_configuration(store.values(), docker_config=docker_config)
    assert errors == []
    assert "NOT CONFIGURED (optional)" in "\n".join(notes)
    assert before == (store.config.read_bytes(), store.secrets_path.read_bytes(), store.runtime.read_bytes())
    shown = subprocess.run(
        [
            "python3", str(HELPER),
            "--config-file", str(store.config),
            "--secrets-file", str(store.secrets_path),
            "--runtime-env", str(store.runtime),
            "--docker-config", str(docker_config),
            "show",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert "smtp-top-secret" not in shown.stdout + shown.stderr


def test_required_missing_fails_but_optional_mail_and_remote_backup_do_not(tmp_path: Path) -> None:
    module = _module()
    docker_config = tmp_path / "docker.json"
    docker_config.write_text('{"auths":{"ghcr.io":{}}}', encoding="utf-8")
    errors, _ = module.check_configuration({}, docker_config=docker_config)
    assert any("DOMAIN: missing required" in error for error in errors)
    errors, notes = module.check_configuration(_ready_values(), docker_config=docker_config)
    assert errors == []
    assert "SMTP: NOT CONFIGURED (optional)" in notes
    assert "Remote backup: NOT CONFIGURED (optional)" in notes


def test_fake_dns_detects_wrong_record_without_contacting_provider(tmp_path: Path) -> None:
    module = _module()
    docker_config = tmp_path / "docker.json"
    docker_config.write_text('{"auths":{"ghcr.io":{}}}', encoding="utf-8")
    values = _ready_values() | {
        "ENVIRONMENT": "production", "DOMAIN": "mifp.eu", "WWW_DOMAIN": "www.mifp.eu",
        "EVENTS_PUBLIC_BASE_URL": "https://events.mifp.eu", "DNS_EXPECTED_IPV4": "192.0.2.10",
    }

    resolved: list[str] = []

    def resolver(host: str):
        resolved.append(host)
        return {"192.0.2.10"}, None

    errors, notes = module.check_configuration(values, docker_config=docker_config, resolver=resolver)
    assert errors == []
    assert resolved == ["mifp.eu", "www.mifp.eu"]
    assert not any("events.mifp.eu" in note for note in notes)
    assert any("DNS mifp.eu: OK" in note for note in notes)


def test_disabled_backend_does_not_require_events_public_url(tmp_path: Path) -> None:
    module = _module()
    docker_config = tmp_path / "docker.json"
    docker_config.write_text('{}', encoding="utf-8")
    values = _ready_values() | {
        "ENVIRONMENT": "production",
        "DOMAIN": "mifp.eu",
        "WWW_DOMAIN": "www.mifp.eu",
        "DNS_EXPECTED_IPV4": "192.0.2.10",
    }
    values.pop("EVENTS_PUBLIC_BASE_URL")

    errors, _ = module.check_configuration(
        values,
        docker_config=docker_config,
        resolver=lambda _host: ({"192.0.2.10"}, None),
    )

    assert errors == []


def test_local_vps_requires_event_dns_but_not_remote_credentials(tmp_path: Path) -> None:
    module = _module()
    docker_config = tmp_path / "docker.json"
    docker_config.write_text("{}", encoding="utf-8")
    values = _ready_values() | {
        "ENVIRONMENT": "production", "DOMAIN": "mifp.eu", "WWW_DOMAIN": "www.mifp.eu",
        "EVENTS_PUBLISH_BACKEND": "local-vps",
        "EVENTS_PUBLIC_BASE_URL": "https://events.mifp.eu",
        "EVENTS_LOCAL_ROOT": "/srv/mifp-events",
        "DNS_EXPECTED_IPV4": "192.0.2.10",
    }
    resolved = []

    def resolver(host):
        resolved.append(host)
        return {"192.0.2.10"}, None

    errors, notes = module.check_configuration(values, docker_config=docker_config, resolver=resolver)
    assert errors == []
    assert resolved == ["mifp.eu", "www.mifp.eu", "events.mifp.eu"]
    assert any("Event hosting: local-vps" in note for note in notes)
    assert not any("EVENTS_REMOTE_PASSWORD" in error for error in errors)


def test_remote_transition_skips_vps_event_dns_and_reports_missing_credentials(tmp_path: Path) -> None:
    module = _module()
    docker_config = tmp_path / "docker.json"
    docker_config.write_text("{}", encoding="utf-8")
    values = _ready_values() | {
        "ENVIRONMENT": "production", "DOMAIN": "mifp.eu", "WWW_DOMAIN": "www.mifp.eu",
        "EVENTS_PUBLISH_BACKEND": "remote",
        "EVENTS_PUBLIC_BASE_URL": "https://events.mifp.eu",
        "EVENTS_LOCAL_ROOT": "/srv/mifp-events",
        "EVENTS_REMOTE_PROTOCOL": "ftps",
        "DNS_EXPECTED_IPV4": "192.0.2.10",
    }
    resolved = []

    def resolver(host):
        resolved.append(host)
        return {"192.0.2.10"}, None

    errors, notes = module.check_configuration(values, docker_config=docker_config, resolver=resolver)
    assert errors == []
    assert resolved == ["mifp.eu", "www.mifp.eu"]
    assert any("Event publisher: UNAVAILABLE" in note for note in notes)
    # Switching back is configuration-only and keeps the stable public origin.
    values["EVENTS_PUBLISH_BACKEND"] = "local-vps"
    assert values["EVENTS_PUBLIC_BASE_URL"] == "https://events.mifp.eu"


def test_public_ghcr_is_ready_without_credentials_and_pat_is_never_copied(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values())
    docker_config = tmp_path / "docker.json"
    docker_config.write_text("{}", encoding="utf-8")
    errors, notes = module.check_configuration(store.values(), docker_config=docker_config)
    assert errors == []
    assert "Registry authentication: not configured (optional; public images use anonymous access)" in notes
    docker_config.write_text('{"auths":{"ghcr.io":{"auth":"PAT-only-in-docker"}}}', encoding="utf-8")
    errors, notes = module.check_configuration(store.values(), docker_config=docker_config)
    assert errors == []
    assert "Registry authentication: configured (optional)" in notes
    assert "PAT-only-in-docker" not in store.config.read_text(encoding="utf-8")
    assert "PAT-only-in-docker" not in store.secrets_path.read_text(encoding="utf-8")


def test_partial_repeated_wizard_preserves_other_sections(monkeypatch, tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values() | {"PUBLIC_IPV4": "192.0.2.10"})
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    answers = iter(["smtp", "smtp.aruba.it", "465", "tls", "info@mifp.eu", "info@mifp.eu", "MIFP", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: "smtp-secret")
    assert module.run_wizard(store, "mail", tmp_path / "missing-docker.json") == 0
    assert store.values()["PUBLIC_IPV4"] == "192.0.2.10"
    saved = store.values()
    assert saved["MAIL_PROVIDER"] == "smtp"
    assert saved["SMTP_HOST"] == "smtp.aruba.it"
    assert saved["SMTP_PORT"] == "465"
    assert saved["SMTP_SECURITY"] == "tls"
    assert saved["SMTP_USERNAME"] == "info@mifp.eu"
    assert saved["SMTP_FROM_ADDRESS"] == "info@mifp.eu"
    assert saved["SMTP_FROM_NAME"] == "MIFP"
    assert saved["SMTP_PASSWORD"] == "smtp-secret"
    assert "SMTP_PASSWORD" not in module.read_env(store.config)
    assert module.read_env(store.secrets_path)["SMTP_PASSWORD"] == "smtp-secret"
    assert module.read_env(store.runtime)["SMTP_PASSWORD"] == ""


def test_cancelled_wizard_does_not_modify_existing_files(monkeypatch, tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values())
    before = (store.config.read_bytes(), store.secrets_path.read_bytes(), store.runtime.read_bytes())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    answers = iter(["", "", "", "", "", "", "n"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    assert module.run_wizard(store, "web", tmp_path / "docker.json") == 0
    assert before == (store.config.read_bytes(), store.secrets_path.read_bytes(), store.runtime.read_bytes())


def test_cli_set_unset_and_secret_command_line_refusal(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.migrate(domain="vpsbox.home.arpa")
    base = [
        "python3", str(HELPER), "--config-file", str(store.config),
        "--secrets-file", str(store.secrets_path), "--runtime-env", str(store.runtime),
        "--example", str(store.example),
    ]
    subprocess.run([*base, "set", "PUBLIC_IPV4", "192.0.2.42"], check=True)
    assert module.read_env(store.config)["PUBLIC_IPV4"] == "192.0.2.42"
    subprocess.run([*base, "unset", "PUBLIC_IPV4"], check=True)
    assert "PUBLIC_IPV4" not in module.read_env(store.config)
    rejected = subprocess.run([*base, "set", "SMTP_PASSWORD", "leak"], text=True, capture_output=True)
    assert rejected.returncode != 0
    assert "Refusing secret on command line" in rejected.stderr
    assert "leak" not in store.config.read_text(encoding="utf-8")


@pytest.mark.parametrize(("security", "uses_ssl", "starts_tls"), [("tls", True, False), ("starttls", False, True), ("none", False, False)])
def test_mailer_honors_explicit_smtp_security(monkeypatch, security: str, uses_ssl: bool, starts_tls: bool) -> None:
    from mifp_app.services import mailer

    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs):
            calls.append("ssl" if type(self).__name__ == "FakeSSL" else "plain")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self):
            calls.append("starttls")

        def login(self, *_args):
            calls.append("login")

        def send_message(self, _message):
            calls.append("send")

    class FakeSSL(FakeSMTP):
        pass

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSSL)
    app = SimpleNamespace(config={
        "MAIL_PROVIDER": "smtp", "MAIL_FROM": "info@mifp.eu", "MAIL_FROM_NAME": "MIFP",
        "SMTP_HOST": "smtp.example", "SMTP_PORT": 465 if security == "tls" else 587,
        "SMTP_SECURITY": security, "SMTP_USERNAME": "user", "SMTP_PASSWORD": "password",
    })
    assert mailer.send_mail(app, to="test@example.net", subject="test", body="body") is True
    assert ("ssl" in calls) is uses_ssl
    assert ("starttls" in calls) is starts_tls
    assert "send" in calls


def test_render_msmtp_config_keeps_smtp_secret_out_of_cli_and_event_files(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    values = _ready_values() | {
        "MAIL_PROVIDER": "smtp",
        "SMTP_HOST": "smtp.example.net",
        "SMTP_PORT": "587",
        "SMTP_SECURITY": "starttls",
        "SMTP_USERNAME": "mailer@example.net",
        "SMTP_PASSWORD": 'test secret # with "quotes" and \\slashes',
        "SMTP_FROM_ADDRESS": "no-reply@example.net",
        "SMTP_FROM_NAME": "MIFP",
    }
    store.save(values)
    output = tmp_path / "msmtprc"

    assert module.render_msmtp_config(store.values(), output) is True
    rendered = output.read_text(encoding="utf-8")

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert 'host "smtp.example.net"' in rendered
    assert "tls on" in rendered
    assert "tls_starttls on" in rendered
    assert 'user "mailer@example.net"' in rendered
    assert 'password "test secret # with \\"quotes\\" and \\\\slashes"' in rendered
    assert "SMTP_PASSWORD" not in rendered
    cli_output = tmp_path / "msmtprc-cli"
    rendered_by_cli = subprocess.run(
        [
            "python3", str(HELPER),
            "--config-file", str(store.config),
            "--secrets-file", str(store.secrets_path),
            "--runtime-env", str(store.runtime),
            "render-mail-relay", "--output", str(cli_output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rendered_by_cli.returncode == 0, rendered_by_cli.stderr
    assert rendered_by_cli.stdout.strip() == "configured"
    assert values["SMTP_PASSWORD"] not in rendered_by_cli.stdout + rendered_by_cli.stderr
    assert cli_output.read_text(encoding="utf-8") == rendered


def test_render_msmtp_config_removes_transport_when_mail_disabled(tmp_path: Path) -> None:
    module, _store_obj = _store(tmp_path)
    output = tmp_path / "msmtprc"
    output.write_text("old-secret\n", encoding="utf-8")

    assert module.render_msmtp_config({"MAIL_PROVIDER": "disabled"}, output) is False
    assert not output.exists()


def test_invalid_smtp_configuration_removes_stale_credential_relay(tmp_path: Path) -> None:
    module = _module()
    output = tmp_path / "msmtprc"
    output.write_text('password "old-secret"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="SMTP_PASSWORD"):
        module.render_msmtp_config(
            {
                "MAIL_PROVIDER": "smtp",
                "SMTP_HOST": "smtp.example.net",
                "SMTP_PORT": "587",
                "SMTP_SECURITY": "starttls",
                "SMTP_USERNAME": "mailer@example.net",
                "SMTP_PASSWORD": "",
                "SMTP_FROM_ADDRESS": "no-reply@example.net",
            },
            output,
        )

    assert not output.exists()


def test_php_regform_mail_transport_is_host_managed() -> None:
    bootstrap = (ROOT / "deploy" / "bootstrap-vps.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")

    assert "msmtp-mta" in bootstrap
    assert "php_admin_value[sendmail_path] = /usr/bin/msmtp" in bootstrap
    assert 'systemctl enable "$PHP_FPM_SERVICE"' in bootstrap
    assert 'systemctl restart "$PHP_FPM_SERVICE"' in bootstrap
    assert 'systemctl enable --now "$PHP_FPM_SERVICE"' not in bootstrap
    assert "render-mail-relay --output" in bootstrap
    assert "sync_mail_relay" in deploy
    assert 'chown root:"$EVENTS_PHP_USER" "$MAIL_RELAY_CONFIG"' in deploy
    assert 'chmod 0640 "$MAIL_RELAY_CONFIG"' in deploy

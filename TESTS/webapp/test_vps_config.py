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
        "EVENTS_DOMAIN": "events.vpsbox.home.arpa",
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
        "EVENTS_DOMAIN": "events.mifp.eu", "DNS_EXPECTED_IPV4": "192.0.2.10",
    }

    def resolver(host: str):
        return ({"192.0.2.20"} if host.startswith("events.") else {"192.0.2.10"}), None

    errors, notes = module.check_configuration(values, docker_config=docker_config, resolver=resolver)
    assert any("DNS events.mifp.eu: expected IPv4 192.0.2.10" in error for error in errors)
    assert any("DNS mifp.eu: OK" in note for note in notes)


def test_registry_auth_is_required_but_pat_is_never_copied(tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values())
    docker_config = tmp_path / "docker.json"
    docker_config.write_text("{}", encoding="utf-8")
    errors, _ = module.check_configuration(store.values(), docker_config=docker_config)
    assert any("Registry authentication" in error for error in errors)
    docker_config.write_text('{"auths":{"ghcr.io":{"auth":"PAT-only-in-docker"}}}', encoding="utf-8")
    errors, _ = module.check_configuration(store.values(), docker_config=docker_config)
    assert errors == []
    assert "PAT-only-in-docker" not in store.config.read_text(encoding="utf-8")
    assert "PAT-only-in-docker" not in store.secrets_path.read_text(encoding="utf-8")


def test_partial_repeated_wizard_preserves_other_sections(monkeypatch, tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values() | {"PUBLIC_IPV4": "192.0.2.10"})
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    answers = iter(["aruba", "smtp.aruba.it", "465", "tls", "info@mifp.eu", "info@mifp.eu", "MIFP", "", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: "smtp-secret")
    assert module.run_wizard(store, "mail", tmp_path / "missing-docker.json") == 0
    assert store.values()["PUBLIC_IPV4"] == "192.0.2.10"
    assert store.values()["SMTP_PASSWORD"] == "smtp-secret"


def test_cancelled_wizard_does_not_modify_existing_files(monkeypatch, tmp_path: Path) -> None:
    module, store = _store(tmp_path)
    store.save(_ready_values())
    before = (store.config.read_bytes(), store.secrets_path.read_bytes(), store.runtime.read_bytes())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    answers = iter(["", "", "", "", "", "", "", "n"])
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

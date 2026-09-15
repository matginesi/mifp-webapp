from __future__ import annotations

import builtins
import importlib.util
import stat
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "deploy" / "configure.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("mifp_deploy_configure_test", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_generates_secret_and_host_settings_without_plaintext_password(tmp_path: Path) -> None:
    module = _load_helper()
    example = tmp_path / ".env.example"
    env_file = tmp_path / ".env"
    example.write_text(
        "SECRET_KEY='CHANGE-ME-generate-with-openssl-rand-hex-32'\n"
        "ADMIN_USERNAME='admin'\n"
        "ADMIN_PASSWORD_HASH=''\n"
        "MIFP_DOMAIN='example.invalid'\n"
        "MIFP_IMAGE_REPOSITORY='ghcr.io/example/mifp'\n",
        encoding="utf-8",
    )
    args = module.argparse.Namespace(
        env_file=env_file,
        example=example,
        domain="mifp.eu",
        image_repository="ghcr.io/matginesi/mifp-webapp",
        admin=False,
        admin_if_missing=False,
        username=None,
    )
    assert module.configure(args) == 0
    values = module.read_env(env_file)
    assert len(values["SECRET_KEY"]) >= 64
    assert values["MIFP_DOMAIN"] == "mifp.eu"
    assert values["MIFP_IMAGE_REPOSITORY"] == "ghcr.io/matginesi/mifp-webapp"
    assert "ADMIN_PASSWORD" not in values
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_admin_prompt_requires_ten_characters_and_persists_only_hash(monkeypatch, tmp_path: Path) -> None:
    module = _load_helper()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n"
        "ADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH=''\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "matteo")
    passwords = iter(["admin_1234", "admin_1234"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: next(passwords))
    module.prompt_admin(env_file)
    values = module.read_env(env_file)
    assert values["ADMIN_USERNAME"] == "matteo"
    assert values["ADMIN_PASSWORD_HASH"].startswith("pbkdf2:sha256:600000$")
    assert "admin_1234" not in env_file.read_text(encoding="utf-8")


def test_admin_password_mismatch_retries_without_losing_configuration(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = _load_helper()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY='a-preserved-secret-that-is-long-enough-1234567890'\n"
        "ADMIN_USERNAME='existing-admin'\nADMIN_PASSWORD_HASH=''\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _prompt: "existing-admin")
    passwords = iter(["first-password", "different-password", "final-pass-123", "final-pass-123"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: next(passwords))

    module.prompt_admin(env_file)

    values = module.read_env(env_file)
    assert values["SECRET_KEY"] == "a-preserved-secret-that-is-long-enough-1234567890"
    assert values["ADMIN_USERNAME"] == "existing-admin"
    assert values["ADMIN_PASSWORD_HASH"].startswith("pbkdf2:sha256:600000$")
    assert "Passwords do not match. Try again." in capsys.readouterr().err
    assert "first-password" not in env_file.read_text(encoding="utf-8")


def test_config_check_rejects_missing_admin_hash(tmp_path: Path) -> None:
    module = _load_helper()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n"
        "ADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH=''\n"
        "MIFP_DOMAIN='mifp.eu'\n"
        "MIFP_IMAGE_REPOSITORY='ghcr.io/matginesi/mifp-webapp'\n",
        encoding="utf-8",
    )
    assert module.check_config(env_file, quiet=True) == 1


def test_configure_preserves_existing_admin_and_secret(monkeypatch, tmp_path: Path) -> None:
    module = _load_helper()
    env_file = tmp_path / ".env"
    example = tmp_path / ".env.example"
    existing_hash = "pbkdf2:sha256:600000$abcd1234$" + "a" * 64
    existing_secret = "b" * 64
    env_file.write_text(
        f"SECRET_KEY='{existing_secret}'\n"
        "ADMIN_USERNAME='existing-admin'\n"
        f"ADMIN_PASSWORD_HASH='{existing_hash}'\n",
        encoding="utf-8",
    )
    example.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "prompt_admin",
        lambda *_args, **_kwargs: pytest.fail("existing admin must not be prompted or replaced"),
    )
    args = module.argparse.Namespace(
        env_file=env_file,
        example=example,
        domain="mifp.eu",
        image_repository="ghcr.io/matginesi/mifp-webapp",
        admin=False,
        admin_if_missing=True,
        username=None,
    )

    assert module.configure(args) == 0
    values = module.read_env(env_file)
    assert values["SECRET_KEY"] == existing_secret
    assert values["ADMIN_USERNAME"] == "existing-admin"
    assert values["ADMIN_PASSWORD_HASH"] == existing_hash

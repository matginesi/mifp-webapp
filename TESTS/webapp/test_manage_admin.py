from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGE_PATH = REPO_ROOT / "MIFPAPP" / "CORE" / "manage.py"


def _load_manage():
    spec = importlib.util.spec_from_file_location("mifp_manage_test", MANAGE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(module, tmp_path: Path, *, username: str | None = None):
    return module.argparse.Namespace(
        env_file=tmp_path / ".env",
        username=username,
    )


def test_admin_password_must_have_at_least_ten_characters(monkeypatch, tmp_path: Path) -> None:
    module = _load_manage()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY='secret'\nADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH='existing'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: "matteo")
    passwords = iter(["123456789", "123456789"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: next(passwords))
    with pytest.raises(SystemExit, match="at least 10"):
        module.configure_admin(_args(module, tmp_path))


def test_admin_accepts_ten_character_password(monkeypatch, tmp_path: Path) -> None:
    module = _load_manage()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY='secret'\nADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH='existing'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: "matteo")
    passwords = iter(["1234567890", "1234567890"])
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: next(passwords))
    assert module.configure_admin(_args(module, tmp_path)) == 0
    values = module.read_env(env_file)
    assert values["ADMIN_USERNAME"] == "matteo"
    assert values["ADMIN_PASSWORD_HASH"].startswith("pbkdf2:sha256:")


def test_password_hash_is_written_as_compose_safe_literal(tmp_path: Path) -> None:
    module = _load_manage()
    env_file = tmp_path / ".env"
    value = "pbkdf2:sha256:600000$salt$digest"
    module.update_env(env_file, {"ADMIN_PASSWORD_HASH": value})
    text = env_file.read_text(encoding="utf-8")
    assert "ADMIN_PASSWORD_HASH='pbkdf2:sha256:600000$salt$digest'" in text
    assert module.read_env(env_file)["ADMIN_PASSWORD_HASH"] == value


def test_bootstrap_rewrites_legacy_double_quoted_hash_safely(tmp_path: Path) -> None:
    module = _load_manage()
    env_file = tmp_path / ".env"
    example = tmp_path / ".env.example"
    value = "pbkdf2:sha256:600000$salt$digest"
    env_file.write_text(
        f'SECRET_KEY="secret"\nADMIN_USERNAME="admin"\nADMIN_PASSWORD_HASH="{value}"\n',
        encoding="utf-8",
    )
    example.write_text("", encoding="utf-8")
    args = module.argparse.Namespace(env_file=env_file, example=example)
    assert module.bootstrap(args) == 0
    assert f"ADMIN_PASSWORD_HASH='{value}'" in env_file.read_text(encoding="utf-8")


def test_bootstrap_never_generates_admin_password(tmp_path: Path) -> None:
    module = _load_manage()
    env_file = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text(
        "SECRET_KEY='change-me'\nADMIN_USERNAME='admin'\nADMIN_PASSWORD_HASH=''\n",
        encoding="utf-8",
    )
    args = module.argparse.Namespace(env_file=env_file, example=example)
    assert module.bootstrap(args) == 0
    values = module.read_env(env_file)
    assert values["SECRET_KEY"] != "change-me"
    assert not values.get("ADMIN_PASSWORD_HASH")

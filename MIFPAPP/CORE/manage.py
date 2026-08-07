#!/usr/bin/env python3
"""Small dependency-free configuration helper used by the root ``mifp`` launcher."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
import secrets
import stat
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
MIN_ADMIN_PASSWORD_LENGTH = 10

_PLACEHOLDERS = {
    "",
    "change-me",
    "change_me",
    "change-me-generate-with-openssl-rand-hex-32",
    "replace-with-a-long-random-password",
}


def _decode(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace(r"\n", "\n")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_RE.match(raw)
        if not match:
            continue
        key = match.group(1)
        values[key] = _decode(raw.split("=", 1)[1])
    return values


def _encode(value: str) -> str:
    # Docker Compose performs variable expansion in unquoted and double-quoted
    # env-file values. Werkzeug password hashes contain ``$`` separators, so
    # write such values as single-quoted literals to preserve the exact hash.
    if "$" in value and "'" not in value and "\n" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", r"\n")
    return f'"{escaped}"'


def update_env(path: Path, updates: dict[str, str], removals: set[str] | None = None) -> None:
    removals = removals or set()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    written: set[str] = set()
    output: list[str] = []

    for raw in lines:
        match = _ENV_RE.match(raw)
        if not match:
            output.append(raw)
            continue
        key = match.group(1)
        if key in removals:
            continue
        if key in updates:
            if key not in written:
                output.append(f"{key}={_encode(updates[key])}")
                written.add(key)
            continue
        output.append(raw)

    if output and output[-1].strip():
        output.append("")
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={_encode(value)}")

    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    temporary.replace(path)


def is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    return normalized in _PLACEHOLDERS or normalized.startswith("change-me")


def password_hash(password: str, *, iterations: int = 600_000) -> str:
    """Create a Werkzeug-compatible pbkdf2 password hash using stdlib only."""
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return f"pbkdf2:sha256:{iterations}${salt}${digest}"


def ensure_example(env_file: Path, example: Path) -> None:
    if env_file.exists():
        return
    if not example.is_file():
        raise SystemExit(f"Environment template not found: {example}")
    env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def bootstrap(args: argparse.Namespace) -> int:
    """Initialize non-secret runtime configuration only.

    Administrator passwords are intentionally never generated, printed, or
    persisted in plaintext by bootstrap. Use ``manage.py admin`` interactively.
    """
    env_file = args.env_file.resolve()
    ensure_example(env_file, args.example.resolve())
    values = read_env(env_file)
    updates: dict[str, str] = {}
    removals: set[str] = {"ADMIN_PASSWORD"}
    if is_placeholder(values.get("SECRET_KEY")):
        updates["SECRET_KEY"] = secrets.token_hex(32)
    username = values.get("ADMIN_USERNAME", "admin").strip() or "admin"
    updates["ADMIN_USERNAME"] = username
    current_hash = values.get("ADMIN_PASSWORD_HASH", "").strip()
    if current_hash and not is_placeholder(current_hash):
        updates["ADMIN_PASSWORD_HASH"] = current_hash
    update_env(env_file, updates, removals)
    if not current_hash or is_placeholder(current_hash):
        print("Administrator password is not configured. Run: ./mifp admin")
    return 0

def configure_admin(args: argparse.Namespace) -> int:
    """Interactively set the administrator password and persist only its hash."""
    env_file = args.env_file.resolve()
    if not env_file.is_file():
        raise SystemExit(f"Environment file not found: {env_file}")
    values = read_env(env_file)
    current_username = values.get("ADMIN_USERNAME", "admin").strip() or "admin"
    username = (args.username or "").strip()
    if not username:
        entered = input(f"Administrator username [{current_username}]: ").strip()
        username = entered or current_username
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
        raise SystemExit("Username must contain 1-64 characters: letters, numbers, _, ., @ or -")
    first = getpass.getpass(
        f"New administrator password (minimum {MIN_ADMIN_PASSWORD_LENGTH} characters): "
    )
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords do not match")
    if len(first) < MIN_ADMIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must contain at least {MIN_ADMIN_PASSWORD_LENGTH} characters")
    update_env(
        env_file,
        {"ADMIN_USERNAME": username, "ADMIN_PASSWORD_HASH": password_hash(first)},
        {"ADMIN_PASSWORD"},
    )
    print("Administrator credentials updated. Only the password hash was persisted.")
    return 0

def set_values(args: argparse.Namespace) -> int:
    updates: dict[str, str] = {}
    for item in args.assignments:
        if "=" not in item:
            raise SystemExit(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        if not _KEY_RE.fullmatch(key):
            raise SystemExit(f"Invalid environment key: {key}")
        updates[key] = value
    update_env(args.env_file.resolve(), updates)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_bootstrap = sub.add_parser("bootstrap")
    p_bootstrap.add_argument("--env-file", type=Path, required=True)
    p_bootstrap.add_argument("--example", type=Path, required=True)
    p_bootstrap.set_defaults(func=bootstrap)

    p_admin = sub.add_parser("admin")
    p_admin.add_argument("--env-file", type=Path, required=True)
    p_admin.add_argument("--username", default=None)
    p_admin.set_defaults(func=configure_admin)

    p_set = sub.add_parser("set")
    p_set.add_argument("--env-file", type=Path, required=True)
    p_set.add_argument("assignments", nargs="+")
    p_set.set_defaults(func=set_values)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

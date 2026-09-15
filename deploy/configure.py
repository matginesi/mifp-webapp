#!/usr/bin/env python3
"""Dependency-free production configuration helper for the MIFP VPS.

It never stores an administrator password in plaintext. Password hashing uses
Werkzeug's PBKDF2-SHA256 wire format so the Flask application can verify the
result without requiring Flask/Werkzeug on the host.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import re
import secrets
import stat
import sys
from pathlib import Path

MIN_ADMIN_PASSWORD_LENGTH = 10
PBKDF2_ITERATIONS = 600_000
_ENV_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_IMAGE_REPO_RE = re.compile(r"^ghcr\.io/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PLACEHOLDERS = {
    "", "change-me", "change_me", "dev-change-me", "dev-only-insecure-key",
    "change-me-generate-with-openssl-rand-hex-32",
}


def _decode(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace(r"\n", "\n")


def _encode(value: str) -> str:
    # Preserve Werkzeug hashes containing '$' from Compose interpolation.
    if "$" in value and "'" not in value and "\n" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", r"\n")
    return f'"{escaped}"'


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_RE.match(raw)
        if match:
            values[match.group(1)] = _decode(raw.split("=", 1)[1])
    return values


def update_env(path: Path, updates: dict[str, str], removals: set[str] | None = None) -> None:
    removals = removals or set()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    output: list[str] = []
    written: set[str] = set()
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)


def is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    return normalized in _PLACEHOLDERS or normalized.startswith("change-me")


def make_password_hash(password: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2:sha256:{PBKDF2_ITERATIONS}${salt}${digest}"


def ensure_env_file(env_file: Path, example: Path | None) -> None:
    if env_file.exists():
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return
    if example is None or not example.is_file():
        raise SystemExit(f"Environment template not found: {example}")
    env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def prompt_admin(env_file: Path, *, username: str | None = None) -> None:
    if not sys.stdin.isatty():
        raise SystemExit("Administrator setup requires an interactive terminal")
    values = read_env(env_file)
    current = values.get("ADMIN_USERNAME", "admin").strip() or "admin"
    chosen = (username or "").strip()
    if not chosen:
        chosen = input(f"Administrator username [{current}]: ").strip() or current
    if not _USERNAME_RE.fullmatch(chosen):
        raise SystemExit("Administrator username contains invalid characters")
    while True:
        first = getpass.getpass(
            f"New administrator password (minimum {MIN_ADMIN_PASSWORD_LENGTH} characters): "
        )
        second = getpass.getpass("Repeat password: ")
        if first != second:
            print("Passwords do not match. Try again.", file=sys.stderr)
            first = second = ""
            continue
        if len(first) < MIN_ADMIN_PASSWORD_LENGTH:
            print(
                f"Password must contain at least {MIN_ADMIN_PASSWORD_LENGTH} characters. "
                "Try again.",
                file=sys.stderr,
            )
            first = second = ""
            continue
        break
    update_env(
        env_file,
        {"ADMIN_USERNAME": chosen, "ADMIN_PASSWORD_HASH": make_password_hash(first)},
        {"ADMIN_PASSWORD"},
    )
    print(f"Administrator configured: {chosen}. Only the password hash was saved.")


def validate_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if not _DOMAIN_RE.fullmatch(domain) or "." not in domain:
        raise SystemExit(f"Invalid domain: {domain}")
    return domain


def validate_image_repository(repo: str) -> str:
    repo = repo.strip().rstrip("/")
    if not _IMAGE_REPO_RE.fullmatch(repo):
        raise SystemExit(
            "Image repository must look like ghcr.io/<owner>/<repository> without tag"
        )
    return repo


def configure(args: argparse.Namespace) -> int:
    env_file = args.env_file.resolve()
    example = args.example.resolve() if args.example else None
    ensure_env_file(env_file, example)
    values = read_env(env_file)
    updates: dict[str, str] = {}

    if is_placeholder(values.get("SECRET_KEY")) or len(values.get("SECRET_KEY", "")) < 32:
        updates["SECRET_KEY"] = secrets.token_hex(32)
    if args.domain:
        domain = validate_domain(args.domain)
        updates["MIFP_DOMAIN"] = domain
        updates["TRUSTED_HOSTS"] = f"{domain},www.{domain},127.0.0.1,localhost"
    if args.image_repository:
        updates["MIFP_IMAGE_REPOSITORY"] = validate_image_repository(args.image_repository)
    update_env(env_file, updates, {"ADMIN_PASSWORD"})

    values = read_env(env_file)
    needs_admin = not values.get("ADMIN_PASSWORD_HASH", "").strip()
    if args.admin or (args.admin_if_missing and needs_admin):
        prompt_admin(env_file, username=args.username)
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return check_config(env_file, quiet=False, allow_missing_admin=not args.admin_if_missing)


def check_config(env_file: Path, *, quiet: bool, allow_missing_admin: bool = False) -> int:
    values = read_env(env_file)
    errors: list[str] = []
    secret = values.get("SECRET_KEY", "")
    if is_placeholder(secret) or len(secret) < 32:
        errors.append("SECRET_KEY is missing, placeholder, or shorter than 32 characters")
    username = values.get("ADMIN_USERNAME", "").strip()
    if not username or not _USERNAME_RE.fullmatch(username):
        errors.append("ADMIN_USERNAME is missing or invalid")
    admin_hash = values.get("ADMIN_PASSWORD_HASH", "").strip()
    if not admin_hash:
        if not allow_missing_admin:
            errors.append("ADMIN_PASSWORD_HASH is missing")
    elif not re.fullmatch(
        r"(?:pbkdf2:sha256:\d+|scrypt:\d+:\d+:\d+)\$[^$\s]+\$[^$\s]+",
        admin_hash,
    ):
        errors.append("ADMIN_PASSWORD_HASH has an unexpected format")
    domain = values.get("MIFP_DOMAIN", "").strip().lower()
    if domain and (not _DOMAIN_RE.fullmatch(domain) or "." not in domain):
        errors.append("MIFP_DOMAIN is invalid")
    repo = values.get("MIFP_IMAGE_REPOSITORY", "").strip()
    if repo and not _IMAGE_REPO_RE.fullmatch(repo.rstrip("/")):
        errors.append("MIFP_IMAGE_REPOSITORY is invalid")
    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1
    if not quiet:
        print("Production configuration OK.")
    return 0


def command_admin(args: argparse.Namespace) -> int:
    env_file = args.env_file.resolve()
    if not env_file.is_file():
        raise SystemExit(f"Environment file not found: {env_file}")
    prompt_admin(env_file, username=args.username)
    return 0


def command_check(args: argparse.Namespace) -> int:
    return check_config(args.env_file.resolve(), quiet=args.quiet)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("configure", help="generate secret and configure host settings")
    p_config.add_argument("--env-file", type=Path, required=True)
    p_config.add_argument("--example", type=Path)
    p_config.add_argument("--domain")
    p_config.add_argument("--image-repository")
    p_config.add_argument("--admin", action="store_true", help="force admin password rotation")
    p_config.add_argument("--admin-if-missing", action="store_true")
    p_config.add_argument("--username")
    p_config.set_defaults(func=configure)

    p_admin = sub.add_parser("admin", help="rotate administrator credentials")
    p_admin.add_argument("--env-file", type=Path, required=True)
    p_admin.add_argument("--username")
    p_admin.set_defaults(func=command_admin)

    p_check = sub.add_parser("check", help="validate production configuration")
    p_check.add_argument("--env-file", type=Path, required=True)
    p_check.add_argument("--quiet", action="store_true")
    p_check.set_defaults(func=command_check)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Manage progressive MIFP VPS configuration without third-party packages."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import secrets
import shlex
import socket
import sys
import tempfile
from pathlib import Path
from typing import Callable

PUBLIC_KEYS = (
    "ENVIRONMENT", "HOSTNAME", "PUBLIC_IPV4", "PUBLIC_IPV6", "TIMEZONE",
    "DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN", "IMAGE_REPOSITORY",
    "REGISTRY", "REGISTRY_USERNAME", "DNS_PROVIDER", "DNS_EXPECTED_IPV4",
    "DNS_EXPECTED_IPV6", "MAIL_PROVIDER", "SMTP_HOST", "SMTP_PORT",
    "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_FROM_ADDRESS", "SMTP_FROM_NAME",
    "BACKUP_ENABLED", "BACKUP_LOCAL_RETENTION", "RESTIC_REPOSITORY",
    "ADMIN_USERNAME",
)
SECRET_KEYS = ("SECRET_KEY", "ADMIN_PASSWORD_HASH", "SMTP_PASSWORD", "RESTIC_PASSWORD")
ALL_KEYS = frozenset(PUBLIC_KEYS + SECRET_KEYS)
SECRET_INPUT_KEYS = frozenset({"SMTP_PASSWORD", "RESTIC_PASSWORD", "SECRET_KEY", "ADMIN_PASSWORD_HASH"})
REQUIRED_KEYS = (
    "ENVIRONMENT", "DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN",
    "IMAGE_REPOSITORY", "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "SECRET_KEY",
)
DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
REPO_RE = re.compile(r"^ghcr\.io/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
ADMIN_HASH_RE = re.compile(
    r"^(?:pbkdf2:sha256:\d+|scrypt:\d+:\d+:\d+)\$[^$\s]+\$[^$\s]+$"
)

RUNTIME_TO_CANONICAL = {
    "FLASK_ENV": "ENVIRONMENT",
    "MIFP_DOMAIN": "DOMAIN",
    "MIFP_IMAGE_REPOSITORY": "IMAGE_REPOSITORY",
    "MAIL_PROVIDER": "MAIL_PROVIDER",
    "SMTP_HOST": "SMTP_HOST",
    "SMTP_PORT": "SMTP_PORT",
    "SMTP_SECURITY": "SMTP_SECURITY",
    "SMTP_USERNAME": "SMTP_USERNAME",
    "SMTP_PASSWORD": "SMTP_PASSWORD",
    "MAIL_FROM": "SMTP_FROM_ADDRESS",
    "MIFP_BACKUP_KEEP": "BACKUP_LOCAL_RETENTION",
    "MIFP_RESTIC_REPOSITORY": "RESTIC_REPOSITORY",
    "ADMIN_USERNAME": "ADMIN_USERNAME",
    "ADMIN_PASSWORD_HASH": "ADMIN_PASSWORD_HASH",
    "SECRET_KEY": "SECRET_KEY",
}


def decode_env(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parts = shlex.split(value, posix=True)
    except ValueError:
        return value.strip("'\"")
    return parts[0] if len(parts) == 1 else value.strip("'\"")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.removeprefix("export ").split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = decode_env(raw_value)
    return values


def encode_env(value: str) -> str:
    return shlex.quote(value)


def atomic_write_env(path: Path, values: dict[str, str], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by mifpctl. Do not edit while a mifpctl command is running."]
    lines.extend(f"{key}={encode_env(values[key])}" for key in sorted(values) if values[key] != "")
    payload = ("\n".join(lines) + "\n").encode()
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.geteuid() == 0:
            os.chown(temp_name, 0, 0)
        os.replace(temp_name, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def update_runtime_env(path: Path, updates: dict[str, str], example: Path | None) -> None:
    if not path.exists() and example and example.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    output: list[str] = []
    written: set[str] = set()
    for raw in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", raw)
        if not match or match.group(1) not in updates:
            output.append(raw)
            continue
        key = match.group(1)
        if key not in written:
            output.append(f"{key}={encode_env(updates[key])}")
            written.add(key)
    if output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={encode_env(value)}" for key, value in updates.items() if key not in written)
    atomic_write_text(path, "\n".join(output).rstrip() + "\n", 0o600)


def atomic_write_text(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.geteuid() == 0:
            os.chown(temp_name, 0, 0)
        os.replace(temp_name, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class Store:
    def __init__(self, config: Path, secrets_path: Path, runtime: Path, example: Path | None):
        self.config = config
        self.secrets_path = secrets_path
        self.runtime = runtime
        self.example = example

    def values(self) -> dict[str, str]:
        return {**read_env(self.config), **read_env(self.secrets_path)}

    def save(self, values: dict[str, str]) -> None:
        public = {key: values[key] for key in PUBLIC_KEYS if values.get(key)}
        hidden = {key: values[key] for key in SECRET_KEYS if values.get(key)}
        atomic_write_env(self.config, public, mode=0o640)
        atomic_write_env(self.secrets_path, hidden, mode=0o600)
        self.sync_runtime(values)

    def sync_runtime(self, values: dict[str, str]) -> None:
        domain = values.get("DOMAIN", "")
        provider = values.get("MAIL_PROVIDER", "disabled").lower()
        if provider not in {"disabled", "console", "smtp"}:
            provider = "smtp" if values.get("SMTP_HOST") else "disabled"
        runtime_updates = {
            "FLASK_ENV": "production" if values.get("ENVIRONMENT", "production") == "production" else "development",
            "MIFP_DOMAIN": domain,
            "MIFP_IMAGE_REPOSITORY": values.get("IMAGE_REPOSITORY", ""),
            "TRUSTED_HOSTS": ",".join(filter(None, [domain, values.get("WWW_DOMAIN", ""), "127.0.0.1", "localhost"])),
            "MAIL_PROVIDER": provider,
            "SMTP_HOST": values.get("SMTP_HOST", ""),
            "SMTP_PORT": values.get("SMTP_PORT", "587"),
            "SMTP_SECURITY": values.get("SMTP_SECURITY", "starttls"),
            "SMTP_USERNAME": values.get("SMTP_USERNAME", ""),
            "MAIL_FROM": values.get("SMTP_FROM_ADDRESS", ""),
            "MAIL_FROM_NAME": values.get("SMTP_FROM_NAME", ""),
            "MIFP_BACKUP_KEEP": values.get("BACKUP_LOCAL_RETENTION", "14"),
            "MIFP_RESTIC_REPOSITORY": values.get("RESTIC_REPOSITORY", ""),
            "ADMIN_USERNAME": values.get("ADMIN_USERNAME", "admin"),
            # Secrets are loaded from /etc/mifp/secrets.env by Compose/systemd.
            # Blank legacy entries prevent stale plaintext from surviving a migration.
            "SMTP_PASSWORD": "",
            "ADMIN_PASSWORD_HASH": "",
            "SECRET_KEY": "",
        }
        update_runtime_env(self.runtime, runtime_updates, self.example)

    def migrate(
        self, *, domain: str = "", image_repository: str = "", save: bool = True
    ) -> dict[str, str]:
        values = self.values()
        runtime_values = read_env(self.runtime)
        for old_key, new_key in RUNTIME_TO_CANONICAL.items():
            if not values.get(new_key) and runtime_values.get(old_key):
                values[new_key] = runtime_values[old_key]
        if not values.get("SMTP_SECURITY") and runtime_values.get("SMTP_USE_TLS"):
            values["SMTP_SECURITY"] = (
                "starttls"
                if runtime_values["SMTP_USE_TLS"].lower() in {"1", "true", "yes", "on"}
                else "none"
            )
        values.setdefault("ENVIRONMENT", "production")
        values.setdefault("HOSTNAME", socket.gethostname())
        values.setdefault("IMAGE_REPOSITORY", "ghcr.io/matginesi/mifp-webapp")
        values.setdefault("REGISTRY", "ghcr.io")
        values.setdefault("BACKUP_ENABLED", "true")
        values.setdefault("BACKUP_LOCAL_RETENTION", "14")
        values.setdefault("SECRET_KEY", secrets.token_hex(32))
        values.setdefault("ADMIN_USERNAME", "admin")
        if domain:
            values["DOMAIN"] = domain.lower()
        if image_repository:
            values["IMAGE_REPOSITORY"] = image_repository
        derive_domains(values)
        if save:
            self.save(values)
        return values


def derive_domains(values: dict[str, str]) -> None:
    domain = values.get("DOMAIN", "").strip().lower()
    if domain:
        values["DOMAIN"] = domain
        values.setdefault("WWW_DOMAIN", f"www.{domain}")
        values.setdefault("EVENTS_DOMAIN", f"events.{domain}")
        if domain.endswith(".home.arpa"):
            values["ENVIRONMENT"] = "local"


def normalize_value(key: str, value: str) -> str:
    value = value.strip()
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(f"control characters are not allowed in {key}")
    if key in {"DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN"}:
        value = value.lower()
        if not DOMAIN_RE.fullmatch(value) or "." not in value:
            raise ValueError(f"invalid domain for {key}: {value}")
    elif key in {"PUBLIC_IPV4", "DNS_EXPECTED_IPV4"} and value:
        if ipaddress.ip_address(value).version != 4:
            raise ValueError(f"invalid IPv4 for {key}: {value}")
    elif key in {"PUBLIC_IPV6", "DNS_EXPECTED_IPV6"} and value:
        if ipaddress.ip_address(value).version != 6:
            raise ValueError(f"invalid IPv6 for {key}: {value}")
    elif key == "ENVIRONMENT" and value not in {"production", "local"}:
        raise ValueError("ENVIRONMENT must be production or local")
    elif key == "IMAGE_REPOSITORY" and not REPO_RE.fullmatch(value.rstrip("/")):
        raise ValueError("IMAGE_REPOSITORY must be ghcr.io/<owner>/<repository>")
    elif key == "SMTP_PORT" and value:
        if not value.isdigit() or not 1 <= int(value) <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
    elif key == "SMTP_SECURITY" and value not in {"tls", "starttls", "none"}:
        raise ValueError("SMTP_SECURITY must be tls, starttls or none")
    elif key in {"BACKUP_ENABLED"} and value.lower() not in {"true", "false"}:
        raise ValueError(f"{key} must be true or false")
    elif key == "BACKUP_LOCAL_RETENTION" and (not value.isdigit() or int(value) < 2):
        # deploy/backup.sh requires >= 2: keeping a single snapshot would make
        # the pre-restore safety snapshot delete the one being restored.
        raise ValueError("BACKUP_LOCAL_RETENTION must be an integer >= 2")
    elif key == "ADMIN_USERNAME" and not USERNAME_RE.fullmatch(value):
        raise ValueError("invalid ADMIN_USERNAME")
    elif key == "ADMIN_PASSWORD_HASH" and value and not ADMIN_HASH_RE.fullmatch(value):
        raise ValueError("ADMIN_PASSWORD_HASH has an unexpected format")
    elif key == "SECRET_KEY" and value and len(value) < 32:
        raise ValueError("SECRET_KEY must contain at least 32 characters")
    return value


def registry_authenticated(docker_config: Path) -> bool:
    try:
        payload = json.loads(docker_config.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    auths = payload.get("auths", {})
    return isinstance(auths, dict) and any(
        name.rstrip("/") in {"ghcr.io", "https://ghcr.io"} for name in auths
    )


def resolve_addresses(host: str) -> tuple[set[str], str | None]:
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return set(), str(exc)
    return {record[4][0] for record in records}, None


def check_configuration(
    values: dict[str, str], *, docker_config: Path,
    resolver: Callable[[str], tuple[set[str], str | None]] = resolve_addresses,
) -> tuple[list[str], list[str]]:
    errors = validate_configuration(values)
    notes: list[str] = []
    environment = values.get("ENVIRONMENT", "")
    domain = values.get("DOMAIN", "")
    is_local_domain = domain.endswith(".home.arpa")
    if environment == "local" and domain and not is_local_domain:
        errors.append("ENVIRONMENT=local requires a .home.arpa domain")
    if environment == "production" and is_local_domain:
        errors.append("A .home.arpa domain must use ENVIRONMENT=local")
    if environment == "local" or is_local_domain:
        notes.append("DNS: local mode; public DNS checks skipped")
    elif domain:
        expected_v4 = values.get("DNS_EXPECTED_IPV4") or values.get("PUBLIC_IPV4", "")
        expected_v6 = values.get("DNS_EXPECTED_IPV6") or values.get("PUBLIC_IPV6", "")
        for key in ("DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN"):
            host = values.get(key, "")
            if not host:
                continue
            current, failure = resolver(host)
            if failure or not current:
                errors.append(f"DNS {host}: unresolved")
            elif expected_v4 and expected_v4 not in current:
                errors.append(f"DNS {host}: expected IPv4 {expected_v4}; current {', '.join(sorted(current))}")
            elif expected_v6 and expected_v6 not in current:
                errors.append(f"DNS {host}: expected IPv6 {expected_v6}; current {', '.join(sorted(current))}")
            else:
                notes.append(f"DNS {host}: OK ({', '.join(sorted(current))})")
    if registry_authenticated(docker_config):
        notes.append("Registry authentication: configured")
    else:
        errors.append("Registry authentication: missing required (run sudo mifpctl registry-login)")
    smtp_fields = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_FROM_ADDRESS")
    if values.get("SMTP_HOST"):
        missing_smtp = [key for key in smtp_fields if not values.get(key)]
        if missing_smtp:
            errors.append(f"SMTP: incomplete ({', '.join(missing_smtp)} missing)")
        elif values.get("SMTP_SECURITY") == "tls" and values.get("SMTP_PORT") != "465":
            errors.append("SMTP: tls normally requires port 465")
        elif values.get("SMTP_SECURITY") == "starttls" and values.get("SMTP_PORT") == "465":
            errors.append("SMTP: starttls cannot use implicit-TLS port 465")
        else:
            notes.append("SMTP: configured")
    else:
        notes.append("SMTP: NOT CONFIGURED (optional)")
    notes.append("Remote backup: configured" if values.get("RESTIC_REPOSITORY") else "Remote backup: NOT CONFIGURED (optional)")
    return errors, notes


def validate_configuration(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED_KEYS:
        if not values.get(key):
            hint = " (run sudo mifpctl admin)" if key == "ADMIN_PASSWORD_HASH" else ""
            errors.append(f"{key}: missing required{hint}")
    for key, value in values.items():
        if key not in ALL_KEYS or not value:
            continue
        try:
            normalize_value(key, value)
        except (ValueError, ipaddress.AddressValueError) as exc:
            errors.append(str(exc))
    return errors


def display_value(values: dict[str, str], key: str, *, secret: bool = False) -> str:
    value = values.get(key, "")
    if secret:
        return "configured" if value else "not configured"
    return value or "<not configured>"


def print_show(values: dict[str, str], docker_config: Path) -> None:
    print("MIFP configuration\n")
    sections = (
        ("Environment", ("ENVIRONMENT", "HOSTNAME", "PUBLIC_IPV4", "PUBLIC_IPV6", "TIMEZONE")),
        ("Web", ("DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN", "IMAGE_REPOSITORY")),
        ("DNS", ("DNS_PROVIDER", "DNS_EXPECTED_IPV4", "DNS_EXPECTED_IPV6")),
        ("Registry", ("REGISTRY", "REGISTRY_USERNAME")),
        ("Mail", ("MAIL_PROVIDER", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_FROM_ADDRESS", "SMTP_FROM_NAME")),
        ("Backups", ("BACKUP_ENABLED", "BACKUP_LOCAL_RETENTION", "RESTIC_REPOSITORY")),
        ("Application", ("ADMIN_USERNAME",)),
    )
    for title, keys in sections:
        print(title)
        for key in keys:
            print(f"  {key:<24} {display_value(values, key)}")
    print(f"  {'REGISTRY_AUTH':<24} {'configured' if registry_authenticated(docker_config) else 'not configured'}")
    for key in ("SMTP_PASSWORD", "RESTIC_PASSWORD", "ADMIN_PASSWORD_HASH", "SECRET_KEY"):
        print(f"  {key:<24} {display_value(values, key, secret=True)}")


WIZARD_SECTIONS = {
    "web": ("ENVIRONMENT", "DOMAIN", "WWW_DOMAIN", "EVENTS_DOMAIN", "IMAGE_REPOSITORY", "PUBLIC_IPV4", "PUBLIC_IPV6"),
    "mail": ("MAIL_PROVIDER", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_FROM_ADDRESS", "SMTP_FROM_NAME", "SMTP_PASSWORD"),
    "registry": ("REGISTRY_USERNAME",),
    "backup": ("BACKUP_ENABLED", "BACKUP_LOCAL_RETENTION", "RESTIC_REPOSITORY", "RESTIC_PASSWORD"),
}


def run_wizard(store: Store, section: str | None, docker_config: Path) -> int:
    if not sys.stdin.isatty():
        raise SystemExit("configure requires an interactive terminal")
    # Build defaults in memory. Nothing is written until the final confirmation.
    values = store.migrate(save=False)
    keys = WIZARD_SECTIONS[section] if section else tuple(dict.fromkeys(sum(WIZARD_SECTIONS.values(), ())))
    print("MIFP VPS configuration\n")
    pending = dict(values)
    for key in keys:
        hidden = key in SECRET_INPUT_KEYS
        current = display_value(values, key, secret=hidden)
        prompt = f"{key}\n  Current: {current}\n  New value [leave empty to keep]: "
        entered = getpass.getpass(prompt) if hidden else input(prompt)
        if not entered:
            continue
        try:
            pending[key] = normalize_value(key, entered)
        except (ValueError, ipaddress.AddressValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if key == "DOMAIN":
            pending["WWW_DOMAIN"] = f"www.{pending[key]}"
            pending["EVENTS_DOMAIN"] = f"events.{pending[key]}"
            pending["ENVIRONMENT"] = "local" if pending[key].endswith(".home.arpa") else "production"
    answer = input("Save changes? [Y/n] ").strip().lower()
    if answer not in {"", "y", "yes"}:
        print("No changes saved.")
        return 0
    store.save(pending)
    print("Configuration saved.")
    print_show(pending, docker_config)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--config-file", type=Path, required=True)
    root.add_argument("--secrets-file", type=Path, required=True)
    root.add_argument("--runtime-env", type=Path, required=True)
    root.add_argument("--example", type=Path)
    root.add_argument("--docker-config", type=Path, default=Path("/root/.docker/config.json"))
    subs = root.add_subparsers(dest="command", required=True)
    init = subs.add_parser("init")
    init.add_argument("--domain", default="")
    init.add_argument("--image-repository", default="")
    wizard = subs.add_parser("configure")
    wizard.add_argument("--section", choices=sorted(WIZARD_SECTIONS))
    set_cmd = subs.add_parser("set")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    unset = subs.add_parser("unset")
    unset.add_argument("key")
    subs.add_parser("show")
    subs.add_parser("check")
    subs.add_parser("validate", help=argparse.SUPPRESS)
    subs.add_parser("import-admin", help=argparse.SUPPRESS)
    get_cmd = subs.add_parser("get")
    get_cmd.add_argument("key")
    return root


def main() -> int:
    args = parser().parse_args()
    store = Store(args.config_file, args.secrets_file, args.runtime_env, args.example)
    if args.command == "init":
        store.migrate(domain=args.domain, image_repository=args.image_repository)
        return 0
    if args.command == "configure":
        return run_wizard(store, args.section, args.docker_config)
    key = getattr(args, "key", "").upper()
    if args.command in {"set", "unset", "get"} and key not in ALL_KEYS:
        raise SystemExit(f"Unsupported configuration key: {key}")
    if args.command == "set" and key in SECRET_INPUT_KEYS:
        raise SystemExit("Refusing secret on command line; use: sudo mifpctl configure")
    # show/check/get are intentionally read-only: no migration, defaults or chmod.
    if args.command in {"show", "check", "validate", "get"}:
        values = store.values()
    elif args.command == "import-admin":
        values = store.values()
        legacy = read_env(store.runtime)
        if not legacy.get("ADMIN_PASSWORD_HASH"):
            raise SystemExit("Runtime administrator hash is missing")
        values["ADMIN_USERNAME"] = legacy.get("ADMIN_USERNAME", "admin")
        values["ADMIN_PASSWORD_HASH"] = legacy["ADMIN_PASSWORD_HASH"]
        store.save(values)
        return 0
    else:
        values = store.migrate()
    if args.command == "set":
        try:
            values[key] = normalize_value(key, args.value)
        except (ValueError, ipaddress.AddressValueError) as exc:
            raise SystemExit(str(exc)) from exc
        if key == "DOMAIN":
            values["WWW_DOMAIN"] = f"www.{values[key]}"
            values["EVENTS_DOMAIN"] = f"events.{values[key]}"
            values["ENVIRONMENT"] = "local" if values[key].endswith(".home.arpa") else "production"
        store.save(values)
        print(f"{key} updated.")
    elif args.command == "unset":
        values.pop(key, None)
        store.save(values)
        print(f"{key} removed.")
    elif args.command == "show":
        print_show(values, args.docker_config)
    elif args.command == "check":
        errors, notes = check_configuration(values, docker_config=args.docker_config)
        print("MIFP configuration check\n")
        for note in notes:
            print(f"  {note}")
        if errors:
            print("\nRequired action:")
            for error in errors:
                print(f"- {error}")
            print("\nResult: NOT READY")
            return 1
        print("\nResult: READY")
    elif args.command == "validate":
        errors = validate_configuration(values)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
    elif args.command == "get":
        if key in SECRET_KEYS:
            raise SystemExit("Secret values cannot be displayed")
        print(values.get(key, ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

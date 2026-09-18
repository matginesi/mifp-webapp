#!/usr/bin/env python3
"""Preflight a historical events document root before publishing it.

The public events tree is served directly by Caddy, so this checker deliberately
fails closed on filesystem objects and payloads that should never be copied into
that tree. PHP source is allowed but reported: Caddy keeps it non-executable and
returns 404 unless an operator explicitly enables a conference prefix.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIXES = {
    ".css", ".htm", ".html", ".inc", ".ini", ".js", ".json", ".md",
    ".php", ".phtml", ".svg", ".txt", ".xml", ".yaml", ".yml",
}
PHP_SUFFIXES = {".php", ".phtml", ".phar", ".php3", ".php4", ".php5", ".php7", ".php8"}
SENSITIVE_DIR_NAMES = {".git", ".svn"}
SENSITIVE_EXACT_NAMES = {
    ".env", ".htpasswd", ".netrc", ".npmrc", ".pgpass", ".s3cfg",
    "credentials.json", "credential.json", "creds.json", "secrets.json",
    "secret.json", "tokens.json", "token.json", "shadow",
}
SENSITIVE_KEY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SENSITIVE_SUFFIXES = {
    ".bak", ".cer", ".crt", ".db", ".key", ".old", ".orig", ".p12",
    ".pem", ".pfx", ".sql", ".sqlite", ".sqlite3",
}
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
MAX_WARNING_PATHS = 20
MAX_ERROR_PATHS = 40


@dataclass
class Report:
    files: int = 0
    directories: int = 0
    bytes_total: int = 0
    php_files: int = 0
    root_index: bool = False
    old_domain_refs: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)


def _is_sensitive(relative: Path) -> str | None:
    parts = [part.lower() for part in relative.parts]
    if any(part in SENSITIVE_DIR_NAMES for part in parts[:-1]):
        return "version-control metadata"
    name = relative.name.lower()
    if name in SENSITIVE_EXACT_NAMES or name.startswith(".env."):
        return "secret/config file"
    if name.startswith(SENSITIVE_KEY_PREFIXES):
        return "private/public SSH key material"
    # Database journals are just as sensitive as their parent database.
    if name.endswith((".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm")):
        return "database payload"
    if relative.suffix.lower() in SENSITIVE_SUFFIXES:
        return "database/backup/key payload"
    if len(parts) >= 2 and "regform" in parts and "registrations" in parts:
        return "legacy registration data"
    return None


def _contains_old_domain_reference(path: Path) -> bool:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size > MAX_TEXT_FILE_BYTES:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "old.mifp.eu" in text


def scan(root: Path) -> Report:
    report = Report()
    root_stat = root.stat()
    root_device = root_stat.st_dev

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in sorted(dirnames):
            path = current_path / name
            relative = path.relative_to(root)
            try:
                info = path.lstat()
            except OSError as exc:
                report.unsafe.append(f"{relative}: cannot stat directory ({exc})")
                continue
            if stat.S_ISLNK(info.st_mode):
                report.unsafe.append(f"{relative}: symlink")
                continue
            if not stat.S_ISDIR(info.st_mode):
                report.unsafe.append(f"{relative}: non-directory object in directory tree")
                continue
            if info.st_dev != root_device:
                report.unsafe.append(f"{relative}: crosses onto another filesystem")
                continue
            if name.lower() in SENSITIVE_DIR_NAMES:
                report.unsafe.append(f"{relative}: version-control metadata")
                continue
            report.directories += 1
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in sorted(filenames):
            path = current_path / name
            relative = path.relative_to(root)
            try:
                info = path.lstat()
            except OSError as exc:
                report.unsafe.append(f"{relative}: cannot stat file ({exc})")
                continue
            if stat.S_ISLNK(info.st_mode):
                report.unsafe.append(f"{relative}: symlink")
                continue
            if not stat.S_ISREG(info.st_mode):
                report.unsafe.append(f"{relative}: file speciale")
                continue
            if info.st_dev != root_device:
                report.unsafe.append(f"{relative}: crosses onto another filesystem")
                continue
            reason = _is_sensitive(relative)
            if reason:
                report.unsafe.append(f"{relative}: {reason}")
                continue

            report.files += 1
            report.bytes_total += info.st_size
            suffix = path.suffix.lower()
            if suffix in PHP_SUFFIXES:
                report.php_files += 1
            if len(relative.parts) == 1 and name.lower() in {"index.html", "index.htm", "index.php"}:
                report.root_index = True
            if len(report.old_domain_refs) < MAX_WARNING_PATHS and _contains_old_domain_reference(path):
                report.old_domain_refs.append(relative.as_posix())

    return report


def human_size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return f"{value} B"


def print_report(root: Path, report: Report, *, quiet: bool) -> None:
    if report.unsafe:
        print("ERROR: events archive preflight failed; unsafe/private payload found:", file=sys.stderr)
        for item in report.unsafe[:MAX_ERROR_PATHS]:
            print(f"  - {item}", file=sys.stderr)
        if len(report.unsafe) > MAX_ERROR_PATHS:
            print(f"  - ... and {len(report.unsafe) - MAX_ERROR_PATHS} more", file=sys.stderr)
        return
    if quiet:
        return

    print("Events archive preflight: OK")
    print(f"  root:        {root}")
    print(f"  files:       {report.files}")
    print(f"  directories: {report.directories}")
    print(f"  size:        {human_size(report.bytes_total)}")
    print(f"  PHP files:   {report.php_files} (deny-by-default after import)")
    if not report.root_index:
        print("  WARN: no index.html/index.htm/index.php at archive root; the domain root may return 404.")
    if report.old_domain_refs:
        print("  WARN: references to old.mifp.eu remain in:")
        for item in report.old_domain_refs:
            print(f"    - {item}")
        print("  Review these links before old.mifp.eu is retired.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Validate a historical events document root before publishing it")
    result.add_argument("root", type=Path, help="document root to inspect")
    result.add_argument("--quiet", action="store_true", help="print only errors")
    return result


def main() -> int:
    args = parser().parse_args()
    candidate = args.root.expanduser()
    if candidate.is_symlink():
        print(f"ERROR: invalid events document root (symlink): {candidate}", file=sys.stderr)
        return 2
    root = candidate.resolve()
    if not root.is_dir():
        print(f"ERROR: invalid events document root: {root}", file=sys.stderr)
        return 2
    if root == Path("/"):
        print("ERROR: refusing to inspect / as an events document root", file=sys.stderr)
        return 2
    report = scan(root)
    print_report(root, report, quiet=args.quiet)
    return 2 if report.unsafe else 0


if __name__ == "__main__":
    raise SystemExit(main())

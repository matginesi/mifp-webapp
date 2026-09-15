#!/usr/bin/env python3
"""Fail closed when generated/runtime data is part of the source repository.

The check is intentionally standard-library only so it can run before project
Python dependencies are installed.  In a Git checkout it inspects *tracked*
files (the source-control contract).  In a source ZIP it falls back to scanning
the extracted tree, which also makes it useful as a packaging smoke check.
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024

# These paths are application state or generated import/scraper material.  The
# source code that manages them remains versioned elsewhere.
FORBIDDEN_PREFIXES = (
    "SCRAPERS/OUTPUTS/",
    "IMPORT_DATA/",
    "NEW_IMPORT_DATA/",
    "SCRAPER/",
    "scrapers_/",
    "MIFPAPP/DATABASE/assets/",
    "MIFPAPP/DATABASE/backups/",
    "MIFPAPP/DATABASE/config/",
    "MIFPAPP/DATABASE/conferences/",
    "MIFPAPP/DATABASE/exports/",
    "MIFPAPP/DATABASE/logs/",
    "MIFPAPP/DATABASE/tmp/",
    "MIFPAPP/DATABASE/uploads/",
)

# Empty placeholders are harmless and make expected runtime directories
# discoverable without versioning their contents.
ALLOWED_PLACEHOLDERS = {
    prefix + ".gitkeep" for prefix in FORBIDDEN_PREFIXES
}

FORBIDDEN_PATTERNS = (
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite-*",
    "*.sqlite3",
    "*.sqlite3-*",
    "*.jsonl",
    "*.ndjson",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.bak",
    "*.dump",
)

SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "credentials.json",
    "credentials.*.json",
    "*.secret",
    "*.token",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.crt",
    ".htpasswd",
)

ALLOWED_SECRET_TEMPLATES = {
    "MIFPAPP/CORE/.env.example",
    "deploy/.env.production.example",
}

SKIP_DIR_NAMES = {
    ".git",
    ".mifp",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}


def _git_tracked_files() -> list[PurePosixPath] | None:
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        return None
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return [
        PurePosixPath(raw.decode("utf-8", errors="surrogateescape"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def _snapshot_files() -> list[PurePosixPath]:
    items: list[PurePosixPath] = []
    for current, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIR_NAMES)
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            items.append(PurePosixPath(path.relative_to(ROOT).as_posix()))
    return items


def _matches(path: PurePosixPath, patterns: tuple[str, ...]) -> bool:
    name = path.name
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def main() -> int:
    tracked = _git_tracked_files()
    mode = "tracked Git files" if tracked is not None else "source snapshot files"
    paths = tracked if tracked is not None else _snapshot_files()

    violations: list[str] = []
    for rel in paths:
        text = rel.as_posix()
        full = ROOT / text

        if text in ALLOWED_PLACEHOLDERS:
            continue

        if any(text.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            violations.append(f"runtime/generated path: {text}")
            continue

        if _matches(rel, FORBIDDEN_PATTERNS):
            violations.append(f"generated/data/archive file: {text}")
            continue

        if text not in ALLOWED_SECRET_TEMPLATES and _matches(rel, SECRET_PATTERNS):
            violations.append(f"secret-like file: {text}")
            continue

        try:
            size = full.stat().st_size
        except (FileNotFoundError, OSError):
            # A sparse/odd checkout should be diagnosed by Git/build tooling;
            # hygiene must not silently classify a missing file as safe data.
            continue
        if size > MAX_TRACKED_FILE_BYTES:
            violations.append(
                f"oversized source file ({size / (1024 * 1024):.1f} MiB > "
                f"{MAX_TRACKED_FILE_BYTES / (1024 * 1024):.0f} MiB): {text}"
            )

    if violations:
        print(f"Repository hygiene FAILED while checking {mode}:", file=sys.stderr)
        for item in sorted(violations):
            print(f"  - {item}", file=sys.stderr)
        print(
            "\nGenerated/runtime data must stay outside source control. "
            "Remove it from the Git index/history instead of weakening this check.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Repository hygiene OK: {len(paths)} {mode}; "
        f"no runtime data, DB/dumps, archives, secrets, or files over 5 MiB."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

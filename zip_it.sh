#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$ROOT_DIR"
PREFIX="mifp-codebase"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Crea uno ZIP del codebase completo, indipendentemente da .gitignore.

Include:
  MIFPAPP, SCRAPERS, TESTS, tools, docs e file di progetto.

Esclude:
  IMPORT_DATA, database, asset/output generati, backup, log, export,
  virtualenv, cache, secret, .env, repository Git e archivi precedenti.

Uso:
  ./zip_it.sh
  ./zip_it.sh --output ~/Backups
  ./zip_it.sh --prefix mifp-snapshot
  ./zip_it.sh --dry-run
USAGE
}

while (($#)); do
  case "$1" in
    --output)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --output richiede una directory" >&2; exit 2; }
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --output=*) OUTPUT_DIR="${1#*=}"; shift ;;
    --prefix)
      [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --prefix richiede un valore" >&2; exit 2; }
      PREFIX="$2"
      shift 2
      ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: opzione sconosciuta: $1" >&2; exit 2 ;;
  esac
done

[[ "$PREFIX" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "ERROR: prefix non valido: $PREFIX" >&2
  exit 2
}

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

last="$({ find "$OUTPUT_DIR" -maxdepth 1 -type f -name "${PREFIX}-*.zip" -printf '%f\n' 2>/dev/null || true; } \
  | sed -n "s/^${PREFIX}-\([0-9][0-9]*\)\.zip$/\1/p" \
  | sort -n | tail -1)"
next=$((10#${last:-0} + 1))
OUTPUT="$(printf '%s/%s-%03d.zip' "$OUTPUT_DIR" "$PREFIX" "$next")"

if ((DRY_RUN)); then
  printf '%s\n' "$OUTPUT"
  exit 0
fi

python3 - "$ROOT_DIR" "$OUTPUT" <<'PY'
from __future__ import annotations

import fnmatch
import os
import sys
import zipfile
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()

EXCLUDED_DIR_NAMES = {
    ".git", ".mifp", ".agents", ".codex", ".superpowers",
    ".venv", ".venv-production", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".hypothesis", ".idea", ".vscode", "htmlcov",
    "playwright-report", "test-results", "tmp", "temp",
}

EXCLUDED_TOP_LEVEL = {"IMPORT_DATA"}

EXCLUDED_PATH_PREFIXES = {
    "MIFPAPP/DATABASE/assets",
    "MIFPAPP/DATABASE/backups",
    "MIFPAPP/DATABASE/conferences",
    "MIFPAPP/DATABASE/exports",
    "MIFPAPP/DATABASE/logs",
    "SCRAPERS/OUTPUTS",
    "MIFPAPP/CORE/secrets",
}

EXCLUDED_FILE_PATTERNS = {
    "*.pyc", "*.pyo", "*.log", "*.pid", "*.zip", "*.tar",
    "*.tar.gz", "*.tgz", "*.db", "*.db-*", "*.sqlite",
    "*.sqlite3", "*.sqlite-*", "*.coverage", "coverage.xml",
    ".DS_Store", ".env", ".env.*.local",
}

# Keep directory placeholders so a restored codebase has the expected shape.
KEEP_GENERATED_PLACEHOLDERS = {
    "MIFPAPP/DATABASE/assets/.gitkeep",
    "MIFPAPP/DATABASE/backups/.gitkeep",
    "MIFPAPP/DATABASE/conferences/.gitkeep",
    "MIFPAPP/DATABASE/exports/.gitkeep",
    "MIFPAPP/DATABASE/logs/.gitkeep",
    "SCRAPERS/OUTPUTS/.gitkeep",
}


def is_excluded(rel: PurePosixPath, *, is_dir: bool) -> bool:
    text = rel.as_posix()
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in EXCLUDED_TOP_LEVEL:
        return True
    if any(part in EXCLUDED_DIR_NAMES for part in parts):
        return True
    if text in KEEP_GENERATED_PLACEHOLDERS:
        return False
    for prefix in EXCLUDED_PATH_PREFIXES:
        if text == prefix or text.startswith(prefix + "/"):
            return True
    if not is_dir:
        if text == "MIFPAPP/CORE/.env" or text.startswith("MIFPAPP/CORE/.env."):
            # Keep only public templates, never local/runtime env files.
            if text in {"MIFPAPP/CORE/.env.example", "MIFPAPP/CORE/.env.production.example"}:
                return False
            return True
        if any(fnmatch.fnmatch(rel.name, pattern) for pattern in EXCLUDED_FILE_PATTERNS):
            return True
    return False

files: list[tuple[Path, str]] = []
for current, dirnames, filenames in os.walk(root):
    current_path = Path(current)
    rel_current = PurePosixPath(current_path.relative_to(root).as_posix())

    kept_dirs = []
    for dirname in sorted(dirnames):
        rel = rel_current / dirname if rel_current.as_posix() != "." else PurePosixPath(dirname)
        if not is_excluded(rel, is_dir=True):
            kept_dirs.append(dirname)
    dirnames[:] = kept_dirs

    for filename in sorted(filenames):
        path = current_path / filename
        if path.resolve() == output:
            continue
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if is_excluded(rel, is_dir=False):
            continue
        files.append((path, rel.as_posix()))

if not files:
    raise SystemExit("Nessun file da archiviare")

output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path, arcname in files:
        archive.write(path, arcname)

print(f"File inclusi: {len(files)}")
PY

printf 'Creato: %s (%s)\n' "$OUTPUT" "$(du -h "$OUTPUT" | cut -f1)"
printf 'IMPORT_DATA, database, output, asset generati, log, cache e secret esclusi.\n'

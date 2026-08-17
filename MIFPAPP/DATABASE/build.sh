#!/usr/bin/env bash
set -Eeuo pipefail

DATABASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$DATABASE_DIR/../.." && pwd)"
CORE_DIR="$PROJECT_ROOT/MIFPAPP/CORE"
TOOLS_DIR="$DATABASE_DIR/tools"
INPUT_DIR="$PROJECT_ROOT/SCRAPERS/OUTPUTS"
DB_PATH="$DATABASE_DIR/mifp.db"
ASSETS_DIR="$DATABASE_DIR/assets"
FRESH=0
SKIP_DOWNLOADS=0

usage() {
  cat <<'USAGE'
Usage: bash MIFPAPP/DATABASE/build.sh [options]

Imports scraper JSONL into the separate MIFP application database. This is an
explicit database step and is never run by SCRAPERS/run_all.sh.

Options:
  --input DIR             JSONL directory (default: SCRAPERS/OUTPUTS)
  --db PATH               SQLite path (default: MIFPAPP/DATABASE/mifp.db)
  --assets-dir PATH       Asset directory (default: MIFPAPP/DATABASE/assets)
  --fresh                 Recreate the SQLite database
  --skip-downloads        Do not download remote assets
  -h, --help
USAGE
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() { printf '  + '; printf '%q ' "$@"; printf '\n'; "$@"; }

while (($#)); do
  case "$1" in
    --input) INPUT_DIR="${2:-}"; shift 2 ;;
    --input=*) INPUT_DIR="${1#*=}"; shift ;;
    --db) DB_PATH="${2:-}"; shift 2 ;;
    --db=*) DB_PATH="${1#*=}"; shift ;;
    --assets-dir) ASSETS_DIR="${2:-}"; shift 2 ;;
    --assets-dir=*) ASSETS_DIR="${1#*=}"; shift ;;
    --fresh) FRESH=1; shift ;;
    --skip-downloads|--no-download-assets) SKIP_DOWNLOADS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

INPUT_DIR="$(realpath -m "$INPUT_DIR")"
DB_PATH="$(realpath -m "$DB_PATH")"
ASSETS_DIR="$(realpath -m "$ASSETS_DIR")"
[[ -f "$INPUT_DIR/records.jsonl" ]] || die "missing $INPUT_DIR/records.jsonl; run SCRAPERS/run_all.sh first"
mkdir -p "$(dirname "$DB_PATH")" "$ASSETS_DIR" "$DATABASE_DIR/backups" "$DATABASE_DIR/exports" "$DATABASE_DIR/logs"


python_is_ready() {
  "$1" -c 'import requests, tqdm, pypdf' >/dev/null 2>&1
}

PY=""
for candidate in \
  "${VIRTUAL_ENV:-}/bin/python" \
  "$CORE_DIR/.venv/bin/python" \
  "$DATABASE_DIR/.venv/bin/python" \
  "$(command -v python3 2>/dev/null || true)"; do
  if [[ -n "$candidate" && -x "$candidate" ]] && python_is_ready "$candidate"; then
    PY="$candidate"
    break
  fi
done

if [[ -z "$PY" ]]; then
  BASE_PY="$(command -v python3 || true)"
  [[ -n "$BASE_PY" ]] || die "python3 is required"
  ENV_DIR="$DATABASE_DIR/.venv"
  [[ -x "$ENV_DIR/bin/python" ]] || run "$BASE_PY" -m venv "$ENV_DIR"
  run "$ENV_DIR/bin/python" -m pip install --disable-pip-version-check --prefer-binary \
    -r "$DATABASE_DIR/requirements.txt"
  PY="$ENV_DIR/bin/python"
fi

BUILD=(
  "$PY" "$TOOLS_DIR/build_database.py"
  --webapp-dir "$CORE_DIR"
  --jsonl-dir "$INPUT_DIR"
  --db "$DB_PATH"
  --assets-dir "$ASSETS_DIR"
)
((FRESH)) && BUILD+=(--fresh)
((SKIP_DOWNLOADS)) && BUILD+=(--skip-downloads)
run "${BUILD[@]}"
run "$PY" "$TOOLS_DIR/pipeline_validate.py" --db "$DB_PATH" --assets-dir "$ASSETS_DIR"
printf '\nDatabase build completed.\n  database: %s\n  assets:   %s\n  source:   %s\n' "$DB_PATH" "$ASSETS_DIR" "$INPUT_DIR"

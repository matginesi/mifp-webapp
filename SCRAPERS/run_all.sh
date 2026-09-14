#!/usr/bin/env bash
set -Eeuo pipefail

SCRAPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRAPERS_DIR/.." && pwd)"
OUTPUT_DIR="$SCRAPERS_DIR/OUTPUTS"
MODE="all"
LOCAL_ROOT="${MIFP_SCRAPER_LOCAL_ROOT:-/run/media/matteo/ARCHDISK/srv/http/mifp.eu}"
FRESH=1
THREADS="${MIFP_SCRAPER_THREADS:-16}"
UPDATE_MIRROR=0
JSONL_DIRS=()

usage() {
  cat <<'USAGE'
Usage: bash SCRAPERS/run_all.sh [options]

Produces only canonical JSONL files and one import ZIP. It never creates,
updates, or deletes the MIFP application database.

Options:
  --scrapers local|remote|all|none
  --local-root PATH
  --jsonl-dir DIR          Existing canonical JSONL input; repeatable, implies none
  --outputs PATH           Output directory (default: SCRAPERS/OUTPUTS)
  --threads N              Remote scraper workers (1-128)
  --update-mirror          Refresh the local mirror before local scraping
  --fresh | --no-fresh     Clear previous *.jsonl and *.zip outputs first
  -h, --help

Final output directory contains only:
  *.jsonl
  MIFP_IMPORT.zip (or source-specific equivalent)
USAGE
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
run() { printf '  + '; printf '%q ' "$@"; printf '\n'; "$@"; }

while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --scrapers|--scraper) MODE="${2:-}"; shift 2 ;;
    --scrapers=*|--scraper=*) MODE="${1#*=}"; shift ;;
    --local-root) LOCAL_ROOT="${2:-}"; shift 2 ;;
    --local-root=*) LOCAL_ROOT="${1#*=}"; shift ;;
    --jsonl-dir) JSONL_DIRS+=("${2:-}"); MODE=none; shift 2 ;;
    --jsonl-dir=*) JSONL_DIRS+=("${1#*=}"); MODE=none; shift ;;
    --outputs) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --outputs=*) OUTPUT_DIR="${1#*=}"; shift ;;
    --threads) THREADS="${2:-}"; shift 2 ;;
    --threads=*) THREADS="${1#*=}"; shift ;;
    --update-mirror) UPDATE_MIRROR=1; shift ;;
    --fresh) FRESH=1; shift ;;
    --no-fresh) FRESH=0; shift ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$MODE" in local|remote|all|none) ;; *) die "--scrapers must be local, remote, all or none" ;; esac
[[ "$THREADS" =~ ^[0-9]+$ ]] && ((THREADS >= 1 && THREADS <= 128)) || die "--threads must be between 1 and 128"
[[ -n "$OUTPUT_DIR" ]] || die "--outputs cannot be empty"

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
[[ "$OUTPUT_DIR" != "/" && "$OUTPUT_DIR" != "$HOME" && "$OUTPUT_DIR" != "$PROJECT_ROOT" ]] || die "unsafe output directory: $OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

python_is_ready() {
  "$1" -c 'import bs4, requests, tqdm' >/dev/null 2>&1
}

PY="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]] || ! python_is_ready "$PY"; then
  [[ -x "$PROJECT_ROOT/mifp" ]] || die "local environment missing; run ./mifp setup from the project root"
  step "Prepare the shared project Python environment"
  run "$PROJECT_ROOT/mifp" setup
fi
[[ -x "$PY" ]] && python_is_ready "$PY" || die "shared environment is incomplete; run ./mifp setup"
printf 'Using Python environment: %s\n' "$PY"

if ((FRESH)); then
  step "Clean previous final artifacts"
  find "$OUTPUT_DIR" -maxdepth 1 -type f \( -name '*.jsonl' -o -name '*.zip' \) -delete
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mifp-scrapers.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
RAW_DIR="$WORK_DIR/raw"
IMPORT_DIR="$WORK_DIR/import"
mkdir -p "$RAW_DIR" "$IMPORT_DIR"

artifact() {
  local source="$1" output="$2" zip_name="$3"
  shift 3
  run "$PY" "$SCRAPERS_DIR/import_artifacts.py" \
    "$@" \
    --output "$output" \
    --source "$source" \
    --zip-name "$zip_name"
}

if ((UPDATE_MIRROR)); then
  [[ "$MODE" == "local" || "$MODE" == "all" ]] || die "--update-mirror requires local or all mode"
  run bash "$SCRAPERS_DIR/update_mirror.sh" "$LOCAL_ROOT" https://old.mifp.eu/
fi

case "$MODE" in
  local|all)
    step "Scrape local archive"
    [[ -d "$LOCAL_ROOT" ]] || die "local archive unavailable: $LOCAL_ROOT"
    run "$PY" "$SCRAPERS_DIR/scrape_local.py" \
      --root "$LOCAL_ROOT" \
      --output "$RAW_DIR/local" \
      --base-url https://old.mifp.eu/
    artifact local "$IMPORT_DIR/local" MIFP_LOCAL_IMPORT.zip --input-dir "$RAW_DIR/local"
    ;;
esac

case "$MODE" in
  remote|all)
    step "Scrape remote sites"
    run "$PY" "$SCRAPERS_DIR/scrape_remote.py" \
      --config "$SCRAPERS_DIR/config.remote.json" \
      --output "$RAW_DIR/remote" \
      --workers "$THREADS"
    artifact remote "$IMPORT_DIR/remote" MIFP_REMOTE_IMPORT.zip \
      --input-dir "$RAW_DIR/remote/events" \
      --input-dir "$RAW_DIR/remote/aruba"
    ;;
esac

case "$MODE" in
  all)
    step "Merge local and remote records"
    artifact combined "$OUTPUT_DIR" MIFP_IMPORT.zip \
      --records "$IMPORT_DIR/local/records.jsonl" \
      --records "$IMPORT_DIR/remote/records.jsonl" \
      --input-dir "$IMPORT_DIR/local" \
      --input-dir "$IMPORT_DIR/remote" \
      --input-dir "$RAW_DIR/local" \
      --input-dir "$RAW_DIR/remote/events" \
      --input-dir "$RAW_DIR/remote/aruba"
    ;;
  local)
    cp "$IMPORT_DIR/local"/*.jsonl "$OUTPUT_DIR/"
    cp "$IMPORT_DIR/local"/*.zip "$OUTPUT_DIR/MIFP_LOCAL_IMPORT.zip"
    ;;
  remote)
    cp "$IMPORT_DIR/remote"/*.jsonl "$OUTPUT_DIR/"
    cp "$IMPORT_DIR/remote"/*.zip "$OUTPUT_DIR/MIFP_REMOTE_IMPORT.zip"
    ;;
  none)
    ((${#JSONL_DIRS[@]})) || die "--scrapers none requires at least one --jsonl-dir"
    RECORD_ARGS=()
    for directory in "${JSONL_DIRS[@]}"; do
      [[ -d "$directory" ]] || die "JSONL directory unavailable: $directory"
      # Assets referenced by these JSONL files may live in a sibling/intermediate ZIP.
      # Let import_artifacts inspect the source directory instead of silently emitting
      # a final package with dangling local asset references.
      RECORD_ARGS+=(--input-dir "$directory")
      if [[ -f "$directory/records.jsonl" ]]; then
        RECORD_ARGS+=(--records "$directory/records.jsonl")
      else
        while IFS= read -r file; do RECORD_ARGS+=(--records "$file"); done \
          < <(find "$directory" -maxdepth 1 -type f -name '*.jsonl' | sort)
      fi
    done
    ((${#RECORD_ARGS[@]})) || die "no JSONL records found"
    artifact combined "$OUTPUT_DIR" MIFP_IMPORT.zip "${RECORD_ARGS[@]}"
    ;;
esac

step "Validate final JSONL and ZIP"
run "$PY" "$SCRAPERS_DIR/validate_import_data.py" "$OUTPUT_DIR"
VALIDATE_ARGS=("$SCRAPERS_DIR/validate_artifacts.py" "$OUTPUT_DIR")
if [[ "$MODE" == "local" || "$MODE" == "all" ]]; then
  # These datasets and downloaded assets are known to exist in the historical
  # MIFP mirror. Never report a successful scraper run if a regression silently
  # produces empty research/publication files or a records-only ZIP.
  VALIDATE_ARGS+=(--require-type publication --require-type research_area --require-assets)
fi
run "$PY" "${VALIDATE_ARGS[@]}"

printf '\nScraper pipeline completed.\n  JSONL + ZIP: %s\n  Database touched: no\n' "$OUTPUT_DIR"

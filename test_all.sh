#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Never let a developer-local MIFPAPP/CORE/.env alter test expectations.
# Override explicitly only for a deliberate integration test of dotenv loading.
export MIFP_LOAD_DOTENV="${MIFP_TEST_LOAD_DOTENV:-0}"

# Importing Config freezes runtime paths for the lifetime of the pytest
# process.  Give every test invocation an isolated storage root before test
# modules are collected, otherwise a route that reads Config directly can
# mutate the developer database even when an individual fixture later changes
# os.environ.
TEST_RUNTIME_DIR="$(mktemp -d "$ROOT_DIR/.mifp-test-runtime.XXXXXX")"
cleanup_test_runtime() {
  rm -rf -- "$TEST_RUNTIME_DIR"
}
trap cleanup_test_runtime EXIT
mkdir -p \
  "$TEST_RUNTIME_DIR/assets" \
  "$TEST_RUNTIME_DIR/exports" \
  "$TEST_RUNTIME_DIR/conferences" \
  "$TEST_RUNTIME_DIR/config" \
  "$TEST_RUNTIME_DIR/logs" \
  "$TEST_RUNTIME_DIR/tmp"
export TESTING=1
export SECRET_KEY="mifp-test-suite-secret"
export DATABASE_PATH="$TEST_RUNTIME_DIR/mifp.db"
export ASSETS_DIR="$TEST_RUNTIME_DIR/assets"
export EXPORT_DIR="$TEST_RUNTIME_DIR/exports"
export CONFERENCES_DIR="$TEST_RUNTIME_DIR/conferences"
export RUNTIME_CONFIG_DIR="$TEST_RUNTIME_DIR/config"
export LOG_DIR="$TEST_RUNTIME_DIR/logs"
export TMPDIR="$TEST_RUNTIME_DIR/tmp"
export AUTO_SYNC_CONFERENCES_ON_STARTUP=0

# Data-only suites must not need TESTS/conftest.py (which imports Flask), but
# they still need the repository source directories on sys.path.
export PYTHONPATH="$ROOT_DIR/MIFPAPP/CORE:$ROOT_DIR/MIFPAPP/DATABASE/tools${PYTHONPATH:+:$PYTHONPATH}"

SUITE=quick
BASE_URL="${MIFP_TEST_BASE_URL:-http://127.0.0.1:8000}"
BASE_URL_EXPLICIT=0
[[ -n "${MIFP_TEST_BASE_URL:-}" ]] && BASE_URL_EXPLICIT=1
ADMIN_USER="${MIFP_BROWSER_ADMIN_USER:-}"
ADMIN_PASSWORD="${MIFP_BROWSER_ADMIN_PASSWORD:-}"
AUTO_INSTALL_DEPS="${MIFP_TEST_AUTO_INSTALL:-1}"
PYTEST_ARGS=()

usage() {
  cat <<'EOF_USAGE'
Usage: bash test_all.sh [options] [-- pytest-args]

Suites:
  --suite quick       Webapp + database + repository-tool tests (default)
  --suite all         Quick suite + real-browser tests
  --suite webapp      Flask/core tests only
  --suite database    Database/import tests only
  --suite tools       Repository maintenance/tooling tests only
  --suite browser     Playwright browser tests only
  --suite smoke       HTTP health/readiness/public-page checks against --base-url

Aliases:
  --no-browser        Same as --suite quick
  --browser-only      Same as --suite browser

External server/browser options:
  --base-url URL
  --admin-user USER
  --admin-password PASS

Environment:
  MIFP_TEST_AUTO_INSTALL=0|1
  MIFP_TEST_LOAD_DOTENV=0|1   (default: 0, isolates tests from CORE/.env)
  MIFP_TEST_BASE_URL=http://127.0.0.1:8000
  MIFP_BROWSER_ADMIN_USER=admin
  MIFP_BROWSER_ADMIN_PASSWORD=<plain test password>

Examples:
  bash test_all.sh
  bash test_all.sh --suite all
  bash test_all.sh --suite webapp -- -x -vv
  bash test_all.sh --suite browser
  bash test_all.sh --suite browser --base-url http://127.0.0.1:8000 \
    --admin-user admin --admin-password '<password>'
  bash test_all.sh --suite smoke --base-url http://127.0.0.1:8000
EOF_USAGE
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

normalise_suite() {
  case "$1" in
    quick) printf 'quick\n' ;;
    all) printf 'all\n' ;;
    webapp|core) printf 'webapp\n' ;;
    database|db) printf 'database\n' ;;
    tools|tooling) printf 'tools\n' ;;
    browser) printf 'browser\n' ;;
    smoke) printf 'smoke\n' ;;
    *) die "unknown test suite: $1" ;;
  esac
}

while (($#)); do
  case "$1" in
    --suite)
      [[ $# -ge 2 && -n "$2" ]] || die "--suite requires a value"
      SUITE="$(normalise_suite "$2")"
      shift 2
      ;;
    --suite=*) SUITE="$(normalise_suite "${1#*=}")"; shift ;;
    --no-browser) SUITE=quick; shift ;;
    --browser-only) SUITE=browser; shift ;;
    --base-url)
      [[ $# -ge 2 && -n "$2" ]] || die "--base-url requires a value"
      BASE_URL="$2"
      BASE_URL_EXPLICIT=1
      shift 2
      ;;
    --base-url=*) BASE_URL="${1#*=}"; BASE_URL_EXPLICIT=1; shift ;;
    --admin-user)
      [[ $# -ge 2 && -n "$2" ]] || die "--admin-user requires a value"
      ADMIN_USER="$2"
      shift 2
      ;;
    --admin-user=*) ADMIN_USER="${1#*=}"; shift ;;
    --admin-password)
      [[ $# -ge 2 && -n "$2" ]] || die "--admin-password requires a value"
      ADMIN_PASSWORD="$2"
      shift 2
      ;;
    --admin-password=*) ADMIN_PASSWORD="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; PYTEST_ARGS+=("$@"); break ;;
    *) PYTEST_ARGS+=("$1"); shift ;;
  esac
done

pick_python() {
  local root_venv="$ROOT_DIR/.venv/bin/python"
  if [[ -x "$root_venv" ]]; then
    printf '%s\n' "$root_venv"
    return 0
  fi
  command -v python3 2>/dev/null || return 1
}

PYTHON_BIN="$(pick_python)" || die "Python 3 is required"

ensure_test_environment() {
  local profile="${1:-full}"
  local check_code
  case "$profile" in
    data) check_code='import pypdf, pytest, requests, tqdm' ;;
    webapp) check_code='import flask, pytest' ;;
    full) check_code='import flask, pypdf, pytest, requests, tqdm' ;;
    *) die "unknown dependency profile: $profile" ;;
  esac

  # Prefer an already-capable interpreter before touching the network. This
  # keeps data-only suites usable in offline/recovery environments and avoids
  # rebuilding the project virtualenv when the runner already has everything.
  if "$PYTHON_BIN" -c "$check_code" >/dev/null 2>&1; then
    return 0
  fi

  local system_python
  system_python="$(command -v python3 2>/dev/null || true)"
  if [[ -n "$system_python" && "$system_python" != "$PYTHON_BIN" ]] \
    && "$system_python" -c "$check_code" >/dev/null 2>&1; then
    PYTHON_BIN="$system_python"
    return 0
  fi

  if [[ "$AUTO_INSTALL_DEPS" != "1" ]]; then
    printf 'Missing test dependencies for profile: %s\n' "$profile" >&2
    return 1
  fi

  # Only install when no available interpreter satisfies the selected suite.
  "$ROOT_DIR/mifp" setup
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  "$PYTHON_BIN" -c "$check_code" >/dev/null 2>&1 || {
    printf 'The local environment is still missing dependencies for profile: %s\n' "$profile" >&2
    return 1
  }
}

ensure_browser_environment() {
  ensure_test_environment webapp
  if ! "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
    if [[ "$AUTO_INSTALL_DEPS" != "1" ]]; then
      printf 'Playwright is missing.\n' >&2
      return 1
    fi
    "$PYTHON_BIN" -m pip install playwright
  fi

  if ! command -v google-chrome-stable >/dev/null 2>&1 \
    && ! command -v google-chrome >/dev/null 2>&1 \
    && ! command -v chromium >/dev/null 2>&1 \
    && ! command -v chromium-browser >/dev/null 2>&1; then
    printf 'Chrome or Chromium is required for browser tests.\n' >&2
    return 1
  fi
}

run_pytest_paths() {
  local label="$1"
  local profile="$2"
  shift 2
  printf '\n== %s ==\n' "$label"
  ensure_test_environment "$profile"
  "$PYTHON_BIN" -m pytest "$@" "${PYTEST_ARGS[@]}"
}

run_quick() {
  run_webapp
  run_database
  run_tools
}

run_webapp() {
  run_pytest_paths "webapp tests" webapp TESTS/webapp
}


run_database() {
  run_pytest_paths "database tests" data --confcutdir=TESTS/database TESTS/database
}

run_tools() {
  run_pytest_paths "repository tooling tests" data --confcutdir=TESTS/tools TESTS/tools
}

run_browser() {
  printf '\n== browser tests ==\n'
  ensure_browser_environment

  if ((BASE_URL_EXPLICIT)); then
    [[ -n "$ADMIN_USER" ]] || die "external browser tests require --admin-user"
    [[ -n "$ADMIN_PASSWORD" ]] || die "external browser tests require --admin-password"
    MIFP_TEST_BASE_URL="$BASE_URL" \
    MIFP_BROWSER_ADMIN_USER="$ADMIN_USER" \
    MIFP_BROWSER_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
      "$PYTHON_BIN" -m pytest TESTS/browser "${PYTEST_ARGS[@]}"
  else
    "$PYTHON_BIN" -m pytest TESTS/browser "${PYTEST_ARGS[@]}"
  fi
}

check_url() {
  local path="$1"
  local url="${BASE_URL%/}${path}"
  printf '  %-10s %s ... ' "$path" "$url"
  curl --fail --silent --show-error --location --max-time 15 "$url" >/dev/null
  printf 'OK\n'
}

run_smoke() {
  printf '\n== HTTP smoke tests ==\n'
  command -v curl >/dev/null 2>&1 || die "curl is required for smoke tests"
  check_url /health
  check_url /ready
  check_url /
  check_url /login
}

case "$SUITE" in
  quick) run_quick ;;
  all) run_quick; run_browser ;;
  webapp) run_webapp ;;
  database) run_database ;;
  tools) run_tools ;;
  browser) run_browser ;;
  smoke) run_smoke ;;
esac

printf '\nAll requested tests passed: %s\n' "$SUITE"

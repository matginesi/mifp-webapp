#!/bin/sh
set -eu

# New images declare the host deploy contract they require. Legacy images do
# not set MIFP_DEPLOY_CONTRACT_REQUIRED and remain rollback-compatible.
if [ -n "${MIFP_DEPLOY_CONTRACT_REQUIRED:-}" ]; then
  case "${MIFP_DEPLOY_CONTRACT:-}" in *[!0-9]*|'') host_contract=0 ;; *) host_contract=$MIFP_DEPLOY_CONTRACT ;; esac
  case "$MIFP_DEPLOY_CONTRACT_REQUIRED" in *[!0-9]*|'') required_contract=999999 ;; *) required_contract=$MIFP_DEPLOY_CONTRACT_REQUIRED ;; esac
  if [ "$host_contract" -lt "$required_contract" ]; then
    echo "MIFP deploy contract mismatch: host=${MIFP_DEPLOY_CONTRACT:-missing} image=$MIFP_DEPLOY_CONTRACT_REQUIRED" >&2
    exit 78
  fi
fi

DATABASE_PATH="${DATABASE_PATH:-/app/data/mifp.db}"
ASSETS_DIR="${ASSETS_DIR:-/app/data/assets}"
EXPORT_DIR="${EXPORT_DIR:-/app/data/exports}"
LOG_DIR="${LOG_DIR:-/app/data/logs}"
CONFERENCES_DIR="${CONFERENCES_DIR:-/app/data/conferences}"
TMPDIR="${TMPDIR:-/app/data/tmp}"
export TMPDIR
BANNER_SETTINGS_PATH="${BANNER_SETTINGS_PATH:-/app/data/config/banner_settings.json}"

mkdir -p \
  "$(dirname "$DATABASE_PATH")" \
  "$ASSETS_DIR" "$EXPORT_DIR" "$LOG_DIR" "$CONFERENCES_DIR" "$TMPDIR" \
  "$(dirname "$BANNER_SETTINGS_PATH")"

if [ ! -f "$BANNER_SETTINGS_PATH" ] && [ -f /app/config/banner_settings.json ]; then
  cp /app/config/banner_settings.json "$BANNER_SETTINGS_PATH"
fi

# Database lifecycle is explicit in every environment.  The container never
# creates or migrates schema as a startup side effect.
python -m mifp_app.db.runtime_check

exec "$@"

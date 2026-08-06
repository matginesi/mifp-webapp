#!/bin/sh
set -eu

DATABASE_PATH="${DATABASE_PATH:-/app/data/mifp.db}"
ASSETS_DIR="${ASSETS_DIR:-/app/data/assets}"
EXPORT_DIR="${EXPORT_DIR:-/app/data/exports}"
LOG_DIR="${LOG_DIR:-/app/data/logs}"
CONFERENCES_DIR="${CONFERENCES_DIR:-/app/data/conferences}"
BANNER_SETTINGS_PATH="${BANNER_SETTINGS_PATH:-/app/data/config/banner_settings.json}"

mkdir -p \
  "$(dirname "$DATABASE_PATH")" \
  "$ASSETS_DIR" "$EXPORT_DIR" "$LOG_DIR" "$CONFERENCES_DIR" \
  "$(dirname "$BANNER_SETTINGS_PATH")"

if [ ! -f "$BANNER_SETTINGS_PATH" ] && [ -f /app/config/banner_settings.json ]; then
  cp /app/config/banner_settings.json "$BANNER_SETTINGS_PATH"
fi

python -m mifp_archive.cli migrate --db "$DATABASE_PATH" >/tmp/mifp-migrate.json
python -m mifp_archive.cli health --db "$DATABASE_PATH" --assets "$ASSETS_DIR" >/tmp/mifp-health.json

exec "$@"

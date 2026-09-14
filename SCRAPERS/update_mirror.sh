#!/usr/bin/env bash
set -Eeo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
source "$PROJECT_DIR/scripts/tui.sh"

LOCAL_ROOT="${1:-/srv/http/mifp.eu}"
BASE_URL="${2:-https://old.mifp.eu/}"

if ! command -v wget &>/dev/null; then
  tui_error "wget is required but not installed"
  exit 1
fi

if [[ ! -d "$LOCAL_ROOT" ]]; then
  tui_info "Local root $LOCAL_ROOT does not exist — creating full mirror"
  mkdir -p "$LOCAL_ROOT"
fi

tui_info "Updating HTTrack mirror at: $LOCAL_ROOT"
tui_info "Source base: $BASE_URL"

wget --mirror \
     --convert-links \
     --adjust-extension \
     --page-requisites \
     --no-parent \
     --no-check-certificate \
     --timeout=30 \
     --tries=3 \
     --wait=0.5 \
     --random-wait \
     --directory-prefix="$LOCAL_ROOT" \
     "$BASE_URL" \
     2>&1 | while IFS= read -r line; do
       echo "  [wget] $line"
     done

EXIT_CODE=${PIPESTATUS[0]}
if [[ "$EXIT_CODE" -eq 0 ]]; then
  tui_ok "Mirror update complete"
else
  tui_warn "Mirror update finished with exit code $EXIT_CODE (partial results may exist)"
fi
exit "$EXIT_CODE"

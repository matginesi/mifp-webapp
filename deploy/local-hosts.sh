#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Maintain only the marked MIFP self-resolution block. Public domains never
# receive local mappings: for them this helper merely removes an old block.
DOMAIN="${1:-}"
HOSTS_FILE="${2:-/etc/hosts}"
WWW_DOMAIN="${3:-}"
EVENTS_HOST="${4:-}"
BEGIN_MARKER="# BEGIN MIFP LOCAL HOSTS"
END_MARKER="# END MIFP LOCAL HOSTS"

if [[ "$DOMAIN" == "--clear" ]]; then
  DOMAIN=""
elif [[ ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ || "$DOMAIN" != *.* ]]; then
  printf 'ERROR: invalid domain: %s\n' "$DOMAIN" >&2
  exit 2
fi
[[ -f "$HOSTS_FILE" && ! -L "$HOSTS_FILE" ]] || {
  printf 'ERROR: hosts file is missing or unsafe: %s\n' "$HOSTS_FILE" >&2
  exit 1
}

tmp="$(mktemp "$(dirname "$HOSTS_FILE")/.mifp-hosts.XXXXXX")"
cleanup() { rm -f -- "$tmp"; }
trap cleanup EXIT

awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
  $0 == begin { managed=1; next }
  $0 == end { managed=0; next }
  !managed { print }
' "$HOSTS_FILE" > "$tmp"

if [[ "$DOMAIN" == *.home.arpa ]]; then
  WWW_DOMAIN="${WWW_DOMAIN:-www.$DOMAIN}"
  {
    printf '%s\n' "$BEGIN_MARKER"
    if [[ -n "$EVENTS_HOST" ]]; then
      printf '127.0.0.1 %s %s %s\n' "$DOMAIN" "$WWW_DOMAIN" "$EVENTS_HOST"
    else
      printf '127.0.0.1 %s %s\n' "$DOMAIN" "$WWW_DOMAIN"
    fi
    printf '%s\n' "$END_MARKER"
  } >> "$tmp"
fi

chmod --reference="$HOSTS_FILE" "$tmp"
chown --reference="$HOSTS_FILE" "$tmp"
mv -f -- "$tmp" "$HOSTS_FILE"
trap - EXIT

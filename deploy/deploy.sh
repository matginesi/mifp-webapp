#!/usr/bin/env bash
set -Eeuo pipefail

# Release script for the MIFP VPS. Runs on the host; the production compose
# stack is expected under /opt/mifp (installed by deploy/bootstrap-vps.sh).
#
# Usage (from GitHub Actions or manually):
#   sudo -u mifp bash /opt/mifp/deploy.sh ghcr.io/matginesi/mifp-webapp:sha-XXXX
#   sudo -u mifp bash /opt/mifp/deploy.sh --rollback
#   sudo -u mifp bash /opt/mifp/deploy.sh status

MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
ENV_FILE="$MIFP_HOME/.env"
COMPOSE_FILE="$MIFP_HOME/compose.yaml"
RELEASE_FILE="$MIFP_HOME/release.env"
DATA_DIR="$MIFP_HOME/data"
COMPOSE_CMD=(docker compose --project-name mifp --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

[[ -f "$ENV_FILE" ]] || die "Manca $ENV_FILE. Esegui deploy/bootstrap-vps.sh per la prima configurazione."
[[ -f "$COMPOSE_FILE" ]] || die "Manca $COMPOSE_FILE."

read_current() {
  if [[ -f "$RELEASE_FILE" ]]; then
    sed -n 's/^MIFP_IMAGE=//p' "$RELEASE_FILE"
  fi
}

wait_ready() {
  local attempts="${1:-60}"
  say "Attendo /ready..."
  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 2 http://127.0.0.1:8000/ready >/dev/null 2>&1; then
      say "Pronto."
      return 0
    fi
    sleep 2
  done
  return 1
}

do_status() {
  say "Current image: $(read_current || echo none)"
  docker compose --project-name mifp -f "$COMPOSE_FILE" ps
  say "Caddy:"
  systemctl is-active caddy >/dev/null 2>&1 && say "  attivo" || say "  non attivo"
}

do_deploy() {
  local image="${1:-}"
  [[ -n "$image" ]] || die "Immagine non specificata: deploy.sh ghcr.io/<owner>/mifp-webapp:tag"
  [[ "$image" == ghcr.io/* ]] || die "Immagine non GHCR: $image"
  say "Deploy di $image"
  docker pull "$image" || die "Pull dell'immagine fallito."

  local previous
  previous="$(read_current || true)"
  if [[ -n "$previous" ]]; then
    say "Immagine precedente: $previous (rollback con --rollback)"
    sed -i '/^MIFP_IMAGE=/d' "$RELEASE_FILE"
    printf 'MIFP_IMAGE=%s\n' "$previous" >> "$RELEASE_FILE"
  else
    printf 'MIFP_IMAGE=%s\n' "$previous" > "$RELEASE_FILE"
  fi

  if ! "${COMPOSE_CMD[@]}" up -d --remove-orphans; then
    "${COMPOSE_CMD[@]}" logs --tail 260 web >&2 || true
    die "Avvio dello stack fallito."
  fi

  if ! wait_ready; then
    "${COMPOSE_CMD[@]}" ps >&2 || true
    "${COMPOSE_CMD[@]}" logs --tail 300 web storage-init >&2 || true
    if [[ -n "$previous" ]]; then
      say "Rollback automatico a $previous"
      sed -i '/^MIFP_IMAGE=/d' "$RELEASE_FILE"
      printf 'MIFP_IMAGE=%s\n' "$previous" >> "$RELEASE_FILE"
      "${COMPOSE_CMD[@]}" up -d --remove-orphans || true
    fi
    die "La webapp non è tornata pronta."
  fi

  sed -i '/^MIFP_IMAGE=/d' "$RELEASE_FILE"
  printf 'MIFP_IMAGE=%s\n' "$image" >> "$RELEASE_FILE"
  say "Deploy completato: $image"
}

do_rollback() {
  local previous
  previous="$(read_current || true)"
  [[ -n "$previous" ]] || die "Nessuna release precedente registrata."
  say "Rollback a $previous"
  sed -i '/^MIFP_IMAGE=/d' "$RELEASE_FILE"
  printf 'MIFP_IMAGE=%s\n' "$previous" >> "$RELEASE_FILE"
  "${COMPOSE_CMD[@]}" up -d --remove-orphans
  wait_ready || die "Rollback non diventato pronto."
  say "Rollback completato."
}

case "${1:-status}" in
  status) do_status ;;
  --rollback) do_rollback ;;
  -h|--help)
    echo "Uso: deploy.sh [ghcr.io/<owner>/mifp-webapp:tag | --rollback | status]"
    ;;
  *) do_deploy "$1" ;;
esac
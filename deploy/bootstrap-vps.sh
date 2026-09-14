#!/usr/bin/env bash
set -Eeuo pipefail

# One-time, idempotent MIFP VPS bootstrap for supported Ubuntu hosts.
# Run as root (normally via sudo). Application data uses the same numeric
# UID/GID as the non-root process inside the production container.

MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
MIFP_USER="mifp"
MIFP_GROUP="mifp"
MIFP_UID="10001"
MIFP_GID="10001"
DOMAIN="${MIFP_DOMAIN:-}"
IMAGE_REPOSITORY="${MIFP_IMAGE_REPOSITORY:-ghcr.io/matginesi/mifp-webapp}"
SSH_PORT="${MIFP_SSH_PORT:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Uso:
  sudo bash bootstrap-vps.sh --domain mifp.eu \
    [--image-repository ghcr.io/OWNER/REPO] [--ssh-port 22]

Il comando genera automaticamente SECRET_KEY e configura l'amministratore
interattivamente se non è già presente. Nessuna password viene salvata in chiaro.
EOF
}

while (($#)); do
  case "$1" in
    --domain) [[ $# -ge 2 ]] || die "--domain richiede un valore"; DOMAIN="$2"; shift 2 ;;
    --domain=*) DOMAIN="${1#*=}"; shift ;;
    --image-repository) [[ $# -ge 2 ]] || die "--image-repository richiede un valore"; IMAGE_REPOSITORY="$2"; shift 2 ;;
    --image-repository=*) IMAGE_REPOSITORY="${1#*=}"; shift ;;
    --ssh-port) [[ $# -ge 2 ]] || die "--ssh-port richiede un valore"; SSH_PORT="$2"; shift 2 ;;
    --ssh-port=*) SSH_PORT="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Opzione sconosciuta: $1" ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || die "Esegui come root (sudo)."
[[ -r /etc/os-release ]] || die "Impossibile identificare il sistema operativo."
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "Bootstrap supportato solo su Ubuntu (rilevato: ${ID:-unknown})."

[[ -n "$DOMAIN" ]] || { usage >&2; die "--domain è obbligatorio"; }
DOMAIN="${DOMAIN,,}"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]] || die "Dominio non valido: $DOMAIN"
[[ "$IMAGE_REPOSITORY" =~ ^ghcr\.io/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "Repository GHCR non valido: $IMAGE_REPOSITORY"
if [[ -z "$SSH_PORT" ]]; then
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    SSH_PORT="$(awk '{print $4}' <<<"$SSH_CONNECTION")"
  elif [[ -n "${SSH_CLIENT:-}" ]]; then
    SSH_PORT="$(awk '{print $3}' <<<"$SSH_CLIENT")"
  else
    SSH_PORT=22
  fi
fi
[[ "$SSH_PORT" =~ ^[0-9]+$ ]] && ((SSH_PORT >= 1 && SSH_PORT <= 65535)) || die "Porta SSH non valida: $SSH_PORT"
[[ -f "$SCRIPT_DIR/configure.py" && -f "$SCRIPT_DIR/backup.sh" && -f "$SCRIPT_DIR/mifpctl" ]] || die "Cartella deploy incompleta: copia tutti i file deploy/."

say "Installo i pacchetti di base"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https python3 sqlite3 rsync restic ufw util-linux

say "Configuro Docker Engine dal repository ufficiale"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list

say "Configuro Caddy dal repository ufficiale"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  > /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin caddy
systemctl enable --now docker.service

docker info >/dev/null 2>&1 || die "Docker è installato ma il daemon non risponde."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 non è disponibile."

if getent group "$MIFP_GROUP" >/dev/null 2>&1; then
  [[ "$(getent group "$MIFP_GROUP" | cut -d: -f3)" == "$MIFP_GID" ]] || die "Il gruppo $MIFP_GROUP esiste ma non ha GID $MIFP_GID."
else
  say "Creo il gruppo dati $MIFP_GROUP ($MIFP_GID)"
  groupadd --system --gid "$MIFP_GID" "$MIFP_GROUP"
fi

if id "$MIFP_USER" >/dev/null 2>&1; then
  [[ "$(id -u "$MIFP_USER")" == "$MIFP_UID" && "$(id -g "$MIFP_USER")" == "$MIFP_GID" ]] || die "L'utente $MIFP_USER esiste ma non usa UID:GID $MIFP_UID:$MIFP_GID."
else
  say "Creo l'utente dati $MIFP_USER ($MIFP_UID:$MIFP_GID)"
  useradd --system --uid "$MIFP_UID" --gid "$MIFP_GID" --home "$MIFP_HOME" --shell /usr/sbin/nologin "$MIFP_USER"
fi

say "Preparo $MIFP_HOME"
install -d -o root -g root -m 0755 "$MIFP_HOME"
install -d -o "$MIFP_UID" -g "$MIFP_GID" -m 0750 "$MIFP_HOME/data"
for dir in assets backups conferences exports logs config tmp; do
  install -d -o "$MIFP_UID" -g "$MIFP_GID" -m 0750 "$MIFP_HOME/data/$dir"
done

say "Installo i file di deploy"
install -o root -g root -m 0644 "$SCRIPT_DIR/compose.production.yaml" "$MIFP_HOME/compose.yaml"
install -o root -g root -m 0750 "$SCRIPT_DIR/deploy.sh" "$MIFP_HOME/deploy.sh"
install -o root -g root -m 0750 "$SCRIPT_DIR/configure.py" "$MIFP_HOME/configure.py"
install -o root -g root -m 0644 "$SCRIPT_DIR/.env.production.example" "$MIFP_HOME/.env.example"
install -o root -g root -m 0644 "$SCRIPT_DIR/Caddyfile" "$MIFP_HOME/Caddyfile.example"
install -o root -g root -m 0750 "$SCRIPT_DIR/backup.sh" "$MIFP_HOME/backup.sh"
install -o root -g root -m 0755 "$SCRIPT_DIR/mifpctl" /usr/local/sbin/mifpctl
install -o root -g root -m 0644 "$SCRIPT_DIR/mifp-backup.service" /etc/systemd/system/mifp-backup.service
install -o root -g root -m 0644 "$SCRIPT_DIR/mifp-backup.timer" /etc/systemd/system/mifp-backup.timer
systemctl daemon-reload
if [[ -f "$MIFP_HOME/data/mifp.db" && ! -L "$MIFP_HOME/data/mifp.db" ]]; then
  systemctl enable --now mifp-backup.timer
else
  systemctl disable --now mifp-backup.timer >/dev/null 2>&1 || true
fi

say "Configuro segreti e amministratore"
python3 "$MIFP_HOME/configure.py" configure \
  --env-file "$MIFP_HOME/.env" \
  --example "$MIFP_HOME/.env.example" \
  --domain "$DOMAIN" \
  --image-repository "$IMAGE_REPOSITORY" \
  --admin-if-missing
chown root:root "$MIFP_HOME/.env"
chmod 0600 "$MIFP_HOME/.env"

say "Configuro Caddy per $DOMAIN"
sed "s/__MIFP_DOMAIN__/$DOMAIN/g" "$SCRIPT_DIR/Caddyfile" > /etc/caddy/Caddyfile
chown root:caddy /etc/caddy/Caddyfile
chmod 0644 /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl enable --now caddy.service
systemctl reload caddy.service

say "Configuro firewall"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

say "Bootstrap completato"
printf '%s\n' \
  "Primo avvio: sudo mifpctl first-deploy sha-<commit>" \
  "Poi importa lo ZIP contenuti dalla dashboard." \
  "Nuove versioni: sudo mifpctl deploy sha-<commit>" \
  "Diagnostica: sudo mifpctl doctor" \
  "Backup manuale: sudo mifpctl backup"

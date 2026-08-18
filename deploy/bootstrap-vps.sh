#!/usr/bin/env bash
set -Eeuo pipefail

# Idempotent first-time setup for the MIFP production VPS. Run as root on a
# fresh Ubuntu 24.04/26.04 host. Safe to re-run.
#
#   sudo bash /opt/mifp/deploy/bootstrap-vps.sh --domain mifp.eu
#
# After this script, run the data install (data dir, initial .env values,
# first import) and then trigger releases from GitHub Actions.

MIFP_HOME="/opt/mifp"
MIFP_USER="mifp"
DOMAIN="${MIFP_DOMAIN:-}"
MIFP_ADMIN_EMAIL="${MIFP_ADMIN_EMAIL:-admin@mifp.eu}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while (($#)); do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --domain=*) DOMAIN="${1#*=}"; shift ;;
    *) echo "Opzione sconosciuta: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$DOMAIN" ]] || { echo "Uso: bootstrap-vps.sh --domain mifp.eu" >&2; exit 2; }
[[ "$(id -u)" -eq 0 ]] || { echo "Esegui come root (sudo)." >&2; exit 1; }

say() { printf '\n==> %s\n' "$*"; }

say "Aggiorno i pacchetti"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl ca-certificates gnupg

say "Configuro Docker Engine"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

say "Configuro Caddy"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://github.com/caddyserver/caddy/releases/download/v2.10.2/caddy_2.10.2_linux_amd64.tar.gz -o /tmp/caddy.tar.gz
mkdir -p /tmp/caddy-bin
tar -xzf /tmp/caddy.tar.gz -C /tmp/caddy-bin
install -m 0755 /tmp/caddy-bin/caddy /usr/bin/caddy
rm -rf /tmp/caddy-bin /tmp/caddy.tar.gz
caddy version

if ! id "$MIFP_USER" >/dev/null 2>&1; then
  say "Creo l'utente $MIFP_USER"
  useradd --system --home "$MIFP_HOME" --shell /usr/sbin/nologin "$MIFP_USER"
fi

say "Struttura /opt/mifp"
mkdir -p \
  "$MIFP_HOME/data/assets" "$MIFP_HOME/data/backups" \
  "$MIFP_HOME/data/conferences" "$MIFP_HOME/data/exports" \
  "$MIFP_HOME/data/logs" "$MIFP_HOME/data/config" "$MIFP_HOME/data/tmp"

say "Copio i file di deploy"
cp "$SCRIPT_DIR/compose.production.yaml" "$MIFP_HOME/compose.yaml"
cp "$SCRIPT_DIR/Caddyfile" "$MIFP_HOME/Caddyfile.example"
cp "$SCRIPT_DIR/deploy.sh" "$MIFP_HOME/deploy.sh"
chmod 0750 "$MIFP_HOME/deploy.sh"
cp "$SCRIPT_DIR/.env.production.example" "$MIFP_HOME/.env.example"

if [[ ! -f "$MIFP_HOME/.env" ]]; then
  say "Creo $MIFP_HOME/.env dal template (compila i segreti!)"
  cp "$SCRIPT_DIR/.env.production.example" "$MIFP_HOME/.env"
  sed -i "s/^MIFP_DOMAIN=.*/MIFP_DOMAIN='$DOMAIN'/" "$MIFP_HOME/.env"
  sed -i "s/^TRUSTED_HOSTS=.*/TRUSTED_HOSTS='$DOMAIN,www.$DOMAIN,127.0.0.1,localhost'/" "$MIFP_HOME/.env"
fi
chmod 0600 "$MIFP_HOME/.env"
chown -R "$MIFP_USER:$MIFP_USER" "$MIFP_HOME"

say "Configuro Caddy per $DOMAIN"
if [[ -d /etc/caddy ]]; then
  sed "s/\$MIFP_DOMAIN/$DOMAIN/g" "$SCRIPT_DIR/Caddyfile" > /etc/caddy/Caddyfile
fi
cat > /etc/systemd/system/caddy.service <<EOF
[Unit]
Description=Caddy web server (MIFP reverse proxy)
After=network.target docker.service
Requires=docker.service

[Service]
Type=notify
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable caddy.service

say "Firewall"
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null || true
fi

say "Prossimi passi"
echo "  1. Compila i segreti in $MIFP_HOME/.env: SECRET_KEY, ADMIN_PASSWORD_HASH (usa ./mifp hash in locale)"
echo "  2. Copia i dati iniziali in $MIFP_HOME/data (mifp.db, assets/) con proprietario $MIFP_USER"
echo "  3. Avvia lo stack: sudo -u $MIFP_USER bash $MIFP_HOME/deploy.sh ghcr.io/matginesi/mifp-webapp:sha-XXXX"
echo "  4. Avvia Caddy: systemctl start caddy"
echo "  5. Configura le secret GitHub: VPS_HOST, VPS_USER, VPS_SSH_KEY, VPS_KNOWN_HOSTS, VPS_DOMAIN"
#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

# One-time, idempotent MIFP VPS bootstrap for supported Ubuntu hosts.
# Run as root (normally via sudo). Application data uses the same numeric
# UID/GID as the non-root process inside the production container.

MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
MIFP_USER="mifp"
MIFP_GROUP="mifp"
MIFP_UID="10001"
MIFP_GID="10001"
EVENTS_PHP_USER="${MIFP_EVENTS_PHP_USER:-mifp-events}"
EVENTS_PUBLIC_GROUP="${MIFP_EVENTS_PUBLIC_GROUP:-mifp-events-public}"
EVENTS_PRIVATE_DIR="${MIFP_EVENTS_PRIVATE_DIR:-/srv/mifp-events-private}"
EVENTS_PHP_STATE="${MIFP_EVENTS_PHP_STATE:-$MIFP_HOME/events-php-enabled.txt}"
EVENTS_PHP_INCLUDE="${MIFP_EVENTS_PHP_INCLUDE:-/etc/caddy/mifp-events-php.caddy}"
MAIL_RELAY_CONFIG="${MIFP_MAIL_RELAY_CONFIG:-/etc/msmtprc}"
DOMAIN="${MIFP_DOMAIN:-}"
EVENTS_BACKEND_SELECTION=""
IMAGE_REPOSITORY="${MIFP_IMAGE_REPOSITORY:-}"
SSH_PORT="${MIFP_SSH_PORT:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

is_supported_ubuntu_release() {
  [[ "$1" == "ubuntu" ]] && [[ "$2" == "22.04" || "$2" == "24.04" ]]
}

usage() {
  cat <<'EOF'
Uso:
  sudo bash bootstrap-vps.sh [--domain mifp.eu] \
    [--image-repository ghcr.io/OWNER/REPO] [--ssh-port 22] \
    [--events-backend local-vps|remote|disabled]

Il dominio e l'amministratore possono essere configurati dopo il provisioning
con `sudo mifpctl configure` e `sudo mifpctl admin`.
EOF
}

while (($#)); do
  case "$1" in
    --events-backend) [[ $# -ge 2 ]] || die "--events-backend richiede un valore"; EVENTS_BACKEND_SELECTION="$2"; shift 2 ;;
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
is_supported_ubuntu_release "${ID:-}" "${VERSION_ID:-}" \
  || die "Sistema non supportato: ${ID:-unknown} ${VERSION_ID:-unknown}. Sono supportati esclusivamente Ubuntu 22.04 e Ubuntu 24.04 (target di produzione corrente: 24.04)."

[[ -z "$EVENTS_BACKEND_SELECTION" || "$EVENTS_BACKEND_SELECTION" =~ ^(local-vps|remote|disabled)$ ]] || die "Backend eventi non valido."
DOMAIN="${DOMAIN,,}"
if [[ -n "$DOMAIN" ]]; then
  [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]] || die "Dominio non valido: $DOMAIN"
fi
[[ -z "$IMAGE_REPOSITORY" || "$IMAGE_REPOSITORY" =~ ^ghcr\.io/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "Repository GHCR non valido: $IMAGE_REPOSITORY"
if [[ -z "$SSH_PORT" ]]; then
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    SSH_PORT="$(awk '{print $4}' <<<"$SSH_CONNECTION")"
  elif [[ -n "${SSH_CLIENT:-}" ]]; then
    SSH_PORT="$(awk '{print $3}' <<<"$SSH_CLIENT")"
  fi
fi
if [[ -z "$SSH_PORT" ]]; then
  # No interactive session (cloud-init, provider console): ask sshd itself
  # instead of assuming 22. Enabling the firewall with the wrong port would
  # lock the operator out of the host.
  SSH_PORT="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2; exit}')" || true
fi
if [[ -z "$SSH_PORT" ]]; then
  die "Impossibile determinare la porta SSH (nessuna sessione SSH attiva e sshd non leggibile). Rilancia con --ssh-port N."
fi
[[ "$SSH_PORT" =~ ^[0-9]+$ ]] && ((SSH_PORT >= 1 && SSH_PORT <= 65535)) || die "Porta SSH non valida: $SSH_PORT"
required=(bootstrap-vps.sh refresh-host-tools.sh deploy.sh configure.py vps_config.py
  backup.sh local-hosts.sh mifpctl compose.production.yaml .env.production.example
  registration-tools.py compose.registrations.yaml Caddyfile mifp-backup.service mifp-backup.timer
  mifp-alert-check.sh mifp-alert-check.service mifp-alert-check.timer)
for name in "${required[@]}"; do
  file="$SCRIPT_DIR/$name"
  [[ -f "$file" && ! -L "$file" ]] || die "Bundle incompleto o non sicuro: $name"
  mode="$(stat -c %a "$file")"
  (( (8#$mode & 8#022) == 0 )) || die "File bundle modificabile da gruppo/altri: $name"
  [[ "$(stat -c %u "$file")" == 0 ]] || die "File bundle non root-owned: $name"
done
# Staging must be root-owned, including its ancestors. A sticky root-owned
# /tmp is allowed as an ancestor, never as the staging directory itself.
bundle_parent="$SCRIPT_DIR"
while :; do
  [[ ! -L "$bundle_parent" && "$(stat -c %u "$bundle_parent")" == 0 ]] \
    || die "Staging bundle non root-owned: $bundle_parent"
  bundle_mode="$(stat -c %a "$bundle_parent")"
  if (( (8#$bundle_mode & 8#022) != 0 )); then
    [[ "$bundle_parent" != "$SCRIPT_DIR" ]] && (( (8#$bundle_mode & 8#1000) != 0 )) \
      || die "Directory bundle modificabile da gruppo/altri: $bundle_parent"
  fi
  [[ "$bundle_parent" != / ]] || break
  bundle_parent="$(dirname "$bundle_parent")"
done

for script in bootstrap-vps.sh refresh-host-tools.sh deploy.sh backup.sh local-hosts.sh mifp-alert-check.sh mifpctl; do
  bash -n "$SCRIPT_DIR/$script" || die "Sintassi shell non valida: $script"
done
for helper in configure.py vps_config.py registration-tools.py; do
  python3 -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); compile(p.read_bytes(), str(p), "exec")' "$SCRIPT_DIR/$helper" || die "Sintassi Python non valida: $helper"
done

say "Installo i pacchetti di base"
export DEBIAN_FRONTEND=noninteractive
# A concurrent or previously interrupted run can leave dpkg half-configured;
# repair it first so the install below cannot fail obscurely.
mkdir -p /run/lock
exec 9>/run/lock/mifp-bootstrap.lock
flock -n 9 || die "Un altro bootstrap MIFP è in corso."
dpkg --configure -a >/dev/null 2>&1 || true
apt-get update -y
apt-get install -y ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https \
  python3 sqlite3 rsync restic ufw util-linux php-fpm php-cli php-mbstring php-curl msmtp-mta \
  unattended-upgrades needrestart update-notifier-common fail2ban python3-systemd

say "Configuro Docker Engine dal repository ufficiale"
install -m 0755 -d /etc/apt/keyrings
# Pin the signing key to its published fingerprint: TLS alone would let a
# DNS/TLS compromise install a rogue key trusted for every future docker-ce
# package. Override only after verifying the new fingerprint out of band.
DOCKER_KEY_FINGERPRINT="${MIFP_DOCKER_KEY_FINGERPRINT:-9DC858229FC7DD38854AE2D88D81803C0EBFCD88}"
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
DOCKER_KEY_ACTUAL="$(gpg --batch --with-colons --import-options show-only --import /etc/apt/keyrings/docker.asc 2>/dev/null | awk -F: '/^fpr:/{print $10; exit}')"
[[ -n "$DOCKER_KEY_ACTUAL" ]] || die "Impossibile leggere la chiave APT di Docker."
[[ "$DOCKER_KEY_ACTUAL" == "$DOCKER_KEY_FINGERPRINT" ]] \
  || die "Fingerprint della chiave Docker inattesa: $DOCKER_KEY_ACTUAL (atteso $DOCKER_KEY_FINGERPRINT). Verifica la chiave e aggiorna MIFP_DOCKER_KEY_FINGERPRINT."
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list

say "Configuro Caddy dal repository ufficiale"
# Same reasoning as Docker. Caddy rotates this key occasionally; if the check
# fails, verify the new fingerprint against the official install page and set
# MIFP_CADDY_KEY_FINGERPRINT explicitly.
CADDY_KEY_FINGERPRINT="${MIFP_CADDY_KEY_FINGERPRINT:-65760C51EDEA2017CEA2CA15155B6D79CA56EA34}"
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
CADDY_KEY_ACTUAL="$(gpg --batch --with-colons --import-options show-only --import /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null | awk -F: '/^fpr:/{print $10; exit}')"
[[ -n "$CADDY_KEY_ACTUAL" ]] || die "Impossibile leggere la chiave APT di Caddy."
[[ "$CADDY_KEY_ACTUAL" == "$CADDY_KEY_FINGERPRINT" ]] \
  || die "Fingerprint della chiave Caddy inattesa: $CADDY_KEY_ACTUAL (atteso $CADDY_KEY_FINGERPRINT). Verifica la chiave e aggiorna MIFP_CADDY_KEY_FINGERPRINT."
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  > /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin caddy
systemctl enable --now docker.service

# Daemon-wide safety defaults: survive a docker restart without killing the
# app, and bound log growth for any future service that forgets a logging block.
if [[ ! -f /etc/docker/daemon.json ]]; then
  say "Configuro i default del daemon Docker"
  install -d -m 0755 /etc/docker
  cat > /etc/docker/daemon.json <<'EOF_DOCKER_DAEMON'
{
  "live-restore": true,
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF_DOCKER_DAEMON
  chmod 0644 /etc/docker/daemon.json
  systemctl restart docker.service
fi

docker info >/dev/null 2>&1 || die "Docker è installato ma il daemon non risponde."
docker compose -f "$SCRIPT_DIR/compose.production.yaml" config --no-interpolate -q >/dev/null || die "Compose bundle non valido."
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
install -o root -g root -m 0750 "$SCRIPT_DIR/vps_config.py" "$MIFP_HOME/vps_config.py"
install -o root -g root -m 0750 "$SCRIPT_DIR/registration-tools.py" "$MIFP_HOME/registration-tools.py"
install -o root -g root -m 0644 "$SCRIPT_DIR/compose.registrations.yaml" "$MIFP_HOME/compose.registrations.yaml"
install -o root -g root -m 0644 "$SCRIPT_DIR/.env.production.example" "$MIFP_HOME/.env.example"
install -o root -g root -m 0644 "$SCRIPT_DIR/Caddyfile" "$MIFP_HOME/Caddyfile.example"
install -o root -g root -m 0750 "$SCRIPT_DIR/backup.sh" "$MIFP_HOME/backup.sh"
install -o root -g root -m 0750 "$SCRIPT_DIR/local-hosts.sh" "$MIFP_HOME/local-hosts.sh"
install -o root -g root -m 0750 "$SCRIPT_DIR/refresh-host-tools.sh" "$MIFP_HOME/refresh-host-tools.sh"
install -o root -g root -m 0755 "$SCRIPT_DIR/mifpctl" /usr/local/sbin/mifpctl
install -o root -g root -m 0644 "$SCRIPT_DIR/mifp-backup.service" /etc/systemd/system/mifp-backup.service
install -o root -g root -m 0644 "$SCRIPT_DIR/mifp-backup.timer" /etc/systemd/system/mifp-backup.timer
install -o root -g root -m 0750 "$SCRIPT_DIR/mifp-alert-check.sh" "$MIFP_HOME/mifp-alert-check.sh"
install -o root -g root -m 0644 "$SCRIPT_DIR/mifp-alert-check.service" /etc/systemd/system/mifp-alert-check.service
install -o root -g root -m 0644 "$SCRIPT_DIR/mifp-alert-check.timer" /etc/systemd/system/mifp-alert-check.timer
# The units are written for the default layout; rewrite paths when the
# operator overrides MIFP_HOME / MIFP_CONFIG_DIR so the timer cannot silently
# run a non-existent script.
if [[ "$MIFP_HOME" != "/opt/mifp" || "${MIFP_CONFIG_DIR:-/etc/mifp}" != "/etc/mifp" ]]; then
  sed -i -e "s|/opt/mifp|$MIFP_HOME|g" -e "s|/etc/mifp|${MIFP_CONFIG_DIR:-/etc/mifp}|g" \
    /etc/systemd/system/mifp-backup.service /etc/systemd/system/mifp-alert-check.service
fi
systemctl daemon-reload

say "Inizializzo la configurazione progressiva"
# Compatibility with installations that used the earlier /etc location.
if [[ ! -f "$MIFP_HOME/.env" && -f /etc/mifp/production.env && ! -L /etc/mifp/production.env ]]; then
  install -o root -g root -m 0600 /etc/mifp/production.env "$MIFP_HOME/.env"
fi
# Import existing runtime values directly. Seeding the example first would make
# its production defaults look like operator choices on a fresh local host.
install -d -o root -g root -m 0750 /etc/mifp
CONFIG_ARGS=(
  --config-file /etc/mifp/config.env
  --secrets-file /etc/mifp/secrets.env
  --runtime-env "$MIFP_HOME/.env"
  --example "$MIFP_HOME/.env.example"
  --docker-config /root/.docker/config.json
  init
)
[[ -z "$IMAGE_REPOSITORY" ]] || CONFIG_ARGS+=(--image-repository "$IMAGE_REPOSITORY")
[[ -z "$DOMAIN" ]] || CONFIG_ARGS+=(--domain "$DOMAIN")
python3 "$MIFP_HOME/vps_config.py" "${CONFIG_ARGS[@]}"
chown root:root "$MIFP_HOME/.env"
chmod 0600 "$MIFP_HOME/.env"
if [[ -n "$EVENTS_BACKEND_SELECTION" ]]; then
  python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" set EVENTS_PUBLISH_BACKEND "$EVENTS_BACKEND_SELECTION"
fi
python3 "$MIFP_HOME/vps_config.py" \
  --config-file /etc/mifp/config.env \
  --secrets-file /etc/mifp/secrets.env \
  --runtime-env "$MIFP_HOME/.env" \
  materialize-secrets --output-dir /etc/mifp/secrets \
  --uid "$MIFP_UID" --gid "$MIFP_GID" --mode 0400
DOMAIN="$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get DOMAIN)"
WWW_DOMAIN="$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get WWW_DOMAIN)"
EVENTS_BACKEND="$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get EVENTS_PUBLISH_BACKEND)"
EVENTS_PUBLIC_URL="$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get EVENTS_PUBLIC_BASE_URL)"
EVENTS_HOST="$(python3 -c 'import sys; from urllib.parse import urlsplit; print(urlsplit(sys.argv[1]).hostname or "")' "$EVENTS_PUBLIC_URL")"
EVENTS_LOCAL_ROOT="$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get EVENTS_LOCAL_ROOT)"

# The same explicit host apply command provisions fresh hosts and updates.
# It reads canonical configuration and never touches application/release data.
systemctl enable --now caddy.service
bash "$MIFP_HOME/deploy.sh" host-apply all
PHP_FPM_SERVICE="$(cat "$MIFP_HOME/php-fpm.service" 2>/dev/null || true)"
MAIL_PROVIDER="$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get MAIL_PROVIDER)"

if [[ -f "$MIFP_HOME/data/mifp.db" && ! -L "$MIFP_HOME/data/mifp.db" ]] \
  && [[ "$(python3 "$MIFP_HOME/vps_config.py" --config-file /etc/mifp/config.env --secrets-file /etc/mifp/secrets.env --runtime-env "$MIFP_HOME/.env" get BACKUP_ENABLED)" != false ]]; then
  systemctl enable --now mifp-backup.timer
else
  systemctl disable --now mifp-backup.timer >/dev/null 2>&1 || true
fi
if [[ "$MAIL_PROVIDER" == "smtp" && -f "$MAIL_RELAY_CONFIG" ]]; then
  systemctl enable --now mifp-alert-check.timer
else
  systemctl disable --now mifp-alert-check.timer >/dev/null 2>&1 || true
fi

if [[ "$DOMAIN" == *.home.arpa ]]; then
  say "Configuro il trust TLS locale sulla VPS"
  if caddy trust --config /etc/caddy/Caddyfile --adapter caddyfile; then
    printf '%s\n' "CA locale Caddy installata nel trust store della VPS."
  else
    printf '%s\n' "WARN: caddy trust non completato (certutil può non essere installato)." >&2
  fi
  printf '%s\n' \
    "Questo trust vale solo sulla VPS, non sulla workstation." \
    "Root CA da copiare se necessaria:" \
    "  /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"
fi

say "Configuro aggiornamenti di sicurezza automatici"
# Security updates only, and never an automatic reboot of a production host.
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF_AUTO_UPGRADES'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF_AUTO_UPGRADES
cat > /etc/apt/apt.conf.d/52mifp-unattended <<'EOF_MIFP_UNATTENDED'
// MIFP: apply security updates only. The Docker and Caddy third-party
// repositories have no -security suite, so they are NOT auto-updated here and
// must be patched deliberately (see docs/DEPLOY_NEW_VPS.md).
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "false";
EOF_MIFP_UNATTENDED
chmod 0644 /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/52mifp-unattended
systemctl enable --now unattended-upgrades.service >/dev/null 2>&1 || true

say "Configuro la protezione brute-force SSH (fail2ban)"
cat > /etc/fail2ban/jail.d/mifp-sshd.local <<EOF_FAIL2BAN
# MIFP: SSH-only brute-force protection. Application login throttling is
# handled inside the webapp, not here.
[DEFAULT]
backend  = systemd
ignoreip = 127.0.0.1/8 ::1
bantime  = 1h
findtime = 10m
maxretry = 5
banaction = ufw
banaction_allports = ufw

[sshd]
enabled = true
port    = $SSH_PORT
EOF_FAIL2BAN
chmod 0644 /etc/fail2ban/jail.d/mifp-sshd.local
systemctl enable --now fail2ban.service >/dev/null 2>&1 || true

say "Configuro firewall"
# UFW only filters IPv6 when IPV6=yes; without this a public v6 address would be
# reachable while the v4 rules look correct.
if [[ -f /etc/default/ufw ]]; then
  sed -i 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw
  grep -q '^IPV6=yes' /etc/default/ufw || printf 'IPV6=yes\n' >> /etc/default/ufw
fi
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
# `limit` adds basic connection-rate limiting on top of the fail2ban jail.
ufw limit "$SSH_PORT/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
# Fail closed: never leave the host with the firewall up but the SSH rule
# missing, which would lock the operator out.
ufw status verbose | grep -qE "(^|[[:space:]])${SSH_PORT}/tcp" \
  || die "Regola UFW per la porta SSH $SSH_PORT assente: verifica manualmente prima di disconnetterti."
ufw status verbose | grep -q "(v6)" \
  || die "UFW non mostra regole IPv6: verifica /etc/default/ufw prima di considerare l'host protetto."

say "Riepilogo post-condizioni"
POSTCONDITIONS_OK=1
POSTCONDITION_UNITS=(docker.service caddy.service fail2ban.service)
[[ "$EVENTS_BACKEND" != "local-vps" ]] || POSTCONDITION_UNITS+=("$PHP_FPM_SERVICE")
for unit in "${POSTCONDITION_UNITS[@]}"; do
  if systemctl is-active --quiet "$unit"; then
    printf '  OK   %s attivo\n' "$unit"
  else
    printf '  FAIL %s NON attivo\n' "$unit" >&2
    POSTCONDITIONS_OK=0
  fi
done
POSTCONDITION_PATHS=("$MIFP_HOME/data" /etc/mifp/config.env /etc/mifp/secrets.env)
if [[ "$EVENTS_BACKEND" == "local-vps" ]]; then
  POSTCONDITION_PATHS+=("$EVENTS_LOCAL_ROOT" "$EVENTS_PRIVATE_DIR" "$EVENTS_PHP_STATE")
fi
for path in "${POSTCONDITION_PATHS[@]}"; do
  if [[ -e "$path" ]]; then
    printf '  OK   %s\n' "$path"
  else
    printf '  FAIL %s mancante\n' "$path" >&2
    POSTCONDITIONS_OK=0
  fi
done
if ufw status | grep -q "Status: active"; then
  printf '  OK   ufw attivo\n'
else
  printf '  FAIL ufw non attivo\n' >&2
  POSTCONDITIONS_OK=0
fi
[[ "$POSTCONDITIONS_OK" == "1" ]] || die "Bootstrap completato con post-condizioni mancanti: risolvile prima di procedere."

say "Host bootstrap completed"
printf '%s\n' \
  "" \
  "Next:" \
  "  sudo mifpctl configure" \
  "  sudo mifpctl admin" \
  "  sudo mifpctl config-check" \
  "  sudo mifpctl init" \
  "" \
  "registry-login è opzionale e serve solo per package privati."

if [[ "$DOMAIN" == *.home.arpa ]]; then
  LAN_IPS="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\./ && $0 !~ /^127\./ {print}' | sort -u)"
  HOST_SHORT="$(hostname -s 2>/dev/null || printf 'vpsbox')"
  if [[ "$(printf '%s\n' "$LAN_IPS" | awk 'NF {count++} END {print count+0}')" == 1 ]]; then
    LAN_IP="$LAN_IPS"
    printf '\nAdd to your workstation /etc/hosts:\n\n%s %s %s %s\n' \
      "$LAN_IP" "$HOST_SHORT" "$DOMAIN" "$WWW_DOMAIN"
  else
    printf '\nWorkstation /etc/hosts: LAN IP ambiguous or unavailable. On the VPS run:\n\n  hostname -I\n\nThen map: %s %s %s\n' \
      "$HOST_SHORT" "$DOMAIN" "$WWW_DOMAIN"
  fi
fi

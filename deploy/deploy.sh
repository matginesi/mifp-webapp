#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

# MIFP production operator. Installed as /usr/local/sbin/mifpctl.
MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
ENV_FILE="$MIFP_HOME/.env"
ENV_EXAMPLE="$MIFP_HOME/.env.example"
CONFIG_HELPER="$MIFP_HOME/configure.py"
VPS_CONFIG_HELPER="$MIFP_HOME/vps_config.py"
CONFIG_DIR="${MIFP_CONFIG_DIR:-/etc/mifp}"
PUBLIC_CONFIG_FILE="$CONFIG_DIR/config.env"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
SECRET_MATERIAL_DIR="${MIFP_SECRET_MATERIAL_DIR:-$CONFIG_DIR/secrets}"
DOCKER_CONFIG_FILE="${MIFP_DOCKER_CONFIG_FILE:-/root/.docker/config.json}"
COMPOSE_FILE="$MIFP_HOME/compose.yaml"
RELEASE_FILE="$MIFP_HOME/release.env"
UPGRADE_FILE="$MIFP_HOME/upgrade.env"
DATA_DIR="$MIFP_HOME/data"
EVENTS_PRIVATE_DIR="${MIFP_EVENTS_PRIVATE_DIR:-/srv/mifp-events-private}"
EVENTS_PUBLIC_GROUP="${MIFP_EVENTS_PUBLIC_GROUP:-mifp-events-public}"
EVENTS_PHP_USER="${MIFP_EVENTS_PHP_USER:-mifp-events}"
EVENTS_PHP_STATE="${MIFP_EVENTS_PHP_STATE:-$MIFP_HOME/events-php-enabled.txt}"
EVENTS_PHP_INCLUDE="${MIFP_EVENTS_PHP_INCLUDE:-/etc/caddy/mifp-events-php.caddy}"
EVENTS_PHP_SOCKET="${MIFP_EVENTS_PHP_SOCKET:-/run/php/mifp-events.sock}"
MAIL_RELAY_CONFIG="${MIFP_MAIL_RELAY_CONFIG:-/etc/msmtprc}"
SYSTEMD_DIR="${MIFP_SYSTEMD_DIR:-/etc/systemd/system}"
PHP_FPM_SERVICE_FILE="${MIFP_PHP_FPM_SERVICE_FILE:-$MIFP_HOME/php-fpm.service}"
CADDY_CONFIG="${MIFP_CADDY_CONFIG:-/etc/caddy/Caddyfile}"
CADDY_TEMPLATE="$MIFP_HOME/Caddyfile.example"
LOCAL_HOSTS_HELPER="$MIFP_HOME/local-hosts.sh"
BACKUP_SCRIPT="$MIFP_HOME/backup.sh"
LOCK_FILE="${MIFP_DEPLOY_LOCK_FILE:-/run/lock/mifp-deploy.lock}"
SSHD_DROPIN="${MIFP_SSHD_DROPIN:-/etc/ssh/sshd_config.d/99-mifp-hardening.conf}"
SSHD_ROLLBACK="${MIFP_SSHD_ROLLBACK:-/root/.mifp-sshd-rollback.conf}"
RUNTIME_UID="${MIFP_RUNTIME_UID:-10001}"
RUNTIME_GID="${MIFP_RUNTIME_GID:-10001}"
MIFPCTL_VERSION="2026.09.25.1"
DEPLOY_CONTRACT_VERSION="2"
DEPLOY_CONTRACT_MIN_VERSION="1"
DEPLOY_CONTRACT_LABEL="org.mifp.deploy-contract"
SECURITY_ROOT_UID="${MIFP_SECURITY_ROOT_UID:-0}"
SECURITY_ROOT_GID="${MIFP_SECURITY_ROOT_GID:-0}"
COMPOSE_BASE=(docker compose --project-name mifp --project-directory "$MIFP_HOME" --env-file "$ENV_FILE" --env-file "$PUBLIC_CONFIG_FILE" -f "$COMPOSE_FILE")

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
MIFP production operator

Uso normale:
  sudo mifpctl configure [--section NAME] wizard progressivo (web/publisher/mail/backup)
  sudo mifpctl config-show                riepilogo senza mostrare segreti
  sudo mifpctl config-set KEY VALUE       aggiorna un valore non segreto
  sudo mifpctl config-unset KEY           rimuove un valore
  sudo mifpctl config-check               readiness read-only (exit 0/1)
  sudo mifpctl registry-check             verifica accesso al manifest GHCR
  sudo mifpctl init                       primo avvio da :latest, fissato subito a digest
  sudo mifpctl check                      preflight completo update; non cambia la release attiva
  sudo mifpctl update                     update sicuro con output operativo completo
  sudo mifpctl status
  sudo mifpctl version                    versione tool + deploy contract host

Compatibilità / diagnostica:
  sudo mifpctl update-check               controllo leggero legacy (registry only; non fa pull)
  sudo mifpctl deploy sha-<commit>        deploy esplicito; il DB non viene modificato
  sudo mifpctl logs
  sudo mifpctl rollback                   torna alla release precedente (anche offline se locale)
  sudo mifpctl backup                     snapshot point-in-time DB + dati privati
  sudo mifpctl events-republish-all       ricrea i siti evento dai ZIP sorgente conservati
  sudo mifpctl events-php-list            mostra i regform autorizzati a eseguire PHP
  sudo mifpctl events-php-enable PATH     abilita PHP per un PATH <evento>/regform sicuro
  sudo mifpctl events-php-disable PATH    revoca immediatamente PHP per quel regform
  sudo mifpctl doctor                     diagnostica completa
  sudo mifpctl security-check             audit read-only di superficie e permessi host
  sudo mifpctl ssh-harden --operator USER hardening opzionale: disabilita password SSH
  sudo mifpctl ssh-rollback               rimuove il drop-in SSH e ricarica sshd
  sudo mifpctl fix-permissions            corregge ownership dati solo su richiesta
Manutenzione rara:
  sudo mifpctl registry-login             login opzionale per package privati (PAT read:packages)
  sudo mifpctl first-deploy sha-<commit>  primo avvio compatibile con selector esplicito
  sudo mifpctl init-db sha-<commit>        crea solo il DB schema-only
  sudo mifpctl upgrade-db sha-<commit> /path/new.db
  sudo mifpctl restore-db /path/backup.db
  sudo mifpctl restore-snapshot /var/backups/mifp/snapshots/snapshot-...
  sudo mifpctl rollback-upgrade            ripristina la coppia image+DB precedente
  sudo mifpctl restart
  sudo mifpctl stop
  sudo mifpctl admin [--username NAME]
  sudo mifpctl admin-reset-password [--username NAME]

`:latest` è solo un canale di discovery: init, check, update-check e update possono
consultarlo, ma lo stato persistente contiene sempre un digest OCI immutabile.
`deploy` continua ad accettare soltanto sha-<commit> o @sha256:... espliciti.
EOF
}

[[ "$(id -u)" -eq 0 ]] || die "Esegui deploy.sh come root (sudo)."
[[ -f "$ENV_FILE" ]] || die "Manca $ENV_FILE. Esegui prima deploy/bootstrap-vps.sh."
[[ -f "$COMPOSE_FILE" ]] || die "Manca $COMPOSE_FILE. Riesegui bootstrap-vps.sh."
[[ -f "$CONFIG_HELPER" ]] || die "Manca $CONFIG_HELPER. Riesegui bootstrap-vps.sh con la cartella deploy aggiornata."
[[ -f "$VPS_CONFIG_HELPER" ]] || die "Manca $VPS_CONFIG_HELPER. Riesegui bootstrap-vps.sh con la cartella deploy aggiornata."

config_cli() {
  python3 "$VPS_CONFIG_HELPER" \
    --config-file "$PUBLIC_CONFIG_FILE" \
    --secrets-file "$SECRETS_FILE" \
    --runtime-env "$ENV_FILE" \
    --example "$ENV_EXAMPLE" \
    --docker-config "$DOCKER_CONFIG_FILE" "$@"
}

sync_backup_timer() {
  # BACKUP_ENABLED is the single source of truth for scheduled host snapshots.
  # Manual `mifpctl backup` remains available even when the timer is disabled.
  local enabled
  enabled="$(config_cli get BACKUP_ENABLED)"
  if [[ "$enabled" == "false" ]]; then
    systemctl disable --now mifp-backup.timer >/dev/null 2>&1 || true
    say "Backup timer: disabled by configuration"
    return 0
  fi
  if ! systemctl enable --now mifp-backup.timer; then
    say "ERROR: impossibile abilitare mifp-backup.timer" >&2
    return 1
  fi
  say "Backup timer: enabled"
}

sync_mail_relay() {
  local backend="$1" provider smtp_host state
  provider="$(config_cli get MAIL_PROVIDER)"
  smtp_host="$(config_cli get SMTP_HOST)"
  if [[ "$provider" != "disabled" && "$provider" != "console" && "$provider" != "smtp" && -n "$smtp_host" ]]; then
    provider="smtp"
  fi

  # The Flask container uses the canonical SMTP configuration directly. The
  # host relay is also the independent emergency path used by the systemd
  # health monitor, so downtime can be reported when the web container is dead.
  # Local-vps PHP regforms may share this relay without receiving credentials.
  if [[ "$provider" != "smtp" ]]; then
    rm -f -- "$MAIL_RELAY_CONFIG"
    say "Host mail relay: disabled (${provider:-disabled})"
    return 0
  fi

  has msmtp || die "msmtp non disponibile; riesegui bootstrap-vps.sh."
  if ! state="$(config_cli render-mail-relay --output "$MAIL_RELAY_CONFIG")"; then
    rm -f -- "$MAIL_RELAY_CONFIG"
    die "Configurazione SMTP incompleta o non valida; relay host disabilitato."
  fi
  [[ "$state" == "configured" && -f "$MAIL_RELAY_CONFIG" && ! -L "$MAIL_RELAY_CONFIG" ]] \
    || die "Impossibile configurare il relay SMTP sicuro host."
  if [[ "$backend" == "local-vps" ]]; then
    id "$EVENTS_PHP_USER" >/dev/null 2>&1 \
      || die "Utente PHP eventi mancante: $EVENTS_PHP_USER"
    chown root:"$EVENTS_PHP_USER" "$MAIL_RELAY_CONFIG"
    chmod 0640 "$MAIL_RELAY_CONFIG"
  else
    chown root:root "$MAIL_RELAY_CONFIG"
    chmod 0600 "$MAIL_RELAY_CONFIG"
  fi
  say "Host mail relay: configured (credentials not displayed)"
}

sync_alert_timer() {
  local provider smtp_host
  provider="$(config_cli get MAIL_PROVIDER)"
  smtp_host="$(config_cli get SMTP_HOST)"
  if [[ "$provider" != "disabled" && "$provider" != "console" && "$provider" != "smtp" && -n "$smtp_host" ]]; then
    provider="smtp"
  fi
  if [[ "$provider" != "smtp" ]]; then
    systemctl disable --now mifp-alert-check.timer >/dev/null 2>&1 || true
    say "Notification monitor: disabled by mail configuration"
    return 0
  fi
  [[ -f "$SYSTEMD_DIR/mifp-alert-check.timer" ]] \
    || { say "ERROR: manca mifp-alert-check.timer; aggiorna prima gli host tools." >&2; return 1; }
  systemctl enable --now mifp-alert-check.timer \
    || { say "ERROR: impossibile abilitare mifp-alert-check.timer" >&2; return 1; }
  say "Notification monitor: enabled"
}


sync_events_php_lifecycle() {
  local backend="$1" service
  service="$(events_php_service || true)"
  if [[ "$backend" != "local-vps" ]]; then
    if [[ -n "$service" ]]; then
      systemctl disable --now "$service" >/dev/null 2>&1 \
        || die "Impossibile disattivare il runtime PHP eventi locale."
    fi
    say "Event PHP-FPM: disattivato per backend $backend"
    return 0
  fi

  [[ -d "$EVENTS_PRIVATE_DIR/registrations" && -d "$EVENTS_PRIVATE_DIR/uploads" \
     && -f "$EVENTS_PHP_STATE" && -f "$EVENTS_PHP_INCLUDE" ]] \
    || die "Runtime PHP eventi locale incompleto; riesegui bootstrap-vps.sh prima di abilitare local-vps."
  [[ -n "$service" ]] \
    || die "Pool PHP-FPM eventi non configurato; riesegui bootstrap-vps.sh."
  systemctl enable --now "$service" \
    || die "Impossibile avviare il runtime PHP eventi locale."
  install_events_php_include
}

apply_host_configuration() {
  local domain www_domain tls_directive="" tmp events_backend events_url events_host events_root
  cleanup_host_config_tmp() { [[ -z "${tmp:-}" ]] || rm -f -- "$tmp"; }
  trap cleanup_host_config_tmp EXIT
  domain="$(config_cli get DOMAIN)"
  events_backend="$(config_cli get EVENTS_PUBLISH_BACKEND)"
  if [[ -z "$domain" ]]; then
    bash "$LOCAL_HOSTS_HELPER" --clear "${MIFP_HOSTS_FILE:-/etc/hosts}"
    tmp="$(mktemp "$(dirname "$CADDY_CONFIG")/.mifp-caddy.XXXXXX")"
    printf '%s\n' ':80 {' '    respond "MIFP host ready; run sudo mifpctl configure" 503' '}' >"$tmp"
  else
    www_domain="$(config_cli get WWW_DOMAIN)"
    [[ -n "$www_domain" ]] || die "Dominio www incompleto; esegui sudo mifpctl configure --section web."
    events_url="$(config_cli get EVENTS_PUBLIC_BASE_URL)"
    events_host="$(python3 -c 'import sys; from urllib.parse import urlsplit; print(urlsplit(sys.argv[1]).hostname or "")' "$events_url")"
    events_root="$(config_cli get EVENTS_LOCAL_ROOT)"
    [[ "$events_backend" != "local-vps" || -n "$events_host" ]] || die "EVENTS_PUBLIC_BASE_URL non valido."
    if [[ "$events_backend" == "local-vps" ]]; then
      install -d -o "$RUNTIME_UID" -g "$EVENTS_PUBLIC_GROUP" -m 0750 "$events_root"
      bash "$LOCAL_HOSTS_HELPER" "$domain" "${MIFP_HOSTS_FILE:-/etc/hosts}" "$www_domain" "$events_host"
    else
      bash "$LOCAL_HOSTS_HELPER" "$domain" "${MIFP_HOSTS_FILE:-/etc/hosts}" "$www_domain"
    fi
    [[ "$domain" != *.home.arpa ]] || tls_directive="tls internal"
    tmp="$(mktemp "$(dirname "$CADDY_CONFIG")/.mifp-caddy.XXXXXX")"
    sed -e "s/__MIFP_DOMAIN__/$domain/g" \
      -e "s/__MIFP_WWW_DOMAIN__/$www_domain/g" \
      -e "s/__MIFP_TLS__/$tls_directive/g" "$CADDY_TEMPLATE" >"$tmp"
    if [[ "$events_backend" == "local-vps" ]]; then
      cat >>"$tmp" <<EOF_EVENTS_CADDY

$events_host {
    $tls_directive
    root * $events_root
    encode zstd gzip
    header {
        -Server
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        Content-Security-Policy "frame-ancestors 'self'; object-src 'none'; base-uri 'self'"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
    }

    route {
        # Local publisher stage/rollback paths are dot-prefixed. Keep only the
        # ACME/verification namespace reachable below a dot path.
        @event_hidden {
            not path /.well-known /.well-known/*
            path_regexp event_hidden ^/(?:.*/)?\\.
        }
        @event_sensitive_tree path_regexp event_sensitive_tree (?i)^/(?:.*/)?(?:\\.env(?:\\..*)?|\\.git|\\.svn|private|registrations|config|regform/(?:registrations|src|config|settings\\.ya?ml|settings\\.json))(?:/|$)
        @event_sensitive_file path_regexp event_sensitive_file (?i)^/(?:.*/)?(?:composer\\.(?:json|lock)|id_(?:rsa|dsa|ecdsa|ed25519)(?:\\.pub)?|creds?\\.json|credentials?\\.json|secrets?\\.json|tokens?\\.json|git-credentials|netrc|npmrc|ftpconfig|pgpass|my\\.cnf|s3cfg|dockercfg|htpasswd|shadow|bash_history|zsh_history)$
        @event_database_backup path_regexp event_database_backup (?i)\\.(?:db|sqlite[0-9]*(?:-wal|-shm)?|sql|bak|backup|old|orig|save|swp)$
        respond @event_hidden 404
        respond @event_sensitive_tree 404
        respond @event_sensitive_file 404
        respond @event_database_backup 404

        # Conference Editor sites require this one public YAML document.
        @event_public_conference_yaml path_regexp event_public_conference_yaml ^/(?:[^/]+/)*conference\\.yaml$
        file_server @event_public_conference_yaml

        @event_sensitive_extension path_regexp event_sensitive_extension (?i)\\.(?:inc|module|install|engine|ini|log|htpasswd|htaccess|sh|bash|zsh|fish|py|pyc|rb|pl|cgi|yml|yaml|toml|dist|pem|key|crt|cer|p12|pfx|gitignore|netrc|npmrc|ftpconfig)$
        @event_php path_regexp event_php (?i)\\.(?:php|phtml|phar|phps|php[0-9]*)$
        respond @event_sensitive_extension 404
        import $EVENTS_PHP_INCLUDE
        respond @event_php 404
        file_server {
            index index.html index.htm
        }
    }
}
EOF_EVENTS_CADDY
    fi
  fi
  chown root:caddy "$tmp"; chmod 0644 "$tmp"
  caddy fmt --overwrite "$tmp" >/dev/null
  caddy validate --config "$tmp" --adapter caddyfile >/dev/null
  mv -f "$tmp" "$CADDY_CONFIG"
  tmp=""
  trap - EXIT
  systemctl reload caddy.service 2>/dev/null || systemctl restart caddy.service
  sync_mail_relay "$events_backend"
  sync_alert_timer || die "Configurazione mail salvata, ma il monitor notifiche non è stato applicato."
  sync_events_php_lifecycle "$events_backend"
  if [[ "$domain" == *.home.arpa ]]; then
    caddy trust --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
      || say "WARN: CA locale Caddy non installata nel trust store della VPS."
    local lan_ip
    lan_ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\./ && $0 !~ /^127\./ {print}' | sort -u | awk 'NR==1 {value=$0} NR==2 {value=""} END {print value}' || true)"
    if [[ -n "$lan_ip" ]]; then
      say "Workstation /etc/hosts: $lan_ip $domain $www_domain"
    else
      say "Workstation /etc/hosts: individua l'IP LAN con 'hostname -I', poi mappa $domain $www_domain"
    fi
  fi
}

read_release_value() {
  local key="$1"
  [[ -f "$RELEASE_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$RELEASE_FILE" | tail -n 1
}
current_image() { read_release_value CURRENT_IMAGE; }
previous_image() { read_release_value PREVIOUS_IMAGE; }

read_upgrade_value() {
  local key="$1"
  [[ -f "$UPGRADE_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$UPGRADE_FILE" | tail -n 1
}

write_upgrade_checkpoint() {
  local upgraded_image="$1" previous_image="$2" previous_db="$3" tmp
  tmp="$(mktemp "$MIFP_HOME/.upgrade.env.XXXXXX")"
  {
    printf 'UPGRADED_IMAGE=%s\n' "$upgraded_image"
    printf 'PREVIOUS_IMAGE=%s\n' "$previous_image"
    printf 'PREVIOUS_DB=%s\n' "$previous_db"
  } >"$tmp"
  chmod 0600 "$tmp"; chown root:root "$tmp"; mv -f "$tmp" "$UPGRADE_FILE"
}

write_release_state() {
  local current="$1" previous="${2:-}" tmp
  tmp="$(mktemp "$MIFP_HOME/.release.env.XXXXXX")"
  { printf 'CURRENT_IMAGE=%s\n' "$current"; printf 'PREVIOUS_IMAGE=%s\n' "$previous"; } >"$tmp"
  chmod 0600 "$tmp"; chown root:root "$tmp"; mv -f "$tmp" "$RELEASE_FILE"
}

env_value() {
  local key="$1" line value
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  if [[ ${#value} -ge 2 ]] && { [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]] || [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; }; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

validate_production_env() {
  config_cli validate || die "Configurazione non valida. Esegui: sudo mifpctl configure"
  chmod 0600 "$ENV_FILE"; chown root:root "$ENV_FILE"
}

image_repository() {
  local repo
  repo="$(env_value MIFP_IMAGE_REPOSITORY || true)"
  [[ "$repo" =~ ^ghcr\.io/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "MIFP_IMAGE_REPOSITORY non valido in $ENV_FILE."
  printf '%s' "$repo"
}

resolve_image() {
  local reference="$1"
  if [[ "$reference" == sha-* ]]; then printf '%s:%s\n' "$(image_repository)" "$reference"; else printf '%s\n' "$reference"; fi
}

validate_release_image() {
  local image="$1" repo pattern
  repo="$(image_repository)"
  pattern="^${repo//./\\.}(@sha256:[0-9a-f]{64}|:sha-[A-Za-z0-9][A-Za-z0-9._-]*)$"
  [[ "$image" =~ $pattern ]] \
    || die "Usa sha-<commit> o un digest OCI @sha256:... valido; tag mutabili come :latest non sono ammessi."
}

prepare_compose_secrets() {
  # /etc/mifp/secrets.env remains the canonical root-only store. Compose cannot
  # use environment-backed secrets with a read-only service root filesystem, so
  # materialize derived root-only regular files immediately before every Compose
  # operation. Optional SMTP/publisher credentials intentionally become empty
  # regular files, keeping the *_FILE contract stable without exposing values in
  # the container environment or host process environment.
  config_cli materialize-secrets --output-dir "$SECRET_MATERIAL_DIR" \
    --uid "$RUNTIME_UID" --gid "$RUNTIME_GID" --mode 0400 \
    >/dev/null || die "Impossibile materializzare i Docker secrets in $SECRET_MATERIAL_DIR."
}

compose_with_image() {
  local image="$1"; shift
  prepare_compose_secrets
  MIFP_IMAGE="$image" MIFP_DATA_DIR="$DATA_DIR" MIFP_SECRETS_DIR="$SECRET_MATERIAL_DIR" \
    MIFP_DEPLOY_CONTRACT="$DEPLOY_CONTRACT_VERSION" \
    "${COMPOSE_BASE[@]}" "$@"
}

ensure_tools() {
  local tool
  for tool in docker curl sqlite3 python3 systemctl flock df; do has "$tool" || die "Comando richiesto non disponibile: $tool"; done
  docker info >/dev/null 2>&1 || die "Docker daemon non raggiungibile."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 non disponibile."
}

check_free_space() {
  local min_mb available_kb available_mb target
  min_mb="$(env_value MIFP_DEPLOY_MIN_FREE_MB || true)"; min_mb="${min_mb:-2048}"
  [[ "$min_mb" =~ ^[0-9]+$ ]] || die "MIFP_DEPLOY_MIN_FREE_MB non valido: $min_mb"
  target="$DATA_DIR"; [[ -d /var/lib/docker ]] && target=/var/lib/docker
  available_kb="$(df -Pk "$target" | awk 'NR==2 {print $4}')"
  available_mb=$((available_kb / 1024))
  (( available_mb >= min_mb )) || die "Spazio insufficiente prima del deploy: ${available_mb} MB liberi, richiesti almeno ${min_mb} MB."
}

validate_database_host() {
  local db="$DATA_DIR/mifp.db" output
  [[ -f "$db" && ! -L "$db" ]] || die "Manca un database regolare: $db"
  output="$(sqlite3 -readonly "$db" 'PRAGMA quick_check; PRAGMA foreign_key_check;' 2>&1)" || die "SQLite non riesce a leggere $db: $output"
  [[ "$output" == "ok" ]] || die "Controlli SQLite falliti per $db: $output"
}

prepare_runtime_storage() {
  local dirs=(assets backups conferences exports logs config tmp) name bad
  install -d -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0750 "$DATA_DIR"
  for name in "${dirs[@]}"; do install -d -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0750 "$DATA_DIR/$name"; done
  bad="$(find "$DATA_DIR" -xdev \( ! -uid "$RUNTIME_UID" -o ! -gid "$RUNTIME_GID" \) -print -quit)"
  [[ -z "$bad" ]] || die "Ownership dati non valida: $bad. Correggi esplicitamente con: sudo mifpctl fix-permissions"
}

do_fix_permissions() {
  validate_production_env; ensure_tools
  step "Correggo ownership dati -> ${RUNTIME_UID}:${RUNTIME_GID}"
  chown -R --no-dereference "$RUNTIME_UID:$RUNTIME_GID" "$DATA_DIR"
  find "$DATA_DIR" -xdev -type d -exec chmod 0750 {} +
  [[ ! -f "$DATA_DIR/mifp.db" ]] || chmod 0640 "$DATA_DIR/mifp.db"
  say "Permessi dati corretti."
}

registry_auth_error() {
  die $'GHCR authentication required.\n\nRun:\n  sudo mifpctl registry-login\n\nThen retry the previous command.'
}

latest_available_image() {
  local repo latest anonymous_config inspect_output digest
  repo="$(image_repository)"
  latest="$repo:latest"
  docker buildx version >/dev/null 2>&1 || die "Docker Buildx non disponibile: impossibile risolvere il digest remoto di $latest"
  anonymous_config="$(mktemp -d)"
  printf '{"auths":{}}\n' >"$anonymous_config/config.json"

  if inspect_output="$(DOCKER_CONFIG="$anonymous_config" docker buildx imagetools inspect "$latest" --format '{{json .Manifest}}' 2>&1)"; then
    rm -rf -- "$anonymous_config"
  else
    rm -rf -- "$anonymous_config"
    if grep -Eqi 'denied|unauthorized|authentication required|status code: 40[13]|status: 40[13]' <<<"$inspect_output"; then
      if ! inspect_output="$(docker buildx imagetools inspect "$latest" --format '{{json .Manifest}}' 2>&1)"; then
        if grep -Eqi 'denied|unauthorized|authentication required|status code: 40[13]|status: 40[13]' <<<"$inspect_output"; then
          registry_auth_error
        fi
        die "Impossibile ispezionare il canale di aggiornamento: $latest"
      fi
    else
      [[ -z "$inspect_output" ]] || printf '%s\n' "$inspect_output" >&2
      die "Impossibile ispezionare il canale di aggiornamento: $latest"
    fi
  fi

  digest="$(python3 -c 'import json,sys; value=json.load(sys.stdin).get("digest", ""); print(value if isinstance(value, str) else "")' <<<"$inspect_output" 2>/dev/null || true)"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "Il registry non ha restituito un digest OCI valido per $latest"
  printf '%s@%s\n' "$repo" "$digest"
}

show_update_state() {
  local repo="$1" current="$2" candidate="$3" available="$4"
  say "Repository:       $repo"
  say "Current release:  $current"
  say "Latest available: $candidate"
  say "Update available: $available"
}

short_image() {
  local image="$1"
  if [[ "$image" =~ @sha256:([0-9a-f]{12})[0-9a-f]*$ ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  else
    printf '%s' "$image"
  fi
}

phase() {
  local index="$1" total="$2" label="$3"
  printf '[%s/%s] %s\n' "$index" "$total" "$label"
}

phase_ok() { printf '      OK%s\n' "${1:+ · $1}"; }
phase_note() { printf '      %s\n' "$1"; }

report_check_blocked() {
  local class="$1" reason="$2"
  say ""
  say "BLOCKED"
  say "Failure class: $class"
  say "Reason:"
  say "  $reason"
  say ""
  say "Production state: UNCHANGED"
  return 1
}

report_update_failure() {
  local class="$1" reason="$2" state="$3"
  say ""
  say "UPDATE FAILED"
  say "Failure class: $class"
  say "Reason:"
  say "  $reason"
  say ""
  say "Production state: $state"
  return 1
}

compose_failure_is_host_configuration() {
  local output="${1:-}"
  # `docker compose up` can fail before the application ever starts because of
  # host/Compose problems. Retrying a previous image with the same broken host
  # configuration is pointless and, worse, can obscure the actual failure.
  # Keep the patterns deliberately narrow: unknown failures still use the
  # existing image/startup rollback path.
  grep -Eqi \
    'cannot create secret|sole supported option|invalid mount config|bind source path does not exist|error while creating mount source path|read-only file system|permission denied|port is already allocated|failed to create network|invalid compose project|required variable .* is missing a value' \
    <<<"$output"
}

image_deploy_contract() {
  local image="$1" value
  value="$(docker image inspect --format '{{ index .Config.Labels "org.mifp.deploy-contract" }}' "$image" 2>/dev/null || true)"
  # Images created before the explicit contract existed are legacy contract v1.
  [[ -n "$value" && "$value" != '<no value>' ]] || value="1"
  printf '%s' "$value"
}

validate_image_deploy_contract() {
  local image="$1" contract
  contract="$(image_deploy_contract "$image")"
  [[ "$contract" =~ ^[0-9]+$ ]] \
    || die "Deploy contract immagine non valido per $image: $contract"
  (( contract >= DEPLOY_CONTRACT_MIN_VERSION && contract <= DEPLOY_CONTRACT_VERSION )) \
    || die "Deploy contract incompatibile: host v$DEPLOY_CONTRACT_VERSION supporta v$DEPLOY_CONTRACT_MIN_VERSION..v$DEPLOY_CONTRACT_VERSION, immagine richiede v$contract. Aggiorna prima i tool host con refresh-host-tools.sh. Produzione invariata."
  printf '%s' "$contract"
}

validate_candidate_compose() {
  local image="$1"
  compose_with_image "$image" config -q >/dev/null \
    || die "Compose preflight fallito per $image. Produzione invariata."
}

current_release_preflight() {
  local current="$1"
  validate_release_image "$current"
  docker image inspect "$current" >/dev/null 2>&1 \
    || die "L'immagine della release corrente non è disponibile localmente: $current"
}

do_check() {
  local repo current candidate image contract total=7
  say "MIFP update preflight"
  say ""

  phase 1 "$total" "Configuration"
  if ! config_cli validate >/dev/null || ! (ensure_tools) >/dev/null 2>&1; then
    phase_note "FAILED"
    report_check_blocked "HOST/CONFIGURATION" "Host configuration or required deployment tools are not ready. Run: sudo mifpctl config-check"
    return 1
  fi
  phase_ok

  phase 2 "$total" "Current release"
  current="$(current_image || true)"
  if [[ -z "$current" ]] || ! (current_release_preflight "$current"); then
    phase_note "FAILED"
    report_check_blocked "HOST/CONFIGURATION" "The current release state is missing, invalid, or its rollback image is not local."
    return 1
  fi
  phase_ok "$(short_image "$current")"

  phase 3 "$total" "Resolve update channel"
  repo="$(image_repository)"
  if ! candidate="$(latest_available_image)"; then
    phase_note "FAILED"
    report_check_blocked "NETWORK/REGISTRY" "The update channel could not be resolved. Check network and registry access."
    return 1
  fi
  if [[ "$candidate" == "$current" ]]; then
    phase_note "UP TO DATE"
    say ""
    say "Current:          $(short_image "$current")"
    say "Host contract:    v$DEPLOY_CONTRACT_VERSION"
    say "Production state: UNCHANGED"
    say "System is already up to date."
    return 0
  fi
  phase_note "AVAILABLE · $(short_image "$candidate")"

  phase 4 "$total" "Candidate image"
  if ! image="$(pull_and_pin "$candidate" 0 1)" || [[ "$image" != "$candidate" ]]; then
    phase_note "FAILED"
    report_check_blocked "NETWORK/REGISTRY" "The pinned candidate image could not be pulled and verified."
    return 1
  fi
  phase_ok "cached locally"

  phase 5 "$total" "Deploy contract"
  if ! contract="$(validate_image_deploy_contract "$image")"; then
    phase_note "FAILED"
    report_check_blocked "HOST/CONFIGURATION" "Host deploy contract v$DEPLOY_CONTRACT_VERSION cannot run this candidate. Copy the updated deploy/ bundle and run: sudo bash /tmp/mifp-deploy/refresh-host-tools.sh"
    return 1
  fi
  phase_ok "host v$DEPLOY_CONTRACT_VERSION / image v$contract"

  phase 6 "$total" "Database compatibility"
  if ! (validate_database_host && preflight_image_db "$image"); then
    phase_note "FAILED"
    report_check_blocked "DATABASE" "The candidate is not compatible with the current production database."
    return 1
  fi
  phase_ok

  phase 7 "$total" "Compose + secrets"
  if ! (validate_candidate_compose "$image"); then
    phase_note "FAILED"
    report_check_blocked "HOST/CONFIGURATION" "Compose validation or file-backed secret preparation failed. Automatic image rollback was not attempted because production was not changed."
    return 1
  fi
  phase_ok

  say ""
  say "READY TO UPDATE"
  say "Current:          $(short_image "$current")"
  say "Target:           $(short_image "$candidate")"
  say "Production state: UNCHANGED"
  say "Run: sudo mifpctl update"
}

do_update_check() {
  local repo current candidate
  config_cli validate || die "Configurazione non valida. Esegui: sudo mifpctl configure"
  ensure_tools
  repo="$(image_repository)"
  current="$(current_image || true)"
  [[ -n "$current" ]] || die "Nessuna release corrente registrata. Usa prima: sudo mifpctl init"
  validate_release_image "$current"
  candidate="$(latest_available_image)"
  if [[ "$candidate" == "$current" ]]; then
    show_update_state "$repo" "$current" "$candidate" "NO"
    say "System is already up to date."
  else
    show_update_state "$repo" "$current" "$candidate" "YES"
  fi
}

do_update() {
  local repo current candidate image contract old_previous total=9
  say "MIFP safe update"
  say ""

  phase 1 "$total" "Configuration"
  if ! (validate_production_env && ensure_tools); then
    phase_note "FAILED"
    report_update_failure "HOST/CONFIGURATION" "Host configuration or required deployment tools are not ready. Run: sudo mifpctl config-check" "UNCHANGED"
    return 1
  fi
  phase_ok

  phase 2 "$total" "Current release"
  current="$(current_image || true)"
  if [[ -z "$current" ]] || ! (current_release_preflight "$current"); then
    phase_note "FAILED"
    report_update_failure "HOST/CONFIGURATION" "The current release state is missing, invalid, or its rollback image is not local." "UNCHANGED"
    return 1
  fi
  old_previous="$(previous_image || true)"
  phase_ok "$(short_image "$current")"

  phase 3 "$total" "Resolve update channel"
  repo="$(image_repository)"
  if ! candidate="$(latest_available_image)"; then
    phase_note "FAILED"
    report_update_failure "NETWORK/REGISTRY" "The update channel could not be resolved. Check network and registry access." "UNCHANGED"
    return 1
  fi
  if [[ "$candidate" == "$current" ]]; then
    phase_note "UP TO DATE"
    say "      Current release:  $current"
    say "      Latest available: $candidate"
    say "      Update available: NO"
    say ""
    say "Production state: UNCHANGED"
    say "Already up to date."
    return 0
  fi
  phase_note "AVAILABLE · $(short_image "$candidate")"
  say "      Current release:  $current"
  say "      Latest available: $candidate"
  say "      Update available: YES"

  phase 4 "$total" "Candidate image"
  if ! image="$(pull_and_pin "$candidate" 0 1)" || [[ "$image" != "$candidate" ]]; then
    phase_note "FAILED"
    report_update_failure "NETWORK/REGISTRY" "The pinned candidate image could not be pulled and verified." "UNCHANGED"
    return 1
  fi
  phase_ok "$(short_image "$image")"

  phase 5 "$total" "Deploy contract"
  if ! contract="$(validate_image_deploy_contract "$image")"; then
    phase_note "FAILED"
    report_update_failure "HOST/CONFIGURATION" "Host deploy contract v$DEPLOY_CONTRACT_VERSION cannot run this candidate. Copy the updated deploy/ bundle and run: sudo bash /tmp/mifp-deploy/refresh-host-tools.sh" "UNCHANGED"
    return 1
  fi
  phase_ok "host v$DEPLOY_CONTRACT_VERSION / image v$contract"

  phase 6 "$total" "Database compatibility"
  if ! (prepare_runtime_storage && validate_database_host && preflight_image_db "$image"); then
    phase_note "FAILED"
    report_update_failure "DATABASE" "The candidate is not compatible with the current production database." "UNCHANGED"
    return 1
  fi
  phase_ok

  phase 7 "$total" "Compose + secrets"
  if ! (validate_candidate_compose "$image"); then
    phase_note "FAILED"
    report_update_failure "HOST/CONFIGURATION" "Compose validation or file-backed secret preparation failed. Automatic image rollback was not attempted because the failure is in host deployment configuration." "UNCHANGED"
    return 1
  fi
  phase_ok

  phase 8 "$total" "Start candidate"
  local start_output start_log
  start_log="$(mktemp)"
  if compose_with_image "$image" up -d --remove-orphans 2>&1 | tee "$start_log"; then
    rm -f -- "$start_log"
    phase_ok
  else
    start_output="$(cat "$start_log")"
    rm -f -- "$start_log"
    phase_note "FAILED"
    show_release_diagnostic "$image"
    if compose_failure_is_host_configuration "$start_output"; then
      report_update_failure \
        "HOST/CONFIGURATION" \
        "Docker Compose could not create/start the candidate because of host deployment configuration. Automatic image rollback was not attempted because it would reuse the same host configuration." \
        "MANUAL ATTENTION REQUIRED"
      return 1
    fi
    step "Automatic rollback -> $current"
    if compose_with_image "$current" up -d --remove-orphans >/dev/null 2>&1 && wait_ready 30; then
      report_update_failure "IMAGE/STARTUP" "The candidate could not be started; the previous release was restored." "ROLLED BACK SUCCESSFULLY"
      return 1
    fi
    show_release_diagnostic "$current"
    report_update_failure "IMAGE/STARTUP" "The candidate failed to start and the previous release could not be restored automatically." "MANUAL ATTENTION REQUIRED"
    return 1
  fi

  phase 9 "$total" "Application readiness"
  if wait_ready 60; then
    phase_ok "healthy"
  else
    phase_note "FAILED"
    show_release_diagnostic "$image"
    step "Automatic rollback -> $current"
    if compose_with_image "$current" up -d --remove-orphans >/dev/null 2>&1 && wait_ready 30; then
      report_update_failure "READINESS" "The candidate did not become ready; the previous release was restored." "ROLLED BACK SUCCESSFULLY"
      return 1
    fi
    show_release_diagnostic "$current"
    report_update_failure "READINESS" "The candidate did not become ready and the previous release could not be restored automatically." "MANUAL ATTENTION REQUIRED"
    return 1
  fi

  if [[ "$image" == "$current" ]]; then
    write_release_state "$image" "$old_previous"
  else
    write_release_state "$image" "$current"
  fi
  cleanup_old_release_images "$image" "$(previous_image || true)"

  say ""
  say "UPDATE COMPLETED"
  say "Current:          $(short_image "$image")"
  say "Previous:         $(short_image "$current")"
  say "Production state: UPDATED"
  say "Rollback:         sudo mifpctl rollback"
}

pull_and_pin() {
  local reference="$1" allow_latest="${2:-0}" quiet="${3:-0}" image repo digest pull_output
  image="$(resolve_image "$reference")"
  repo="$(image_repository)"
  if [[ "$allow_latest" == 1 && "$image" == "$repo:latest" ]]; then
    :
  else
    validate_release_image "$image"
  fi
  check_free_space
  if [[ "$quiet" == 1 ]]; then
    if pull_output="$(docker pull --quiet "$image" 2>&1)"; then
      :
    else
      [[ -z "$pull_output" ]] || printf '%s\n' "$pull_output" >&2
      if grep -Eqi 'denied|unauthorized|authentication required' <<<"$pull_output"; then
        registry_auth_error
      fi
      die "Pull immagine fallito: $image"
    fi
  elif pull_output="$(docker pull "$image" 2>&1)"; then
    [[ -z "$pull_output" ]] || printf '%s\n' "$pull_output" >&2
  else
    [[ -z "$pull_output" ]] || printf '%s\n' "$pull_output" >&2
    if grep -Eqi 'denied|unauthorized|authentication required' <<<"$pull_output"; then
      registry_auth_error
    fi
    die "Pull immagine fallito: $image"
  fi
  digest="$(
    docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image" 2>/dev/null \
      | awk -v prefix="$repo@sha256:" 'index($0,prefix)==1 {print; exit}'
  )"
  [[ "$digest" =~ ^${repo//./\.}@sha256:[0-9a-f]{64}$ ]] \
    || die "Impossibile risolvere il digest OCI immutabile di $image per $repo"
  printf '%s\n' "$digest"
}

ensure_image_local() {
  local image="$1"
  if docker image inspect "$image" >/dev/null 2>&1; then return 0; fi
  local pull_output
  if ! pull_output="$(docker pull "$image" 2>&1)"; then
    [[ -z "$pull_output" ]] || printf '%s\n' "$pull_output" >&2
    if grep -Eqi 'denied|unauthorized|authentication required' <<<"$pull_output"; then
      registry_auth_error
    fi
    die "Immagine non disponibile localmente e pull fallito: $image"
  fi
  [[ -z "$pull_output" ]] || printf '%s\n' "$pull_output" >&2
}

cleanup_old_release_images() {
  local current="$1" previous="${2:-}" repo current_id previous_id id
  repo="$(image_repository)"
  current_id="$(docker image inspect --format '{{.Id}}' "$current" 2>/dev/null || true)"
  previous_id=""
  [[ -n "$previous" ]] && previous_id="$(docker image inspect --format '{{.Id}}' "$previous" 2>/dev/null || true)"
  while read -r id; do
    [[ -n "$id" ]] || continue
    [[ "$id" == "$current_id" || "$id" == "$previous_id" ]] && continue
    docker image rm "$id" >/dev/null 2>&1 || true
  done < <(docker image ls "$repo" --format '{{.ID}}' 2>/dev/null | sort -u)
}

sqlite_snapshot_temp() {
  local source="$1" snapshot
  [[ -f "$source" && ! -L "$source" ]] || die "DB candidato non valido: $source"
  # The preflight file is bind-mounted into the non-root runtime container.
  # Never change data/tmp back to root ownership here: doing so makes TMPDIR
  # unwritable to UID 10001 after an otherwise successful deploy.
  install -d -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0750 "$DATA_DIR/tmp"
  snapshot="$(mktemp "$DATA_DIR/tmp/preflight-XXXXXXXX.db")"
  if ! sqlite3 -readonly "$source" ".backup '$snapshot'"; then
    rm -f -- "$snapshot" "$snapshot-wal" "$snapshot-shm"
    die "Impossibile creare snapshot SQLite coerente da $source"
  fi
  [[ "$(sqlite3 -readonly "$snapshot" 'PRAGMA quick_check; PRAGMA foreign_key_check;')" == "ok" ]] || {
    rm -f -- "$snapshot" "$snapshot-wal" "$snapshot-shm"
    die "Snapshot SQLite di preflight non valida: $source"
  }
  # SQLite may create WAL shared-memory sidecars while checking a copied DB.
  # They are transient preflight artifacts and must never survive under the
  # runtime-owned data tree as root-owned files.
  rm -f -- "$snapshot-wal" "$snapshot-shm"
  chown "$RUNTIME_UID:$RUNTIME_GID" "$snapshot"
  chmod 0440 "$snapshot"
  printf '%s\n' "$snapshot"
}

preflight_image_db() {
  local image="$1" db="${2:-$DATA_DIR/mifp.db}" snapshot
  snapshot="$(sqlite_snapshot_temp "$db")"
  if docker run --rm --network none --entrypoint python \
    -e DATABASE_PATH=/app/data/mifp.db \
    -v "$snapshot:/app/data/mifp.db:ro" \
    "$image" -m mifp_app.db.runtime_check >/dev/null; then
    rm -f -- "$snapshot" "$snapshot-wal" "$snapshot-shm"
    return 0
  fi
  rm -f -- "$snapshot" "$snapshot-wal" "$snapshot-shm"
  die "La release $image non è compatibile con il database. Nessuno switch eseguito."
}

wait_ready() {
  local attempts="${MIFP_READY_ATTEMPTS:-${1:-60}}" i
  [[ "$attempts" =~ ^[1-9][0-9]*$ ]] || die "MIFP_READY_ATTEMPTS non valido: $attempts"
  say "Attendo readiness applicativa (max $((attempts * 2))s)..." >&2
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS --max-time 2 http://127.0.0.1:8000/ready >/dev/null 2>&1; then
      say "Application readiness: OK (tentativo $i/$attempts)" >&2
      return 0
    fi
    if (( i == 1 || i % 5 == 0 )); then
      say "Readiness in attesa: tentativo $i/$attempts" >&2
    fi
    sleep 2
  done
  say "Application readiness: TIMEOUT dopo $attempts tentativi" >&2
  return 1
}

show_release_logs() { local image="$1"; compose_with_image "$image" ps >&2 || true; compose_with_image "$image" logs --tail 300 web >&2 || true; }

show_release_diagnostic() {
  local image="$1" cid state status exit_code docker_error
  cid="$(compose_with_image "$image" ps -a -q web 2>/dev/null || true)"
  if [[ -z "$cid" ]]; then
    say "Container: not created"
    say "Recent application error: unavailable; run sudo mifpctl logs if a container exists."
    return 0
  fi
  state="$(docker inspect --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.Error}}' "$cid" 2>/dev/null || true)"
  IFS='|' read -r status exit_code docker_error <<<"$state"
  say "Container: ${status:-unknown}"
  say "Exit code: ${exit_code:-unknown}"
  if [[ -n "$docker_error" ]]; then
    say "Docker error: ${docker_error:0:300}"
  else
    say "Recent application error: not exposed automatically; use sudo mifpctl logs for reviewed diagnostics."
  fi
}
service_running() { local image="$1"; [[ -n "$(compose_with_image "$image" ps --status running -q web 2>/dev/null || true)" ]]; }

stop_release_for_db_swap() {
  local image="$1"
  # A DB replacement must never continue while the old process may still be
  # writing. Failing closed here is safer than installing a file under a live
  # SQLite/WAL process.
  compose_with_image "$image" down \
    || die "Impossibile fermare MIFP: database invariato, operazione annullata."
}

activate_release() {
  local image="$1" fallback="${2:-}"
  if ! compose_with_image "$image" up -d --remove-orphans; then
    show_release_logs "$image"
    die "Compose non ha avviato la release $image. Errore host/Compose: rollback immagine automatico non tentato perché usa la stessa configurazione host."
  fi
  wait_ready 60 && return 0
  show_release_logs "$image"
  if [[ -n "$fallback" ]]; then
    step "Release non pronta: ripristino $fallback"
    if ! compose_with_image "$fallback" up -d --remove-orphans >/dev/null 2>&1; then
      show_release_logs "$fallback"
      die "La nuova release non è partita e anche il ripristino automatico di $fallback è fallito."
    fi
    if ! wait_ready 30; then
      show_release_logs "$fallback"
      die "La nuova release non è pronta e la release precedente $fallback non è tornata ready."
    fi
  fi
  return 1
}

init_db_with_image() {
  local image="$1" db="$DATA_DIR/mifp.db" work candidate
  [[ ! -e "$db" ]] || die "$db esiste già: init-db non sovrascrive mai."
  work="$(mktemp -d "$DATA_DIR/tmp/initdb-XXXXXXXX")"
  chown "$RUNTIME_UID:$RUNTIME_GID" "$work"; chmod 0750 "$work"
  candidate="$work/mifp.db"
  step "Creo database schema-only con $image"
  if ! docker run --rm --network none --entrypoint python \
    -v "$work:/work" "$image" -m mifp_app.db.manage init /work/mifp.db; then
    rm -rf -- "$work"
    die "Creazione DB iniziale fallita."
  fi
  preflight_image_db "$image" "$candidate"
  install -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0640 "$candidate" "$db"
  rm -rf -- "$work"
  say "DB iniziale creato: $db"
}

do_registry_login() {
  local username token="" stty_state=""
  has docker || die "Docker non disponibile. Riesegui bootstrap-vps.sh."
  [[ -t 0 ]] || die "registry-login richiede un terminale interattivo."
  printf 'GitHub username: ' >&2
  IFS= read -r username
  [[ "$username" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}$ ]] \
    || die "GitHub username non valido."

  # Disable terminal echo *before* printing the PAT prompt. `read -s` toggles
  # echo only after it starts reading, leaving a small race in PTY/CI tests
  # (and for a very fast paste) where the token can be echoed.
  stty_state="$(stty -g)" || die "Impossibile leggere lo stato del terminale."
  stty -echo || die "Impossibile disabilitare l'echo del terminale."
  trap 'stty "$stty_state" >/dev/null 2>&1 || true' INT TERM
  printf 'GitHub PAT classic (scope read:packages): ' >&2
  if ! IFS= read -r token; then
    stty "$stty_state" || true
    printf '\n' >&2
    die "Lettura del token GHCR interrotta."
  fi
  stty "$stty_state" || die "Impossibile ripristinare l'echo del terminale."
  trap - INT TERM
  printf '\n' >&2
  [[ -n "$token" ]] || die "Token vuoto."
  if printf '%s\n' "$token" | docker login ghcr.io --username "$username" --password-stdin; then
    token=""; unset token
    if [[ -f "$DOCKER_CONFIG_FILE" && ! -L "$DOCKER_CONFIG_FILE" ]]; then
      chown root:root "$DOCKER_CONFIG_FILE"
      chmod 0600 "$DOCKER_CONFIG_FILE"
    fi
    say "GHCR login successful."
    return 0
  fi
  token=""; unset token
  die "GHCR login failed. Verify username, PAT classic and read:packages scope."
}

do_registry_check() {
  local repository anonymous_config anonymous_output authenticated_output
  has docker || die "Docker non disponibile. Riesegui bootstrap-vps.sh."
  docker info >/dev/null 2>&1 || die "Docker daemon non raggiungibile."
  repository="$(image_repository)"
  anonymous_config="$(mktemp -d)"
  if anonymous_output="$(DOCKER_CONFIG="$anonymous_config" docker manifest inspect "$repository:latest" 2>&1)"; then
    rm -rf -- "$anonymous_config"
    say "Registry: OK ($repository:latest leggibile anonimamente; nessuna release modificata)"
    return 0
  fi
  if grep -Eqi 'denied|unauthorized|authentication required|status code: 40[13]|status: 40[13]' <<<"$anonymous_output"; then
    if authenticated_output="$(docker manifest inspect "$repository:latest" 2>&1)"; then
      rm -rf -- "$anonymous_config"
      say "Registry: OK ($repository:latest leggibile con credenziali Docker; nessuna release modificata)"
      return 0
    fi
    rm -rf -- "$anonymous_config"
    [[ -z "$authenticated_output" ]] || printf '%s\n' "$authenticated_output" >&2
    registry_auth_error
  fi
  rm -rf -- "$anonymous_config"
  [[ -z "$anonymous_output" ]] || printf '%s\n' "$anonymous_output" >&2
  die "GHCR non accessibile o manifest :latest assente: $repository:latest"
}

do_config_check() {
  config_cli check || return 1
  ensure_tools
  systemctl is-active caddy.service >/dev/null 2>&1 || die "Caddy non è attivo."
  caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
    || die "Caddyfile non valido."
  # Docker publishes ports through its own iptables rules, so UFW would not
  # contain a bad mapping: every published port must be loopback-bound.
  local compose_json published service host_ip host_port target_port protocol mapping_count=0
  if ! compose_json="$(compose_with_image "$(image_repository):latest" config --format json)"; then
    die "Impossibile normalizzare $COMPOSE_FILE con docker compose config."
  fi
  if ! published="$(python3 -c '
import json
import sys

try:
    document = json.load(sys.stdin)
    services = document["services"]
    if not isinstance(services, dict):
        raise TypeError("services is not an object")
    for service_name, service in services.items():
        if not isinstance(service, dict):
            raise TypeError(f"service {service_name} is not an object")
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            raise TypeError(f"ports for {service_name} is not a list")
        for port in ports:
            if not isinstance(port, dict):
                raise TypeError(f"port for {service_name} is not normalized")
            fields = (
                service_name,
                str(port.get("host_ip") or ""),
                str(port.get("published") or "<dynamic>"),
                str(port.get("target") or "<unknown>"),
                str(port.get("protocol") or "tcp"),
            )
            if any("|" in field or "\n" in field for field in fields):
                raise ValueError("unexpected delimiter in normalized port")
            print("|".join(fields))
except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"invalid normalized Compose JSON: {exc}", file=sys.stderr)
    raise SystemExit(1)
' <<<"$compose_json")"; then
    die "Impossibile ispezionare le porte normalizzate di $COMPOSE_FILE."
  fi
  if [[ -n "$published" ]]; then
    while IFS='|' read -r service host_ip host_port target_port protocol; do
      [[ -z "$service" ]] && continue
      ((mapping_count += 1))
      [[ "$host_ip" == "127.0.0.1" || "$host_ip" == "::1" ]] \
        || die "Porta pubblicata non loopback in $COMPOSE_FILE: ${service} ${host_ip:-0.0.0.0}:${host_port}:${target_port}/${protocol} (Docker la esporrebbe a Internet ignorando UFW)."
    done <<<"$published"
    say "Compose exposure contract: OK ($mapping_count mapping loopback)."
  fi
  do_registry_check
  say "Host readiness: READY"
}

do_init() {
  local selector image db="$DATA_DIR/mifp.db"
  [[ $# -eq 0 ]] || die "Uso: mifpctl init"
  do_config_check || die "Configurazione non pronta. Correggi le azioni indicate e ripeti config-check."
  validate_production_env; ensure_tools; prepare_runtime_storage
  systemctl is-active caddy.service >/dev/null 2>&1 || die "Caddy non è attivo."
  caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 || die "Caddyfile non valido."
  [[ ! -e "$RELEASE_FILE" ]] || die "Release già inizializzata. Usa: sudo mifpctl deploy sha-<commit>"
  [[ ! -e "$db" ]] || die "$db esiste già senza una release registrata; init non lo sovrascrive."
  selector="$(image_repository):latest"
  image="$(pull_and_pin "$selector" 1)"
  validate_image_deploy_contract "$image" >/dev/null
  init_db_with_image "$image"
  validate_database_host
  preflight_image_db "$image"
  step "Prima release $image"
  if ! activate_release "$image" ""; then
    compose_with_image "$image" down >/dev/null 2>&1 || true
    rm -f -- "$db" "$db-wal" "$db-shm"
    die "Init fallito; nessuna release o database iniziale è stato registrato."
  fi
  if ! sync_backup_timer; then
    compose_with_image "$image" down >/dev/null 2>&1 || true
    rm -f -- "$db" "$db-wal" "$db-shm"
    die "Init fallito: timer backup non abilitato; release e DB iniziale rimossi."
  fi
  write_release_state "$image" ""
  say "Init completato: $image"
  say "Ora importa lo ZIP contenuti dalla dashboard."
}

do_init_db() {
  local reference="${1:-}" image
  [[ -n "$reference" ]] || die "Uso: mifpctl init-db sha-<commit>"
  validate_production_env; ensure_tools; prepare_runtime_storage
  image="$(pull_and_pin "$reference")"
  init_db_with_image "$image"
}

do_deploy() {
  local reference="${1:-}" image old_current old_previous
  [[ -n "$reference" ]] || die "Uso: mifpctl deploy sha-<commit>"
  validate_release_image "$(resolve_image "$reference")"
  validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host
  old_current="$(current_image || true)"; old_previous="$(previous_image || true)"
  image="$(pull_and_pin "$reference")"
  validate_image_deploy_contract "$image" >/dev/null
  preflight_image_db "$image"
  validate_candidate_compose "$image"
  step "Deploy $image"
  activate_release "$image" "$old_current" || die "Deploy fallito; la release precedente è stata mantenuta/ripristinata."
  if [[ "$image" == "$old_current" ]]; then write_release_state "$image" "$old_previous"; else write_release_state "$image" "$old_current"; fi
  cleanup_old_release_images "$image" "$(previous_image || true)"
  say "Deploy completato: $image"
}

do_first_deploy() {
  local reference="${1:-}" image old_current old_previous
  [[ -n "$reference" ]] || die "Uso: mifpctl first-deploy sha-<commit>"
  validate_release_image "$(resolve_image "$reference")"
  validate_production_env; ensure_tools; prepare_runtime_storage
  old_current="$(current_image || true)"; old_previous="$(previous_image || true)"
  image="$(pull_and_pin "$reference")"
  validate_image_deploy_contract "$image" >/dev/null
  [[ -e "$DATA_DIR/mifp.db" ]] || init_db_with_image "$image"
  validate_database_host
  preflight_image_db "$image"
  step "Primo deploy $image"
  activate_release "$image" "$old_current" || die "Primo deploy fallito; nessuna release nuova registrata."
  if [[ "$image" == "$old_current" ]]; then write_release_state "$image" "$old_previous"; else write_release_state "$image" "$old_current"; fi
  cleanup_old_release_images "$image" "$(previous_image || true)"
  sync_backup_timer \
    || die "Applicazione avviata, ma lo stato del timer backup non è stato applicato."
  say "Primo deploy completato. Ora importa lo ZIP contenuti dalla dashboard."
}

do_rollback() {
  local current previous upgraded
  validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host
  current="$(current_image || true)"; previous="$(previous_image || true)"
  [[ -n "$previous" ]] || die "Nessuna release precedente registrata."
  upgraded="$(read_upgrade_value UPGRADED_IMAGE || true)"
  if [[ -n "$upgraded" && "$current" == "$upgraded" ]]; then
    die "L'ultima release ha cambiato schema DB: usa 'sudo mifpctl rollback-upgrade' per ripristinare insieme immagine e database."
  fi
  validate_release_image "$previous"; ensure_image_local "$previous"; preflight_image_db "$previous"
  step "Rollback -> $previous"
  activate_release "$previous" "$current" || die "Rollback non riuscito."
  write_release_state "$previous" "$current"
  cleanup_old_release_images "$previous" "$current"
  say "Rollback completato: $previous"
}

snapshot_live_database() {
  local label="$1" live="$DATA_DIR/mifp.db" saved
  saved="$DATA_DIR/backups/${label}-$(date -u +%Y%m%d-%H%M%S-%N).db"
  # The webapp is still running here, so use the SQLite backup API with a busy
  # timeout and a read-only source handle: without it a concurrent WAL writer
  # makes this fail with SQLITE_BUSY before any swap happens.
  sqlite3 -readonly "$live" ".timeout 30000" ".backup '$saved'"
  [[ "$(sqlite3 -readonly "$saved" 'PRAGMA quick_check; PRAGMA foreign_key_check;')" == "ok" ]] \
    || die "Backup di sicurezza non valido: $saved"
  chown "$RUNTIME_UID:$RUNTIME_GID" "$saved"; chmod 0640 "$saved"
  printf '%s\n' "$saved"
}

install_database_candidate() {
  local candidate="$1" live="$DATA_DIR/mifp.db"
  rm -f "$live-wal" "$live-shm"
  install -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0640 "$candidate" "$live"
  rm -f "$live-wal" "$live-shm"
}

restore_database_snapshot() {
  local saved="$1"
  install_database_candidate "$saved"
}

do_upgrade_db() {
  local reference="${1:-}" candidate="${2:-}" image current previous saved
  [[ -n "$reference" && -n "$candidate" ]] || die "Uso: mifpctl upgrade-db sha-<commit> /path/new.db"
  [[ -f "$candidate" && ! -L "$candidate" ]] || die "DB candidato non valido: $candidate"
  validate_release_image "$(resolve_image "$reference")"
  validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host
  current="$(current_image || true)"; previous="$(previous_image || true)"
  [[ -n "$current" ]] || die "Nessuna release corrente: usa init prima di upgrade-db."
  image="$(pull_and_pin "$reference")"
  preflight_image_db "$image" "$candidate"
  saved="$(snapshot_live_database pre-upgrade)"
  step "Fermo MIFP e installo DB+release verificati"
  stop_release_for_db_swap "$current"
  install_database_candidate "$candidate"
  if activate_release "$image" ""; then
    write_release_state "$image" "$current"
    write_upgrade_checkpoint "$image" "$current" "$saved"
    cleanup_old_release_images "$image" "$current"
    say "Upgrade DB+release completato. Checkpoint rollback: $saved"
    return 0
  fi
  step "Upgrade fallito: ripristino DB+release precedenti"
  compose_with_image "$image" down >/dev/null 2>&1 || true
  restore_database_snapshot "$saved"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    die "Upgrade DB fallito; DB e release precedenti sono stati ripristinati."
  fi
  die "Upgrade DB fallito e la coppia precedente non è tornata ready. Backup: $saved"
}

do_rollback_upgrade() {
  local upgraded previous saved current saved_current
  validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host
  upgraded="$(read_upgrade_value UPGRADED_IMAGE || true)"
  previous="$(read_upgrade_value PREVIOUS_IMAGE || true)"
  saved="$(read_upgrade_value PREVIOUS_DB || true)"
  current="$(current_image || true)"
  [[ -n "$upgraded" && -n "$previous" && -n "$saved" ]] || die "Nessun checkpoint di schema upgrade disponibile."
  [[ "$current" == "$upgraded" ]] || die "La release corrente non coincide con l'ultimo schema upgrade; rollback-upgrade rifiutato."
  [[ -f "$saved" && ! -L "$saved" ]] || die "Backup pre-upgrade mancante: $saved"
  ensure_image_local "$previous"
  preflight_image_db "$previous" "$saved"
  step "Rollback schema upgrade -> $previous + $(basename "$saved")"
  saved_current="$(snapshot_live_database pre-rollback-upgrade)"
  stop_release_for_db_swap "$current"
  restore_database_snapshot "$saved"
  if activate_release "$previous" ""; then
    write_release_state "$previous" "$current"
    rm -f -- "$UPGRADE_FILE"
    say "Rollback schema upgrade completato."
    return 0
  fi

  step "Rollback schema fallito: ripristino la coppia DB+release corrente"
  compose_with_image "$previous" down >/dev/null 2>&1 || true
  restore_database_snapshot "$saved_current"
  if activate_release "$current" ""; then
    die "Rollback schema upgrade annullato: la release corrente è stata ripristinata. Backup tentativo: $saved_current"
  fi
  die "Rollback schema upgrade non riuscito e ripristino automatico fallito. Backup corrente: $saved_current; backup precedente: $saved"
}

do_restore_db() {
  local candidate="${1:-}" current previous saved
  [[ -n "$candidate" ]] || die "Uso: mifpctl restore-db /path/backup.db"
  [[ -f "$candidate" && ! -L "$candidate" ]] || die "Backup DB non valido: $candidate"
  validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host
  current="$(current_image || true)"; previous="$(previous_image || true)"
  [[ -n "$current" ]] || die "Nessuna release corrente registrata."
  ensure_image_local "$current"
  preflight_image_db "$current" "$candidate"
  saved="$(snapshot_live_database pre-restore)"
  step "Fermo MIFP e ripristino il database verificato"
  stop_release_for_db_swap "$current"
  install_database_candidate "$candidate"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    say "Restore DB completato. Snapshot precedente: $saved"
    return 0
  fi
  step "Restore fallito: torno al database precedente"
  compose_with_image "$current" down >/dev/null 2>&1 || true
  restore_database_snapshot "$saved"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    die "Restore DB fallito; database precedente ripristinato."
  fi
  die "Restore DB fallito e il database precedente non è tornato ready. Snapshot: $saved"
}


verify_snapshot_integrity() {
  local snapshot="$1"
  python3 - "$snapshot" <<'PY_VERIFY'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
manifest_path = root / "manifest.json"
if not manifest_path.is_file() or manifest_path.is_symlink():
    raise SystemExit("missing regular manifest.json")
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"invalid manifest.json: {exc}") from exc
version = manifest.get("version")
if manifest.get("format") != "mifp-host-snapshot" or version not in {1, 2, 3, 4, 5}:
    raise SystemExit("unsupported snapshot manifest format")
files = manifest.get("files")
if not isinstance(files, dict) or not files:
    raise SystemExit("snapshot manifest has no files")

# Reject links and special filesystem objects before considering manifest contents.
for path in root.rglob("*"):
    if path.is_symlink():
        raise SystemExit(f"symbolic link rejected: {path.relative_to(root)}")
    if not (path.is_file() or path.is_dir()):
        raise SystemExit(f"special filesystem object rejected: {path.relative_to(root)}")

expected: set[str] = {"mifp.db"}
directories = ["assets", "conferences", "config"]
events_backend = None
if version == 2:
    directories.extend(["events", "events-private"])
    state = root / "events-php-enabled.txt"
    if not state.is_file() or state.is_symlink():
        raise SystemExit("missing or unsafe file: events-php-enabled.txt")
    expected.add("events-php-enabled.txt")
    import re
    safe_prefix = re.compile(r"^[A-Za-z0-9._/-]+$")
    for raw in state.read_text(encoding="utf-8").splitlines():
        prefix = raw.strip()
        if not prefix:
            continue
        parts = prefix.split("/")
        if (
            prefix.startswith("/") or prefix.endswith("/") or "//" in prefix
            or not safe_prefix.fullmatch(prefix) or any(part in {".", ".."} for part in parts)
        ):
            raise SystemExit(f"unsafe PHP allow-list prefix: {prefix!r}")
        target = root / "events" / prefix
        if not target.is_dir() or target.is_symlink():
            raise SystemExit(f"PHP allow-list target missing or unsafe: {prefix}")
elif version in {4, 5}:
    events_backend = "local-vps" if version == 4 else manifest.get("events_backend")
    if events_backend not in {"local-vps", "remote", "disabled"}:
        raise SystemExit("snapshot has invalid events_backend")
    if events_backend != "local-vps":
        events_backend = None
if version in {4, 5} and events_backend == "local-vps":
    directories.append("events-private")
    state = root / "events-php-enabled.txt"
    if not state.is_file() or state.is_symlink():
        raise SystemExit("missing or unsafe file: events-php-enabled.txt")
    expected.add("events-php-enabled.txt")
    import re
    safe_regform = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._~-]*(?:/[A-Za-z0-9][A-Za-z0-9._~-]*)*/regform$"
    )
    for raw in state.read_text(encoding="utf-8").splitlines():
        prefix = raw.strip()
        if prefix and not safe_regform.fullmatch(prefix):
            raise SystemExit(f"unsafe PHP regform allow-list prefix: {prefix!r}")
for dirname in directories:
    directory = root / dirname
    if not directory.is_dir() or directory.is_symlink():
        raise SystemExit(f"missing or unsafe directory: {dirname}/")
    for path in directory.rglob("*"):
        if path.is_file():
            expected.add(path.relative_to(root).as_posix())

if set(files) != expected:
    # `files` is the on-disk walk, `expected` the manifest keys: name the two
    # sets by what they actually mean so an incident is not diagnosed backwards.
    stored_but_absent_from_disk = sorted(expected - set(files))
    present_but_unlisted = sorted(set(files) - expected)
    raise SystemExit(
        "snapshot file set mismatch; "
        f"listed_in_manifest_but_missing_on_disk={stored_but_absent_from_disk[:5]} "
        f"present_on_disk_but_not_in_manifest={present_but_unlisted[:5]}"
    )

for relative, wanted in files.items():
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not isinstance(wanted, str) or len(wanted) != 64:
        raise SystemExit(f"unsafe manifest entry: {relative!r}")
    path = root / rel
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"manifest file missing or unsafe: {relative}")
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != wanted.lower():
        raise SystemExit(f"checksum mismatch: {relative}")
PY_VERIFY
}

snapshot_manifest_version() {
  python3 - "$1" <<'PY_VERSION'
import json, sys
from pathlib import Path
manifest = json.loads((Path(sys.argv[1]) / "manifest.json").read_text(encoding="utf-8"))
print(manifest.get("version", 0))
PY_VERSION
}

snapshot_events_backend() {
  python3 - "$1" <<'PY_EVENTS_BACKEND'
import json, sys
from pathlib import Path
manifest = json.loads((Path(sys.argv[1]) / "manifest.json").read_text(encoding="utf-8"))
version = manifest.get("version", 0)
print("local-vps" if version == 4 else manifest.get("events_backend", ""))
PY_EVENTS_BACKEND
}

restore_snapshot_files() {
  local snapshot="$1" name version
  for name in assets conferences config; do
    [[ -d "$snapshot/$name" && ! -L "$snapshot/$name" ]] || die "Snapshot incompleta o non sicura: $name/"
    install -d -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0750 "$DATA_DIR/$name"
    # --chmod normalises modes even when the snapshot was produced elsewhere;
    # plain `rsync -a` would re-apply the snapshot's own modes and undo the
    # `install -d -m 0750` above.
    rsync -a --delete --chmod=D750,F640 --chown="$RUNTIME_UID:$RUNTIME_GID" "$snapshot/$name/" "$DATA_DIR/$name/"
  done

  version="$(snapshot_manifest_version "$snapshot")"
  if [[ "$version" == "4" || ( "$version" == "5" && "$(snapshot_events_backend "$snapshot")" == "local-vps" ) ]]; then
    install -d -o "$EVENTS_PHP_USER" -g "$EVENTS_PHP_USER" -m 0700 "$EVENTS_PRIVATE_DIR"
    for name in registrations uploads; do
      install -d -o "$EVENTS_PHP_USER" -g "$EVENTS_PHP_USER" -m 0700 "$EVENTS_PRIVATE_DIR/$name"
      rsync -a --delete --chmod=D700,F600 --chown="$EVENTS_PHP_USER:$EVENTS_PHP_USER" \
        "$snapshot/events-private/$name/" "$EVENTS_PRIVATE_DIR/$name/"
    done
    for name in sessions tmp; do
      install -d -o "$EVENTS_PHP_USER" -g "$EVENTS_PHP_USER" -m 0700 "$EVENTS_PRIVATE_DIR/$name"
    done
    install -o root -g root -m 0644 "$snapshot/events-php-enabled.txt" "$EVENTS_PHP_STATE"
  fi

  # Version-2 snapshots may contain the retired VPS event webroot. Integrity is
  # verified, but extracted public copies are intentionally not restored.
}

do_restore_snapshot() {
  local snapshot="${1:-}" current previous saved_root backup_root php_service="" php_was_active=0 version snapshot_backend
  [[ -n "$snapshot" ]] || die "Uso: mifpctl restore-snapshot /path/snapshot-dir"
  snapshot="$(readlink -f -- "$snapshot")"
  [[ -d "$snapshot" && -f "$snapshot/mifp.db" && ! -L "$snapshot/mifp.db" ]] || die "Snapshot non valida: $snapshot"
  validate_production_env; ensure_tools; has rsync || die "Comando richiesto non disponibile: rsync"
  verify_snapshot_integrity "$snapshot" || die "Snapshot corrotta, incompleta o non sicura: $snapshot"
  prepare_runtime_storage; validate_database_host
  current="$(current_image || true)"; previous="$(previous_image || true)"
  [[ -n "$current" ]] || die "Nessuna release corrente registrata."
  ensure_image_local "$current"
  preflight_image_db "$current" "$snapshot/mifp.db"

  backup_root="${MIFP_BACKUP_ROOT:-/var/backups/mifp}"
  # MIFP_BACKUP_NO_PRUNE keeps the pre-restore safety snapshot from rotating:
  # the oldest snapshot it would delete is exactly the one being restored.
  MIFP_OPERATION_LOCK_HELD=1 MIFP_BACKUP_NO_PRUNE=1 "$BACKUP_SCRIPT" >/dev/null
  saved_root="$(readlink -f -- "$backup_root/snapshots/latest")"
  [[ -d "$saved_root" && "$saved_root" != "$snapshot" ]] || die "Impossibile creare snapshot di sicurezza pre-restore."
  # Re-assert the source snapshot immediately before taking the service down, so
  # any future loss aborts while the site is still serving.
  [[ -f "$snapshot/mifp.db" && ! -L "$snapshot/mifp.db" ]] \
    || die "Snapshot sorgente scomparsa dopo la snapshot di sicurezza: $snapshot"
  verify_snapshot_integrity "$saved_root" || die "Snapshot di sicurezza pre-restore non valida: $saved_root"

  step "Fermo MIFP e ripristino snapshot completa"
  version="$(snapshot_manifest_version "$snapshot")"
  snapshot_backend="$(snapshot_events_backend "$snapshot")"
  if [[ "$snapshot_backend" == "local-vps" && "$(config_cli get EVENTS_PUBLISH_BACKEND)" == "local-vps" ]]; then
    php_service="$(events_php_service || true)"
    if [[ -n "$php_service" ]] && systemctl is-active --quiet "$php_service"; then
      systemctl stop "$php_service" || die "Impossibile fermare PHP-FPM eventi per il restore."
      php_was_active=1
      trap '[[ "$php_was_active" == 0 ]] || systemctl start "$php_service" >/dev/null 2>&1 || true' EXIT
    fi
  fi
  stop_release_for_db_swap "$current"
  install_database_candidate "$snapshot/mifp.db"
  restore_snapshot_files "$snapshot"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    if [[ "$snapshot_backend" == "local-vps" && "$(config_cli get EVENTS_PUBLISH_BACKEND)" == "local-vps" ]]; then
      suspend_events_php_include
      say "PHP regform resta deny-by-default finché events-republish-all ricrea e valida i path pubblici."
    fi
    if ((php_was_active)); then systemctl start "$php_service"; php_was_active=0; trap - EXIT; fi
    say "Restore snapshot completato. Snapshot precedente: $saved_root"
    return 0
  fi

  step "Restore snapshot fallito: ripristino la fotografia precedente"
  compose_with_image "$current" down >/dev/null 2>&1 || true
  install_database_candidate "$saved_root/mifp.db"
  restore_snapshot_files "$saved_root"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    if [[ "$(snapshot_events_backend "$saved_root")" == "local-vps" \
       && "$(config_cli get EVENTS_PUBLISH_BACKEND)" == "local-vps" ]]; then
      install_events_php_include
    fi
    if ((php_was_active)); then systemctl start "$php_service"; php_was_active=0; trap - EXIT; fi
    die "Restore snapshot fallito; stato precedente ripristinato."
  fi
  die "Restore snapshot fallito e lo stato precedente non è tornato ready. Snapshot di sicurezza: $saved_root"
}

events_php_service() {
  local service
  [[ -f "$PHP_FPM_SERVICE_FILE" && ! -L "$PHP_FPM_SERVICE_FILE" ]] || return 1
  service="$(tr -d '[:space:]' < "$PHP_FPM_SERVICE_FILE")"
  [[ "$service" =~ ^php[0-9]+\.[0-9]+-fpm\.service$ ]] || return 1
  printf '%s' "$service"
}

events_local_root() {
  local root
  [[ "$(config_cli get EVENTS_PUBLISH_BACKEND)" == "local-vps" ]] \
    || die "I comandi PHP eventi sono disponibili solo con EVENTS_PUBLISH_BACKEND=local-vps."
  root="$(config_cli get EVENTS_LOCAL_ROOT)"
  [[ -n "$root" && "$root" == /* && -d "$root" && ! -L "$root" ]] \
    || die "EVENTS_LOCAL_ROOT non è una directory assoluta sicura: $root"
  readlink -f -- "$root"
}

normalize_events_php_prefix() {
  local prefix="${1:-}"
  prefix="${prefix#/}"; prefix="${prefix%/}"
  [[ "$prefix" =~ ^[A-Za-z0-9][A-Za-z0-9._~-]*(/[A-Za-z0-9][A-Za-z0-9._~-]*)*/regform$ ]] \
    || die "Path PHP non valido: usa un path relativo sicuro che termini in /regform."
  printf '%s' "$prefix"
}

render_events_php_include() {
  local target="$1" policy="${2:-$EVENTS_PHP_STATE}" root prefix resolved escaped i=0
  root="$(events_local_root)"
  {
    printf '%s\n' '# Generated by mifpctl. Do not edit by hand.'
    printf '%s\n' '# Empty means every PHP-like event file is denied.'
    if [[ -f "$policy" ]]; then
      while IFS= read -r prefix; do
        [[ -n "$prefix" ]] || continue
        [[ "$(normalize_events_php_prefix "$prefix")" == "$prefix" ]] \
          || die "Allow-list PHP non canonica: $prefix"
        [[ -d "$root/$prefix" && ! -L "$root/$prefix" ]] \
          || die "Allow-list PHP punta a una directory mancante o non sicura: $prefix"
        resolved="$(readlink -f -- "$root/$prefix")"
        [[ "$resolved" == "$root/"* ]] || die "Allow-list PHP esce dalla root eventi: $prefix"
        find "$root/$prefix" -type l -print -quit | grep -q . \
          && die "Allow-list PHP contiene symlink: $prefix"
        escaped="$(python3 -c 'import re,sys; print(re.escape(sys.argv[1]))' "$prefix")"
        i=$((i + 1))
        printf '@mifp_events_php_%d path_regexp mifp_events_php_%d ^/%s/(?:.*/)?[^/]+\\.php$\n' "$i" "$i" "$escaped"
        printf 'php_fastcgi @mifp_events_php_%d unix/%s\n\n' "$i" "$EVENTS_PHP_SOCKET"
      done < "$policy"
    fi
  } > "$target"
}

install_events_php_include() {
  local policy="${1:-$EVENTS_PHP_STATE}" candidate backup include_dir had_old=0
  include_dir="$(dirname "$EVENTS_PHP_INCLUDE")"
  [[ -d "$include_dir" ]] || die "Directory Caddy mancante: $include_dir"
  candidate="$(mktemp "$include_dir/.mifp-events-php.caddy.XXXXXX")"
  backup="$(mktemp "$include_dir/.mifp-events-php.previous.XXXXXX")"
  render_events_php_include "$candidate" "$policy"
  chown root:caddy "$candidate"; chmod 0644 "$candidate"
  if [[ -f "$EVENTS_PHP_INCLUDE" ]]; then
    cp -a "$EVENTS_PHP_INCLUDE" "$backup"
    had_old=1
  fi
  mv -f "$candidate" "$EVENTS_PHP_INCLUDE"
  if ! caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null; then
    if ((had_old)); then mv -f "$backup" "$EVENTS_PHP_INCLUDE"; else rm -f "$EVENTS_PHP_INCLUDE"; fi
    die "La configurazione PHP generata non è valida; configurazione precedente ripristinata."
  fi
  if ! systemctl reload caddy.service; then
    if ((had_old)); then cp -a "$backup" "$EVENTS_PHP_INCLUDE"; else rm -f "$EVENTS_PHP_INCLUDE"; fi
    systemctl reload caddy.service >/dev/null 2>&1 || true
    die "Caddy non ha accettato il reload; routing PHP precedente ripristinato."
  fi
  rm -f "$backup"
  if [[ "$policy" != "$EVENTS_PHP_STATE" ]]; then
    chmod 0644 "$policy"; chown root:root "$policy"; mv -f "$policy" "$EVENTS_PHP_STATE"
  fi
}

suspend_events_php_include() {
  local candidate backup include_dir
  include_dir="$(dirname "$EVENTS_PHP_INCLUDE")"
  candidate="$(mktemp "$include_dir/.mifp-events-php.suspended.XXXXXX")"
  backup="$(mktemp "$include_dir/.mifp-events-php.previous.XXXXXX")"
  printf '%s\n' '# Suspended until mifpctl events-republish-all validates restored paths.' > "$candidate"
  chown root:caddy "$candidate"; chmod 0644 "$candidate"
  cp -a "$EVENTS_PHP_INCLUDE" "$backup"
  mv -f "$candidate" "$EVENTS_PHP_INCLUDE"
  if ! caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null; then
    mv -f "$backup" "$EVENTS_PHP_INCLUDE"
    die "Impossibile sospendere in sicurezza il routing PHP eventi."
  fi
  if ! systemctl reload caddy.service; then
    mv -f "$backup" "$EVENTS_PHP_INCLUDE"
    systemctl reload caddy.service >/dev/null 2>&1 || true
    die "Caddy non ha accettato la sospensione PHP; routing precedente ripristinato."
  fi
  rm -f "$backup"
}

do_events_php_list() {
  events_local_root >/dev/null
  if [[ ! -s "$EVENTS_PHP_STATE" ]]; then
    say "PHP eventi: nessun regform abilitato (deny-by-default)."
    return 0
  fi
  say "PHP eventi abilitato esclusivamente per:"
  sed 's/^/  - /' "$EVENTS_PHP_STATE"
}

do_events_php_enable() {
  local prefix root service tmp
  prefix="$(normalize_events_php_prefix "${1:-}")"
  root="$(events_local_root)"
  [[ -d "$root/$prefix" && ! -L "$root/$prefix" ]] \
    || die "Directory regform inesistente o non sicura: $root/$prefix"
  find "$root/$prefix" -type l -print -quit | grep -q . \
    && die "Il regform contiene symlink; PHP non viene abilitato."
  find "$root/$prefix" -type f -iname '*.php' -print -quit | grep -q . \
    || die "Nessun file .php trovato sotto $root/$prefix"
  service="$(events_php_service || true)"
  [[ -n "$service" ]] || die "Pool PHP-FPM eventi non configurato; riesegui bootstrap-vps.sh."
  systemctl enable --now "$service"
  [[ -S "$EVENTS_PHP_SOCKET" ]] || die "Socket PHP-FPM eventi non disponibile: $EVENTS_PHP_SOCKET"

  [[ -e "$EVENTS_PHP_STATE" ]] || install -o root -g root -m 0644 /dev/null "$EVENTS_PHP_STATE"
  [[ -f "$EVENTS_PHP_STATE" && ! -L "$EVENTS_PHP_STATE" ]] || die "Stato PHP eventi non sicuro."
  if grep -Fxq -- "$prefix" "$EVENTS_PHP_STATE"; then
    install_events_php_include
  else
    tmp="$(mktemp "$MIFP_HOME/.events-php-enabled.XXXXXX")"
    { cat "$EVENTS_PHP_STATE"; printf '%s\n' "$prefix"; } | LC_ALL=C sort -u > "$tmp"
    install_events_php_include "$tmp"
  fi
  say "PHP abilitato soltanto per $prefix."
}

do_events_php_disable() {
  local prefix tmp
  prefix="$(normalize_events_php_prefix "${1:-}")"
  events_local_root >/dev/null
  if [[ ! -f "$EVENTS_PHP_STATE" ]]; then
    say "PHP era già disabilitato per $prefix."
    return 0
  fi
  tmp="$(mktemp "$MIFP_HOME/.events-php-enabled.XXXXXX")"
  grep -Fxv -- "$prefix" "$EVENTS_PHP_STATE" > "$tmp" || true
  install_events_php_include "$tmp"
  say "PHP disabilitato per $prefix."
}

do_status() {
  local current previous repo
  current="$(current_image || true)"; previous="$(previous_image || true)"
  repo="$(env_value MIFP_IMAGE_REPOSITORY || true)"
  say "Host tools:         $MIFPCTL_VERSION (deploy contract v$DEPLOY_CONTRACT_VERSION)"
  say "Current release:    ${current:-none}"
  say "Previous release:   ${previous:-none}"
  [[ -z "$repo" ]] || say "Configured channel: $repo:latest"
  [[ -n "$current" ]] && compose_with_image "$current" ps || docker ps --filter label=com.docker.compose.project=mifp || true
  systemctl is-active caddy >/dev/null 2>&1 && say "Caddy: attivo" || say "Caddy: NON attivo"
  if [[ "$(config_cli get MAIL_PROVIDER)" == "smtp" ]]; then
    local alert_timer
    alert_timer="$(systemctl is-enabled mifp-alert-check.timer 2>/dev/null || true)"
    say "Notifications:      ${alert_timer:-disabled}"
  else
    say "Notifications:      mail disabled"
  fi
  config_cli check || true
}

do_logs() { local current; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; compose_with_image "$current" logs --tail 300 -f web; }
do_stop() { local current; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; compose_with_image "$current" down; }
do_restart() { local current; validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; preflight_image_db "$current"; activate_release "$current" "" || die "Restart fallito."; }

do_backup() { [[ -x "$BACKUP_SCRIPT" ]] || die "Manca $BACKUP_SCRIPT"; "$BACKUP_SCRIPT"; }

do_events_republish_all() {
  local current
  validate_production_env
  ensure_tools
  current="$(current_image || true)"
  [[ -n "$current" ]] || die "Nessuna release corrente registrata."
  service_running "$current" || die "Il servizio web non è in esecuzione; avvialo prima del recovery eventi."
  step "Ripubblico i siti evento dai pacchetti sorgente conservati"
  compose_with_image "$current" exec -T web flask events-republish-all
  # A restored allow-list remains inert until every referenced public path has
  # been safely rebuilt. Validation happens before the live include is swapped.
  if [[ "$(config_cli get EVENTS_PUBLISH_BACKEND)" == "local-vps" ]]; then
    install_events_php_include
  fi
}

# Report backup health without restoring anything: the newest published snapshot
# must be found, fresh, and pass the same integrity verification restore uses.
check_backup_health() {
  local backup_root latest newest_age_h check_output configured timer_enabled
  backup_root="${MIFP_BACKUP_ROOT:-/var/backups/mifp}"
  configured="$(config_cli get BACKUP_ENABLED)"
  if [[ "$configured" == "false" ]]; then
    say "Backups: DISABLED by configuration"
    return 0
  fi
  timer_enabled="$(systemctl is-enabled mifp-backup.timer 2>/dev/null || true)"
  if [[ "$timer_enabled" != "enabled" ]]; then
    say "Backups: ERROR (BACKUP_ENABLED=true but mifp-backup.timer is not enabled)"
    return 1
  fi
  if [[ ! -d "$backup_root/snapshots" ]]; then
    say "Backups: PENDING (timer enabled; no snapshot published yet)"
    return 0
  fi
  latest="$(readlink -f -- "$backup_root/snapshots/latest" 2>/dev/null || true)"
  if [[ -z "$latest" || ! -d "$latest" ]]; then
    say "Backups: ERROR (no published snapshot)"; return 1
  fi
  if ! check_output="$(verify_snapshot_integrity "$latest" 2>&1)"; then
    say "Backups: ERROR (latest snapshot fails verification: ${check_output})"; return 1
  fi
  newest_age_h=$(( ( $(date +%s) - $(stat -c %Y "$latest") ) / 3600 ))
  if (( newest_age_h > 48 )); then
    say "Backups: ERROR (latest snapshot is ${newest_age_h}h old)"; return 1
  fi
  say "Backups: OK ($(basename "$latest"), ${newest_age_h}h old, verified)"
  return 0
}

do_doctor() {
  local failed=0 current db="$DATA_DIR/mifp.db" domain events_backend events_url events_root event_code php_service
  local min_mb available_kb available_mb target db_check
  step "Stato host"
  if config_cli check; then say "Configuration: OK"; else say "Configuration: ERROR"; failed=1; fi
  docker info >/dev/null 2>&1 && say "Docker: OK" || { say "Docker: ERROR"; failed=1; }
  docker compose version >/dev/null 2>&1 && say "Compose: OK" || { say "Compose: ERROR"; failed=1; }
  systemctl is-active caddy >/dev/null 2>&1 && say "Caddy: OK" || { say "Caddy: ERROR"; failed=1; }
  caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
    && say "Caddyfile: OK" || { say "Caddyfile: ERROR"; failed=1; }
  events_backend="$(config_cli get EVENTS_PUBLISH_BACKEND)"
  events_url="$(config_cli get EVENTS_PUBLIC_BASE_URL)"
  if [[ "$events_backend" == "local-vps" ]]; then
    events_root="$(config_cli get EVENTS_LOCAL_ROOT)"
    if [[ -d "$events_root" && ! -L "$events_root" ]]; then
      say "Event hosting: local-vps (root $events_root; publisher available)"
    else
      say "Event hosting: ERROR (missing/unsafe local root $events_root)"; failed=1
    fi
    event_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$events_url/" 2>/dev/null || true)"
    case "$event_code" in
      200|403|404) say "Event HTTPS/ACME: OK ($events_url, HTTP $event_code)" ;;
      *) say "Event HTTPS/ACME: ERROR ($events_url, HTTP ${event_code:-unreachable})"; failed=1 ;;
    esac
    php_service="$(events_php_service || true)"
    if [[ -n "$php_service" ]] && systemctl is-active --quiet "$php_service" \
       && [[ -S "$EVENTS_PHP_SOCKET" ]]; then
      say "Event PHP-FPM: OK (dedicated pool; deny-by-default allow-list)"
    else
      say "Event PHP-FPM: ERROR (dedicated service/socket unavailable)"; failed=1
    fi
    if [[ -d "$EVENTS_PRIVATE_DIR/registrations" && ! -L "$EVENTS_PRIVATE_DIR" \
       && ! -L "$EVENTS_PRIVATE_DIR/registrations" ]]; then
      say "Event private storage: OK ($EVENTS_PRIVATE_DIR)"
    else
      say "Event private storage: ERROR ($EVENTS_PRIVATE_DIR)"; failed=1
    fi
  else
    say "Event hosting: $events_backend (VPS event serving and HTTPS checks disabled)"
  fi

  min_mb="$(env_value MIFP_DEPLOY_MIN_FREE_MB || true)"; min_mb="${min_mb:-2048}"
  target="$DATA_DIR"; [[ -d /var/lib/docker ]] && target=/var/lib/docker
  available_kb="$(df -Pk "$target" | awk 'NR==2 {print $4}')"
  available_mb=$((available_kb / 1024))
  if (( available_mb >= min_mb )); then say "Storage: OK (${available_mb} MB free)"; else say "Storage: ERROR (${available_mb} MB < ${min_mb} MB)"; failed=1; fi

  check_backup_health || failed=1
  if [[ -f /var/run/reboot-required ]]; then
    say "Host reboot: REQUIRED to finish applying updates (/var/run/reboot-required)"; failed=1
  else
    say "Host reboot: not required"
  fi
  current="$(current_image || true)"
  if [[ -f "$db" && ! -L "$db" ]]; then
    if db_check="$(sqlite3 -readonly "$db" 'PRAGMA quick_check; PRAGMA foreign_key_check;' 2>&1)" && [[ "$db_check" == ok ]]; then
      say "DB: OK"
    else
      say "DB: ERROR"; failed=1
    fi
  elif [[ -n "$current" ]]; then
    say "DB: ERROR (missing after initialization)"; failed=1
  else
    say "DB: NOT INITIALIZED"
  fi

  if [[ -z "$current" ]]; then
    say "Release: NOT INITIALIZED"
  else
    if [[ ! "$current" =~ @sha256:[0-9a-f]{64}$ ]]; then
      say "Release: ERROR (state is not an immutable digest)"; failed=1
    elif ! docker image inspect "$current" >/dev/null 2>&1; then
      say "Release: ERROR (image missing locally)"; failed=1
    else
      say "Release: OK ($current)"
      if [[ -f "$db" ]]; then preflight_image_db "$current" "$db" && say "DB/runtime contract: OK" || failed=1; fi
    fi
    curl -fsS --max-time 3 http://127.0.0.1:8000/ready >/dev/null 2>&1 \
      && say "Application health: OK" || { say "Application health: ERROR"; failed=1; }
  fi

  domain="$(env_value MIFP_DOMAIN || true)"
  if [[ -n "$current" && -n "$domain" ]]; then
    curl -fsS --max-time 5 "https://$domain/health" >/dev/null 2>&1 \
      && say "HTTPS application: OK" || { say "HTTPS application: ERROR"; failed=1; }
  fi
  ((failed == 0)) || die "Doctor found errors."
  say "Doctor: OK"
}

do_security_check() {
  local failed=0 warnings=0 bad current cid inspect user privileged network_mode readonly_root security_opts mounts envs unexpected mode
  step "Security check"

  security_error() { say "ERROR: $*"; failed=1; }
  security_warn() { say "WARN: $*"; warnings=$((warnings + 1)); }
  security_ok() { say "$*: OK"; }

  security_file() {
    local label="$1" path="$2" expected_mode="$3" expected_uid="$4" expected_gid="$5" optional="${6:-false}" details
    if [[ ! -e "$path" && ! -L "$path" ]]; then
      [[ "$optional" == true ]] && { say "$label: NOT CONFIGURED"; return 0; }
      security_error "missing $label: $path"
      return
    fi
    if [[ -L "$path" || ! -f "$path" ]]; then
      security_error "$label is not a safe regular file: $path"
      return
    fi
    details="$(stat -c '%a|%u|%g' "$path" 2>/dev/null || true)"
    if [[ "$details" == "$expected_mode|$expected_uid|$expected_gid" ]]; then
      security_ok "$label permissions and ownership"
    else
      IFS='|' read -r mode file_uid file_gid <<<"$details"
      security_error "$label must be uid ${expected_uid}, gid ${expected_gid}, mode 0${expected_mode} (found uid ${file_uid:-unknown}, gid ${file_gid:-unknown}, mode ${mode:-unknown})"
    fi
  }

  security_dir() {
    local label="$1" path="$2" expected_mode="$3" expected_uid="$4" expected_gid="$5" details
    if [[ -L "$path" || ! -d "$path" ]]; then
      security_error "$label is not a safe directory: $path"
      return
    fi
    details="$(stat -c '%a|%u|%g' "$path" 2>/dev/null || true)"
    if [[ "$details" == "$expected_mode|$expected_uid|$expected_gid" ]]; then
      security_ok "$label permissions and ownership"
    else
      local mode dir_uid dir_gid
      IFS='|' read -r mode dir_uid dir_gid <<<"$details"
      security_error "$label must be uid ${expected_uid}, gid ${expected_gid}, mode 0${expected_mode} (found uid ${dir_uid:-unknown}, gid ${dir_gid:-unknown}, mode ${mode:-unknown})"
    fi
  }

  security_state_file() {
    local path="$1" label="$2" allowed="$3"
    [[ -e "$path" || -L "$path" ]] || return 0
    security_file "$label" "$path" 600 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
    [[ -f "$path" && ! -L "$path" ]] || return 0
    if ! grep -Eq "^(${allowed})=" "$path" 2>/dev/null \
       || grep -Evq "^(${allowed})=[^[:cntrl:]]*$" "$path" 2>/dev/null; then
      security_error "$label contains an unexpected key or malformed line"
    else
      security_ok "$label content class"
    fi
  }

  if caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1; then
    security_ok "Caddy configuration"
  else
    security_error "Caddy configuration invalid"
  fi

  # Effective SSH policy (not the file text): sshd -T is the only trustworthy
  # source because provider/cloud-init drop-ins can override ours.
  if has sshd && sshd -T >/dev/null 2>&1; then
    local ssh_effective
    ssh_effective="$(sshd -T 2>/dev/null)"
    if grep -qx 'passwordauthentication no' <<<"$ssh_effective"; then
      security_ok "SSH password authentication disabled"
    else
      security_warn "SSH PasswordAuthentication allows password login. This is the retained provider/default policy, not key-only hardening; use strong passwords. Optional future hardening: mifpctl ssh-harden --operator USER"
    fi
    if grep -qxE 'permitrootlogin (no|prohibit-password|without-password)' <<<"$ssh_effective"; then
      security_ok "SSH root login restricted"
    else
      security_warn "SSH PermitRootLogin allows root password login. This is the retained provider/default policy, not key-only hardening; use a strong root password. Optional future hardening: mifpctl ssh-harden --operator USER"
    fi
  else
    # A missing sshd is a limitation, not a finding: report it truthfully
    # instead of claiming the SSH policy is safe.
    say "WARN: sshd not available; SSH policy NOT VERIFIED"
  fi

  if has ufw; then
    if ufw status 2>/dev/null | grep -q 'Status: active'; then
      security_ok "Firewall active"
      local ufw_status
      ufw_status="$(ufw status verbose 2>/dev/null || true)"
      for rule in 80/tcp 443/tcp; do
        grep -qE "(^|[[:space:]])${rule}" <<<"$ufw_status" \
          && security_ok "Firewall allows $rule" || security_error "missing UFW rule for $rule"
      done
      local ssh_rule_port
      ssh_rule_port="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2; exit}')"
      ssh_rule_port="${ssh_rule_port:-22}"
      if grep -qE "(^|[[:space:]])${ssh_rule_port}/tcp" <<<"$ufw_status"; then
        security_ok "Firewall allows SSH ($ssh_rule_port/tcp)"
      else
        security_error "missing UFW rule for the SSH port $ssh_rule_port/tcp"
      fi
      grep -q '(v6)' <<<"$ufw_status" \
        && security_ok "Firewall IPv6 rules present" \
        || security_error "UFW shows no IPv6 rules: check IPV6=yes in /etc/default/ufw"
    else
      security_error "UFW is not active"
    fi
  else
    security_error "ufw not available; firewall NOT VERIFIED"
  fi

  if [[ -f /var/run/reboot-required ]]; then
    security_error "host requires a reboot to finish applying updates (/var/run/reboot-required)"
  else
    security_ok "No pending reboot"
  fi
  if [[ -f /var/run/reboot-required.pkgs ]]; then
    say "Packages requiring reboot:"; sed 's/^/  - /' /var/run/reboot-required.pkgs || true
  fi

  # Effective Docker daemon configuration. The bootstrap installs secure
  # defaults only when the operator has no daemon.json of their own, so the
  # effective state is what matters here, not the file we would have written.
  # TCP API exposure is a real exposure (error); live-restore and daemon-wide
  # log rotation are resilience/observability settings (WARN only), and
  # per-service limits in compose remain in force either way.
  local docker_tcp=0 live_restore log_driver log_max_size
  if [[ -f /etc/docker/daemon.json ]] \
     && grep -qE '"hosts"[[:space:]]*:[[:space:]]*\[[^]]*tcp://' /etc/docker/daemon.json 2>/dev/null; then
    security_error "Docker daemon is configured with a TCP API socket (/etc/docker/daemon.json)"
    docker_tcp=1
  fi
  if has ps && ps -eo args= 2>/dev/null | grep -E '(^|/)dockerd( |$)' | grep -qE '(-H|--host)[= ]tcp://'; then
    security_error "dockerd is running with a TCP API socket"
    docker_tcp=1
  fi
  if has ss && ss -H -lnt 2>/dev/null | awk '{print $4}' | grep -qE ':(2375|2376)$'; then
    security_error "a Docker API TCP port (2375/2376) is listening"
    docker_tcp=1
  fi
  (( docker_tcp )) || security_ok "Docker daemon TCP API not exposed"

  if has docker && docker info >/dev/null 2>&1; then
    live_restore="$(docker info --format '{{.LiveRestoreEnabled}}' 2>/dev/null || true)"
    if [[ "$live_restore" == "true" ]]; then
      security_ok "Docker live-restore"
    else
      say "WARN: Docker live-restore is disabled; a daemon restart stops the webapp. Add it via /etc/docker/daemon.json (bootstrap sets it only when no daemon.json exists)."
    fi
    log_driver="$(docker info --format '{{.LoggingDriver}}' 2>/dev/null || true)"
    log_max_size=""
    if [[ -f /etc/docker/daemon.json ]] && has python3; then
      log_max_size="$(python3 -c 'import json; d=json.load(open("/etc/docker/daemon.json")); print((d.get("log-opts") or {}).get("max-size", ""))' 2>/dev/null || true)"
    fi
    if [[ -n "$log_max_size" ]]; then
      security_ok "Docker daemon log rotation ($log_max_size)"
    else
      say "WARN: no daemon-level Docker log rotation (log-driver=${log_driver:-unknown}); the compose services set their own 10m x 3 limits, so growth is bounded for MIFP itself."
    fi
  fi

  bad="$(find "$MIFP_HOME" -xdev -perm -0002 -print -quit 2>/dev/null || true)"
  [[ -z "$bad" ]] && security_ok "World-writable MIFP paths" || security_error "world-writable path: $bad"

  bad="$(find "$MIFP_HOME" -maxdepth 1 \( -name '.release.env.*' -o -name '.upgrade.env.*' \) -print -quit 2>/dev/null || true)"
  [[ -z "$bad" ]] && security_ok "Stale deployment staging" || security_error "stale staging file/directory: $bad"

  security_file "Runtime environment" "$ENV_FILE" 600 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
  security_file "Secrets file" "$SECRETS_FILE" 600 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
  security_file "Public config" "$PUBLIC_CONFIG_FILE" 640 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
  security_dir "Docker secret material directory" "$SECRET_MATERIAL_DIR" 700 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
  local material_name
  for material_name in mifp_secret_key mifp_admin_password_hash mifp_smtp_password mifp_events_remote_password; do
    security_file "Docker secret material $material_name" "$SECRET_MATERIAL_DIR/$material_name" 400 "$RUNTIME_UID" "$RUNTIME_GID"
  done
  local material_audit=""
  if material_audit="$(config_cli audit-secret-material --output-dir "$SECRET_MATERIAL_DIR" 2>&1)"; then
    security_ok "Docker secret material matches canonical state"
  else
    local material_line material_reported=0
    while IFS= read -r material_line; do
      if [[ "$material_line" == ERROR:* ]]; then
        say "$material_line"
        material_reported=1
      fi
    done <<<"$material_audit"
    (( material_reported )) || security_error "unable to verify Docker secret material"
    failed=1
  fi
  security_state_file "$RELEASE_FILE" "Release state" 'CURRENT_IMAGE|PREVIOUS_IMAGE'
  security_state_file "$UPGRADE_FILE" "Upgrade state" 'UPGRADED_IMAGE|PREVIOUS_IMAGE|PREVIOUS_DB'

  local layout_audit=""
  if layout_audit="$(config_cli audit-layout 2>&1)"; then
    security_ok "Secret/config location"
  else
    local layout_line layout_reported=0
    while IFS= read -r layout_line; do
      if [[ "$layout_line" =~ ^ERROR:\ (SECRET_KEY|ADMIN_PASSWORD_HASH|SMTP_PASSWORD|RESTIC_PASSWORD|EVENTS_REMOTE_PASSWORD|[A-Z][A-Z0-9_]*)\ unexpectedly\ present\ in\  ]]; then
        say "$layout_line"
        layout_reported=1
      fi
    done <<<"$layout_audit"
    (( layout_reported )) || security_error "unable to verify secret/config content classes"
    failed=1
  fi

  local events_backend mail_provider smtp_host
  events_backend="$(config_cli get EVENTS_PUBLISH_BACKEND)"
  mail_provider="$(config_cli get MAIL_PROVIDER)"
  smtp_host="$(config_cli get SMTP_HOST)"
  if [[ "$mail_provider" != "disabled" && "$mail_provider" != "console" && -n "$smtp_host" ]]; then
    mail_provider="smtp"
  fi
  if [[ "$mail_provider" == "smtp" ]]; then
    if [[ "$events_backend" == "local-vps" ]]; then
      local events_php_gid
      events_php_gid="$(id -g "$EVENTS_PHP_USER" 2>/dev/null || true)"
      [[ -n "$events_php_gid" ]] \
        && security_file "Host SMTP relay secret" "$MAIL_RELAY_CONFIG" 640 "$SECURITY_ROOT_UID" "$events_php_gid" \
        || security_error "cannot resolve group for event PHP user: $EVENTS_PHP_USER"
    else
      security_file "Host SMTP relay secret" "$MAIL_RELAY_CONFIG" 600 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
    fi
    local alert_timer_enabled
    alert_timer_enabled="$(systemctl is-enabled mifp-alert-check.timer 2>/dev/null || true)"
    [[ "$alert_timer_enabled" == "enabled" ]] \
      && security_ok "Independent notification monitor enabled" \
      || security_error "SMTP is configured but mifp-alert-check.timer is not enabled"
  elif [[ -e "$MAIL_RELAY_CONFIG" ]]; then
    security_error "stale host SMTP relay config exists while mail is disabled: $MAIL_RELAY_CONFIG"
  fi
  security_file "Docker credentials" "$DOCKER_CONFIG_FILE" 600 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID" true

  local backup_root restic_password_file
  backup_root="${MIFP_BACKUP_ROOT:-/var/backups/mifp}"
  if [[ -e "$backup_root" || -L "$backup_root" ]]; then
    local backup_details
    backup_details="$(stat -c '%F|%a|%u|%g' "$backup_root" 2>/dev/null || true)"
    [[ ! -L "$backup_root" && "$backup_details" == "directory|700|$SECURITY_ROOT_UID|$SECURITY_ROOT_GID" ]] \
      && security_ok "Backup root permissions and ownership" \
      || security_error "backup root must be a root-owned mode 0700 directory: $backup_root"
  fi
  restic_password_file="$(env_value MIFP_RESTIC_PASSWORD_FILE || true)"
  if [[ -n "$restic_password_file" ]]; then
    security_file "Restic password file" "$restic_password_file" 600 "$SECURITY_ROOT_UID" "$SECURITY_ROOT_GID"
  fi

  if [[ "$events_backend" == "local-vps" ]]; then
    local events_uid events_gid private_details events_root
    events_uid="${MIFP_SECURITY_EVENTS_UID:-$(id -u "$EVENTS_PHP_USER" 2>/dev/null || true)}"
    events_gid="${MIFP_SECURITY_EVENTS_GID:-$(id -g "$EVENTS_PHP_USER" 2>/dev/null || true)}"
    private_details="$(stat -c '%F|%a|%u|%g' "$EVENTS_PRIVATE_DIR" 2>/dev/null || true)"
    [[ -n "$events_uid" && -n "$events_gid" && ! -L "$EVENTS_PRIVATE_DIR" && "$private_details" == "directory|700|$events_uid|$events_gid" ]] \
      && security_ok "Private event storage permissions and ownership" \
      || security_error "private event storage must be owned by $EVENTS_PHP_USER and mode 0700: $EVENTS_PRIVATE_DIR"

    events_root="$(config_cli get EVENTS_LOCAL_ROOT)"
    if [[ -d "$events_root" && ! -L "$events_root" ]]; then
      if bad="$(find "$events_root" -xdev -mindepth 1 \
        \( -type d \( -iname private -o -iname registrations -o -iname config \) \
        -o -iname '.env' -o -iname '.env.*' -o -iname '*.key' -o -iname '*.pem' \
        -o -iname 'credentials*.json' -o -iname 'secrets*.json' -o -iname 'tokens*.json' \
        -o -iname '*.db' -o -iname '*.sqlite*' -o -iname '*.bak' -o -iname '*.backup' \
        -o -iname '.htpasswd' -o -iname 'git-credentials' \) -print -quit 2>/dev/null)"; then
        if [[ -z "$bad" ]]; then
          security_ok "Published event tree sensitive paths"
        else
          security_error "sensitive path in published event tree: ${bad#"$events_root"/}"
        fi
      else
        security_error "unable to inspect the published event tree"
      fi
    else
      security_error "event publication root is missing, unsafe, or a symlink: $events_root"
    fi
  fi

  if has ss; then
    unexpected="$(ss -H -lntp 2>/dev/null | awk '
      {
        local_addr=$4; proc=""; for (i=6; i<=NF; i++) proc=proc $i;
        if (local_addr ~ /^127\.0\.0\.1:/ || local_addr ~ /^\[::1\]:/) next;
        n=split(local_addr, parts, ":"); port=parts[n]; gsub(/[^0-9]/, "", port);
        if (port == "80" || port == "443" || proc ~ /sshd/) next;
        print local_addr; exit;
      }')"
    [[ -z "$unexpected" ]] && security_ok "Unexpected public TCP listeners" || security_error "unexpected public listener: $unexpected"
  else
    say "WARN: ss unavailable; listener audit skipped"
  fi

  current="$(current_image || true)"
  if [[ -n "$current" ]]; then
    cid="$(compose_with_image "$current" ps -q web 2>/dev/null || true)"
    if [[ -z "$cid" ]]; then
      security_error "web container not running"
    else
      inspect="$(docker inspect --format '{{.Config.User}}|{{.HostConfig.Privileged}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.SecurityOpt}}|{{json .Mounts}}' "$cid" 2>/dev/null || true)"
      IFS='|' read -r user privileged network_mode readonly_root security_opts mounts <<<"$inspect"
      [[ -n "$user" && "$user" != 0 && "$user" != root && "$user" != 0:* ]] \
        && security_ok "Container non-root user" || security_error "container user is root/unspecified: ${user:-<empty>}"
      [[ "$privileged" == false ]] && security_ok "Container privileged mode" || security_error "container is privileged"
      [[ "$network_mode" != host ]] && security_ok "Container host networking" || security_error "container uses host networking"
      [[ "$readonly_root" == true ]] && security_ok "Container read-only rootfs" || security_error "container rootfs is writable"
      [[ "$security_opts" == *no-new-privileges* ]] && security_ok "Container no-new-privileges" || security_error "no-new-privileges missing"
      [[ "$mounts" != *docker.sock* ]] && security_ok "Docker socket mount" || security_error "Docker socket is mounted into web container"
      envs="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$cid" 2>/dev/null || true)"
      grep -Fxq 'FLASK_DEBUG=0' <<<"$envs" && security_ok "Production debug" || security_error "FLASK_DEBUG is not explicitly 0"
      grep -Fxq 'FLASK_ENV=production' <<<"$envs" && security_ok "Production environment" || security_error "FLASK_ENV is not production"
      local secret_key secret_exposed=0
      # Each finding is key-only (for example, "RESTIC_PASSWORD is exposed");
      # never echo the corresponding environment entry or value.
      for secret_key in SECRET_KEY ADMIN_PASSWORD_HASH SMTP_PASSWORD EVENTS_REMOTE_PASSWORD RESTIC_PASSWORD; do
        if grep -Eq "^${secret_key}=.+$" <<<"$envs"; then
          security_error "$secret_key is exposed to the web container"
          secret_exposed=1
        fi
      done
      (( secret_exposed )) || security_ok "Direct container secret isolation"

      local file_key file_path
      while IFS='|' read -r file_key file_path; do
        grep -Fxq "${file_key}_FILE=${file_path}" <<<"$envs" \
          && security_ok "Container ${file_key}_FILE contract" \
          || security_error "${file_key}_FILE does not use the expected /run/secrets target"
      done <<'EOF_SECRET_FILES'
SECRET_KEY|/run/secrets/mifp_secret_key
ADMIN_PASSWORD_HASH|/run/secrets/mifp_admin_password_hash
SMTP_PASSWORD|/run/secrets/mifp_smtp_password
EVENTS_REMOTE_PASSWORD|/run/secrets/mifp_events_remote_password
EOF_SECRET_FILES

      local mount_lines mount_source mount_target _mount_type mount_problem=0
      local found_secret_key=0 found_admin_hash=0 found_smtp_password=0 found_events_password=0
      mount_lines="$(docker inspect --format '{{range .Mounts}}{{println .Source "|" .Destination "|" .Type}}{{end}}' "$cid" 2>/dev/null || true)"
      while IFS='|' read -r mount_source mount_target _mount_type; do
        [[ -n "$mount_target" ]] || continue
        case "$mount_target" in
          /run/secrets/mifp_secret_key) found_secret_key=1 ;;
          /run/secrets/mifp_admin_password_hash) found_admin_hash=1 ;;
          /run/secrets/mifp_smtp_password) found_smtp_password=1 ;;
          /run/secrets/mifp_events_remote_password) found_events_password=1 ;;
          /run/secrets/*)
            security_error "unexpected secret mount target in web container: $mount_target"
            mount_problem=1
            ;;
        esac
        case "$mount_target" in /run/secrets/mifp_*) continue ;; esac
        case "$mount_source|$mount_target" in
          "$DATA_DIR|/app/data"|"$events_root|/app/event-sites") continue ;;
        esac
        case "$mount_source" in
          "$ENV_FILE"|"$PUBLIC_CONFIG_FILE"|"$SECRETS_FILE"|"$DOCKER_CONFIG_FILE"|"$MAIL_RELAY_CONFIG"|"$MIFP_HOME"|"$MIFP_HOME"/*|"$CONFIG_DIR"|"$CONFIG_DIR"/*|"$EVENTS_PRIVATE_DIR"|"$EVENTS_PRIVATE_DIR"/*|"$backup_root"|"$backup_root"/*|/root|/root/*|/etc|/etc/*)
            security_error "sensitive host path is mounted at $mount_target"
            mount_problem=1
            ;;
        esac
      done <<<"$mount_lines"
      [[ "$found_secret_key$found_admin_hash$found_smtp_password$found_events_password" == 1111 ]] \
        && security_ok "Container /run/secrets mounts" \
        || security_error "one or more required /run/secrets mounts are missing"
      (( mount_problem )) || security_ok "Unexpected sensitive host mounts"
    fi
  else
    say "Container checks: NOT INITIALIZED"
  fi

  if ((failed)); then
    die "Security check found problems."
  fi
  if ((warnings)); then
    say "Security check: OK ($warnings warning(s); review WARN lines)"
  else
    say "Security check: OK"
  fi
}

do_ssh_harden() {
  local operator="" home akf p found=0 expected tmp
  while (($#)); do case "$1" in
    --operator) [[ $# -ge 2 ]] || die "--operator richiede un valore"; operator="$2"; shift 2 ;;
    --operator=*) operator="${1#*=}"; shift ;;
    -h|--help) say "Uso: mifpctl ssh-harden [--operator USER]"; return 0 ;;
    *) die "Uso: mifpctl ssh-harden [--operator USER]" ;;
  esac; done
  step "SSH hardening (staged, validato prima del reload)"

  has sshd || die "sshd non trovato: impossibile validare la configurazione."
  [[ -n "$operator" ]] || operator="${SUDO_USER:-}"
  [[ -n "$operator" && "$operator" != "root" ]] \
    || die "Specifica l'utente operatore con --operator USER (deve avere accesso via chiave)."
  id "$operator" >/dev/null 2>&1 || die "Utente inesistente: $operator"

  # 1. Refuse to disable password auth unless a usable public key already exists
  #    for the operator. This is the step that prevents a lockout.
  home="$(getent passwd "$operator" | cut -d: -f6)"
  akf="$(sshd -T 2>/dev/null | awk '$1 == "authorizedkeysfile" {print $2; exit}')"
  akf="${akf:-.ssh/authorized_keys}"
  for p in $akf; do
    p="${p//%h/$home}"; p="${p//%u/$operator}"
    [[ "$p" = /* ]] || p="$home/$p"
    if [[ -s "$p" ]] && grep -Eq '^(ssh-(rsa|ed25519)|ecdsa-sha2-|sk-)' "$p"; then found=1; break; fi
  done
  (( found )) || die "L'utente $operator non ha chiavi pubbliche autorizzate in $home: rifiuto di disabilitare l'autenticazione a password."

  sshd -t || die "sshd_config non valido PRIMA delle modifiche: risolvilo manualmente."

  # 2. Make sure the drop-in directory is actually Included (older images may
  #    not have it) and that our file wins over cloud-init drop-ins.
  install -d -m 0755 /etc/ssh/sshd_config.d
  if ! grep -Eq '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' /etc/ssh/sshd_config; then
    tmp="$(mktemp /etc/ssh/.sshd_config.XXXXXX)"
    { printf 'Include /etc/ssh/sshd_config.d/*.conf\n'; cat /etc/ssh/sshd_config; } > "$tmp"
    chmod --reference=/etc/ssh/sshd_config "$tmp"
    chown --reference=/etc/ssh/sshd_config "$tmp"
    mv -f "$tmp" /etc/ssh/sshd_config
  fi

  # 3. Write the drop-in. No port change, no AllowUsers, no source-IP match.
  cat > "$SSHD_DROPIN" <<'EOF_SSHD_HARDENING'
# Managed by `mifpctl ssh-harden`. Remove this file and reload sshd to revert.
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
MaxAuthTries 3
MaxSessions 4
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
ClientAliveInterval 300
ClientAliveCountMax 2
EOF_SSHD_HARDENING
  chown root:root "$SSHD_DROPIN"
  chmod 0644 "$SSHD_DROPIN"
  cp -a "$SSHD_DROPIN" "$SSHD_ROLLBACK"
  chmod 0600 "$SSHD_ROLLBACK"

  # 4. Validate, assert the EFFECTIVE values, and only then reload.
  if ! sshd -t; then
    rm -f "$SSHD_DROPIN"
    die "sshd -t fallito: drop-in rimosso, configurazione SSH invariata."
  fi
  expected="$(sshd -T 2>/dev/null)"
  if ! grep -qx 'passwordauthentication no' <<<"$expected"; then
    rm -f "$SSHD_DROPIN"
    die "PasswordAuthentication non è effettiva (un drop-in con nome precedente la sovrascrive). Hardening annullato."
  fi
  if ! grep -qxE 'permitrootlogin (no|prohibit-password|without-password)' <<<"$expected"; then
    rm -f "$SSHD_DROPIN"
    die "PermitRootLogin non è effettivo. Hardening annullato."
  fi
  if systemctl list-unit-files 'ssh.service' >/dev/null 2>&1; then
    systemctl reload ssh.service
  else
    systemctl reload sshd.service
  fi
  if ! systemctl is-active --quiet ssh.service && ! systemctl is-active --quiet sshd.service; then
    rm -f "$SSHD_DROPIN"
    systemctl restart ssh.service 2>/dev/null || systemctl restart sshd.service 2>/dev/null || true
    die "Il reload di sshd ha lasciato il servizio inattivo: hardening annullato."
  fi
  say "SSH hardening applicato: password disabilitate, root solo con chiave."
  say "Rollback: rm -f $SSHD_DROPIN && systemctl reload ssh"
  say "Le sessioni SSH già aperte restano attive: verifica con una nuova connessione prima di chiuderle."
}

do_ssh_rollback() {
  step "Ripristino della configurazione SSH"
  if [[ -f "$SSHD_DROPIN" ]]; then
    rm -f "$SSHD_DROPIN"
  fi
  sshd -t || die "sshd_config non valido dopo la rimozione del drop-in: intervento manuale richiesto."
  systemctl reload ssh.service 2>/dev/null || systemctl reload sshd.service
  say "Drop-in rimosso e sshd ricaricato."
}

do_admin() {
  local args=(admin --env-file "$ENV_FILE") current; shift || true
  while (($#)); do case "$1" in --username) [[ $# -ge 2 ]] || die "--username richiede un valore"; args+=(--username "$2"); shift 2 ;; --username=*) args+=("$1"); shift ;; *) die "Uso: mifpctl admin [--username NAME]" ;; esac; done
  python3 "$CONFIG_HELPER" "${args[@]}"
  config_cli import-admin
  current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true
}

do_configure() {
  local current; shift || true
  config_cli configure "$@"
  apply_host_configuration
  sync_backup_timer || die "Configurazione salvata, ma lo stato del timer backup non è stato applicato."
  current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true
}

do_config_set() {
  local current
  [[ $# -eq 2 ]] || die "Uso: mifpctl config-set KEY VALUE"
  config_cli set "$1" "$2"
  case "${1^^}" in
    DOMAIN|WWW_DOMAIN|ENVIRONMENT|EVENTS_PUBLISH_BACKEND|EVENTS_PUBLIC_BASE_URL|EVENTS_LOCAL_ROOT|EVENTS_REMOTE_PROTOCOL|EVENTS_REMOTE_HOST|EVENTS_REMOTE_PORT|EVENTS_REMOTE_USER|EVENTS_REMOTE_ROOT|EVENTS_REMOTE_TIMEOUT|MAIL_PROVIDER|SMTP_HOST|SMTP_PORT|SMTP_SECURITY|SMTP_USERNAME|SMTP_FROM_ADDRESS|SMTP_FROM_NAME|MAIL_TO)
      apply_host_configuration
      current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true ;;
    BACKUP_ENABLED) sync_backup_timer || die "Valore salvato, ma lo stato del timer backup non è stato applicato." ;;
  esac
}

do_config_unset() {
  local current
  [[ $# -eq 1 ]] || die "Uso: mifpctl config-unset KEY"
  config_cli unset "$1"
  case "${1^^}" in
    DOMAIN|WWW_DOMAIN|ENVIRONMENT|EVENTS_PUBLISH_BACKEND|EVENTS_PUBLIC_BASE_URL|EVENTS_LOCAL_ROOT|EVENTS_REMOTE_PROTOCOL|EVENTS_REMOTE_HOST|EVENTS_REMOTE_PORT|EVENTS_REMOTE_USER|EVENTS_REMOTE_ROOT|EVENTS_REMOTE_TIMEOUT|MAIL_PROVIDER|SMTP_HOST|SMTP_PORT|SMTP_SECURITY|SMTP_USERNAME|SMTP_FROM_ADDRESS|SMTP_FROM_NAME|MAIL_TO|SMTP_PASSWORD)
      apply_host_configuration
      current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true ;;
    BACKUP_ENABLED) sync_backup_timer || die "Valore salvato, ma lo stato del timer backup non è stato applicato." ;;
  esac
}

command="${1:-status}"
case "$command" in
  init|check|update|first-deploy|deploy|init-db|upgrade-db|restore-db|restore-snapshot|events-republish-all|events-php-enable|events-php-disable|rollback-upgrade|rollback|--rollback|restart|stop|admin|admin-reset-password|configure|config-set|config-unset|fix-permissions|security-check|ssh-harden|ssh-rollback)
    mkdir -p "$(dirname "$LOCK_FILE")"; exec 9>"$LOCK_FILE"; flock -n 9 || die "Un'altra operazione MIFP è già in corso." ;;
esac
case "$command" in
  registry-login) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl registry-login"; do_registry_login ;;
  registry-check) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl registry-check"; do_registry_check ;;
  init) shift; do_init "$@" ;;
  check) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl check"; do_check ;;
  update-check) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl update-check"; do_update_check ;;
  update) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl update"; do_update ;;
  version) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl version"; say "MIFP host tools"; say "Version:         $MIFPCTL_VERSION"; say "Deploy contract: $DEPLOY_CONTRACT_VERSION (legacy default: 1)" ;;
  first-deploy) shift; do_first_deploy "$@" ;;
  deploy) shift; do_deploy "$@" ;;
  init-db) shift; do_init_db "$@" ;;
  upgrade-db) shift; do_upgrade_db "$@" ;;
  restore-db) shift; do_restore_db "$@" ;;
  restore-snapshot) shift; do_restore_snapshot "$@" ;;
  events-republish-all) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl events-republish-all"; do_events_republish_all ;;
  events-php-list) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl events-php-list"; do_events_php_list ;;
  events-php-enable) shift; [[ $# -eq 1 ]] || die "Uso: mifpctl events-php-enable EVENTO/regform"; do_events_php_enable "$1" ;;
  events-php-disable) shift; [[ $# -eq 1 ]] || die "Uso: mifpctl events-php-disable EVENTO/regform"; do_events_php_disable "$1" ;;
  rollback-upgrade) do_rollback_upgrade ;;
  rollback|--rollback) do_rollback ;;
  restart) do_restart ;;
  stop) do_stop ;;
  status) ensure_tools; do_status ;;
  logs) ensure_tools; do_logs ;;
  backup) do_backup ;;
  doctor) do_doctor ;;
  security-check) do_security_check ;;
  ssh-harden) shift; do_ssh_harden "$@" ;;
  ssh-rollback) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl ssh-rollback"; do_ssh_rollback ;;
  fix-permissions) do_fix_permissions ;;
  admin) do_admin "$@" ;;
  admin-reset-password) do_admin "$@" ;;
  configure) do_configure "$@" ;;
  config-show) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl config-show"; config_cli show ;;
  config-set) shift; do_config_set "$@" ;;
  config-unset) shift; do_config_unset "$@" ;;
  config-check|production-check) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl config-check"; do_config_check ;;
  -h|--help|help) usage ;;
  *) usage >&2; die "Comando sconosciuto: $command" ;;
esac

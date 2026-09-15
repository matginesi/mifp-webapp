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
DOCKER_CONFIG_FILE="${MIFP_DOCKER_CONFIG_FILE:-/root/.docker/config.json}"
COMPOSE_FILE="$MIFP_HOME/compose.yaml"
RELEASE_FILE="$MIFP_HOME/release.env"
UPGRADE_FILE="$MIFP_HOME/upgrade.env"
DATA_DIR="$MIFP_HOME/data"
EVENTS_DIR="$MIFP_HOME/events"
EVENTS_PREVIOUS_DIR="$MIFP_HOME/events.previous"
EVENTS_PRIVATE_DIR="$MIFP_HOME/events-private"
EVENTS_PUBLIC_GROUP="${MIFP_EVENTS_PUBLIC_GROUP:-mifp-events-public}"
EVENTS_PHP_USER="${MIFP_EVENTS_PHP_USER:-mifp-events}"
EVENTS_PHP_STATE="$MIFP_HOME/events-php-enabled.txt"
EVENTS_PHP_INCLUDE="${MIFP_EVENTS_PHP_INCLUDE:-/etc/caddy/mifp-events-php.caddy}"
EVENTS_PHP_SOCKET="${MIFP_EVENTS_PHP_SOCKET:-/run/php/mifp-events.sock}"
CADDY_CONFIG="${MIFP_CADDY_CONFIG:-/etc/caddy/Caddyfile}"
CADDY_TEMPLATE="$MIFP_HOME/Caddyfile.example"
LOCAL_HOSTS_HELPER="$MIFP_HOME/local-hosts.sh"
PHP_FPM_SERVICE_FILE="$MIFP_HOME/php-fpm.service"
BACKUP_SCRIPT="$MIFP_HOME/backup.sh"
LOCK_FILE="${MIFP_DEPLOY_LOCK_FILE:-/run/lock/mifp-deploy.lock}"
RUNTIME_UID="${MIFP_RUNTIME_UID:-10001}"
RUNTIME_GID="${MIFP_RUNTIME_GID:-10001}"
COMPOSE_BASE=(docker compose --project-name mifp --project-directory "$MIFP_HOME" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
MIFP production operator

Uso normale:
  sudo mifpctl registry-login             login interattivo a GHCR (PAT read:packages)
  sudo mifpctl configure [--section NAME] wizard progressivo (web/mail/registry/backup)
  sudo mifpctl config-show                riepilogo senza mostrare segreti
  sudo mifpctl config-set KEY VALUE       aggiorna un valore non segreto
  sudo mifpctl config-unset KEY           rimuove un valore
  sudo mifpctl config-check               readiness read-only (exit 0/1)
  sudo mifpctl registry-check             verifica accesso al manifest GHCR
  sudo mifpctl init                       primo avvio da :latest, fissato subito a digest
  sudo mifpctl deploy sha-<commit>        nuova versione applicazione; il DB non viene modificato
  sudo mifpctl status
  sudo mifpctl logs
  sudo mifpctl rollback                   torna alla release precedente (anche offline se locale)
  sudo mifpctl backup                     snapshot point-in-time DB + file/eventi
  sudo mifpctl doctor                     diagnostica completa
  sudo mifpctl fix-permissions            corregge ownership dati solo su richiesta
  sudo mifpctl events-import /backup/root importa/sostituisce events.mifp.eu in modo atomico
  sudo mifpctl events-rollback            scambia events/ con l'import precedente
  sudo mifpctl events-php-list            mostra i path autorizzati a eseguire PHP

PHP conferenze (deny-by-default):
  sudo mifpctl events-php-enable PLMCN-2027/regform
  sudo mifpctl events-php-disable PLMCN-2027/regform

Manutenzione rara:
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

Solo init può usare :latest, esclusivamente come selector iniziale. Lo stato
persistente contiene sempre un digest OCI immutabile.
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

apply_host_configuration() {
  local domain www_domain events_domain tls_directive="" tmp
  cleanup_host_config_tmp() { [[ -z "${tmp:-}" ]] || rm -f -- "$tmp"; }
  trap cleanup_host_config_tmp EXIT
  domain="$(config_cli get DOMAIN)"
  if [[ -z "$domain" ]]; then
    bash "$LOCAL_HOSTS_HELPER" --clear "${MIFP_HOSTS_FILE:-/etc/hosts}"
    tmp="$(mktemp "$(dirname "$CADDY_CONFIG")/.mifp-caddy.XXXXXX")"
    printf '%s\n' ':80 {' '    respond "MIFP host ready; run sudo mifpctl configure" 503' '}' >"$tmp"
  else
    www_domain="$(config_cli get WWW_DOMAIN)"
    events_domain="$(config_cli get EVENTS_DOMAIN)"
    [[ -n "$www_domain" && -n "$events_domain" ]] || die "Domini incompleti; esegui sudo mifpctl configure --section web."
    bash "$LOCAL_HOSTS_HELPER" "$domain" "${MIFP_HOSTS_FILE:-/etc/hosts}" "$www_domain" "$events_domain"
    [[ "$domain" != *.home.arpa ]] || tls_directive="tls internal"
    tmp="$(mktemp "$(dirname "$CADDY_CONFIG")/.mifp-caddy.XXXXXX")"
    sed -e "s/__MIFP_DOMAIN__/$domain/g" \
      -e "s/__MIFP_WWW_DOMAIN__/$www_domain/g" \
      -e "s/__MIFP_EVENTS_DOMAIN__/$events_domain/g" \
      -e "s/__MIFP_TLS__/$tls_directive/g" "$CADDY_TEMPLATE" >"$tmp"
  fi
  chown root:caddy "$tmp"; chmod 0644 "$tmp"
  caddy fmt --overwrite "$tmp" >/dev/null
  caddy validate --config "$tmp" --adapter caddyfile >/dev/null
  mv -f "$tmp" "$CADDY_CONFIG"
  tmp=""
  trap - EXIT
  systemctl reload caddy.service 2>/dev/null || systemctl restart caddy.service
  if [[ "$domain" == *.home.arpa ]]; then
    caddy trust --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
      || say "WARN: CA locale Caddy non installata nel trust store della VPS."
    local lan_ip
    lan_ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | awk '/^[0-9]+\./ && $0 !~ /^127\./ {print}' | sort -u | awk 'NR==1 {value=$0} NR==2 {value=""} END {print value}')"
    if [[ -n "$lan_ip" ]]; then
      say "Workstation /etc/hosts: $lan_ip $domain $www_domain $events_domain"
    else
      say "Workstation /etc/hosts: individua l'IP LAN con 'hostname -I', poi mappa $domain $www_domain $events_domain"
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

compose_with_image() {
  local image="$1"; shift
  MIFP_IMAGE="$image" MIFP_DATA_DIR="$DATA_DIR" "${COMPOSE_BASE[@]}" "$@"
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

pull_and_pin() {
  local reference="$1" allow_latest="${2:-0}" image repo digest pull_output
  image="$(resolve_image "$reference")"
  repo="$(image_repository)"
  if [[ "$allow_latest" == 1 && "$image" == "$repo:latest" ]]; then
    :
  else
    validate_release_image "$image"
  fi
  check_free_space
  if pull_output="$(docker pull "$image" 2>&1)"; then
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
    rm -f -- "$snapshot"
    die "Impossibile creare snapshot SQLite coerente da $source"
  fi
  [[ "$(sqlite3 -readonly "$snapshot" 'PRAGMA quick_check; PRAGMA foreign_key_check;')" == "ok" ]] || {
    rm -f -- "$snapshot"
    die "Snapshot SQLite di preflight non valida: $source"
  }
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
    rm -f -- "$snapshot"
    return 0
  fi
  rm -f -- "$snapshot"
  die "La release $image non è compatibile con il database. Nessuno switch eseguito."
}

wait_ready() {
  local attempts="${MIFP_READY_ATTEMPTS:-${1:-60}}" i
  [[ "$attempts" =~ ^[1-9][0-9]*$ ]] || die "MIFP_READY_ATTEMPTS non valido: $attempts"
  for ((i=1; i<=attempts; i++)); do
    curl -fsS --max-time 2 http://127.0.0.1:8000/ready >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

show_release_logs() { local image="$1"; compose_with_image "$image" ps >&2 || true; compose_with_image "$image" logs --tail 300 web >&2 || true; }
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
  if compose_with_image "$image" up -d --remove-orphans; then
    wait_ready 60 && return 0
  fi
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
  local username token=""
  has docker || die "Docker non disponibile. Riesegui bootstrap-vps.sh."
  [[ -t 0 ]] || die "registry-login richiede un terminale interattivo."
  printf 'GitHub username: ' >&2
  IFS= read -r username
  [[ "$username" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}$ ]] \
    || die "GitHub username non valido."
  printf 'GitHub PAT classic (scope read:packages): ' >&2
  IFS= read -r -s token
  printf '\n' >&2
  [[ -n "$token" ]] || die "Token vuoto."
  if printf '%s\n' "$token" | docker login ghcr.io --username "$username" --password-stdin; then
    token=""; unset token
    config_cli set REGISTRY_USERNAME "$username" >/dev/null
    say "GHCR login successful."
    return 0
  fi
  token=""; unset token
  die "GHCR login failed. Verify username, PAT classic and read:packages scope."
}

do_registry_check() {
  local repository
  has docker || die "Docker non disponibile. Riesegui bootstrap-vps.sh."
  docker info >/dev/null 2>&1 || die "Docker daemon non raggiungibile."
  repository="$(image_repository)"
  docker manifest inspect "$repository:latest" >/dev/null 2>&1 \
    || die "GHCR non accessibile o manifest :latest assente. Esegui: sudo mifpctl registry-login"
  say "Registry: OK ($repository:latest leggibile; nessuna release modificata)"
}

do_config_check() {
  config_cli check || return 1
  ensure_tools
  systemctl is-active caddy.service >/dev/null 2>&1 || die "Caddy non è attivo."
  caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
    || die "Caddyfile non valido."
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
  init_db_with_image "$image"
  validate_database_host
  preflight_image_db "$image"
  step "Prima release $image"
  if ! activate_release "$image" ""; then
    compose_with_image "$image" down >/dev/null 2>&1 || true
    rm -f -- "$db" "$db-wal" "$db-shm"
    die "Init fallito; nessuna release o database iniziale è stato registrato."
  fi
  if [[ "$(config_cli get BACKUP_ENABLED)" == "false" ]]; then
    systemctl disable --now mifp-backup.timer >/dev/null 2>&1 || true
  elif ! systemctl enable --now mifp-backup.timer; then
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
  preflight_image_db "$image"
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
  [[ -e "$DATA_DIR/mifp.db" ]] || init_db_with_image "$image"
  validate_database_host
  preflight_image_db "$image"
  step "Primo deploy $image"
  activate_release "$image" "$old_current" || die "Primo deploy fallito; nessuna release nuova registrata."
  if [[ "$image" == "$old_current" ]]; then write_release_state "$image" "$old_previous"; else write_release_state "$image" "$old_current"; fi
  cleanup_old_release_images "$image" "$(previous_image || true)"
  systemctl enable --now mifp-backup.timer \
    || die "Applicazione avviata, ma il timer backup non è stato abilitato. Esegui: sudo systemctl enable --now mifp-backup.timer"
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
  sqlite3 "$live" ".backup '$saved'"
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
if manifest.get("format") != "mifp-host-snapshot" or version not in {1, 2}:
    raise SystemExit("unsupported snapshot manifest format")
files = manifest.get("files")
if not isinstance(files, dict) or not files:
    raise SystemExit("snapshot manifest has no files")

expected: set[str] = {"mifp.db"}
directories = ["assets", "conferences", "config"]
if version >= 2:
    directories.extend(["events", "events-private"])
    state = root / "events-php-enabled.txt"
    if not state.is_file() or state.is_symlink():
        raise SystemExit("missing or unsafe file: events-php-enabled.txt")
    expected.add("events-php-enabled.txt")
for dirname in directories:
    directory = root / dirname
    if not directory.is_dir() or directory.is_symlink():
        raise SystemExit(f"missing or unsafe directory: {dirname}/")
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise SystemExit(f"symbolic link rejected: {path.relative_to(root)}")
        if path.is_file():
            expected.add(path.relative_to(root).as_posix())

if set(files) != expected:
    missing = sorted(expected - set(files))
    extra = sorted(set(files) - expected)
    raise SystemExit(f"snapshot file set mismatch; missing={missing[:5]} extra={extra[:5]}")

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

restore_snapshot_files() {
  local snapshot="$1" name version
  for name in assets conferences config; do
    [[ -d "$snapshot/$name" && ! -L "$snapshot/$name" ]] || die "Snapshot incompleta o non sicura: $name/"
    install -d -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0750 "$DATA_DIR/$name"
    rsync -a --delete --chown="$RUNTIME_UID:$RUNTIME_GID" "$snapshot/$name/" "$DATA_DIR/$name/"
  done

  version="$(snapshot_manifest_version "$snapshot")"
  if (( version >= 2 )); then
    [[ -d "$snapshot/events" && ! -L "$snapshot/events" ]] || die "Snapshot incompleta o non sicura: events/"
    [[ -d "$snapshot/events-private" && ! -L "$snapshot/events-private" ]] || die "Snapshot incompleta o non sicura: events-private/"
    install -d -o root -g "$EVENTS_PUBLIC_GROUP" -m 0750 "$EVENTS_DIR"
    rsync -a --delete --chown="root:$EVENTS_PUBLIC_GROUP" "$snapshot/events/" "$EVENTS_DIR/"
    install -d -o "$EVENTS_PHP_USER" -g "$EVENTS_PHP_USER" -m 0700 "$EVENTS_PRIVATE_DIR"
    rsync -a --delete --chown="$EVENTS_PHP_USER:$EVENTS_PHP_USER" "$snapshot/events-private/" "$EVENTS_PRIVATE_DIR/"
    install -o root -g root -m 0644 "$snapshot/events-php-enabled.txt" "$EVENTS_PHP_STATE"
    install_events_php_include
  fi
}

do_restore_snapshot() {
  local snapshot="${1:-}" current previous saved_root backup_root php_service=""
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
  MIFP_OPERATION_LOCK_HELD=1 "$BACKUP_SCRIPT" >/dev/null
  saved_root="$(readlink -f -- "$backup_root/snapshots/latest")"
  [[ -d "$saved_root" && "$saved_root" != "$snapshot" ]] || die "Impossibile creare snapshot di sicurezza pre-restore."
  verify_snapshot_integrity "$saved_root" || die "Snapshot di sicurezza pre-restore non valida: $saved_root"

  step "Fermo MIFP e ripristino snapshot completa"
  php_service="$(events_php_service || true)"
  if [[ -n "$php_service" ]] && systemctl is-active "$php_service" >/dev/null 2>&1; then
    systemctl stop "$php_service" || die "Impossibile fermare $php_service prima del restore."
    # If an unexpected set -e exit occurs below, never leave FPM stopped.
    trap 'if [[ -n "${php_service:-}" ]]; then systemctl start "$php_service" >/dev/null 2>&1 || true; fi' EXIT
  else
    php_service=""
  fi
  stop_release_for_db_swap "$current"
  install_database_candidate "$snapshot/mifp.db"
  restore_snapshot_files "$snapshot"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    if [[ -n "$php_service" ]]; then
      systemctl start "$php_service" || die "Restore completato, ma $php_service non è ripartito."
      php_service=""; trap - EXIT
    fi
    say "Restore snapshot completato. Snapshot precedente: $saved_root"
    return 0
  fi

  step "Restore snapshot fallito: ripristino la fotografia precedente"
  compose_with_image "$current" down >/dev/null 2>&1 || true
  install_database_candidate "$saved_root/mifp.db"
  restore_snapshot_files "$saved_root"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    if [[ -n "$php_service" ]]; then
      systemctl start "$php_service" || die "Stato precedente ripristinato, ma $php_service non è ripartito."
      php_service=""; trap - EXIT
    fi
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

normalize_events_prefix() {
  local prefix="${1:-}"
  prefix="${prefix#/}"; prefix="${prefix%/}"
  [[ -n "$prefix" ]] || die "Path conferenza vuoto."
  [[ "$prefix" =~ ^[A-Za-z0-9._/-]+$ ]] || die "Path conferenza non valido: $prefix"
  [[ "/$prefix/" != *"/../"* && "/$prefix/" != *"/./"* && "$prefix" != *"//"* ]] \
    || die "Path conferenza non sicuro: $prefix"
  printf '%s' "$prefix"
}

render_events_php_include() {
  local target="$1" prefix i=0
  {
    printf '%s\n' '# Generated by mifpctl. Do not edit by hand.'
    printf '%s\n' '# Paths not listed here cannot execute PHP.'
    if [[ -f "$EVENTS_PHP_STATE" ]]; then
      while IFS= read -r prefix; do
        [[ -n "$prefix" ]] || continue
        i=$((i + 1))
        printf '@mifp_events_php_%d path /%s /%s/*\n' "$i" "$prefix" "$prefix"
        printf 'php_fastcgi @mifp_events_php_%d unix/%s\n\n' "$i" "$EVENTS_PHP_SOCKET"
      done < "$EVENTS_PHP_STATE"
    fi
  } > "$target"
}

install_events_php_include() {
  local candidate backup include_dir had_old=0
  include_dir="$(dirname "$EVENTS_PHP_INCLUDE")"
  [[ -d "$include_dir" ]] || die "Directory Caddy mancante: $include_dir"
  candidate="$(mktemp "$include_dir/.mifp-events-php.caddy.XXXXXX")"
  backup="$(mktemp "$include_dir/.mifp-events-php.previous.XXXXXX")"
  render_events_php_include "$candidate"
  chown root:caddy "$candidate"; chmod 0644 "$candidate"
  if [[ -f "$EVENTS_PHP_INCLUDE" ]]; then
    cp -a "$EVENTS_PHP_INCLUDE" "$backup"
    had_old=1
  fi
  mv -f "$candidate" "$EVENTS_PHP_INCLUDE"
  if ! caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null; then
    if ((had_old)); then mv -f "$backup" "$EVENTS_PHP_INCLUDE"; else rm -f "$EVENTS_PHP_INCLUDE"; fi
    die "La configurazione PHP generata non supera caddy validate; configurazione precedente ripristinata."
  fi
  rm -f "$backup"
  systemctl reload caddy.service || die "Caddy non ha accettato il reload."
}

do_events_php_list() {
  if [[ ! -s "$EVENTS_PHP_STATE" ]]; then
    say "PHP eventi: nessun path abilitato (deny-by-default)."
    return 0
  fi
  say "PHP eventi abilitato esclusivamente per:"
  sed 's/^/  - /' "$EVENTS_PHP_STATE"
}

disable_all_events_php() {
  # A full document-root replacement must never inherit executable PHP paths
  # from the tree it replaces.  Re-enable only after inspecting the new files.
  install -o root -g root -m 0644 /dev/null "$EVENTS_PHP_STATE"
  install_events_php_include
}

do_events_php_enable() {
  local prefix service tmp
  prefix="$(normalize_events_prefix "${1:-}")"
  [[ -d "$EVENTS_DIR/$prefix" && ! -L "$EVENTS_DIR/$prefix" ]] || die "Directory pubblica inesistente: $EVENTS_DIR/$prefix"
  find "$EVENTS_DIR/$prefix" -type l -print -quit | grep -q . && die "Il path contiene symlink; PHP non viene abilitato."
  find "$EVENTS_DIR/$prefix" -type f \( -name '*.php' -o -name '*.phtml' \) -print -quit | grep -q . \
    || die "Nessun file PHP trovato sotto $EVENTS_DIR/$prefix"
  service="$(events_php_service || true)"
  [[ -n "$service" ]] || die "PHP-FPM MIFP non configurato. Riesegui deploy/bootstrap-vps.sh."
  systemctl enable --now "$service"
  [[ -S "$EVENTS_PHP_SOCKET" ]] || die "Socket PHP-FPM non disponibile: $EVENTS_PHP_SOCKET"

  touch "$EVENTS_PHP_STATE"; chown root:root "$EVENTS_PHP_STATE"; chmod 0644 "$EVENTS_PHP_STATE"
  if ! grep -Fxq -- "$prefix" "$EVENTS_PHP_STATE"; then
    tmp="$(mktemp "$MIFP_HOME/.events-php-enabled.XXXXXX")"
    { cat "$EVENTS_PHP_STATE"; printf '%s\n' "$prefix"; } | LC_ALL=C sort -u > "$tmp"
    chmod 0644 "$tmp"; chown root:root "$tmp"; mv -f "$tmp" "$EVENTS_PHP_STATE"
  fi
  install_events_php_include
  say "PHP abilitato solo per https://$(config_cli get EVENTS_DOMAIN)/$prefix/"
}

do_events_php_disable() {
  local prefix tmp
  prefix="$(normalize_events_prefix "${1:-}")"
  if [[ ! -f "$EVENTS_PHP_STATE" ]]; then
    say "PHP era già disabilitato per $prefix."
    return 0
  fi
  tmp="$(mktemp "$MIFP_HOME/.events-php-enabled.XXXXXX")"
  grep -Fxv -- "$prefix" "$EVENTS_PHP_STATE" > "$tmp" || true
  chmod 0644 "$tmp"; chown root:root "$tmp"; mv -f "$tmp" "$EVENTS_PHP_STATE"
  install_events_php_include
  say "PHP disabilitato per $prefix."
}

do_events_import() {
  local source="${1:-}" stage bad
  [[ -n "$source" ]] || die "Uso: mifpctl events-import /path/document-root"
  source="$(readlink -f -- "$source")"
  [[ -d "$source" && ! -L "$source" ]] || die "Directory sorgente non valida: $source"
  [[ "$source" != "$EVENTS_DIR" && "$source" != "$EVENTS_DIR"/* ]] || die "La sorgente non può essere dentro $EVENTS_DIR."
  has rsync || die "Comando richiesto non disponibile: rsync"
  bad="$(find "$source" -type l -print -quit)"
  [[ -z "$bad" ]] || die "Backup eventi non sicuro: symlink trovato: $bad"

  stage="$MIFP_HOME/.events-stage-$$"
  rm -rf -- "$stage"
  install -d -o root -g "$EVENTS_PUBLIC_GROUP" -m 0750 "$stage"
  rsync -a --delete "$source/" "$stage/"
  chown -R --no-dereference root:"$EVENTS_PUBLIC_GROUP" "$stage"
  find "$stage" -type d -exec chmod 0750 {} +
  find "$stage" -type f -exec chmod 0640 {} +

  # Fail closed before replacing the document root: previously enabled PHP
  # prefixes may point at different code after this import.
  disable_all_events_php

  # Keep exactly one local rollback tree.  The operation stays on one filesystem,
  # so the public root switch itself is an atomic rename.
  rm -rf -- "$EVENTS_PREVIOUS_DIR"
  if [[ -d "$EVENTS_DIR" ]]; then mv "$EVENTS_DIR" "$EVENTS_PREVIOUS_DIR"; fi
  if ! mv "$stage" "$EVENTS_DIR"; then
    [[ ! -d "$EVENTS_PREVIOUS_DIR" ]] || mv "$EVENTS_PREVIOUS_DIR" "$EVENTS_DIR"
    die "Import eventi fallito; tree precedente ripristinato."
  fi
  say "Eventi pubblicati da $source -> $EVENTS_DIR"
  if [[ -d "$EVENTS_PREVIOUS_DIR" ]]; then
    say "Rollback locale disponibile: sudo mifpctl events-rollback"
  fi
}

do_events_rollback() {
  local swap="$MIFP_HOME/.events-swap-$$"
  [[ -d "$EVENTS_PREVIOUS_DIR" && ! -L "$EVENTS_PREVIOUS_DIR" ]] || die "Nessun import eventi precedente disponibile."
  [[ -d "$EVENTS_DIR" && ! -L "$EVENTS_DIR" ]] || die "Tree eventi corrente non valido."
  disable_all_events_php
  mv "$EVENTS_DIR" "$swap"
  if ! mv "$EVENTS_PREVIOUS_DIR" "$EVENTS_DIR"; then
    mv "$swap" "$EVENTS_DIR"
    die "Rollback eventi fallito; tree corrente ripristinato."
  fi
  mv "$swap" "$EVENTS_PREVIOUS_DIR"
  say "Rollback eventi completato."
}

do_status() {
  local current previous php_service events_domain events_count=0
  current="$(current_image || true)"; previous="$(previous_image || true)"
  events_domain="$(config_cli get EVENTS_DOMAIN)"
  say "Current image:  ${current:-none}"; say "Previous image: ${previous:-none}"
  [[ -n "$current" ]] && compose_with_image "$current" ps || docker ps --filter label=com.docker.compose.project=mifp || true
  systemctl is-active caddy >/dev/null 2>&1 && say "Caddy: attivo" || say "Caddy: NON attivo"
  if [[ -d "$EVENTS_DIR" ]]; then
    events_count="$(find "$EVENTS_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)"
    say "${events_domain:-events}: $events_count directory pubbliche in $EVENTS_DIR"
  else
    say "${events_domain:-events}: directory pubblica mancante"
  fi
  php_service="$(events_php_service || true)"
  if [[ -n "$php_service" ]] && systemctl is-active "$php_service" >/dev/null 2>&1; then
    say "PHP-FPM eventi: attivo ($php_service), esecuzione pubblica solo su allow-list"
  else
    say "PHP-FPM eventi: non attivo/non configurato"
  fi
  do_events_php_list
}

do_logs() { local current; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; compose_with_image "$current" logs --tail 300 -f web; }
do_stop() { local current; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; compose_with_image "$current" down; }
do_restart() { local current; validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; preflight_image_db "$current"; activate_release "$current" "" || die "Restart fallito."; }

do_backup() { [[ -x "$BACKUP_SCRIPT" ]] || die "Manca $BACKUP_SCRIPT"; "$BACKUP_SCRIPT"; }

do_doctor() {
  local failed=0 current db="$DATA_DIR/mifp.db" domain events_domain php_service
  local min_mb available_kb available_mb target db_check
  step "Stato host"
  if config_cli validate; then say "Configuration: OK"; else say "Configuration: ERROR"; failed=1; fi
  docker info >/dev/null 2>&1 && say "Docker: OK" || { say "Docker: ERROR"; failed=1; }
  docker compose version >/dev/null 2>&1 && say "Compose: OK" || { say "Compose: ERROR"; failed=1; }
  systemctl is-active caddy >/dev/null 2>&1 && say "Caddy: OK" || { say "Caddy: ERROR"; failed=1; }
  caddy validate --config "$CADDY_CONFIG" --adapter caddyfile >/dev/null 2>&1 \
    && say "Caddyfile: OK" || { say "Caddyfile: ERROR"; failed=1; }

  min_mb="$(env_value MIFP_DEPLOY_MIN_FREE_MB || true)"; min_mb="${min_mb:-2048}"
  target="$DATA_DIR"; [[ -d /var/lib/docker ]] && target=/var/lib/docker
  available_kb="$(df -Pk "$target" | awk 'NR==2 {print $4}')"
  available_mb=$((available_kb / 1024))
  if (( available_mb >= min_mb )); then say "Storage: OK (${available_mb} MB free)"; else say "Storage: ERROR (${available_mb} MB < ${min_mb} MB)"; failed=1; fi

  if [[ -d "$EVENTS_DIR" && ! -L "$EVENTS_DIR" && -d "$EVENTS_PRIVATE_DIR" && ! -L "$EVENTS_PRIVATE_DIR" ]]; then
    say "Events filesystem: OK"
  else
    say "Events filesystem: ERROR"; failed=1
  fi
  php_service="$(events_php_service || true)"
  if [[ -n "$php_service" ]] && systemctl is-active "$php_service" >/dev/null 2>&1 && [[ -S "$EVENTS_PHP_SOCKET" ]]; then
    say "PHP-FPM: OK ($php_service)"
  else
    say "PHP-FPM: ERROR"; failed=1
  fi
  [[ -f "$EVENTS_PHP_INCLUDE" && ! -L "$EVENTS_PHP_INCLUDE" ]] \
    && say "PHP allow-list: OK" || { say "PHP allow-list: ERROR"; failed=1; }

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
  events_domain="$(config_cli get EVENTS_DOMAIN)"
  if [[ -n "$current" && -n "$domain" ]]; then
    curl -fsS --max-time 5 "https://$domain/health" >/dev/null 2>&1 \
      && say "HTTPS application: OK" || { say "HTTPS application: ERROR"; failed=1; }
  fi
  if [[ -n "$events_domain" ]]; then
    curl -fsS --max-time 5 "https://$events_domain/.mifp-events-health" >/dev/null 2>&1 \
      && say "HTTPS events: OK" || { say "HTTPS events: ERROR"; failed=1; }
  fi
  ((failed == 0)) || die "Doctor found errors."
  say "Doctor: OK"
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
  current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true
}

do_config_set() {
  [[ $# -eq 2 ]] || die "Uso: mifpctl config-set KEY VALUE"
  config_cli set "$1" "$2"
  case "${1^^}" in DOMAIN|WWW_DOMAIN|EVENTS_DOMAIN|ENVIRONMENT) apply_host_configuration ;; esac
}

do_config_unset() {
  [[ $# -eq 1 ]] || die "Uso: mifpctl config-unset KEY"
  config_cli unset "$1"
  case "${1^^}" in DOMAIN|WWW_DOMAIN|EVENTS_DOMAIN|ENVIRONMENT) apply_host_configuration ;; esac
}

command="${1:-status}"
case "$command" in
  init|first-deploy|deploy|init-db|upgrade-db|restore-db|restore-snapshot|rollback-upgrade|rollback|--rollback|restart|stop|admin|admin-reset-password|configure|config-set|config-unset|fix-permissions|events-import|events-rollback|events-php-enable|events-php-disable)
    mkdir -p "$(dirname "$LOCK_FILE")"; exec 9>"$LOCK_FILE"; flock -n 9 || die "Un'altra operazione MIFP è già in corso." ;;
esac
case "$command" in
  registry-login) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl registry-login"; do_registry_login ;;
  registry-check) shift; [[ $# -eq 0 ]] || die "Uso: mifpctl registry-check"; do_registry_check ;;
  init) shift; do_init "$@" ;;
  first-deploy) shift; do_first_deploy "$@" ;;
  deploy) shift; do_deploy "$@" ;;
  init-db) shift; do_init_db "$@" ;;
  upgrade-db) shift; do_upgrade_db "$@" ;;
  restore-db) shift; do_restore_db "$@" ;;
  restore-snapshot) shift; do_restore_snapshot "$@" ;;
  rollback-upgrade) do_rollback_upgrade ;;
  rollback|--rollback) do_rollback ;;
  restart) do_restart ;;
  stop) do_stop ;;
  status) ensure_tools; do_status ;;
  logs) ensure_tools; do_logs ;;
  backup) do_backup ;;
  doctor) do_doctor ;;
  fix-permissions) do_fix_permissions ;;
  events-import) shift; do_events_import "$@" ;;
  events-rollback) do_events_rollback ;;
  events-php-list) do_events_php_list ;;
  events-php-enable) shift; do_events_php_enable "$@" ;;
  events-php-disable) shift; do_events_php_disable "$@" ;;
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

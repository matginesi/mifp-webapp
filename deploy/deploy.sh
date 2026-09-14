#!/usr/bin/env bash
set -Eeuo pipefail

# MIFP production operator. Installed as /usr/local/sbin/mifpctl.
MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
ENV_FILE="$MIFP_HOME/.env"
ENV_EXAMPLE="$MIFP_HOME/.env.example"
CONFIG_HELPER="$MIFP_HOME/configure.py"
COMPOSE_FILE="$MIFP_HOME/compose.yaml"
RELEASE_FILE="$MIFP_HOME/release.env"
UPGRADE_FILE="$MIFP_HOME/upgrade.env"
DATA_DIR="$MIFP_HOME/data"
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
  sudo mifpctl first-deploy sha-<commit>  primo avvio: crea DB schema-only + deploy
  sudo mifpctl deploy sha-<commit>        nuova versione applicazione; il DB non viene modificato
  sudo mifpctl status
  sudo mifpctl logs
  sudo mifpctl rollback                   torna alla release precedente (anche offline se locale)
  sudo mifpctl backup                     snapshot point-in-time DB + file
  sudo mifpctl doctor                     diagnostica completa
  sudo mifpctl fix-permissions            corregge ownership dati solo su richiesta

Manutenzione rara:
  sudo mifpctl init-db sha-<commit>        crea solo il DB schema-only
  sudo mifpctl upgrade-db sha-<commit> /path/new.db
  sudo mifpctl restore-db /path/backup.db
  sudo mifpctl restore-snapshot /var/backups/mifp/snapshots/snapshot-...
  sudo mifpctl rollback-upgrade            ripristina la coppia image+DB precedente
  sudo mifpctl restart
  sudo mifpctl stop
  sudo mifpctl admin [--username NAME]
  sudo mifpctl configure [--admin]

Usa sempre sha-<commit> o un digest OCI. :latest è rifiutato.
EOF
}

[[ "$(id -u)" -eq 0 ]] || die "Esegui deploy.sh come root (sudo)."
[[ -f "$ENV_FILE" ]] || die "Manca $ENV_FILE. Esegui prima deploy/bootstrap-vps.sh."
[[ -f "$COMPOSE_FILE" ]] || die "Manca $COMPOSE_FILE. Riesegui bootstrap-vps.sh."
[[ -f "$CONFIG_HELPER" ]] || die "Manca $CONFIG_HELPER. Riesegui bootstrap-vps.sh con la cartella deploy aggiornata."

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
  python3 "$CONFIG_HELPER" check --env-file "$ENV_FILE" --quiet || die "Configurazione non valida. Esegui: sudo mifpctl configure"
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
  local image="$1"
  [[ "$image" == ghcr.io/* ]] || die "Immagine non GHCR: $image"
  [[ "$image" == *@sha256:* || "$image" == *:sha-* ]] || die "Usa sha-<commit> o @sha256:...; tag mutabili come :latest non sono ammessi."
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

pull_and_pin() {
  local reference="$1" image repo digest
  image="$(resolve_image "$reference")"; validate_release_image "$image"; check_free_space
  docker pull "$image" || die "Pull fallito. Se GHCR è privato, esegui una volta: docker login ghcr.io"
  repo="$(image_repository)"
  digest="$(
    docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image" 2>/dev/null \
      | awk -v prefix="$repo@sha256:" 'index($0,prefix)==1 {print; exit}'
  )"
  [[ "$digest" == "$repo"@sha256:* ]] || die "Impossibile risolvere il digest OCI immutabile di $image per $repo"
  printf '%s\n' "$digest"
}

ensure_image_local() {
  local image="$1"
  if docker image inspect "$image" >/dev/null 2>&1; then return 0; fi
  docker pull "$image" >/dev/null || die "Immagine non disponibile localmente e pull fallito: $image"
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
  local attempts="${1:-60}" i
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
  [[ -n "$current" ]] || die "Nessuna release corrente: usa first-deploy prima di upgrade-db."
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
if manifest.get("format") != "mifp-host-snapshot" or manifest.get("version") != 1:
    raise SystemExit("unsupported snapshot manifest format")
files = manifest.get("files")
if not isinstance(files, dict) or not files:
    raise SystemExit("snapshot manifest has no files")

expected: set[str] = {"mifp.db"}
for dirname in ("assets", "conferences", "config"):
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

restore_snapshot_files() {
  local snapshot="$1" name
  for name in assets conferences config; do
    [[ -d "$snapshot/$name" && ! -L "$snapshot/$name" ]] || die "Snapshot incompleta o non sicura: $name/"
    install -d -o "$RUNTIME_UID" -g "$RUNTIME_GID" -m 0750 "$DATA_DIR/$name"
    rsync -a --delete --chown="$RUNTIME_UID:$RUNTIME_GID" "$snapshot/$name/" "$DATA_DIR/$name/"
  done
}

do_restore_snapshot() {
  local snapshot="${1:-}" current previous saved_root backup_root
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
  stop_release_for_db_swap "$current"
  install_database_candidate "$snapshot/mifp.db"
  restore_snapshot_files "$snapshot"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    say "Restore snapshot completato. Snapshot precedente: $saved_root"
    return 0
  fi

  step "Restore snapshot fallito: ripristino la fotografia precedente"
  compose_with_image "$current" down >/dev/null 2>&1 || true
  install_database_candidate "$saved_root/mifp.db"
  restore_snapshot_files "$saved_root"
  if activate_release "$current" ""; then
    write_release_state "$current" "$previous"
    die "Restore snapshot fallito; stato precedente ripristinato."
  fi
  die "Restore snapshot fallito e lo stato precedente non è tornato ready. Snapshot di sicurezza: $saved_root"
}

do_status() {
  local current previous
  current="$(current_image || true)"; previous="$(previous_image || true)"
  say "Current image:  ${current:-none}"; say "Previous image: ${previous:-none}"
  [[ -n "$current" ]] && compose_with_image "$current" ps || docker ps --filter label=com.docker.compose.project=mifp || true
  systemctl is-active caddy >/dev/null 2>&1 && say "Caddy: attivo" || say "Caddy: NON attivo"
}

do_logs() { local current; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; compose_with_image "$current" logs --tail 300 -f web; }
do_stop() { local current; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; compose_with_image "$current" down; }
do_restart() { local current; validate_production_env; ensure_tools; prepare_runtime_storage; validate_database_host; current="$(current_image || true)"; [[ -n "$current" ]] || die "Nessuna release corrente."; preflight_image_db "$current"; activate_release "$current" "" || die "Restart fallito."; }

do_backup() { [[ -x "$BACKUP_SCRIPT" ]] || die "Manca $BACKUP_SCRIPT"; "$BACKUP_SCRIPT"; }

do_doctor() {
  local failed=0 current db="$DATA_DIR/mifp.db" domain
  step "Configurazione"
  python3 "$CONFIG_HELPER" check --env-file "$ENV_FILE" || failed=1
  step "Host"
  docker info >/dev/null 2>&1 && say "Docker: OK" || { say "Docker: ERRORE"; failed=1; }
  docker compose version >/dev/null 2>&1 && say "Compose: OK" || { say "Compose: ERRORE"; failed=1; }
  systemctl is-active caddy >/dev/null 2>&1 && say "Caddy: OK" || { say "Caddy: NON attivo"; failed=1; }
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 && say "Caddyfile: OK" || { say "Caddyfile: ERRORE"; failed=1; }
  step "Storage e database"
  local min_mb available_kb available_mb target
  min_mb="$(env_value MIFP_DEPLOY_MIN_FREE_MB || true)"; min_mb="${min_mb:-2048}"
  target="$DATA_DIR"; [[ -d /var/lib/docker ]] && target=/var/lib/docker
  available_kb="$(df -Pk "$target" | awk 'NR==2 {print $4}')"
  available_mb=$((available_kb / 1024))
  if (( available_mb >= min_mb )); then say "Spazio deploy: OK (${available_mb} MB liberi)"; else say "Spazio deploy: INSUFFICIENTE (${available_mb} MB < ${min_mb} MB)"; failed=1; fi
  if [[ -f "$db" ]]; then validate_database_host && say "SQLite: OK" || failed=1; else say "DB: non ancora inizializzato"; fi
  step "Release"
  current="$(current_image || true)"
  if [[ -n "$current" ]]; then
    docker image inspect "$current" >/dev/null 2>&1 && say "Immagine corrente: locale" || { say "Immagine corrente: MANCANTE"; failed=1; }
    if [[ -f "$db" ]] && docker image inspect "$current" >/dev/null 2>&1; then
      preflight_image_db "$current" "$db" && say "Contratto DB/runtime: OK" || failed=1
    fi
    curl -fsS --max-time 3 http://127.0.0.1:8000/ready >/dev/null 2>&1 && say "Ready locale: OK" || { say "Ready locale: NON raggiungibile"; failed=1; }
  else say "Release: non ancora installata"; fi
  domain="$(env_value MIFP_DOMAIN || true)"
  if [[ -n "$current" && -n "$domain" ]]; then curl -fsS --max-time 5 "https://$domain/health" >/dev/null 2>&1 && say "HTTPS pubblico: OK" || { say "HTTPS pubblico: NON raggiungibile"; failed=1; }; fi
  ((failed == 0)) || die "Doctor ha trovato problemi."
  say "Doctor: tutto OK."
}

do_admin() {
  local args=(admin --env-file "$ENV_FILE") current; shift || true
  while (($#)); do case "$1" in --username) [[ $# -ge 2 ]] || die "--username richiede un valore"; args+=(--username "$2"); shift 2 ;; --username=*) args+=("$1"); shift ;; *) die "Uso: mifpctl admin [--username NAME]" ;; esac; done
  python3 "$CONFIG_HELPER" "${args[@]}"
  current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true
}

do_configure() {
  local force_admin=0 current; shift || true
  while (($#)); do case "$1" in --admin) force_admin=1; shift ;; *) die "Uso: mifpctl configure [--admin]" ;; esac; done
  local args=(configure --env-file "$ENV_FILE" --example "$ENV_EXAMPLE" --admin-if-missing); ((force_admin)) && args+=(--admin)
  python3 "$CONFIG_HELPER" "${args[@]}"
  current="$(current_image || true)"; [[ -n "$current" ]] && service_running "$current" && do_restart || true
}

command="${1:-status}"
case "$command" in
  first-deploy|deploy|init-db|upgrade-db|restore-db|restore-snapshot|rollback-upgrade|rollback|--rollback|restart|stop|admin|configure|fix-permissions)
    mkdir -p "$(dirname "$LOCK_FILE")"; exec 9>"$LOCK_FILE"; flock -n 9 || die "Un'altra operazione MIFP è già in corso." ;;
esac
case "$command" in
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
  doctor) ensure_tools; do_doctor ;;
  fix-permissions) do_fix_permissions ;;
  admin) do_admin "$@" ;;
  configure) do_configure "$@" ;;
  -h|--help|help) usage ;;
  *) usage >&2; die "Comando sconosciuto: $command" ;;
esac

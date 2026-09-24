#!/usr/bin/env bash
set -Eeuo pipefail

MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
DATA_DIR="$MIFP_HOME/data"
DB="$DATA_DIR/mifp.db"
BACKUP_ROOT="${MIFP_BACKUP_ROOT:-/var/backups/mifp}"
ENV_FILE="$MIFP_HOME/.env"
LOCK_FILE="${MIFP_BACKUP_LOCK_FILE:-/run/lock/mifp-backup.lock}"
OPERATION_LOCK_FILE="${MIFP_DEPLOY_LOCK_FILE:-/run/lock/mifp-deploy.lock}"
QUIESCE="${MIFP_BACKUP_QUIESCE:-1}"
EVENTS_PRIVATE_DIR="${MIFP_EVENTS_PRIVATE_DIR:-/srv/mifp-events-private}"
EVENTS_PHP_STATE="${MIFP_EVENTS_PHP_STATE:-$MIFP_HOME/events-php-enabled.txt}"
PHP_FPM_SERVICE_FILE="${MIFP_PHP_FPM_SERVICE_FILE:-$MIFP_HOME/php-fpm.service}"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
env_value_in() {
  local file="$1" key="$2" line value
  [[ -f "$file" ]] || return 1
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"; [[ -n "$line" ]] || return 1
  value="${line#*=}"; value="${value%\'}"; value="${value#\'}"; value="${value%\"}"; value="${value#\"}"; printf '%s' "$value"
}
env_value() { env_value_in "$ENV_FILE" "$1"; }

[[ "$(id -u)" -eq 0 ]] || die "Esegui come root (sudo)."
for tool in sqlite3 rsync sha256sum flock python3; do command -v "$tool" >/dev/null 2>&1 || die "Comando mancante: $tool"; done
mkdir -p "$(dirname "$LOCK_FILE")"; exec 9>"$LOCK_FILE"; flock -n 9 || die "Backup MIFP già in corso."
if [[ "${MIFP_OPERATION_LOCK_HELD:-0}" != "1" ]]; then
  mkdir -p "$(dirname "$OPERATION_LOCK_FILE")"; exec 8>"$OPERATION_LOCK_FILE"
  flock -w 300 8 || die "Un'altra operazione MIFP è rimasta occupata per oltre 5 minuti."
fi
[[ "$QUIESCE" == "0" || "$QUIESCE" == "1" ]] || die "MIFP_BACKUP_QUIESCE deve essere 0 o 1"

if [[ ! -e "$DB" ]]; then
  say "Nessun database MIFP da salvare: $DB"
  exit 0
fi
# A symlinked or non-regular database must fail loudly instead of turning every
# scheduled backup into a silent no-op reported as success by systemd.
[[ -f "$DB" && ! -L "$DB" ]] || die "Database live non regolare o symlink: $DB"

KEEP="$(env_value MIFP_BACKUP_KEEP || true)"; KEEP="${KEEP:-14}"
[[ "$KEEP" =~ ^[0-9]+$ ]] && ((KEEP >= 2)) || die "MIFP_BACKUP_KEEP deve essere un intero >= 2"
EVENTS_BACKEND="$(env_value EVENTS_PUBLISH_BACKEND || true)"
EVENTS_BACKEND="${EVENTS_BACKEND:-local-vps}"
[[ "$EVENTS_BACKEND" == "local-vps" || "$EVENTS_BACKEND" == "remote" || "$EVENTS_BACKEND" == "disabled" ]] \
  || die "EVENTS_PUBLISH_BACKEND non valido: $EVENTS_BACKEND"

SNAPSHOT_ROOT="$BACKUP_ROOT/snapshots"
install -d -o root -g root -m 0700 "$BACKUP_ROOT" "$SNAPSHOT_ROOT"
# A SIGKILL/power loss skips the EXIT trap, so reclaim abandoned partial
# snapshots at startup rather than letting them accumulate until the disk fills.
find "$SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d -name '.snapshot-*.tmp' -mmin +360 \
  -exec rm -rf -- {} + 2>/dev/null || true
stamp="$(date -u +%Y%m%d-%H%M%S-%N)"
tmp="$SNAPSHOT_ROOT/.snapshot-$stamp.tmp"
final="$SNAPSHOT_ROOT/snapshot-$stamp"
previous="$(find "$SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'snapshot-*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {sub(/^[^ ]+ /, ""); print; exit}')"
mkdir -m 0700 "$tmp"
paused_container=""
paused_php_service=""
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if [[ -n "$paused_container" ]]; then docker unpause "$paused_container" >/dev/null 2>&1 || true; fi
  if [[ -n "$paused_php_service" ]]; then systemctl start "$paused_php_service" >/dev/null 2>&1 || true; fi
  rm -rf -- "$tmp"
  exit "$rc"
}
trap cleanup EXIT INT TERM

if [[ "$QUIESCE" == "1" ]] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  paused_container="$(docker ps -q \
    --filter label=com.docker.compose.project=mifp \
    --filter label=com.docker.compose.service=web | head -n 1)"
  if [[ -n "$paused_container" ]]; then
    docker pause "$paused_container" >/dev/null || die "Impossibile mettere in pausa MIFP per la snapshot."
  fi
fi

if [[ "$EVENTS_BACKEND" == "local-vps" && "$QUIESCE" == "1" \
   && -f "$PHP_FPM_SERVICE_FILE" && ! -L "$PHP_FPM_SERVICE_FILE" ]]; then
  candidate_php_service="$(tr -d '[:space:]' < "$PHP_FPM_SERVICE_FILE")"
  if [[ "$candidate_php_service" =~ ^php[0-9]+\.[0-9]+-fpm\.service$ ]] \
     && systemctl is-active --quiet "$candidate_php_service"; then
    systemctl stop "$candidate_php_service" \
      || die "Impossibile fermare PHP-FPM eventi per la snapshot."
    paused_php_service="$candidate_php_service"
  fi
fi

# SQLite Backup API gives a transactionally coherent database even when the
# live database is in WAL mode.
sqlite3 -readonly "$DB" ".timeout 30000" ".backup '$tmp/mifp.db'"
[[ "$(sqlite3 -readonly "$tmp/mifp.db" 'PRAGMA quick_check; PRAGMA foreign_key_check;')" == "ok" ]] \
  || die "Snapshot SQLite non valido."
# Record the digest with a path relative to the snapshot: the previous absolute
# temporary path made the documented `sha256sum -c mifp.db.sha256` always fail.
( cd "$tmp" && sha256sum mifp.db > mifp.db.sha256 )
chmod 0600 "$tmp/mifp.db" "$tmp/mifp.db.sha256"

# Each directory is a real point-in-time tree. --link-dest hard-links files
# unchanged since the previous snapshot, so snapshots stay cheap without the
# ambiguity of one cumulative mirror shared by every DB generation.
for name in assets conferences config; do
  mkdir -m 0700 "$tmp/$name"
  source_dir="$DATA_DIR/$name"
  if [[ -d "$source_dir" ]]; then
    args=(-a --delete)
    if [[ -n "$previous" && -d "$previous/$name" ]]; then
      args+=(--link-dest="$previous/$name")
    fi
    rsync "${args[@]}" "$source_dir/" "$tmp/$name/"
  fi
  # Managed runtime/public trees must never contain links or special filesystem
  # objects: they could escape/alter semantics during a privileged restore.
  unsafe="$(find "$tmp/$name" \( -type l -o -type b -o -type c -o -type p -o -type s \) -print -quit)"
  [[ -z "$unsafe" ]] || die "Snapshot non sicura: link simbolico o file speciale trovato in $name/: $unsafe"
done

# Phase 1 submissions are authoritative on this host. Remote backends have a
# separate authority and must define their own verified backup before cutover.
if [[ "$EVENTS_BACKEND" == "local-vps" ]]; then
  [[ -d "$EVENTS_PRIVATE_DIR" && ! -L "$EVENTS_PRIVATE_DIR" ]] \
    || die "Runtime privato eventi mancante o non sicuro: $EVENTS_PRIVATE_DIR"
  mkdir -m 0700 "$tmp/events-private"
  for name in registrations uploads; do
    [[ -d "$EVENTS_PRIVATE_DIR/$name" && ! -L "$EVENTS_PRIVATE_DIR/$name" ]] \
      || die "Runtime privato eventi mancante o non sicuro: $name/"
    mkdir -m 0700 "$tmp/events-private/$name"
    args=(-a --delete)
    if [[ -n "$previous" && -d "$previous/events-private/$name" ]]; then
      args+=(--link-dest="$previous/events-private/$name")
    fi
    rsync "${args[@]}" "$EVENTS_PRIVATE_DIR/$name/" "$tmp/events-private/$name/"
  done
  unsafe="$(find "$tmp/events-private" \( -type l -o -type b -o -type c -o -type p -o -type s \) -print -quit)"
  [[ -z "$unsafe" ]] || die "Snapshot non sicura nel runtime privato eventi: $unsafe"
  [[ -f "$EVENTS_PHP_STATE" && ! -L "$EVENTS_PHP_STATE" ]] \
    || die "Allow-list PHP eventi mancante o non sicura: $EVENTS_PHP_STATE"
  install -o root -g root -m 0600 "$EVENTS_PHP_STATE" "$tmp/events-php-enabled.txt"
fi

# Integrity manifest for the entire restorable snapshot, not only SQLite.
# JSON avoids pathname ambiguities and lets restore verify the exact file set.
python3 - "$tmp" "$EVENTS_BACKEND" <<'PY_MANIFEST'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
events_backend = sys.argv[2]
files: dict[str, str] = {}

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

for candidate in [root / "mifp.db"] + [
    path
    for dirname in ("assets", "conferences", "config", "events-private")
    for path in sorted((root / dirname).rglob("*"))
    if path.is_file() and not path.is_symlink()
]:
    relative = candidate.relative_to(root).as_posix()
    files[relative] = digest(candidate)

if events_backend == "local-vps":
    files["events-php-enabled.txt"] = digest(root / "events-php-enabled.txt")
manifest = {
    "format": "mifp-host-snapshot",
    "version": 5,
    "events_backend": events_backend,
    "files": files,
}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY_MANIFEST
chmod 0600 "$tmp/manifest.json"

if [[ -n "$paused_container" ]]; then
  docker unpause "$paused_container" >/dev/null || die "Impossibile riattivare MIFP dopo il backup."
  paused_container=""
fi
if [[ -n "$paused_php_service" ]]; then
  systemctl start "$paused_php_service" || die "Impossibile riattivare PHP-FPM dopo il backup."
  paused_php_service=""
fi
if [[ "$EVENTS_BACKEND" == "local-vps" ]]; then
  EVENTS_BACKUP_SUMMARY="events-private/{registrations,uploads}/, events-php-enabled.txt"
else
  EVENTS_BACKUP_SUMMARY="remote event submissions excluded; verify the remote host backup separately"
fi
cat > "$tmp/README.txt" <<EOF
MIFP point-in-time backup
UTC: $stamp
Database: mifp.db (verified with quick_check + foreign_key_check)
Event backend: $EVENTS_BACKEND
Files: assets/, conferences/, config/, $EVENTS_BACKUP_SUMMARY
Public event tree: excluded; rebuild with sudo mifpctl events-republish-all
Integrity: manifest.json covers every restorable file
Restore DB: sudo mifpctl restore-db $final/mifp.db
Restore complete snapshot: sudo mifpctl restore-snapshot $final
EOF

# Publishing the directory rename is atomic on the backup filesystem.
mv "$tmp" "$final"
trap - EXIT INT TERM
ln -sfn "$(basename "$final")" "$SNAPSHOT_ROOT/latest.tmp"
mv -Tf "$SNAPSHOT_ROOT/latest.tmp" "$SNAPSHOT_ROOT/latest"

# Local rotation. A pre-restore safety snapshot sets MIFP_BACKUP_NO_PRUNE=1 so
# it can never delete the very snapshot the operator asked to restore.
if [[ "${MIFP_BACKUP_NO_PRUNE:-0}" != "1" ]]; then
  mapfile -t old < <(find "$SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'snapshot-*' -printf '%T@ %p\n' | sort -nr | awk -v keep="$KEEP" 'NR>keep {sub(/^[^ ]+ /, ""); print}')
  for path in "${old[@]:-}"; do [[ -n "$path" ]] || continue; rm -rf -- "$path"; done
fi

# Optional encrypted/off-site replication. Configure restic explicitly; the
# default installation has no external destination and never sends data away.
RESTIC_REPO="$(env_value MIFP_RESTIC_REPOSITORY || true)"
if [[ -n "$RESTIC_REPO" ]]; then
  command -v restic >/dev/null 2>&1 || die "MIFP_RESTIC_REPOSITORY è configurato ma restic non è installato."
  export RESTIC_REPOSITORY="$RESTIC_REPO"
  password_file="$(env_value MIFP_RESTIC_PASSWORD_FILE || true)"
  if [[ -z "${RESTIC_PASSWORD:-}" ]]; then
    # The configure wizard stores the restic password in the host-only secrets
    # file (loaded by systemd for the timer, but not for a manual mifpctl run).
    # Read it here so `sudo mifpctl backup` and restore-snapshot keep working.
    secrets_file="${MIFP_CONFIG_DIR:-/etc/mifp}/secrets.env"
    RESTIC_PASSWORD="$(env_value_in "$secrets_file" RESTIC_PASSWORD || true)"
  fi
  if [[ -n "${RESTIC_PASSWORD:-}" ]]; then
    export RESTIC_PASSWORD
  elif [[ -n "$password_file" && -r "$password_file" ]]; then
    export RESTIC_PASSWORD_FILE="$password_file"
  else
    die "Configura RESTIC_PASSWORD in /etc/mifp/secrets.env o un MIFP_RESTIC_PASSWORD_FILE leggibile."
  fi
  # Validate the retention policy BEFORE any off-site write: a typo would
  # otherwise publish an unprunable snapshot and only fail afterwards.
  KEEP_DAILY="$(env_value MIFP_RESTIC_KEEP_DAILY || true)"; KEEP_DAILY="${KEEP_DAILY:-7}"
  KEEP_WEEKLY="$(env_value MIFP_RESTIC_KEEP_WEEKLY || true)"; KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
  KEEP_MONTHLY="$(env_value MIFP_RESTIC_KEEP_MONTHLY || true)"; KEEP_MONTHLY="${KEEP_MONTHLY:-6}"
  [[ "$KEEP_DAILY" =~ ^[0-9]+$ ]] || die "MIFP_RESTIC_KEEP_DAILY deve essere un intero non negativo"
  [[ "$KEEP_WEEKLY" =~ ^[0-9]+$ ]] || die "MIFP_RESTIC_KEEP_WEEKLY deve essere un intero non negativo"
  [[ "$KEEP_MONTHLY" =~ ^[0-9]+$ ]] || die "MIFP_RESTIC_KEEP_MONTHLY deve essere un intero non negativo"
  restic backup "$final" --tag mifp --tag production
  # Local retention prunes $BACKUP_ROOT only; without this the off-site
  # repository would grow without bound.
  restic forget --tag mifp \
    --keep-daily "$KEEP_DAILY" \
    --keep-weekly "$KEEP_WEEKLY" \
    --keep-monthly "$KEEP_MONTHLY" \
    --prune
fi

say "Backup completato: $final"

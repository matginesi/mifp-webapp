#!/usr/bin/env bash
set -Eeuo pipefail

MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
DATA_DIR="$MIFP_HOME/data"
DB="$DATA_DIR/mifp.db"
EVENTS_DIR="$MIFP_HOME/events"
EVENTS_PRIVATE_DIR="$MIFP_HOME/events-private"
EVENTS_PHP_STATE="$MIFP_HOME/events-php-enabled.txt"
PHP_FPM_SERVICE_FILE="$MIFP_HOME/php-fpm.service"
BACKUP_ROOT="${MIFP_BACKUP_ROOT:-/var/backups/mifp}"
ENV_FILE="$MIFP_HOME/.env"
LOCK_FILE="${MIFP_BACKUP_LOCK_FILE:-/run/lock/mifp-backup.lock}"
OPERATION_LOCK_FILE="${MIFP_DEPLOY_LOCK_FILE:-/run/lock/mifp-deploy.lock}"
QUIESCE="${MIFP_BACKUP_QUIESCE:-1}"

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

# Future conference registration endpoints may write files outside the public
# document root.  If the dedicated FPM service is active, briefly stop it so
# events-private/ is captured at one coherent point in time as well.
if [[ "$QUIESCE" == "1" && -f "$PHP_FPM_SERVICE_FILE" ]] && command -v systemctl >/dev/null 2>&1; then
  candidate_service="$(tr -d '[:space:]' < "$PHP_FPM_SERVICE_FILE")"
  if [[ "$candidate_service" =~ ^php[0-9]+\.[0-9]+-fpm\.service$ ]] && systemctl is-active "$candidate_service" >/dev/null 2>&1; then
    systemctl stop "$candidate_service" || die "Impossibile sospendere PHP-FPM eventi per la snapshot."
    paused_php_service="$candidate_service"
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
for name in assets conferences config events events-private; do
  mkdir -m 0700 "$tmp/$name"
  case "$name" in
    events) source_dir="$EVENTS_DIR" ;;
    events-private) source_dir="$EVENTS_PRIVATE_DIR" ;;
    *) source_dir="$DATA_DIR/$name" ;;
  esac
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

# PHP execution policy is part of the public conference state.  Store the
# allow-list source, not the derived Caddy include.  Missing means empty/deny-all.
if [[ -L "$EVENTS_PHP_STATE" ]]; then
  die "Snapshot non sicura: $EVENTS_PHP_STATE è un symlink."
fi
if [[ -f "$EVENTS_PHP_STATE" ]]; then
  cp -- "$EVENTS_PHP_STATE" "$tmp/events-php-enabled.txt"
else
  : > "$tmp/events-php-enabled.txt"
fi
chmod 0600 "$tmp/events-php-enabled.txt"

# Fail closed when the allow-list points at a directory that no longer exists:
# the restore verifier (and Caddy include renderer) reject such a snapshot, so
# publishing one would report success for a backup that can never be restored.
while IFS= read -r prefix; do
  [[ -n "$prefix" ]] || continue
  if [[ "$prefix" == *..* || "$prefix" == /* || "$prefix" == *//* ]]; then
    die "Allow-list PHP non valida: $prefix"
  fi
  if [[ ! -d "$EVENTS_DIR/$prefix" || -L "$EVENTS_DIR/$prefix" ]]; then
    die "Allow-list PHP punta a una directory mancante o non sicura: $prefix (in $EVENTS_DIR)"
  fi
done < "$tmp/events-php-enabled.txt"

# Integrity manifest for the entire restorable snapshot, not only SQLite.
# JSON avoids pathname ambiguities and lets restore verify the exact file set.
python3 - "$tmp" <<'PY_MANIFEST'
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
files: dict[str, str] = {}

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

for candidate in [root / "mifp.db", root / "events-php-enabled.txt"] + [
    path
    for dirname in ("assets", "conferences", "config", "events", "events-private")
    for path in sorted((root / dirname).rglob("*"))
    if path.is_file() and not path.is_symlink()
]:
    relative = candidate.relative_to(root).as_posix()
    files[relative] = digest(candidate)

manifest = {"format": "mifp-host-snapshot", "version": 2, "files": files}
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

cat > "$tmp/README.txt" <<EOF
MIFP point-in-time backup
UTC: $stamp
Database: mifp.db (verified with quick_check + foreign_key_check)
Files: assets/, conferences/, config/, events/, events-private/, events-php-enabled.txt
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

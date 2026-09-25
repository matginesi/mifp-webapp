#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Refresh only the already-provisioned MIFP host tooling from this local deploy/
# bundle. Initial host provisioning remains bootstrap-vps.sh's responsibility.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
CONFIG_DIR="${MIFP_CONFIG_DIR:-/etc/mifp}"
SECRET_MATERIAL_DIR="${MIFP_SECRET_MATERIAL_DIR:-$CONFIG_DIR/secrets}"
SYSTEMD_DIR="${MIFP_SYSTEMD_DIR:-/etc/systemd/system}"
MIFPCTL_TARGET="${MIFPCTL_TARGET:-/usr/local/sbin/mifpctl}"
RUNTIME_UID="${MIFP_RUNTIME_UID:-10001}"
RUNTIME_GID="${MIFP_RUNTIME_GID:-10001}"
LOCK_FILE="${MIFP_REFRESH_LOCK_FILE:-/run/lock/mifp-host-tools-refresh.lock}"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Esegui refresh-host-tools.sh come root (sudo)."
for path in "$MIFP_HOME" "$CONFIG_DIR" "$SYSTEMD_DIR" "$(dirname "$MIFPCTL_TARGET")"; do
  [[ "$path" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "Percorso di installazione non sicuro: $path"
done

required=(
  bootstrap-vps.sh refresh-host-tools.sh deploy.sh configure.py vps_config.py
  backup.sh local-hosts.sh mifpctl compose.production.yaml .env.production.example
  Caddyfile mifp-backup.service mifp-backup.timer
  mifp-alert-check.sh mifp-alert-check.service mifp-alert-check.timer
)
for name in "${required[@]}"; do
  source_path="$SCRIPT_DIR/$name"
  [[ -f "$source_path" && ! -L "$source_path" ]] || die "Bundle deploy incompleto o non sicuro: $name"
  mode="$(stat -c '%a' "$source_path")"
  (( (8#$mode & 8#022) == 0 )) || die "File bundle modificabile da gruppo/altri: $name"
done

for script in bootstrap-vps.sh refresh-host-tools.sh deploy.sh backup.sh local-hosts.sh mifp-alert-check.sh mifpctl; do
  bash -n "$SCRIPT_DIR/$script" || die "Sintassi shell non valida: $script"
done
for helper in configure.py vps_config.py; do
  python3 -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); compile(p.read_bytes(), str(p), "exec")' "$SCRIPT_DIR/$helper" \
    || die "Sintassi Python non valida: $helper"
done
docker compose -f "$SCRIPT_DIR/compose.production.yaml" config --no-interpolate -q >/dev/null \
  || die "Definizione Compose non valida."
if grep -Eq '^[[:space:]]+environment:[[:space:]]*(SECRET_KEY|ADMIN_PASSWORD_HASH|SMTP_PASSWORD|EVENTS_REMOTE_PASSWORD)[[:space:]]*$' "$SCRIPT_DIR/compose.production.yaml"; then
  die "Compose usa ancora secret environment-backed incompatibili con read_only; sono ammessi solo secret file-backed."
fi

# This path is intentionally for existing hosts only. Refuse to turn a partial
# installation into something that merely looks provisioned.
[[ -d "$MIFP_HOME" && ! -L "$MIFP_HOME" && -d "$CONFIG_DIR" && ! -L "$CONFIG_DIR" ]] \
  || die "Host non inizializzato o directory di installazione non sicure."
for path in "$MIFP_HOME/.env" "$CONFIG_DIR/config.env" "$CONFIG_DIR/secrets.env"; do
  [[ -f "$path" && ! -L "$path" ]] || die "Host non inizializzato o configurazione non sicura: $path"
done
[[ -d "$MIFP_HOME/data" && ! -L "$MIFP_HOME/data" ]] \
  || die "Host non inizializzato: directory dati mancante o non sicura."
[[ "$SECRET_MATERIAL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] \
  || die "Percorso Docker secrets non sicuro: $SECRET_MATERIAL_DIR"
python3 "$SCRIPT_DIR/vps_config.py" \
  --config-file "$CONFIG_DIR/config.env" \
  --secrets-file "$CONFIG_DIR/secrets.env" \
  --runtime-env "$MIFP_HOME/.env" \
  materialize-secrets --output-dir "$SECRET_MATERIAL_DIR" \
  --uid "$RUNTIME_UID" --gid "$RUNTIME_GID" --mode 0400 \
  || die "Impossibile materializzare i Docker secrets dal file canonico."

install -d -o root -g root -m 0755 "$MIFP_HOME" "$(dirname "$MIFPCTL_TARGET")" "$SYSTEMD_DIR" "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || die "Un altro refresh degli strumenti MIFP è in corso."

declare -a staged=() targets=()
cleanup() {
  local file
  for file in "${staged[@]:-}"; do [[ -z "$file" ]] || rm -f -- "$file"; done
}
trap cleanup EXIT

stage_file() {
  local source="$1" target="$2" mode="$3" temp
  temp="$(mktemp "$(dirname "$target")/.${target##*/}.refresh.XXXXXX")"
  install -o root -g root -m "$mode" "$source" "$temp"
  staged+=("$temp")
  targets+=("$target")
}

stage_file "$SCRIPT_DIR/compose.production.yaml" "$MIFP_HOME/compose.yaml" 0644
stage_file "$SCRIPT_DIR/deploy.sh" "$MIFP_HOME/deploy.sh" 0750
stage_file "$SCRIPT_DIR/configure.py" "$MIFP_HOME/configure.py" 0750
stage_file "$SCRIPT_DIR/vps_config.py" "$MIFP_HOME/vps_config.py" 0750
stage_file "$SCRIPT_DIR/.env.production.example" "$MIFP_HOME/.env.example" 0644
stage_file "$SCRIPT_DIR/Caddyfile" "$MIFP_HOME/Caddyfile.example" 0644
stage_file "$SCRIPT_DIR/backup.sh" "$MIFP_HOME/backup.sh" 0750
stage_file "$SCRIPT_DIR/local-hosts.sh" "$MIFP_HOME/local-hosts.sh" 0750
stage_file "$SCRIPT_DIR/refresh-host-tools.sh" "$MIFP_HOME/refresh-host-tools.sh" 0750
stage_file "$SCRIPT_DIR/mifpctl" "$MIFPCTL_TARGET" 0755
stage_file "$SCRIPT_DIR/mifp-backup.service" "$SYSTEMD_DIR/mifp-backup.service" 0644
stage_file "$SCRIPT_DIR/mifp-backup.timer" "$SYSTEMD_DIR/mifp-backup.timer" 0644
stage_file "$SCRIPT_DIR/mifp-alert-check.sh" "$MIFP_HOME/mifp-alert-check.sh" 0750
stage_file "$SCRIPT_DIR/mifp-alert-check.service" "$SYSTEMD_DIR/mifp-alert-check.service" 0644
stage_file "$SCRIPT_DIR/mifp-alert-check.timer" "$SYSTEMD_DIR/mifp-alert-check.timer" 0644

if [[ "$MIFP_HOME" != /opt/mifp || "$CONFIG_DIR" != /etc/mifp ]]; then
  backup_service_stage="${staged[10]}"
  alert_service_stage="${staged[13]}"
  sed -i -e "s|/opt/mifp|$MIFP_HOME|g" -e "s|/etc/mifp|$CONFIG_DIR|g" "$backup_service_stage" "$alert_service_stage"
fi

systemd_changed=0
compose_changed=0
caddy_template_changed=0
for index in "${!staged[@]}"; do
  target="${targets[$index]}"
  if [[ -f "$target" && ! -L "$target" ]] && cmp -s "${staged[$index]}" "$target"; then
    rm -f -- "${staged[$index]}"
    staged[index]=""
    continue
  fi
  [[ "$target" != "$SYSTEMD_DIR/mifp-backup.service" && "$target" != "$SYSTEMD_DIR/mifp-backup.timer" \
     && "$target" != "$SYSTEMD_DIR/mifp-alert-check.service" && "$target" != "$SYSTEMD_DIR/mifp-alert-check.timer" ]] \
    || systemd_changed=1
  [[ "$target" != "$MIFP_HOME/compose.yaml" ]] || compose_changed=1
  [[ "$target" != "$MIFP_HOME/Caddyfile.example" ]] || caddy_template_changed=1
  mv -f -- "${staged[$index]}" "$target"
  staged[index]=""
done

(( systemd_changed == 0 )) || systemctl daemon-reload
trap - EXIT

say "Host deploy tooling refreshed from the local bundle."
say "Preserved: $CONFIG_DIR/config.env, $CONFIG_DIR/secrets.env, $MIFP_HOME/.env, release state, data and backups."
say "Derived Docker secret files refreshed in $SECRET_MATERIAL_DIR (root-only; canonical values remain in secrets.env)."
say "No packages, firewall, SSH policy, Docker repositories, live Caddy configuration, containers or services were changed."
say "Next: run 'sudo mifpctl config-check' and 'sudo mifpctl security-check'."
if (( caddy_template_changed )); then
  say "ACTION REQUIRED: the Caddy template changed but the live Caddyfile did not. Review it, then apply explicitly with 'sudo mifpctl configure --section web' (this may restart a running app)."
fi
if (( compose_changed )); then
  say "NOTICE: the Compose definition changed; it takes effect only on a subsequent explicit application deploy/restart."
fi
say "Application images remain unchanged; run 'sudo mifpctl update-check' and then 'sudo mifpctl update' explicitly when wanted."

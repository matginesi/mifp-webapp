#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Independent host-level notification path. This intentionally does not depend
# on Flask/Docker, so a dead web container can still generate an email alert.
MIFP_HOME="${MIFP_HOME:-/opt/mifp}"
CONFIG_DIR="${MIFP_CONFIG_DIR:-/etc/mifp}"
DATA_DIR="${MIFP_DATA_DIR:-$MIFP_HOME/data}"
STATE_DIR="${MIFP_ALERT_STATE_DIR:-/var/lib/mifp-alerts}"
MAIL_RELAY_CONFIG="${MIFP_MAIL_RELAY_CONFIG:-/etc/msmtprc}"
CONFIG_HELPER="${MIFP_VPS_CONFIG_HELPER:-$MIFP_HOME/vps_config.py}"
PUBLIC_CONFIG_FILE="${MIFP_PUBLIC_CONFIG_FILE:-$CONFIG_DIR/config.env}"
SECRETS_FILE="${MIFP_SECRETS_FILE:-$CONFIG_DIR/secrets.env}"
ENV_FILE="${MIFP_ENV_FILE:-$MIFP_HOME/.env}"
DB="${MIFP_DATABASE_PATH:-$DATA_DIR/mifp.db}"
RELEASE_FILE="${MIFP_RELEASE_FILE:-$MIFP_HOME/release.env}"
STATE_FILE="$STATE_DIR/state.env"

say() { printf '%s\n' "$*"; }

config_get() {
  python3 "$CONFIG_HELPER" \
    --config-file "$PUBLIC_CONFIG_FILE" \
    --secrets-file "$SECRETS_FILE" \
    --runtime-env "$ENV_FILE" get "$1" 2>/dev/null || true
}

db_setting() {
  local key="$1" value=""
  [[ -f "$DB" && ! -L "$DB" ]] || return 0
  case "$key" in
    notification_enabled|notification_problems_enabled|notification_down_enabled|notification_recovery_enabled|notification_down_threshold|notification_problem_threshold) ;;
    *) return 0 ;;
  esac
  value="$(sqlite3 -readonly "$DB" "PRAGMA busy_timeout=500; SELECT value FROM settings WHERE key='$key' LIMIT 1;" 2>/dev/null | tail -n 1 || true)"
  printf '%s\n' "$value"
}

bool_setting() {
  local key="$1" default="$2" value
  value="$(db_setting "$key")"
  [[ -n "$value" ]] || value="$default"
  case "${value,,}" in 1|true|yes|on) return 0 ;; *) return 1 ;; esac
}

int_setting() {
  local key="$1" default="$2" min="$3" max="$4" value
  value="$(db_setting "$key")"
  [[ "$value" =~ ^[0-9]+$ ]] || value="$default"
  (( value >= min && value <= max )) || value="$default"
  printf '%s\n' "$value"
}

valid_email() {
  local value="$1"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* && "$value" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
}

# No production release means there is nothing to monitor yet. This keeps the
# timer safe to enable as soon as SMTP is configured on a freshly provisioned
# host, before the first image has been deployed.
[[ -f "$RELEASE_FILE" && ! -L "$RELEASE_FILE" ]] || exit 0

authorized_state_dir() {
  [[ "$STATE_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || return 1
  mkdir -p "$STATE_DIR"
  chmod 0700 "$STATE_DIR" 2>/dev/null || true
}
authorized_state_dir || { say "WARN: invalid alert state directory"; exit 0; }

DOWN_FAILURES=0
DOWN_ACTIVE=0
PROBLEM_FAILURES=0
PROBLEM_ACTIVE=0
BACKUP_ACTIVE=0
if [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]]; then
  # Root-created state only; reject unexpected syntax rather than sourcing it.
  if grep -Eqv '^(DOWN_FAILURES|DOWN_ACTIVE|PROBLEM_FAILURES|PROBLEM_ACTIVE|BACKUP_ACTIVE)=[0-9]+$|^[[:space:]]*$' "$STATE_FILE"; then
    say "WARN: invalid alert state ignored"
  else
    # shellcheck disable=SC1090
    source "$STATE_FILE"
  fi
fi
for name in DOWN_FAILURES DOWN_ACTIVE PROBLEM_FAILURES PROBLEM_ACTIVE BACKUP_ACTIVE; do
  value="${!name:-0}"
  [[ "$value" =~ ^[0-9]+$ ]] || printf -v "$name" '%s' 0
done

save_state() {
  local tmp
  tmp="$(mktemp "$STATE_DIR/.state.XXXXXX")"
  chmod 0600 "$tmp"
  cat >"$tmp" <<EOF_STATE
DOWN_FAILURES=$DOWN_FAILURES
DOWN_ACTIVE=$DOWN_ACTIVE
PROBLEM_FAILURES=$PROBLEM_FAILURES
PROBLEM_ACTIVE=$PROBLEM_ACTIVE
BACKUP_ACTIVE=$BACKUP_ACTIVE
EOF_STATE
  mv -f "$tmp" "$STATE_FILE"
}
trap save_state EXIT

provider="$(config_get MAIL_PROVIDER)"
[[ "$provider" == "smtp" ]] || exit 0
bool_setting notification_enabled 1 || exit 0

recipient="$(config_get MAIL_TO)"
from_address="$(config_get SMTP_FROM_ADDRESS)"
domain="$(config_get DOMAIN)"
[[ -n "$domain" ]] || domain="mifp.eu"
valid_email "$recipient" || { say "WARN: MAIL_TO is not configured with a valid address"; exit 0; }
valid_email "$from_address" || { say "WARN: SMTP_FROM_ADDRESS is not configured with a valid address"; exit 0; }
[[ -f "$MAIL_RELAY_CONFIG" && ! -L "$MAIL_RELAY_CONFIG" ]] || { say "WARN: SMTP relay is not configured"; exit 0; }
command -v msmtp >/dev/null 2>&1 || { say "WARN: msmtp is not installed"; exit 0; }
command -v python3 >/dev/null 2>&1 || { say "WARN: python3 is not installed"; exit 0; }

send_alert() {
  local severity="$1" event="$2" subject="$3" body="$4"
  MIFP_ALERT_FROM="$from_address" \
  MIFP_ALERT_TO="$recipient" \
  MIFP_ALERT_SEVERITY="$severity" \
  MIFP_ALERT_EVENT="$event" \
  MIFP_ALERT_SUBJECT="$subject" \
  MIFP_ALERT_BODY="$body" \
  python3 - <<'PY' | msmtp --file="$MAIL_RELAY_CONFIG" -- "$recipient"
import os
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

sender = os.environ["MIFP_ALERT_FROM"]
recipient = os.environ["MIFP_ALERT_TO"]
severity = os.environ.get("MIFP_ALERT_SEVERITY", "info").lower()
event = os.environ.get("MIFP_ALERT_EVENT", "host_alert")
subject = os.environ.get("MIFP_ALERT_SUBJECT", "MIFP VPS Notification").replace("\r", " ").replace("\n", " ")[:180]
display_subject = subject
while display_subject.startswith("[") and "]" in display_subject:
    _tag, display_subject = display_subject.split("]", 1)
    display_subject = display_subject.lstrip()
display_subject = display_subject or "MIFP VPS Notification"
body = os.environ.get("MIFP_ALERT_BODY", "")
colors = {
    "critical": ("#a72b31", "#faeeee", "CRITICAL"),
    "error": ("#a72b31", "#faeeee", "ERROR"),
    "warning": ("#9a5b08", "#fff7e6", "WARNING"),
    "recovery": ("#197149", "#edf8f2", "RECOVERED"),
    "info": ("#315f9b", "#eef5fc", "INFO"),
}
color, subtle, label = colors.get(severity, colors["info"])
paragraphs = []
for paragraph in body.strip().split("\n\n"):
    if paragraph.strip():
        paragraphs.append(
            '<p style="margin:0 0 14px;color:#2c3036;font-size:14px;line-height:1.6;">'
            + "<br>".join(escape(line) for line in paragraph.splitlines())
            + "</p>"
        )
content = "".join(paragraphs) or '<p style="color:#5f6670;">No additional details were provided.</p>'
generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
html = f'''<!doctype html><html><body style="margin:0;padding:0;background:#eef1f4;font-family:Inter,Segoe UI,Arial,sans-serif;color:#2c3036;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef1f4;padding:28px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:#ffffff;border:1px solid #cbd0d6;border-radius:6px;overflow:hidden;">
<tr><td style="background:#181b20;padding:18px 22px;border-left:5px solid #a72b31;"><div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#aeb5bf;font-weight:700;">Mediterranean Institute of Fundamental Physics</div><div style="margin-top:5px;color:#ffffff;font-size:20px;font-weight:750;">MIFP VPS Notification</div></td></tr>
<tr><td style="padding:22px;"><div style="display:inline-block;padding:5px 8px;border-radius:999px;background:{subtle};color:{color};font-size:11px;font-weight:800;letter-spacing:.08em;">{label}</div><h1 style="margin:13px 0 16px;font-size:21px;line-height:1.35;color:#2c3036;">{escape(display_subject)}</h1><div style="border-left:3px solid {color};padding:2px 0 2px 15px;">{content}</div><div style="margin-top:20px;padding:10px 12px;background:#f8f9fa;border:1px solid #e2e5e9;border-radius:4px;color:#5f6670;font-size:12px;line-height:1.5;"><b style="color:#2c3036;">Event</b> &nbsp; {escape(event)}<br><b style="color:#2c3036;">Generated</b> &nbsp; {generated}</div></td></tr>
<tr><td style="padding:15px 22px;background:#f8f9fa;border-top:1px solid #e2e5e9;color:#5f6670;font-size:11px;line-height:1.5;">Independent host-level MIFP monitor. SMTP credentials, application secrets and session data are never included.</td></tr>
</table></td></tr></table></body></html>'''
msg = EmailMessage()
msg["From"] = formataddr(("MIFP Alerts", sender))
msg["To"] = recipient
msg["Subject"] = subject
msg["Auto-Submitted"] = "auto-generated"
msg["X-MIFP-Event"] = event
msg.set_content(body, subtype="plain", charset="utf-8")
msg.add_alternative(html, subtype="html", charset="utf-8")
sys.stdout.write(msg.as_string())
PY
}

host="$(hostname -f 2>/dev/null || hostname 2>/dev/null || printf 'mifp-host')"
down_threshold="$(int_setting notification_down_threshold 3 1 20)"
problem_threshold="$(int_setting notification_problem_threshold 3 1 20)"
recovery_enabled=0
bool_setting notification_recovery_enabled 1 && recovery_enabled=1

local_health=0
public_health=0
ready=0
curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1 && local_health=1
curl -fsS --max-time 8 "https://$domain/health" >/dev/null 2>&1 && public_health=1
curl -fsS --max-time 5 http://127.0.0.1:8000/ready >/dev/null 2>&1 && ready=1

if (( local_health == 0 || public_health == 0 )); then
  DOWN_FAILURES=$((DOWN_FAILURES + 1))
  if (( DOWN_FAILURES >= down_threshold && DOWN_ACTIVE == 0 )); then
    if bool_setting notification_down_enabled 1; then
      body="$(printf 'MIFP production failed repeated liveness checks.\n\nHost: %s\nDomain: %s\nConsecutive failures: %s\nLocal health: %s\nPublic health: %s\n\nCheck: sudo mifpctl status\nLogs: sudo mifpctl logs' "$host" "$domain" "$DOWN_FAILURES" "$local_health" "$public_health")"
      if send_alert critical production_down "[MIFP][CRITICAL] Production is unavailable" "$body"; then
        DOWN_ACTIVE=1
      fi
    fi
  fi
else
  DOWN_FAILURES=0
  if (( DOWN_ACTIVE == 1 )); then
    if (( recovery_enabled == 1 )); then
      body="$(printf 'MIFP production health checks are passing again.\n\nHost: %s\nDomain: %s\n\nNo action is required if the recovery was expected.' "$host" "$domain")"
      send_alert recovery production_recovered "[MIFP][RECOVERED] Production is reachable again" "$body" || true
    fi
    DOWN_ACTIVE=0
  fi
fi

# A live process can still be operationally degraded (database/storage). Keep
# this distinct from downtime so the operator knows the public process exists.
if (( local_health == 1 && ready == 0 )); then
  PROBLEM_FAILURES=$((PROBLEM_FAILURES + 1))
  if (( PROBLEM_FAILURES >= problem_threshold && PROBLEM_ACTIVE == 0 )); then
    if bool_setting notification_problems_enabled 1; then
      body="$(printf 'MIFP is reachable but /ready is failing repeatedly.\n\nHost: %s\nDomain: %s\nConsecutive failures: %s\n\nPossible causes include database or durable-storage problems.\nCheck: sudo mifpctl status' "$host" "$domain" "$PROBLEM_FAILURES")"
      if send_alert warning readiness_degraded "[MIFP][WARNING] Production readiness is degraded" "$body"; then
        PROBLEM_ACTIVE=1
      fi
    fi
  fi
else
  PROBLEM_FAILURES=0
  if (( PROBLEM_ACTIVE == 1 )); then
    if (( recovery_enabled == 1 )); then
      body="$(printf 'MIFP /ready checks are passing again.\n\nHost: %s\nDomain: %s' "$host" "$domain")"
      send_alert recovery readiness_recovered "[MIFP][RECOVERED] Production readiness restored" "$body" || true
    fi
    PROBLEM_ACTIVE=0
  fi
fi

# A failed scheduled backup is a problem even while the website stays healthy.
if systemctl is-failed --quiet mifp-backup.service 2>/dev/null; then
  if (( BACKUP_ACTIVE == 0 )) && bool_setting notification_problems_enabled 1; then
    body="$(printf 'The systemd service mifp-backup.service is in a failed state.\n\nHost: %s\n\nInspect: sudo systemctl status mifp-backup.service\nJournal: sudo journalctl -u mifp-backup.service' "$host")"
    if send_alert warning backup_failed "[MIFP][WARNING] Scheduled backup failed" "$body"; then
      BACKUP_ACTIVE=1
    fi
  fi
else
  if (( BACKUP_ACTIVE == 1 )); then
    if (( recovery_enabled == 1 )); then
      body="$(printf 'mifp-backup.service is no longer in a failed state.\n\nHost: %s' "$host")"
      send_alert recovery backup_recovered "[MIFP][RECOVERED] Backup service recovered" "$body" || true
    fi
    BACKUP_ACTIVE=0
  fi
fi

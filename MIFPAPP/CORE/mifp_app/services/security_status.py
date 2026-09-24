from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ..db.connection import connect_readonly
from ..utils.logger import redact
from .control_center import backup_inventory
from .dashboard_repository import search_logs


_STATUS_ORDER = {"CRITICAL": 0, "WARNING": 1, "UNKNOWN": 2, "OK": 3, "NOT APPLICABLE": 4}
_EVENT_DETAIL_KEYS = {
    "actor",
    "category",
    "client_ip",
    "client_ip_hash",
    "endpoint",
    "method",
    "outcome",
    "path",
    "rate_limited",
    "section",
}


def _check(
    key: str,
    label: str,
    status: str,
    detail: str,
    remediation: str = "",
    *,
    group: str,
) -> dict[str, str]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "remediation": remediation,
        "group": group,
    }


def _secret_source(name: str) -> str:
    if os.getenv(f"{name}_FILE", "").strip():
        return "runtime secret file"
    if os.getenv(name) not in (None, ""):
        return "environment"
    return "application default"


def _configured_secret(config: Mapping[str, Any], name: str) -> dict[str, str]:
    configured = bool(config.get(name))
    return {
        "label": name,
        "status": "Configured" if configured else "Not configured",
        "source": _secret_source(name) if configured else "none",
    }


def _database_status(database_path: Path) -> tuple[str, str]:
    try:
        if not database_path.is_file() or database_path.is_symlink():
            return "CRITICAL", "The configured SQLite database is not a regular file."
        with connect_readonly(database_path, timeout=1.0) as conn:
            result = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        if result == "ok":
            return "OK", "SQLite is reachable and its quick integrity check passed."
        return "CRITICAL", "SQLite quick_check reported an integrity problem."
    except Exception:
        return "CRITICAL", "SQLite could not be opened for a read-only integrity check."


def application_security_status(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build a non-secret, evidence-based view of effective application controls."""
    environment = str(config.get("ENV") or "development").lower()
    production = environment == "production"
    debug = bool(config.get("DEBUG"))
    secret = str(config.get("SECRET_KEY") or "")
    insecure_secret = not secret or secret in {"dev-change-me", "dev-only-insecure-key"} or secret.startswith("CHANGE_ME")
    cookie_secure = bool(config.get("SESSION_COOKIE_SECURE"))
    same_site = str(config.get("SESSION_COOKIE_SAMESITE") or "")
    trusted_hosts = config.get("TRUSTED_HOSTS") or []
    csrf = bool(config.get("WTF_CSRF_ENABLED", True))
    max_upload = int(config.get("MAX_UPLOAD_FILE_BYTES") or config.get("MAX_CONTENT_LENGTH") or 0)

    checks = [
        _check("environment", "Application environment", "OK" if production else "NOT APPLICABLE", f"Effective environment: {environment}.", group="Application"),
        _check("debug", "Debug mode", "CRITICAL" if production and debug else ("WARNING" if debug else "OK"), "Debug is enabled." if debug else "Debug is disabled.", "Disable FLASK_DEBUG before exposing this process." if debug else "", group="Application"),
        _check("secret_key", "Application signing key", "CRITICAL" if production and insecure_secret else ("WARNING" if insecure_secret else "OK"), "A non-default signing key is configured." if not insecure_secret else "The application is using a development/default signing key.", "Configure SECRET_KEY through a runtime secret." if insecure_secret else "", group="Secrets"),
        _check("csrf", "CSRF protection", "OK" if csrf else "CRITICAL", "All state-changing requests pass the central CSRF and same-origin checks." if csrf else "Central CSRF validation is disabled.", "Enable CSRF_ENABLED." if not csrf else "", group="Application"),
        _check("cookie_httponly", "Session cookie HttpOnly", "OK" if config.get("SESSION_COOKIE_HTTPONLY", True) else "CRITICAL", "Browser scripts cannot read the session cookie." if config.get("SESSION_COOKIE_HTTPONLY", True) else "The session cookie is readable by browser scripts.", group="Authentication"),
        _check("cookie_secure", "Session cookie Secure", "OK" if cookie_secure else ("CRITICAL" if production else "NOT APPLICABLE"), "The session cookie is restricted to HTTPS." if cookie_secure else "Disabled for the local HTTP development environment." if not production else "The production session cookie may cross plaintext HTTP.", "Set SESSION_COOKIE_SECURE=1 in production." if production and not cookie_secure else "", group="Authentication"),
        _check("cookie_samesite", "Session cookie SameSite", "OK" if same_site in {"Lax", "Strict"} or (same_site == "None" and cookie_secure) else "CRITICAL", f"Effective SameSite policy: {same_site or 'unset'}.", group="Authentication"),
        _check("https_detection", "HTTPS proxy detection", "OK" if (not production or config.get("TRUST_PROXY")) else "WARNING", "Trusted proxy normalization is enabled." if config.get("TRUST_PROXY") else "Direct/local request scheme is used.", "Enable TRUST_PROXY only behind the configured reverse proxy." if production and not config.get("TRUST_PROXY") else "", group="Headers"),
        _check("hsts", "Strict-Transport-Security", "OK" if config.get("HSTS_VALUE") else "WARNING", "HSTS is emitted by the application for requests recognized as HTTPS." if config.get("HSTS_VALUE") else "No HSTS policy is configured.", group="Headers"),
        _check("csp", "Content-Security-Policy", "OK", "A nonce-aware configured CSP is active." if config.get("CONTENT_SECURITY_POLICY") else "The nonce-aware built-in restrictive CSP is active.", group="Headers"),
        _check("response_headers", "Browser security headers", "OK", "X-Content-Type-Options, Referrer-Policy, Permissions-Policy and frame protections are applied centrally.", group="Headers"),
        _check("trusted_hosts", "Trusted hosts", "OK" if trusted_hosts else ("CRITICAL" if production else "NOT APPLICABLE"), f"{len(trusted_hosts)} host pattern(s) are allowlisted." if trusted_hosts else "No host allowlist is required in local development." if not production else "No production host allowlist is configured.", "Set TRUSTED_HOSTS to the public application hostnames." if production and not trusted_hosts else "", group="Application"),
        _check("rate_limit", "Authentication and admin rate limits", "OK" if int(config.get("LOGIN_IP_MAX_ATTEMPTS", 0)) > 0 and int(config.get("ADMIN_WRITE_RATE_LIMIT", 0)) > 0 else "WARNING", "Login guessing and dashboard writes use bounded shared rate limits.", group="Authentication"),
        _check("upload_limit", "Upload limits", "OK" if max_upload > 0 else "CRITICAL", f"Maximum individual upload: {max_upload // (1024 * 1024)} MB.", group="Uploads"),
        _check("upload_validation", "Upload content validation", "OK", "Assets are allowlisted and validated from file content before content-addressed storage.", group="Uploads"),
        _check("audit_logging", "Audit and security logs", "OK" if config.get("LOG_AUDIT_ENABLED", True) and config.get("LOG_SECURITY_ENABLED", True) else "WARNING", "Dedicated redacted audit and security streams are enabled." if config.get("LOG_AUDIT_ENABLED", True) and config.get("LOG_SECURITY_ENABLED", True) else "One or more security-relevant log streams are disabled.", group="Observability"),
    ]

    database_path = Path(config["DATABASE_PATH"])
    db_status, db_detail = _database_status(database_path)
    checks.append(_check("database", "Database availability", db_status, db_detail, "Run the database contract check and restore only through server-side maintenance." if db_status != "OK" else "", group="Storage"))

    backups = backup_inventory(database_path)
    latest = backups.get("latest")
    if not latest:
        backup_status, backup_detail = "WARNING", "No local SQLite snapshot is available."
    elif float(latest.get("age_hours") or 0) > 168:
        backup_status, backup_detail = "WARNING", f"The latest snapshot is {latest['age_hours']} hours old."
    else:
        backup_status, backup_detail = "OK", f"The latest snapshot is {latest['age_hours']} hours old."
    checks.append(_check("backups", "Backup availability", backup_status, backup_detail, "Create and verify a fresh snapshot from the existing backup workflow." if backup_status != "OK" else "", group="Storage"))

    mail_provider = str(config.get("MAIL_PROVIDER") or "disabled").lower()
    smtp_ready = bool(config.get("SMTP_HOST") and config.get("SMTP_USERNAME") and config.get("SMTP_PASSWORD"))
    smtp_status = "NOT APPLICABLE" if mail_provider == "disabled" else ("OK" if smtp_ready else "WARNING")
    checks.append(_check("smtp", "SMTP configuration", smtp_status, "Mail delivery is disabled." if mail_provider == "disabled" else "SMTP credentials and endpoint are configured." if smtp_ready else "The selected mail provider is missing required SMTP configuration.", "Complete the SMTP runtime configuration without storing credentials in Git." if mail_provider != "disabled" and not smtp_ready else "", group="Secrets"))

    checks.sort(key=lambda item: (item["group"], _STATUS_ORDER[item["status"]], item["label"]))
    counts = {status: sum(1 for item in checks if item["status"] == status) for status in _STATUS_ORDER}
    secrets = [
        _configured_secret(config, "SECRET_KEY"),
        _configured_secret(config, "ADMIN_PASSWORD_HASH"),
        _configured_secret(config, "SMTP_PASSWORD"),
        _configured_secret(config, "EVENTS_REMOTE_PASSWORD"),
    ]
    return {
        "checks": checks,
        "counts": counts,
        "backups": backups,
        "secrets": secrets,
        "uploads": {
            "max_bytes": max_upload,
            "max_mb": max_upload // (1024 * 1024),
            "content_validation": True,
            "storage": "outside the public static tree",
        },
    }


def recent_security_events(log_dir: Path, limit: int = 30) -> list[dict[str, Any]]:
    """Return an allowlisted projection of redacted audit/security log entries."""
    try:
        rows = search_logs(Path(log_dir), q=None, level="ALL", limit=max(100, limit * 5))
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for row in rows:
        logger = str(row.get("logger") or "")
        event = str(row.get("event") or "")
        if logger not in {"mifp.audit", "mifp.security"} and not event.startswith("auth."):
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        safe_details = redact({key: details[key] for key in _EVENT_DETAIL_KEYS if key in details})
        events.append({
            "when": str(row.get("when") or ""),
            "level": str(row.get("level") or "LOG"),
            "event": event or "security.event",
            "message": str(redact(str(row.get("message") or "")))[:240],
            "request_id": str(row.get("request_id") or ""),
            "details": safe_details,
        })
        if len(events) >= limit:
            break
    return events


def current_session_status(session_data: Mapping[str, Any], *, client_ip: str, user_agent: str) -> dict[str, Any]:
    login_at = float(session_data.get("admin_login_at") or 0)
    login_time = datetime.fromtimestamp(login_at, UTC).isoformat(timespec="seconds") if login_at else "Unknown"
    agent = " ".join(str(user_agent or "Unknown").split())[:160]
    return {
        "username": str(session_data.get("admin_username") or "Administrator"),
        "created_at": login_time,
        "client_ip": str(client_ip or "Unknown"),
        "user_agent": agent,
        "storage": "Signed browser cookie",
        "revocation": "Server-side session enumeration and revocation are not available with this session backend.",
    }

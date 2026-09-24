from __future__ import annotations

import json
import os
from pathlib import Path

from .utils.runtime_capacity import automatic_background_workers, configured_count

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "webapp.json"
_JSON_CONFIG_KEYS = {"content_security_policy", "hsts_value", "site_defaults"}

from dotenv import load_dotenv

# One parser for native Flask and production. Existing shell variables keep
# precedence over values stored in MIFPAPP/CORE/.env. Tests and isolated tools
# can disable local-file loading with MIFP_LOAD_DOTENV=0 so a developer's .env
# cannot leak credentials, paths or security settings into subprocesses.
_DOTENV_ENABLED = os.getenv("MIFP_LOAD_DOTENV", "1").strip().lower() in {
    "1", "true", "yes", "on"
}
if _DOTENV_ENABLED:
    _dotenv_path = Path(os.getenv("MIFP_DOTENV_PATH", str(BASE_DIR / ".env")))
    if not _dotenv_path.is_absolute():
        _dotenv_path = BASE_DIR / _dotenv_path
    load_dotenv(_dotenv_path, override=False)


def _secret_setting(name: str, default: str = "") -> str:
    """Read a secret from the environment or an explicitly configured file."""
    direct = os.getenv(name)
    if direct is not None:
        return direct
    filename = os.getenv(f"{name}_FILE", "").strip()
    if not filename:
        return default
    path = Path(filename)
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{name}_FILE must reference a regular, non-symlink file")
    if path.stat().st_size > 64 * 1024:
        raise RuntimeError(f"{name}_FILE is unexpectedly large")
    return path.read_text(encoding="utf-8").strip()


def _load_json_config() -> dict:
    config_path = Path(os.getenv("MIFP_CONFIG", str(DEFAULT_CONFIG_PATH)))
    if not config_path.is_absolute():
        config_path = BASE_DIR / config_path
    if not config_path.exists():
        raise FileNotFoundError(f"MIFP config not found: {config_path}")
    with config_path.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise RuntimeError(f"MIFP config must contain a JSON object: {config_path}")
    unknown = sorted(set(cfg) - _JSON_CONFIG_KEYS)
    if unknown:
        raise RuntimeError(f"Unknown MIFP config key(s): {', '.join(unknown)}")
    if not isinstance(cfg.get("site_defaults", {}), dict):
        raise RuntimeError("MIFP config key 'site_defaults' must be an object")
    for key in ("content_security_policy", "hsts_value"):
        if key in cfg and not isinstance(cfg[key], str):
            raise RuntimeError(f"MIFP config key '{key}' must be a string")
    cfg["_config_path"] = str(config_path)
    return cfg


PROJECT_CONFIG = _load_json_config()


def _cfg(name: str, default=None):
    return PROJECT_CONFIG.get(name, default)



def _path_from_config(name: str, env_name: str | None = None, default: str | None = None) -> Path:
    value = os.getenv(env_name or name.upper()) or default
    if not value:
        raise RuntimeError(f"Missing required config value: {name}")
    path = Path(value)
    if not path.is_absolute():
        # Accept paths copied from the repository root without resolving them
        # as CORE/CORE/... when this module already lives inside CORE.
        if path.parts and path.parts[0].casefold() == BASE_DIR.name.casefold():
            path = Path(*path.parts[1:])
        path = BASE_DIR / path
    return path.resolve()


class Config:
    CONFIG_PATH = Path(PROJECT_CONFIG["_config_path"])
    ENV = os.getenv('FLASK_ENV', os.getenv('ENV', 'development'))
    DEBUG = os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true')
    TESTING = os.getenv('TESTING', '0') in {'1','true','True','yes','on'}
    _secret = _secret_setting('SECRET_KEY')
    if ENV == 'production':
        if not _secret:
            raise RuntimeError('SECRET_KEY environment variable is required in production')
        if len(_secret) < 32 or _secret.startswith('CHANGE_ME') or _secret in ('dev-change-me', 'dev-only-insecure-key'):
            raise RuntimeError('SECRET_KEY is set to an insecure default value for production')
        SECRET_KEY = _secret
    else:
        SECRET_KEY = _secret or 'dev-only-insecure-key'
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME')
    ADMIN_PASSWORD_HASH = _secret_setting('ADMIN_PASSWORD_HASH')
    ALLOW_DB_DUMP = os.getenv('ALLOW_DB_DUMP', '0') in {'1','true','True','yes','on'}
    ALLOW_DB_RESTORE = os.getenv(
        'ALLOW_DB_RESTORE', os.getenv('ALLOW_DB_DUMP', '0')
    ) in {'1','true','True','yes','on'}
    ADMIN_SESSION_HOURS = int(os.getenv('ADMIN_SESSION_HOURS', '8'))
    JOIN_MAX_PER_IP_HOUR = int(os.getenv('JOIN_MAX_PER_IP_HOUR', '5'))
    # Retention for *closed* membership applications. The privacy policy promises
    # that rejected/archived join requests are not kept forever; the value is
    # applied by the protected safety-cleanup procedure, never by a background
    # job. Pending/in_review requests and approved members are never touched.
    JOIN_REQUEST_RETENTION_DAYS = max(0, int(os.getenv('JOIN_REQUEST_RETENTION_DAYS', '730')))
    JOIN_STORE_RAW_IP = os.getenv('JOIN_STORE_RAW_IP', '0') in {'1','true','True','yes','on'}
    MAIL_PROVIDER = os.getenv('MAIL_PROVIDER', 'disabled').strip().lower()
    MAIL_FROM = os.getenv('MAIL_FROM', 'no-reply@mifp.eu')
    MAIL_FROM_NAME = os.getenv('MAIL_FROM_NAME', '')
    MAIL_TO = os.getenv('MAIL_TO', 'info@mifp.eu')
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_SECURITY = os.getenv(
        'SMTP_SECURITY',
        'starttls' if os.getenv('SMTP_USE_TLS', '1') in {'1','true','True','yes','on'} else 'none',
    ).strip().lower()
    SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', '1') in {'1','true','True','yes','on'}
    DATABASE_PATH = _path_from_config('db_path', 'DATABASE_PATH', '../DATABASE/mifp.db')
    ASSETS_DIR = _path_from_config('assets_dir', 'ASSETS_DIR', '../DATABASE/assets')
    EXPORT_DIR = _path_from_config('export_dir', 'EXPORT_DIR', '../DATABASE/exports')
    STORAGE_MIN_FREE_MB = max(0, int(os.getenv('STORAGE_MIN_FREE_MB', '1024' if ENV == 'production' else '0')))
    STORAGE_MIN_FREE_BYTES = STORAGE_MIN_FREE_MB * 1024 * 1024
    EXPORT_RETENTION_DAYS = max(0, int(os.getenv('EXPORT_RETENTION_DAYS', '1')))
    EXPORT_MAX_FILES = max(1, int(os.getenv('EXPORT_MAX_FILES', '30')))
    EXPORT_MAX_BYTES = max(1, int(os.getenv('EXPORT_MAX_MB', '2048'))) * 1024 * 1024
    # Runtime configuration directory: the storage anchor for small, mutable
    # runtime files and the capacity probe for that filesystem. Deployments that
    # predate RUNTIME_CONFIG_DIR only ever set BANNER_SETTINGS_PATH, so its parent
    # is accepted as the directory.
    _legacy_banner_settings = os.getenv("BANNER_SETTINGS_PATH", "").strip()
    RUNTIME_CONFIG_DIR = _path_from_config(
        "runtime_config_dir",
        "RUNTIME_CONFIG_DIR",
        str(Path(_legacy_banner_settings).parent) if _legacy_banner_settings else "config",
    )
    # Public cookie-notice banner. It keeps its own small settings file inside the
    # runtime configuration directory so it shares the writable volume and the
    # existing capacity probe; an explicit BANNER_SETTINGS_PATH still overrides it.
    BANNER_SETTINGS_PATH = _path_from_config(
        "banner_settings_path",
        "BANNER_SETTINGS_PATH",
        str(RUNTIME_CONFIG_DIR / "banner_settings.json"),
    )
    CONFERENCES_DIR = _path_from_config(
        "conferences_dir", "CONFERENCES_DIR", "../DATABASE/conferences"
    )
    # Event mini-sites are a separate publication capability. Phase 1 uses the
    # local-vps backend and Caddy static hosting; a future remote backend can
    # move publication without changing application or database semantics.
    EVENTS_PUBLISH_BACKEND = os.getenv(
        "EVENTS_PUBLISH_BACKEND", "local-vps" if ENV == "production" else "local"
    ).strip().lower()
    EVENTS_PUBLIC_BASE_URL = os.getenv(
        "EVENTS_PUBLIC_BASE_URL",
        (f"https://{os.getenv('EVENTS_DOMAIN')}" if os.getenv("EVENTS_DOMAIN") else
         ("https://events.mifp.eu" if ENV == "production" else "http://events.localhost")),
    ).strip().rstrip("/")
    EVENTS_LOCAL_ROOT = _path_from_config(
        "events_local_root", "EVENTS_LOCAL_ROOT", os.getenv("EVENTS_ROOT", "../DATABASE/events")
    )
    EVENTS_REMOTE_HOST = os.getenv("EVENTS_REMOTE_HOST", "").strip()
    EVENTS_REMOTE_PROTOCOL = os.getenv("EVENTS_REMOTE_PROTOCOL", "ftps").strip().lower()
    EVENTS_REMOTE_PORT = int(os.getenv("EVENTS_REMOTE_PORT", "21"))
    EVENTS_REMOTE_USER = os.getenv("EVENTS_REMOTE_USER", "")
    EVENTS_REMOTE_PASSWORD = _secret_setting("EVENTS_REMOTE_PASSWORD")
    EVENTS_REMOTE_ROOT = os.getenv("EVENTS_REMOTE_ROOT", "").strip()
    EVENTS_REMOTE_TIMEOUT = max(5, int(os.getenv("EVENTS_REMOTE_TIMEOUT", "30")))
    EVENT_IMPORT_MAX_FILES = max(1, int(os.getenv("EVENT_IMPORT_MAX_FILES", "5000")))
    EVENT_IMPORT_MAX_UNPACKED_BYTES = max(
        1, int(os.getenv("EVENT_IMPORT_MAX_UNPACKED_BYTES", str(1024 * 1024 * 1024)))
    )
    EVENT_IMPORT_MAX_FILE_BYTES = max(
        1, int(os.getenv("EVENT_IMPORT_MAX_FILE_BYTES", str(128 * 1024 * 1024)))
    )
    EVENT_IMPORT_MAX_COMPRESSION_RATIO = max(
        1, int(os.getenv("EVENT_IMPORT_MAX_COMPRESSION_RATIO", "1000"))
    )
    EVENT_IMPORT_STAGING_TTL_SECONDS = max(
        300, int(os.getenv("EVENT_IMPORT_STAGING_TTL_SECONDS", "7200"))
    )
    LOG_DIR = _path_from_config('log_dir', 'LOG_DIR', '../DATABASE/logs')
    TMP_DIR = _path_from_config('tmp_dir', 'TMPDIR', '../DATABASE/tmp')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = os.getenv(
        'LOG_FORMAT',
        'json' if os.getenv('LOG_JSON', '0') in {'1','true','True','yes','on'} else 'text',
    ).strip().lower()
    LOG_JSON = LOG_FORMAT == 'json'
    # Optional per-destination overrides. Both fall back to LOG_FORMAT, so an
    # existing deployment keeps exactly the format it has today; set them to
    # split the durable record (files) from what a human reads in `docker logs`.
    LOG_FILE_FORMAT = os.getenv('LOG_FILE_FORMAT', LOG_FORMAT).strip().lower()
    LOG_CONSOLE_FORMAT = os.getenv('LOG_CONSOLE_FORMAT', LOG_FORMAT).strip().lower()
    LOG_OUTPUT = os.getenv('LOG_OUTPUT', 'both').strip().lower()
    LOG_COLORS = os.getenv('LOG_COLORS', 'auto').strip().lower()
    LOG_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', '5000000'))
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', '7'))
    LOG_ACCESS_ENABLED = os.getenv('LOG_ACCESS_ENABLED', '1') in {'1','true','True','yes','on'}
    LOG_AUDIT_ENABLED = os.getenv('LOG_AUDIT_ENABLED', '1') in {'1','true','True','yes','on'}
    LOG_SECURITY_ENABLED = os.getenv('LOG_SECURITY_ENABLED', '1') in {'1','true','True','yes','on'}
    LOG_SLOW_REQUEST_MS = int(os.getenv('LOG_SLOW_REQUEST_MS', '5000'))
    LOG_INCLUDE_CLIENT_IP = os.getenv('LOG_INCLUDE_CLIENT_IP', '0') in {'1','true','True','yes','on'}
    LOG_HASH_CLIENT_IP = os.getenv('LOG_HASH_CLIENT_IP', '1') in {'1','true','True','yes','on'}
    LOG_RETENTION_DAYS = int(os.getenv('LOG_RETENTION_DAYS', '30'))
    PRIVACY_SAFE_METRICS_ENABLED = os.getenv('PRIVACY_SAFE_METRICS_ENABLED', '1') in {'1','true','True','yes','on'}
    PRIVACY_SAFE_METRICS_RETENTION_DAYS = int(os.getenv('PRIVACY_SAFE_METRICS_RETENTION_DAYS', '730'))
    # One uploaded package may be up to 1 GiB.  Flask's request ceiling is a
    # little higher to leave room for multipart/form-data boundaries and form
    # fields; otherwise a file exactly at the advertised limit is rejected as
    # HTTP 413 before the per-file validator can inspect it.
    MAX_UPLOAD_FILE_BYTES = int(
        os.getenv('MAX_UPLOAD_FILE_BYTES', str(1024 * 1024 * 1024))
    )
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH_MB', '1032')) * 1024 * 1024
    FLASK_HOST = os.getenv('FLASK_HOST', '127.0.0.1')
    FLASK_PORT = int(os.getenv('FLASK_PORT', '8000'))
    SITE_DEFAULTS = dict(_cfg("site_defaults", {}))
    # Single source of truth for the public cookie notice. The template, the
    # dashboard preview and the editor all read these values, so the shipped
    # wording cannot drift between them; the operator override lives in
    # BANNER_SETTINGS_PATH and wins over these defaults. The text is deliberately
    # factual: this site sets only strictly necessary cookies, and the notice is
    # informational rather than a consent gate.
    DEFAULT_BANNER_SETTINGS = {
        "cookie_banner_enabled": "1",
        "cookie_banner_text": (
            "This website uses cookies only when they are strictly necessary: to "
            "secure forms and authenticated administrator sessions. No analytics, "
            "advertising or tracking cookies are used."
        ),
        "banner_force_show": "0",
        "cookie_banner_link_enabled": "1",
        "cookie_banner_dismiss_label": "Dismiss",
        "cookie_banner_theme": "brand",
    }

    @staticmethod
    def normalize_banner_settings(values: dict | None) -> dict[str, str]:
        """Coerce stored banner settings to plain strings.

        An empty ``cookie_banner_text`` means "use the shipped wording", not "show
        an empty notice", so the key is dropped and the default survives the merge.
        """
        cleaned = {str(key): str(value) for key, value in (values or {}).items()}
        if not cleaned.get("cookie_banner_text", "").strip():
            cleaned.pop("cookie_banner_text", None)
        return cleaned
    HTTP_USER_AGENT = os.getenv('HTTP_USER_AGENT', 'MIFP-Webapp/1.0')
    CONTENT_SECURITY_POLICY = os.getenv('CONTENT_SECURITY_POLICY') or _cfg('content_security_policy', None)
    HSTS_VALUE = os.getenv('HSTS_VALUE') or _cfg('hsts_value', 'max-age=31536000; includeSubDomains')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_NAME = os.getenv('SESSION_COOKIE_NAME', 'mifp_admin_session')
    SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax').strip().title()
    if SESSION_COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None")
    SESSION_COOKIE_SECURE = os.getenv(
        'SESSION_COOKIE_SECURE', '1' if ENV == 'production' else '0'
    ) in {'1','true','True','yes','on'}
    if SESSION_COOKIE_SAMESITE == "None" and not SESSION_COOKIE_SECURE:
        raise RuntimeError("SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE=1")
    SESSION_COOKIE_PATH = "/"
    _trusted_hosts = os.getenv('TRUSTED_HOSTS', '').strip()
    TRUSTED_HOSTS = [host.strip() for host in _trusted_hosts.split(',') if host.strip()] or None
    WTF_CSRF_ENABLED = os.getenv('CSRF_ENABLED', '1') in {'1','true','True','yes','on'}
    LOGIN_LOCKOUT_SECONDS = int(os.getenv('LOGIN_LOCKOUT_SECONDS', '60'))
    LOGIN_IP_MAX_ATTEMPTS = int(os.getenv('LOGIN_IP_MAX_ATTEMPTS', '10'))
    # Second, per-account bound for *failed* passwords only. It is checked after
    # the password verdict, so a correct password always succeeds and this can
    # never lock the administrator out; it only slows guessing from many IPs.
    LOGIN_ACCOUNT_MAX_ATTEMPTS = int(os.getenv('LOGIN_ACCOUNT_MAX_ATTEMPTS', '30'))
    LOGIN_ACCOUNT_LOCKOUT_SECONDS = int(os.getenv('LOGIN_ACCOUNT_LOCKOUT_SECONDS', '900'))
    IMPORT_MAX_ZIP_BYTES = int(
        os.getenv('IMPORT_MAX_ZIP_BYTES', str(MAX_UPLOAD_FILE_BYTES))
    )
    # JSON/JSONL and ZIP metadata are parsed in memory. Keep their individual
    # limits well below the global HTTP upload ceiling to avoid memory spikes.
    IMPORT_MAX_JSONL_BYTES = int(os.getenv('IMPORT_MAX_JSONL_BYTES', str(128 * 1024 * 1024)))
    IMPORT_MAX_MANIFEST_BYTES = int(os.getenv('IMPORT_MAX_MANIFEST_BYTES', str(4 * 1024 * 1024)))
    IMPORT_MAX_STATE_BYTES = int(os.getenv('IMPORT_MAX_STATE_BYTES', str(128 * 1024 * 1024)))
    IMPORT_MAX_FILES = int(os.getenv('IMPORT_MAX_FILES', '5000'))
    PERMISSIONS_POLICY = os.getenv(
        'PERMISSIONS_POLICY',
        'geolocation=(), microphone=(), camera=(), interest-cohort=()'
    )
    TRUST_PROXY = os.getenv('TRUST_PROXY', '0') in {'1','true','True','yes','on'}
    PROXY_FIX_X_FOR = int(os.getenv('PROXY_FIX_X_FOR', '1'))
    PROXY_FIX_X_PROTO = int(os.getenv('PROXY_FIX_X_PROTO', '1'))
    PROXY_FIX_X_HOST = int(os.getenv('PROXY_FIX_X_HOST', '0'))
    ADMIN_WRITE_RATE_LIMIT = int(os.getenv('ADMIN_WRITE_RATE_LIMIT', '120'))
    ADMIN_WRITE_RATE_WINDOW_SECONDS = int(os.getenv('ADMIN_WRITE_RATE_WINDOW_SECONDS', '60'))
    BACKGROUND_JOB_WORKERS = configured_count(
        'BACKGROUND_JOB_WORKERS',
        automatic=automatic_background_workers(),
        maximum=4,
    )
    BACKGROUND_JOB_MAX_PENDING = max(
        BACKGROUND_JOB_WORKERS,
        int(os.getenv('BACKGROUND_JOB_MAX_PENDING', '4')),
    )
    IMPORT_MAX_JSONL_LINES = int(os.getenv('IMPORT_MAX_JSONL_LINES', '20000'))
    IMPORT_MAX_UNPACKED_BYTES = int(os.getenv('IMPORT_MAX_UNPACKED_BYTES', str(1024 * 1024 * 1024)))
    ASSET_REMOTE_MAX_BYTES = int(os.getenv('ASSET_REMOTE_MAX_BYTES', str(64 * 1024 * 1024)))
    ASSET_DOWNLOAD_TIMEOUT_SECONDS = float(os.getenv('ASSET_DOWNLOAD_TIMEOUT_SECONDS', '10'))
    # Per-socket timeout above; this is the wall-clock bound for one complete
    # download including every retry, so a slow-drip server cannot pin a
    # background worker indefinitely.
    ASSET_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = max(
        ASSET_DOWNLOAD_TIMEOUT_SECONDS,
        float(os.getenv('ASSET_DOWNLOAD_TOTAL_TIMEOUT_SECONDS', '120')),
    )
    ASSET_DOWNLOAD_MAX_ATTEMPTS = max(1, int(os.getenv('ASSET_DOWNLOAD_MAX_ATTEMPTS', '3')))
    ASSET_RECOVERY_MAX_ASSETS_PER_RUN = max(1, int(os.getenv('ASSET_RECOVERY_MAX_ASSETS_PER_RUN', '30')))
    ASSET_RECOVERY_MAX_RUN_ATTEMPTS = max(1, int(os.getenv('ASSET_RECOVERY_MAX_RUN_ATTEMPTS', '3')))
    ASSET_RECOVERY_TIME_BUDGET_SECONDS = max(1.0, float(os.getenv('ASSET_RECOVERY_TIME_BUDGET_SECONDS', '75')))
    ASSET_RECOVERY_BACKOFF_HOURS = max(1.0, float(os.getenv('ASSET_RECOVERY_BACKOFF_HOURS', '6')))
    ASSET_ALLOWED_DOMAINS = {
        d.strip().lower()
        for d in os.getenv('ASSET_ALLOWED_DOMAINS', '').split(',')
        if d.strip()
    }
    # Dashboard ZIP exports preserve DB-tracked remote assets by materializing
    # them into the archive before it is finalized. ``*`` means every public
    # HTTP(S) source host; entity_links are never materialized, so ordinary web
    # links remain links. The existing allow-list can still narrow the export
    # when an installation explicitly sets PORTABLE_EXPORT_PRESERVE_DOMAINS.
    PORTABLE_EXPORT_PRESERVE_DOMAINS = {
        d.strip().lower().rstrip('.')
        for d in os.getenv('PORTABLE_EXPORT_PRESERVE_DOMAINS', '*').split(',')
        if d.strip()
    }

    @classmethod
    def resolve_paths(cls) -> None:
        if cls.ENV == 'production' and cls.DEBUG:
            raise RuntimeError('DEBUG=True is not allowed in production')
        if cls.ENV == 'production':
            required_values = {
                "SECRET_KEY": cls.SECRET_KEY,
                "ADMIN_USERNAME": cls.ADMIN_USERNAME,
                "ADMIN_PASSWORD_HASH": cls.ADMIN_PASSWORD_HASH,
                "TRUSTED_HOSTS": ",".join(cls.TRUSTED_HOSTS) if cls.TRUSTED_HOSTS else "",
                "DATABASE_PATH": os.getenv("DATABASE_PATH", ""),
                "ASSETS_DIR": os.getenv("ASSETS_DIR", ""),
                "EXPORT_DIR": os.getenv("EXPORT_DIR", ""),
                "LOG_DIR": os.getenv("LOG_DIR", ""),
                "CONFERENCES_DIR": os.getenv("CONFERENCES_DIR", ""),
                "RUNTIME_CONFIG_DIR": os.getenv("RUNTIME_CONFIG_DIR", "") or os.getenv("BANNER_SETTINGS_PATH", ""),
                "TMPDIR": os.getenv("TMPDIR", ""),
            }
            missing = [f"{name} (missing value)" for name, value in required_values.items() if not value]
            if not cls.DATABASE_PATH.is_file():
                missing.append(f"DATABASE_PATH={cls.DATABASE_PATH} (missing file)")
            if not cls.ASSETS_DIR.is_dir():
                missing.append(f"ASSETS_DIR={cls.ASSETS_DIR} (missing directory)")
            if missing:
                details = "; ".join(missing)
                raise RuntimeError(
                    "Production data is missing or configuration is incomplete. "
                    f"Provision the required settings, database and assets before starting: {details}"
                )
        from .runtime_storage import RuntimeStorage, prepare_runtime_storage

        prepare_runtime_storage(
            RuntimeStorage(
                database=cls.DATABASE_PATH,
                assets=cls.ASSETS_DIR,
                exports=cls.EXPORT_DIR,
                logs=cls.LOG_DIR,
                conferences=cls.CONFERENCES_DIR,
                config_dir=cls.RUNTIME_CONFIG_DIR,
                temporary=cls.TMP_DIR,
            ),
            require_database=cls.ENV == "production",
            harden_permissions=cls.ENV == "production",
            minimum_free_bytes=cls.STORAGE_MIN_FREE_BYTES,
            export_max_files=cls.EXPORT_MAX_FILES,
            export_max_bytes=cls.EXPORT_MAX_BYTES,
            export_retention_days=cls.EXPORT_RETENTION_DAYS,
        )

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


def _flask_cfg(name: str, default=None):
    return (PROJECT_CONFIG.get("flask") or {}).get(name, default)


def _path_from_config(name: str, env_name: str | None = None, default: str | None = None) -> Path:
    value = os.getenv(env_name or name.upper()) or default
    if not value:
        raise RuntimeError(f"Missing required config value: {name}")
    path = Path(value)
    if not path.is_absolute():
        # Relative runtime paths are resolved from CORE. A leading ``CORE/``
        # segment is accepted for compatibility and stripped before resolution.
        if path.parts and path.parts[0].casefold() == BASE_DIR.name.casefold():
            path = Path(*path.parts[1:])
        path = BASE_DIR / path
    return path.resolve()


class Config:
    CONFIG_PATH = Path(PROJECT_CONFIG["_config_path"])
    ENV = os.getenv('FLASK_ENV', os.getenv('ENV', str(_flask_cfg("environment"))))
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
    JOIN_REQUIRE_INVITATION_CODE = os.getenv('JOIN_REQUIRE_INVITATION_CODE', '1') in {'1','true','True','yes','on'}
    JOIN_MAX_PER_IP_HOUR = int(os.getenv('JOIN_MAX_PER_IP_HOUR', '5'))
    JOIN_STORE_RAW_IP = os.getenv('JOIN_STORE_RAW_IP', '0') in {'1','true','True','yes','on'}
    MAIL_PROVIDER = os.getenv('MAIL_PROVIDER', 'disabled').strip().lower()
    MAIL_FROM = os.getenv('MAIL_FROM', 'no-reply@mifp.eu')
    MAIL_TO = os.getenv('MAIL_TO', 'info@mifp.eu')
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', '1') in {'1','true','True','yes','on'}
    DATABASE_PATH = _path_from_config('db_path', 'DATABASE_PATH', '../DATABASE/mifp.db')
    ASSETS_DIR = _path_from_config('assets_dir', 'ASSETS_DIR', '../DATABASE/assets')
    EXPORT_DIR = _path_from_config('export_dir', 'EXPORT_DIR', '../DATABASE/exports')
    STORAGE_MIN_FREE_MB = max(0, int(os.getenv('STORAGE_MIN_FREE_MB', '1024' if ENV == 'production' else '0')))
    STORAGE_MIN_FREE_BYTES = STORAGE_MIN_FREE_MB * 1024 * 1024
    EXPORT_RETENTION_DAYS = max(0, int(os.getenv('EXPORT_RETENTION_DAYS', '1')))
    EXPORT_MAX_FILES = max(1, int(os.getenv('EXPORT_MAX_FILES', '30')))
    EXPORT_MAX_BYTES = max(1, int(os.getenv('EXPORT_MAX_MB', '2048'))) * 1024 * 1024
    AUTO_MIGRATE_ON_STARTUP = os.getenv(
        'AUTO_MIGRATE_ON_STARTUP', '0' if ENV == 'production' else '1'
    ) in {'1','true','True','yes','on'}
    SITE_SETTINGS_CACHE_SECONDS = int(os.getenv('SITE_SETTINGS_CACHE_SECONDS', '30'))
    BANNER_SETTINGS_PATH = _path_from_config(
        "banner_settings_path",
        "BANNER_SETTINGS_PATH",
        "config/banner_settings.json",
    )
    CONFERENCES_DIR = _path_from_config(
        "conferences_dir", "CONFERENCES_DIR", "../DATABASE/conferences"
    )
    LOG_DIR = _path_from_config('log_dir', 'LOG_DIR', '../DATABASE/logs')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = os.getenv(
        'LOG_FORMAT',
        'json' if os.getenv('LOG_JSON', '0') in {'1','true','True','yes','on'} else 'text',
    ).strip().lower()
    LOG_JSON = LOG_FORMAT == 'json'
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
    PAGE_VIEWS_RETENTION_DAYS = int(os.getenv('PAGE_VIEWS_RETENTION_DAYS', '365'))
    PRIVACY_SAFE_METRICS_ENABLED = os.getenv('PRIVACY_SAFE_METRICS_ENABLED', '1') in {'1','true','True','yes','on'}
    PRIVACY_SAFE_METRICS_RETENTION_DAYS = int(os.getenv('PRIVACY_SAFE_METRICS_RETENTION_DAYS', '730'))
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH_MB', '768')) * 1024 * 1024
    FLASK_HOST = os.getenv('FLASK_HOST', '127.0.0.1')
    FLASK_PORT = int(os.getenv('FLASK_PORT', '8000'))
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', '0').lower() in ('1', 'true')
    INTERNAL_DOMAINS = {
        d.strip() for d in os.getenv('INTERNAL_DOMAINS', 'mifp.eu,www.mifp.eu,old.mifp.eu,events.mifp.eu').split(',')
        if d.strip()
    }
    MIRROR_DEFAULT_HOST = os.getenv('MIRROR_DEFAULT_HOST', 'www.mifp.eu')
    SITE_DEFAULTS = dict(_cfg("site_defaults", {}))
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
    UPLOAD_ALLOWED_EXTENSIONS = {
        ext.strip().lower()
        for ext in os.getenv('UPLOAD_ALLOWED_EXTENSIONS', 'jpg,jpeg,png,gif,webp,svg,pdf,doc,docx,xls,xlsx,ppt,pptx,zip,mp4,mov,txt,csv,json').split(',')
        if ext.strip()
    }
    LOGIN_MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', '5'))
    LOGIN_LOCKOUT_SECONDS = int(os.getenv('LOGIN_LOCKOUT_SECONDS', '60'))
    LOGIN_IP_MAX_ATTEMPTS = int(os.getenv('LOGIN_IP_MAX_ATTEMPTS', '10'))
    IMPORT_MAX_ZIP_BYTES = int(os.getenv('IMPORT_MAX_ZIP_BYTES', str(768 * 1024 * 1024)))
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
            ),
            require_database=cls.ENV == "production",
            harden_permissions=cls.ENV == "production",
            minimum_free_bytes=cls.STORAGE_MIN_FREE_BYTES,
            export_max_files=cls.EXPORT_MAX_FILES,
            export_max_bytes=cls.EXPORT_MAX_BYTES,
            export_retention_days=cls.EXPORT_RETENTION_DAYS,
        )

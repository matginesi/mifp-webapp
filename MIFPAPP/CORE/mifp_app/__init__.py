from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

_CSRF_TIMEOUT = 7200  # stateless CSRF token expiry (seconds)
def _stateless_csrf_token() -> str:
    """HMAC-signed CSRF token for anonymous visitors (no session cookie)."""
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    secret_key = current_app.secret_key
    if not isinstance(secret_key, bytes):
        secret_key = str(secret_key or "").encode()
    sig = hmac.new(
        secret_key,
        f"{nonce}:{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()[:16]
    return f"{ts}:{nonce}:{sig}"


def _validate_stateless_csrf(token: str) -> bool:
    try:
        ts_str, nonce, sig = token.split(":", 2)
        ts = int(ts_str)
        if time.time() - ts > _CSRF_TIMEOUT:
            return False
        secret_key = current_app.secret_key
        if not isinstance(secret_key, bytes):
            secret_key = str(secret_key or "").encode()
        expected = hmac.new(
            secret_key,
            f"{nonce}:{ts_str}".encode(),
            hashlib.sha256,
        ).hexdigest()[:16]
        return secrets.compare_digest(expected, sig)
    except (ValueError, IndexError):
        return False


def _csrf_token() -> str:
    if session.get("admin_logged_in"):
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token
    return _stateless_csrf_token()


def _is_tmpfs(path) -> bool:
    """Return True when path lives on a tmpfs (ephemeral RAM) filesystem."""
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    nodev = getattr(os, "ST_NODEV", 0)
    try:
        return bool(os.statvfs(candidate).f_flag & nodev)
    except (OSError, ValueError):
        return False


def create_app():
    import logging

    from flask import Flask, flash, g, jsonify, redirect, render_template, url_for
    from werkzeug.exceptions import HTTPException

    from .config import Config
    from .db.connection import connect, connect_readonly
    from .db.migrations import migrate_content_schema
    from .routes.auth import bp as auth_bp
    from .routes.dashboard import bp as dashboard_bp
    from .routes.maintenance import bp as maintenance_bp
    from .routes.maintenance import maintenance_gate
    from .routes.public import bp as public_bp
    from .utils.logger import (
        audit_log,
        get_logger,
        init_request_logging,
        log_event,
        log_exception,
        security_event,
        setup_logging,
    )
    from .utils.security import get_client_ip, ip_rate_allowed

    Config.resolve_paths()
    setup_logging(
        Config.LOG_DIR,
        Config.LOG_LEVEL,
        json_logs=Config.LOG_JSON,
        log_format=Config.LOG_FORMAT,
        output=Config.LOG_OUTPUT,
        max_bytes=Config.LOG_MAX_BYTES,
        backup_count=Config.LOG_BACKUP_COUNT,
        access_enabled=Config.LOG_ACCESS_ENABLED,
        audit_enabled=Config.LOG_AUDIT_ENABLED,
        security_enabled=Config.LOG_SECURITY_ENABLED,
        colors=Config.LOG_COLORS,
    )
    from .services.operation_maintenance import clear_stale_operation_marker
    clear_stale_operation_marker(
        Config.DATABASE_PATH,
        logger=get_logger("startup"),
    )
    if Config.AUTO_MIGRATE_ON_STARTUP:
        try:
            from .services.admin_safety import backup_sqlite_database
            backup_sqlite_database(Config.DATABASE_PATH, label="pre-auto-migrate")
            with connect(Config.DATABASE_PATH) as conn:
                migrate_content_schema(conn)
        except Exception:
            log_exception(get_logger("startup"), "app.migration_failed", "Database migration failed")
            raise
    app = Flask(__name__)
    mimetypes.add_type('font/woff2', '.woff2')
    app.config.from_object(Config)
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=int(app.config.get("PROXY_FIX_X_FOR", 1)),
            x_proto=int(app.config.get("PROXY_FIX_X_PROTO", 1)),
            x_host=int(app.config.get("PROXY_FIX_X_HOST", 0)),
        )
    init_request_logging(app, db_path=str(Config.DATABASE_PATH))
    app.register_blueprint(public_bp)
    app.register_blueprint(maintenance_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    @app.cli.command("db-upgrade")
    def db_upgrade_command():
        """Back up, migrate and verify the configured SQLite database."""
        import click

        from .services.admin_safety import backup_sqlite_database

        backup = backup_sqlite_database(Config.DATABASE_PATH, label="pre-migration")
        with connect(Config.DATABASE_PATH) as conn:
            report = migrate_content_schema(conn)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise click.ClickException(f"SQLite integrity check failed: {integrity}")
        click.echo(f"Database migration complete. Backup: {backup or 'not created'}")
        click.echo(f"Migration report: {json.dumps(report, default=str, sort_keys=True)}")

    app.before_request(maintenance_gate)

    def _wants_json_response() -> bool:
        if request.path.startswith("/api") or request.path.endswith(".json"):
            return True
        if request.args.get("format") == "json":
            return True
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return True
        best = request.accept_mimetypes.best
        return best == "application/json" and (
            request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
        )

    @app.before_request
    def ensure_csrf_token():
        """Ensure a CSRF token exists for the current user.
        Logged-in admins: session-based token (creates session cookie).
        Anonymous visitors: stateless HMAC token (no cookie)."""
        if session.get("admin_logged_in") and "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)

    @app.before_request
    def validate_host():
        trusted = app.config.get("TRUSTED_HOSTS")
        if not trusted:
            return None
        if request.path.startswith(("/static/", "/media/", "/health", "/ready")):
            return None
        host = request.host.split(":")[0].lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return None
        if host in {h.strip().lower() for h in trusted}:
            return None
        security_event("host.rejected", "Host header not in TRUSTED_HOSTS", severity="warning", path=request.path, host=host)
        return "Forbidden", 403

    import os as _os
    _static_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static")
    # One startup-time cache version for all local frontend assets. The old
    # implementation only watched dashboard.css, so JS-only deployments could
    # keep serving stale dashboard logic from the browser cache.
    _static_mtimes = []
    for _root, _dirs, _files in _os.walk(_static_dir):
        for _name in _files:
            if _name.endswith((".css", ".js")):
                try:
                    _static_mtimes.append(_os.path.getmtime(_os.path.join(_root, _name)))
                except OSError:
                    pass
    _static_ver = str(int(max(_static_mtimes, default=_os.path.getmtime(_static_dir))))

    @app.context_processor
    def inject_security_context():
        from datetime import datetime

        from .services.site_copy import copy_values
        token = _csrf_token()
        site_settings = dict(Config.SITE_DEFAULTS)
        if not getattr(g, "maintenance_active", False):
            try:
                with connect_readonly(Config.DATABASE_PATH) as conn:
                    rows = conn.execute("SELECT key, value FROM settings").fetchall()
                    site_settings.update({r["key"]: r["value"] for r in rows})
            except Exception:
                pass
        try:
            _banner_path = Path(app.config["BANNER_SETTINGS_PATH"])
            if _banner_path.exists():
                site_settings.update(json.loads(_banner_path.read_text()))
        except Exception:
            pass
        return {
            "csrf_token": token, "csp_nonce": getattr(g, "csp_nonce", ""),
            "now": datetime.now(), "site_settings": site_settings,
            "site_copy": copy_values(site_settings),
            "static_version": _static_ver,
        }

    @app.before_request
    def validate_csrf():
        if not app.config.get("WTF_CSRF_ENABLED", True):
            return None
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        supplied = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
        is_logged_in = session.get("admin_logged_in")

        # Tokens remain the primary CSRF control. When browsers send Origin or
        # Referer, also reject cross-origin admin writes as defense in depth.
        if is_logged_in and request.path.startswith("/dashboard/"):
            source = request.headers.get("Origin") or request.headers.get("Referer")
            if source:
                source_parts = urlsplit(source)
                expected_parts = urlsplit(request.host_url)
                if (
                    source_parts.scheme not in {"http", "https"}
                    or source_parts.netloc.casefold() != expected_parts.netloc.casefold()
                ):
                    security_event(
                        "csrf.origin_rejected",
                        "cross-origin dashboard write rejected",
                        severity="warning",
                        ip=get_client_ip(),
                        path=request.path,
                    )
                    return jsonify({
                        "error": "origin_rejected",
                        "request_id": getattr(g, "request_id", "-"),
                    }), 403

        if request.path == "/login" and request.method == "POST":
            if supplied and (_validate_stateless_csrf(supplied) if not is_logged_in else (
                session.get("_csrf_token") and secrets.compare_digest(session["_csrf_token"], supplied)
            )):
                return None
            security_event("csrf.failed", "CSRF token mismatch on login", path="/login", ip=get_client_ip())
            flash("Session expired. Please try again.", "warning")
            return redirect(url_for("auth.login"))

        if is_logged_in:
            expected = session.get("_csrf_token")
            if expected and supplied and secrets.compare_digest(expected, supplied):
                return None
            session["_csrf_token"] = secrets.token_urlsafe(32)
        else:
            if supplied and _validate_stateless_csrf(supplied):
                return None

        security_event("csrf.failed", "CSRF validation failed", severity="warning", ip=get_client_ip(), path=request.path, method=request.method)
        rid = getattr(g, "request_id", "-")
        if _wants_json_response():
            return jsonify({"error": "csrf_failed", "request_id": rid}), 400
        return render_template("errors/error.html", code=400, title="Bad Request",
                               message="The form has expired or the session is invalid. Please go back and try again.",
                               request_id=rid), 400

    @app.before_request
    def rate_limit_admin_writes():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if not request.path.startswith("/dashboard/"):
            return None
        limit = int(app.config.get("ADMIN_WRITE_RATE_LIMIT", 120))
        window = int(app.config.get("ADMIN_WRITE_RATE_WINDOW_SECONDS", 60))
        if limit <= 0 or window <= 0:
            return None
        key = f"{session.get('admin_username') or 'anon'}:{get_client_ip()}"
        if not ip_rate_allowed("admin_write", key, limit=limit, window_seconds=window):
            security_event("admin.write_rate_limited", "dashboard write rate limit exceeded", severity="warning", ip=get_client_ip(), path=request.path)
            return jsonify({"error": "rate_limited"}), 429
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if (
            getattr(g, "maintenance_active", False)
            or response.status_code >= 400
            or request.path.startswith(("/dashboard", "/login", "/logout"))
        ):
            # Never cache maintenance/error bodies under a public or media URL.
            # Otherwise a browser can keep serving a stale Work in Progress
            # page after the protected operation has already completed.
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        elif request.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
        elif request.path.startswith("/media/"):
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        # CSP: scripts and stylesheet blocks use per-request nonces; the remaining
        # style attributes are limited to server-generated presentation values.
        csp = app.config.get(
            "CONTENT_SECURITY_POLICY",
            "default-src 'self'; "
            "img-src 'self' data: blob: https:; "
            "style-src-elem 'self'; style-src-attr 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-src 'none'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        nonce = getattr(g, "csp_nonce", "")
        if nonce:
            csp = csp.replace("script-src 'self'", f"script-src 'self' 'nonce-{nonce}'", 1)
            csp = csp.replace("style-src-elem 'self'", f"style-src-elem 'self' 'nonce-{nonce}'", 1)
        response.headers["Content-Security-Policy"] = csp
        response.headers.setdefault("Permissions-Policy", app.config.get("PERMISSIONS_POLICY", "geolocation=(), microphone=(), camera=(), interest-cohort=()"))
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # HSTS: only when behind real HTTPS (TRUST_PROXY or native HTTPS)
        if app.config.get("TRUST_PROXY") or request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", app.config.get("HSTS_VALUE", "max-age=31536000; includeSubDomains"))
        return response


    @app.get('/health')
    def health():
        from .runtime_storage import available_bytes

        db_path = Config.DATABASE_PATH
        db_ok = False
        if db_path.exists():
            try:
                with connect(db_path) as conn:
                    conn.execute("SELECT 1").fetchone()
                db_ok = True
            except Exception:
                pass
        status = "degraded" if not db_ok else "ok"
        storage_free = {
            "database": available_bytes(db_path.parent),
            "assets": available_bytes(Config.ASSETS_DIR),
            "exports": available_bytes(Config.EXPORT_DIR),
            "logs": available_bytes(Config.LOG_DIR),
        }
        storage_ok = all(free >= Config.STORAGE_MIN_FREE_BYTES for free in storage_free.values())
        if not storage_ok:
            status = "degraded"
        return jsonify({
            "status": status,
            "database_exists": db_path.exists(),
            "database_ok": db_ok,
            "assets_dir_exists": Config.ASSETS_DIR.exists(),
            "exports_dir_exists": Config.EXPORT_DIR.exists(),
            "backups_dir_exists": (Config.DATABASE_PATH.parent / "backups").exists(),
            "log_dir_exists": Config.LOG_DIR.exists(),
            "storage_ok": storage_ok,
            "storage_free_bytes": storage_free,
            "storage_reserve_bytes": Config.STORAGE_MIN_FREE_BYTES,
        })

    @app.get('/ready')
    def ready():
        from .runtime_storage import available_bytes

        db_path = Config.DATABASE_PATH
        try:
            with connect(db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            storage_paths = {
                "database": db_path.parent,
                "assets": Config.ASSETS_DIR,
                "exports": Config.EXPORT_DIR,
                "logs": Config.LOG_DIR,
            }
            free = {name: available_bytes(path) for name, path in storage_paths.items()}
            # tmpfs directories are ephemeral and bounded independently, so
            # only durable storage must honor the safety reserve.
            low = {}
            for name, path in storage_paths.items():
                if _is_tmpfs(path):
                    continue
                value = free[name]
                if value < Config.STORAGE_MIN_FREE_BYTES:
                    low[name] = value
            if low:
                logging.getLogger('mifp.flask').error(
                    "Readiness check failed: storage reserve crossed low=%s reserve_bytes=%s",
                    low,
                    Config.STORAGE_MIN_FREE_BYTES,
                )
                return jsonify({
                    "status": "error",
                    "database": "ok",
                    "storage": "low",
                    "storage_free_bytes": free,
                    "storage_reserve_bytes": Config.STORAGE_MIN_FREE_BYTES,
                }), 503
            return jsonify({
                "status": "ok",
                "database": "ok",
                "storage": "ok",
                "storage_free_bytes": free,
                "storage_reserve_bytes": Config.STORAGE_MIN_FREE_BYTES,
            })
        except Exception as e:
            logging.getLogger('mifp.flask').warning("Readiness check failed: %s", type(e).__name__)
            return jsonify({"status": "error", "database": "unavailable"}), 503

    @app.errorhandler(403)
    def forbidden(exc):
        rid = getattr(g, "request_id", "-")
        if _wants_json_response():
            return jsonify({'error': 'forbidden', 'request_id': rid}), 403
        return render_template("errors/error.html", code=403, title="Forbidden",
                               message="You do not have permission to access this resource.",
                               request_id=rid), 403

    @app.errorhandler(404)
    def not_found(exc):
        rid = getattr(g, "request_id", "-")
        if _wants_json_response():
            return jsonify({'error': 'not_found', 'request_id': rid}), 404
        return render_template("errors/error.html", code=404, title="Page Not Found",
                               message="The requested resource does not exist.", request_id=rid), 404

    @app.errorhandler(413)
    def too_large(exc):
        rid = getattr(g, "request_id", "-")
        max_mb = app.config.get("MAX_CONTENT_LENGTH", 32 * 1024 * 1024) // (1024 * 1024)
        if _wants_json_response():
            return jsonify({'error': 'file_too_large', 'max_mb': max_mb, 'request_id': rid}), 413
        return render_template("errors/error.html", code=413, title="File Too Large",
                               message=f"The file exceeds the {max_mb} MB limit.",
                               request_id=rid), 413

    @app.errorhandler(418)
    def teapot(exc):
        rid = getattr(g, "request_id", "-")
        if _wants_json_response():
            return jsonify({'error': 'teapot', 'request_id': rid}), 418
        return render_template("errors/error.html", code=418, title="I'm a Teapot",
                               message="The server refuses to brew coffee because it is, permanently, a teapot.",
                               request_id=rid), 418

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        rid = getattr(g, "request_id", "-")
        if isinstance(exc, HTTPException):
            if _wants_json_response():
                return jsonify({'error': exc.name.lower().replace(" ", "_"), 'request_id': rid}), exc.code
            return render_template("errors/error.html", code=exc.code, title=exc.name, message=exc.description, request_id=rid), exc.code
        logging.getLogger('mifp.flask').error(
            f'Unhandled Flask error: {type(exc).__name__}: {exc}',
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        if _wants_json_response():
            return jsonify({'error': 'internal_error', 'request_id': rid}), 500
        return render_template("errors/error.html", code=500, title="Internal Server Error",
                               message="Something went wrong. Details have been saved to the logs.",
                               request_id=rid), 500

    log_event(
        get_logger("startup"),
        "app.ready",
        "MIFP application ready",
        environment=Config.ENV,
        host=Config.FLASK_HOST,
        port=Config.FLASK_PORT,
        database=str(Config.DATABASE_PATH),
        log_output=Config.LOG_OUTPUT,
        admin_username=Config.ADMIN_USERNAME or "admin",
        admin_configured=bool(Config.ADMIN_PASSWORD_HASH),
    )
    return app

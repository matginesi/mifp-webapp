from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit


_CSRF_TIMEOUT = 7200  # stateless CSRF token expiry (seconds)
_CSRF_COOKIE_NAME = "mifp_csrf"


def _valid_csrf_client(value: str) -> bool:
    return len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


def _csrf_client_value() -> str:
    """Return the anonymous double-submit binding value for this client.

    The signed token alone is replayable: anybody can fetch a fresh one from a
    public form and embed it in a cross-site POST. Binding the signature to a
    random value that is also stored in a SameSite cookie means the attacker's
    page can obtain a token but cannot make the victim's browser send the
    matching cookie, so the signature no longer validates. The cookie is created
    here and written by :func:`issue_csrf_cookie` so a token rendered in this
    request is always bound to the value the response will set.
    """
    from flask import g, request

    cached = getattr(g, "_mifp_csrf_client", None)
    if cached:
        return cached
    value = request.cookies.get(_CSRF_COOKIE_NAME, "")
    if not _valid_csrf_client(value):
        value = secrets.token_hex(16)
        g._mifp_csrf_client_issued = value
    g._mifp_csrf_client = value
    return value


def _stateless_csrf_token() -> str:
    """HMAC-signed CSRF token for anonymous visitors (no session cookie)."""
    from flask import current_app
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    secret_key = current_app.secret_key
    if not isinstance(secret_key, bytes):
        secret_key = str(secret_key or "").encode()
    sig = hmac.new(
        secret_key,
        f"{nonce}:{ts}:{_csrf_client_value()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{ts}:{nonce}:{sig}"


def _validate_stateless_csrf(token: str) -> bool:
    from flask import current_app, request
    try:
        ts_str, nonce, sig = token.split(":", 2)
        ts = int(ts_str)
        now = time.time()
        # Reject expired tokens and timestamps implausibly far in the future.
        # A small skew allowance avoids failures on hosts whose clocks differ by
        # a few seconds without turning a future timestamp into a non-expiring
        # token.
        if ts < 0 or now - ts > _CSRF_TIMEOUT or ts - now > 60:
            return False
        if len(nonce) != 16 or any(ch not in "0123456789abcdef" for ch in nonce):
            return False
        if len(sig) != 64 or any(ch not in "0123456789abcdef" for ch in sig):
            return False
        client = request.cookies.get(_CSRF_COOKIE_NAME, "")
        if not _valid_csrf_client(client):
            return False
        secret_key = current_app.secret_key
        if not isinstance(secret_key, bytes):
            secret_key = str(secret_key or "").encode()
        expected = hmac.new(
            secret_key,
            f"{nonce}:{ts_str}:{client}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return secrets.compare_digest(expected, sig)
    except (AttributeError, ValueError, IndexError):
        return False


def _csrf_token() -> str:
    from flask import session
    if session.get("admin_logged_in"):
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token
    return _stateless_csrf_token()


class _LazyCsrfToken:
    """Render-time CSRF token for the Jinja context.

    The context processor runs for **every** request, so minting the token there
    would bind an anonymous client and set the ``mifp_csrf`` cookie on ordinary
    read-only pages such as ``/``, ``/events`` or ``/privacy``. This object mints
    on first render instead, so a page that renders no form never receives the
    cookie, while every existing ``{{ csrf_token }}`` usage keeps working
    unchanged.

    It deliberately quacks like a string: Jinja renders it through ``__html__``
    or ``str()``, and inline ``{{ { 'csrfToken': csrf_token } }}`` blocks go
    through ``__repr__``, which is how the plain token behaved before.
    """

    __slots__ = ("_value",)

    def __init__(self) -> None:
        self._value: str | None = None

    def _mint(self) -> str:
        if self._value is None:
            self._value = _csrf_token()
        return self._value

    def __str__(self) -> str:
        return self._mint()

    def __repr__(self) -> str:
        return repr(self._mint())

    def __html__(self) -> str:
        from markupsafe import escape

        return str(escape(self._mint()))

    def __eq__(self, other: object) -> bool:
        return str(self) == other

    def __hash__(self) -> int:
        return hash(str(self))

    def __bool__(self) -> bool:
        return bool(self._mint())

    def __len__(self) -> int:
        return len(self._mint())


def _is_tmpfs(path) -> bool:
    """Return True when path lives on a tmpfs (ephemeral RAM) filesystem."""
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return False
    try:
        resolved = candidate.resolve()
        matches: list[tuple[int, str]] = []
        for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            left_fields = left.split()
            right_fields = right.split()
            if len(left_fields) < 5 or not right_fields:
                continue
            mount_point = Path(left_fields[4].replace("\\040", " "))
            try:
                resolved.relative_to(mount_point)
            except ValueError:
                continue
            matches.append((len(mount_point.parts), right_fields[0].casefold()))
        if not matches:
            return False
        return max(matches)[1] in {"tmpfs", "ramfs"}
    except (OSError, ValueError):
        return False


def create_app():
    import logging

    from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
    from werkzeug.exceptions import HTTPException
    from werkzeug.middleware.proxy_fix import ProxyFix

    from .config import Config
    from .db.connection import connect_readonly
    from .routes.auth import bp as auth_bp
    from .routes.dashboard import bp as dashboard_bp
    from .routes.maintenance import bp as maintenance_bp
    from .routes.maintenance import maintenance_gate
    from .routes.public import bp as public_bp
    from .utils.http import wants_json_response
    from .utils.logger import (
        audit_log,
        get_logger,
        init_request_logging,
        log_event,
        log_event_throttled,
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
        file_format=Config.LOG_FILE_FORMAT,
        console_format=Config.LOG_CONSOLE_FORMAT,
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

    app.before_request(maintenance_gate)

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
        # Parse the authority instead of splitting on ':' so IPv6 literals are
        # handled correctly. Local hosts are not special-cased: health checks
        # must be listed explicitly in TRUSTED_HOSTS like every other host.
        host = (urlsplit(f"//{request.host}").hostname or "").casefold()
        allowed = {str(value).strip().strip("[]").casefold() for value in trusted if str(value).strip()}
        if host and host in allowed:
            return None
        security_event("host.rejected", "Host header not in TRUSTED_HOSTS", severity="warning", path=request.path, host=host or request.host)
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
        from .services.versioning import application_version
        # Lazy on purpose: minting here would set `mifp_csrf` on every public page.
        token = _LazyCsrfToken()
        # Banner defaults first so the notice is always renderable, even on a
        # deployment that never saved banner settings; database site copy and then
        # the banner settings file override them.
        site_settings = {**Config.DEFAULT_BANNER_SETTINGS, **Config.SITE_DEFAULTS}
        if not getattr(g, "maintenance_active", False):
            try:
                with connect_readonly(Config.DATABASE_PATH) as conn:
                    rows = conn.execute("SELECT key, value FROM settings").fetchall()
                    site_settings.update({r["key"]: r["value"] for r in rows})
            except Exception as exc:
                log_event_throttled(
                    get_logger("runtime"),
                    "runtime.settings_read_failed",
                    "Runtime settings could not be read; defaults are in use",
                    interval_seconds=60,
                    error_type=type(exc).__name__,
                )
        try:
            _banner_path = Path(app.config["BANNER_SETTINGS_PATH"])
            if _banner_path.exists():
                site_settings.update(
                    Config.normalize_banner_settings(
                        json.loads(_banner_path.read_text(encoding="utf-8"))
                    )
                )
        except Exception as exc:
            log_event_throttled(
                get_logger("runtime"),
                "runtime.banner_settings_read_failed",
                "Banner settings could not be read; database/default values are in use",
                interval_seconds=60,
                error_type=type(exc).__name__,
            )
        return {
            "csrf_token": token, "csp_nonce": getattr(g, "csp_nonce", ""),
            "now": datetime.now(), "site_settings": site_settings,
            "site_copy": copy_values(site_settings),
            "static_version": _static_ver,
            "app_version": application_version(),
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
        # Referer, also reject cross-origin writes. This applies to every
        # state-changing request, not only authenticated dashboard writes: an
        # anonymous token can be fetched from a public form (/join, /login) and
        # replayed in a cross-site POST, and no session cookie is involved for
        # SameSite to protect. Requests without either header (non-browser
        # clients) still fall back to token validation alone.
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
                    "cross-origin write rejected",
                    severity="warning",
                    ip=get_client_ip(),
                    path=request.path,
                )
                rid = getattr(g, "request_id", "-")
                if wants_json_response():
                    return jsonify({"error": "origin_rejected", "request_id": rid}), 403
                return render_template(
                    "errors/error.html",
                    code=403,
                    title="Forbidden",
                    message="This form was submitted from another site and was rejected.",
                    request_id=rid,
                ), 403

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
        if wants_json_response():
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
    def issue_csrf_cookie(response):
        """Persist the anonymous CSRF client binding created for this response.

        Only set when a token was actually minted during the request, so pages
        that render no form never emit the cookie.
        """
        issued = getattr(g, "_mifp_csrf_client_issued", None)
        if issued:
            response.set_cookie(
                _CSRF_COOKIE_NAME,
                issued,
                max_age=_CSRF_TIMEOUT,
                httponly=True,
                secure=bool(app.config.get("SESSION_COOKIE_SECURE", False)),
                samesite=str(app.config.get("SESSION_COOKIE_SAMESITE", "Lax")),
                path="/",
            )
        return response

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
        # ProxyFix normalizes request.is_secure from the trusted reverse proxy.
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", app.config.get("HSTS_VALUE", "max-age=31536000; includeSubDomains"))
        return response


    @app.get('/health')
    def health():
        # Public liveness endpoint: production intentionally exposes no storage
        # or database internals.  /ready is a localhost-only deployment gate.
        if app.config.get("ENV") == "production":
            return jsonify({"status": "ok"})

        from .runtime_storage import available_bytes

        db_path = Config.DATABASE_PATH
        db_ok = False
        if db_path.exists():
            try:
                with connect_readonly(db_path, timeout=1.0) as conn:
                    conn.execute("SELECT 1").fetchone()
                db_ok = True
            except Exception as exc:
                log_event_throttled(
                    get_logger("health"),
                    "health.database_check_failed",
                    "Health database check failed",
                    interval_seconds=60,
                    error_type=type(exc).__name__,
                )
        status = "degraded" if not db_ok else "ok"
        storage_free = {
            "database": available_bytes(db_path.parent),
            "assets": available_bytes(Config.ASSETS_DIR),
            "conferences": available_bytes(Config.CONFERENCES_DIR),
            "config": available_bytes(Config.RUNTIME_CONFIG_DIR),
            "exports": available_bytes(Config.EXPORT_DIR),
            "logs": available_bytes(Config.LOG_DIR),
            "temporary": available_bytes(Config.TMP_DIR),
        }
        storage_ok = all(free >= Config.STORAGE_MIN_FREE_BYTES for free in storage_free.values())
        if not storage_ok:
            status = "degraded"
        return jsonify({
            "status": status,
            "database_exists": db_path.exists(),
            "database_ok": db_ok,
            "assets_dir_exists": Config.ASSETS_DIR.exists(),
            "conferences_dir_exists": Config.CONFERENCES_DIR.exists(),
            "config_dir_exists": Config.RUNTIME_CONFIG_DIR.exists(),
            "exports_dir_exists": Config.EXPORT_DIR.exists(),
            "backups_dir_exists": (Config.DATABASE_PATH.parent / "backups").exists(),
            "log_dir_exists": Config.LOG_DIR.exists(),
            "tmp_dir_exists": Config.TMP_DIR.exists(),
            "storage_ok": storage_ok,
            "storage_free_bytes": storage_free,
            "storage_reserve_bytes": Config.STORAGE_MIN_FREE_BYTES,
        })

    @app.get('/ready')
    def ready():
        from .runtime_storage import available_bytes

        db_path = Config.DATABASE_PATH
        try:
            with connect_readonly(db_path, timeout=1.0) as conn:
                conn.execute("SELECT 1").fetchone()
            storage_paths = {
                "database": db_path.parent,
                "assets": Config.ASSETS_DIR,
                "conferences": Config.CONFERENCES_DIR,
                "config": Config.RUNTIME_CONFIG_DIR,
                "exports": Config.EXPORT_DIR,
                "logs": Config.LOG_DIR,
                "temporary": Config.TMP_DIR,
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
        if wants_json_response():
            return jsonify({'error': 'forbidden', 'request_id': rid}), 403
        return render_template("errors/error.html", code=403, title="Forbidden",
                               message="You do not have permission to access this resource.",
                               request_id=rid), 403

    @app.errorhandler(404)
    def not_found(exc):
        rid = getattr(g, "request_id", "-")
        if wants_json_response():
            return jsonify({'error': 'not_found', 'request_id': rid}), 404
        return render_template("errors/error.html", code=404, title="Page Not Found",
                               message="The requested resource does not exist.", request_id=rid), 404

    @app.errorhandler(413)
    def too_large(exc):
        rid = getattr(g, "request_id", "-")
        max_mb = app.config.get("MAX_CONTENT_LENGTH", 32 * 1024 * 1024) // (1024 * 1024)
        content_length = request.content_length
        app.logger.warning(
            "request rejected: payload too large path=%s bytes=%s max_mb=%s request_id=%s",
            request.path, content_length, max_mb, rid,
        )
        if request.path == "/dashboard/data-portability/import":
            audit_log(
                "import.rejected_too_large",
                "data portability import rejected before processing",
                category="admin",
                outcome="failure",
                request_bytes=content_length,
                max_bytes=app.config.get("MAX_CONTENT_LENGTH"),
                request_id=rid,
            )
        message = (
            f"This upload exceeds the {max_mb} MB limit. "
            "Select the files together from Data portability: the dashboard will upload large packages sequentially."
        )
        if wants_json_response():
            return jsonify({
                'error': 'file_too_large',
                'message': message,
                'max_mb': max_mb,
                'request_id': rid,
            }), 413
        return render_template("errors/error.html", code=413, title="File Too Large",
                               message=message,
                               request_id=rid), 413

    @app.errorhandler(418)
    def teapot(exc):
        rid = getattr(g, "request_id", "-")
        if wants_json_response():
            return jsonify({'error': 'teapot', 'request_id': rid}), 418
        return render_template("errors/error.html", code=418, title="I'm a Teapot",
                               message="The server refuses to brew coffee because it is, permanently, a teapot.",
                               request_id=rid), 418

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        rid = getattr(g, "request_id", "-")
        if isinstance(exc, HTTPException):
            if wants_json_response():
                return jsonify({'error': exc.name.lower().replace(" ", "_"), 'request_id': rid}), exc.code
            return render_template("errors/error.html", code=exc.code, title=exc.name, message=exc.description, request_id=rid), exc.code
        logging.getLogger('mifp.flask').error(
            f'Unhandled Flask error: {type(exc).__name__}: {exc}',
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        if wants_json_response():
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

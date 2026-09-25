from __future__ import annotations

import hashlib
import hmac
import time
from functools import wraps
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ..services.notifications import notify
from ..utils.http import is_safe_relative_url, wants_json_response
from ..utils.logger import audit_log, security_event
from ..utils.security import admin_password_matches, get_client_ip, ip_rate_allowed

bp = Blueprint("auth", __name__)


def _auth_dedup(kind: str, ip: str) -> str:
    digest = hashlib.sha256(str(ip or "unknown").encode("utf-8", "replace")).hexdigest()[:20]
    return f"auth:{kind}:{digest}"


def _notify_auth(
    *,
    event: str,
    category: str,
    severity: str,
    subject: str,
    body: str,
    dedup_kind: str,
    threshold: int | None = None,
) -> None:
    # Authentication must never depend on the notification channel. The
    # notification service itself is fail-safe, and this wrapper keeps the
    # security boundary explicit if that implementation changes later.
    ip = get_client_ip()
    try:
        notify(
            current_app,
            event=event,
            category=category,
            severity=severity,
            subject=subject,
            body=body,
            dedup_key=_auth_dedup(dedup_kind, ip),
            threshold=threshold,
            metadata={"path": request.path},
        )
    except Exception:
        current_app.logger.exception("authentication notification failed safely")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            source_ip = get_client_ip()
            audit_log("auth.access_denied", "access denied to dashboard", category="auth", outcome="denied",
                      ip=source_ip, path=request.path)
            _notify_auth(
                event="dashboard_access_denied",
                category="auth_failed",
                severity="security",
                subject="[MIFP][SECURITY] Repeated unauthenticated dashboard access",
                body=(
                    "MIFP recorded repeated access attempts to an authenticated dashboard route.\n\n"
                    f"Source IP: {source_ip}\n"
                    f"Route: {request.path}\n\n"
                    "No authenticated session was present. Review Dashboard → Security and Logs if this activity is unexpected.\n"
                ),
                dedup_kind="dashboard_access",
            )
            if wants_json_response():
                return jsonify({"error": "login_required"}), 401
            return redirect(url_for("auth.login", next=request.path))
        login_at = float(session.get("admin_login_at", 0) or 0)
        max_age = int(current_app.config.get("ADMIN_SESSION_HOURS", 8)) * 3600
        if login_at and time.time() - login_at > max_age:
            audit_log("auth.session_expired", "session expired", category="auth", outcome="failure",
                      username=session.get("admin_username", "-"), ip=get_client_ip())
            session.clear()
            flash("Session expired. Please log in again.", "warning")
            if wants_json_response():
                return jsonify({"error": "session_expired"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    wrapped._login_required = True
    return wrapped



def _check_rate_limit() -> bool:
    """Bounded IP rate limit that does not create anonymous sessions.

    Shared across gunicorn workers via the SQLite-backed limiter.
    """
    if current_app.config.get("TESTING"):
        return True
    return ip_rate_allowed(
        "login",
        get_client_ip(),
        limit=int(current_app.config.get("LOGIN_IP_MAX_ATTEMPTS", 10)),
        window_seconds=float(current_app.config.get("LOGIN_LOCKOUT_SECONDS", 60)),
    )


def _account_rate_key(username: str) -> str:
    """Opaque, stable limiter key for a submitted login name.

    Hashed so the shared rate-limit store never holds an account name. The key
    is derived from whatever was submitted, so it is created for non-existent
    names too and cannot be used to probe which accounts exist.
    """
    normalized = str(username or "").strip().casefold().encode("utf-8", "replace")
    return hashlib.sha256(normalized).hexdigest()[:32]


def _account_failure_allowed(username: str) -> bool:
    """Record a failed attempt for this login name and report whether it is
    still inside the per-account bound.

    This is only consulted *after* the password check has already failed, so a
    correct password always authenticates and the bound can never lock the real
    administrator out — it only throttles guessing.
    """
    if current_app.config.get("TESTING"):
        return True
    limit = int(current_app.config.get("LOGIN_ACCOUNT_MAX_ATTEMPTS", 30))
    window = float(current_app.config.get("LOGIN_ACCOUNT_LOCKOUT_SECONDS", 900))
    if limit <= 0 or window <= 0:
        return True
    return ip_rate_allowed(
        "login_account", _account_rate_key(username), limit=limit, window_seconds=window
    )


@bp.get("/login")
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("dashboard.index"))
    return render_template("auth/login.html")


@bp.post("/login")
def login_post():
    maintenance_login = request.args.get("source") == "maintenance"
    failure_url = (
        url_for("maintenance.page")
        if maintenance_login
        else url_for("auth.login", next=request.args.get("next", ""))
    )
    if not _check_rate_limit():
        source_ip = get_client_ip()
        security_event(
            "auth.login_rate_limited",
            "login rate limit exceeded",
            severity="warning",
            username=request.form.get("login_username", "").strip(),
            ip=source_ip,
        )
        _notify_auth(
            event="login_rate_limited",
            category="auth_failed",
            severity="security",
            subject="[MIFP][SECURITY] Login rate limit activated",
            body=(
                "MIFP blocked additional administrator login attempts from a source that exceeded the configured IP rate limit.\n\n"
                f"Source IP: {source_ip}\n"
                f"Lockout window: {int(current_app.config.get('LOGIN_LOCKOUT_SECONDS', 60))} seconds\n\n"
                "No password, submitted credential or session token is included in this message.\n"
            ),
            dedup_kind="login_rate_limit",
            threshold=1,
        )
        flash(f"Too many attempts. Please try again in {int(current_app.config.get('LOGIN_LOCKOUT_SECONDS', 60))} seconds.", "error")
        return redirect(failure_url)

    username = request.form.get("login_username", "").strip()
    password = request.form.get("login_password", "")
    expected_user = current_app.config.get("ADMIN_USERNAME") or "admin"
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")

    # Verify the password before comparing the username so the response time
    # cannot reveal whether the submitted login name exists: the expensive hash
    # check must run for every attempt, not only for a correct username.
    password_ok = admin_password_matches(password, expected_hash)
    if hmac.compare_digest(str(username), str(expected_user)) and password_ok:
        session.clear()
        import secrets
        session["admin_logged_in"] = True
        session["admin_username"] = username
        session["admin_login_at"] = time.time()
        session["_csrf_token"] = secrets.token_urlsafe(32)
        source_ip = get_client_ip()
        audit_log("auth.login_success", "admin login", category="auth", outcome="success", username=username, ip=source_ip)
        _notify_auth(
            event="login_success",
            category="auth_success",
            severity="security",
            subject="[MIFP][SECURITY] Administrator login",
            body=(
                "An administrator successfully signed in to the MIFP dashboard.\n\n"
                f"Account: {username}\n"
                f"Source IP: {source_ip}\n\n"
                "If this login was not expected, rotate the administrator password and inspect the security log immediately.\n"
            ),
            dedup_kind="login_success",
        )
        flash("Login successful.", "success")
        next_url = request.args.get("next") or ""
        if not is_safe_relative_url(next_url):
            next_url = url_for("dashboard.index")
        return redirect(next_url)

    # Always log failed attempts and use generic message (don't reveal if user exists).
    source_ip = get_client_ip()
    security_event("auth.login_failed", "failed admin login", username=username, ip=source_ip)
    _notify_auth(
        event="login_failed",
        category="auth_failed",
        severity="security",
        subject="[MIFP][SECURITY] Repeated failed administrator login",
        body=(
            "MIFP recorded failed administrator login attempts.\n\n"
            f"Source IP: {source_ip}\n\n"
            "Submitted usernames, passwords and session values are intentionally omitted. The alert is aggregated to avoid email flooding.\n"
        ),
        dedup_kind="login_failed",
    )
    if not _account_failure_allowed(username):
        # Same wording as the IP limiter and the same password-first ordering,
        # so this reveals nothing about the account and cannot lock out the
        # real administrator (a correct password is accepted above).
        security_event(
            "auth.account_rate_limited",
            "login account rate limit exceeded",
            severity="warning",
            username=username,
            ip=source_ip,
        )
        _notify_auth(
            event="account_rate_limited",
            category="auth_failed",
            severity="security",
            subject="[MIFP][SECURITY] Account login throttle activated",
            body=(
                "MIFP activated the per-account guessing throttle after repeated failed authentication attempts.\n\n"
                f"Source IP: {source_ip}\n"
                f"Throttle window: {int(current_app.config.get('LOGIN_ACCOUNT_LOCKOUT_SECONDS', 900))} seconds\n\n"
                "The submitted account name is intentionally omitted from email.\n"
            ),
            dedup_kind="account_rate_limit",
            threshold=1,
        )
        flash(
            f"Too many attempts. Please try again in "
            f"{int(current_app.config.get('LOGIN_ACCOUNT_LOCKOUT_SECONDS', 900))} seconds.",
            "error",
        )
        return redirect(failure_url)
    flash("Invalid credentials.", "error")
    return redirect(failure_url)


@bp.post("/logout")
def logout():
    username = session.get("admin_username")
    audit_log("auth.logout", "admin logout", category="auth", outcome="success", username=username, ip=get_client_ip())

    # Keep logout truly session-empty. Using flash() after session.clear() would
    # create a fresh signed session cookie just to carry the confirmation text.
    # The login page receives a non-sensitive query flag instead.
    session.clear()
    response = redirect(url_for("auth.login", logged_out="1"))
    response.headers["Cache-Control"] = "no-store, max-age=0"
    current_app.logger.info("admin logout completed username=%s", username or "unknown")
    return response

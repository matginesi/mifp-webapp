from __future__ import annotations

import time
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ..utils.logger import audit_log, security_event
from ..utils.security import get_client_ip, ip_rate_allowed

bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            audit_log("auth.access_denied", "access denied to dashboard", category="auth", outcome="denied",
                      ip=get_client_ip(), path=request.path)
            if _wants_json_response():
                return jsonify({"error": "login_required"}), 401
            return redirect(url_for("auth.login", next=request.path))
        login_at = float(session.get("admin_login_at", 0) or 0)
        max_age = int(current_app.config.get("ADMIN_SESSION_HOURS", 8)) * 3600
        if login_at and time.time() - login_at > max_age:
            audit_log("auth.session_expired", "session expired", category="auth", outcome="failure",
                      username=session.get("admin_username", "-"), ip=get_client_ip())
            session.clear()
            flash("Session expired. Please log in again.", "warning")
            if _wants_json_response():
                return jsonify({"error": "session_expired"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    wrapped._login_required = True
    return wrapped


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
        security_event("auth.login_rate_limited", "login rate limit exceeded", severity="warning", username=request.form.get("username", "").strip(), ip=get_client_ip())
        flash("Too many attempts. Please try again in 60 seconds.", "error")
        return redirect(failure_url)

    username = request.form.get("login_username", "").strip()
    password = request.form.get("login_password", "")
    expected_user = current_app.config.get("ADMIN_USERNAME") or "admin"
    expected_hash = current_app.config.get("ADMIN_PASSWORD_HASH", "")

    if username == expected_user and expected_hash and check_password_hash(expected_hash, password):
        session.clear()
        import secrets
        session["admin_logged_in"] = True
        session["admin_username"] = username
        session["admin_login_at"] = time.time()
        session["_csrf_token"] = secrets.token_urlsafe(32)
        audit_log("auth.login_success", "admin login", category="auth", outcome="success", username=username, ip=get_client_ip())
        flash("Login successful.", "success")
        next_url = request.args.get("next") or ""
        # Prevent open redirect: only allow same-origin relative paths. A
        # leading backslash must be rejected too — browsers normalize "\" to
        # "/", turning "/\evil.example.com" into a protocol-relative redirect.
        parsed_next = urlsplit(next_url)
        if (
            not next_url.startswith("/")
            or parsed_next.scheme != ""
            or parsed_next.netloc != ""
            or "\\" in next_url
        ):
            next_url = url_for("dashboard.index")
        return redirect(next_url)

    # Always log failed attempts and use generic message (don't reveal if user exists)
    security_event("auth.login_failed", "failed admin login", username=username, ip=get_client_ip())
    flash("Invalid credentials.", "error")
    return redirect(failure_url)


@bp.post("/logout")
def logout():
    username = session.get("admin_username")
    audit_log("auth.logout", "admin logout", category="auth", outcome="success", username=username, ip=get_client_ip())
    session.clear()
    flash("You have been logged out.", "info")
    response = redirect(url_for("auth.login"))
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

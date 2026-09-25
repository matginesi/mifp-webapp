from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _settings_db(path: Path, values: dict[str, str] | None = None) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        for key, value in (values or {}).items():
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))
        conn.commit()


def test_notification_html_uses_dashboard_palette_and_escapes_untrusted_text() -> None:
    from mifp_app.services.notifications import render_notification_html

    rendered = render_notification_html(
        subject="[MIFP] <unsafe>",
        body="Problem <script>alert(1)</script>\n\nReview Dashboard -> Notifications.",
        severity="security",
        event="login_failed",
    )

    assert "#181b20" in rendered
    assert "#a72b31" in rendered
    assert "#eef1f4" in rendered
    assert "SECURITY" in rendered
    assert "&lt;unsafe&gt;" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_mailer_builds_plain_text_and_html_alternatives(monkeypatch) -> None:
    from mifp_app.services import mailer

    sent = []

    class FakeSSL:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def login(self, *_args):
            return None

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSSL)
    app = SimpleNamespace(config={
        "MAIL_PROVIDER": "smtp",
        "MAIL_FROM": "alerts@mifp.eu",
        "MAIL_FROM_NAME": "MIFP Alerts",
        "SMTP_HOST": "smtp.example.net",
        "SMTP_PORT": 465,
        "SMTP_SECURITY": "tls",
        "SMTP_USERNAME": "alerts@mifp.eu",
        "SMTP_PASSWORD": "not-a-real-secret",
    })

    assert mailer.send_mail(
        app,
        to="operator@example.net",
        subject="MIFP test",
        body="Plain fallback",
        html_body="<html><body><b>Formatted</b></body></html>",
    ) is True

    assert len(sent) == 1
    message = sent[0]
    assert message["Auto-Submitted"] == "auto-generated"
    assert message.is_multipart()
    parts = list(message.iter_parts())
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert "Plain fallback" in parts[0].get_content()
    assert "Formatted" in parts[1].get_content()


def test_notification_without_credentials_is_fail_safe(tmp_path: Path) -> None:
    from mifp_app.services.notifications import notify

    db = tmp_path / "settings.db"
    _settings_db(db)
    app = SimpleNamespace(config={
        "DATABASE_PATH": db,
        "RUNTIME_CONFIG_DIR": tmp_path / "config",
        "MAIL_PROVIDER": "disabled",
        "MAIL_TO": "operator@example.net",
        "MAIL_FROM": "alerts@mifp.eu",
    })

    result = notify(
        app,
        event="test_disabled",
        category="error",
        severity="error",
        subject="Should not send",
        body="No SMTP credentials exist in this test.",
    )

    assert result.sent is False
    assert result.status == "skipped"
    assert result.reason == "transport_unavailable"
    assert not (tmp_path / "config" / "notification_state.json").exists()


def test_repeated_notification_is_thresholded_and_cooled_down(tmp_path: Path, monkeypatch) -> None:
    from mifp_app.services import notifications

    db = tmp_path / "settings.db"
    _settings_db(db, {
        "notification_error_threshold": "3",
        "notification_window_minutes": "5",
        "notification_cooldown_minutes": "30",
    })
    app = SimpleNamespace(config={
        "DATABASE_PATH": db,
        "RUNTIME_CONFIG_DIR": tmp_path / "config",
        "MAIL_PROVIDER": "console",
        "MAIL_TO": "operator@example.net",
        "MAIL_FROM": "alerts@mifp.eu",
        "MAIL_FROM_NAME": "MIFP Alerts",
        "ENV": "test",
    })
    delivered = []

    def fake_send_mail(_app, **kwargs):
        delivered.append(kwargs)
        return True

    monkeypatch.setattr(notifications, "send_mail", fake_send_mail)

    kwargs = dict(
        event="application_error",
        category="error",
        severity="error",
        subject="Repeated error",
        body="A bounded error occurred.",
        dedup_key="error:test",
    )
    first = notifications.notify(app, now=100.0, **kwargs)
    second = notifications.notify(app, now=120.0, **kwargs)
    third = notifications.notify(app, now=140.0, **kwargs)
    fourth = notifications.notify(app, now=160.0, **kwargs)

    assert (first.reason, second.reason) == ("threshold", "threshold")
    assert third.sent is True and third.count == 3
    assert fourth.reason == "cooldown"
    assert len(delivered) == 1
    assert "Aggregated occurrences: 3" in delivered[0]["body"]
    assert "text/html" not in delivered[0]["html_body"]  # raw HTML body, not MIME wrapper
    assert "MIFP notification" in delivered[0]["html_body"]


@pytest.fixture
def notification_app(tmp_path: Path):
    import os
    from werkzeug.security import generate_password_hash

    os.environ.update({
        "TESTING": "1",
        "DATABASE_PATH": str(tmp_path / "mifp.db"),
        "ASSETS_DIR": str(tmp_path / "assets"),
        "EXPORT_DIR": str(tmp_path / "exports"),
        "CONFERENCES_DIR": str(tmp_path / "conferences"),
        "RUNTIME_CONFIG_DIR": str(tmp_path / "config"),
        "LOG_DIR": str(tmp_path / "logs"),
        "SECRET_KEY": "notification-dashboard-test-key",
        "MAIL_PROVIDER": "disabled",
        "MAIL_TO": "operator@example.net",
    })
    from mifp_app import create_app
    from mifp_app.db.manage import init_database

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        RUNTIME_CONFIG_DIR=tmp_path / "config",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
        MAIL_PROVIDER="disabled",
        MAIL_TO="operator@example.net",
        MAIL_FROM="alerts@mifp.eu",
        SMTP_USERNAME="smtp-user-that-must-never-render",
        SMTP_PASSWORD="secret-that-must-never-render",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "RUNTIME_CONFIG_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    init_database(Path(app.config["DATABASE_PATH"]))
    return app


def _logged_in_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_login_at"] = time.time()
    return client


def test_notification_dashboard_is_authenticated_and_never_renders_secret(notification_app) -> None:
    anonymous = notification_app.test_client().get("/dashboard/notifications")
    assert anonymous.status_code == 302
    assert "/login" in anonymous.location

    response = _logged_in_client(notification_app).get("/dashboard/notifications")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Notifications" in body
    assert "SMTP status" in body
    assert "Failed access attempts" in body
    assert "Successful administrator logins" in body
    assert "secret-that-must-never-render" not in body
    assert "smtp-user-that-must-never-render" not in body


def test_notification_settings_can_be_saved_without_smtp_credentials(notification_app) -> None:
    client = _logged_in_client(notification_app)
    response = client.post("/dashboard/notifications", data={
        "notification_enabled": "1",
        "notification_errors_enabled": "1",
        "notification_problems_enabled": "1",
        "notification_down_enabled": "1",
        "notification_recovery_enabled": "1",
        "notification_join_enabled": "1",
        "notification_auth_failed_enabled": "1",
        "notification_auth_success_enabled": "1",
        "notification_error_threshold": "4",
        "notification_problem_threshold": "3",
        "notification_down_threshold": "3",
        "notification_auth_failed_threshold": "2",
        "notification_window_minutes": "5",
        "notification_cooldown_minutes": "45",
    }, follow_redirects=False)
    assert response.status_code == 302

    with sqlite3.connect(notification_app.config["DATABASE_PATH"]) as conn:
        stored = dict(conn.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'notification_%'"
        ).fetchall())
    assert stored["notification_error_threshold"] == "4"
    assert stored["notification_auth_failed_threshold"] == "2"
    assert stored["notification_auth_success_enabled"] == "1"
    assert "SMTP_PASSWORD" not in stored


def test_authentication_events_feed_notification_service_without_changing_login_flow(notification_app, monkeypatch) -> None:
    from mifp_app.routes import auth

    events = []

    def fake_notify(_app, **kwargs):
        events.append(kwargs)
        return SimpleNamespace(sent=False, reason="transport_unavailable")

    monkeypatch.setattr(auth, "notify", fake_notify)
    client = notification_app.test_client()

    failed = client.post("/login", data={"login_username": "admin", "login_password": "wrong"})
    assert failed.status_code == 302
    assert events[-1]["event"] == "login_failed"
    assert events[-1]["category"] == "auth_failed"
    assert "wrong" not in events[-1]["body"]

    succeeded = client.post("/login", data={"login_username": "admin", "login_password": "secret123"})
    assert succeeded.status_code == 302
    assert events[-1]["event"] == "login_success"
    assert events[-1]["category"] == "auth_success"
    with client.session_transaction() as session:
        assert session["admin_logged_in"] is True


def test_host_notification_monitor_is_independent_and_packaged() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "deploy" / "mifp-alert-check.sh").read_text(encoding="utf-8")
    refresh = (root / "deploy" / "refresh-host-tools.sh").read_text(encoding="utf-8")
    bootstrap = (root / "deploy" / "bootstrap-vps.sh").read_text(encoding="utf-8")
    service = (root / "deploy" / "mifp-alert-check.service").read_text(encoding="utf-8")

    assert "http://127.0.0.1:8000/health" in script
    assert "https://$domain/health" in script
    assert "http://127.0.0.1:8000/ready" in script
    assert "mifp-backup.service" in script
    assert "msg.add_alternative(html" in script
    assert "mifp-alert-check.sh" in refresh
    assert "mifp-alert-check.timer" in refresh
    assert "mifp-alert-check.service" in bootstrap
    assert "ExecStart=/opt/mifp/mifp-alert-check.sh" in service

"""Shared isolated app fixture and live Notifications compatibility checks."""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def poll_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mifp.db"))
    monkeypatch.setenv("ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("CONFERENCES_DIR", str(tmp_path / "conferences"))
    monkeypatch.setenv("RUNTIME_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SECRET_KEY", "poll-test-secret-key-that-is-long-enough")
    from mifp_app import create_app
    from mifp_app.db.manage import init_database

    init_database(tmp_path / "mifp.db")
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
        MAIL_PROVIDER="console",
        MAIL_TO="operator@example.org",
        MAIL_FROM="alerts@mifp.eu",
        ENV="test",
    )
    for key in (
        "ASSETS_DIR",
        "EXPORT_DIR",
        "CONFERENCES_DIR",
        "RUNTIME_CONFIG_DIR",
        "LOG_DIR",
    ):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    return app


def logged_in_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["admin_login_at"] = time.time()
    return client


def test_ephemeral_email_endpoint_personalizes_safely_without_history(
    poll_app, monkeypatch
) -> None:
    from mifp_app.routes import dashboard_notifications

    delivered = []
    monkeypatch.setattr(
        dashboard_notifications,
        "send_mail",
        lambda _app, **kwargs: delivered.append(kwargs) or True,
    )
    client = logged_in_client(poll_app)
    response = client.post(
        "/dashboard/notifications/send-one",
        json={
            "password": "secret123",
            "email": "recipient@example.org",
            "first_name": '<img src="https://tracker.invalid/x">',
            "last_name": "=Formula",
            "subject": "Hello {{first_name}}",
            "mail_title": "MIFP update",
            "message": "Hello {{first_name}},\n\nUpdate for {{last_name}}.",
            "message_html": "<p>Hello {{first_name}}</p><p>{{last_name}}</p>",
        },
    )

    assert response.status_code == 200
    assert len(delivered) == 1
    assert '<img src="https://tracker.invalid/x">' not in delivered[0]["html_body"]
    assert "&lt;img" in delivered[0]["html_body"]
    assert delivered[0]["to"] == "recipient@example.org"
    assert delivered[0]["privacy_safe_log"] is True
    assert not (
        Path(poll_app.config["RUNTIME_CONFIG_DIR"]) / "manual_email_history.json"
    ).exists()
    log_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in Path(poll_app.config["LOG_DIR"]).glob("*")
        if path.is_file()
    )
    assert "recipient@example.org" not in log_text
    assert "tracker.invalid" not in log_text


def test_client_recipient_parser_never_uses_web_storage_or_upload_field(poll_app) -> None:
    root = Path(__file__).resolve().parents[2]
    parser = (
        root / "MIFPAPP/CORE/mifp_app/static/js/dashboard/recipient-files.js"
    ).read_text(encoding="utf-8")
    email_batch = (
        root / "MIFPAPP/CORE/mifp_app/static/js/dashboard/email-batch.js"
    ).read_text(encoding="utf-8")
    template = logged_in_client(poll_app).get("/dashboard/notifications/batch").get_data(as_text=True)
    poll_template = (
        root / "MIFPAPP/CORE/mifp_app/templates/dashboard/polls_simple.html"
    ).read_text(encoding="utf-8")

    assert "localStorage" not in parser + email_batch
    assert "sessionStorage" not in parser + email_batch
    assert "indexedDB" not in (parser + email_batch).lower()
    assert "data-email-recipient-file" in template
    assert "Batch email" in template
    direct = logged_in_client(poll_app).get("/dashboard/notifications").get_data(as_text=True)
    assert "data-email-recipient-file" not in direct
    assert "data-recipient-template-download" in template
    assert "data-recipient-template-download" not in poll_template
    assert "downloadTemplate" in parser
    assert "email,first_name,last_name" in parser
    recipient_input = re.search(
        r"<input[^>]+data-email-recipient-file[^>]*>", template
    ).group(0)
    assert "name=" not in recipient_input
    assert "Promise.all" not in email_batch


def archived_poll(runtime):
    """Synthetic immutable archive: no retired creation or invitation engine."""
    import json
    poll = {'id':'f'*32,'title':'Archived survey','description':'Preserved records','status':'closed',
            'response_mode':'identified','deadline':'2025-01-01','retention_until':'2025-02-01',
            'updated_at':'2024-12-01T00:00:00+00:00','allow_changes':True,
            'questions':[{'id':'1'*16,'type':'yes_no','question':'Attend?','required':True,'yes_label':'Yes','no_label':'No'}]}
    directory = runtime/'polls'/poll['id']
    directory.mkdir(parents=True)
    (directory/'poll.json').write_text(json.dumps(poll))
    (directory/'invitations.json').write_text(json.dumps({'invitations':[{'invitation_id':'a'*32,
          'first_name':'Synthetic','last_name':'Archived','display_name':'Synthetic Archived','email':'old@example.org','status':'sent'}]}))
    rows = [{'response_id':str(i)*32,'invitation_id':'a'*32,'revision':i,
             'submitted_at':f'2024-12-0{i}T12:00:00+00:00','answers':{'1'*16:'yes' if i==1 else 'no'}} for i in (1,2)]
    (directory/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return poll

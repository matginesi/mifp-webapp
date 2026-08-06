from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

import pytest


def test_setup_logging_does_not_duplicate_handlers():
    from mifp_app.utils.logger import setup_logging, shutdown_logging

    log_dir = Path(tempfile.mkdtemp())
    setup_logging(log_dir, "DEBUG")
    root = logging.getLogger()
    handler_count = len(root.handlers)
    setup_logging(log_dir, "DEBUG")
    assert len(root.handlers) == handler_count
    shutdown_logging()


def test_audit_log_does_not_include_sensitive_keys(tmp_path):
    from mifp_app.utils.logger import audit_log, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    audit_log("test.event", "test", password="secret123", token="abc", safe_field="hello")
    shutdown_logging()
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists()
    content = audit_file.read_text(encoding="utf-8")
    assert "secret123" not in content
    assert "abc" not in content
    assert "hello" in content


def test_error_events_go_to_error_log(tmp_path):
    from mifp_app.utils.logger import setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG")
    logging.getLogger("mifp.test").error("this is an error")
    shutdown_logging()
    error_file = tmp_path / "errors.log"
    assert error_file.exists()
    content = error_file.read_text(encoding="utf-8")
    assert "this is an error" in content


def test_stdout_output_does_not_write_runtime_log_files(tmp_path):
    from mifp_app.utils.logger import get_logger, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", output="stdout")
    get_logger("test").info("stdout-only mode")
    shutdown_logging()

    assert not (tmp_path / "mifp_app.log").exists()
    assert not (tmp_path / "errors.log").exists()


def test_console_formatter_has_no_ansi_when_colors_are_disabled():
    from mifp_app.utils.logger import ConsoleFormatter

    record = logging.LogRecord("mifp.web", logging.WARNING, __file__, 1, "plain warning", (), None)
    rendered = ConsoleFormatter(colors=False).format(record)

    assert "\x1b[" not in rendered
    assert "WARNING" in rendered
    assert "plain warning" in rendered


def test_json_logs_include_event_type(tmp_path):
    from mifp_app.utils.logger import audit_log, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    audit_log("auth.login_success", "test login", category="auth", outcome="success")
    shutdown_logging()
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists()
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        data = json.loads(line)
        assert data.get("event") == "auth.login_success"
        break


@pytest.fixture
def app(tmp_path):
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from werkzeug.security import generate_password_hash
    from mifp_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_logs_export_txt_returns_plain_text(client):
    client.post("/login", data={"login_username": "admin", "login_password": "secret123"})

    resp = client.get("/dashboard/logs/export/txt")

    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    assert "filename=mifp_logs.txt" in resp.headers["Content-Disposition"]


def test_logs_page_does_not_claim_file_logging_is_disabled(client):
    client.post("/login", data={"login_username": "admin", "login_password": "secret123"})

    resp = client.get("/dashboard/logs")

    assert resp.status_code == 200
    assert b"File logging is disabled" not in resp.data
    assert b"Conference ops" not in resp.data


def test_paginated_log_counts_cover_the_filtered_scope(tmp_path):
    from mifp_app.services.dashboard_repository import search_logs_paginated

    log_file = tmp_path / "mifp_app.log"
    log_file.write_text(
        "\n".join(
            [
                "2026-01-01T00:00:00 | ERROR | application | test.logger | event=test rid=a | first",
                "2026-01-01T00:01:00 | WARNING | application | test.logger | event=test rid=b | second",
                "2026-01-01T00:02:00 | INFO | application | test.logger | event=test rid=c | third",
            ]
        ),
        encoding="utf-8",
    )

    result = search_logs_paginated(tmp_path, level="ERROR", page=1, per_page=1)

    assert result["total"] == 1
    assert result["scoped_total"] == 3
    assert result["level_counts"] == {"ERROR": 1, "WARNING": 1, "INFO": 1}


def test_recursive_redaction_handles_nested_values_and_email():
    from mifp_app.utils.logger import redact

    value = {
        "profile": {
            "admin_password_hash": "hash-value",
            "items": [{"access_token": "token-value"}, "person@example.org"],
        },
        "safe": ("visible",),
    }
    result = redact(value)
    rendered = json.dumps(result)
    assert "hash-value" not in rendered
    assert "token-value" not in rendered
    assert "person@example.org" not in rendered
    assert "visible" in rendered


def test_logging_flows_are_separated(tmp_path):
    from mifp_app.utils.logger import (
        audit_event,
        get_logger,
        log_event,
        security_event,
        setup_logging,
        shutdown_logging,
    )

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    log_event(get_logger("test"), "app.test", "application-only")
    audit_event("audit.test", "audit-only")
    security_event("security.test", "security-only")
    shutdown_logging()

    app_log = (tmp_path / "mifp_app.jsonl").read_text(encoding="utf-8")
    audit_log_content = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    security_log_content = (tmp_path / "security.jsonl").read_text(encoding="utf-8")
    assert "application-only" in app_log
    assert "audit-only" not in app_log and "security-only" not in app_log
    assert "audit-only" in audit_log_content and "application-only" not in audit_log_content
    assert "security-only" in security_log_content and "application-only" not in security_log_content


def test_exception_json_contains_trace_without_sensitive_email(tmp_path):
    from mifp_app.utils.logger import get_logger, log_exception, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    try:
        raise ValueError("failure for private@example.org")
    except ValueError:
        log_exception(get_logger("test"), "test.failed", "operation failed")
    shutdown_logging()

    record = json.loads((tmp_path / "errors.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["exception_type"] == "ValueError"
    assert "Traceback" in record["stack_trace"]
    assert "private@example.org" not in json.dumps(record)


def test_request_id_validation_and_slow_request_logging(tmp_path):
    from flask import Flask
    from mifp_app.utils.logger import init_request_logging, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    mini = Flask(__name__)
    mini.secret_key = "test-secret"
    mini.config.update(LOG_ACCESS_ENABLED=True, LOG_SLOW_REQUEST_MS=0.1, PRIVACY_SAFE_METRICS_ENABLED=False)
    init_request_logging(mini)

    @mini.get("/slow")
    def slow():
        time.sleep(0.002)
        return "ok"

    response = mini.test_client().get("/slow", headers={"X-Request-ID": "bad value with spaces"})
    shutdown_logging()
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    assert request_id.isalnum()
    records = [json.loads(line) for line in (tmp_path / "access.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {record["event"] for record in records} == {"request.completed", "request.slow"}
    assert all(record["request_id"] == request_id for record in records)


def test_expected_maintenance_response_is_logged_as_info(tmp_path):
    from flask import Flask, g
    from mifp_app.utils.logger import init_request_logging, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    mini = Flask(__name__)
    mini.secret_key = "test-secret"
    mini.config.update(
        LOG_ACCESS_ENABLED=True,
        LOG_SLOW_REQUEST_MS=0,
        PRIVACY_SAFE_METRICS_ENABLED=False,
    )
    init_request_logging(mini)

    @mini.get("/")
    def maintenance():
        g.maintenance_active = True
        return "maintenance", 503

    response = mini.test_client().get("/")
    shutdown_logging()

    assert response.status_code == 503
    record = json.loads(
        (tmp_path / "access.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert record["level"] == "INFO"
    assert record["event"] == "request.maintenance"
    assert record["status"] == 503
    assert record["expected_response"] is True


def test_cleanup_helpers_remove_only_expired_rows(tmp_path):
    import sqlite3
    from mifp_app.utils.logger import cleanup_metrics_daily, cleanup_page_views

    db_path = tmp_path / "metrics.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE page_views(created_at TEXT)")
        conn.execute("CREATE TABLE metrics_daily(date TEXT)")
        conn.executemany("INSERT INTO page_views VALUES (?)", [("2000-01-01",), ("2999-01-01",)])
        conn.executemany("INSERT INTO metrics_daily VALUES (?)", [("2000-01-01",), ("2999-01-01",)])
    assert cleanup_page_views(str(db_path), 30) == 1
    assert cleanup_metrics_daily(str(db_path), 30) == 1

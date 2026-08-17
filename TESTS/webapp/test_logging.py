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


def test_console_formatter_includes_structured_fields_and_redacts_secrets():
    from mifp_app.utils.logger import ConsoleFormatter

    record = logging.LogRecord("mifp.web", logging.WARNING, __file__, 1, "operation rejected", (), None)
    record.extra_fields = {
        "operation": "import",
        "password": "must-not-leak",
    }
    rendered = ConsoleFormatter(colors=False).format(record)

    assert '"operation": "import"' in rendered
    assert "must-not-leak" not in rendered
    assert "[REDACTED]" in rendered


def test_plain_logger_errors_receive_searchable_default_event(tmp_path):
    from mifp_app.utils.logger import setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    logging.getLogger("mifp_app.routes.example").error("plain route failure")
    shutdown_logging()

    record = json.loads((tmp_path / "errors.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "application.error"
    assert record["message"] == "plain route failure"


def test_repeated_degraded_fallback_is_throttled(tmp_path):
    from mifp_app.utils.logger import (
        get_logger,
        log_event_throttled,
        setup_logging,
        shutdown_logging,
    )

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    first = log_event_throttled(
        get_logger("runtime"), "runtime.degraded", "degraded fallback", throttle_key="test"
    )
    second = log_event_throttled(
        get_logger("runtime"), "runtime.degraded", "degraded fallback", throttle_key="test"
    )
    shutdown_logging()

    assert first is True
    assert second is False
    records = (tmp_path / "mifp_app.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1


def test_failed_audit_outcome_defaults_to_warning(tmp_path):
    from mifp_app.utils.logger import audit_log, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    audit_log("data.failed", "data operation failed", outcome="failure")
    shutdown_logging()

    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["level"] == "WARNING"
    assert record["outcome"] == "failure"


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


def test_login_success_and_failure_are_logged_without_password(app, client):
    from mifp_app.utils.logger import shutdown_logging

    client.post(
        "/login",
        data={"login_username": "admin", "login_password": "never-log-this-password"},
    )
    client.post(
        "/login",
        data={"login_username": "admin", "login_password": "secret123"},
    )
    shutdown_logging()

    log_dir = Path(app.config["LOG_DIR"])
    security = next(log_dir.glob("security.*")).read_text(encoding="utf-8")
    audit = next(log_dir.glob("audit.*")).read_text(encoding="utf-8")
    combined = security + audit
    assert "auth.login_failed" in security
    assert "auth.login_success" in audit
    assert "never-log-this-password" not in combined
    assert "secret123" not in combined


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


def test_text_logs_split_message_from_structured_context(tmp_path):
    from mifp_app.services.dashboard_repository import search_logs

    (tmp_path / "mifp_app.log").write_text(
        '2026-08-17T01:57:49.218+00:00 | WARNING | application | mifp.dashboard | '
        'event=dashboard.request_failed rid=request-1 | Administrative request failed | '
        '{"method":"GET","path":"/dashboard/assets/missing.png","status":404,"duration_ms":5.35}',
        encoding="utf-8",
    )

    row = search_logs(tmp_path, level="WARNING")[0]

    assert row["message"] == "Administrative request failed"
    assert row["event"] == "dashboard.request_failed"
    assert row["stream"] == "application"
    assert row["status"] == 404
    assert row["details"]["path"] == "/dashboard/assets/missing.png"
    assert {item["label"] for item in row["detail_items"]} >= {"Method", "Path", "Status"}
    assert '"status":404' in row["raw"]


def test_logs_page_presents_context_and_keeps_raw_record_collapsed(app, client):
    log_dir = Path(app.config["LOG_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "mifp_app.log").write_text(
        '2026-08-17T01:57:49.218+00:00 | ERROR | application | mifp.jobs | '
        'event=jobs.failed rid=job-request | Background job failed | '
        '{"job_name":"data-import:all","error_type":"ValueError"}',
        encoding="utf-8",
    )
    client.post("/login", data={"login_username": "admin", "login_password": "secret123"})

    response = client.get("/dashboard/logs?level=ERROR")

    assert response.status_code == 200
    assert b"Structured context" in response.data
    assert b"Background job failed" in response.data
    assert b"Raw record" in response.data
    assert b"log-context-grid" in response.data


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


def test_dashboard_mutation_fallback_logs_flash_error_without_form_values(tmp_path):
    from flask import Flask, flash, redirect
    from mifp_app.utils.logger import init_request_logging, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    mini = Flask(__name__)
    mini.secret_key = "test-secret"
    mini.config.update(
        LOG_ACCESS_ENABLED=False,
        LOG_SLOW_REQUEST_MS=0,
        PRIVACY_SAFE_METRICS_ENABLED=False,
    )
    init_request_logging(mini)

    @mini.post("/dashboard/save")
    def save():
        flash("The submitted record could not be saved.", "error")
        return redirect("/dashboard/save")

    response = mini.test_client().post(
        "/dashboard/save",
        data={"password": "top-secret-value", "title": "private form value"},
        headers={"X-Request-ID": "mutation-test"},
    )
    shutdown_logging()

    assert response.status_code == 302
    audit_records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    app_records = [
        json.loads(line)
        for line in (tmp_path / "mifp_app.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    mutation = next(record for record in audit_records if record["event"] == "dashboard.mutation")
    failure = next(record for record in app_records if record["event"] == "dashboard.request_failed")
    assert mutation["outcome"] == "failure"
    assert mutation["error_feedback"] is True
    assert mutation["request_id"] == "mutation-test"
    assert failure["level"] == "WARNING"
    rendered = json.dumps([mutation, failure])
    assert "top-secret-value" not in rendered
    assert "private form value" not in rendered


def test_successful_dashboard_mutation_has_generic_audit_fallback(tmp_path):
    from flask import Flask
    from mifp_app.utils.logger import init_request_logging, setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    mini = Flask(__name__)
    mini.secret_key = "test-secret"
    mini.config.update(
        LOG_ACCESS_ENABLED=False,
        LOG_SLOW_REQUEST_MS=0,
        PRIVACY_SAFE_METRICS_ENABLED=False,
    )
    init_request_logging(mini)

    @mini.post("/dashboard/save")
    def save():
        return {"ok": True}

    response = mini.test_client().post("/dashboard/save", data={"value": "never logged"})
    shutdown_logging()

    assert response.status_code == 200
    records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    mutation = next(record for record in records if record["event"] == "dashboard.mutation")
    assert mutation["outcome"] == "success"
    assert mutation["method"] == "POST"
    assert mutation["path"] == "/dashboard/save"
    assert "never logged" not in json.dumps(mutation)


def test_background_job_failure_is_written_to_error_stream(tmp_path):
    from mifp_app.services.job_manager import JobManager
    from mifp_app.utils.logger import setup_logging, shutdown_logging

    setup_logging(tmp_path, "DEBUG", json_logs=True)
    manager = JobManager(max_workers=1, max_pending=1, db_path=str(tmp_path / "content.db"))

    def fail():
        raise RuntimeError("simulated background failure")

    _job_id, future = manager.submit("test-background-job", fail)
    with pytest.raises(RuntimeError, match="simulated background failure"):
        future.result(timeout=5)
    manager.shutdown()
    shutdown_logging()

    records = [
        json.loads(line)
        for line in (tmp_path / "errors.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failure = next(record for record in records if record["event"] == "job.failed")
    assert failure["job_name"] == "test-background-job"
    assert failure["exception_type"] == "RuntimeError"


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

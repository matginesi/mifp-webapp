from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def app(tmp_path):
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["ASSETS_DIR"] = str(tmp_path / "assets")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    os.environ["PRIVACY_SAFE_METRICS_ENABLED"] = "1"
    from werkzeug.security import generate_password_hash

    from mifp_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    Path(app.config["ASSETS_DIR"]).mkdir(parents=True, exist_ok=True)
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _rows(app, table: str):
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def test_public_get_increments_aggregate_metrics_without_page_views_or_identifiers(app, client):
    legacy_before = len(_rows(app, "page_views"))
    resp = client.get(
        "/events?email=test@example.com",
        headers={
            "User-Agent": "PrivacyTestBrowser/1.0",
            "Referer": "https://example.test/private?token=abc",
            "X-Forwarded-For": "203.0.113.44",
        },
    )

    assert resp.status_code == 200
    assert len(_rows(app, "page_views")) == legacy_before
    metrics = _rows(app, "metrics_daily")
    matching = [
        row for row in metrics
        if row["scope"] == "public_site" and row["metric_name"] == "page_view" and row["metric_key"] == "/events"
    ]
    assert matching
    serialized = repr(matching)
    assert "203.0.113.44" not in serialized
    assert "PrivacyTestBrowser" not in serialized
    assert "example.test" not in serialized
    assert "test@example.com" not in serialized
    assert "?email=" not in serialized


def test_dashboard_is_not_counted_as_public_page_view(app, client):
    before = [
        row for row in _rows(app, "metrics_daily")
        if row["scope"] == "public_site" and row["metric_name"] == "page_view"
    ]
    client.get("/dashboard")

    after = [
        row for row in _rows(app, "metrics_daily")
        if row["scope"] == "public_site" and row["metric_name"] == "page_view"
    ]
    assert after == before


def test_dashboard_stats_does_not_show_unique_ips(app, client):
    client.post("/login", data={"login_username": "admin", "login_password": "secret123"})

    html = client.get("/dashboard/stats").get_data(as_text=True)

    assert "unique IPs" not in html
    assert "unique visitors" not in html.lower()
    assert "Privacy-safe statistics" in html


def test_data_portability_counts_exclude_legacy_page_views(app):
    from mifp_app.services.data_portability import table_counts

    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO page_views(path, method, client_ip, user_agent_hash, status, duration_ms) VALUES('/legacy','GET','hash','ua',200,1)"
        )
        conn.commit()
        counts = table_counts(conn)

    assert "page_views" not in counts


def test_download_metrics_are_aggregated_by_asset_type(app, client):
    assets_dir = Path(app.config["ASSETS_DIR"])
    (assets_dir / "report-with-email-test@example.com.pdf").write_bytes(b"%PDF-1.4\n")

    resp = client.get("/media/report-with-email-test@example.com.pdf")

    assert resp.status_code == 200
    metrics = _rows(app, "metrics_daily")
    assert any(row["scope"] == "public_download" and row["metric_name"] == "download" and row["metric_key"] == "pdf" for row in metrics)
    assert "test@example.com" not in repr(metrics)


def test_public_home_does_not_set_anonymous_session_cookie(app, client):
    resp = client.get("/")

    cookie = resp.headers.get("Set-Cookie", "")
    assert "session=" not in cookie

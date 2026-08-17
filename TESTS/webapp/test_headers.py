from __future__ import annotations

import json
import pytest


@pytest.fixture
def app(tmp_path):
    import os
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_x_content_type_options_header(client):
    resp = client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_x_frame_options_header(client):
    resp = client.get("/health")
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_referrer_policy_header(client):
    resp = client.get("/health")
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_csp_header_present(client):
    resp = client.get("/health")
    csp = resp.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "'self'" in csp
    assert "script-src" in csp
    # no CDN or external domains allowed
    assert "cdn.jsdelivr.net" not in csp
    assert "cdnjs.cloudflare.com" not in csp
    assert "fonts.googleapis.com" not in csp
    assert "fonts.gstatic.com" not in csp
    assert "cdn.tailwindcss.com" not in csp
    assert "frame-ancestors 'none'" in csp


def test_permissions_policy_header(client):
    resp = client.get("/health")
    pp = resp.headers.get("Permissions-Policy")
    assert pp is not None
    assert "interest-cohort=()" in pp


def test_cross_origin_opener_policy_header(client):
    resp = client.get("/health")
    coop = resp.headers.get("Cross-Origin-Opener-Policy")
    assert coop == "same-origin"


def test_cross_origin_resource_policy_header(client):
    resp = client.get("/health")
    assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-origin"


def test_dashboard_responses_are_never_cached(client):
    resp = client.get("/dashboard/")
    assert resp.headers["Cache-Control"] == "no-store, max-age=0"
    assert resp.headers["Pragma"] == "no-cache"


def test_hsts_not_sent_over_http(client):
    resp = client.get("/health")
    assert "Strict-Transport-Security" not in resp.headers


def test_x_request_id_header(client):
    resp = client.get("/health", headers={"X-Request-ID": "my-test-id"})
    assert resp.headers.get("X-Request-ID") == "my-test-id"


def test_public_cookie_banner_reads_configured_runtime_file(app, client, tmp_path):
    banner_path = tmp_path / "banner.json"
    banner_path.write_text(
        json.dumps({
            "cookie_banner_enabled": "1",
            "cookie_banner_text": "Runtime cookie notice",
        }),
        encoding="utf-8",
    )
    app.config["BANNER_SETTINGS_PATH"] = banner_path

    response = client.get("/")

    assert response.status_code == 200
    assert b'id="cookie-banner"' in response.data
    assert b"Runtime cookie notice" in response.data
    assert b"cookie-banner-icon" in response.data
    assert b"cookie-banner-actions" in response.data
    assert b'<span class="visually-hidden">Dismiss</span>' in response.data

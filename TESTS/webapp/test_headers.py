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


def test_public_cookie_notice_banner_renders_the_configured_text(app, client, tmp_path):
    """The notice is informational and its wording is operator-editable."""
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


def test_public_cookie_notice_banner_is_informational_only(app, client):
    """It must not become a fake consent gate: no accept/reject, no cookie set."""
    response = client.get("/")
    body = response.data.lower()

    assert b'id="cookie-banner"' in body
    for fake in (b"accept all", b"reject all", b"manage cookies", b"i consent"):
        assert fake not in body, fake
    # Rendering the notice must not bind an anonymous CSRF client.
    assert not any(
        header.startswith("mifp_csrf=") for header in response.headers.getlist("Set-Cookie")
    )


def test_shipped_banner_default_text_matches_the_real_cookie_model():
    """The default wording must not claim admin-session-only cookies."""
    from mifp_app.config import Config

    text = Config.DEFAULT_BANNER_SETTINGS["cookie_banner_text"].lower()
    assert "strictly necessary" in text
    assert "no analytics" in text
    # The stale claim: only an admin session cookie exists.
    assert "session cookie for admin authentication" not in text


def test_shipped_banner_defaults_are_complete():
    """A clean checkout must work without committing mutable runtime settings."""
    from mifp_app.config import Config

    assert set(Config.DEFAULT_BANNER_SETTINGS) == {
        "cookie_banner_enabled",
        "cookie_banner_text",
        "banner_force_show",
        "cookie_banner_link_enabled",
        "cookie_banner_dismiss_label",
        "cookie_banner_theme",
    }


def test_empty_banner_text_falls_back_to_the_shipped_wording(app, client, tmp_path):
    """Clearing the textarea must not produce an empty notice."""
    from mifp_app.config import Config

    banner_path = tmp_path / "banner.json"
    banner_path.write_text(
        json.dumps({"cookie_banner_enabled": "1", "cookie_banner_text": ""}),
        encoding="utf-8",
    )
    app.config["BANNER_SETTINGS_PATH"] = banner_path

    body = client.get("/").data.decode("utf-8")
    assert 'id="cookie-banner-message">' in body
    assert Config.DEFAULT_BANNER_SETTINGS["cookie_banner_text"] in body


def test_banner_settings_path_defaults_inside_the_runtime_config_dir():
    from pathlib import Path

    from mifp_app.config import Config

    assert Config.BANNER_SETTINGS_PATH == Path(Config.RUNTIME_CONFIG_DIR) / "banner_settings.json"

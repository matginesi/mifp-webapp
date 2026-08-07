from __future__ import annotations

import os
import re

import pytest


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
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _anonymous_csrf(client) -> str:
    response = client.get("/login")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode("utf-8")


def test_all_dashboard_routes_are_login_decorated(app):
    unprotected = []
    for rule in app.url_map.iter_rules():
        if rule.rule == "/dashboard" or rule.rule.startswith("/dashboard/"):
            view = app.view_functions[rule.endpoint]
            if not getattr(view, "_login_required", False):
                unprotected.append(f"{','.join(sorted(rule.methods or []))} {rule.rule} -> {rule.endpoint}")
    assert unprotected == []


def test_dashboard_json_endpoint_requires_login(client):
    response = client.get("/dashboard/assets/search.json", headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert response.is_json
    assert response.get_json()["error"] == "login_required"


def test_dashboard_ajax_post_requires_login_even_with_anonymous_csrf(client):
    csrf = _anonymous_csrf(client)
    response = client.post(
        "/dashboard/data-quality/analyze",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 401
    assert response.is_json
    assert response.get_json()["error"] == "login_required"


def test_cross_origin_dashboard_write_is_rejected(app, client):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = "admin"
        sess["_csrf_token"] = "valid-test-token"

    response = client.post(
        "/dashboard/settings",
        data={"_csrf_token": "valid-test-token"},
        headers={
            "Origin": "https://attacker.example",
            "X-Requested-With": "XMLHttpRequest",
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "origin_rejected"


def test_same_origin_dashboard_write_passes_origin_check(app, client):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = "admin"
        sess["_csrf_token"] = "valid-test-token"

    response = client.post(
        "/dashboard/settings",
        data={"_csrf_token": "valid-test-token"},
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code != 403


def test_logout_invalidates_session_without_browser_wide_clear(client):
    with client.session_transaction() as sess:
        sess["admin_logged_in"] = True
        sess["admin_username"] = "admin"
        sess["_csrf_token"] = "logout-token"

    response = client.post("/logout", data={"_csrf_token": "logout-token"}, follow_redirects=False)

    assert response.status_code == 302
    assert response.location.endswith("/login?logged_out=1")
    # Clear-Site-Data previously caused disruptive browser-wide clearing/freeze.
    assert "Clear-Site-Data" not in response.headers
    cache_control = response.headers.get("Cache-Control", "")
    assert "no-store" in cache_control
    assert "max-age=0" in cache_control

    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")
        assert not sess.get("admin_username")
        assert "_csrf_token" not in sess

    protected = client.get("/dashboard/", follow_redirects=False)
    assert protected.status_code == 302
    assert "/login" in protected.location

from __future__ import annotations

import pytest


@pytest.fixture
def app(tmp_path):
    import os
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


def test_login_page_returns_200(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="login_username"' in html
    assert 'autocomplete="username"' in html
    assert 'required' in html
    assert 'readonly' not in html
    assert 'value=""' in html
    assert 'placeholder="Username"' not in html
    assert 'class="login-submit auth-action"' in html


def test_login_with_valid_credentials(client):
    resp = client.post("/login", data={"login_username": "admin", "login_password": "secret123"},
                       follow_redirects=False)
    assert resp.status_code == 302


def test_login_with_invalid_credentials(client):
    resp = client.post("/login", data={"login_username": "admin", "login_password": "wrong"},
                       follow_redirects=False)
    assert resp.status_code == 302


def test_logout_clears_session(client):
    client.post("/login", data={"login_username": "admin", "login_password": "secret123"})
    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert not sess.get("admin_logged_in")


def test_dashboard_redirects_to_login(client):
    resp = client.get("/dashboard/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_dashboard_accessible_when_logged_in(client):
    client.post("/login", data={"login_username": "admin", "login_password": "secret123"})
    resp = client.get("/dashboard/")
    assert resp.status_code == 200


def test_login_rejects_protocol_relative_redirect(client):
    resp = client.post("/login?next=//evil.example.com",
                       data={"login_username": "admin", "login_password": "secret123"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "evil.example.com" not in resp.location
    assert resp.location.startswith("/")


def test_login_rejects_backslash_redirect(client):
    resp = client.post("/login?next=/\\evil.example.com",
                       data={"login_username": "admin", "login_password": "secret123"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "evil.example.com" not in resp.location


def test_login_allows_relative_next(client):
    resp = client.post("/login?next=/dashboard/control/site",
                       data={"login_username": "admin", "login_password": "secret123"},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location == "/dashboard/control/site"

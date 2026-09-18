from __future__ import annotations

import re

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
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("secret123")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_post_without_csrf_token_fails(client):
    """POST without _csrf_token should return 400."""
    client.get("/login")
    resp = client.post("/dashboard/settings", data={})
    assert resp.status_code == 400


def test_post_with_valid_csrf_token_passes(client):
    """POST with valid _csrf_token should pass."""
    login_page = client.get("/login")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', login_page.data)
    assert match is not None
    login_csrf = match.group(1).decode("utf-8")
    client.post(
        "/login",
        data={"login_username": "admin", "login_password": "secret123", "_csrf_token": login_csrf},
    )
    with client.session_transaction() as sess:
        csrf2 = sess.get("_csrf_token", "")
    resp = client.post("/dashboard/settings", data={"_csrf_token": csrf2})
    assert resp.status_code != 400


def _login_token(client) -> str:
    page = client.get("/login")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
    assert match is not None
    return match.group(1).decode("utf-8")


def test_anonymous_form_issues_client_binding_cookie(client):
    """Rendering a form must also set the double-submit binding cookie."""
    _login_token(client)
    cookie = client.get_cookie("mifp_csrf")
    assert cookie is not None
    assert cookie.http_only is True
    assert cookie.same_site  # SameSite is explicitly set


def test_scraped_token_cannot_be_replayed_by_another_client(app):
    """A token lifted from a public page must not validate without the cookie
    that was issued to the browser it was rendered for (CSRF replay)."""
    victim = app.test_client()
    stolen = _login_token(victim)
    assert victim.get_cookie("mifp_csrf") is not None

    attacker = app.test_client()
    assert attacker.get_cookie("mifp_csrf") is None
    attacker.post(
        "/login",
        data={
            "login_username": "admin",
            "login_password": "secret123",
            "_csrf_token": stolen,
        },
    )
    with attacker.session_transaction() as sess:
        assert not sess.get("admin_logged_in"), "replayed token must not authenticate"


def test_stateless_token_validation_requires_the_cookie(app):
    """Directly: a freshly minted anonymous token is only valid when the
    matching client cookie is present on the request."""
    from mifp_app import _stateless_csrf_token, _validate_stateless_csrf

    with app.test_request_context("/"):
        token = _stateless_csrf_token()
        assert _validate_stateless_csrf(token) is False


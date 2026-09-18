"""Anonymous CSRF cookies are minted only where a form actually needs one.

The anonymous double-submit/HMAC design stays exactly as it was: the signed token
is bound to a random value carried in the ``mifp_csrf`` cookie, so a token fetched
from a public form cannot be replayed cross-site. What this file pins is *when*
that binding is created. Ordinary read-only browsing must not receive the cookie,
while ``/join`` and ``/login`` must keep a fully working token + binding.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

CSRF_COOKIE = "mifp_csrf"

READ_ONLY_PUBLIC_PAGES = ("/", "/events", "/news", "/members", "/privacy", "/cookie-policy")


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "CONFERENCES_DIR": str(tmp_path / "conferences"),
            "LOG_DIR": str(tmp_path / "logs"),
            "RUNTIME_CONFIG_DIR": str(tmp_path / "config"),
            "SECRET_KEY": "csrf-lazy-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        SESSION_COOKIE_SECURE=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
        RUNTIME_CONFIG_DIR=tmp_path / "config",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
        MAIL_PROVIDER="disabled",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR", "RUNTIME_CONFIG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.manage import init_database

    init_database(Path(app.config["DATABASE_PATH"]))
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def _token_from(html: bytes, name: str = "_csrf_token") -> str:
    match = re.search(rb'name="' + name.encode() + rb'" value="([^"]+)"', html)
    assert match is not None, f"{name} not found in {html[:400]!r}"
    return match.group(1).decode("utf-8")


# ---------------------------------------------------------------------------
# Ordinary anonymous browsing receives no CSRF cookie
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", READ_ONLY_PUBLIC_PAGES)
def test_read_only_public_page_sets_no_csrf_cookie(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert CSRF_COOKIE not in response.headers.getlist("Set-Cookie"), path


def test_public_pages_do_not_embed_a_global_csrf_token(client):
    """No page-wide token: only pages with a real form render one."""
    for path in READ_ONLY_PUBLIC_PAGES:
        body = client.get(path).get_data(as_text=True)
        assert 'name="csrf-token"' not in body, path


def test_public_pages_do_not_carry_a_stale_csrf_cookie(client):
    """A cookie from an earlier visit is not refreshed by a read-only page."""
    with client.session_transaction() as session:
        session["_csrf_token"] = "session-only"
    client.set_cookie(CSRF_COOKIE, "a" * 32)
    response = client.get("/")

    assert response.status_code == 200
    assert CSRF_COOKIE not in response.headers.getlist("Set-Cookie")


# ---------------------------------------------------------------------------
# Pages with a form keep a working token + binding
# ---------------------------------------------------------------------------

def test_join_page_mints_a_bound_token(client):
    response = client.get("/join")

    assert response.status_code == 200
    cookies = response.headers.getlist("Set-Cookie")
    assert any(header.startswith(f"{CSRF_COOKIE}=") for header in cookies), cookies

    token = _token_from(response.data)
    assert re.fullmatch(r"\d+:[0-9a-f]{16}:[0-9a-f]{64}", token), token


def test_join_post_still_works_end_to_end(client):
    page = client.get("/join")
    token = _token_from(page.data)

    response = client.post(
        "/join",
        data={
            "_csrf_token": token,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.org",
            "motivation": "I would like to join.",
            "website": "",
        },
    )

    assert response.status_code in {200, 302}
    assert response.status_code != 400, "CSRF validation must accept the minted token"


def test_join_post_without_the_binding_is_rejected(client):
    """The double-submit binding is still enforced: no cookie, no valid token."""
    page = client.get("/join")
    token = _token_from(page.data)

    client.delete_cookie(CSRF_COOKIE)
    response = client.post(
        "/join",
        data={
            "_csrf_token": token,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.org",
        },
    )

    assert response.status_code == 400


def test_login_page_mints_a_bound_token_and_login_works(client):
    page = client.get("/login")
    assert any(
        header.startswith(f"{CSRF_COOKIE}=") for header in page.headers.getlist("Set-Cookie")
    )
    token = _token_from(page.data)

    response = client.post(
        "/login",
        data={"login_username": "admin", "login_password": "secret123", "_csrf_token": token},
    )

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session.get("admin_logged_in") is True
        assert session.get("_csrf_token")


def test_admin_csrf_uses_the_session_token_not_the_anonymous_binding(client):
    page = client.get("/login")
    client.post(
        "/login",
        data={
            "login_username": "admin",
            "login_password": "secret123",
            "_csrf_token": _token_from(page.data),
        },
    )
    with client.session_transaction() as session:
        session_token = session["_csrf_token"]

    dashboard = client.get("/dashboard/")
    assert dashboard.status_code == 200
    assert session_token.encode() in dashboard.data

    # The token must also survive the inline dict literal that feeds the
    # data-portability page (rendered through the lazy object's __repr__).
    portability = client.get("/dashboard/data-portability")
    assert portability.status_code == 200
    block = re.search(
        r'id="dataPortabilityConfig"[^>]*>(.*?)</script>',
        portability.get_data(as_text=True),
        re.S,
    )
    assert block is not None
    assert session_token in block.group(1)

    # An anonymous token must not be accepted for an authenticated write.
    response = client.post("/dashboard/settings", data={"_csrf_token": "1:" + "a" * 16 + ":" + "b" * 64})
    assert response.status_code == 400


def test_cross_origin_write_is_still_rejected(client):
    page = client.get("/join")
    token = _token_from(page.data)
    response = client.post(
        "/join",
        data={"_csrf_token": token, "first_name": "A", "last_name": "B", "email": "a@example.org"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403

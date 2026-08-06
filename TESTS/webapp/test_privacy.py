from __future__ import annotations

from pathlib import Path

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


def _set_cookie_policy_page(app, content):
    """Set the cookie policy page body in DB."""
    import sqlite3
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    from mifp_app.db.migrations import migrate_content_schema
    migrate_content_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO pages(slug, title, body, review_status, section) VALUES(?,?,?,?,?)",
        ("cookie-policy", "Cookie Policy", content, "published", "institutional"),
    )
    conn.commit()
    conn.close()


def test_no_set_cookie_on_public_health(app):
    resp = app.test_client().get("/health")
    assert "Set-Cookie" not in resp.headers or not resp.headers.get("Set-Cookie")


def test_no_set_cookie_on_public_home(app):
    resp = app.test_client().get("/")
    if "Set-Cookie" in resp.headers:
        cookie = resp.headers.get("Set-Cookie", "")
        assert "session=" not in cookie


def test_no_set_cookie_on_cookie_policy_page(app):
    resp = app.test_client().get("/cookie-policy")
    if "Set-Cookie" in resp.headers:
        cookie = resp.headers.get("Set-Cookie", "")
        assert "session=" not in cookie


def test_privacy_page_accessible_no_cookie(app):
    resp = app.test_client().get("/privacy")
    assert resp.status_code == 200
    if "Set-Cookie" in resp.headers:
        cookie = resp.headers.get("Set-Cookie", "")
        assert "session=" not in cookie


def test_sponsor_how_to_page_accessible(app):
    resp = app.test_client().get("/sponsors/how-to-become-a-sponsor")
    assert resp.status_code == 200
    assert b"How to become a sponsor" in resp.data


def test_research_pdf_is_available_even_without_research_rows(app, monkeypatch):
    import sys
    import types

    class FakeHTML:
        def __init__(self, *, string, base_url):
            assert "Research Areas" in string
            assert base_url.startswith("http://")

        def write_pdf(self):
            return b"%PDF-1.4\nempty-research\n"

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=FakeHTML))
    resp = app.test_client().get("/pdf/research")

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data.startswith(b"%PDF")


def test_join_form_uses_compact_fields_and_keeps_honeypot(app):
    html = app.test_client().get("/join").get_data(as_text=True)

    assert 'name="orcid"' not in html
    assert 'name="website_url"' not in html
    assert 'name="website"' in html
    assert 'name="field"' in html
    assert 'class="join-submit auth-action"' in html
    assert 'M19 8v6' in html
    assert 'M22 11h-6' in html


def test_public_site_has_no_cookie_notice_or_browser_storage(app):
    html = app.test_client().get("/").get_data(as_text=True)

    assert "cookie-notice" not in html
    assert "cookieDismiss" not in html
    homepage_js = (Path(app.static_folder) / "js" / "homepage.js").read_text(encoding="utf-8")
    assert "localStorage" not in homepage_js

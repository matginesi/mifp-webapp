from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pytest


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
            "SECRET_KEY": "seo-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app
    from mifp_app.db.manage import init_database

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
        TRUSTED_HOSTS=["localhost", "mifp.eu", "www.mifp.eu"],
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    init_database(Path(app.config["DATABASE_PATH"]))
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('seo_canonical_origin','https://mifp.eu')"
        )
        conn.commit()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "seo-csrf"
    return client


def _db(app):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _head_fragment(body: str) -> str:
    return body.split("</head>", 1)[0]


def _json_ld_fragment(body: str) -> str:
    return "\n".join(
        re.findall(
            r'<script\s+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )


def test_public_head_uses_stable_canonical_and_noindex_for_query_variants(client):
    response = client.get("/news?q=physics", base_url="https://www.mifp.eu")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<link rel="canonical" href="https://mifp.eu/news">' in body
    assert '<meta property="og:url" content="https://mifp.eu/news">' in body
    assert '<meta name="robots" content="noindex,follow">' in body
    assert "Matteo Ginesi" not in _head_fragment(body)


def test_public_home_has_organization_publisher_not_personal_creator(client):
    body = client.get("/", base_url="https://www.mifp.eu").get_data(as_text=True)
    structured_data = _json_ld_fragment(body)
    assert '"@type": "WebSite"' in structured_data
    assert '"publisher"' in structured_data
    assert '"@type": "Organization"' in structured_data
    assert '"creator"' not in structured_data
    assert "Matteo Ginesi" not in structured_data


def test_sitemap_uses_real_lastmod_and_omits_ignored_hints(app, client):
    with _db(app) as conn:
        conn.execute(
            """
            INSERT INTO news(slug,title,summary,body,date,review_status,updated_at)
            VALUES('seo-news','SEO News','A useful summary long enough for a search snippet.','Body','2020-01-01','published','2024-02-03 12:00:00')
            """
        )
        conn.commit()
    response = client.get("/sitemap.xml", base_url="https://www.mifp.eu")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "https://mifp.eu/news/seo-news" in body
    assert "<lastmod>2024-02-03</lastmod>" in body
    assert "<changefreq>" not in body
    assert "<priority>" not in body
    assert response.headers.get("ETag")


def test_robots_uses_canonical_sitemap_and_keeps_crawling_available(client):
    body = client.get("/robots.txt", base_url="https://www.mifp.eu").get_data(as_text=True)
    assert "Allow: /" in body
    assert "Disallow:" not in body
    assert "Sitemap: https://mifp.eu/sitemap.xml" in body


def test_indexing_kill_switch_updates_robots_and_public_meta(app, client):
    with _db(app) as conn:
        conn.execute(
            "UPDATE settings SET value='0',updated_at=CURRENT_TIMESTAMP WHERE key='seo_indexing_enabled'"
        )
        if conn.total_changes == 0:
            conn.execute("INSERT INTO settings(key,value) VALUES('seo_indexing_enabled','0')")
        conn.commit()
    robots = client.get("/robots.txt", base_url="https://mifp.eu").get_data(as_text=True)
    home = client.get("/", base_url="https://mifp.eu").get_data(as_text=True)
    assert "Allow: /" in robots
    assert "Disallow:" not in robots
    assert "noindex,nofollow" in robots
    assert '<meta name="robots" content="noindex,nofollow">' in home


def test_dashboard_seo_page_and_save(app, admin_client):
    response = admin_client.get("/dashboard/seo")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "SEO &amp; indexing" in body or "SEO &amp; Indexing" in body
    assert "sitemap.xml" in body

    saved = admin_client.post(
        "/dashboard/seo",
        data={
            "seo_indexing_enabled": "1",
            "seo_canonical_origin": "https://mifp.eu",
            "seo_google_site_verification": "verify_token-123",
            "seo_default_description": "MIFP default search description for automated SEO metadata.",
        },
    )
    assert saved.status_code == 302
    with _db(app) as conn:
        values = dict(conn.execute("SELECT key,value FROM settings WHERE key LIKE 'seo_%'").fetchall())
    assert values["seo_google_site_verification"] == "verify_token-123"
    assert values["seo_indexing_enabled"] == "1"


def test_asset_metadata_change_touches_linked_public_record(app, admin_client):
    with _db(app) as conn:
        news_id = conn.execute(
            """
            INSERT INTO news(slug,title,summary,review_status,updated_at)
            VALUES('asset-touch','Asset Touch','Summary for asset touch test.','published','2000-01-01 00:00:00')
            """
        ).lastrowid
        asset_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,storage_status) VALUES('x.jpg','x.jpg','image','local')"
        ).lastrowid
        conn.execute(
            "INSERT INTO asset_links(asset_id,entity_type,entity_id,role) VALUES(?,'news',?,'cover')",
            (asset_id, news_id),
        )
        conn.commit()

    response = admin_client.post(
        "/dashboard/assets",
        data={"action": "update", "id": str(asset_id), "kind": "image", "alt_text": "Updated alt text"},
    )
    assert response.status_code == 302
    with _db(app) as conn:
        updated_at = conn.execute("SELECT updated_at FROM news WHERE id=?", (news_id,)).fetchone()[0]
    assert updated_at > "2000-01-01 00:00:00"


def test_private_generated_pdf_and_error_responses_emit_x_robots_tag(client):
    assert client.get("/health", base_url="https://mifp.eu").headers["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get("/pdf/about", base_url="https://mifp.eu").headers["X-Robots-Tag"] == "noindex, nofollow"
    assert client.get("/missing-page", base_url="https://mifp.eu").headers["X-Robots-Tag"] == "noindex, nofollow"

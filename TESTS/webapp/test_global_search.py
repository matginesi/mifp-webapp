from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "MIFPAPP"
    / "CORE"
    / "mifp_app"
    / "db"
    / "schema.sql"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def _seed_events(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO events(id, slug, title, start_date, location, description, "
        "review_status, speakers_json, chairs_json, committee_json) "
        "VALUES (1, 'quantum-optics-2024', 'Quantum Optics 2024', '2024-06-01', "
        "'Rome', 'Advances in photonics', 'published', "
        "'[{\"name\":\"Maria Rossi\"}]', '[{\"name\":\"Giulia Verdi\"}]', "
        "'[{\"name\":\"Anna Neri\"}]')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, location, review_status) "
        "VALUES (2, 'hidden-draft', 'Hidden Draft Event', 'Nowhere', 'draft')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, start_date, location, description, review_status) "
        "VALUES (3, 'old-photonics-1999', 'Old Photonics Meeting', '1999-09-01', "
        "'Catania', 'A historical photonics meeting', 'published')"
    )
    conn.execute(
        "INSERT INTO event_archive_entries(id, event_id, source_schema, public_path, "
        "category, archive_year, acronym, summary, topics_json, people_json) "
        "VALUES (1, 3, 'v3', '/archive/meetings/1999/old-photonics-1999/', "
        "'meetings', 1999, 'OPM', 'Historical photonics summary', "
        "'[\"photonics\"]', '{\"speakers\":[\"Luca Bianchi\"]}')"
    )
    conn.commit()


@pytest.fixture
def app(tmp_path):
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "CONFERENCES_DIR": str(tmp_path / "conferences"),
            "LOG_DIR": str(tmp_path / "logs"),
            "SECRET_KEY": "search-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.manage import init_database

    init_database(Path(app.config["DATABASE_PATH"]))
    yield app


@pytest.fixture
def request_ctx(app):
    with app.test_request_context("/"):
        yield


def _search(conn, query, scope="public", **kwargs):
    from mifp_app.services.search import run_search

    return run_search(conn, query, scope=scope, **kwargs)


def test_normalize_query_trims_and_collapses():
    from mifp_app.services.search import normalize_query

    assert normalize_query("  Quantum   Optics  ") == "Quantum Optics"
    assert normalize_query("a") is None
    assert normalize_query("") is None
    assert normalize_query(None) is None


def test_event_by_title_public(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Quantum Optics")
    titles = [r["title"] for r in page["results"]]
    assert "Quantum Optics 2024" in titles
    assert all(r["type"] == "event" for r in page["results"] if r["title"].startswith("Quantum"))


def test_archive_event_by_title_is_not_duplicated(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Old Photonics")
    matches = [r for r in page["results"] if r["title"] == "Old Photonics Meeting"]
    assert len(matches) == 1
    assert matches[0]["type"] == "archive_event"
    assert "/archive/meetings/1999/old-photonics-1999/" in matches[0]["url"]


def test_search_by_location(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Catania")
    assert [r["title"] for r in page["results"]] == ["Old Photonics Meeting"]


def test_search_by_topic(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "photonics")
    assert "Old Photonics Meeting" in [r["title"] for r in page["results"]]


def test_search_by_speaker(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Maria Rossi")
    assert "Quantum Optics 2024" in [r["title"] for r in page["results"]]


def test_search_by_chair(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Giulia Verdi")
    assert "Quantum Optics 2024" in [r["title"] for r in page["results"]]


def test_search_by_committee(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Anna Neri")
    assert "Quantum Optics 2024" in [r["title"] for r in page["results"]]


def test_case_insensitive(request_ctx):
    conn = _conn()
    _seed_events(conn)
    assert _search(conn, "QUANTUM")["results"]
    assert _search(conn, "quantum")["results"]


def test_accent_insensitive_both_directions(request_ctx):
    conn = _conn()
    conn.execute(
        "INSERT INTO events(id, slug, title, location, review_status) "
        "VALUES (9, 'citta', 'Città della Scienza', 'Perù', 'published')"
    )
    conn.commit()
    assert "Città della Scienza" in [r["title"] for r in _search(conn, "citta")["results"]]
    assert "Città della Scienza" in [r["title"] for r in _search(conn, "città")["results"]]
    assert "Città della Scienza" in [r["title"] for r in _search(conn, "peru")["results"]]


def test_leading_trailing_repeated_whitespace(request_ctx):
    conn = _conn()
    _seed_events(conn)
    assert _search(conn, "   Quantum    Optics   ")["results"]


def test_empty_and_short_query_return_nothing(request_ctx):
    conn = _conn()
    _seed_events(conn)
    for query in ("", " ", "a", None):
        page = _search(conn, query)
        assert page["results"] == []
        assert page["total"] == 0
        assert page["failed"] is False


def test_wildcard_characters_are_literal(request_ctx):
    conn = _conn()
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (20, 'pct', '100% Physics', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (21, 'x', '100X Physics', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (22, 'underscore', 'Under_score Meeting', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (23, 'space', 'Under score Meeting', 'published')"
    )
    conn.commit()
    percent_titles = [r["title"] for r in _search(conn, "100%")["results"]]
    assert "100% Physics" in percent_titles
    assert "100X Physics" not in percent_titles
    underscore_titles = [r["title"] for r in _search(conn, "Under_score")["results"]]
    assert "Under_score Meeting" in underscore_titles
    assert "Under score Meeting" not in underscore_titles


def test_public_visibility_excludes_drafts(request_ctx):
    conn = _conn()
    _seed_events(conn)
    assert "Hidden Draft Event" not in [r["title"] for r in _search(conn, "Hidden Draft")["results"]]
    assert "Hidden Draft Event" in [
        r["title"] for r in _search(conn, "Hidden Draft", scope="dashboard")["results"]
    ]


def test_result_limit_and_has_more(request_ctx):
    conn = _conn()
    for i in range(5):
        conn.execute(
            "INSERT INTO events(id, slug, title, review_status) VALUES (?, ?, ?, 'published')",
            (100 + i, f"series-{i}", f"Series Meeting {i}"),
        )
    conn.commit()
    page = _search(conn, "Series Meeting", limit=2)
    assert len(page["results"]) == 2
    assert page["total"] == 5
    assert page["has_more"] is True
    second = _search(conn, "Series Meeting", limit=2, offset=2)
    assert len(second["results"]) == 2
    assert {r["id"] for r in page["results"]}.isdisjoint({r["id"] for r in second["results"]})


def test_total_is_exact_and_pages_are_deterministic(request_ctx):
    conn = _conn()
    for i in range(25):
        conn.execute(
            "INSERT INTO events(id, slug, title, review_status) VALUES (?, ?, ?, 'published')",
            (200 + i, f"series-{i}", f"Series Meeting {i}"),
        )
    conn.commit()
    first = _search(conn, "Series Meeting", limit=10)
    assert first["total"] == 25
    assert first["has_more"] is True
    assert len(first["results"]) == 10
    second = _search(conn, "Series Meeting", limit=10, offset=10)
    assert len(second["results"]) == 10
    assert {r["id"] for r in first["results"]}.isdisjoint({r["id"] for r in second["results"]})


def _seed_content(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO news(id, slug, title, summary, body, review_status) "
        "VALUES (1, 'lab-news', 'New Laboratory Opened', 'A new lab', 'Details about the lab', 'published')"
    )
    conn.execute(
        "INSERT INTO news(id, slug, title, review_status) "
        "VALUES (2, 'draft-news', 'Draft News Item', 'draft')"
    )
    conn.execute(
        "INSERT INTO members(id, slug, display_name, first_name, last_name, affiliation, country, field, bio, review_status, is_active) "
        "VALUES (1, 'giulia-verdi', 'Giulia Verdi', 'Giulia', 'Verdi', 'Perugia University', 'Italy', 'photonics', 'Researcher', 'published', 1)"
    )
    conn.execute(
        "INSERT INTO members(id, slug, display_name, review_status, is_active) "
        "VALUES (2, 'draft-member', 'Draft Member', 'draft', 1)"
    )
    conn.execute(
        "INSERT INTO members(id, slug, display_name, review_status, is_active) "
        "VALUES (3, 'inactive-member', 'Inactive Member', 'published', 0)"
    )
    conn.execute(
        "INSERT INTO publications(id, slug, title, year, authors, journal, abstract, review_status) "
        "VALUES (1, 'metamaterials-review', 'Metamaterials Review', 2023, 'Rossi, Bianchi', 'Optics Today', 'A review of metamaterials', 'published')"
    )
    conn.execute(
        "INSERT INTO research_areas(id, slug, title, summary, description, review_status) "
        "VALUES (1, 'nanophotonics', 'Nanophotonics', 'Light at the nanoscale', 'Nanophotonics research', 'published')"
    )
    conn.execute(
        "INSERT INTO pages(id, slug, title, type, summary, body, review_status) "
        "VALUES (1, 'about', 'About MIFP', 'about', 'Institute profile', 'Body of the about page', 'published')"
    )
    conn.execute(
        "INSERT INTO sponsors(id, slug, name, description, is_active) "
        "VALUES (1, 'acme', 'Acme Optics', 'Sponsor of photonics', 1)"
    )
    conn.execute(
        "INSERT INTO sponsors(id, slug, name, is_active) VALUES (2, 'inactive-sponsor', 'Dormant Sponsor', 0)"
    )
    conn.commit()


def test_news_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "New Laboratory")
    assert page["results"][0]["type"] == "news"
    assert page["results"][0]["url"] == "/news/lab-news"


def test_news_with_null_slug_links_to_news_index(request_ctx):
    conn = _conn()
    conn.execute(
        "INSERT INTO news(id, slug, title, summary, review_status) "
        "VALUES (30, NULL, 'Slugless Bulletin', 'unique-null-slug-marker', 'published')"
    )
    conn.commit()
    matches = [r for r in _search(conn, "unique-null-slug-marker")["results"] if r["title"] == "Slugless Bulletin"]
    assert len(matches) == 1
    assert matches[0]["url"] == "/news"


def test_member_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Giulia Verdi")
    assert page["results"][0]["type"] == "member"
    assert page["results"][0]["url"] == "/members?q=Giulia+Verdi"


def test_member_bio_is_dashboard_only(request_ctx):
    conn = _conn()
    _seed_content(conn)
    conn.execute(
        "INSERT INTO members(id, slug, display_name, first_name, last_name, bio, review_status, is_active) "
        "VALUES (10, 'astro-member', 'Astro Member', 'Astro', 'Member', "
        "'astrochemistry pioneer', 'published', 1)"
    )
    conn.commit()
    public_titles = [r["title"] for r in _search(conn, "astrochemistry")["results"]]
    assert "Astro Member" not in public_titles
    dashboard_titles = [
        r["title"] for r in _search(conn, "astrochemistry", scope="dashboard")["results"]
    ]
    assert "Astro Member" in dashboard_titles


def test_member_email_is_not_public(request_ctx):
    conn = _conn()
    _seed_content(conn)
    conn.execute(
        "INSERT INTO members(id, slug, display_name, first_name, last_name, email, review_status, is_active) "
        "VALUES (11, 'email-member', 'Email Member', 'Email', 'Member', "
        "'unique-person@example.org', 'published', 1)"
    )
    conn.commit()
    assert _search(conn, "unique-person@example.org")["results"] == []


def test_publication_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Metamaterials Review")
    assert page["results"][0]["type"] == "publication"
    assert page["results"][0]["url"].startswith("/publications")


def test_research_area_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Nanophotonics")
    assert page["results"][0]["type"] == "research_area"


def test_page_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "About MIFP")
    assert page["results"][0]["type"] == "page"
    assert page["results"][0]["url"] == "/about"


def test_sponsor_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Acme Optics")
    assert page["results"][0]["type"] == "sponsor"
    assert page["results"][0]["url"] == "/sponsors/acme"


def test_public_visibility_for_content(request_ctx):
    conn = _conn()
    _seed_content(conn)
    titles = [r["title"] for r in _search(conn, "Draft")["results"]]
    assert "Draft News Item" not in titles
    assert "Draft Member" not in titles
    assert "Dormant Sponsor" not in [r["title"] for r in _search(conn, "Dormant")["results"]]


def test_dashboard_visibility_includes_drafts(request_ctx):
    conn = _conn()
    _seed_content(conn)
    titles = [r["title"] for r in _search(conn, "Draft", scope="dashboard")["results"]]
    assert "Draft News Item" in titles
    assert "Draft Member" in titles


def _seed_dashboard(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO assets(id, filename, original_filename, path, alt_text, caption, source_url, kind) "
        "VALUES (1, 'conference-hall.jpg', 'hall.jpg', 'image/conference-hall.jpg', "
        "'Conference hall', 'Main hall', 'https://example.org/hall.jpg', 'image')"
    )
    conn.execute(
        "INSERT INTO conference_sites(id, slug, title, acronym, year, city, venue, description) "
        "VALUES (1, 'plmcn-2024', 'PLMCN 2024', 'PLMCN', 2024, 'Rome', 'Aula Magna', 'A conference on light matter')"
    )
    conn.execute(
        "INSERT INTO conference_people(id, conference_id, name, affiliation, role, contribution_title) "
        "VALUES (1, 1, 'Anna Neri', 'Sapienza', 'speaker', 'Terahertz spectroscopy')"
    )
    conn.commit()


def test_assets_only_in_dashboard(request_ctx):
    conn = _conn()
    _seed_dashboard(conn)
    assert _search(conn, "conference-hall")["results"] == []
    page = _search(conn, "conference-hall", scope="dashboard")
    assert page["results"][0]["type"] == "asset"
    assert page["results"][0]["url"].startswith("/dashboard/assets?q=")


def test_conference_site_and_person_only_in_dashboard(request_ctx):
    conn = _conn()
    _seed_dashboard(conn)
    assert _search(conn, "PLMCN")["results"] == []
    site = _search(conn, "PLMCN", scope="dashboard")
    assert site["results"][0]["type"] == "conference_site"
    person = _search(conn, "Anna Neri", scope="dashboard")
    assert person["results"][0]["type"] == "conference_person"


def test_asset_alt_text_and_caption_searchable(request_ctx):
    conn = _conn()
    _seed_dashboard(conn)
    assert _search(conn, "Main hall", scope="dashboard")["results"]
    assert _search(conn, "example.org/hall", scope="dashboard")["results"]


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_app_db(app) -> None:
    from mifp_app.db.connection import connect

    conn = connect(app.config["DATABASE_PATH"])
    _seed_events(conn)
    _seed_content(conn)
    conn.commit()
    conn.close()


def test_public_search_route_renders_results(app, client):
    _seed_app_db(app)
    response = client.get("/search?q=Quantum+Optics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Quantum Optics 2024" in body
    assert "Hidden Draft Event" not in body


def test_public_search_route_empty_query_does_not_dump(app, client):
    _seed_app_db(app)
    response = client.get("/search")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Quantum Optics 2024" not in body


def test_public_search_route_has_navbar_control(app, client):
    response = client.get("/search")
    assert response.status_code == 200
    assert 'action="/search"' in response.get_data(as_text=True)


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
    return client


def test_dashboard_search_uses_shared_global_service(app, admin_client):
    # "Quantum Optics 2024" matches only through its description ("photonics"),
    # a field the replaced title/slug/filename/source_url helper did not search.
    # ("Old Photonics Meeting" also matches, by title.)
    _seed_app_db(app)
    response = admin_client.get("/dashboard/search?q=photonics")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Quantum Optics 2024" in body
    assert "Old Photonics Meeting" in body


def test_dashboard_search_includes_drafts_and_assets(app, admin_client):
    _seed_app_db(app)
    from mifp_app.db.connection import connect

    conn = connect(app.config["DATABASE_PATH"])
    _seed_dashboard(conn)
    conn.commit()
    conn.close()
    drafts = admin_client.get("/dashboard/search?q=Hidden+Draft")
    assert "Hidden Draft Event" in drafts.get_data(as_text=True)
    assets = admin_client.get("/dashboard/search?q=conference-hall")
    assert "conference-hall.jpg" in assets.get_data(as_text=True)


def test_dashboard_pages_result_links_to_institutional(app, admin_client):
    # Anchor on the result markup, not the sidebar link or the form value.
    _seed_app_db(app)
    response = admin_client.get("/dashboard/search?q=About+MIFP")
    body = response.get_data(as_text=True)
    assert '<a href="/dashboard/institutional"><span><b>About MIFP</b>' in body

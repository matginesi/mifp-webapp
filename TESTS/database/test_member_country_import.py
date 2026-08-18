import sqlite3

from build_database_pkg.importers import add_member
from build_database_pkg.utils import infer_country


def _member_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            display_name TEXT,
            slug TEXT UNIQUE,
            dedup_key TEXT UNIQUE,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            affiliation TEXT,
            country TEXT,
            bio TEXT,
            review_status TEXT DEFAULT 'draft',
            is_active INTEGER DEFAULT 1,
            updated_at TEXT
        )
        """
    )
    return conn


def test_infer_country_ignores_institution_categories():
    assert infer_country("University of Somewhere") == ""
    assert infer_country("Institute for Quantum Research") == ""
    assert infer_country("Sapienza University of Rome") == "Italy"


def test_add_member_derives_country_from_affiliation_not_full_affiliation():
    conn = _member_conn()

    imported, updated = add_member(
        conn,
        {
            "display_name": "Mario Rossi",
            "affiliation": "Dipartimento di Fisica, Sapienza University of Rome",
        },
        0,
        0,
    )

    row = conn.execute("SELECT affiliation, country FROM members WHERE slug='mario-rossi'").fetchone()
    assert imported == 1
    assert updated == 0
    assert row["affiliation"] == "Dipartimento di Fisica, Sapienza University of Rome"
    assert row["country"] == "Italy"
    assert row["country"] != row["affiliation"]


def test_add_member_leaves_country_empty_when_affiliation_has_no_country_signal():
    conn = _member_conn()

    add_member(
        conn,
        {
            "display_name": "Jane Example",
            "affiliation": "Quantum Materials Laboratory",
        },
        0,
        0,
    )

    row = conn.execute("SELECT country FROM members WHERE slug='jane-example'").fetchone()
    assert row["country"] == ""

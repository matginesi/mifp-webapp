from __future__ import annotations

import sqlite3
from pathlib import Path

from build_database_pkg.importers import add_event


def _conn():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


def test_add_event_sets_public_homepage_fields_for_dated_events():
    conn = _conn()
    imported, updated = add_event(conn, {
        'title': 'Future Conference',
        'date': '2099-04-12',
        'end_date': '2099-04-15',
        'date_text': '12 - 15 April, 2099',
        'date_precision': 'range',
        'location': 'Rome',
        'event_type': 'conference',
        'review_status': 'published',
        'confidence': 0.9,
    }, 0, 0)

    row = conn.execute('SELECT * FROM events WHERE slug=?', ('future-conference',)).fetchone()
    assert imported == 1
    assert updated == 0
    assert row['is_featured'] == 1
    assert row['review_status'] == 'published'
    assert row['start_date'] == '2099-04-12'
    assert row['date_precision'] == 'range'


def test_add_event_does_not_treat_event_status_as_review_status():
    conn = _conn()
    add_event(conn, {
        'title': 'Past Conference',
        'date': '2024-04-12',
        'status': 'past',
        'confidence': 0.9,
    }, 0, 0)

    row = conn.execute('SELECT * FROM events WHERE slug=?', ('past-conference',)).fetchone()
    assert row['review_status'] == 'published'
    assert "status" not in row.keys()

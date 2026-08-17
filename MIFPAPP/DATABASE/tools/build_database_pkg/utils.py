#!/usr/bin/env python3
"""Utility functions for build_database."""

import unicodedata
import re
import sqlite3
from pathlib import Path

from .config import WEBAPP, DEFAULT_JSONL_DIR, COUNTRY_HINTS


def clean(t):
    if not t:
        return ''
    t = str(t).strip()
    t = re.sub(r'\s+', ' ', t)
    return unicodedata.normalize('NFKC', t)


def slugify(t):
    if not t:
        return ''
    t = t.lower().strip()
    t = re.sub(r'[^a-z0-9]+', '-', t)
    return t.strip('-')


def norm_key(t):
    k = unicodedata.normalize('NFKD', str(t or '').lower())
    k = k.encode('ascii', 'ignore').decode()
    k = re.sub(r'[^a-z0-9]', '', k)
    return k[:64]


def _norm_name_for_dedup(name):
    n = ''.join(c for c in unicodedata.normalize('NFKD', name.lower()) if not unicodedata.combining(c)).strip()
    n = re.sub(r'[^a-z0-9\u00e0-\u024f\u0400-\u04ff]', '', n)
    n = re.sub(r'\s+', ' ', n)
    return n or None


def columns(conn, table):
    return {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}


def insert_or_update(conn, table, key_col, key_val, data):
    table_cols = columns(conn, table)
    data = {k: v for k, v in data.items() if k in table_cols}
    if key_col not in table_cols:
        raise sqlite3.OperationalError(f"Missing key column {key_col} on {table}")
    existing = conn.execute(f'SELECT id FROM {table} WHERE {key_col}=?', (key_val,)).fetchone()
    if existing:
        update_cols = [k for k in data if k != key_col and k != "id"]
        if update_cols:
            sets = ', '.join(f'{k}=?' for k in update_cols)
            values = tuple(data[k] for k in update_cols)
            updated = ", updated_at=CURRENT_TIMESTAMP" if "updated_at" in table_cols else ""
            conn.execute(f'UPDATE {table} SET {sets}{updated} WHERE id=?', (*values, existing['id']))
        return int(existing['id'])
    if not data:
        raise sqlite3.OperationalError(f"No valid columns for {table}")
    keys = ', '.join(data)
    qs = ', '.join('?' for _ in data)
    cur = conn.execute(f'INSERT INTO {table}({keys}) VALUES({qs})', list(data.values()))
    return int(cur.lastrowid)


def unique_slug(conn, table, slug, id_col='id', slug_col='slug'):
    if not conn.execute(f'SELECT 1 FROM {table} WHERE {slug_col}=?', (slug,)).fetchone():
        return slug
    base = slug.rsplit('-', 1)[0]
    if not base:
        return slug
    i = 1
    while True:
        new_slug = f'{base}-{i}'
        if not conn.execute(f'SELECT 1 FROM {table} WHERE {slug_col}=?', (new_slug,)).fetchone():
            return new_slug
        i += 1


NON_COUNTRY_HINT_VALUES = {
    'Academia',
    'Academy',
    'Association',
    'Center',
    'College',
    'Company',
    'Conference',
    'Corporation',
    'European Union',
    'Foundation',
    'GmbH',
    'Group',
    'Incorporated',
    'Institute',
    'Laboratory',
    'LLC',
    'Ltd',
    'Meeting',
    'Program',
    'Project',
    'Research',
    'School',
    'Symposium',
    'Team',
    'University',
    'Workshop',
    'spa',
    'srl',
}


def _ascii_lower(text):
    return unicodedata.normalize('NFKD', str(text or '').lower()).encode('ascii', 'ignore').decode()


def infer_country(text):
    t = _ascii_lower(text)
    if not t:
        return ''
    for key, country in COUNTRY_HINTS.items():
        if country in NON_COUNTRY_HINT_VALUES:
            continue
        key_norm = _ascii_lower(key)
        if not key_norm:
            continue
        if re.search(rf'(?<![a-z0-9]){re.escape(key_norm)}(?![a-z0-9])', t):
            return country
    return ''

def db_connect(db_path):
    """Connect to SQLite database and create schema if not exists."""
    import sqlite3
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.execute('PRAGMA busy_timeout = 10000')
    conn.row_factory = sqlite3.Row
    exec_schema(conn)
    return conn

def exec_schema(conn):
    """Create the canonical webapp v2 schema."""
    schema_path = WEBAPP / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()

#!/usr/bin/env python3
"""Canonical v2 post-processing for scraper-built databases."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

from .assets import add_asset


def populate_asset_links(conn):
    """Extract explicit downloadable asset URLs into asset_links.

    Public links are not inferred from source URLs. This helper only materializes
    obvious file assets found inside content text.
    """
    patterns = [
        ("news", "news", ["title", "body", "summary"]),
        ("event", "events", ["title", "description", "location"]),
        ("page", "pages", ["title", "body", "summary"]),
        ("publication", "publications", ["title", "abstract"]),
    ]
    url_re = re.compile(r"https?://[^\s\"'<>]+\.(?:jpg|jpeg|png|gif|webp|svg|pdf|doc|docx)(?:[?#][^\s\"'<>]*)?", re.I)
    linked = 0
    for entity_type, table, text_cols in patterns:
        cols = _columns(conn, table)
        usable = [c for c in text_cols if c in cols]
        if not usable:
            continue
        for row in conn.execute(f"SELECT id, {', '.join(usable)} FROM {table}").fetchall():
            text = " ".join(str(row[c] or "") for c in usable)
            for sort_order, url in enumerate(dict.fromkeys(url_re.findall(text)), start=1):
                role = "document" if url.lower().split("?", 1)[0].endswith((".pdf", ".doc", ".docx")) else "cover"
                before = conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0]
                add_asset(conn, url, role, entity_type, row["id"], role=role, sort_order=sort_order)
                after = conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0]
                linked += max(int(after - before), 0)
    conn.commit()
    return linked


def _set_primary_assets(conn):
    """Primary asset is represented by asset_links.is_primary in v2."""
    for entity_type in ("event", "news", "member", "publication", "research_area", "page", "sponsor"):
        rows = conn.execute(
            """
            SELECT MIN(id) AS id
            FROM asset_links
            WHERE entity_type=?
            GROUP BY entity_type, entity_id, role
            """,
            (entity_type,),
        ).fetchall()
        for row in rows:
            if row["id"]:
                conn.execute("UPDATE asset_links SET is_primary=1 WHERE id=?", (row["id"],))
    conn.commit()


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

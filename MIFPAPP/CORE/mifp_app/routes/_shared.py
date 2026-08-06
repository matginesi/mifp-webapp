"""Shared dashboard constants and helpers.

These used to live in ``routes/dashboard.py`` and were imported by every
sibling route module, coupling them to an 1800-line god module. Moving them
here lets the sibling blueprints depend on a small, stable module instead.
"""

from __future__ import annotations

from flask import g, jsonify


def admin_error_payload(message: str, status: int = 500):
    """Return a stable admin error without exposing the caught exception."""
    return jsonify({
        "error": message,
        "request_id": getattr(g, "request_id", "-"),
    }), status


def admin_error_text(message: str) -> str:
    request_id = getattr(g, "request_id", "-")
    return f"{message} Reference: {request_id}."


SECTION_TABLES = {
    "members": "members",
    "news": "news",
    "events": "events",
    "publications": "publications",
    "research": "research_areas",
    "sponsors": "sponsors",
}

ENTITY_TYPES = {
    "members": "member",
    "news": "news",
    "events": "event",
    "publications": "publication",
    "research_areas": "research_area",
    "sponsors": "sponsor",
    "pages": "page",
}

PRIMARY_ASSET_FIELDS = {
    "members": {"image": "profile"},
    "events": {"image": "cover"},
    "news": {"image": "cover", "document": "document", "pdf": "document"},
    "publications": {"document": "document", "pdf": "document"},
    "research_areas": {"image": "cover"},
    "sponsors": {"image": "logo"},
    "pages": {"document": "document", "pdf": "document"},
}

NEWS_TEMPLATES = {
    "award": {
        "label": "Award News",
        "news_type": "award",
        "title": "Congratulations to ...",
        "summary": "Award/prize announcement.",
        "body": "MIFP congratulates ... for ...",
    },
    "publication": {
        "label": "Publication",
        "news_type": "publication_highlight",
        "title": "New publication by ...",
        "summary": "Publication announcement.",
        "body": "Authors, title, journal, year. Add DOI/link/document asset if available.",
    },
    "agreement": {
        "label": "Agreement",
        "news_type": "agreement",
        "title": "Agreement with ...",
        "summary": "Institutional collaboration agreement.",
        "body": "MIFP announces a collaboration agreement with ...",
    },
    "event": {
        "label": "Event Highlight",
        "news_type": "event_highlight",
        "title": "Upcoming event: ...",
        "summary": "Event highlight for homepage/news.",
        "body": "Name, location, dates, short description and registration/document links.",
    },
}

"""Stable content-domain contracts shared by runtime services.

Keep only concepts that are genuinely global here: canonical entity/table
identity, importable fields, required fields, and workflow states. UI metadata,
query ordering and route aliases belong in their respective modules.
"""
from __future__ import annotations

ENTITY_TABLES = {
    "event": "events",
    "news": "news",
    "member": "members",
    "publication": "publications",
    "research_area": "research_areas",
    "page": "pages",
    "sponsor": "sponsors",
}
TABLE_ENTITY_TYPES = {table: entity for entity, table in ENTITY_TABLES.items()}
IMPORT_TYPES = frozenset(ENTITY_TABLES)

# Must stay aligned with the CHECK constraints in db/schema.sql.
REVIEW_STATUSES = frozenset({"draft", "review", "published", "quarantined", "duplicate"})
DATE_PRECISIONS = frozenset({"day", "month", "year", "range", "unknown"})
EVENT_TYPES = frozenset({"conference", "workshop", "seminar", "meeting", "school", "project_event", "other"})
NEWS_TYPES = frozenset({
    "general", "announcement", "publication_highlight", "agreement", "award",
    "event_highlight", "institutional", "sponsor", "memorial", "science_commentary",
})
PAGE_TYPES = frozenset({
    "about", "privacy", "cookie_policy", "manifesto", "code_of_conduct",
    "documentation", "custom", "legacy_home", "contact", "error_page",
})

# Shared link/asset vocabulary used by canonical import/export and the dashboard.
LINK_ROLES = frozenset({
    "primary", "website", "source", "doi", "publisher", "registration",
    "program", "document", "social", "other",
})
ASSET_ROLES = frozenset({"cover", "gallery", "attachment", "logo", "document", "profile"})
ASSET_KINDS = frozenset({"image", "document", "pdf", "video", "other"})
ASSET_STORAGE_STATUSES = frozenset({"local", "external", "missing"})
ASSET_DATA_FIELDS = frozenset({
    "filename", "original_filename", "path", "mime_type", "size", "kind",
    "alt_text", "caption", "source_url", "storage_status", "is_external",
    "width", "height", "duration_seconds", "checksum", "uid",
    "content_sha256", "source_url_sha256",
})
ASSET_LINK_FIELDS = frozenset({
    "path", "url", "role", "kind", "caption", "alt_text", "is_primary", "sort_order",
    "uid", "checksum", "content_sha256", "source_url_sha256", "storage_status",
    "is_external", "filename", "original_filename", "mime_type", "size",
    "width", "height", "duration_seconds",
})

ENTITY_DATA_FIELDS = {
    "event": frozenset({
        "uid", "slug", "title", "start_date", "end_date", "date_text", "date_precision",
        "location", "description", "event_type", "series_key", "parent_event_id",
        "parent_event_slug", "review_status", "is_featured", "sort_order", "remote_url",
    }),
    "news": frozenset({
        "uid", "slug", "title", "news_type", "card_layout", "date", "date_text",
        "date_precision", "date_is_inferred", "date_inference_rule", "original_date_text",
        "summary", "body", "review_status", "is_featured", "source_kind", "source_priority",
        "source_order", "display_order", "sort_order",
    }),
    "member": frozenset({
        "uid", "slug", "first_name", "last_name", "display_name", "affiliation", "country",
        "email", "role", "role_id", "field", "bio", "review_status", "is_active", "sort_order",
        "normalized_affiliation", "normalized_name",
    }),
    "publication": frozenset({
        "uid", "slug", "title", "year", "authors", "journal", "doi", "abstract",
        "date_text", "date_precision", "review_status", "sort_order",
    }),
    "research_area": frozenset({
        "uid", "slug", "title", "summary", "description", "review_status", "sort_order",
    }),
    "page": frozenset({
        "uid", "slug", "title", "type", "summary", "body", "version", "effective_date",
        "nav_group", "menu_order", "review_status", "sort_order",
    }),
    "sponsor": frozenset({
        "uid", "slug", "name", "description", "sponsor_type", "tier", "is_active", "sort_order",
    }),
}

REQUIRED_FIELDS = {
    "event": frozenset({"title"}),
    "news": frozenset({"title"}),
    "member": frozenset({"display_name"}),
    "publication": frozenset({"title"}),
    "research_area": frozenset({"title"}),
    "page": frozenset({"title"}),
    "sponsor": frozenset({"name"}),
}


def normalize_review_status(value: object, *, default: str = "published") -> str:
    """Map legacy pipeline labels to the canonical database workflow states."""
    raw = str(value or default).strip().casefold()
    if raw in {"needs_review", "pending", "candidate"}:
        raw = "review"
    return raw if raw in REVIEW_STATUSES else default

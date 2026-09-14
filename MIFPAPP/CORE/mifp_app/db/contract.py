"""Canonical SQLite runtime contract for MIFP.

``schema.sql`` is the physical source of truth for a fresh database.  This
module contains only the invariants that every supported runtime database must
satisfy.  Migrations may transform older databases, but after migration they
must satisfy exactly this contract.
"""
from __future__ import annotations

SCHEMA_VERSION = 9

TABLE_GROUPS = {
    "content": frozenset({
        "roles", "members", "events", "news", "publications",
        "research_areas", "pages", "sponsors",
    }),
    "assets_relations": frozenset({
        "assets", "asset_recovery_state", "asset_links", "entity_links",
        "entity_relations",
    }),
    "provenance": frozenset({
        "source_systems", "source_runs", "source_records", "canonical_mappings",
    }),
    "imports_operations": frozenset({
        "import_runs", "import_records", "join_requests", "metrics_daily", "settings",
    }),
    "data_quality": frozenset({
        "quality_runs", "quality_findings", "merge_exclusions", "quality_bundles",
        "quality_bundle_items", "content_aliases", "resolved_pairs",
    }),
    "conferences": frozenset({
        "conference_sites", "conference_people", "conference_assets",
    }),
    "schema": frozenset({"schema_migrations"}),
}

RUNTIME_REQUIRED_TABLES = frozenset().union(*TABLE_GROUPS.values())

REQUIRED_COLUMNS = {
    "assets": frozenset({"id", "uid", "path", "kind", "storage_status", "content_sha256", "source_url_sha256"}),
    "events": frozenset({"id", "uid", "title", "review_status", "remote_url"}),
    "news": frozenset({"id", "uid", "title", "review_status"}),
    "members": frozenset({"id", "uid", "display_name", "review_status"}),
    "publications": frozenset({"id", "uid", "title", "review_status"}),
    "research_areas": frozenset({"id", "uid", "title", "review_status"}),
    "pages": frozenset({"id", "uid", "slug", "title", "review_status"}),
    "sponsors": frozenset({"id", "uid", "name"}),
    "quality_runs": frozenset({"id", "status", "progress_pct", "progress_message"}),
    "conference_sites": frozenset({"id", "slug", "config_json"}),
    "schema_migrations": frozenset({"version", "name", "checksum", "applied_at"}),
}

REQUIRED_INDEXES = frozenset({
    "idx_assets_uid", "idx_members_uid", "idx_events_uid", "idx_news_uid",
    "idx_publications_uid", "idx_research_areas_uid", "idx_pages_uid",
    "idx_sponsors_uid", "idx_assets_content_sha256", "idx_assets_source_url_sha256",
    "idx_source_records_run", "idx_canonical_mappings_entity",
    "idx_import_records_entity", "idx_metrics_daily_date",
})

REQUIRED_TRIGGERS = frozenset({
    "reset_asset_recovery_after_source_change",
    "assign_assets_uid", "assign_members_uid", "assign_events_uid",
    "assign_news_uid", "assign_publications_uid", "assign_research_areas_uid",
    "assign_pages_uid", "assign_sponsors_uid",
})

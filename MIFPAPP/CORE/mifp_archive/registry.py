from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntitySpec:
    type_name: str
    table: str
    uid_prefix: str
    title_field: str


ENTITY_SPECS: tuple[EntitySpec, ...] = (
    EntitySpec("member", "members", "member", "display_name"),
    EntitySpec("event", "events", "event", "title"),
    EntitySpec("news", "news", "news", "title"),
    EntitySpec("publication", "publications", "publication", "title"),
    EntitySpec("research_area", "research_areas", "research_area", "title"),
    EntitySpec("page", "pages", "page", "title"),
    EntitySpec("sponsor", "sponsors", "sponsor", "name"),
)

BY_TYPE = {spec.type_name: spec for spec in ENTITY_SPECS}
BY_TABLE = {spec.table: spec for spec in ENTITY_SPECS}
ENTITY_TYPES = tuple(BY_TYPE)

ARCHIVE_TABLES = {
    *(spec.table for spec in ENTITY_SPECS),
    "roles",
    "assets",
    "asset_links",
    "entity_links",
    "entity_relations",
    "source_systems",
    "source_runs",
    "source_records",
    "canonical_mappings",
    "content_aliases",
    "merge_exclusions",
    "resolved_pairs",
}

RUNTIME_TABLES = {
    "settings",
    "metrics_daily",
    "page_views",
    "join_requests",
    "asset_recovery_state",
}

CONFERENCE_TABLES = {
    "conference_sites",
    "conference_people",
    "conference_assets",
}

WORKFLOW_TABLES = {
    "import_runs",
    "import_records",
    "quality_runs",
    "quality_findings",
    "quality_bundles",
    "quality_bundle_items",
}

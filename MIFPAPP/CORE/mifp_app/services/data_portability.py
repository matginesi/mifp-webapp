from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from ..db.connection import table_exists, utc_now, sha256_file
from ..db.migrations import SCHEMA_VERSION
from .assets import resolve_db_asset_path
from .data_quality.normalizers import stable_fingerprint
from .importers import (
    ASSET_KINDS,
    ASSET_LINK_FIELDS,
    ASSET_ROLES,
    ASSET_STORAGE_STATUSES,
    DATA_FIELDS,
    LINK_ROLES,
    REQUIRED_FIELDS,
    REVIEW_STATUSES,
    TYPE_TO_TABLE,
    import_jsonl,
)

TABLE_TO_TYPE = {table: typ for typ, table in TYPE_TO_TABLE.items()}
PORTABLE_TYPES = ["member", "news", "event", "publication", "research_area", "page", "sponsor"]
EXPORT_SCOPES = {
    "members": {
        "label": "Members", "description": "Member records, links and profile assets.",
        "types": ["member"], "primary": "members", "icon": "bi-people",
    },
    "news": {
        "label": "News", "description": "News records with links and attached assets.",
        "types": ["news"], "primary": "news", "icon": "bi-newspaper",
    },
    "events": {
        "label": "Events", "description": "Public event records with links and assets.",
        "types": ["event"], "primary": "events", "icon": "bi-calendar-event",
    },
    "publications": {
        "label": "Publications", "description": "Publication metadata, documents and external links.",
        "types": ["publication"], "primary": "publications", "icon": "bi-journal-text",
    },
    "research": {
        "label": "Research", "description": "Research areas with their linked assets.",
        "types": ["research_area"], "primary": "research_areas", "icon": "bi-lightbulb",
    },
    "sponsors": {
        "label": "Sponsors", "description": "Sponsor records, logos and destination links.",
        "types": ["sponsor"], "primary": "sponsors", "icon": "bi-building",
    },
    "all": {
        "label": "All content", "description": "Every record type supported by the JSONL import format.",
        "types": PORTABLE_TYPES, "primary": "", "icon": "bi-database",
    },
}

ZIP_RECORDS_NAME = "records.jsonl"
ZIP_MANIFEST_NAME = "manifest.json"
ZIP_STATE_NAME = "state.json"
ZIP_MAX_COMPRESSION_RATIO = 1000
PORTABLE_FORMAT = "mifp-export"  # accepted for backward-compatible imports only
PORTABLE_FORMAT_VERSION = 2
CANONICAL_FORMAT = "mifp-jsonl-v2"
SUPPORTED_FORMAT_VERSIONS = {1, 2}
QUALITY_FINGERPRINT_ACTIONS = {
    "", "aggregated_event", "clean_record", "date_placeholder", "invalid_record",
    "inverted_date_range", "junk_record", "merge_records", "missing_asset_file",
    "missing_date", "multiple_primary_links", "name_inversion", "page_fragment",
    "placeholder_title", "split_aggregated_record",
}


def scope_options() -> list[dict[str, Any]]:
    return [{"key": key, **meta} for key, meta in EXPORT_SCOPES.items()]


def build_import_format_guide() -> str:
    """Build the agent-facing guide from the importer's live field contract."""
    type_notes = {
        "member": "One real person. Use natural given-name/family-name order in display_name.",
        "news": "One announcement or article. Similar wording does not make two news items identical.",
        "event": "One occurrence. Recurring editions must be separate records.",
        "publication": "One scholarly output. Prefer DOI as stable identity when available.",
        "research_area": "One research topic or programme area.",
        "page": "One managed site page.",
        "sponsor": "One sponsoring organisation.",
    }
    field_types = {
        "uid": "string", "slug": "string", "title": "string", "name": "string",
        "first_name": "string", "last_name": "string", "display_name": "string",
        "email": "string", "year": "integer", "sort_order": "integer",
        "source_priority": "integer", "source_order": "integer", "display_order": "integer",
        "menu_order": "integer", "parent_event_id": "integer", "is_featured": "boolean",
        "is_active": "boolean", "date_is_inferred": "boolean", "start_date": "date",
        "end_date": "date", "date": "date", "effective_date": "date",
    }
    field_help = {
        "uid": "Stable external identity. Reuse exactly on every run; never use a database row id.",
        "slug": "Stable lowercase URL key using ASCII words and hyphens; do not add random suffixes.",
        "title": "Canonical human title, trimmed; not a filename, caption, menu label, or surrounding page chrome.",
        "name": "Canonical organisation name, preserving official spelling and legal suffix when sourced.",
        "first_name": "Given name(s), including initials/particles exactly as supported by the source.",
        "last_name": "Family name(s), preserving particles such as de, van, von, Di.",
        "display_name": "Public natural-order name, normally First Last; never Last First unless that is the person's documented usage.",
        "affiliation": "Organisation as stated for this person; do not concatenate conflicting historical affiliations.",
        "normalized_affiliation": "Optional machine-normalized affiliation; omit unless built deterministically.",
        "normalized_name": "Optional machine-normalized name; omit unless built deterministically.",
        "country": "English country name supported by explicit evidence, not guessed from a person's name.",
        "email": "Person-specific email. Lower/upper case is ignored for identity; never infer an address pattern.",
        "role": "Portable member role name, for example member, coordinator, or advisory_board; prefer this over role_id.",
        "role_id": "Installation-local numeric role id. Agents must omit it; dashboard exports may carry it.",
        "field": "Research discipline or position text from the source.",
        "bio": "Clean factual biography; remove navigation, consent text, and repeated headings.",
        "review_status": "Workflow state. Use review for uncertain/new agent extraction; published only after verification.",
        "is_active": "Whether the member/sponsor is active. Omit unless the source establishes this.",
        "sort_order": "Deterministic display order; use 0 when no curated order exists.",
        "start_date": "ISO start date. For incomplete dates use the documented placeholder convention plus date_precision.",
        "end_date": "ISO end date, only for a genuine range; must not precede start_date.",
        "date": "ISO news date. It is part of news identity, so never fabricate precision.",
        "date_text": "Human source wording for incomplete, inferred, or display-specific dates.",
        "original_date_text": "Unmodified date phrase extracted from the source before normalization.",
        "date_precision": "Precision actually supported by evidence: day, month, year, range, or unknown.",
        "date_is_inferred": "True only when date was derived rather than explicitly printed.",
        "date_inference_rule": "Short deterministic rule identifier explaining an inferred date; omit for explicit dates.",
        "location": "Venue/city/country as one concise factual string; no travel or registration prose.",
        "description": "Clean main descriptive text, preserving paragraphs and factual distinctions.",
        "event_type": "Controlled event category; choose other if evidence does not support a narrower value.",
        "series_key": "Stable identifier shared by editions of one event series; editions remain separate records.",
        "parent_event_slug": "Stable slug of a parent event included in the same or existing dataset.",
        "parent_event_id": "Installation-local id. Agents must omit it and use parent_event_slug.",
        "remote_url": "Canonical external event page URL when distinct from links.",
        "is_featured": "Editorial presentation flag; omit unless explicitly requested by the operator.",
        "news_type": "Controlled editorial category based on what happened, not keyword similarity.",
        "card_layout": "Optional existing theme layout token. Omit for new agent data unless supplied by the operator.",
        "summary": "Concise standalone factual abstract; do not simply truncate mid-sentence.",
        "body": "Complete clean content; preserve distinct news and do not blend other source items into it.",
        "source_kind": "Origin label such as agent, scraper, local, remote, or manual; use one consistent vocabulary per run.",
        "source_priority": "Lower/higher source ranking only when the pipeline defines it; otherwise omit and accept default 50.",
        "source_order": "Stable order within the source feed, otherwise omit.",
        "display_order": "Explicit editorial order, otherwise omit.",
        "year": "Four-digit publication year supported by bibliographic evidence.",
        "authors": "Author names in source order, either one string or a list; do not reorder alphabetically.",
        "journal": "Canonical venue/journal name, without mixing volume/pages unless no separate field exists.",
        "doi": "Canonical DOI such as 10.xxxx/yyy, without doi: or https://doi.org/ decoration.",
        "abstract": "Publication abstract only; do not substitute an unrelated news summary.",
        "type": "Controlled page type when record type is page.",
        "version": "Human policy/document version string, not a database schema version.",
        "effective_date": "ISO date on which a managed page/policy becomes effective.",
        "nav_group": "Existing site navigation group token; omit unless supplied by site configuration.",
        "menu_order": "Integer order inside nav_group; use only when navigation placement is intentional.",
        "sponsor_type": "Organisation relationship category, using source/site vocabulary consistently.",
        "tier": "Sponsorship tier exactly as defined by the programme; never infer from logo size.",
    }
    enums = {
        "review_status": sorted(REVIEW_STATUSES),
        "date_precision": ["day", "month", "year", "range", "unknown"],
        "event_type": ["conference", "workshop", "seminar", "meeting", "school", "project_event", "other"],
        "news_type": ["general", "announcement", "publication_highlight", "agreement", "award", "event_highlight", "institutional", "sponsor", "memorial", "science_commentary"],
        "page.type": ["about", "privacy", "cookie_policy", "manifesto", "code_of_conduct", "documentation", "custom", "legacy_home", "contact", "error_page"],
    }

    example = {
        "type": "news",
        "data": {
            "uid": "news_example_2026_001", "slug": "example-research-announcement",
            "title": "Example Research Announcement", "date": "2026-08-17",
            "date_precision": "day", "summary": "A concise factual summary.",
            "body": "Complete article text without navigation or cookie boilerplate.",
            "news_type": "announcement", "review_status": "review", "source_kind": "agent",
        },
        "links": [{
            "url": "https://example.org/news/announcement", "role": "source",
            "label": "Original announcement", "is_primary": True, "sort_order": 1,
        }],
        "assets": [],
    }
    member_example = {
        "type": "member",
        "data": {
            "uid": "member_jacqueline_bloch", "slug": "jacqueline-bloch",
            "first_name": "Jacqueline", "last_name": "Bloch",
            "display_name": "Jacqueline Bloch", "affiliation": "CNRS",
            "country": "France", "review_status": "review",
        },
        "links": [{"url": "https://example.org/people/jacqueline-bloch", "role": "website"}],
        "assets": [{
            "url": "https://example.org/media/jacqueline-bloch.jpg", "role": "profile",
            "kind": "image", "alt_text": "Jacqueline Bloch", "is_primary": True,
        }],
    }
    type_examples = {
        "member": member_example,
        "news": example,
        "event": {
            "type": "event", "data": {
                "uid": "event_plmcn_2027", "slug": "plmcn-2027",
                "title": "PLMCN 2027", "start_date": "2027-06-14",
                "end_date": "2027-06-18", "date_precision": "range",
                "location": "Rome, Italy", "event_type": "conference",
                "series_key": "plmcn", "review_status": "review",
            }, "links": [{"url": "https://example.org/plmcn-2027", "role": "primary"}], "assets": [],
        },
        "publication": {
            "type": "publication", "data": {
                "uid": "publication_10_1234_example", "slug": "light-matter-coupling-review",
                "title": "Light–Matter Coupling: A Review", "year": 2026,
                "authors": ["Ada Example", "Bruno Example"], "journal": "Example Physics",
                "doi": "10.1234/example.2026.42", "date_precision": "year",
                "review_status": "review",
            }, "links": [{"url": "https://doi.org/10.1234/example.2026.42", "role": "doi"}], "assets": [],
        },
        "research_area": {
            "type": "research_area", "data": {
                "uid": "research_quantum_fluids", "slug": "quantum-fluids",
                "title": "Quantum Fluids", "summary": "Research on collective quantum phenomena.",
                "description": "A source-grounded description of the programme.", "review_status": "review",
            }, "links": [], "assets": [],
        },
        "page": {
            "type": "page", "data": {
                "uid": "page_code_of_conduct", "slug": "code-of-conduct",
                "title": "Code of Conduct", "type": "code_of_conduct",
                "body": "Complete approved page content.", "version": "1.0",
                "effective_date": "2026-08-17", "review_status": "review",
            }, "links": [], "assets": [],
        },
        "sponsor": {
            "type": "sponsor", "data": {
                "uid": "sponsor_example_lab", "slug": "example-lab", "name": "Example Lab",
                "description": "Official programme sponsor.", "sponsor_type": "institutional",
                "tier": "gold", "is_active": True,
            }, "links": [{"url": "https://example.org", "role": "website"}], "assets": [{
                "url": "https://example.org/logo.svg", "role": "logo", "kind": "image",
                "alt_text": "Example Lab", "is_primary": True,
            }],
        },
    }
    compact = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    lines = [
        "# MIFP data-generation guide for agents and LLMs", "",
        f"> Target: UTF-8 JSONL. Packaged format: `{CANONICAL_FORMAT}` version `{PORTABLE_FORMAT_VERSION}`.", "",
        "## Objective", "",
        "Transform supplied material into clean, factual MIFP records for **Dashboard → Import / Export**. "
        "The safest agent output is record-only `.jsonl`: exactly one JSON object per line, with no "
        "Markdown fences, comments, headings, trailing commas, or explanatory prose in the output file.", "",
        "Do not invent missing facts. Omit unknown optional fields. Preserve meaningful text, accents, "
        "names, dates, URLs, and distinctions between separate people, articles, events, or publications.", "",
        "## Required workflow", "",
        "1. Inventory sources and assign every item to one supported record type.",
        "2. Extract facts and provenance; never merge on title similarity alone.",
        "3. Normalize names, dates, URLs, identifiers, whitespace, and obvious boilerplate.",
        "4. Deduplicate only on strong identity evidence described below.",
        "5. Emit one compact UTF-8 JSON object per line.",
        "6. Run **Validate only** first; import only after zero structural errors and a count review.", "",
        "## Record envelope", "",
        "Only these top-level keys are accepted:", "",
        "| Key | Required | Meaning |", "| --- | --- | --- |",
        "| `type` | yes | A supported singular type below. |",
        "| `data` | yes | Only fields allowed for that type. |",
        "| `links` | no | External link objects; default `[]`. |",
        "| `assets` | no | Local or remote asset objects; default `[]`. |",
        "| `meta` | no | Provenance metadata. Do not use `exported_from_id` in new agent data. |", "",
        "```json", compact(example), "```", "",
        "## Supported record types and fields", "",
        "Unknown fields are rejected. Keep `uid` and `slug` deterministic across repeated runs. "
        "Prefer lowercase hyphenated slugs; the importer can generate one only as a fallback.", "",
    ]
    for typ in sorted(DATA_FIELDS):
        lines.extend([
            f"### `{typ}`", "", type_notes[typ], "",
            "| Field | Required | Type / allowed | Construction rule |", "| --- | --- | --- | --- |",
        ])
        for field in sorted(DATA_FIELDS[typ]):
            expected = field_types.get(field, "string or null")
            enum_key = "page.type" if typ == "page" and field == "type" else field
            if enum_key in enums:
                expected = "one of: " + ", ".join(f"`{item}`" for item in enums[enum_key])
            lines.append(
                f"| `{field}` | {'yes' if field in REQUIRED_FIELDS[typ] else 'no'} | "
                f"{expected} | {field_help.get(field, 'Source-supported value; omit rather than guess.')} |"
            )
        lines.append("")
        lines.extend(["Valid example:", "", "```json", compact(type_examples[typ]), "```", ""])

    lines.extend([
        "## Links", "",
        "A link accepts only `url`, `role`, `label`, `is_primary`, and `sort_order`.", "",
        f"- `role`: {', '.join(f'`{v}`' for v in sorted(LINK_ROLES))}.",
        "- Use an absolute HTTP(S) canonical source URL and at most one primary link.",
        "- `is_primary` is a JSON boolean; `sort_order` is an integer starting at 1.",
        "- PDF/Office URLs in ordinary JSONL may be promoted to document assets.", "",
        "## Assets", "",
        "An asset needs `path` or `url`. Do not fabricate checksums, dimensions, MIME types, or paths.", "",
        f"- Allowed keys: {', '.join(f'`{v}`' for v in sorted(ASSET_LINK_FIELDS))}.",
        f"- `role`: {', '.join(f'`{v}`' for v in sorted(ASSET_ROLES))}.",
        f"- `kind`: {', '.join(f'`{v}`' for v in sorted(ASSET_KINDS))}.",
        f"- `storage_status`: {', '.join(f'`{v}`' for v in sorted(ASSET_STORAGE_STATUSES))}; normally omit it.",
        "- Paths must be relative, contain no `..` or backslashes, and identify a supplied file.",
        "- Write useful image `alt_text`; do not prefix it with “image of”.", "",
        "```json", compact(member_example), "```", "",
        "## Dates and values", "",
        "- Exact date: `YYYY-MM-DD`. Month: first day plus `date_precision: \"month\"`. "
        "Year: `YYYY-01-01` plus `date_precision: \"year\"`.",
        "- Keep uncertain wording in `date_text`/`original_date_text`; never invent day precision.",
        "- Use JSON booleans and numbers, not quoted substitutes. Omit unknown optional values.",
        "- `authors` may be a string or list; it is stored as a comma-separated string.", "",
        "## Identity, duplicates, and merging", "",
        "Repeated imports are idempotent when stable identities are reused. Matching considers `uid`, "
        "`slug`, provenance, then strong type-specific identity:", "",
        "- member: same complete name (order-insensitive), strengthened by email; different non-empty emails mean different people;",
        "- publication: same normalized DOI;",
        "- event: same recognized series and year;",
        "- news: same normalized full title **and exact date**, or same canonical source URL;",
        "- other types: same normalized title/name or canonical source URL.", "",
        "Never merge two news items only because they share words, topics, people, institutions, or book titles. "
        "Different dates, sources, bodies, awards, agreements, announcements, and event editions remain separate. "
        "For uncertainty use `review_status: \"review\"`; do not use force-import as an identity decision.", "",
        "## Content quality", "",
        "- One record is one real entity/content item; never manufacture an article from a caption or filename.",
        "- Member `display_name` uses natural `First Last` order; preserve particles and diacritics.",
        "- Remove menus, cookie banners, breadcrumbs, related lists, and repeated headers from body fields.",
        "- Prefer primary sources and retain their URL as `source` or `primary`.",
        "- Never silently combine conflicting names, affiliations, dates, titles, or descriptions.",
        "- Use `published` only for verified material; otherwise use `review`.", "",
        "## File/package choices", "",
        "### Record-only JSONL (recommended for agents)", "",
        "Use any `.jsonl` filename and one envelope per line. One JSON object or a JSON array is also "
        "accepted, but JSONL gives better large-file and line-error handling.", "",
        "### ZIP (records plus local files)", "",
        "A compatible ZIP contains `manifest.json`, `records.jsonl`, optional `state.json`, and declared "
        "files under `assets/`. Each declared file needs exact byte size and lowercase SHA-256. "
        "`records_sha256` and `state_sha256` hash the exact UTF-8 bytes. Paths are relative and unique; "
        "any mismatch rejects the archive.", "",
        "Agents should not generate `state.json`: it is installation-owned durable state (settings, "
        "quality decisions, relations, provenance, mappings). Use dashboard export for a lossless backup. "
        "For new content with local assets use the scraper artifact assembler or deterministic packaging "
        "code, never LLM-generated checksums.", "",
        "## Import scopes", "",
        "The selected dashboard scope must match the content being imported. A record-only JSONL may "
        "contain only types allowed by that scope. A packaged ZIP/JSONL declares its scope in the manifest.", "",
        "| Scope | Allowed record types |", "| --- | --- |",
        "| `members` | `member` |", "| `news` | `news` |", "| `events` | `event` |",
        "| `publications` | `publication` |", "| `research` | `research_area` |",
        "| `sponsors` | `sponsor` |", "| `all` | every supported type, including `page` |", "",
        "If a generated dataset contains more than one type, instruct the operator to select `all`.", "",
        "## Exact ZIP manifest contract", "",
        "This example shows shape only. A deterministic program must replace counts, timestamps, sizes, "
        "and digests after serializing the final byte streams. Do not copy placeholder hashes.", "",
        "```json", compact({
            "format": CANONICAL_FORMAT, "format_version": PORTABLE_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION, "generated_at": "2026-08-17T12:00:00Z",
            "exported_at": "2026-08-17T12:00:00Z", "app_version": "",
            "scope": "news", "records": 1,
            "records_sha256": "<64 lowercase hexadecimal characters>",
            "counts": {"news": 1},
            "files": [{
                "path": "news/example/image.jpg", "archive_path": "assets/news/example/image.jpg",
                "size": 12345, "sha256": "<64 lowercase hexadecimal characters>",
            }],
        }), "```", "",
        "Manifest invariants:", "",
        "- `format`, `format_version`, `scope`, `records`, `records_sha256`, `counts`, and `files` are required for canonical packages.",
        "- `records` equals the number of non-empty record lines; `counts` exactly groups them by singular type.",
        "- `path` is the database-relative asset path; `archive_path` is exactly `assets/` plus that path.",
        "- Every archive asset is declared exactly once; no undeclared asset or unsupported extra file is allowed.",
        "- A full `all` dashboard backup also requires `state.json`, `state_sha256`, and `state_counts`.",
        "- Hash exact bytes, not parsed/reformatted JSON. Repacking or pretty-printing after hashing invalidates the package.", "",
        "## Self-contained JSONL v2 envelope", "",
        "Dashboard JSONL exports are not ordinary record-only JSONL. Their first line is an `_mifp` "
        "manifest envelope, followed by optional durable state, bounded Base64 asset chunks, then ordinary "
        "record objects. This is designed for dashboard round-trip, not freehand agent output.", "",
        "```json", compact({"_mifp": {"kind": "manifest", "data": {"format": CANONICAL_FORMAT, "format_version": PORTABLE_FORMAT_VERSION, "scope": "news", "records": 1, "records_sha256": "<sha256>", "counts": {"news": 1}, "files": []}}}), "```", "",
        "```json", compact({"_mifp": {"kind": "asset_chunk", "path": "news/example/image.jpg", "archive_path": "assets/news/example/image.jpg", "index": 0, "final": True, "encoding": "base64", "data": "<base64>"}}), "```", "",
        "Asset chunks for one file must be contiguous, zero-indexed, complete, and together match the "
        "manifest size/hash. Prefer ZIP when binary assets are involved.", "",
        "## Durable state: reserved for dashboard backups", "",
        "`state.json` is an object whose supported list sections are: `roles`, `settings`, `assets`, "
        "`metrics_daily`, `merge_exclusions`, `resolved_pairs`, `quality_decisions`, `entity_relations`, "
        "`join_requests`, `content_aliases`, `source_systems`, `source_runs`, `source_records`, and "
        "`canonical_mappings`. It may contain security-sensitive or installation-specific operational data.", "",
        "An agent creating new editorial content must omit durable state. Only preserve it byte-for-byte from "
        "a dashboard export. Never synthesize roles, settings, quality decisions, join requests, source lineage, "
        "or canonical mappings from source documents.", "",
        "## Provenance and `meta`", "",
        "`meta` is stored with import provenance but is not public content. For new agent datasets it may "
        "contain concise non-sensitive traceability such as source document name, source item key, extraction "
        "timestamp, or an operator-supplied batch id. Do not place credentials, personal notes, raw private "
        "documents, chain-of-thought, or prompts in it. Never set `exported_from_id`: that marker is reserved "
        "for dashboard exports and changes restore behavior.", "",
        "Recommended shape (keys are descriptive metadata, not identity):", "",
        "```json", compact({"source_document": "announcements-2026.pdf", "source_item": "page-4-item-2", "batch": "operator-provided-batch-id", "extraction_confidence": 0.93}), "```", "",
        "## What import actually does", "",
        "- Ordinary import matches existing records and enriches them. It does not blindly replace curated non-empty values.",
        "- `uid` match has priority, then `slug`, recorded provenance, and strong type-specific identity keys.",
        "- Existing name/display_name/slug values are not overwritten by ordinary enrichment.",
        "- Existing real descriptive text is not replaced merely because incoming text is longer; empty or obvious placeholder values may be enriched.",
        "- Boolean featured/active flags are additive during enrichment.",
        "- Links and asset links are added/updated with primary-link normalization.",
        "- A malformed record is rolled back to its savepoint and reported with its line number; other valid records may continue.",
        "- A fatal package-integrity error rejects the package. A multi-file batch is committed only if its database transaction completes.",
        "- `Validate only` verifies syntax/structure/package integrity but cannot prove factual truth and does not exercise every final database conflict.",
        "- Actual import creates a pre-import database backup. Asset/network failures may be reported separately from record errors.", "",
        "Never tell the operator that validation proves the facts are correct. It proves conformance, not truth.", "",
        "## Per-type decision rules", "",
        "### Members", "",
        "Split one person per record. Resolve `Surname Given` or `Surname, Given` into explicit first/last "
        "fields and natural display order. Keep homonyms separate when emails differ. Do not treat title prefixes "
        "(Prof., Dr.) as name tokens. Do not create a second person merely because affiliation formatting changed.", "",
        "### News", "",
        "A news record represents one dated editorial occurrence. Book announcements for different books, "
        "agreements with different partners, awards to different recipients, and different editions/dates are "
        "separate even when the wording template is nearly identical. Title similarity is context, never sufficient "
        "merge authority. Retain the exact canonical source URL and enough clean body/summary to disambiguate.", "",
        "### Events", "",
        "One edition/occurrence per record. Reuse `series_key` across editions but keep year-specific `uid` and "
        "slug`. Use `parent_event_slug` only for a real containment relationship, not merely related events. "
        "A year-only event is not a full-year range unless the source explicitly says so.", "",
        "### Publications", "",
        "DOI dominates identity when present. Normalize it without URL/prefix. Preserve author order. A news item "
        "announcing a publication and the publication itself are two different records of different types.", "",
        "### Research areas, pages, sponsors", "",
        "Do not infer editorial navigation, sponsorship tier, active status, policy version, or effective date from "
        "visual prominence. Preserve official organisation/topic/page naming and only emit site-control fields when supplied.", "",
        "## Failure modes the agent must prevent", "",
        "| Bad output | Why it fails or causes damage | Correct action |", "| --- | --- | --- |",
        "| Markdown code fences inside `.jsonl` | They are not JSON records. | Save raw JSON lines only. |",
        "| Unknown key such as `content` | Strict schema rejects the line. | Map to `body`, `description`, or `abstract` as appropriate. |",
        "| `review_status: archived` | Not a supported content-table status. | Use draft, review, published, quarantined, or duplicate. |",
        "| Same slug reused for different news | Creates a false identity collision. | Derive stable item-specific slugs and UIDs. |",
        "| Random UID on every run | Re-import can create duplicates. | Derive UID deterministically from source identity. |",
        "| Same person emitted as First Last and Last First | Name-order duplicates. | Normalize explicit name parts and one display_name. |",
        "| Missing date replaced with today's date | Fabricated identity and chronology. | Omit date; retain source wording and unknown precision. |",
        "| Remote asset invented from page URL | Asset retrieval errors or wrong media. | Use the direct media URL only when evidenced. |",
        "| Hand-edited ZIP after hashing | Integrity verification rejects it. | Rebuild manifest and all hashes deterministically. |",
        "| Conflicting facts blended together | Silent semantic data loss. | Keep records separate or mark review. |", "",
        "## Copy/paste task prompt for an agent", "",
        "Use the following prompt together with this guide and the source material:", "",
        "```text",
        "You are preparing data for the MIFP importer. Treat MIFP_LLM_IMPORT_GUIDE.md as a strict contract.",
        "Read all supplied source material before emitting records. Build an internal evidence inventory first.",
        "Do not reveal chain-of-thought. Do not invent facts. Do not merge entities on weak similarity.",
        "For every proposed record, verify type, required fields, stable identity, date precision, provenance URL,",
        "and separation from every other record. When uncertain, keep records separate and set review_status to review.",
        "Produce two deliverables:",
        "1. dataset.jsonl — UTF-8, one compact valid JSON object per line, no fences/comments/prose.",
        "2. generation-report.md — source inventory, counts by type, omitted/uncertain items, duplicate decisions with",
        "   evidence, warnings, and validation checklist. Never put this report inside dataset.jsonl.",
        "Unless explicitly asked for a packaged backup, produce record-only JSONL and do not create state.json,",
        "manifest hashes, installation ids, or local asset paths.",
        "```", "",
        "## Required generation report", "",
        "The companion report is for human review and is not imported. It must contain:", "",
        "- input source list and any unreadable/missing material;",
        "- record counts by type and total;",
        "- every omitted item with reason;",
        "- every deduplication/merge decision and the strong identity evidence used;",
        "- every uncertain date, identity, affiliation, category, or asset;",
        "- deterministic UID/slug strategy;",
        "- whether URLs/assets were verified or merely copied from supplied material;",
        "- JSON parse/schema self-check outcome and a SHA-256 of the final `dataset.jsonl` when code execution is available.", "",
        "## Final checklist", "",
        "- [ ] UTF-8; one JSON object per line; no Markdown/prose in the data file.",
        "- [ ] Supported type, data object, required field, and no unknown keys.",
        "- [ ] Deterministic UIDs/slugs reused for repeated source items.",
        "- [ ] Dates match their precision and no facts were invented.",
        "- [ ] Separate people/news/events/publications were not merged on weak similarity.",
        "- [ ] Canonical source URLs and real/reachable or actually packaged assets.",
        "- [ ] Uncertain records use `review_status: \"review\"`.",
        "- [ ] Dashboard **Validate only** finishes with zero structural errors.", "",
    ])
    return "\n".join(lines)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in [*TYPE_TO_TABLE.values(), "assets", "asset_links", "entity_links", "join_requests", "settings"]:
        if table_exists(conn, table):
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    return counts


def build_export_bundle(conn: sqlite3.Connection, scope: str) -> dict[str, Any]:
    if scope not in EXPORT_SCOPES:
        raise ValueError("Invalid export scope")
    records = _records_for_scope(conn, scope)
    return {
        "meta": {
            "scope": scope,
            "exported_at": utc_now(),
            "format": CANONICAL_FORMAT,
            "format_version": PORTABLE_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
        },
        "records": records,
    }




def _write_bundle_zip(
    conn: sqlite3.Connection,
    scope: str,
    assets_dir: Path,
    target: BytesIO | Path,
    *,
    app_version: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    def report(message: str, pct: int) -> None:
        if progress_callback:
            progress_callback(message, pct)

    bundle = build_export_bundle(conn, scope)
    report("Collecting records…", 5)
    records = bundle.get("records") or []
    asset_rows = _asset_rows_for_scope(conn, scope, records)
    records_payload = _records_to_jsonl(records)
    report("Serializing records…", 15)
    durable_state = _durable_state(conn) if scope == "all" else None
    state_payload = (
        json.dumps(durable_state, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        if durable_state is not None
        else None
    )
    manifest: dict[str, Any] = {
        "format": CANONICAL_FORMAT,
        "format_version": PORTABLE_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "exported_at": bundle["meta"]["exported_at"],
        "app_version": app_version,
        "scope": scope,
        "records": len(records),
        "records_sha256": hashlib.sha256(records_payload).hexdigest(),
        "counts": _record_counts(records),
        "files": [],
    }
    if state_payload is not None and durable_state is not None:
        manifest["state_sha256"] = hashlib.sha256(state_payload).hexdigest()
        manifest["state_counts"] = {
            key: len(value) for key, value in durable_state.items() if isinstance(value, list)
        }

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(ZIP_RECORDS_NAME, records_payload)
        if state_payload is not None:
            zf.writestr(ZIP_STATE_NAME, state_payload)
        seen_archive_paths: set[str] = set()
        for asset in asset_rows:
            db_path = str(asset.get("path") or "").strip()
            if not db_path:
                continue
            path = resolve_db_asset_path(assets_dir, db_path)
            if not path.is_file():
                continue
            archive_path = db_path if db_path.startswith("assets/") else f"assets/{db_path}"
            archive_path = _validate_asset_archive_path(archive_path)
            if archive_path in seen_archive_paths:
                continue
            seen_archive_paths.add(archive_path)
            report(f"Packaging assets {len(seen_archive_paths)}/{len(asset_rows)}…", 15 + 70 * len(seen_archive_paths) // max(len(asset_rows), 1))
            zf.write(path, archive_path)
            manifest["files"].append({
                "path": db_path,
                "archive_path": archive_path,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        report("Writing manifest…", 92)
        zf.writestr(
            ZIP_MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
    report("Finalizing…", 100)
    return manifest


def bundle_to_zip(
    conn: sqlite3.Connection, scope: str, assets_dir: Path, *, app_version: str = ""
) -> bytes:
    """Compatibility API returning ZIP bytes; prefer bundle_to_zip_file for HTTP exports."""
    out = BytesIO()
    _write_bundle_zip(conn, scope, assets_dir, out, app_version=app_version)
    return out.getvalue()


def bundle_to_zip_file(
    conn: sqlite3.Connection,
    scope: str,
    assets_dir: Path,
    destination: Path,
    *,
    app_version: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
) -> int:
    """Write a portable ZIP directly to disk and return its byte size."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_bundle_zip(conn, scope, assets_dir, destination,
                      app_version=app_version, progress_callback=progress_callback)
    return destination.stat().st_size




def bundle_to_jsonl_file(
    conn: sqlite3.Connection,
    scope: str,
    assets_dir: Path,
    destination: Path,
    *,
    app_version: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Write a self-contained JSONL v2 package equivalent to the ZIP export.

    Canonical record lines remain ordinary JSONL records. Package metadata,
    durable state, and local assets use a reserved ``_mifp`` envelope so the
    importer can restore the same information without an accompanying folder.
    Legacy record-only JSONL files remain supported by ``import_jsonl_payload``.
    """
    def report(message: str, pct: int) -> None:
        if progress_callback:
            progress_callback(message, pct)

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_export_bundle(conn, scope)
    report("Collecting records…", 5)
    records = bundle.get("records") or []
    durable_state = _durable_state(conn) if scope == "all" else None
    asset_rows = _asset_rows_for_scope(conn, scope, records)
    records_payload = _records_to_jsonl(records)
    report("Serializing records…", 15)
    packaged_assets: list[dict[str, Any]] = []
    for asset in asset_rows:
        db_path = str(asset.get("path") or "").strip()
        if not db_path:
            continue
        local_path = resolve_db_asset_path(assets_dir, db_path)
        if not local_path.is_file():
            continue
        archive_path = db_path if db_path.startswith("assets/") else f"assets/{db_path}"
        archive_path = _validate_asset_archive_path(archive_path)
        packaged_assets.append({
            "path": db_path,
            "archive_path": archive_path,
            "size": local_path.stat().st_size,
            "sha256": sha256_file(local_path),
            "source": local_path,
        })
        report(f"Packaging assets {len(packaged_assets)}/{len(asset_rows)}…", 15 + 70 * len(packaged_assets) // max(len(asset_rows), 1))

    manifest = {
        "format": CANONICAL_FORMAT,
        "format_version": PORTABLE_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "exported_at": bundle["meta"]["exported_at"],
        "app_version": app_version,
        "scope": scope,
        "records": len(records),
        "records_sha256": hashlib.sha256(records_payload).hexdigest(),
        "counts": _record_counts(records),
        "files": [
            {key: item[key] for key in ("path", "archive_path", "size", "sha256")}
            for item in packaged_assets
        ],
        "container": "jsonl",
    }
    if durable_state is not None:
        state_payload = json.dumps(durable_state, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        manifest["state_sha256"] = hashlib.sha256(state_payload).hexdigest()
        manifest["state_counts"] = {key: len(value) for key, value in durable_state.items() if isinstance(value, list)}

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps({"_mifp": {"kind": "manifest", "data": manifest}}, ensure_ascii=False, sort_keys=True) + "\n")
        if durable_state is not None:
            output.write(json.dumps({"_mifp": {"kind": "state", "data": durable_state}}, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        # Keep every JSONL line bounded: large binary files are emitted as
        # independently decodable Base64 chunks instead of one enormous line.
        # 1 MiB is divisible only after choosing a 3-byte aligned chunk size.
        asset_chunk_bytes = 3 * 256 * 1024
        for item in packaged_assets:
            source = Path(item["source"])
            total_size = int(item["size"])
            emitted = 0
            chunk_index = 0
            with source.open("rb") as asset_in:
                while True:
                    chunk = asset_in.read(asset_chunk_bytes)
                    if not chunk and (total_size > 0 or chunk_index > 0):
                        break
                    emitted += len(chunk)
                    final = emitted >= total_size
                    output.write(json.dumps({"_mifp": {
                        "kind": "asset_chunk",
                        "path": item["path"],
                        "archive_path": item["archive_path"],
                        "index": chunk_index,
                        "final": final,
                        "encoding": "base64",
                        "data": base64.b64encode(chunk).decode("ascii"),
                    }}, ensure_ascii=False, sort_keys=True) + "\n")
                    chunk_index += 1
                    if final:
                        break
        output.write(records_payload.decode("utf-8"))
    temporary.replace(destination)
    manifest["bytes"] = destination.stat().st_size
    report("Finalizing…", 100)
    return manifest


def import_jsonl_payload(
    conn: sqlite3.Connection,
    raw: Path,
    scope: str,
    assets_dir: Path,
    *,
    dry_run: bool = False,
    skip_assets: bool = False,
    progress: Callable[[int, int], None] | None = None,
    asset_detail: Callable[[str], None] | None = None,
    force_import: bool = False,
    source_name: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Import either legacy record-only JSONL or self-contained JSONL v2."""
    path = Path(raw)
    try:
        package_bytes = path.stat().st_size
    except OSError as exc:
        raise ValueError("JSONL package is not available") from exc
    # Self-contained JSONL uses base64 for binary assets, so it can be larger
    # than records.jsonl. The HTTP upload limit remains the outer hard bound.
    if package_bytes > max(Config.IMPORT_MAX_JSONL_BYTES, Config.IMPORT_MAX_ZIP_BYTES * 2):
        raise ValueError("JSONL package exceeds configured maximum size")
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            first = next((line for line in handle if line.strip()), "")
        first_obj = json.loads(first) if first else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        first_obj = {}
    envelope = first_obj.get("_mifp") if isinstance(first_obj, dict) else None
    if not isinstance(envelope, dict) or envelope.get("kind") != "manifest":
        return import_jsonl(
            conn, path, dry_run=dry_run, assets_dir=assets_dir, progress=progress,
            asset_detail=asset_detail, force_import=force_import, source_name=source_name, cancel_check=cancel_check, commit=commit,
        )

    with tempfile.TemporaryDirectory(prefix="mifp-jsonl-package-") as temp_dir:
        tmp = Path(temp_dir)
        records_path = tmp / ZIP_RECORDS_NAME
        state: dict[str, Any] | None = None
        manifest = _validate_manifest_object(envelope.get("data"))
        if manifest.get("format") != CANONICAL_FORMAT or int(manifest.get("format_version") or 0) != PORTABLE_FORMAT_VERSION:
            raise ValueError("Unsupported JSONL package format/version")
        if manifest.get("scope") != scope:
            raise ValueError(f"Import scope {scope!r} does not match package scope {manifest.get('scope')!r}")
        declared_rows = manifest.get("files") or []
        declared_archive_paths = _manifest_asset_paths(manifest)
        declared_files = {str(item["archive_path"]): item for item in declared_rows}
        if set(declared_files) != declared_archive_paths:
            raise ValueError("JSONL package manifest contains invalid or duplicate asset entries")
        seen_files: set[str] = set()
        active_asset: dict[str, Any] | None = None
        active_handle = None
        record_count = 0
        record_types: dict[str, int] = {}
        records_digest = hashlib.sha256()
        try:
            with records_path.open("w", encoding="utf-8", newline="\n") as records_out, path.open("r", encoding="utf-8-sig") as handle:
                for line_no, line in enumerate(handle, 1):
                    if cancel_check and cancel_check():
                        from .job_manager import JobCancelled
                        raise JobCancelled("Import cancelled by administrator")
                    if not line.strip():
                        continue
                    # Bound an individual line before json.loads. State may be
                    # larger than ordinary entries; asset chunks are checked
                    # against a much smaller limit after their kind is known.
                    line_bytes = len(line.encode("utf-8"))
                    if line_bytes > max(Config.IMPORT_MAX_STATE_BYTES, Config.IMPORT_MAX_MANIFEST_BYTES):
                        raise ValueError(f"JSONL package line {line_no} exceeds the maximum entry size")
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid JSONL package line {line_no}: {exc.msg}") from exc
                    meta = item.get("_mifp") if isinstance(item, dict) else None
                    if not isinstance(meta, dict):
                        if active_asset is not None:
                            raise ValueError("JSONL asset chunks must be contiguous")
                        record_count += 1
                        if record_count > Config.IMPORT_MAX_JSONL_LINES:
                            raise ValueError(f"JSONL package exceeds maximum record count: {Config.IMPORT_MAX_JSONL_LINES}")
                        typ = str(item.get("type") or "")
                        if typ:
                            record_types[typ] = record_types.get(typ, 0) + 1
                        serialized = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                        records_out.write(serialized)
                        records_digest.update(serialized.encode("utf-8"))
                        continue
                    kind = meta.get("kind")
                    if kind == "manifest":
                        if line_bytes > Config.IMPORT_MAX_MANIFEST_BYTES:
                            raise ValueError("JSONL package manifest exceeds the maximum size")
                        continue
                    if kind == "state":
                        if line_bytes > Config.IMPORT_MAX_STATE_BYTES:
                            raise ValueError("JSONL package state exceeds the maximum size")
                        if active_asset is not None:
                            raise ValueError("JSONL asset chunks must be contiguous")
                        if state is not None:
                            raise ValueError("JSONL package contains duplicate state metadata")
                        state = meta.get("data")
                        if not isinstance(state, dict):
                            raise ValueError("JSONL package state is invalid")
                        continue
                    if kind not in {"asset", "asset_chunk"}:
                        raise ValueError(f"Unsupported JSONL package entry at line {line_no}")
                    if kind == "asset_chunk" and line_bytes > 2 * 1024 * 1024:
                        raise ValueError(f"JSONL asset chunk is too large: line {line_no}")

                    # ``asset`` is retained as an import-only compatibility path
                    # for packages created by the first v2 implementation. New
                    # exports always use bounded ``asset_chunk`` entries.
                    archive_path = _validate_asset_archive_path(str(meta.get("archive_path") or ""))
                    declared = declared_files.get(archive_path)
                    if declared is None:
                        raise ValueError(f"JSONL package contains undeclared asset: {archive_path}")
                    expected_path = str(declared.get("path") or "")
                    if str(meta.get("path") or "") != expected_path:
                        raise ValueError(f"JSONL asset path mismatch: {archive_path}")
                    if meta.get("encoding") != "base64":
                        raise ValueError(f"Unsupported JSONL asset encoding: {archive_path}")
                    try:
                        data = base64.b64decode(str(meta.get("data") or ""), validate=True)
                    except Exception as exc:
                        raise ValueError(f"Invalid base64 asset payload: {archive_path}") from exc

                    if kind == "asset":
                        if active_asset is not None or archive_path in seen_files:
                            raise ValueError(f"JSONL package contains duplicate asset: {archive_path}")
                        if len(data) != int(declared.get("size") or 0) or hashlib.sha256(data).hexdigest() != str(declared.get("sha256") or ""):
                            raise ValueError(f"JSONL asset failed integrity verification: {archive_path}")
                        target = tmp / archive_path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        seen_files.add(archive_path)
                        continue

                    index = int(meta.get("index") if meta.get("index") is not None else -1)
                    final = bool(meta.get("final"))
                    if active_asset is None:
                        if archive_path in seen_files or index != 0:
                            raise ValueError(f"JSONL asset chunk sequence is invalid: {archive_path}")
                        target = tmp / archive_path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        active_handle = target.open("wb")
                        active_asset = {
                            "archive_path": archive_path,
                            "next_index": 0,
                            "size": 0,
                            "sha256": hashlib.sha256(),
                            "declared": declared,
                        }
                    if active_asset["archive_path"] != archive_path or index != active_asset["next_index"]:
                        raise ValueError(f"JSONL asset chunk sequence is invalid: {archive_path}")
                    active_handle.write(data)
                    active_asset["sha256"].update(data)
                    active_asset["size"] += len(data)
                    active_asset["next_index"] += 1
                    if active_asset["size"] > int(declared.get("size") or 0):
                        raise ValueError(f"JSONL asset exceeds declared size: {archive_path}")
                    if final:
                        active_handle.close()
                        active_handle = None
                        if active_asset["size"] != int(declared.get("size") or 0) or active_asset["sha256"].hexdigest() != str(declared.get("sha256") or ""):
                            raise ValueError(f"JSONL asset failed integrity verification: {archive_path}")
                        seen_files.add(archive_path)
                        active_asset = None
        finally:
            if active_handle is not None:
                active_handle.close()
        if active_asset is not None:
            raise ValueError(f"JSONL package contains an incomplete asset: {active_asset['archive_path']}")
        if set(declared_files) != seen_files:
            missing = sorted(set(declared_files) - seen_files)
            raise ValueError(f"JSONL package is missing {len(missing)} declared asset file(s)")
        if int(manifest.get("records") or 0) != record_count:
            raise ValueError("JSONL package record count does not match manifest")
        if manifest.get("counts") != record_types:
            raise ValueError("JSONL package record type counts do not match manifest")
        if manifest.get("records_sha256") and records_digest.hexdigest() != str(manifest.get("records_sha256")):
            raise ValueError("JSONL package records failed integrity verification")
        _validate_manifest_scope(scope, record_types)
        if scope == "all" and state is None:
            raise ValueError("JSONL package is missing durable state")
        if state is not None and manifest.get("state_sha256"):
            state_raw = json.dumps(state, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            if hashlib.sha256(state_raw).hexdigest() != manifest["state_sha256"]:
                raise ValueError("JSONL package state failed integrity verification")
        if state is not None:
            state = _normalize_durable_state(state, manifest, source_label="JSONL package state")

        summary = import_jsonl(
            conn, records_path, dry_run=dry_run, assets_dir=assets_dir,
            asset_source_dir=None if skip_assets else tmp / "assets", import_assets=not skip_assets,
            progress=progress, asset_detail=asset_detail, force_import=force_import, source_name=source_name, cancel_check=cancel_check, commit=False,
        )
        if cancel_check and cancel_check():
            from .job_manager import JobCancelled
            raise JobCancelled("Import cancelled by administrator")
        if not dry_run and state is not None:
            summary["restored_state"] = _restore_durable_state(conn, state, assets_dir, tmp / "assets")
        summary["manifest"] = manifest
        summary["jsonl_package"] = {"record_count": record_count, "asset_files": len(seen_files)}
        if not dry_run and commit:
            conn.commit()
        return summary


def parse_zip_payload(raw: bytes | Path) -> dict[str, Any]:
    if isinstance(raw, Path):
        if not raw.is_file():
            raise ValueError("Uploaded file is not available")
        size = raw.stat().st_size
        zip_source: BytesIO | Path = raw
    else:
        size = len(raw)
        zip_source = BytesIO(raw)
    if size > Config.IMPORT_MAX_ZIP_BYTES:
        raise ValueError(f"ZIP package exceeds maximum size: {Config.IMPORT_MAX_ZIP_BYTES} bytes")
    try:
        zf = zipfile.ZipFile(zip_source, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid ZIP archive") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > Config.IMPORT_MAX_FILES:
            raise ValueError(f"ZIP package exceeds maximum file count: {Config.IMPORT_MAX_FILES}")
        _validate_zip_members(infos)
        unpacked = sum(info.file_size for info in infos)
        if unpacked > Config.IMPORT_MAX_UNPACKED_BYTES:
            raise ValueError(f"ZIP package expands beyond maximum size: {Config.IMPORT_MAX_UNPACKED_BYTES} bytes")
        names = set(zf.namelist())
        if ZIP_MANIFEST_NAME not in names:
            raise ValueError(f"ZIP package is missing {ZIP_MANIFEST_NAME}")
        if ZIP_RECORDS_NAME not in names:
            raise ValueError(f"ZIP package is missing {ZIP_RECORDS_NAME}")
        manifest = _read_manifest(zf)
        format_version = int(manifest.get("format_version") or 1)
        if format_version >= 2 and manifest.get("scope") == "all" and ZIP_STATE_NAME not in names:
            raise ValueError(f"ZIP package is missing {ZIP_STATE_NAME}")
        manifest_files = _manifest_asset_paths(manifest)
        asset_names = {name for name in names if name.startswith("assets/") and not name.endswith("/")}
        unexpected_assets = sorted(asset_names - manifest_files)
        if unexpected_assets:
            raise ValueError(f"ZIP contains asset files not declared in manifest: {', '.join(unexpected_assets[:5])}")
        unexpected_files = sorted(
            name for name in names
            if name not in {ZIP_MANIFEST_NAME, ZIP_RECORDS_NAME, ZIP_STATE_NAME}
            and not name.startswith("assets/")
            and not name.endswith("/")
        )
        if unexpected_files:
            raise ValueError(f"ZIP contains unsupported files: {', '.join(unexpected_files[:5])}")
        records_info = zf.getinfo(ZIP_RECORDS_NAME)
        if records_info.file_size > Config.IMPORT_MAX_JSONL_BYTES:
            raise ValueError(
                f"records.jsonl exceeds maximum size: {Config.IMPORT_MAX_JSONL_BYTES} bytes"
            )
        records_raw = zf.read(ZIP_RECORDS_NAME)
        expected_records_hash = manifest.get("records_sha256")
        if expected_records_hash and hashlib.sha256(records_raw).hexdigest() != expected_records_hash:
            raise ValueError("records.jsonl failed integrity verification")
        _verify_manifest_assets(zf, manifest)
        records = records_raw.decode("utf-8-sig")
        record_stats = _inspect_records_jsonl(records)
        _validate_manifest_scope(manifest["scope"], record_stats["record_types"])
        declared_records = manifest.get("records")
        if declared_records is not None and declared_records != record_stats["record_count"]:
            raise ValueError(
                f"manifest.records declares {declared_records}, but records.jsonl contains "
                f"{record_stats['record_count']} record(s)"
            )
        declared_counts = manifest.get("counts")
        if declared_counts is not None and declared_counts != record_stats["record_types"]:
            raise ValueError("manifest.counts does not match records.jsonl")
        missing_assets = sorted(manifest_files - asset_names)
        durable_state = _read_durable_state(zf, manifest) if ZIP_STATE_NAME in names else None
    return {
        "manifest": manifest,
        "records_jsonl": records,
        "record_count": record_stats["record_count"],
        "record_types": record_stats["record_types"],
        "tables": {typ: [None] * count for typ, count in record_stats["record_types"].items()},
        "asset_files": sorted(asset_names),
        "missing_assets": missing_assets,
        "durable_state": durable_state,
    }


def import_zip_payload(
    conn: sqlite3.Connection,
    raw: bytes | Path,
    scope: str,
    assets_dir: Path,
    *,
    dry_run: bool = False,
    skip_assets: bool = False,
    progress: Callable[[int, int], None] | None = None,
    force_import: bool = False,
    source_name: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    package = parse_zip_payload(raw)
    if scope not in EXPORT_SCOPES:
        raise ValueError("Invalid import scope")
    if package["manifest"]["scope"] != scope:
        raise ValueError(
            f"Import scope {scope!r} does not match package scope {package['manifest']['scope']!r}"
        )
    missing_assets = package.get("missing_assets") or []
    if missing_assets and not skip_assets:
        raise ValueError(f"ZIP is missing {len(missing_assets)} declared asset file(s)")
    with tempfile.TemporaryDirectory(prefix="mifp-import-") as tmp:
        tmp_path = Path(tmp)
        records_path = tmp_path / "records.jsonl"
        records_path.write_text(package["records_jsonl"], encoding="utf-8")
        packaged_assets_dir = tmp_path / "assets"
        if not dry_run and not skip_assets:
            _extract_zip_assets(raw, packaged_assets_dir, asset_files=package["asset_files"])
        summary = import_jsonl(
            conn,
            records_path,
            dry_run=dry_run,
            assets_dir=assets_dir,
            asset_source_dir=None if skip_assets else packaged_assets_dir,
            import_assets=not skip_assets,
            progress=progress,
            force_import=force_import,
            source_name=source_name,
            cancel_check=cancel_check,
            commit=False,
        )
        if cancel_check and cancel_check():
            from .job_manager import JobCancelled
            raise JobCancelled("Import cancelled by administrator")
        if not dry_run and package.get("durable_state") is not None:
            summary["restored_state"] = _restore_durable_state(
                conn,
                package["durable_state"],
                assets_dir,
                packaged_assets_dir,
            )
        summary["manifest"] = package["manifest"]
        summary["zip"] = {
            "record_count": package["record_count"],
            "record_types": package["record_types"],
            "asset_files": len(package["asset_files"]),
            "missing_assets": missing_assets,
        }
        if not dry_run and commit:
            conn.commit()
        if not dry_run and commit:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        return summary


def _records_for_scope(conn: sqlite3.Connection, scope: str) -> list[dict[str, Any]]:
    wanted = EXPORT_SCOPES[scope]["types"]
    links_by_entity = _links_for_types(conn, wanted)
    assets_by_entity = _assets_for_types(conn, wanted)
    role_names = _role_names(conn)
    event_slugs = _event_slugs(conn)
    records: list[dict[str, Any]] = []
    for typ in wanted:
        table = TYPE_TO_TABLE[typ]
        if not table_exists(conn, table):
            continue
        for row in _rows(conn, table):
            entity_id = int(row["id"])
            data = _strip_runtime(row)
            if typ == "member":
                role_id = data.pop("role_id", None)
                if role_id in role_names:
                    data["role"] = role_names[role_id]
            elif typ == "event":
                parent_id = data.pop("parent_event_id", None)
                if parent_id in event_slugs:
                    data["parent_event_slug"] = event_slugs[parent_id]
            records.append({
                "type": typ,
                "data": data,
                "links": links_by_entity.get((typ, entity_id), []),
                "assets": assets_by_entity.get((typ, entity_id), []),
                "meta": {"exported_from_id": entity_id},
            })
    return records


def _entity_reference(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> dict[str, Any]:
    table = TYPE_TO_TABLE.get(entity_type)
    if not table or not table_exists(conn, table):
        return {"type": entity_type, "exported_id": entity_id}
    row = conn.execute(f"SELECT slug FROM {table} WHERE id=?", (entity_id,)).fetchone()
    return {
        "type": entity_type,
        "slug": str(row["slug"]) if row and row["slug"] else None,
        "exported_id": entity_id,
    }


def _durable_state(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    state: dict[str, list[dict[str, Any]]] = {}
    state["roles"] = [
        _strip_runtime(dict(row))
        for row in conn.execute("SELECT name,label FROM roles ORDER BY id")
    ] if table_exists(conn, "roles") else []
    state["settings"] = [
        _strip_runtime(dict(row))
        for row in conn.execute("SELECT key,value FROM settings ORDER BY key")
    ] if table_exists(conn, "settings") else []
    state["assets"] = [
        _strip_runtime(row) for row in _asset_rows(conn)
    ]
    state["metrics_daily"] = [
        _strip_runtime(dict(row))
        for row in conn.execute(
            "SELECT date,scope,metric_name,metric_key,metric_value,extra_json "
            "FROM metrics_daily ORDER BY date,scope,metric_name,metric_key"
        )
    ] if table_exists(conn, "metrics_daily") else []
    state["merge_exclusions"] = [
        _strip_runtime(dict(row))
        for row in conn.execute(
            "SELECT entity_type,record_fingerprint,decision,note,created_by "
            "FROM merge_exclusions ORDER BY id"
        )
    ] if table_exists(conn, "merge_exclusions") else []
    state["resolved_pairs"] = [
        _strip_runtime(dict(row))
        for row in conn.execute(
            "SELECT entity_type,left_fingerprint,right_fingerprint,action,applied_at "
            "FROM resolved_pairs ORDER BY id"
        )
    ] if table_exists(conn, "resolved_pairs") else []
    state["quality_decisions"] = _portable_quality_decisions(conn)
    state["entity_relations"] = []
    if table_exists(conn, "entity_relations"):
        for row in conn.execute(
            "SELECT source_type,source_id,target_type,target_id,role,sort_order "
            "FROM entity_relations ORDER BY id"
        ):
            state["entity_relations"].append({
                "source": _entity_reference(conn, str(row["source_type"]), int(row["source_id"])),
                "target": _entity_reference(conn, str(row["target_type"]), int(row["target_id"])),
                "role": row["role"],
                "sort_order": row["sort_order"],
            })
    state["join_requests"] = []
    if table_exists(conn, "join_requests"):
        for raw in conn.execute("SELECT * FROM join_requests ORDER BY id"):
            # created_at makes restoring the same archive idempotent.
            row = dict(raw)
            row.pop("id", None)
            member_id = row.pop("member_id", None)
            if member_id:
                ref = _entity_reference(conn, "member", int(member_id))
                row["member_slug"] = ref.get("slug")
            state["join_requests"].append(row)
    state["content_aliases"] = [
        {
            "entity_type": row["entity_type"],
            "old_slug": row["old_slug"],
            "canonical_slug": row["canonical_slug"],
        }
        for row in conn.execute(
            "SELECT entity_type,old_slug,canonical_slug FROM content_aliases ORDER BY id"
        )
    ] if table_exists(conn, "content_aliases") else []
    state.update(_provenance_state(conn))
    return state


def _provenance_state(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Portable representation of scraper lineage tables.

    References between source_systems, source_runs and source_records are
    exported as stable uids so they survive an id remap on restore.
    """
    state: dict[str, list[dict[str, Any]]] = {
        "source_systems": [],
        "source_runs": [],
        "source_records": [],
        "canonical_mappings": [],
    }
    if not table_exists(conn, "source_systems"):
        return state
    system_ids: dict[int, str] = {}
    for row in conn.execute(
        "SELECT id,uid,name,kind,base_url,description FROM source_systems ORDER BY id"
    ):
        state["source_systems"].append({
            "uid": row["uid"],
            "name": row["name"],
            "kind": row["kind"],
            "base_url": row["base_url"],
            "description": row["description"],
        })
        system_ids[int(row["id"])] = row["uid"]
    if table_exists(conn, "source_runs"):
        run_ids: dict[int, str] = {}
        for row in conn.execute(
            "SELECT id,uid,source_system_id,scraper_version,parser_version,started_at,"
            "completed_at,status,source_snapshot_sha256,stats_json,notes "
            "FROM source_runs ORDER BY id"
        ):
            state["source_runs"].append({
                "uid": row["uid"],
                "source_system_uid": system_ids.get(int(row["source_system_id"])),
                "scraper_version": row["scraper_version"],
                "parser_version": row["parser_version"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": row["status"],
                "source_snapshot_sha256": row["source_snapshot_sha256"],
                "stats_json": row["stats_json"],
                "notes": row["notes"],
            })
            run_ids[int(row["id"])] = row["uid"]
    if table_exists(conn, "source_records"):
        for row in conn.execute(
            "SELECT id,uid,source_run_id,source_system_id,external_id,source_url,"
            "source_path,fetched_at,raw_sha256,raw_payload,record_type,mapping_status "
            "FROM source_records ORDER BY id"
        ):
            state["source_records"].append({
                "uid": row["uid"],
                "source_run_uid": run_ids.get(int(row["source_run_id"])) if row["source_run_id"] else None,
                "source_system_uid": system_ids.get(int(row["source_system_id"])) if row["source_system_id"] else None,
                "external_id": row["external_id"],
                "source_url": row["source_url"],
                "source_path": row["source_path"],
                "fetched_at": row["fetched_at"],
                "raw_sha256": row["raw_sha256"],
                "raw_payload": row["raw_payload"],
                "record_type": row["record_type"],
                "mapping_status": row["mapping_status"],
            })
    if table_exists(conn, "canonical_mappings"):
        record_uids: dict[int, str] = {
            int(row["id"]): row["uid"] for row in conn.execute(
                "SELECT id,uid FROM source_records WHERE uid IS NOT NULL"
            )
        }
        for row in conn.execute(
            "SELECT source_record_id,entity_type,entity_uid,mapping_kind,confidence,decision_note "
            "FROM canonical_mappings ORDER BY id"
        ):
            state["canonical_mappings"].append({
                "source_record_uid": record_uids.get(int(row["source_record_id"])),
                "entity_type": row["entity_type"],
                "entity_uid": row["entity_uid"],
                "mapping_kind": row["mapping_kind"],
                "confidence": row["confidence"],
                "decision_note": row["decision_note"],
            })
    return state


def _legacy_quality_fingerprint(entity_type: str, records: list[dict], action: str) -> str:
    material = [
        {
            key: value for key, value in sorted(row.items())
            if key not in {"sort_order", "source_order", "display_order"}
        }
        for row in sorted(records, key=lambda item: int(item["id"]))
    ]
    return hashlib.sha256(
        json.dumps(
            [entity_type, action, material], ensure_ascii=False, sort_keys=True, default=str
        ).encode()
    ).hexdigest()


def _portable_quality_decisions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "quality_findings"):
        return []
    output: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT action_type,entity_type,record_ids_json,classification,score,evidence_json,"
        "contradictions_json,fingerprint,status "
        "FROM quality_findings WHERE status IN ('resolved','rejected','deferred') ORDER BY id"
    )
    for raw in rows:
        item = _strip_runtime(dict(raw))
        entity_type = str(item.get("entity_type") or "")
        table = "assets" if entity_type == "asset" else TYPE_TO_TABLE.get(entity_type)
        try:
            record_ids = [int(value) for value in json.loads(item.pop("record_ids_json", "[]"))]
        except (TypeError, ValueError, json.JSONDecodeError):
            record_ids = []
        records: list[dict[str, Any]] = []
        if table and record_ids:
            placeholders = ",".join("?" for _ in record_ids)
            records = [
                dict(row) for row in conn.execute(
                    f"SELECT * FROM {table} WHERE id IN ({placeholders})", record_ids
                )
            ]
        if len(records) == len(record_ids) and records:
            stored = str(item.get("fingerprint") or "")
            for action in QUALITY_FINGERPRINT_ACTIONS:
                if stored in {
                    _legacy_quality_fingerprint(entity_type, records, action),
                    stable_fingerprint(entity_type, records, action=action),
                }:
                    item["fingerprint"] = stable_fingerprint(entity_type, records, action=action)
                    break
        output.append(item)
    return output


def _target_entity_id(conn: sqlite3.Connection, reference: dict[str, Any]) -> int | None:
    entity_type = str(reference.get("type") or "")
    table = TYPE_TO_TABLE.get(entity_type)
    if not table:
        return None
    slug = str(reference.get("slug") or "").strip()
    if slug:
        row = conn.execute(f"SELECT id FROM {table} WHERE slug=?", (slug,)).fetchone()
        if row:
            return int(row["id"])
    exported_id = reference.get("exported_id")
    if isinstance(exported_id, int):
        row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (exported_id,)).fetchone()
        if row:
            return int(row["id"])
    return None


def _restore_durable_state(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    assets_dir: Path,
    packaged_assets_dir: Path,
) -> dict[str, int]:
    restored: dict[str, int] = {}
    for role in state.get("roles") or []:
        if not isinstance(role, dict) or not role.get("name"):
            continue
        conn.execute(
            "INSERT INTO roles(name,label) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET label=excluded.label",
            (role["name"], role.get("label")),
        )
        restored["roles"] = restored.get("roles", 0) + 1
    for setting in state.get("settings") or []:
        if not isinstance(setting, dict) or not setting.get("key"):
            continue
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
            (setting["key"], setting.get("value")),
        )
        restored["settings"] = restored.get("settings", 0) + 1
    for metric in state.get("metrics_daily") or []:
        if not isinstance(metric, dict):
            continue
        conn.execute(
            "INSERT INTO metrics_daily(date,scope,metric_name,metric_key,metric_value,extra_json) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(date,scope,metric_name,metric_key) "
            "DO UPDATE SET metric_value=excluded.metric_value,extra_json=excluded.extra_json,"
            "updated_at=CURRENT_TIMESTAMP",
            (
                metric.get("date"), metric.get("scope"), metric.get("metric_name"),
                metric.get("metric_key", ""), metric.get("metric_value", 0), metric.get("extra_json"),
            ),
        )
        restored["metrics_daily"] = restored.get("metrics_daily", 0) + 1
    for exclusion in state.get("merge_exclusions") or []:
        if not isinstance(exclusion, dict):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO merge_exclusions("
            "entity_type,record_fingerprint,decision,note,created_by"
            ") VALUES(?,?,?,?,?)",
            (
                exclusion.get("entity_type"), exclusion.get("record_fingerprint"),
                exclusion.get("decision"), exclusion.get("note"), exclusion.get("created_by"),
            ),
        )
        restored["merge_exclusions"] = restored.get("merge_exclusions", 0) + 1
    for pair in state.get("resolved_pairs") or []:
        if not isinstance(pair, dict):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO resolved_pairs("
            "entity_type,left_fingerprint,right_fingerprint,action,applied_at"
            ") VALUES(?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))",
            (
                pair.get("entity_type"), pair.get("left_fingerprint"),
                pair.get("right_fingerprint"), pair.get("action"), pair.get("applied_at"),
            ),
        )
        restored["resolved_pairs"] = restored.get("resolved_pairs", 0) + 1
    decisions = [
        item for item in state.get("quality_decisions") or []
        if isinstance(item, dict) and item.get("fingerprint") and not conn.execute(
            "SELECT 1 FROM quality_findings WHERE fingerprint=? AND status=? LIMIT 1",
            (item.get("fingerprint"), item.get("status")),
        ).fetchone()
    ]
    if decisions:
        run_id = conn.execute(
            "INSERT INTO quality_runs(status,fingerprint,summary_json,completed_at) "
            "VALUES('completed','portable-restored-decisions','{}',CURRENT_TIMESTAMP)"
        ).lastrowid
        for decision in decisions:
            conn.execute(
                "INSERT OR IGNORE INTO quality_findings("
                "run_id,action_type,entity_type,record_ids_json,classification,score,"
                "evidence_json,contradictions_json,plan_json,fingerprint,status"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, decision.get("action_type"), decision.get("entity_type"), "[]",
                    decision.get("classification"), decision.get("score", 0),
                    decision.get("evidence_json", "[]"), decision.get("contradictions_json", "[]"),
                    "{}", decision.get("fingerprint"), decision.get("status"),
                ),
            )
        restored["quality_decisions"] = len(decisions)
    for relation in state.get("entity_relations") or []:
        if not isinstance(relation, dict):
            continue
        source_ref = relation.get("source")
        target_ref = relation.get("target")
        source: dict[str, Any] = source_ref if isinstance(source_ref, dict) else {}
        target: dict[str, Any] = target_ref if isinstance(target_ref, dict) else {}
        source_id, target_id = _target_entity_id(conn, source), _target_entity_id(conn, target)
        if source_id is None or target_id is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO entity_relations("
            "source_type,source_id,target_type,target_id,role,sort_order"
            ") VALUES(?,?,?,?,?,?)",
            (
                source.get("type"), source_id, target.get("type"), target_id,
                relation.get("role", "related"), relation.get("sort_order", 0),
            ),
        )
        restored["entity_relations"] = restored.get("entity_relations", 0) + 1
    for request_row in state.get("join_requests") or []:
        if not isinstance(request_row, dict) or not request_row.get("email"):
            continue
        payload = dict(request_row)
        member_slug = str(payload.pop("member_slug", "") or "")
        member = conn.execute("SELECT id FROM members WHERE slug=?", (member_slug,)).fetchone() if member_slug else None
        payload["member_id"] = int(member["id"]) if member else None
        columns = [name for name in payload if name in {str(row["name"]) for row in conn.execute("PRAGMA table_info(join_requests)")} and name != "id"]
        duplicate = conn.execute(
            "SELECT id FROM join_requests WHERE email=? AND created_at=?",
            (payload.get("email"), payload.get("created_at")),
        ).fetchone()
        if not duplicate:
            conn.execute(
                f"INSERT INTO join_requests({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                tuple(payload[name] for name in columns),
            )
        restored["join_requests"] = restored.get("join_requests", 0) + 1
    for alias in state.get("content_aliases") or []:
        if not isinstance(alias, dict):
            continue
        table = TYPE_TO_TABLE.get(str(alias.get("entity_type") or ""))
        canonical = conn.execute(
            f"SELECT id FROM {table} WHERE slug=?", (alias.get("canonical_slug"),)
        ).fetchone() if table else None
        if not canonical:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO content_aliases("
            "entity_type,old_slug,canonical_entity_id,canonical_slug,bundle_id"
            ") VALUES(?,?,?,?,NULL)",
            (
                alias.get("entity_type"), alias.get("old_slug"),
                int(canonical["id"]), alias.get("canonical_slug"),
            ),
        )
        restored["content_aliases"] = restored.get("content_aliases", 0) + 1
    _restore_provenance(conn, state, restored)
    _restore_unlinked_assets(conn, state.get("assets") or [], assets_dir, packaged_assets_dir, restored)
    conn.commit()
    return restored


def _restore_provenance(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    restored: dict[str, int],
) -> None:
    """Restore scraper lineage (source_systems/runs/records + canonical_mappings)."""
    if not table_exists(conn, "source_systems"):
        return
    system_uid_to_id: dict[str, int] = {}
    for system in state.get("source_systems") or []:
        if not isinstance(system, dict) or not system.get("uid"):
            continue
        conn.execute(
            "INSERT INTO source_systems(uid,name,kind,base_url,description) VALUES(?,?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET name=excluded.name,kind=excluded.kind,"
            "base_url=excluded.base_url,description=excluded.description,updated_at=CURRENT_TIMESTAMP",
            (system["uid"], system.get("name"), system.get("kind"),
             system.get("base_url"), system.get("description")),
        )
        row = conn.execute("SELECT id FROM source_systems WHERE uid=?", (system["uid"],)).fetchone()
        system_uid_to_id[system["uid"]] = int(row["id"])
        restored["source_systems"] = restored.get("source_systems", 0) + 1
    run_uid_to_id: dict[str, int] = {}
    if table_exists(conn, "source_runs"):
        for run in state.get("source_runs") or []:
            if not isinstance(run, dict) or not run.get("uid"):
                continue
            system_id = system_uid_to_id.get(str(run.get("source_system_uid") or ""))
            conn.execute(
                "INSERT INTO source_runs(uid,source_system_id,scraper_version,parser_version,"
                "started_at,completed_at,status,source_snapshot_sha256,stats_json,notes) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET source_system_id=excluded.source_system_id,"
                "scraper_version=excluded.scraper_version,parser_version=excluded.parser_version,"
                "started_at=excluded.started_at,completed_at=excluded.completed_at,status=excluded.status,"
                "source_snapshot_sha256=excluded.source_snapshot_sha256,stats_json=excluded.stats_json,"
                "notes=excluded.notes",
                (run["uid"], system_id, run.get("scraper_version"), run.get("parser_version"),
                 run.get("started_at"), run.get("completed_at"), run.get("status"),
                 run.get("source_snapshot_sha256"), run.get("stats_json"), run.get("notes")),
            )
            row = conn.execute("SELECT id FROM source_runs WHERE uid=?", (run["uid"],)).fetchone()
            run_uid_to_id[run["uid"]] = int(row["id"])
            restored["source_runs"] = restored.get("source_runs", 0) + 1
    record_uid_to_id: dict[str, int] = {}
    if table_exists(conn, "source_records"):
        for record in state.get("source_records") or []:
            if not isinstance(record, dict) or not record.get("uid"):
                continue
            run_id = run_uid_to_id.get(str(record.get("source_run_uid") or ""))
            system_id = system_uid_to_id.get(str(record.get("source_system_uid") or ""))
            conn.execute(
                "INSERT INTO source_records(uid,source_run_id,source_system_id,external_id,source_url,"
                "source_path,fetched_at,raw_sha256,raw_payload,record_type,mapping_status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET source_run_id=excluded.source_run_id,"
                "source_system_id=excluded.source_system_id,external_id=excluded.external_id,"
                "source_url=excluded.source_url,source_path=excluded.source_path,fetched_at=excluded.fetched_at,"
                "raw_sha256=excluded.raw_sha256,raw_payload=excluded.raw_payload,"
                "record_type=excluded.record_type,mapping_status=excluded.mapping_status",
                (record["uid"], run_id, system_id, record.get("external_id"), record.get("source_url"),
                 record.get("source_path"), record.get("fetched_at"), record.get("raw_sha256"),
                 record.get("raw_payload"), record.get("record_type"), record.get("mapping_status")),
            )
            row = conn.execute("SELECT id FROM source_records WHERE uid=?", (record["uid"],)).fetchone()
            record_uid_to_id[record["uid"]] = int(row["id"])
            restored["source_records"] = restored.get("source_records", 0) + 1
    if table_exists(conn, "canonical_mappings"):
        mappings = [
            item for item in state.get("canonical_mappings") or []
            if isinstance(item, dict) and item.get("source_record_uid") in record_uid_to_id
        ]
        if mappings:
            record_ids = {record_uid_to_id[str(item["source_record_uid"])] for item in mappings}
            placeholders = ",".join("?" for _ in record_ids)
            conn.execute(
                f"DELETE FROM canonical_mappings WHERE source_record_id IN ({placeholders})",
                tuple(record_ids),
            )
            for mapping in mappings:
                record_id = record_uid_to_id[str(mapping["source_record_uid"])]
                conn.execute(
                    "INSERT INTO canonical_mappings("
                    "source_record_id,entity_type,entity_uid,mapping_kind,confidence,decision_note"
                    ") VALUES(?,?,?,?,?,?)",
                    (record_id, mapping.get("entity_type"), mapping.get("entity_uid"),
                     mapping.get("mapping_kind"), mapping.get("confidence"), mapping.get("decision_note")),
                )
                restored["canonical_mappings"] = restored.get("canonical_mappings", 0) + 1


def _restore_unlinked_assets(
    conn: sqlite3.Connection,
    assets: list[Any],
    assets_dir: Path,
    packaged_assets_dir: Path,
    restored: dict[str, int],
) -> None:
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(assets)")}
    root = Path(assets_dir).resolve()
    for item in assets:
        if not isinstance(item, dict):
            continue
        path_text = str(item.get("path") or "").strip()
        if not path_text:
            continue
        _validate_asset_archive_path(f"assets/{path_text.removeprefix('assets/')}")
        relative = Path(path_text.removeprefix("assets/"))
        source = (Path(packaged_assets_dir) / relative).resolve()
        target = (root / relative).resolve()
        if root not in target.parents:
            continue
        if source.is_file() and not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        existing = conn.execute("SELECT id FROM assets WHERE path=?", (path_text,)).fetchone()
        if not existing and item.get("checksum"):
            existing = conn.execute("SELECT id FROM assets WHERE checksum=?", (item["checksum"],)).fetchone()
        payload = {key: value for key, value in item.items() if key in columns and key != "id"}
        if existing:
            assignments = [f"{key}=?" for key in payload if key not in {"path", "checksum"}]
            if assignments:
                conn.execute(
                    f"UPDATE assets SET {','.join(assignments)},updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*[payload[key] for key in payload if key not in {"path", "checksum"}], int(existing["id"])),
                )
        else:
            names = list(payload)
            conn.execute(
                f"INSERT INTO assets({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                tuple(payload[name] for name in names),
            )
        restored["assets"] = restored.get("assets", 0) + 1


def _strip_runtime(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"id", "created_at", "updated_at"} and v is not None}


def _links_for_types(
    conn: sqlite3.Connection, types: Sequence[str]
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    if not table_exists(conn, "entity_links"):
        return {}
    placeholders = ",".join("?" for _ in types)
    rows = conn.execute(
        f"""
        SELECT entity_type, entity_id, url, role, label, is_primary, sort_order
        FROM entity_links
        WHERE entity_type IN ({placeholders})
        ORDER BY entity_type, entity_id, sort_order, id
        """,
        types,
    ).fetchall()
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        key = (str(item.pop("entity_type")), int(item.pop("entity_id")))
        grouped.setdefault(key, []).append(_strip_runtime(item))
    return grouped


def _assets_for_types(
    conn: sqlite3.Connection, types: Sequence[str]
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    if not table_exists(conn, "asset_links"):
        return {}
    placeholders = ",".join("?" for _ in types)
    rows = conn.execute(
        f"""
        SELECT al.entity_type, al.entity_id, a.path, a.source_url AS url,
               al.role, a.kind, a.caption, a.alt_text, al.is_primary, al.sort_order,
               a.uid, a.checksum, a.content_sha256, a.source_url_sha256,
               a.filename, a.original_filename, a.mime_type, a.size,
               a.storage_status, a.is_external, a.width, a.height, a.duration_seconds
        FROM asset_links al
        JOIN assets a ON a.id = al.asset_id
        WHERE al.entity_type IN ({placeholders})
        ORDER BY al.entity_type, al.entity_id, al.sort_order, al.id
        """,
        types,
    ).fetchall()
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        key = (str(item.pop("entity_type")), int(item.pop("entity_id")))
        grouped.setdefault(key, []).append(_strip_runtime(item))
    return grouped


def _role_names(conn: sqlite3.Connection) -> dict[int, str]:
    if not table_exists(conn, "roles"):
        return {}
    return {int(row["id"]): str(row["name"]) for row in conn.execute("SELECT id, name FROM roles")}


def _event_slugs(conn: sqlite3.Connection) -> dict[int, str]:
    if not table_exists(conn, "events"):
        return {}
    return {
        int(row["id"]): str(row["slug"])
        for row in conn.execute("SELECT id, slug FROM events WHERE slug IS NOT NULL AND slug != ''")
    }


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id ASC").fetchall()]


def _asset_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "assets"):
        return []
    return _rows(conn, "assets")


def _asset_rows_for_scope(conn: sqlite3.Connection, scope: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if scope == "all":
        return _asset_rows(conn)
    paths: set[str] = set()
    for record in records:
        for asset in record.get("assets") or []:
            if isinstance(asset, dict) and asset.get("path"):
                paths.add(str(asset["path"]).strip())
    asset_rows: list[dict[str, Any]] = []
    if paths and table_exists(conn, "assets"):
        rows = _asset_rows(conn)
        asset_rows = [row for row in rows if str(row.get("path") or "").strip() in paths]
    return asset_rows


def _record_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        typ = str(record.get("type") or "").strip()
        if typ:
            counts[typ] = counts.get(typ, 0) + 1
    return counts


def _records_to_jsonl(records: list[dict[str, Any]]) -> bytes:
    lines = [
        json.dumps(record, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _extract_zip_assets(raw: bytes | Path, assets_dir: Path, *, asset_files: list[str] | None = None) -> None:
    if asset_files is None:
        package = parse_zip_payload(raw)
        asset_files = package.get("asset_files") or []
    allowed = set(asset_files)
    root = Path(assets_dir).resolve()
    zip_source: BytesIO | Path = BytesIO(raw) if isinstance(raw, bytes) else raw
    with zipfile.ZipFile(zip_source, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if name not in allowed:
                continue
            rel = Path(_validate_asset_archive_path(name))
            # Archive paths are rooted at assets/; ASSETS_DIR is that directory.
            if rel.parts and rel.parts[0] == "assets":
                rel = Path(*rel.parts[1:])
            target = (root / rel).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Unsafe asset target in ZIP: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


def _validate_zip_members(infos: list[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    for info in infos:
        name = info.filename
        _validate_archive_name(name, allow_directory=True)
        if name in seen:
            raise ValueError(f"ZIP contains duplicate file name: {name}")
        seen.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise ValueError(f"ZIP contains a symbolic link: {name}")
        if info.compress_size == 0 and info.file_size > 0:
            raise ValueError(f"ZIP member has invalid compressed size: {name}")
        if info.compress_size > 0 and info.file_size > 1024 * 1024:
            ratio = info.file_size / info.compress_size
            if ratio > ZIP_MAX_COMPRESSION_RATIO:
                raise ValueError(f"ZIP member has suspicious compression ratio: {name}")


def _validate_archive_name(name: str, *, allow_directory: bool = False) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("ZIP contains an empty file name")
    if "\x00" in name or "\\" in name:
        raise ValueError(f"ZIP contains an unsafe file name: {name}")
    if name.startswith(("/", "./")) or ":" in PurePosixPath(name).parts[0]:
        raise ValueError(f"ZIP contains an unsafe file name: {name}")
    is_dir = name.endswith("/")
    if is_dir and not allow_directory:
        raise ValueError(f"ZIP contains an unexpected directory entry: {name}")
    parts = name[:-1].split("/") if is_dir else name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"ZIP contains an unsafe file path: {name}")
    return name


def _validate_asset_archive_path(path: str) -> str:
    name = _validate_archive_name(str(path or ""), allow_directory=False)
    if not name.startswith("assets/"):
        raise ValueError(f"Asset archive path must start with assets/: {name}")
    return name


def _validate_manifest_object(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("package manifest must contain an object")
    package_format = manifest.get("format")
    if package_format not in (None, PORTABLE_FORMAT, CANONICAL_FORMAT):
        raise ValueError(f"Unsupported export format: {package_format!r}")
    format_version = manifest.get("format_version")
    if format_version is not None and format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(f"Unsupported export format version: {format_version!r}")
    if package_format == CANONICAL_FORMAT and format_version != PORTABLE_FORMAT_VERSION:
        raise ValueError(
            f"{CANONICAL_FORMAT} packages must declare format_version={PORTABLE_FORMAT_VERSION}"
        )
    schema_version = manifest.get("schema_version")
    if schema_version is not None and (
        not isinstance(schema_version, int) or schema_version < 1 or schema_version > SCHEMA_VERSION
    ):
        raise ValueError(f"Unsupported database schema version: {schema_version!r}")
    records_sha256 = manifest.get("records_sha256")
    if records_sha256 is not None and not _valid_sha256(records_sha256):
        raise ValueError("manifest.records_sha256 must be a SHA-256 digest")
    if package_format == CANONICAL_FORMAT and not records_sha256:
        raise ValueError(f"{CANONICAL_FORMAT} packages require records_sha256")
    state_sha256 = manifest.get("state_sha256")
    if state_sha256 is not None and not _valid_sha256(state_sha256):
        raise ValueError("manifest.state_sha256 must be a SHA-256 digest")
    scope = str(manifest.get("scope") or "").strip()
    if scope not in EXPORT_SCOPES:
        raise ValueError(f"Unsupported package scope: {scope!r}")
    if package_format == CANONICAL_FORMAT and scope == "all" and not state_sha256:
        raise ValueError(f"{CANONICAL_FORMAT} full exports require state_sha256")
    if "records" in manifest and (not isinstance(manifest["records"], int) or manifest["records"] < 0):
        raise ValueError("manifest.records must be a non-negative integer")
    if "counts" in manifest:
        counts = manifest["counts"]
        if not isinstance(counts, dict) or any(
            not isinstance(key, str) or not isinstance(value, int) or value < 0
            for key, value in counts.items()
        ):
            raise ValueError("manifest.counts must map record types to non-negative integers")
    files = manifest.get("files", [])
    if files is None:
        files = []
    if not isinstance(files, list):
        raise ValueError("manifest.files must be a list")
    if len(files) > Config.IMPORT_MAX_FILES:
        raise ValueError(f"manifest.files exceeds maximum file count: {Config.IMPORT_MAX_FILES}")
    return manifest


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    info = zf.getinfo(ZIP_MANIFEST_NAME)
    if info.file_size > Config.IMPORT_MAX_MANIFEST_BYTES:
        raise ValueError(
            f"manifest.json exceeds maximum size: {Config.IMPORT_MAX_MANIFEST_BYTES} bytes"
        )
    try:
        manifest = json.loads(zf.read(ZIP_MANIFEST_NAME).decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("manifest.json is not valid JSON") from exc
    return _validate_manifest_object(manifest)


def _normalize_durable_state(
    state: Any, manifest: dict[str, Any], *, source_label: str = "state.json"
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(state, dict):
        raise ValueError(f"{source_label} must contain an object")
    allowed = {
        "roles", "settings", "assets", "metrics_daily", "merge_exclusions",
        "resolved_pairs", "quality_decisions", "entity_relations",
        "join_requests", "content_aliases", "source_systems", "source_runs",
        "source_records", "canonical_mappings",
    }
    unexpected = sorted(set(state) - allowed)
    if unexpected:
        raise ValueError(f"{source_label} contains unsupported sections: {', '.join(unexpected)}")
    normalized: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for key in allowed:
        value = state.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"{source_label} section {key!r} must be a list of objects")
        normalized[key] = value
        total += len(value)
    if total > Config.IMPORT_MAX_JSONL_LINES * 5:
        raise ValueError(f"{source_label} contains too many records")
    declared_counts = manifest.get("state_counts")
    actual_counts = {key: len(value) for key, value in normalized.items()}
    if declared_counts is not None:
        if not isinstance(declared_counts, dict) or any(
            not isinstance(key, str) or not isinstance(value, int) or value < 0
            for key, value in declared_counts.items()
        ):
            raise ValueError("manifest.state_counts is invalid")
        if any(actual_counts.get(key, 0) != value for key, value in declared_counts.items()):
            raise ValueError(f"{source_label} counts do not match manifest")
    return normalized


def _read_durable_state(
    zf: zipfile.ZipFile, manifest: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    info = zf.getinfo(ZIP_STATE_NAME)
    if info.file_size > Config.IMPORT_MAX_STATE_BYTES:
        raise ValueError(
            f"state.json exceeds maximum size: {Config.IMPORT_MAX_STATE_BYTES} bytes"
        )
    raw = zf.read(ZIP_STATE_NAME)
    expected_hash = manifest.get("state_sha256")
    if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError(
            "state.json does not match the checksum in manifest.json; "
            "the archive may be incomplete, corrupt, or modified after export"
        )
    try:
        state = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("state.json is not valid JSON") from exc
    return _normalize_durable_state(state, manifest)


def _manifest_asset_paths(manifest: dict[str, Any]) -> set[str]:
    archive_paths: set[str] = set()
    canonical_package = manifest.get("format") == CANONICAL_FORMAT
    for idx, item in enumerate(manifest.get("files") or [], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"manifest.files[{idx}] must be an object")
        archive_path = _validate_asset_archive_path(str(item.get("archive_path") or ""))
        raw_db_path = str(item.get("path") or "").strip()
        if raw_db_path:
            db_path = _validate_manifest_asset_db_path(raw_db_path, idx)
        elif canonical_package:
            raise ValueError(f"manifest.files[{idx}].path is required")
        else:
            # Older portable bundles only carried archive_path. Import them,
            # but all newly generated v2 bundles must declare the DB path.
            db_path = archive_path[len("assets/"):]
        expected_archive_path = db_path if db_path.startswith("assets/") else f"assets/{db_path}"
        if archive_path != expected_archive_path:
            raise ValueError(
                f"manifest.files[{idx}] path does not match archive_path"
            )
        if archive_path in archive_paths:
            raise ValueError(f"manifest.files contains duplicate archive_path: {archive_path}")
        archive_paths.add(archive_path)
        if item.get("size") is not None and (
            not isinstance(item["size"], int) or item["size"] < 0
        ):
            raise ValueError(f"manifest.files[{idx}].size must be a non-negative integer")
        if item.get("sha256") is not None and not _valid_sha256(item["sha256"]):
            raise ValueError(f"manifest.files[{idx}].sha256 must be a SHA-256 digest")
        if canonical_package and (item.get("size") is None or item.get("sha256") is None):
            raise ValueError(
                f"manifest.files[{idx}] requires size and sha256 in {CANONICAL_FORMAT}"
            )
    return archive_paths


def _validate_manifest_asset_db_path(path: str, index: int) -> str:
    value = str(path or "").strip()
    if not value:
        raise ValueError(f"manifest.files[{index}].path is required")
    if "\x00" in value or "\\" in value or value.startswith(("/", "./")):
        raise ValueError(f"manifest.files[{index}].path is unsafe")
    parts = PurePosixPath(value).parts
    if not parts or ":" in parts[0] or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"manifest.files[{index}].path is unsafe")
    if parts[0] == "assets" and len(parts) == 1:
        raise ValueError(f"manifest.files[{index}].path must identify a file")
    return PurePosixPath(*parts).as_posix()


def _valid_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.lower())


def _verify_manifest_assets(zf: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    names = set(zf.namelist())
    for item in manifest.get("files") or []:
        archive_path = str(item["archive_path"])
        if archive_path not in names:
            continue
        info = zf.getinfo(archive_path)
        expected_size = item.get("size")
        if expected_size is not None and info.file_size != expected_size:
            raise ValueError(f"Asset failed size verification: {archive_path}")
        expected_hash = item.get("sha256")
        if not expected_hash:
            continue
        digest = hashlib.sha256()
        with zf.open(info, "r") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise ValueError(f"Asset failed integrity verification: {archive_path}")


def _inspect_records_jsonl(raw: str) -> dict[str, Any]:
    record_types: dict[str, int] = {}
    record_count = 0
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if line_no > Config.IMPORT_MAX_JSONL_LINES:
            raise ValueError(f"records.jsonl exceeds maximum line count: {Config.IMPORT_MAX_JSONL_LINES}")
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"records.jsonl line {line_no} is not valid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"records.jsonl line {line_no} must be an object")
        typ = str(item.get("type") or "").strip()
        if typ not in PORTABLE_TYPES:
            raise ValueError(f"records.jsonl line {line_no} has unsupported type: {typ!r}")
        record_types[typ] = record_types.get(typ, 0) + 1
        record_count += 1
    return {"record_count": record_count, "record_types": record_types}


def _validate_manifest_scope(scope: str, record_types: dict[str, int]) -> None:
    if scope == "all":
        return
    allowed = set(EXPORT_SCOPES[scope]["types"])
    unexpected = sorted(set(record_types) - allowed)
    if unexpected:
        raise ValueError(
            f"ZIP scope {scope!r} contains unsupported record type(s): {', '.join(unexpected)}"
        )

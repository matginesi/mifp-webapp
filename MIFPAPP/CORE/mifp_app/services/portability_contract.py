"""Canonical data-portability contract and human/agent documentation.

This module contains only the stable interchange contract: supported entity
types/scopes, package format identifiers and the generated import guide. It has
no filesystem, database mutation or Flask dependencies.
"""
from __future__ import annotations

import json
from typing import Any

from ..db.migrations import SCHEMA_VERSION
from ..domain import (
    ASSET_KINDS,
    ASSET_LINK_FIELDS,
    ASSET_ROLES,
    ASSET_STORAGE_STATUSES,
    DATE_PRECISIONS,
    ENTITY_DATA_FIELDS as DATA_FIELDS,
    EVENT_TYPES,
    LINK_ROLES,
    NEWS_TYPES,
    PAGE_TYPES,
    REQUIRED_FIELDS,
    REVIEW_STATUSES,
)

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
PORTABLE_FORMAT_VERSION = 2
CANONICAL_FORMAT = "mifp-jsonl-v2"
# Content-only ZIP produced by the local scraper pipeline. It carries canonical
# records and optional asset files, but never installation-owned durable state.
CONTENT_FORMAT = "mifp-content"
CONTENT_FORMAT_VERSION = 1
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
        "date_precision": sorted(DATE_PRECISIONS),
        "event_type": sorted(EVENT_TYPES),
        "news_type": sorted(NEWS_TYPES),
        "page.type": sorted(PAGE_TYPES),
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
        f"The local scraper pipeline produces `{CONTENT_FORMAT}` version {CONTENT_FORMAT_VERSION}: `manifest.json`, "
        "`records.jsonl`, and declared files under `assets/`. It never carries installation-owned durable state. "
        f"Dashboard portable ZIP export uses `{CANONICAL_FORMAT}` version {PORTABLE_FORMAT_VERSION} and may also "
        "contain `state.json` for full-scope installation transfer. Both formats are importable.", "",
        "A dashboard ZIP is intended to be a portable snapshot. Before serializing it, the exporter tries to "
        "materialize every DB-tracked remote asset, regardless of its public source host. The default preservation "
        "matcher is `*`; `PORTABLE_EXPORT_PRESERVE_DOMAINS` may explicitly narrow it for an installation. "
        "This applies only to `assets`: ordinary public URLs stored in `entity_links` remain links and are not "
        "crawled. HTML responses are rejected as assets, so normal web pages are not silently copied into the ZIP. "
        "Staging is export-only and does not rewrite the live database or live asset directory.", "",
        "Each declared asset needs exact byte size and lowercase SHA-256. `records_sha256` hashes the exact UTF-8 "
        "record bytes; a dashboard package that contains state also hashes `state.json`. Paths are relative and "
        "unique, and any mismatch rejects the archive.", "",
        "Agents should not generate `state.json`: it is installation-owned durable state (settings, quality "
        "decisions, relations, provenance, mappings). Dashboard ZIP export may preserve this state, but it is not "
        "a byte-for-byte SQLite backup. For new content with local assets use the scraper artifact assembler or "
        "deterministic packaging code, never LLM-generated checksums.", "",
        "## Import scopes", "",
        "The selected dashboard scope must match the content being imported. A record-only JSONL may "
        "contain only types allowed by that scope. A packaged ZIP declares its scope in the manifest. "
        "| Scope | Allowed record types |", "| --- | --- |",
        "| `members` | `member` |", "| `news` | `news` |", "| `events` | `event` |",
        "| `publications` | `publication` |", "| `research` | `research_area` |",
        "| `sponsors` | `sponsor` |", "| `all` | every supported type, including `page` |", "",
        "If a generated dataset contains more than one type, instruct the operator to select `all`.", "",
        "## Exact ZIP manifest contract", "",
        "This content-package example shows shape only. A deterministic program must replace counts, timestamps, "
        "sizes, and digests after serializing the final byte streams. Do not copy placeholder hashes.", "",
        "```json", compact({
            "format": CONTENT_FORMAT, "format_version": CONTENT_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION, "generated_at": "2026-08-17T12:00:00Z",
            "scope": "news", "records": 1,
            "records_sha256": "<64 lowercase hexadecimal characters>",
            "counts": {"news": 1},
            "files": [{
                "path": "news/example/image.jpg", "archive_path": "assets/news/example/image.jpg",
                "size": 12345, "sha256": "<64 lowercase hexadecimal characters>",
            }],
        }), "```", "",
        "Manifest invariants:", "",
        "- `format`, `format_version`, `scope`, `records`, `records_sha256`, `counts`, and `files` are required for modern content packages.",
        "- `records` equals the number of non-empty record lines; `counts` exactly groups them by singular type.",
        "- `path` is the database-relative asset path. `archive_path` contains exactly one `assets/` prefix: if `path` already starts with `assets/`, it is unchanged; otherwise `assets/` is prepended.",
        "- Every archive asset is declared exactly once; no undeclared asset or unsupported extra file is allowed.",
        "- An `all` dashboard portable ZIP also requires `state.json`, `state_sha256`, and `state_counts`.",
        "- Hash exact bytes, not parsed/reformatted JSON. Repacking or pretty-printing after hashing invalidates the package.", "",
        "### Dashboard portable asset-preservation metadata", "",
        f"New `{CANONICAL_FORMAT}` version {PORTABLE_FORMAT_VERSION} exports add a `preservation` object to `manifest.json`. "
        "This object is additive metadata: older valid v2 dashboard ZIPs that do not contain it remain importable.", "",
        "```json", compact({
            "preservation": {
                "enabled": True, "domains": ["*"], "matching": 12,
                "already_local": 4, "attempted": 8, "materialized": 8,
                "failed": 0, "remaining_remote": 0, "complete": True, "failures": [],
            }
        }), "```", "",
        "`complete: true` means that every DB-tracked asset matching the configured preservation matcher is "
        "packageable from the ZIP snapshot. With the default `*`, this means all remote assets that the exporter "
        "could safely retrieve. It does **not** mean that every URL on the site has been mirrored: `entity_links` "
        "stay as links. If `remaining_remote` is non-zero, the export still completes and `failures` contains "
        "bounded diagnostics for assets that could not be embedded.", "",
        "## Dashboard JSONL export", "",
        "Dashboard JSONL export is deliberately record-only: one canonical record per line, with no Base64 "
        "binary payloads and no installation-owned durable state. This keeps JSONL suitable for inspection, "
        "versioning and data pipelines. Use ZIP when local assets or durable dashboard state must travel with "
        "the records.", "",
        "## Durable state: reserved for portable ZIP transfers", "",
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
        "- Actual import creates a pre-import database backup. Asset/network failures may be reported separately from record errors.",
        f"- Re-importing only validated `{CANONICAL_FORMAT}` dashboard ZIPs is an offline/deterministic restore: packaged assets are restored from the archive and the post-import network recovery pass is skipped.",
        f"- Record-only JSONL and `{CONTENT_FORMAT}` scraper packages may still use normal asset recovery for unresolved remote files; they are content-ingest formats, not guaranteed offline snapshots.",
        "- A canonical dashboard asset path may be `assets/<kind>/<file>` or an older relative `<kind>/<file>` path; import resolves both without creating a second `assets/assets/` level.", "",
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
        "Unless explicitly asked for a portable ZIP package, produce record-only JSONL and do not create state.json,",
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

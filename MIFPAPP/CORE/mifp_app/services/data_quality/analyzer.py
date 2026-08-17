from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from typing import Any

from ..assets import resolve_db_asset_path
from .cluster import cluster_is_safe
from .models import ActionType, Classification, Evidence, Finding
from .normalizers import (
    aggregate_markers,
    classify_url,
    clean_boilerplate,
    comparison_text,
    content_fingerprint,
    normalize_url,
    person_name,
    split_aggregate_segments,
    stable_fingerprint,
    tokens,
    years,
)
from .planner import LABELS, TABLES, build_clean_plan, build_merge_plan, build_split_plan
from .policies import POLICIES

Progress = Callable[[int, int, str, str], None]


def database_fingerprint(conn: sqlite3.Connection) -> str:
    parts = []
    for table in [*TABLES.values(), "assets", "asset_links", "entity_links", "entity_relations", "import_records"]:
        columns = {column["name"] for column in conn.execute(f'PRAGMA table_info("{table}")')}
        updated = 'COALESCE(MAX(updated_at),"")' if "updated_at" in columns else '""'
        row = conn.execute(f'SELECT COUNT(*),COALESCE(MAX(id),0),{updated} FROM "{table}"').fetchone()
        parts.append([table, *row])
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()


def _context(conn: sqlite3.Connection, entity_type: str) -> dict:
    links: dict[int, list[dict]] = defaultdict(list)
    for row in conn.execute("SELECT * FROM entity_links WHERE entity_type=?", (entity_type,)):
        links[int(row["entity_id"])].append(dict(row))
    assets: dict[int, list[dict]] = defaultdict(list)
    for row in conn.execute(
        "SELECT al.*,a.checksum,a.path,a.source_url FROM asset_links al JOIN assets a ON a.id=al.asset_id WHERE al.entity_type=?",
        (entity_type,),
    ):
        assets[int(row["entity_id"])].append(dict(row))
    imports: dict[int, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT entity_id,content_hash FROM import_records WHERE entity_type=? AND content_hash IS NOT NULL",
        (entity_type,),
    ):
        content_hash = str(row["content_hash"] or "").strip()
        if content_hash:
            imports[int(row["entity_id"])].add(content_hash)
    return {"links": links, "assets": assets, "import_hashes": imports}


def _invalid_findings(entity_type: str, rows: list[dict]) -> list[Finding]:
    findings: list[Finding] = []
    invalid_titles = {"authorization required", "http test event", "xhr event"}
    for row in rows:
        if str(row.get("review_status") or "") in {"quarantined", "archived", "duplicate"}:
            continue
        label = comparison_text(row.get(LABELS[entity_type]))
        if entity_type == "event" and (label in invalid_titles or label.startswith("404 view not found")):
            evidence = [Evidence("invalid_or_test_record", "strong", "Technical response or explicit test record", [row.get("title")])]
            plan: dict[str, Any] = {
                "action_type": "clean_record", "entity_type": entity_type, "record_ids": [row["id"]],
                "operation": "quarantine", "requires_review": False,
                "previous_review_status": str(row.get("review_status") or "draft"),
                "source_fingerprint": stable_fingerprint(entity_type, [row], action="invalid_record"),
                "source_state_fingerprint": stable_fingerprint(entity_type, [row]),
            }
            findings.append(Finding(ActionType.CLEAN, entity_type, [row["id"]], Classification.INVALID, evidence, [], plan, plan["source_fingerprint"]))
        if entity_type == "news" and label in {"news", "xhr news"}:
            source = str(row.get("summary") or row.get("body") or "")
            source = re.sub(r"<[^>]+>", " ", source)
            source = re.sub(r"\s+", " ", source).strip()
            candidate = re.split(r"(?<=[.!?])\s+|\n", source, maxsplit=1)[0].strip(" -–—:;")
            usable = 12 <= len(candidate) <= 180 and len(tokens(candidate)) >= 3
            evidence = [Evidence("placeholder_title", "strong", "Generic title does not identify the article", [row.get("title")])]
            plan = {
                "action_type": "clean_record", "entity_type": entity_type, "record_ids": [row["id"]],
                "operation": "derive_title_or_review" if usable else "quarantine",
                "requires_review": False,
                "previous_review_status": str(row.get("review_status") or "draft"),
                "fields": [{
                    "field": "title",
                    "values_by_record": [{"record_id": row["id"], "value": row.get("title")}],
                    "proposed_value": candidate if usable else None,
                    "action": "derive_from_content" if usable else "manual_edit_required",
                    "reason": "Use the first informative content sentence as the headline." if usable else "No informative content is available to derive a headline.",
                    "confidence": "high" if usable else "review",
                    "requires_review": not usable,
                    "losses": [],
                }] if usable else [],
                "source_fingerprint": stable_fingerprint(entity_type, [row], action="placeholder_title"),
                "source_state_fingerprint": stable_fingerprint(entity_type, [row]),
            }
            classification = Classification.CLEANING if usable else Classification.INVALID
            findings.append(Finding(ActionType.CLEAN, entity_type, [row["id"]], classification, evidence, [], plan, plan["source_fingerprint"], .9))
    return findings


def _legacy_merged_duplicate_findings(
    conn: sqlite3.Connection,
    entity_type: str,
    rows: list[dict],
) -> list[Finding]:
    """Safely remove rows retained by the pre-delete merge implementation."""
    findings: list[Finding] = []
    table = TABLES[entity_type]
    for row in rows:
        if str(row.get("review_status") or "") != "duplicate":
            continue
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        alias = conn.execute(
            """
            SELECT canonical_entity_id
            FROM content_aliases
            WHERE entity_type=? AND old_slug=?
            """,
            (entity_type, slug),
        ).fetchone()
        if not alias:
            continue
        canonical_id = int(alias["canonical_entity_id"])
        if canonical_id == int(row["id"]) or not conn.execute(
            f'SELECT 1 FROM "{table}" WHERE id=?', (canonical_id,)
        ).fetchone():
            continue
        plan: dict[str, Any] = {
            "action_type": "clean_record",
            "entity_type": entity_type,
            "record_ids": [int(row["id"])],
            "canonical_id": canonical_id,
            "operation": "remove_merged_duplicate",
            "requires_review": False,
            "source_fingerprint": stable_fingerprint(
                entity_type, [row], action="remove_merged_duplicate"
            ),
            "source_state_fingerprint": stable_fingerprint(entity_type, [row]),
        }
        findings.append(Finding(
            ActionType.CLEAN,
            entity_type,
            [int(row["id"])],
            Classification.CLEANING,
            [Evidence(
                "legacy_merged_duplicate",
                "deterministic",
                "A canonical alias proves this retained row was already merged",
                [slug, canonical_id],
            )],
            [],
            plan,
            plan["source_fingerprint"],
            1,
        ))
    return findings


def _check_name_inversion(row: dict) -> Finding | None:
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    if not first or not last:
        return None
    # Only flag an inversion when display_name actually agrees with the reversed
    # field order. Substring checks break legitimate surnames such as D'Andrea.
    display = comparison_text(row.get("display_name"))
    normal_order = comparison_text(f"{first} {last}")
    reversed_order = comparison_text(f"{last} {first}")
    if display and reversed_order != normal_order and display == reversed_order:
        evidence = [Evidence(
            "name_inversion", "strong",
            "display_name matches last-name/first-name order",
            [first, last, row.get("display_name")],
        )]
        plan: dict[str, Any] = {
            "action_type": "clean_record", "entity_type": "member", "record_ids": [row["id"]],
            "operation": "swap_name_fields", "requires_review": True,
            "first_name": first, "last_name": last,
            "source_fingerprint": stable_fingerprint("member", [row], action="name_inversion"),
            "source_state_fingerprint": stable_fingerprint("member", [row]),
        }
        return Finding(ActionType.CLEAN, "member", [row["id"]], Classification.CLEANING, evidence, [], plan, plan["source_fingerprint"], .95)
    return None


def _check_event_date_inversion(row: dict) -> Finding | None:
    start = str(row.get("start_date") or "").strip()
    end = str(row.get("end_date") or "").strip()
    if start and end and start > end:
        evidence = [Evidence("inverted_date_range", "strong", "Start date is after end date", [start, end])]
        plan: dict[str, Any] = {
            "action_type": "clean_record", "entity_type": "event", "record_ids": [row["id"]],
            "fields": [{"field": "end_date", "proposed_value": None, "action": "replace_with_cleaned", "requires_review": True, "reason": f"End date {end} precedes start {start}."}],
            "source_fingerprint": stable_fingerprint("event", [row], action="inverted_date_range"),
            "source_state_fingerprint": stable_fingerprint("event", [row]),
        }
        return Finding(ActionType.CLEAN, "event", [row["id"]], Classification.CLEANING, evidence, [], plan, plan["source_fingerprint"], 1)
    return None


def _check_date_placeholder(row: dict) -> Finding | None:
    start = str(row.get("start_date") or "")
    end = str(row.get("end_date") or "")
    precision = str(row.get("date_precision") or "")
    if precision == "range" and start.endswith("-01-01") and end.endswith("-12-31") and start[:4] == end[:4]:
        year = start[:4]
        evidence = [Evidence(
            "false_annual_range", "strong",
            f"Date range {start} to {end} encodes a year ({year}), not a proven range",
            [start, end],
        )]
        plan: dict[str, Any] = {
            "action_type": "clean_record",
            "entity_type": "event",
            "record_ids": [row["id"]],
            "fields": [
                {"field": "end_date", "proposed_value": None,
                 "action": "clear_value", "requires_review": False,
                 "reason": "A synthetic 31 December end date is not evidence of a year-long event."},
                {"field": "date_precision", "proposed_value": "year",
                 "action": "replace_with_cleaned", "requires_review": False,
                 "reason": "Only the year is known."},
                {"field": "date_text", "proposed_value": year,
                 "action": "replace_with_cleaned", "requires_review": False,
                 "reason": "Preserve the known year."},
            ],
            "source_fingerprint": stable_fingerprint("event", [row], action="date_placeholder"),
            "source_state_fingerprint": stable_fingerprint("event", [row]),
        }
        return Finding(
            ActionType.CLEAN, "event", [row["id"]],
            Classification.CLEANING, evidence, [], plan,
            plan["source_fingerprint"], 0.95,
        )
    return None


def _check_event_missing_dates(row: dict) -> Finding | None:
    start = str(row.get("start_date") or "").strip()
    end = str(row.get("end_date") or "").strip()
    # ``end_date`` is optional for normal single-day events. Flagging every
    # event with only a start date created thousands of non-actionable findings.
    if start:
        return None
    missing_fields = ["start_date"] if end else ["start_date", "end_date"]
    if not start and not end:
        score = .95
        strength = "critical"
        explanation = "Event has no start or end date"
    elif not start:
        score = .9
        strength = "strong"
        explanation = "Event has no start date"
    else:
        score = .65
        strength = "supporting"
        explanation = "Event has no end date"
    evidence = [Evidence("missing_event_date", strength, explanation, missing_fields)]
    plan: dict[str, Any] = {
        "action_type": "clean_record", "entity_type": "event", "record_ids": [row["id"]],
        "fields": [{"field": name, "proposed_value": None, "action": "fill_missing", "requires_review": True, "reason": f"{name} is empty."} for name in missing_fields],
        "source_fingerprint": stable_fingerprint("event", [row], action="missing_date"),
        "source_state_fingerprint": stable_fingerprint("event", [row]),
    }
    return Finding(ActionType.CLEAN, "event", [row["id"]], Classification.CLEANING, evidence, [], plan, plan["source_fingerprint"], score)


_FRAGMENT_KEYWORDS = frozenset({
    "topics", "fees", "program", "gallery", "registration",
    "committees", "speakers", "call for papers", "venue",
    "accommodation", "sponsors", "support", "proceedings",
    "template", "downloads", "important dates", "scope",
    "invited speakers", "commitee", "programme",
    "photo gallery", "travel", "visa", "submission",
})


def _check_event_page_fragment(row: dict, context: dict) -> Finding | None:
    title = str(row.get("title") or "")
    title_lower = comparison_text(title)
    matches = [kw for kw in _FRAGMENT_KEYWORDS if kw in title_lower]
    if not matches:
        return None
    evidence_words = sorted(matches)
    evidence = [Evidence(
        "event_page_fragment", "strong",
        f"Title contains fragment keywords ({', '.join(evidence_words)}), "
        f"suggesting this is a subpage of a larger event, not a standalone event",
        [title],
    )]
    plan: dict[str, Any] = {
        "action_type": "merge_records",
        "entity_type": "event",
        "record_ids": [row["id"]],
        "operation": "absorb_fragment",
        "requires_review": True,
        "proposed_parent_hint": None,
        "fragment_keywords": evidence_words,
        "source_fingerprint": stable_fingerprint("event", [row], action="page_fragment"),
        "source_state_fingerprint": stable_fingerprint("event", [row]),
    }
    return Finding(
        ActionType.MERGE, "event", [row["id"]],
        Classification.FRAGMENT, evidence, [], plan,
        plan["source_fingerprint"], 0.88,
    )


def _check_aggregated_event(row: dict) -> Finding | None:
    title = str(row.get("title") or "")
    if re.search(r"\s*\|\s*[A-Z]", title):
        parts = [part.strip() for part in title.split("|")]
        informative = [part for part in parts if len(tokens(part)) >= 3 and len(part) >= 18]
        # A pipe before/after an acronym (e.g. "... | ICP2DC5") is a title
        # separator, not evidence that multiple event records were aggregated.
        if len(parts) >= 2 and len(informative) >= 2:
            evidence = [Evidence("pipe_separated_title", "strong", "Title contains pipe-delimited segments suggesting multiple entries", parts)]
            plan: dict[str, Any] = {
                "action_type": "split_aggregated_record", "entity_type": "event", "record_ids": [row["id"]],
                "source_record": row,
                "proposed_records": [{"segment": part, "title_hint": part[:240]} for part in parts],
                "requires_review": True,
                "source_fingerprint": stable_fingerprint("event", [row], action="aggregated_event"),
                "source_state_fingerprint": stable_fingerprint("event", [row]),
            }
            return Finding(ActionType.SPLIT, "event", [row["id"]], Classification.AGGREGATED, evidence, [], plan, plan["source_fingerprint"], .9)
    return None


_JUNK_TITLE_PATTERNS = [
    re.compile(r"^\d{1,3}$"),  # pure short numbers
    re.compile(r"^\d+\s*(?:MB|KB|GB|bytes?)$", re.I),  # file sizes
    re.compile(r"^(?:page|file|document|download)\s*\d*$", re.I),
    re.compile(r"^[a-z]+[\da-f]{4,}$", re.I),  # hex-ish page IDs
    re.compile(r"^[a-z]*\d+[a-z]+$", re.I),  # mixed letter-number junk
    re.compile(r"^(?:publications?|archive|news)\s*\d*\s*$", re.I),
    re.compile(r"^\s*$"),
]


def _check_junk_record(entity_type: str, row: dict) -> Finding | None:
    label_field = LABELS.get(entity_type, "title")
    # Sponsors use is_active rather than review_status and therefore cannot be
    # placed in the reversible editorial quarantine.
    if entity_type not in TABLES or entity_type == "sponsor":
        return None
    label = str(row.get(label_field) or "")
    for pattern in _JUNK_TITLE_PATTERNS:
        if pattern.match(label.strip()):
            evidence = [Evidence(
                "junk_technical_title", "deterministic",
                f"Title '{label}' appears to be a technical identifier, not a real entity name",
                [label],
            )]
            plan: dict[str, Any] = {
                "action_type": "clean_record",
                "entity_type": entity_type,
                "record_ids": [row["id"]],
                "operation": "quarantine",
                "requires_review": False,
                "previous_review_status": str(row.get("review_status") or "draft"),
                "source_fingerprint": stable_fingerprint(entity_type, [row], action="junk_record"),
                "source_state_fingerprint": stable_fingerprint(entity_type, [row]),
            }
            return Finding(
                ActionType.CLEAN, entity_type, [row["id"]],
                Classification.JUNK, evidence, [], plan,
                plan["source_fingerprint"], 1,
            )
    return None


def _quality_findings(entity_type: str, rows: list[dict], context: dict | None = None) -> list[Finding]:
    output = _invalid_findings(entity_type, rows)
    text_fields = {
        "event": ("description",), "news": ("summary", "body"),
        "publication": ("authors", "abstract"), "member": ("bio",), "sponsor": ("description",),
        "research_area": ("summary", "description"), "page": ("summary", "body"),
    }[entity_type]
    for row in rows:
        junk = _check_junk_record(entity_type, row)
        if junk:
            output.append(junk)
            continue
        changes, removed = {}, {}
        for field in text_fields:
            cleaned, discarded = clean_boilerplate(row.get(field))
            if discarded and cleaned != str(row.get(field) or "").strip():
                changes[field], removed[field] = cleaned, discarded
        if entity_type == "publication":
            markers = aggregate_markers(row.get("abstract")) + aggregate_markers(row.get("authors"))
            segments = split_aggregate_segments(row.get("abstract"))
            if markers and len(segments) >= 2:
                plan = build_split_plan(row, segments)
                output.append(Finding(
                    ActionType.SPLIT, entity_type, [row["id"]], Classification.AGGREGATED,
                    [Evidence("multiple_publication_segments", "strong", "Archive markers and multiple content segments were detected", markers)],
                    [], plan, plan["source_fingerprint"], .95,
                ))
                continue
        if changes:
            plan = build_clean_plan(entity_type, row, changes, removed)
            output.append(Finding(
                ActionType.CLEAN, entity_type, [row["id"]], Classification.CLEANING,
                [Evidence("scraper_boilerplate", "strong", "Technical page fragments can be removed by segment", sum(removed.values(), []))],
                [], plan, plan["source_fingerprint"], .9,
            ))
        if entity_type == "event":
            placeholder = _check_date_placeholder(row)
            if placeholder:
                output.append(placeholder)
            else:
                inv = _check_event_date_inversion(row)
                if inv:
                    output.append(inv)
                md = _check_event_missing_dates(row)
                if md:
                    output.append(md)
                agg = _check_aggregated_event(row)
                if agg:
                    output.append(agg)
                frag = _check_event_page_fragment(row, context or {})
                if frag:
                    output.append(frag)
        if entity_type == "member":
            inv_name = _check_name_inversion(row)
            if inv_name:
                output.append(inv_name)
    return output


_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "it", "its", "be", "are", "was",
    "were", "been", "has", "have", "had", "not", "no", "so", "if", "about",
    "into", "over", "after", "before", "between", "under", "above", "below",
    "this", "that", "these", "those", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "than", "then", "just", "also",
    "very", "too", "can", "will", "may", "news", "mifp", "www", "com", "org",
})


def _blocks(entity_type: str, rows: list[dict], context: dict | None = None) -> set[tuple[int, int]]:
    buckets: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        label = row.get(LABELS[entity_type])
        if entity_type == "member":
            name = person_name(label)
            if len(name.normal) >= 2:
                buckets["name:" + "|".join(sorted(name.normal))].add(row["id"])
            email = comparison_text(row.get("email"))
            if email:
                buckets["email:" + email].add(row["id"])
        elif entity_type == "event":
            words = set(tokens(label)) - {str(y) for y in years(label)} - _STOP_WORDS
            word_key = " ".join(sorted(words))
            if len(word_key) >= 8:
                buckets["event:" + word_key[:100]].add(row["id"])
            if context:
                for link in context.get("links", {}).get(row["id"], []):
                    url = normalize_url(link["url"])
                    if classify_url(url) == "entity_detail":
                        buckets["event-url:" + url].add(row["id"])
        elif entity_type == "publication":
            doi = comparison_text(row.get("doi"))
            title = " ".join(tokens(label)[:8])
            if doi:
                buckets["doi:" + doi].add(row["id"])
            if len(title) >= 12:
                buckets["title:" + title].add(row["id"])
            authors = row.get("authors")
            if authors:
                author_key = " ".join(tokens(str(authors))[:3])
                if len(author_key) >= 6:
                    buckets["pub-author:" + author_key].add(row["id"])
        else:
            meaningful = [word for word in tokens(label) if len(word) >= 4 and word not in _STOP_WORDS]
            for word in meaningful[:4]:
                buckets["word:" + word].add(row["id"])
            if entity_type == "news":
                # Headlines can legitimately differ while the article text is the
                # strongest identity signal. Content tokens provide bounded
                # candidate generation without falling back to an O(n²) scan.
                content = " ".join(
                    str(row.get(field) or "") for field in ("summary", "body")
                )
                content_words = [
                    word for word in tokens(content)
                    if len(word) >= 5 and word not in _STOP_WORDS
                ]
                for word in content_words[:6]:
                    buckets["news-content:" + word].add(row["id"])
                for asset in (context or {}).get("assets", {}).get(row["id"], []):
                    if asset.get("checksum"):
                        buckets["news-asset:" + str(asset["checksum"])].add(row["id"])
            elif entity_type == "sponsor":
                if context:
                    for link in context.get("links", {}).get(row["id"], []):
                        url = normalize_url(link["url"])
                        if classify_url(url) == "entity_detail":
                            buckets["sponsor-url:" + url].add(row["id"])
    pairs: set[tuple[int, int]] = set()
    for ids in buckets.values():
        if 1 < len(ids) <= 40:
            pairs.update((a, b) if a < b else (b, a) for a, b in combinations(ids, 2))
    return pairs


def _forced_reimport_clone(
    entity_type: str,
    left: dict,
    right: dict,
) -> Evidence | None:
    """Return deterministic evidence for copies produced by Force reimport."""
    slug_left = str(left.get("slug") or "").strip().casefold()
    slug_right = str(right.get("slug") or "").strip().casefold()
    if not slug_left or not slug_right or slug_left == slug_right:
        return None

    def base(value: str) -> str:
        match = re.fullmatch(r"(.+)-([2-9]\d*)", value)
        return match.group(1) if match else value

    if base(slug_left) != base(slug_right):
        return None
    if base(slug_left) == slug_left and base(slug_right) == slug_right:
        return None

    ignored = {
        "id", "uid", "slug", "created_at", "updated_at",
        # Parent IDs are local database references and are intentionally
        # resolved separately during imports.
        "parent_event_id",
    }
    fields = (set(left) | set(right)) - ignored
    if any(left.get(field) != right.get(field) for field in fields):
        return None
    label = LABELS[entity_type]
    if not comparison_text(left.get(label)):
        return None
    return Evidence(
        "forced_reimport_clone",
        "deterministic",
        "Same imported payload with a Force reimport slug suffix",
        [slug_left, slug_right],
    )


def _save_progress(conn: sqlite3.Connection, run_id: int, pct: int, message: str) -> None:
    try:
        conn.execute("UPDATE quality_runs SET progress_pct=?,progress_message=? WHERE id=?", (pct, message[:200], run_id))
        conn.commit()
    except Exception:
        pass


def analyze(
    conn: sqlite3.Connection,
    run_id: int | None = None,
    progress: Progress | None = None,
    *,
    assets_dir: Path | None = None,
) -> dict:
    started = time.monotonic()
    fingerprint = database_fingerprint(conn)
    if run_id is None:
        run_id = conn.execute("INSERT INTO quality_runs(status,fingerprint) VALUES('running',?)", (fingerprint,)).lastrowid or 0
        conn.commit()
    all_findings: list[Finding] = []
    pair_count = 0
    total_entities = len(TABLES)
    try:
        for index, entity_type in enumerate(TABLES, start=1):
            if progress:
                progress(index - 1, len(TABLES) + 2, "quality", f"Checking {entity_type} records")
            pct = int((index - 1) * 80 / total_entities)
            _save_progress(conn, run_id, pct, f"Scanning {entity_type} records\u2026")
            rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{TABLES[entity_type]}" ORDER BY id')]
            entity_findings_start = len(all_findings)
            all_findings.extend(
                _legacy_merged_duplicate_findings(conn, entity_type, rows)
            )
            rows = [row for row in rows if str(row.get("review_status") or "") not in {"quarantined", "archived", "duplicate"}]
            context = _context(conn, entity_type)
            all_findings.extend(_quality_findings(entity_type, rows, context))
            by_id = {row["id"]: row for row in rows}
            pairs = sorted(_blocks(entity_type, rows, context))
            msg_base = f"Comparing {entity_type} records"
            for pair_idx, (left_id, right_id) in enumerate(pairs):
                if pair_idx % 50 == 0:
                    pct = int((index - 1) * 80 / total_entities + pair_idx * 80 / total_entities / max(len(pairs), 1))
                    _save_progress(conn, run_id, min(pct, 85), f"{msg_base} ({pair_idx}/{len(pairs)})")
                pair_count += 1
                records = [by_id[left_id], by_id[right_id]]
                forced_clone = _forced_reimport_clone(
                    entity_type, records[0], records[1]
                )
                if forced_clone:
                    classification: Classification = Classification.EXACT
                    score: float = 1
                    evidence: list[Evidence] = [forced_clone]
                    contradictions: list[Evidence] = []
                else:
                    classification, score, evidence, contradictions = POLICIES[entity_type](records[0], records[1], context)
                if (
                    classification == Classification.BLOCKED
                    and any(item.code == "insufficient_identity" for item in contradictions)
                ):
                    # A generic title is already reported once as a record-quality
                    # issue. Emitting every possible pair creates quadratic noise
                    # and gives the administrator nothing actionable.
                    continue
                if classification == Classification.RELATED:
                    continue
                plan = build_merge_plan(conn, entity_type, records)
                action = ActionType.MERGE
                exclusion = conn.execute(
                    "SELECT decision FROM merge_exclusions WHERE entity_type=? AND record_fingerprint=?",
                    (entity_type, plan["source_fingerprint"]),
                ).fetchone()
                if exclusion:
                    classification = Classification.KEEP_SEPARATE
                fp_a = content_fingerprint(records[0])
                fp_b = content_fingerprint(records[1])
                pair_key = tuple(sorted([fp_a, fp_b]))
                resolved = conn.execute(
                    "SELECT 1 FROM resolved_pairs WHERE entity_type=? AND left_fingerprint=? AND right_fingerprint=?",
                    (entity_type, pair_key[0], pair_key[1]),
                ).fetchone()
                if resolved:
                    continue
                all_findings.append(Finding(action, entity_type, [left_id, right_id], classification, evidence, contradictions, plan, plan["source_fingerprint"], score))
            if entity_type == "member":
                # When an inverted display name belongs to a viable duplicate
                # pair, merging is the complete fix. Keeping a competing
                # single-record cleanup would leave both member rows in place
                # and creates overlapping queue actions.
                member_findings = all_findings[entity_findings_start:]
                merge_ids = {
                    int(record_id)
                    for finding in member_findings
                    if finding.action_type == ActionType.MERGE
                    and finding.classification in {Classification.EXACT, Classification.STRONG}
                    for record_id in finding.record_ids
                }
                all_findings[entity_findings_start:] = [
                    finding for finding in member_findings
                    if not (
                        finding.action_type == ActionType.CLEAN
                        and finding.plan.get("operation") == "swap_name_fields"
                        and any(int(record_id) in merge_ids for record_id in finding.record_ids)
                    )
                ]
        all_findings = _consolidate_exact_groups(conn, all_findings)
        if progress:
            progress(len(TABLES), len(TABLES) + 2, "relations", "Checking links and assets")
        all_findings.extend(_relation_findings(conn, assets_dir))
        persisted_findings: list[Finding] = []
        for finding in all_findings:
            prior = conn.execute(
                """SELECT 1 FROM quality_findings
                   WHERE fingerprint=? AND status IN ('rejected','resolved','deferred') AND run_id<>?
                   LIMIT 1""",
                (finding.fingerprint, run_id),
            ).fetchone()
            if prior:
                continue
            persisted_findings.append(finding)
            conn.execute(
                """INSERT OR IGNORE INTO quality_findings(
                       run_id,action_type,entity_type,record_ids_json,classification,score,
                       evidence_json,contradictions_json,plan_json,fingerprint
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, finding.action_type.value, finding.entity_type, json.dumps(finding.record_ids),
                    finding.classification.value, finding.score,
                    json.dumps([item.__dict__ for item in finding.evidence], default=str),
                    json.dumps([item.__dict__ for item in finding.contradictions], default=str),
                    json.dumps(finding.plan, default=str), finding.fingerprint,
                ),
            )
        counts = Counter(f.action_type.value for f in persisted_findings)
        classes = Counter(f.classification.value for f in persisted_findings)
        duration = int((time.monotonic() - started) * 1000)
        summary = {"actions": counts, "classifications": classes, "records": sum(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in TABLES.values()), "pairs": pair_count}
        conn.execute("UPDATE quality_runs SET status='completed',completed_at=CURRENT_TIMESTAMP,duration_ms=?,summary_json=? WHERE id=?", (duration, json.dumps(summary), run_id))
        conn.commit()
        if progress:
            progress(len(TABLES) + 2, len(TABLES) + 2, "complete", "Analysis complete")
        return {"run_id": run_id, "duration_ms": duration, "summary": summary, "finding_count": len(persisted_findings)}
    except Exception as exc:
        conn.execute("UPDATE quality_runs SET status='failed',completed_at=CURRENT_TIMESTAMP,error_message=? WHERE id=?", (str(exc)[:1000], run_id))
        conn.commit()
        raise


def _consolidate_exact_groups(
    conn: sqlite3.Connection,
    findings: list[Finding],
) -> list[Finding]:
    """Collapse deterministic pair matches into one actionable entity group."""
    exact = [
        finding for finding in findings
        if finding.action_type == ActionType.MERGE
        and finding.classification == Classification.EXACT
    ]
    if not exact:
        return findings

    parents: dict[tuple[str, int], tuple[str, int]] = {}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def union(left: tuple[str, int], right: tuple[str, int]) -> None:
        a, b = find(left), find(right)
        if a != b:
            parents[max(a, b)] = min(a, b)

    for finding in exact:
        nodes = [(finding.entity_type, int(value)) for value in finding.record_ids]
        for node in nodes[1:]:
            union(nodes[0], node)

    components: dict[tuple[str, int], set[int]] = defaultdict(set)
    for entity_type, record_id in parents:
        components[find((entity_type, record_id))].add(record_id)

    grouped_ids = {
        (entity_type, frozenset(record_ids))
        for (entity_type, _), record_ids in components.items()
        if len(record_ids) >= 2
    }
    consumed: set[int] = set()
    output: list[Finding] = []
    for entity_type, record_ids in sorted(grouped_ids, key=lambda item: (item[0], sorted(item[1]))):
        members = [
            finding for finding in exact
            if finding.entity_type == entity_type
            and set(finding.record_ids) <= set(record_ids)
        ]
        consumed.update(id(finding) for finding in members)
        records = records_for_group = [
            dict(row) for row in conn.execute(
                f'SELECT * FROM "{TABLES[entity_type]}" WHERE id IN ({",".join("?" for _ in record_ids)}) ORDER BY id',
                sorted(record_ids),
            )
        ]
        if len(records_for_group) != len(record_ids):
            continue
        plan = build_merge_plan(conn, entity_type, records)
        evidence_by_code: dict[str, Evidence] = {}
        for finding in members:
            for item in finding.evidence:
                evidence_by_code.setdefault(item.code, item)
        evidence = list(evidence_by_code.values())
        evidence.append(Evidence(
            "deterministic_identity_group",
            "deterministic",
            f"{len(record_ids)} records are connected only by deterministic identity signals",
            sorted(record_ids),
        ))
        safe, reasons, _subclusters = cluster_is_safe(records, entity_type, {})
        if not safe:
            contradictions = []
            for reason in reasons:
                contradictions.append(Evidence("cluster_unsafe", "blocking", reason, []))
            output.append(Finding(
                ActionType.MERGE,
                entity_type,
                sorted(record_ids),
                Classification.AMBIGUOUS,
                evidence,
                contradictions,
                plan,
                plan["source_fingerprint"],
                0.5,
            ))
            continue
        output.append(Finding(
            ActionType.MERGE,
            entity_type,
            sorted(record_ids),
            Classification.EXACT,
            evidence,
            [],
            plan,
            plan["source_fingerprint"],
            1,
        ))

    output.extend(finding for finding in findings if id(finding) not in consumed)
    return output


def _relation_findings(conn: sqlite3.Connection, assets_dir: Path | None = None) -> list[Finding]:
    output: list[Finding] = []
    for row in conn.execute(
        """SELECT entity_type,entity_id,COUNT(*) amount,GROUP_CONCAT(id) ids
           FROM entity_links WHERE is_primary=1 GROUP BY entity_type,entity_id HAVING COUNT(*)>1"""
    ):
        records = [{"id": int(row["entity_id"]), "updated_at": None, "slug": None, "title": None}]
        fingerprint = stable_fingerprint(str(row["entity_type"]), records, action="multiple_primary_links")
        plan = {"action_type": "repair_relations_or_assets", "entity_type": row["entity_type"], "record_ids": [row["entity_id"]], "operation": "deduplicate_primary_links", "link_ids": [int(x) for x in row["ids"].split(",")], "source_fingerprint": fingerprint, "source_state_fingerprint": stable_fingerprint(str(row["entity_type"]), records)}
        output.append(Finding(ActionType.REPAIR, row["entity_type"], [row["entity_id"]], Classification.CLEANING, [Evidence("multiple_primary_links", "strong", "An entity has more than one primary link", [row["amount"]])], [], plan, fingerprint, 1))
    if assets_dir is not None:
        root = Path(assets_dir)
        for asset_row in conn.execute("SELECT * FROM assets WHERE COALESCE(path,'')<>'' ORDER BY id"):
            asset = dict(asset_row)
            # Only locally-managed assets are expected to exist below ASSETS_DIR.
            # Rows intentionally marked external/missing are handled by the asset
            # recovery workflow and must not become Data Quality human decisions.
            if bool(asset.get("is_external")) or str(asset.get("storage_status") or "local") != "local":
                continue
            try:
                file_path = resolve_db_asset_path(root, str(asset["path"]))
            except ValueError:
                file_path = None
            if file_path is not None and file_path.is_file():
                continue
            fingerprint = stable_fingerprint("asset", [asset], action="missing_asset_file")
            plan = {
                "action_type": "repair_relations_or_assets",
                "entity_type": "asset",
                "record_ids": [asset["id"]],
                "operation": "recover_or_relink_missing_asset",
                "requires_review": True,
                "source_url": asset.get("source_url"),
                "stored_path": asset["path"],
                "source_fingerprint": fingerprint,
                "source_state_fingerprint": stable_fingerprint("asset", [asset]),
            }
            output.append(Finding(
                ActionType.REPAIR,
                "asset",
                [asset["id"]],
                Classification.BLOCKED,
                [Evidence(
                    "missing_asset_file",
                    "strong",
                    "The database references an asset file that is not present on disk",
                    [asset["path"], asset.get("source_url")],
                )],
                [],
                plan,
                fingerprint,
                1,
            ))
    return output


def latest_run(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM quality_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    result = dict(row)
    result["summary"] = json.loads(result.pop("summary_json") or "{}")
    return result


def _evidence_codes(finding: dict) -> set[str]:
    return {
        str(item.get("code") or "")
        for item in (finding.get("evidence") or [])
        if isinstance(item, dict)
    }


def _has_blocking_contradiction(finding: dict) -> bool:
    return any(
        str(item.get("strength") or "") in {"blocking", "review"}
        for item in (finding.get("contradictions") or [])
        if isinstance(item, dict)
    )


def _deterministic_fields(fields: list[dict]) -> bool:
    """True when every field already has a safe, concrete server-side result."""
    if not fields:
        return False
    allowed = {
        "replace_with_cleaned", "derive_from_content", "fill_missing",
        "clear_value", "best_quality_choice", "keep", "preserve_relationship",
    }
    for field in fields:
        if not isinstance(field, dict):
            return False
        action = str(field.get("action") or "")
        if action == "manual_edit_required" or action not in allowed:
            return False
        if action not in {"clear_value", "keep", "preserve_relationship"} and field.get("proposed_value") is None:
            return False
    return True


def finding_automatic_reason(finding: dict) -> str | None:
    """Return why a finding is safe for one-click/bulk execution, or None.

    This is intentionally plan-based rather than classification-only. It lets
    deterministic cleanup happen automatically while keeping genuinely lossy
    or ambiguous decisions in the manual queue.
    """
    classification = str(finding.get("classification") or "")
    action = str(finding.get("action_type") or "")
    plan = finding.get("plan") or {}
    fields = [field for field in (plan.get("fields") or []) if isinstance(field, dict)]
    operation = str(plan.get("operation") or "")
    score = float(finding.get("score") or 0)
    evidence_codes = _evidence_codes(finding)

    if classification in {"related_not_duplicate", "keep_separate", "blocked", "ambiguous", "aggregated_record", "page_fragment_attached"}:
        return None
    if action == "split_aggregated_record" or _has_blocking_contradiction(finding):
        return None

    # Proven duplicate identity is safe to consolidate even when the richer
    # record has conflicting descriptive fields: apply_best_quality() resolves
    # those fields deterministically and the operation is backed up first.
    if action == "merge_records" and classification == "exact_duplicate":
        return "Deterministic identity match; the richer canonical record can be selected automatically."

    # Very-high-confidence duplicate candidates are the common legacy case
    # (same headline/date, almost identical content, forced reimport clones).
    # Keep lower-confidence strong candidates manual.
    if action == "merge_records" and classification == "strong_candidate" and score >= 0.97:
        return "Duplicate confidence is at least 97% with no review/blocking contradiction."

    # Entity-specific evidence profiles safely cover common duplicates that a
    # single global score misses. Every profile requires several independent
    # identity signals and the blocking-contradiction guard above still wins.
    if action == "merge_records" and classification == "strong_candidate":
        entity_type = str(finding.get("entity_type") or plan.get("entity_type") or "")
        if (
            entity_type == "news"
            and score >= 0.94
            and {"equivalent_article_text", "compatible_publication_date"} <= evidence_codes
        ):
            return "Near-identical article text and compatible publication dates identify the same news item."
        if (
            entity_type == "publication"
            and score >= 0.88
            and {"same_title_authors", "same_publication_year"} <= evidence_codes
            and ("same_journal" in evidence_codes or score >= 0.94)
        ):
            return "Title, authors and year match, with compatible journal evidence."
        if (
            entity_type == "member"
            and score >= 0.90
            and {"same_normalized_person_name", "compatible_affiliation", "similar_bio"} <= evidence_codes
        ):
            return "Name, affiliation and biography independently identify the same member."

    if action == "repair_relations_or_assets" and operation in {"deduplicate_primary_links", "deduplicate_primary_assets"}:
        return "Relationship repair is deterministic and does not require choosing content."

    if action == "clean_record" and operation == "remove_merged_duplicate":
        return "A canonical alias proves this legacy duplicate was already merged."

    # Explicit technical/test/junk rows can be quarantined rather than deleted.
    # Quarantine is reversible and avoids asking the administrator to approve
    # hundreds of obvious scraper artefacts one at a time.
    if action == "clean_record" and operation == "quarantine":
        if classification == "junk_technical_record" or "invalid_or_test_record" in evidence_codes:
            return "Technical/test content can be quarantined reversibly without deleting it."

    if action == "clean_record" and _deterministic_fields(fields):
        deterministic_cleanup = bool(evidence_codes & {
            "scraper_boilerplate", "false_annual_range", "placeholder_title",
            "legacy_merged_duplicate",
        })
        if deterministic_cleanup or classification in {"needs_cleaning", "invalid_record"}:
            return "The cleaned values are already fully determined by the analyzer."

    if action == "enrich_record" and _deterministic_fields(fields):
        return "All enrichment values have one concrete high-confidence value."

    return None


def finding_workflow(finding: dict) -> str:
    """Map a finding to automatic, manual, or informational administrator work."""
    classification = str(finding.get("classification") or "")
    action = str(finding.get("action_type") or "")
    plan = finding.get("plan") or {}
    fields = [field for field in (plan.get("fields") or []) if isinstance(field, dict)]

    if classification in {"related_not_duplicate", "keep_separate"}:
        return "informational"

    if finding_automatic_reason(finding):
        return "automatic"

    if action == "split_aggregated_record" or classification == "aggregated_record":
        return "manual"
    if bool(plan.get("requires_review")):
        return "manual"
    if classification == "blocked":
        return "informational"
    if any(field.get("requires_review") or field.get("action") == "manual_edit_required" for field in fields):
        return "manual"
    if classification in {"ambiguous", "invalid_record", "junk_technical_record", "page_fragment_attached"}:
        return "manual"
    if classification == "strong_candidate":
        return "manual"
    if classification in {"exact_duplicate", "needs_cleaning"}:
        return "automatic"
    if action == "repair_relations_or_assets":
        return "automatic"
    if action in {"clean_record", "enrich_record"} and fields:
        return "automatic"
    return "manual"


def finding_workflow_reason(finding: dict) -> str:
    automatic = finding_automatic_reason(finding)
    if automatic:
        return automatic
    workflow = finding_workflow(finding)
    classification = str(finding.get("classification") or "")
    action = str(finding.get("action_type") or "")
    plan = finding.get("plan") or {}
    if workflow == "informational":
        return "No database change is required; review or dismiss this information."
    if action == "split_aggregated_record":
        return "Confirm the records and titles that should be created by the split."
    if classification == "ambiguous":
        return "Identity evidence is not strong enough to merge records automatically."
    if classification == "blocked":
        return "The analyzer found a problem but cannot repair it safely without administrator input."
    if plan.get("requires_review"):
        return "The proposed change is potentially lossy or needs an explicit administrator decision."
    if any(isinstance(field, dict) and field.get("requires_review") for field in (plan.get("fields") or [])):
        return "At least one proposed field value needs administrator confirmation."
    return "Review the proposed change before it is queued."

def manual_plan_actionable(finding: dict) -> bool:
    """Return True only when the manual review UI has a real editable decision.

    Manual findings with no canonical choice, editable fields, or split titles are
    review-only: the administrator must be able to close them without creating
    an impossible/empty bundle item.
    """
    if finding_workflow(finding) != "manual":
        return False
    plan = finding.get("plan") or {}
    action = str(finding.get("action_type") or plan.get("action_type") or "")
    if action == "merge_records":
        ids = {
            int(value)
            for value in (finding.get("record_ids") or plan.get("record_ids") or [])
            if str(value).isdigit()
        }
        records = [item for item in (plan.get("records") or []) if isinstance(item, dict)]
        record_ids = {int(item["id"]) for item in records if str(item.get("id") or "").isdigit()}
        return len(ids | record_ids) >= 2
    if action == "split_aggregated_record":
        return bool([item for item in (plan.get("proposed_records") or []) if isinstance(item, dict)])
    if action in {"clean_record", "enrich_record"}:
        return any(
            isinstance(field, dict) and str(field.get("field") or "").strip()
            for field in (plan.get("fields") or [])
        )
    return False


def finding_review_only(finding: dict) -> bool:
    return finding_workflow(finding) == "manual" and not manual_plan_actionable(finding)


def _requested_workflow(classification: str) -> str:
    if classification == "reviewable":
        return "automatic"
    return classification if classification in {"automatic", "manual", "informational"} else ""


def _finding_filter(run_id: int, action_type: str, entity_type: str, classification: str):
    # Keep this helper's historical two-value return contract because executor
    # batch operations also use it. Workflow pseudo-filters are applied after
    # decoding the plan, since review requirements live inside plan_json.
    clauses: list[str] = ["run_id=?", "status='open'"]
    args: list[Any] = [run_id]
    workflow = _requested_workflow(classification)
    for column, value in (("action_type", action_type), ("entity_type", entity_type)):
        if value:
            clauses.append(f"{column}=?")
            args.append(value)
    if classification and not workflow:
        clauses.append("classification=?")
        args.append(classification)
    return clauses, args


def _workflow_rows(conn: sqlite3.Connection, run_id: int, *, action_type: str = "", entity_type: str = "", classification: str = "") -> list[dict]:
    clauses, args = _finding_filter(run_id, action_type, entity_type, classification)
    workflow = _requested_workflow(classification)
    rows = [_decode_finding(row) for row in conn.execute(
        f"SELECT * FROM quality_findings WHERE {' AND '.join(clauses)} ORDER BY score DESC,id", args
    )]
    return [row for row in rows if not workflow or row["workflow"] == workflow]


def list_findings(conn: sqlite3.Connection, run_id: int, *, action_type: str = "", entity_type: str = "", classification: str = "", limit: int = 500, offset: int = 0) -> list[dict]:
    rows = _workflow_rows(conn, run_id, action_type=action_type, entity_type=entity_type, classification=classification)
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    return rows[safe_offset:safe_offset + safe_limit]


def count_findings(conn: sqlite3.Connection, run_id: int, *, action_type: str = "", entity_type: str = "", classification: str = "") -> int:
    return len(_workflow_rows(conn, run_id, action_type=action_type, entity_type=entity_type, classification=classification))


def count_workflows(conn: sqlite3.Connection, run_id: int) -> dict[str, int]:
    """Count open findings by UX workflow with one database pass."""
    counts = {"automatic": 0, "manual": 0, "informational": 0}
    clauses, args = _finding_filter(run_id, "", "", "")
    for row in conn.execute(
        f"SELECT * FROM quality_findings WHERE {' AND '.join(clauses)}", args
    ):
        item = _decode_finding(row)
        workflow = item["workflow"]
        counts[workflow] = counts.get(workflow, 0) + 1
    return counts


def get_finding(conn: sqlite3.Connection, finding_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM quality_findings WHERE id=?", (finding_id,)).fetchone()
    return _decode_finding(row) if row else None


def _decode_finding(row: sqlite3.Row) -> dict:
    item = dict(row)
    for source, target, fallback in (
        ("record_ids_json", "record_ids", []), ("evidence_json", "evidence", []),
        ("contradictions_json", "contradictions", []), ("plan_json", "plan", {}),
    ):
        item[target] = json.loads(item.pop(source) or json.dumps(fallback))
    item["workflow"] = finding_workflow(item)
    item["workflow_reason"] = finding_workflow_reason(item)
    item["automatic_reason"] = finding_automatic_reason(item)
    item["manual_actionable"] = manual_plan_actionable(item)
    item["review_only"] = item["workflow"] == "manual" and not item["manual_actionable"]
    return item

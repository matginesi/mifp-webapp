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
    return {"links": links, "assets": assets}


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
                "operation": "quarantine", "requires_review": True,
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
    first_lower, last_lower = first.casefold(), last.casefold()
    if first_lower != last_lower and (last_lower in first_lower or first_lower in last_lower):
        evidence = [Evidence("name_inversion", "strong", "One name field contains the other; likely first/last name are swapped", [first, last])]
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
        if len(parts) >= 2:
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
    if entity_type not in TABLES:
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
                "requires_review": True,
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
            stored_path = Path(str(asset["path"]))
            file_path = stored_path if stored_path.is_absolute() else root / stored_path
            if file_path.is_file():
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


def finding_workflow(finding: dict) -> str:
    """Map a finding to the administrator workflow shown by the UI."""
    classification = str(finding.get("classification") or "")
    action = str(finding.get("action_type") or "")
    plan = finding.get("plan") or {}
    if classification in {"blocked", "related_not_duplicate", "keep_separate"}:
        return "informational"
    if classification in {"ambiguous", "invalid_record", "aggregated_record"}:
        return "manual"
    if action == "split_aggregated_record" or bool(plan.get("requires_review")):
        return "manual"
    for field in plan.get("fields") or []:
        if field.get("requires_review") or field.get("action") == "manual_edit_required":
            return "manual"
    if classification in {"exact_duplicate", "strong_candidate", "needs_cleaning"} or action == "repair_relations_or_assets":
        return "automatic"
    return "manual"


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
    return item

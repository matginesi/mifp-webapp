from __future__ import annotations

import copy
import re
import sqlite3
from typing import Any

from .normalizers import clean_boilerplate, comparison_text, stable_fingerprint

_DATE_PLACEHOLDER_START = re.compile(r"^\d{4}-01-01$")
_DATE_PLACEHOLDER_END = re.compile(r"^\d{4}-12-31$")


def resolve_dates(fields: list[dict], records: list[dict]) -> dict:
    merged: dict[str, Any] = {}
    for field in ("start_date", "end_date", "date_precision", "date_text", "date_is_inferred"):
        values: list[Any] = [r.get(field) for r in records if r.get(field) not in (None, "")]
        if not values:
            continue
        if field == "date_precision":
            precisions = {"day": 4, "month": 3, "year": 2, "range": 1, "unknown": 0}
            best = max(values, key=lambda v: precisions.get(str(v), 0))
            merged[field] = best
        elif field == "date_is_inferred":
            merged[field] = min(int(v) for v in values)
        elif field in ("start_date", "end_date"):
            non_placeholder = [v for v in values
                               if not _DATE_PLACEHOLDER_START.match(str(v))
                               and not _DATE_PLACEHOLDER_END.match(str(v))]
            if non_placeholder:
                merged[field] = max(non_placeholder, key=lambda v: len(str(v)))
            else:
                merged[field] = max(values, key=lambda v: len(str(v)))
        else:
            merged[field] = max(values, key=lambda v: len(str(v)))
    if "start_date" in merged and "end_date" in merged:
        if merged["start_date"] > merged["end_date"]:
            merged["end_date"] = None
            merged["date_precision"] = merged.get("date_precision", "unknown")
    return merged


TABLES = {
    "member": "members",
    "event": "events",
    "news": "news",
    "publication": "publications",
    "research_area": "research_areas",
    "page": "pages",
    "sponsor": "sponsors",
}
LABELS = {
    "member": "display_name",
    "event": "title",
    "news": "title",
    "publication": "title",
    "research_area": "title",
    "page": "title",
    "sponsor": "name",
}
_OPERATIONAL = {"id", "created_at", "updated_at", "sort_order", "source_order", "display_order"}
_TEXT_FIELDS = {"description", "body", "summary", "abstract", "bio"}


def records_for(conn: sqlite3.Connection, entity_type: str, ids: list[int]) -> list[dict]:
    table = TABLES[entity_type]
    marks = ",".join("?" for _ in ids)
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" WHERE id IN ({marks}) ORDER BY id', ids)]


def _record_score(conn: sqlite3.Connection, entity_type: str, row: dict) -> tuple:
    specific_links = conn.execute(
        """SELECT COUNT(*) FROM entity_links
           WHERE entity_type=? AND entity_id=? AND is_primary=1""",
        (entity_type, row["id"]),
    ).fetchone()[0]
    relations = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM entity_links WHERE entity_type=? AND entity_id=?)
             +(SELECT COUNT(*) FROM asset_links WHERE entity_type=? AND entity_id=?)""",
        (entity_type, row["id"], entity_type, row["id"]),
    ).fetchone()[0]
    semantic = sum(bool(row.get(key)) for key in row if key not in _OPERATIONAL)
    slug = str(row.get("slug") or "")
    stable_slug = bool(slug)
    # Force reimport appends -2, -3, ... to an existing stable slug. When
    # otherwise equivalent, retain the original public identity as canonical.
    force_suffix = re.fullmatch(r".+-([2-9]\d*)", slug)
    original_slug = not bool(force_suffix)
    suffix_preference = -int(force_suffix.group(1)) if force_suffix else 0
    date_key = str(row.get("updated_at") or row.get("created_at") or "")
    return (
        specific_links, stable_slug, semantic, relations,
        original_slug, suffix_preference, date_key, int(row["id"]),
    )


def canonical_record(conn: sqlite3.Connection, entity_type: str, records: list[dict]) -> dict:
    return max(records, key=lambda row: _record_score(conn, entity_type, row))


def _best_field_value(field: str, values: list[dict], canonical_id: int) -> tuple[Any, int]:
    candidates = [item for item in values if item.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    identity_fields = {"slug", "title", "display_name", "name", "first_name", "last_name"}
    if field in identity_fields:
        canonical = next((item for item in candidates if int(item["record_id"]) == canonical_id), None)
        if canonical:
            return canonical["value"], canonical_id

    def score(item: dict) -> tuple:
        value = item["value"]
        text = str(value).strip()
        cleaned, removed = clean_boilerplate(text)
        useful = cleaned if field in _TEXT_FIELDS else text
        normalized = comparison_text(useful)
        status = 3 if field == "review_status" and normalized == "published" else 0
        url_specificity = 2 if field.endswith("url") and "/" in text.removeprefix("https://").removeprefix("http://") else 0
        density = len(set(normalized.split()))
        return (
            status,
            not removed,
            url_specificity,
            min(density, 200),
            min(len(useful), 4000),
            int(item["record_id"]) == canonical_id,
            int(item["record_id"]),
        )

    best = max(candidates, key=score)
    value = clean_boilerplate(best["value"])[0] if field in _TEXT_FIELDS else best["value"]
    return value, int(best["record_id"])


_GENERIC_EMAILS = frozenset({"info", "contact", "admin", "webmaster", "support", "noreply", "no-reply"})


def _is_generic_email(email: str) -> bool:
    local = email.split("@")[0].strip().lower() if "@" in email else email
    return local in _GENERIC_EMAILS


def _resolve_member_email(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    personal = [v for v in candidates if not _is_generic_email(str(v["value"]))]
    if personal:
        best = max(personal, key=lambda v: len(str(v["value"])))
        return str(best["value"]), int(best["record_id"])
    best = max(candidates, key=lambda v: len(str(v["value"])))
    return str(best["value"]), int(best["record_id"])


def _resolve_member_affiliation(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    best = max(candidates, key=lambda v: (len(str(v["value"]).split(",")), len(str(v["value"]))))
    return str(best["value"]), int(best["record_id"])


def _resolve_news_body(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id

    def body_score(v):
        text = str(v["value"])
        cleaned, _ = clean_boilerplate(text)
        return len(cleaned)

    best = max(candidates, key=body_score)
    cleaned, _ = clean_boilerplate(str(best["value"]))
    return cleaned, int(best["record_id"])


def _resolve_news_summary(values: list[dict], canonical_id: int, body_values: list[dict]) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if candidates:
        best = max(candidates, key=lambda v: len(str(v["value"])))
        summary = str(best["value"])
        if len(summary.split()) >= 10:
            return summary, int(best["record_id"])
    body_candidates = [v for v in body_values if v.get("value") not in (None, "")]
    if body_candidates:
        best_body = max(body_candidates, key=lambda v: len(str(v["value"])))
        body = str(best_body["value"])
        sentences = body.replace("\n", " ").split(". ")
        if sentences:
            first = sentences[0].strip()
            if not first.endswith((".", "!", "?")):
                first += "."
            if 10 <= len(first.split()) <= 40:
                return first, int(best_body["record_id"])
    return None, canonical_id


def _resolve_event_description(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id

    def desc_score(v):
        text = str(v["value"])
        cleaned, removed = clean_boilerplate(text)
        return len(cleaned), -len(removed), -len(text)

    best = max(candidates, key=desc_score)
    cleaned, _ = clean_boilerplate(str(best["value"]))
    return cleaned, int(best["record_id"])


def _resolve_publication_title(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id

    def title_score(v):
        text = str(v["value"]).strip()
        if text.isdigit():
            return 0
        if len(text) < 10:
            return 1
        return 10 + len(text)

    best = max(candidates, key=title_score)
    return str(best["value"]), int(best["record_id"])


def apply_best_quality(plan: dict[str, Any]) -> dict[str, Any]:
    """Resolve field choices deterministically while preserving the plan audit trail."""
    resolved = copy.deepcopy(plan)
    canonical_id = int(resolved.get("canonical_id") or (resolved.get("record_ids") or [0])[0])
    entity_type = str(resolved.get("entity_type") or "")
    choices: list[dict[str, Any]] = []

    body_values: list[dict[str, Any]] = []
    for f in resolved.get("fields") or []:
        if f.get("field") == "body":
            body_values = f.get("values_by_record") or []

    for field in resolved.get("fields") or []:
        field_name = str(field["field"])
        if resolved.get("action_type") == "clean_record":
            value, record_id = field.get("proposed_value"), canonical_id
        else:
            values = field.get("values_by_record") or []

            if entity_type == "member" and field_name == "email":
                value, record_id = _resolve_member_email(values, canonical_id)
            elif entity_type == "member" and field_name == "affiliation":
                value, record_id = _resolve_member_affiliation(values, canonical_id)
            elif entity_type == "news" and field_name == "body":
                value, record_id = _resolve_news_body(values, canonical_id)
            elif entity_type == "news" and field_name == "summary":
                value, record_id = _resolve_news_summary(values, canonical_id, body_values)
            elif entity_type == "event" and field_name == "description":
                value, record_id = _resolve_event_description(values, canonical_id)
            elif entity_type == "publication" and field_name == "title":
                value, record_id = _resolve_publication_title(values, canonical_id)
            else:
                value, record_id = _best_field_value(field_name, values, canonical_id)

            field["proposed_value"] = value

        if field.get("action") == "manual_edit_required" and value is None:
            values = field.get("values_by_record") or []
            nonnull = [item for item in values if item.get("value") not in (None, "")]
            if len(nonnull) == 1:
                value, record_id = nonnull[0]["value"], int(nonnull[0]["record_id"])
                field["proposed_value"] = value
        field["requires_review"] = False
        field["action"] = "best_quality_choice"
        field["confidence"] = "high"
        field["reason"] = "Automatically selected the most complete clean value; stable identity wins ties."
        choices.append({"field": field_name, "record_id": record_id})
    resolved["selection_strategy"] = "best_quality"
    resolved["quality_choices"] = choices
    return resolved


def build_merge_plan(conn: sqlite3.Connection, entity_type: str, records: list[dict]) -> dict[str, Any]:
    canonical = canonical_record(conn, entity_type, records)
    fields: list[dict[str, Any]] = []
    table = TABLES[entity_type]
    self_references = {
        str(row["from"])
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        if str(row["table"]) == table
    }
    columns = [key for key in canonical if key not in _OPERATIONAL]
    for field in columns:
        values = [{"record_id": row["id"], "value": row.get(field)} for row in records]
        nonempty = [item for item in values if item["value"] not in (None, "")]
        normalized = {comparison_text(item["value"]) for item in nonempty}
        proposed = canonical.get(field)
        action = "keep"
        requires_review = False
        reason = "Canonical value is retained."

        type_resolved = field in self_references
        if field in self_references:
            action = "preserve_relationship"
            requires_review = False
            reason = "Relationship is preserved and remapped safely during the merge."
        elif entity_type == "member" and field == "email":
            proposed, _ = _resolve_member_email(values, canonical["id"])
            action = "best_quality_choice"
            reason = "Selected personal email over generic."
            type_resolved = True
        elif entity_type == "member" and field == "affiliation":
            proposed, _ = _resolve_member_affiliation(values, canonical["id"])
            action = "best_quality_choice"
            reason = "Selected most specific affiliation."
            type_resolved = True
        elif entity_type == "news" and field == "body":
            proposed, _ = _resolve_news_body(values, canonical["id"])
            action = "best_quality_choice"
            reason = "Selected longest clean body text."
            type_resolved = True
        elif entity_type == "news" and field == "summary":
            body_values = [{"record_id": r["id"], "value": r.get("body")} for r in records]
            proposed, _ = _resolve_news_summary(values, canonical["id"], body_values)
            action = "best_quality_choice"
            reason = "Selected from best summary or derived from body."
            type_resolved = True
        elif entity_type == "event" and field == "description":
            proposed, _ = _resolve_event_description(values, canonical["id"])
            action = "best_quality_choice"
            reason = "Selected description with most content after boilerplate removal."
            type_resolved = True
        elif entity_type == "publication" and field == "title":
            proposed, _ = _resolve_publication_title(values, canonical["id"])
            action = "best_quality_choice"
            reason = "Selected most substantive title."
            type_resolved = True

        if not type_resolved:
            if not proposed and len(nonempty) == 1:
                proposed = nonempty[0]["value"]
                action = "fill_missing"
                reason = "Only one record provides this value."
            elif field in _TEXT_FIELDS:
                cleaned = [(item, clean_boilerplate(item["value"])[0]) for item in nonempty]
                clean_values = {comparison_text(value) for _, value in cleaned if value}
                if len(clean_values) == 1 and cleaned:
                    proposed = cleaned[0][1]
                    action = "replace_with_cleaned" if proposed != canonical.get(field) else "keep"
                    reason = "Equivalent text remains after deterministic boilerplate removal."
                elif len(clean_values) > 1:
                    action = "manual_edit_required"
                    requires_review = True
                    reason = "Text contains complementary or conflicting segments and must be reviewed."
            elif len(normalized) > 1:
                action = "manual_edit_required"
                requires_review = True
                reason = "Distinct semantic values cannot be chosen automatically."
        fields.append({
            "field": field,
            "values_by_record": values,
            "proposed_value": proposed,
            "action": action,
            "reason": reason,
            "confidence": "high" if not requires_review else "review",
            "requires_review": requires_review,
            "losses": [],
        })
    ids = [int(row["id"]) for row in records]
    links = [dict(row) for row in conn.execute(
        f"SELECT * FROM entity_links WHERE entity_type=? AND entity_id IN ({','.join('?' for _ in ids)})",
        [entity_type, *ids],
    )]
    assets = [dict(row) for row in conn.execute(
        f"SELECT al.*,a.filename,a.checksum,a.path,a.mime_type,a.size,a.width,a.height,a.storage_status,a.is_external "
        f"FROM asset_links al JOIN assets a ON a.id=al.asset_id "
        f"WHERE al.entity_type=? AND al.entity_id IN ({','.join('?' for _ in ids)})",
        [entity_type, *ids],
    )]
    preferred_asset = max(
        assets,
        key=lambda asset: (
            asset.get("storage_status") == "local",
            str(asset.get("mime_type") or "").startswith("image/"),
            int(asset.get("width") or 0) * int(asset.get("height") or 0),
            int(asset.get("size") or 0),
            bool(asset.get("checksum")),
            -int(asset["asset_id"]),
        ),
        default=None,
    )
    return {
        "action_type": "merge_records",
        "entity_type": entity_type,
        "record_ids": ids,
        "canonical_id": canonical["id"],
        "canonical_reason": "Best stable public identity, specific links, valid fields and relations.",
        "records": records,
        "fields": fields,
        "links": links,
        "assets": assets,
        "preferred_asset_id": preferred_asset["asset_id"] if preferred_asset else None,
        "aliases": [row.get("slug") for row in records if row["id"] != canonical["id"] and row.get("slug")],
        "source_fingerprint": stable_fingerprint(entity_type, records, action="merge_records"),
        "source_state_fingerprint": stable_fingerprint(entity_type, records),
    }


def build_clean_plan(entity_type: str, record: dict, changes: dict[str, Any], removed: dict[str, list[str]]) -> dict:
    fields = []
    for field, value in changes.items():
        fields.append({
            "field": field,
            "values_by_record": [{"record_id": record["id"], "value": record.get(field)}],
            "proposed_value": value,
            "action": "replace_with_cleaned",
            "reason": "Remove detected technical or navigation segments.",
            "confidence": "review" if len(value) < len(str(record.get(field) or "")) * .6 else "high",
            "requires_review": True,
            "losses": removed.get(field, []),
        })
    return {
        "action_type": "clean_record",
        "entity_type": entity_type,
        "record_ids": [record["id"]],
        "record": record,
        "fields": fields,
        "source_fingerprint": stable_fingerprint(entity_type, [record], action="clean_record"),
        "source_state_fingerprint": stable_fingerprint(entity_type, [record]),
    }


def build_split_plan(record: dict, segments: list[str]) -> dict:
    candidates = [{"segment": item, "title_hint": re.split(r"[.\n]", item, maxsplit=1)[0][:240]} for item in segments]
    return {
        "action_type": "split_aggregated_record",
        "entity_type": "publication",
        "record_ids": [record["id"]],
        "source_record": record,
        "proposed_records": candidates,
        "requires_review": True,
        "source_fingerprint": stable_fingerprint("publication", [record], action="split_aggregated_record"),
        "source_state_fingerprint": stable_fingerprint("publication", [record]),
    }

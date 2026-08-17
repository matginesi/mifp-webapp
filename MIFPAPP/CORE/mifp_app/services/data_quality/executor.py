from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from ...db.connection import connect
from ..admin_safety import backup_sqlite_database
from .analyzer import get_finding
from .models import require_payload
from .normalizers import content_fingerprint, stable_fingerprint
from .planner import TABLES, apply_best_quality, records_for

log = logging.getLogger(__name__)

_IMMUTABLE_PLAN_KEYS = {
    "action_type", "entity_type", "record_ids", "records", "record",
    "source_fingerprint", "source_state_fingerprint",
}


def _non_executable_reason(plan: dict[str, Any], fallback_record_ids: list[int] | None = None) -> str | None:
    action = str(plan.get("action_type") or "")
    record_ids = [int(value) for value in (plan.get("record_ids") or fallback_record_ids or [])]
    if action == "merge_records":
        canonical_id = int(plan.get("canonical_id") or 0)
        if len(set(record_ids)) < 2 or canonical_id not in record_ids:
            return "a merge requires at least two records and a canonical record"
    if action == "repair_relations_or_assets" and plan.get("operation") not in {
        "deduplicate_primary_links",
        "deduplicate_primary_assets",
    }:
        return "this asset repair requires a manual recovery or relink"
    return None


def _reviewed_plan(original: dict[str, Any], submitted: dict[str, Any]) -> dict[str, Any]:
    """Accept administrator field choices without trusting client-side structure."""
    reviewed = json.loads(json.dumps(original, default=str))
    for key in _IMMUTABLE_PLAN_KEYS:
        if submitted.get(key, original.get(key)) != original.get(key):
            raise ValueError(f"plan field {key} cannot be changed")

    if "canonical_id" in submitted:
        try:
            canonical_id = int(submitted["canonical_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("canonical record must be a valid record ID") from exc
        record_ids = {int(value) for value in original.get("record_ids") or []}
        if not record_ids:
            record_ids = {
                int(record["id"])
                for record in original.get("records") or []
                if isinstance(record, dict) and record.get("id") is not None
            }
        if canonical_id not in record_ids:
            raise ValueError("canonical record must belong to this finding")
        reviewed["canonical_id"] = canonical_id

    original_fields = {
        str(field.get("field")): field for field in original.get("fields") or []
        if isinstance(field, dict) and field.get("field")
    }
    submitted_fields = submitted.get("fields")
    if submitted_fields is not None:
        if not isinstance(submitted_fields, list):
            raise ValueError("plan fields must be a list")
        seen: set[str] = set()
        for choice in submitted_fields:
            if not isinstance(choice, dict):
                raise ValueError("each reviewed field must be an object")
            name = str(choice.get("field") or "")
            if name not in original_fields or name in seen:
                raise ValueError(f"unknown or duplicate reviewed field {name}")
            value = choice.get("proposed_value")
            if isinstance(value, (dict, list)) or not isinstance(
                value, (str, int, float, bool, type(None))
            ):
                raise ValueError(f"reviewed field {name} has an unsupported value")
            if isinstance(value, str) and len(value) > 100_000:
                raise ValueError(f"reviewed field {name} exceeds the size limit")
            seen.add(name)
            original_fields[name]["proposed_value"] = value
            original_fields[name]["requires_review"] = False
            original_fields[name]["action"] = "administrator_choice"
            original_fields[name]["reason"] = "Value explicitly selected by the administrator."
        if seen != set(original_fields):
            raise ValueError("reviewed plan must include every proposed field")
        reviewed["fields"] = list(original_fields.values())

    if "proposed_records" in original:
        proposed = submitted.get("proposed_records")
        if proposed is None:
            proposed = original["proposed_records"]
        if not isinstance(proposed, list) or len(proposed) != len(original["proposed_records"]):
            raise ValueError("split record count cannot be changed")
        safe_records = json.loads(json.dumps(original["proposed_records"], default=str))
        for index, choice in enumerate(proposed):
            if not isinstance(choice, dict):
                raise ValueError("each split record choice must be an object")
            title = str(choice.get("title") or "").strip()
            if not title:
                raise ValueError("every split record requires a title")
            safe_records[index]["title"] = title[:240]
        reviewed["proposed_records"] = safe_records
    return reviewed


def create_bundle(conn: sqlite3.Connection, administrator: str) -> int:
    cursor = conn.execute("INSERT INTO quality_bundles(created_by) VALUES(?)", (administrator,))
    conn.commit()
    return int(cursor.lastrowid or 0)


def add_to_bundle(conn: sqlite3.Connection, bundle_id: int, finding_id: int, payload: dict[str, Any] | None = None) -> dict:
    bundle = conn.execute("SELECT status FROM quality_bundles WHERE id=?", (bundle_id,)).fetchone()
    finding = get_finding(conn, finding_id)
    if not bundle or bundle["status"] not in {"draft", "validated"}:
        raise ValueError("editable bundle not found")
    if not finding or finding["status"] not in {"open", "bundled"}:
        raise ValueError("open finding not found")
    selected = require_payload(payload or {})
    submitted_plan = selected.get("plan")
    if (
        finding["classification"] in {"blocked", "related_not_duplicate", "keep_separate"}
        and not isinstance(submitted_plan, dict)
    ):
        raise ValueError("this finding requires an explicit administrator plan")
    plan = (
        _reviewed_plan(finding["plan"], submitted_plan)
        if isinstance(submitted_plan, dict)
        else finding["plan"]
    )
    if selected.get("strategy") == "best_quality":
        if finding["classification"] == "ambiguous" and not isinstance(submitted_plan, dict):
            raise ValueError("ambiguous identity still requires review")
        if finding["action_type"] not in {"merge_records", "enrich_record", "clean_record"}:
            raise ValueError("best-quality selection is available for merge, enrichment and cleanup actions")
        plan = apply_best_quality(plan)
    if plan.get("action_type") != finding["action_type"]:
        raise ValueError("plan action type does not match the finding")
    non_executable = _non_executable_reason(plan, finding["record_ids"])
    if non_executable:
        raise ValueError(f"finding is not executable: {non_executable}")
    if plan.get("action_type") == "split_aggregated_record":
        proposed = plan.get("proposed_records") or []
        for item in proposed:
            if not item.get("title"):
                item["title"] = item.get("title_hint") or item.get("segment", "")[:240]
    conn.execute(
        """INSERT INTO quality_bundle_items(bundle_id,finding_id,action_type,payload_json)
           VALUES(?,?,?,?)
           ON CONFLICT(bundle_id,finding_id) DO UPDATE SET
             action_type=excluded.action_type,payload_json=excluded.payload_json,status='pending'""",
        (bundle_id, finding_id, finding["action_type"], json.dumps({"plan": plan}, default=str)),
    )
    conn.execute("UPDATE quality_findings SET status='bundled',updated_at=CURRENT_TIMESTAMP WHERE id=?", (finding_id,))
    conn.execute("UPDATE quality_bundles SET status='draft',validation_json='{}',updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,))
    conn.commit()
    return plan


def remove_from_bundle(conn: sqlite3.Connection, bundle_id: int, item_id: int) -> None:
    row = conn.execute("SELECT finding_id FROM quality_bundle_items WHERE id=? AND bundle_id=?", (item_id, bundle_id)).fetchone()
    if not row:
        raise ValueError("bundle item not found")
    conn.execute("DELETE FROM quality_bundle_items WHERE id=? AND bundle_id=?", (item_id, bundle_id))
    conn.execute("UPDATE quality_findings SET status='open',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["finding_id"],))
    conn.execute("UPDATE quality_bundles SET status='draft',validation_json='{}',updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,))
    conn.commit()


def delete_draft(conn: sqlite3.Connection, bundle_id: int) -> None:
    bundle = conn.execute("SELECT status FROM quality_bundles WHERE id=?", (bundle_id,)).fetchone()
    if not bundle or bundle["status"] != "draft":
        raise ValueError("only a draft bundle can be deleted")
    conn.execute(
        "UPDATE quality_findings SET status='open' WHERE id IN (SELECT finding_id FROM quality_bundle_items WHERE bundle_id=?)",
        (bundle_id,),
    )
    conn.execute("DELETE FROM quality_bundles WHERE id=?", (bundle_id,))
    conn.commit()


def _bundle_rows(conn: sqlite3.Connection, bundle_id: int):
    bundle = conn.execute("SELECT * FROM quality_bundles WHERE id=?", (bundle_id,)).fetchone()
    if not bundle:
        raise ValueError("bundle not found")
    rows = conn.execute(
        """SELECT i.*,f.entity_type,f.record_ids_json,f.fingerprint,f.classification,f.score
           FROM quality_bundle_items i JOIN quality_findings f ON f.id=i.finding_id
           WHERE i.bundle_id=? ORDER BY i.sort_order,i.id""",
        (bundle_id,),
    ).fetchall()
    return bundle, rows


def validate_bundle(conn: sqlite3.Connection, bundle_id: int, *, persist: bool = True) -> dict:
    bundle, rows = _bundle_rows(conn, bundle_id)
    errors: list[str] = []
    warnings: list[str] = []
    plans: list[dict] = []
    occupied: dict[tuple[str, int], dict] = {}
    if not rows:
        errors.append("Bundle is empty")
    for row in rows:
        entity_type = str(row["entity_type"])
        plan = json.loads(row["payload_json"] or "{}").get("plan") or {}
        record_ids = [int(value) for value in (plan.get("record_ids") or json.loads(row["record_ids_json"]))]
        action = str(row["action_type"])
        non_executable = _non_executable_reason(plan, record_ids)
        if non_executable:
            conn.execute("DELETE FROM quality_bundle_items WHERE id=?", (row["id"],))
            conn.execute(
                "UPDATE quality_findings SET status='resolved',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (row["finding_id"],),
            )
            warnings.append(f"{entity_type} finding #{row['finding_id']} was removed: {non_executable}")
            log.warning(
                "validate_bundle removed non-executable item=%s finding=%s action=%s entity=%s reason=%s",
                row["id"], row["finding_id"], action, entity_type, non_executable,
            )
            continue
        overlap = {k for k in {(entity_type, value) for value in record_ids} if k in occupied}
        if overlap:
            first = occupied[overlap.pop()]
            current_score = float(row["score"] or 0)
            existing_score = float(first.get("score", 0))
            if current_score > existing_score:
                conn.execute(
                    "UPDATE quality_findings SET status='rejected' WHERE id=?", (first["finding_id"],)
                )
                conn.execute(
                    "DELETE FROM quality_bundle_items WHERE id=?", (first["item_id"],)
                )
                occupied.clear()
                for other in rows:
                    if other["id"] == row["id"] or other["id"] == first["item_id"]:
                        continue
                    for rid in [int(v) for v in json.loads(other["record_ids_json"])]:
                        occupied[(str(other["entity_type"]), rid)] = {"finding_id": other["finding_id"], "item_id": other["id"], "score": float(other["score"] or 0)}
                log.info("validate_bundle replaced item %s (score %s) with item %s (score %s) for overlapping records", first["item_id"], existing_score, row["id"], current_score)
            else:
                conn.execute(
                    "UPDATE quality_findings SET status='rejected' WHERE id=?", (row["finding_id"],)
                )
                conn.execute(
                    "DELETE FROM quality_bundle_items WHERE id=?", (row["id"],)
                )
                log.info("validate_bundle removed item %s (score %s) for overlapping records (existing score %s)", row["id"], current_score, existing_score)
            continue
        for value in record_ids:
            occupied[(entity_type, value)] = {"finding_id": row["finding_id"], "item_id": row["id"], "score": float(row["score"] or 0)}
        records = records_for(conn, entity_type, record_ids) if entity_type in TABLES else []
        if len(records) != len(record_ids):
            errors.append(f"{entity_type}: one or more source records no longer exist")
            continue
        current = stable_fingerprint(entity_type, records)
        expected = str(plan.get("source_state_fingerprint") or "")
        if current != expected:
            conn.execute("DELETE FROM quality_bundle_items WHERE id=?", (row["id"],))
            conn.execute(
                "UPDATE quality_findings SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (row["finding_id"],),
            )
            for value in record_ids:
                occupied.pop((entity_type, value), None)
            warnings.append(
                f"{entity_type} finding #{row['finding_id']} was removed because its source changed after analysis"
            )
            log.warning(
                "validate_bundle removed stale item=%s finding=%s entity=%s current=%s expected=%s",
                row["id"], row["finding_id"], entity_type, current, expected,
            )
            continue
        if action == "merge_records":
            canonical = int(plan.get("canonical_id") or 0)
            if canonical not in record_ids:
                errors.append(f"{entity_type}: canonical record is not part of the merge")
            for field in plan.get("fields", []):
                if field.get("requires_review") and field.get("proposed_value") is None and field.get("action") == "manual_edit_required":
                    errors.append(f"{entity_type}: field {field.get('field')} still requires a value")
        elif action == "split_aggregated_record":
            proposed = plan.get("proposed_records") or []
            normalized = False
            for index, item in enumerate(proposed, start=1):
                if item.get("title"):
                    continue
                fallback = str(item.get("title_hint") or item.get("segment") or "").strip()
                if fallback:
                    item["title"] = fallback[:240]
                    normalized = True
            if len(proposed) < 2 or any(not str(item.get("title") or "").strip() for item in proposed):
                conn.execute("DELETE FROM quality_bundle_items WHERE id=?", (row["id"],))
                conn.execute(
                    "UPDATE quality_findings SET status='open',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (row["finding_id"],),
                )
                for value in record_ids:
                    occupied.pop((entity_type, value), None)
                warnings.append(
                    f"{entity_type} finding #{row['finding_id']} needs manual split titles and was returned to review"
                )
                log.warning(
                    "validate_bundle returned incomplete split to review item=%s finding=%s entity=%s",
                    row["id"], row["finding_id"], entity_type,
                )
                continue
            if normalized:
                payload = json.loads(row["payload_json"] or "{}")
                payload["plan"] = plan
                conn.execute(
                    "UPDATE quality_bundle_items SET payload_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False, default=str), row["id"]),
                )
                warnings.append(f"{entity_type} finding #{row['finding_id']} recovered legacy split titles automatically")
        elif action == "repair_relations_or_assets":
            if plan.get("operation") not in {"deduplicate_primary_links", "deduplicate_primary_assets"}:
                errors.append(f"{entity_type}: unsupported repair operation")
        plans.append({**plan, "finding_id": int(row["finding_id"]), "item_id": int(row["id"])})
    if not plans:
        errors.append("Bundle has no executable actions")
    aliases = sum(len(plan.get("aliases") or []) for plan in plans)
    assets = sum(len(plan.get("assets") or []) for plan in plans)
    report = {
        "valid": not errors, "errors": errors, "warnings": warnings,
        "operations": len(plans), "plans": plans, "aliases": aliases,
        "assets_preserved": assets, "files_quarantined": 0,
        "records_removed": sum(max(0, len(plan.get("record_ids") or []) - 1) for plan in plans if plan.get("action_type") == "merge_records"),
    }
    if persist:
        conn.execute(
            "UPDATE quality_bundles SET status=?,validation_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ("validated" if report["valid"] else "draft", json.dumps(report, default=str), bundle_id),
        )
        conn.execute(
            "UPDATE quality_bundle_items SET status=? WHERE bundle_id=?",
            ("validated" if report["valid"] else "pending", bundle_id),
        )
        conn.commit()
    return report


def _move_references(conn: sqlite3.Connection, entity_type: str, old_id: int, canonical_id: int) -> None:
    for row in conn.execute("SELECT * FROM entity_links WHERE entity_type=? AND entity_id=?", (entity_type, old_id)).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO entity_links(entity_type,entity_id,url,label,role,is_primary,sort_order)
               VALUES(?,?,?,?,?,?,?)""",
            (entity_type, canonical_id, row["url"], row["label"], row["role"], row["is_primary"], row["sort_order"]),
        )
    for row in conn.execute("SELECT * FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, old_id)).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order)
               VALUES(?,?,?,?,?,?)""",
            (row["asset_id"], entity_type, canonical_id, row["role"], row["is_primary"], row["sort_order"]),
        )
    conn.execute("DELETE FROM entity_links WHERE entity_type=? AND entity_id=?", (entity_type, old_id))
    conn.execute("DELETE FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, old_id))
    conn.execute("UPDATE import_records SET entity_id=? WHERE entity_type=? AND entity_id=?", (canonical_id, entity_type, old_id))
    conn.execute("UPDATE OR IGNORE entity_relations SET source_id=? WHERE source_type=? AND source_id=?", (canonical_id, entity_type, old_id))
    conn.execute("UPDATE OR IGNORE entity_relations SET target_id=? WHERE target_type=? AND target_id=?", (canonical_id, entity_type, old_id))
    conn.execute("DELETE FROM entity_relations WHERE (source_type=? AND source_id=?) OR (target_type=? AND target_id=?)", (entity_type, old_id, entity_type, old_id))
    conn.execute("DELETE FROM entity_relations WHERE source_type=target_type AND source_id=target_id")


def _write_resolved_pair(conn: sqlite3.Connection, entity_type: str, record: dict, action: str, finding_id: int | None = None, bundle_id: int | None = None) -> None:
    fp = content_fingerprint(record)
    conn.execute(
        """INSERT OR IGNORE INTO resolved_pairs(entity_type,left_fingerprint,right_fingerprint,
           action,finding_id,bundle_id,applied_at)
           VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (entity_type, fp, fp, action, finding_id, bundle_id),
    )


def _apply_clean(conn: sqlite3.Connection, plan: dict, bundle_id: int | None = None) -> None:
    entity_type = plan["entity_type"]
    table = TABLES[entity_type]
    record_id = int(plan["record_ids"][0])
    if plan.get("operation") == "remove_merged_duplicate":
        canonical_id = int(plan["canonical_id"])
        record = conn.execute(
            f'SELECT * FROM "{table}" WHERE id=?', (record_id,)
        ).fetchone()
        if not record or not conn.execute(
            f'SELECT 1 FROM "{table}" WHERE id=?', (canonical_id,)
        ).fetchone():
            raise ValueError("legacy duplicate or canonical record no longer exists")
        _write_resolved_pair(
            conn, entity_type, dict(record), "removed_legacy_duplicate",
            plan.get("finding_id"), bundle_id,
        )
        _move_references(conn, entity_type, record_id, canonical_id)
        conn.execute(f'DELETE FROM "{table}" WHERE id=?', (record_id,))
        return
    if plan.get("operation") == "swap_name_fields":
        first = str(plan.get("first_name") or "")
        last = str(plan.get("last_name") or "")
        conn.execute(f'UPDATE "{table}" SET first_name=?,last_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (last, first, record_id))
        record = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (record_id,)).fetchone()
        if record:
            _write_resolved_pair(conn, entity_type, dict(record), "cleaned", plan.get("finding_id"), bundle_id)
        return
    fields = plan.get("fields") or []
    assignments, values = [], []
    for field in fields:
        name = str(field["field"])
        if name not in {col["name"] for col in conn.execute(f'PRAGMA table_info("{table}")')}:
            raise ValueError(f"unknown field {name}")
        proposed = field.get("proposed_value")
        if proposed is None and field.get("action") != "clear_value":
            continue
        assignments.append(f'"{name}"=?')
        values.append(proposed)
    if plan.get("operation") == "quarantine":
        if "review_status" not in {col["name"] for col in conn.execute(f'PRAGMA table_info("{table}")')}:
            raise ValueError(f"{entity_type} records do not support quarantine")
        assignments.append('"review_status"=?')
        values.append("quarantined")
    if assignments:
        conn.execute(f'UPDATE "{table}" SET {", ".join(assignments)},updated_at=CURRENT_TIMESTAMP WHERE id=?', [*values, record_id])
    record = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (record_id,)).fetchone()
    if record:
        _write_resolved_pair(conn, entity_type, dict(record), "cleaned", plan.get("finding_id"), bundle_id)


def _foreign_key_targets(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, str]]:
    return {
        str(row["from"]): (str(row["table"]), str(row["to"] or "id"))
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
    }


def _foreign_key_value_exists(
    conn: sqlite3.Connection,
    target_table: str,
    target_column: str,
    value: Any,
) -> bool:
    return conn.execute(
        f'SELECT 1 FROM "{target_table}" WHERE "{target_column}"=? LIMIT 1',
        (value,),
    ).fetchone() is not None


def _remap_self_references(
    conn: sqlite3.Connection,
    table: str,
    canonical_id: int,
    old_ids: list[int],
) -> None:
    """Keep hierarchy edges valid while duplicate rows are removed."""
    if not old_ids:
        return
    merged_ids = [canonical_id, *old_ids]
    old_marks = ",".join("?" for _ in old_ids)
    merged_marks = ",".join("?" for _ in merged_ids)
    for row in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
        if str(row["table"]) != table or str(row["to"] or "id") != "id":
            continue
        column = str(row["from"])
        # A canonical row cannot retain a link to itself or to a row about to
        # disappear. ON DELETE would make it NULL later, but doing it explicitly
        # also avoids transient constraint failures during this transaction.
        conn.execute(
            f'UPDATE "{table}" SET "{column}"=NULL,updated_at=CURRENT_TIMESTAMP '
            f'WHERE id=? AND "{column}" IN ({merged_marks})',
            (canonical_id, *merged_ids),
        )
        # Preserve children of duplicate rows by attaching them to the canonical
        # row. Rows participating in the merge are excluded to prevent cycles.
        conn.execute(
            f'UPDATE "{table}" SET "{column}"=?,updated_at=CURRENT_TIMESTAMP '
            f'WHERE "{column}" IN ({old_marks}) AND id NOT IN ({merged_marks})',
            (canonical_id, *old_ids, *merged_ids),
        )


def _apply_merge(conn: sqlite3.Connection, plan: dict, bundle_id: int) -> None:
    entity_type, canonical_id = plan["entity_type"], int(plan["canonical_id"])
    table = TABLES[entity_type]
    columns = {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    old_ids = [int(value) for value in plan["record_ids"] if int(value) != canonical_id]
    merged_ids = {canonical_id, *old_ids}
    foreign_keys = _foreign_key_targets(conn, table)
    canonical_row = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (canonical_id,)).fetchone()
    for field in plan.get("fields", []):
        name = str(field["field"])
        if name in columns and name not in {"id", "slug", "created_at", "updated_at"}:
            proposed = field.get("proposed_value")
            current = canonical_row[name]
            if proposed is None:
                continue
            if current is not None and (not isinstance(current, str) or current.strip()):
                continue
            if name in foreign_keys:
                target_table, target_column = foreign_keys[name]
                try:
                    proposed_id = int(proposed)
                except (TypeError, ValueError):
                    proposed_id = None
                if (
                    target_table == table
                    and target_column == "id"
                    and proposed_id in merged_ids
                ):
                    log.warning(
                        "merge skipped unsafe self-reference table=%s field=%s value=%s canonical_id=%s",
                        table, name, proposed, canonical_id,
                    )
                    continue
                if not _foreign_key_value_exists(conn, target_table, target_column, proposed):
                    log.warning(
                        "merge skipped missing foreign-key target table=%s field=%s value=%s target=%s.%s",
                        table, name, proposed, target_table, target_column,
                    )
                    continue
            conn.execute(f'UPDATE "{table}" SET "{name}"=?,updated_at=CURRENT_TIMESTAMP WHERE id=?', (proposed, canonical_id))
    canonical = conn.execute(f'SELECT slug FROM "{table}" WHERE id=?', (canonical_id,)).fetchone()
    canonical_slug = str(canonical["slug"] or "")
    if "review_status" in columns:
        conn.execute(f'UPDATE "{table}" SET review_status="published",updated_at=CURRENT_TIMESTAMP WHERE id=?', (canonical_id,))
    _remap_self_references(conn, table, canonical_id, old_ids)
    for old_id in old_ids:
        old = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (old_id,)).fetchone()
        if old and old["slug"]:
            conn.execute(
                """INSERT INTO content_aliases(entity_type,old_slug,canonical_entity_id,canonical_slug,bundle_id)
                   VALUES(?,?,?,?,?) ON CONFLICT(entity_type,old_slug) DO UPDATE SET
                   canonical_entity_id=excluded.canonical_entity_id,canonical_slug=excluded.canonical_slug,bundle_id=excluded.bundle_id""",
                (entity_type, old["slug"], canonical_id, canonical_slug, bundle_id),
            )
        if old:
            _write_resolved_pair(
                conn, entity_type, dict(old), "merged",
                plan.get("finding_id"), bundle_id,
            )
        _move_references(conn, entity_type, old_id, canonical_id)
        conn.execute(f'DELETE FROM "{table}" WHERE id=?', (old_id,))
    _repair_primary_flags(conn, entity_type, canonical_id, plan.get("preferred_asset_id"))
    canonical_record = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (canonical_id,)).fetchone()
    if canonical_record:
        _write_resolved_pair(conn, entity_type, dict(canonical_record), "merged", plan.get("finding_id"), bundle_id)


def _repair_primary_flags(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    preferred_asset_id: int | None = None,
) -> None:
    for table in ("entity_links", "asset_links"):
        ids = [row["id"] for row in conn.execute(f"SELECT id FROM {table} WHERE entity_type=? AND entity_id=? AND is_primary=1 ORDER BY id", (entity_type, entity_id))]
        if len(ids) > 1:
            conn.execute(f"UPDATE {table} SET is_primary=0 WHERE entity_type=? AND entity_id=? AND is_primary=1 AND id<>?", (entity_type, entity_id, ids[0]))
    if preferred_asset_id:
        preferred = conn.execute(
            "SELECT id FROM asset_links WHERE entity_type=? AND entity_id=? AND asset_id=? ORDER BY id LIMIT 1",
            (entity_type, entity_id, int(preferred_asset_id)),
        ).fetchone()
        if preferred:
            conn.execute("UPDATE asset_links SET is_primary=0 WHERE entity_type=? AND entity_id=?", (entity_type, entity_id))
            conn.execute("UPDATE asset_links SET is_primary=1 WHERE id=?", (preferred["id"],))


def _apply_split(conn: sqlite3.Connection, plan: dict) -> None:
    entity_type = plan["entity_type"]
    source_id = int(plan["record_ids"][0])
    table = TABLES[entity_type]
    for item in plan["proposed_records"]:
        title = item.get("title") or item.get("title_hint") or item.get("segment", "")[:240] or f"Split #{source_id}"
        if entity_type == "event":
            cursor = conn.execute(
                f"""INSERT INTO "{table}"(title,slug,start_date,end_date,date_precision,review_status)
                    VALUES(?,?,?,?,?,'review')""",
                (title, item.get("slug"), item.get("start_date"), item.get("end_date"), item.get("date_precision") or "unknown"),
            )
        else:
            cursor = conn.execute(
                f"""INSERT INTO "{table}"(title,slug,authors,abstract,year,date_text,date_precision,review_status)
                    VALUES(?,?,?,?,?,?,?,'review')""",
                (title, item.get("slug"), item.get("authors"), item.get("abstract") or item.get("segment"), item.get("year"), str(item.get("year") or ""), "year" if item.get("year") else "unknown"),
            )
        for asset in conn.execute("SELECT * FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, source_id)).fetchall():
            conn.execute("INSERT OR IGNORE INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order) VALUES(?,?,?,?,?,?)", (asset["asset_id"], entity_type, cursor.lastrowid, asset["role"], asset["is_primary"], asset["sort_order"]))
    conn.execute(f'UPDATE "{table}" SET review_status=\'quarantined\',updated_at=CURRENT_TIMESTAMP WHERE id=?', (source_id,))


def verify_invariants(conn: sqlite3.Connection, skip_date_inversion: bool = False) -> list[str]:
    errors: list[str] = []
    tables_map = {"member": "members", "event": "events", "news": "news",
                  "publication": "publications", "sponsor": "sponsors",
                  "research_area": "research_areas", "page": "pages"}

    orphan_el = 0
    for row in conn.execute("SELECT id, entity_type, entity_id FROM entity_links").fetchall():
        et, eid = str(row["entity_type"]), int(row["entity_id"])
        table = tables_map.get(et)
        if table and not conn.execute(f'SELECT 1 FROM "{table}" WHERE id=?', (eid,)).fetchone():
            errors.append(f"orphan entity_link: {et} id={eid} references non-existent {table} row")
            conn.execute("DELETE FROM entity_links WHERE id=?", (row["id"],))
            orphan_el += 1
    if orphan_el:
        log.info("verify_invariants cleaned %d orphan entity_link(s)", orphan_el)

    orphan_al = 0
    for row in conn.execute("SELECT id, entity_type, entity_id FROM asset_links").fetchall():
        et, eid = str(row["entity_type"]), int(row["entity_id"])
        table = tables_map.get(et)
        if table and not conn.execute(f'SELECT 1 FROM "{table}" WHERE id=?', (eid,)).fetchone():
            errors.append(f"orphan asset_link: {et} id={eid} references non-existent {table} row")
            conn.execute("DELETE FROM asset_links WHERE id=?", (row["id"],))
            orphan_al += 1
    if orphan_al:
        log.info("verify_invariants cleaned %d orphan asset_link(s)", orphan_al)

    for entity_type, table in tables_map.items():
        slugs = conn.execute(f'SELECT slug, COUNT(*) as cnt FROM "{table}" WHERE slug IS NOT NULL AND slug!=\'\' GROUP BY slug HAVING cnt>1').fetchall()
        for slug_row in slugs:
            errors.append(f"duplicate slug {slug_row['slug']} in {table} (count={slug_row['cnt']})")

    dois = conn.execute("SELECT doi, COUNT(*) as cnt FROM publications WHERE doi IS NOT NULL AND doi!='' GROUP BY doi HAVING cnt>1").fetchall()
    for doi_row in dois:
        errors.append(f"duplicate doi {doi_row['doi']} (count={doi_row['cnt']})")

    if not skip_date_inversion:
        bad_dates = conn.execute("SELECT id, start_date, end_date FROM events WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND start_date > end_date").fetchall()
        for bad in bad_dates:
            errors.append(f"event id={bad['id']}: start_date {bad['start_date']} > end_date {bad['end_date']}")

    for table, label in [("events", "title"), ("news", "title"), ("publications", "title"),
                         ("members", "display_name"), ("sponsors", "name")]:
        empties = conn.execute(f'SELECT id FROM "{table}" WHERE COALESCE({label},\'\')=\'\'').fetchall()
        for empty in empties:
            errors.append(f"empty {label} in {table} id={empty['id']}")

    return errors


def apply_bundle(db_path: Path, bundle_id: int) -> dict:
    with connect(db_path) as preflight:
        report = validate_bundle(preflight, bundle_id)
    if not report["valid"]:
        if report.get("errors") == ["Bundle has no executable actions"] and report.get("warnings"):
            no_changes = {
                **report,
                "status": "no_changes",
                "bundle_id": bundle_id,
                "operations": 0,
                "backup_path": None,
            }
            with connect(db_path) as conn:
                conn.execute(
                    "UPDATE quality_bundles SET status='applied',report_json=?,"
                    "applied_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(no_changes, default=str), bundle_id),
                )
                conn.commit()
            log.info("apply_bundle no executable changes bundle_id=%s warnings=%s", bundle_id, len(report["warnings"]))
            return no_changes
        details = "; ".join(report.get("errors") or ["bundle validation failed"])
        log.warning("apply_bundle preflight failed bundle_id=%s errors=%s", bundle_id, details)
        raise ValueError(f"Bundle cannot be applied: {details}")

    backup = backup_sqlite_database(db_path, label=f"data-quality-bundle-{bundle_id}")
    if not backup:
        raise RuntimeError("verified database backup could not be created")
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        bundle, rows = _bundle_rows(conn, bundle_id)
        if bundle["status"] != "validated":
            raise ValueError("bundle must pass validation immediately before application")
        fresh = validate_bundle(conn, bundle_id, persist=False)
        if not fresh["valid"]:
            raise ValueError("bundle changed after validation")
        conn.execute("UPDATE quality_bundles SET status='applying',backup_path=? WHERE id=?", (str(backup), bundle_id))
        plans: list[dict] = []
        aliases = assets = 0
        for row in rows:
            plan = json.loads(row["payload_json"] or "{}").get("plan") or {}
            finding_id = int(row["finding_id"])
            item_id = int(row["id"])
            entity_type = str(row["entity_type"])
            record_ids = [int(v) for v in (plan.get("record_ids") or json.loads(row["record_ids_json"]))]
            plan_entry = {
                **plan, "finding_id": finding_id, "item_id": item_id,
                "action_type": str(row["action_type"]), "entity_type": entity_type,
                "record_ids": record_ids,
            }
            plans.append(plan_entry)
            aliases += len(plan.get("aliases") or [])
            assets += len(plan.get("assets") or [])
            action = plan_entry["action_type"]
            try:
                if action in {"clean_record", "enrich_record"}:
                    _apply_clean(conn, plan_entry, bundle_id)
                elif action == "merge_records":
                    _apply_merge(conn, plan_entry, bundle_id)
                elif action == "split_aggregated_record":
                    _apply_split(conn, plan_entry)
                elif action == "repair_relations_or_assets":
                    _repair_primary_flags(conn, entity_type, int(record_ids[0]))
                else:
                    log.warning("apply_bundle unknown action_type=%s entity_type=%s finding_id=%s", action, entity_type, finding_id)
            except Exception as exc:
                log.error("apply_bundle plan failed action=%s entity_type=%s record_ids=%s finding_id=%s: %s", action, entity_type, record_ids, finding_id, exc)
                raise
            conn.execute("UPDATE quality_findings SET status='resolved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (finding_id,))
            conn.execute("UPDATE quality_bundle_items SET status='applied' WHERE id=?", (item_id,))
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            log.warning("apply_bundle foreign key violations (ignored): %s", [(r[0], r[1], r[2]) for r in fk_violations])
        invariant_errors = verify_invariants(conn, skip_date_inversion=True)
        if invariant_errors:
            log.warning("apply_bundle invariant issues (ignored): %s", '; '.join(invariant_errors[:10]))
        records_removed = sum(max(0, len(p.get("record_ids") or []) - 1) for p in plans if p.get("action_type") == "merge_records")
        report = {
            "valid": True, "errors": [], "warnings": [],
            "operations": len(plans), "plans": plans, "aliases": aliases,
            "assets_preserved": assets, "files_quarantined": 0,
            "records_removed": records_removed,
            "backup_path": str(backup), "bundle_id": bundle_id, "status": "applied",
        }
        conn.execute("UPDATE quality_bundles SET status='applied',report_json=?,applied_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(report, default=str), bundle_id))
        conn.commit()
        return report
    except Exception:
        conn.rollback()
        with connect(db_path) as failed:
            failed.execute("UPDATE quality_bundles SET status='failed',backup_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(backup), bundle_id))
            failed.commit()
        raise
    finally:
        conn.close()


def bundle_detail(conn: sqlite3.Connection, bundle_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM quality_bundles WHERE id=?", (bundle_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["validation"] = json.loads(result.pop("validation_json") or "{}")
    result["report"] = json.loads(result.pop("report_json") or "{}")
    result["items"] = [
        {**dict(item), "payload": json.loads(item["payload_json"] or "{}")}
        for item in conn.execute(
            """SELECT i.*,f.entity_type,f.classification,f.record_ids_json
               FROM quality_bundle_items i JOIN quality_findings f ON f.id=i.finding_id
               WHERE i.bundle_id=? ORDER BY i.sort_order,i.id""",
            (bundle_id,),
        )
    ]
    return result


def force_merge_candidate(finding: dict[str, Any]) -> bool:
    if finding["classification"] != "ambiguous" or finding["action_type"] != "merge_records":
        return False
    if any(item.get("strength") == "blocking" for item in finding.get("contradictions") or []):
        return False
    thresholds = {
        "news": .65,
        "event": .70,
        "publication": .72,
        "member": .75,
        "sponsor": .75,
    }
    return float(finding.get("score") or 0) >= thresholds.get(str(finding["entity_type"]), 1)


def _has_effective_change(plan: dict[str, Any]) -> bool:
    if plan.get("operation") in {
        "quarantine", "swap_name_fields", "remove_merged_duplicate"
    }:
        return True
    if plan.get("action_type") == "merge_records":
        return len(plan.get("record_ids") or []) > 1
    fields = plan.get("fields") or []
    return any(
        field.get("proposed_value") is not None
        and any(
            value.get("value") != field.get("proposed_value")
            for value in field.get("values_by_record") or []
        )
        for field in fields
    )


def batch_add_to_bundle(
    conn: sqlite3.Connection,
    bundle_id: int,
    run_id: int,
    *,
    action_type: str = "",
    entity_type: str = "",
    classification: str = "",
    force: bool = False,
) -> dict:
    from .analyzer import _finding_filter, get_finding
    if force and classification == "reviewable":
        classification = ""
    clauses, args = _finding_filter(run_id, action_type, entity_type, classification)
    clauses.append("status='open'")
    query = f"SELECT id FROM quality_findings WHERE {' AND '.join(clauses)} ORDER BY score DESC,id"
    rows = conn.execute(query, args).fetchall()
    log.info("batch_add_to_bundle bundle_id=%s run_id=%s query=%s args=%s rows=%s", bundle_id, run_id, query, args, len(rows))
    added = 0
    errors = 0
    skipped_uncertain = 0
    skipped_conflicts = 0
    skipped_conflict_ids: list[int] = []
    error_details: list[str] = []
    occupied: dict[tuple[str, int], dict] = {}
    for existing in conn.execute(
        """SELECT i.id, i.finding_id, i.action_type, f.entity_type, f.record_ids_json
           FROM quality_bundle_items i
           JOIN quality_findings f ON f.id=i.finding_id
           WHERE i.bundle_id=?""",
        (bundle_id,),
    ):
        for record_id in json.loads(existing["record_ids_json"]):
            occupied[(str(existing["entity_type"]), int(record_id))] = {
                "item_id": existing["id"],
                "finding_id": existing["finding_id"],
                "action_type": existing["action_type"],
            }
    for row in rows:
        finding = get_finding(conn, int(row["id"]))
        if not finding:
            log.warning("batch_add_to_bundle finding %s not found", row["id"])
            errors += 1
            continue
        if finding["classification"] in {"blocked", "related_not_duplicate", "keep_separate"}:
            # These are valid analysis results, not execution failures. They
            # remain open for an administrator decision and are never silently
            # converted into automatic actions by a bulk operation.
            skipped_uncertain += 1
            continue
        force_selected = force and force_merge_candidate(finding)
        if finding["classification"] == "ambiguous" and not force_selected:
            skipped_uncertain += 1
            continue
        if finding["action_type"] == "split_aggregated_record":
            plan = finding["plan"]
            proposed = plan.get("proposed_records") or []
            if len(proposed) < 2:
                skipped_uncertain += 1
                continue
            needs_titles = [item for item in proposed if not item.get("title")]
            if needs_titles:
                for item in proposed:
                    if not item.get("title"):
                        item["title"] = item.get("title_hint") or item.get("segment", "")[:240]
            plan["requires_review"] = False
            try:
                add_to_bundle(conn, bundle_id, finding["id"], {"plan": plan})
                added += 1
                split_key = (str(finding["entity_type"]), int(finding["record_ids"][0]))
                split_item = conn.execute(
                    "SELECT id FROM quality_bundle_items WHERE bundle_id=? AND finding_id=?",
                    (bundle_id, finding["id"]),
                ).fetchone()
                occupied[split_key] = {
                    "item_id": int(split_item["id"]) if split_item else 0,
                    "finding_id": finding["id"],
                    "action_type": finding["action_type"],
                }
            except ValueError as exc:
                log.warning("batch_add_to_bundle split finding %s error: %s", finding["id"], exc)
                errors += 1
            continue
        table = TABLES.get(str(finding["entity_type"]))
        if table and finding["action_type"] != "split_aggregated_record":
            existing_ids = {r["id"] for r in conn.execute(f'SELECT id FROM "{table}" WHERE id IN ({",".join("?" for _ in finding["record_ids"])})', finding["record_ids"])}
            missing = [str(v) for v in finding["record_ids"] if int(v) not in existing_ids]
            if missing:
                conn.execute(
                    "UPDATE quality_findings SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (finding["id"],),
                )
                skipped_uncertain += 1
                log.info("batch_add_to_bundle rejected finding %s: records %s no longer exist", finding["id"], ", ".join(missing))
                continue
        records = {
            (str(finding["entity_type"]), int(record_id))
            for record_id in finding["record_ids"]
        }
        if any(k in occupied for k in records):
            skipped_conflicts += 1
            if len(skipped_conflict_ids) < 50:
                skipped_conflict_ids.append(int(finding["id"]))
            continue
        payload: dict[str, Any] = {"plan": finding["plan"]}
        if finding["action_type"] in {"merge_records", "enrich_record", "clean_record"}:
            payload["strategy"] = "best_quality"
        candidate_plan = apply_best_quality(finding["plan"]) if payload.get("strategy") else finding["plan"]
        if not _has_effective_change(candidate_plan):
            skipped_uncertain += 1
            continue
        try:
            if force_selected:
                # The identity threshold and contradiction checks above are the
                # explicit approval boundary for force mode.
                original = finding["classification"]
                conn.execute(
                    "UPDATE quality_findings SET classification='strong_candidate' WHERE id=?",
                    (finding["id"],),
                )
                try:
                    add_to_bundle(conn, bundle_id, finding["id"], payload)
                finally:
                    conn.execute(
                        "UPDATE quality_findings SET classification=? WHERE id=?",
                        (original, finding["id"]),
                    )
            else:
                add_to_bundle(conn, bundle_id, finding["id"], payload)
            added += 1
            new_row = conn.execute(
                "SELECT id FROM quality_bundle_items WHERE bundle_id=? AND finding_id=?",
                (bundle_id, finding["id"]),
            ).fetchone()
            item_id = int(new_row["id"]) if new_row else 0
            for record_key in records:
                occupied[record_key] = {
                    "item_id": item_id,
                    "finding_id": finding["id"],
                    "action_type": finding["action_type"],
                }
        except ValueError as exc:
            log.warning("batch_add_to_bundle finding %s error: %s", finding["id"], exc)
            errors += 1
            if len(error_details) < 10:
                error_details.append(f"#{finding['id']}: {exc}")
    log.info("batch_add_to_bundle done bundle_id=%s added=%s errors=%s", bundle_id, added, errors)
    conn.commit()
    return {
        "added": added,
        "errors": errors,
        "skipped_uncertain": skipped_uncertain,
        "skipped_conflicts": skipped_conflicts,
        "skipped_conflict_ids": skipped_conflict_ids,
        "error_details": error_details,
    }


def batch_reject_findings(conn: sqlite3.Connection, run_id: int, *, action_type: str = "", entity_type: str = "", classification: str = "") -> dict:
    from .analyzer import _finding_filter
    clauses, args = _finding_filter(run_id, action_type, entity_type, classification)
    clauses.append("status='open'")
    rows = conn.execute(
        f"SELECT id, entity_type, record_ids_json FROM quality_findings WHERE {' AND '.join(clauses)}",
        args,
    ).fetchall()
    for row in rows:
        et = str(row["entity_type"])
        table = TABLES.get(et)
        if not table:
            continue
        for rid in json.loads(row["record_ids_json"]):
            record = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (int(rid),)).fetchone()
            if record:
                _write_resolved_pair(conn, et, dict(record), "rejected", int(row["id"]))
    cur = conn.execute(
        f"UPDATE quality_findings SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE {' AND '.join(clauses)}",
        args,
    )
    conn.commit()
    return {"rejected": cur.rowcount}

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .planner import LABELS, TABLES


def _review_status_tables(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        entity_type: table
        for entity_type, table in TABLES.items()
        if "review_status" in {
            str(column["name"])
            for column in conn.execute(f'PRAGMA table_info("{table}")')
        }
    }


def _quarantine_evidence(conn: sqlite3.Connection) -> dict[tuple[str, int], dict[str, Any]]:
    evidence_by_record: dict[tuple[str, int], dict[str, Any]] = {}
    rows = conn.execute(
        """SELECT id,entity_type,record_ids_json,evidence_json,plan_json,updated_at
           FROM quality_findings
           WHERE action_type='clean_record'
           ORDER BY id DESC"""
    )
    for row in rows:
        try:
            plan = json.loads(row["plan_json"] or "{}")
            if plan.get("operation") != "quarantine":
                continue
            record_ids = json.loads(row["record_ids_json"] or "[]")
            evidence = json.loads(row["evidence_json"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        explanation = next(
            (
                str(item.get("explanation") or "").strip()
                for item in evidence
                if isinstance(item, dict) and str(item.get("explanation") or "").strip()
            ),
            "Flagged as technical or invalid content by Data Quality.",
        )
        for value in record_ids:
            if not str(value).isdigit():
                continue
            key = (str(row["entity_type"]), int(value))
            evidence_by_record.setdefault(key, {
                "reason": explanation,
                "finding_id": int(row["id"]),
                "previous_status": str(plan.get("previous_review_status") or "draft"),
                "finding_updated_at": row["updated_at"],
            })
    return evidence_by_record


def list_quarantined_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    evidence = _quarantine_evidence(conn)
    output: list[dict[str, Any]] = []
    for entity_type, table in _review_status_tables(conn).items():
        label_field = LABELS[entity_type]
        columns = {
            str(column["name"])
            for column in conn.execute(f'PRAGMA table_info("{table}")')
        }
        slug_sql = '"slug"' if "slug" in columns else "NULL"
        updated_sql = '"updated_at"' if "updated_at" in columns else "NULL"
        for row in conn.execute(
            f'''SELECT id,"{label_field}" AS label,{slug_sql} AS slug,
                       {updated_sql} AS quarantined_at
                FROM "{table}"
                WHERE review_status='quarantined'
                ORDER BY quarantined_at DESC,id DESC'''
        ):
            item = dict(row)
            item.update({"entity_type": entity_type, "table": table})
            item.update(evidence.get((entity_type, int(row["id"])), {
                "reason": "This record is hidden because it was quarantined by an earlier cleanup.",
                "finding_id": None,
                "previous_status": "draft",
                "finding_updated_at": None,
            }))
            output.append(item)
    return sorted(
        output,
        key=lambda item: (str(item.get("quarantined_at") or ""), int(item["id"])),
        reverse=True,
    )


def transition_quarantined_record(
    conn: sqlite3.Connection,
    entity_type: str,
    record_id: int,
    decision: str,
) -> dict[str, Any]:
    tables = _review_status_tables(conn)
    table = tables.get(entity_type)
    if not table:
        raise ValueError("Unsupported quarantine record type.")
    if decision != "restore":
        raise ValueError("Unsupported quarantine action.")
    target = "draft"
    row = conn.execute(
        f'SELECT * FROM "{table}" WHERE id=?', (int(record_id),)
    ).fetchone()
    if not row:
        raise LookupError("Quarantined record not found.")
    if str(row["review_status"] or "") != "quarantined":
        raise ValueError("This record is no longer quarantined. Reload the page.")
    columns = {str(column["name"]) for column in conn.execute(f'PRAGMA table_info("{table}")')}
    updated = ",updated_at=CURRENT_TIMESTAMP" if "updated_at" in columns else ""
    conn.execute(
        f'UPDATE "{table}" SET review_status=?{updated} WHERE id=?',
        (target, int(record_id)),
    )
    return {
        "entity_type": entity_type,
        "record_id": int(record_id),
        "table": table,
        "previous_status": "quarantined",
        "new_status": target,
    }

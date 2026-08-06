from __future__ import annotations

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
from ..db.connection import table_exists
from ..db.migrations import SCHEMA_VERSION
from .assets import resolve_db_asset_path
from .data_quality.normalizers import stable_fingerprint
from .importers import TYPE_TO_TABLE, import_jsonl

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
CANONICAL_FORMAT_VERSION = 1
SUPPORTED_FORMAT_VERSIONS = {1, 2}
QUALITY_FINGERPRINT_ACTIONS = {
    "", "aggregated_event", "clean_record", "date_placeholder", "invalid_record",
    "inverted_date_range", "junk_record", "merge_records", "missing_asset_file",
    "missing_date", "multiple_primary_links", "name_inversion", "page_fragment",
    "placeholder_title", "split_aggregated_record",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def scope_options() -> list[dict[str, Any]]:
    return [{"key": key, **meta} for key, meta in EXPORT_SCOPES.items()]


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
            "exported_at": _utc_now(),
            "format": PORTABLE_FORMAT,
            "format_version": PORTABLE_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
        },
        "records": records,
    }


def canonical_bundle_to_zip(
    conn: sqlite3.Connection,
    assets_dir: Path,
    *,
    app_version: str = "",
) -> bytes:
    """Build the canonical JSONL v2 package used by SCRAPERS/ and DATABASE/.

    The package intentionally contains only portable records, a manifest and
    managed asset files. Runtime settings, metrics, sessions and operational
    state are not exported.
    """
    records = _records_for_scope(conn, "all")
    records_payload = _records_to_jsonl(records)
    referenced_paths = {
        str(asset.get("path") or "").strip()
        for record in records
        for asset in (record.get("assets") or [])
        if isinstance(asset, dict) and str(asset.get("path") or "").strip()
    }
    asset_rows = [
        row for row in _asset_rows(conn)
        if str(row.get("path") or "").strip() in referenced_paths
    ]
    manifest: dict[str, Any] = {
        "format": CANONICAL_FORMAT,
        "format_version": CANONICAL_FORMAT_VERSION,
        "generated_at": _utc_now(),
        "scope": "all",
        "records": len(records),
        "counts": _record_counts(records),
        "records_sha256": hashlib.sha256(records_payload).hexdigest(),
        "app_version": app_version,
        "files": [],
    }
    output = BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(ZIP_RECORDS_NAME, records_payload)
        for asset in asset_rows:
            db_path = str(asset.get("path") or "").strip()
            if not db_path:
                continue
            source = resolve_db_asset_path(assets_dir, db_path)
            if not source.is_file():
                continue
            relative = db_path.removeprefix("assets/")
            archive_path = _validate_asset_archive_path(f"assets/{relative}")
            if archive_path in seen:
                continue
            seen.add(archive_path)
            size = source.stat().st_size
            checksum = _file_sha256(source)
            archive.write(source, archive_path)
            manifest["files"].append({
                "path": relative,
                "archive_path": archive_path,
                "bytes": size,
                "size": size,
                "sha256": checksum,
            })
        archive.writestr(
            ZIP_MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        )
    return output.getvalue()


def bundle_to_zip(conn: sqlite3.Connection, scope: str, assets_dir: Path, *, app_version: str = "") -> bytes:
    bundle = build_export_bundle(conn, scope)
    records = bundle.get("records") or []
    asset_rows = _asset_rows_for_scope(conn, scope, records)
    records_payload = _records_to_jsonl(records)
    durable_state = _durable_state(conn) if scope == "all" else None
    state_payload = (
        json.dumps(durable_state, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        if durable_state is not None
        else None
    )
    manifest = {
        "format": PORTABLE_FORMAT,
        "format_version": PORTABLE_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
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
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
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
            zf.write(path, archive_path)
            manifest["files"].append({
                "path": db_path,
                "archive_path": archive_path,
                "size": path.stat().st_size,
                "sha256": _file_sha256(path),
            })
        zf.writestr(ZIP_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return out.getvalue()


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
        )
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
        if not dry_run:
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
    _restore_unlinked_assets(conn, state.get("assets") or [], assets_dir, packaged_assets_dir, restored)
    conn.commit()
    return restored


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
               al.role, a.kind, a.caption, a.alt_text, al.is_primary, al.sort_order
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
    if scope in {"assets", "all"}:
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        manifest = json.loads(zf.read(ZIP_MANIFEST_NAME).decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    package_format = manifest.get("format")
    if package_format not in (None, PORTABLE_FORMAT, CANONICAL_FORMAT):
        raise ValueError(f"Unsupported export format: {package_format!r}")
    format_version = manifest.get("format_version")
    if format_version is not None and format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(f"Unsupported export format version: {format_version!r}")
    schema_version = manifest.get("schema_version")
    if schema_version is not None and (
        not isinstance(schema_version, int) or schema_version < 1 or schema_version > SCHEMA_VERSION
    ):
        raise ValueError(f"Unsupported database schema version: {schema_version!r}")
    records_sha256 = manifest.get("records_sha256")
    if records_sha256 is not None and not _valid_sha256(records_sha256):
        raise ValueError("manifest.records_sha256 must be a SHA-256 digest")
    state_sha256 = manifest.get("state_sha256")
    if state_sha256 is not None and not _valid_sha256(state_sha256):
        raise ValueError("manifest.state_sha256 must be a SHA-256 digest")
    scope = str(manifest.get("scope") or "").strip()
    if scope not in EXPORT_SCOPES:
        raise ValueError(f"Unsupported ZIP scope: {scope!r}")
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
    return manifest


def _read_durable_state(
    zf: zipfile.ZipFile, manifest: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    raw = zf.read(ZIP_STATE_NAME)
    expected_hash = manifest.get("state_sha256")
    if expected_hash and hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("state.json failed integrity verification")
    try:
        state = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("state.json is not valid JSON") from exc
    if not isinstance(state, dict):
        raise ValueError("state.json must contain an object")
    allowed = {
        "roles", "settings", "assets", "metrics_daily", "merge_exclusions",
        "resolved_pairs", "quality_decisions", "entity_relations",
        "join_requests", "content_aliases",
    }
    unexpected = sorted(set(state) - allowed)
    if unexpected:
        raise ValueError(f"state.json contains unsupported sections: {', '.join(unexpected)}")
    normalized: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for key in allowed:
        value = state.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"state.json section {key!r} must be a list of objects")
        normalized[key] = value
        total += len(value)
    if total > Config.IMPORT_MAX_JSONL_LINES * 5:
        raise ValueError("state.json contains too many records")
    declared_counts = manifest.get("state_counts")
    actual_counts = {key: len(value) for key, value in normalized.items()}
    if declared_counts is not None:
        if not isinstance(declared_counts, dict) or any(
            not isinstance(key, str) or not isinstance(value, int) or value < 0
            for key, value in declared_counts.items()
        ):
            raise ValueError("manifest.state_counts must map section names to non-negative integers")
        if declared_counts != actual_counts:
            raise ValueError("manifest.state_counts does not match state.json")
    return normalized


def _manifest_asset_paths(manifest: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for idx, item in enumerate(manifest.get("files") or [], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"manifest.files[{idx}] must be an object")
        archive_path = _validate_asset_archive_path(str(item.get("archive_path") or ""))
        if item.get("size") is not None and (
            not isinstance(item["size"], int) or item["size"] < 0
        ):
            raise ValueError(f"manifest.files[{idx}].size must be a non-negative integer")
        if item.get("sha256") is not None and not _valid_sha256(item["sha256"]):
            raise ValueError(f"manifest.files[{idx}].sha256 must be a SHA-256 digest")
        paths.add(archive_path)
    return paths


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

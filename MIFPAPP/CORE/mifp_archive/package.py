from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .database import columns, table_exists
from .migrate import migrate
from .registry import BY_TYPE, ENTITY_SPECS

ARCHIVE_FORMAT = "mifp-content-archive"
ARCHIVE_FORMAT_VERSION = 1
SUPPORTED_VERSIONS = {1}
MANIFEST = "manifest.json"
README = "README.txt"
CHECKSUMS = "checksums.sha256"

SECTION_FILES = {
    "entities": "data/entities.jsonl",
    "assets": "data/assets.jsonl",
    "asset_links": "data/asset-links.jsonl",
    "entity_links": "data/entity-links.jsonl",
    "relations": "data/relations.jsonl",
    "source_systems": "provenance/source-systems.jsonl",
    "source_runs": "provenance/source-runs.jsonl",
    "source_records": "provenance/source-records.jsonl",
    "canonical_mappings": "provenance/canonical-mappings.jsonl",
    "aliases": "quality/aliases.jsonl",
    "merge_exclusions": "quality/merge-exclusions.jsonl",
    "resolved_pairs": "quality/resolved-pairs.jsonl",
}

_MAX_FILES = 100_000
_MAX_UNPACKED = 4 * 1024 * 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _jsonl(items: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")
        for item in items
    )


def _clean(row: dict[str, Any], *, remove: set[str] | None = None) -> dict[str, Any]:
    ignored = {"id"} | (remove or set())
    return {key: value for key, value in row.items() if key not in ignored and value is not None}


def _entity_maps(conn: sqlite3.Connection) -> tuple[dict[tuple[str, int], str], dict[str, tuple[str, int]]]:
    by_local: dict[tuple[str, int], str] = {}
    by_uid: dict[str, tuple[str, int]] = {}
    for spec in ENTITY_SPECS:
        if not table_exists(conn, spec.table):
            continue
        for row in conn.execute(f'SELECT id,uid FROM "{spec.table}"'):
            uid = str(row["uid"] or "")
            if uid:
                by_local[(spec.type_name, int(row["id"]))] = uid
                by_uid[uid] = (spec.type_name, int(row["id"]))
    return by_local, by_uid


def _asset_maps(conn: sqlite3.Connection) -> tuple[dict[int, str], dict[str, int]]:
    if not table_exists(conn, "assets"):
        return {}, {}
    by_local: dict[int, str] = {}
    by_uid: dict[str, int] = {}
    for row in conn.execute("SELECT id,uid FROM assets"):
        uid = str(row["uid"] or "")
        if uid:
            by_local[int(row["id"])] = uid
            by_uid[uid] = int(row["id"])
    return by_local, by_uid


def _entities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    role_names = {int(row["id"]): str(row["name"]) for row in conn.execute("SELECT id,name FROM roles")} if table_exists(conn, "roles") else {}
    event_uids = {int(row["id"]): str(row["uid"]) for row in conn.execute("SELECT id,uid FROM events")} if table_exists(conn, "events") else {}
    output: list[dict[str, Any]] = []
    for spec in ENTITY_SPECS:
        if not table_exists(conn, spec.table):
            continue
        for raw in conn.execute(f'SELECT * FROM "{spec.table}" ORDER BY id'):
            row = dict(raw)
            local_id = int(row.pop("id"))
            uid = str(row.pop("uid") or "")
            if not uid:
                raise ValueError(f"{spec.table} row {local_id} has no portable uid")
            row.pop("created_at", None)
            row.pop("updated_at", None)
            if spec.type_name == "member":
                role_id = row.pop("role_id", None)
                if role_id in role_names:
                    row["role"] = role_names[role_id]
            if spec.type_name == "event":
                parent_id = row.pop("parent_event_id", None)
                if parent_id in event_uids:
                    row["parent_event_uid"] = event_uids[parent_id]
            output.append({"type": spec.type_name, "uid": uid, "data": {k: v for k, v in row.items() if v is not None}})
    return output


def _assets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "assets"):
        return []
    return [_clean(dict(row), remove={"created_at", "updated_at"}) for row in conn.execute("SELECT * FROM assets ORDER BY id")]


def _asset_links(conn: sqlite3.Connection, entity_uids: dict[tuple[str, int], str], asset_uids: dict[int, str]) -> list[dict[str, Any]]:
    if not table_exists(conn, "asset_links"):
        return []
    output = []
    for row in conn.execute("SELECT * FROM asset_links ORDER BY id"):
        entity_uid = entity_uids.get((str(row["entity_type"]), int(row["entity_id"])))
        asset_uid = asset_uids.get(int(row["asset_id"]))
        if entity_uid and asset_uid:
            output.append({
                "entity_type": row["entity_type"], "entity_uid": entity_uid,
                "asset_uid": asset_uid, "role": row["role"],
                "is_primary": row["is_primary"], "sort_order": row["sort_order"],
            })
    return output


def _entity_links(conn: sqlite3.Connection, entity_uids: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
    if not table_exists(conn, "entity_links"):
        return []
    output = []
    for row in conn.execute("SELECT * FROM entity_links ORDER BY id"):
        uid = entity_uids.get((str(row["entity_type"]), int(row["entity_id"])))
        if uid:
            item = _clean(dict(row), remove={"created_at", "entity_id"})
            item["entity_uid"] = uid
            output.append(item)
    return output


def _relations(conn: sqlite3.Connection, entity_uids: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
    if not table_exists(conn, "entity_relations"):
        return []
    output = []
    for row in conn.execute("SELECT * FROM entity_relations ORDER BY id"):
        source_uid = entity_uids.get((str(row["source_type"]), int(row["source_id"])))
        target_uid = entity_uids.get((str(row["target_type"]), int(row["target_id"])))
        if source_uid and target_uid:
            output.append({
                "source_type": row["source_type"], "source_uid": source_uid,
                "target_type": row["target_type"], "target_uid": target_uid,
                "role": row["role"], "sort_order": row["sort_order"],
            })
    return output


def _simple_table(conn: sqlite3.Connection, table: str, *, remove: set[str] | None = None) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    return [_clean(dict(row), remove=(remove or set()) | {"created_at", "updated_at"}) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY id')]


def _quality_aliases(conn: sqlite3.Connection, entity_uids: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
    if not table_exists(conn, "content_aliases"):
        return []
    output = []
    for row in conn.execute("SELECT * FROM content_aliases ORDER BY id"):
        uid = entity_uids.get((str(row["entity_type"]), int(row["canonical_entity_id"])))
        if uid:
            output.append({
                "entity_type": row["entity_type"], "old_slug": row["old_slug"],
                "canonical_uid": uid, "canonical_slug": row["canonical_slug"],
            })
    return output


def _provenance(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    systems = _simple_table(conn, "source_systems")
    system_uid = {int(row["id"]): str(row["uid"]) for row in conn.execute("SELECT id,uid FROM source_systems")} if table_exists(conn, "source_systems") else {}
    run_uid = {int(row["id"]): str(row["uid"]) for row in conn.execute("SELECT id,uid FROM source_runs")} if table_exists(conn, "source_runs") else {}
    runs: list[dict[str, Any]] = []
    if table_exists(conn, "source_runs"):
        for raw in conn.execute("SELECT * FROM source_runs ORDER BY id"):
            row = _clean(dict(raw), remove={"created_at"})
            source_id = row.pop("source_system_id", None)
            if source_id in system_uid:
                row["source_system_uid"] = system_uid[source_id]
            runs.append(row)
    records: list[dict[str, Any]] = []
    source_record_uid: dict[int, str] = {}
    if table_exists(conn, "source_records"):
        for raw in conn.execute("SELECT * FROM source_records ORDER BY id"):
            source_record_uid[int(raw["id"])] = str(raw["uid"])
            row = _clean(dict(raw), remove={"created_at"})
            run_id = row.pop("source_run_id", None)
            source_id = row.pop("source_system_id", None)
            if run_id in run_uid:
                row["source_run_uid"] = run_uid[run_id]
            if source_id in system_uid:
                row["source_system_uid"] = system_uid[source_id]
            records.append(row)
    mappings: list[dict[str, Any]] = []
    if table_exists(conn, "canonical_mappings"):
        for raw in conn.execute("SELECT * FROM canonical_mappings ORDER BY id"):
            row = _clean(dict(raw), remove={"created_at", "updated_at"})
            source_record_id = row.pop("source_record_id", None)
            if source_record_id in source_record_uid:
                row["source_record_uid"] = source_record_uid[source_record_id]
                mappings.append(row)
    return {
        "source_systems": systems,
        "source_runs": runs,
        "source_records": records,
        "canonical_mappings": mappings,
    }


def _payloads(conn: sqlite3.Connection) -> dict[str, bytes]:
    entity_uids, _ = _entity_maps(conn)
    asset_uids, _ = _asset_maps(conn)
    provenance = _provenance(conn)
    data: dict[str, list[dict[str, Any]]] = {
        "entities": _entities(conn),
        "assets": _assets(conn),
        "asset_links": _asset_links(conn, entity_uids, asset_uids),
        "entity_links": _entity_links(conn, entity_uids),
        "relations": _relations(conn, entity_uids),
        **provenance,
        "aliases": _quality_aliases(conn, entity_uids),
        "merge_exclusions": _simple_table(conn, "merge_exclusions"),
        "resolved_pairs": _simple_table(conn, "resolved_pairs"),
    }
    return {SECTION_FILES[key]: _jsonl(value) for key, value in data.items()}


def _safe_asset_archive_path(db_path: str) -> str:
    normalized = db_path.removeprefix("assets/").lstrip("/")
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"Unsafe asset path: {db_path!r}")
    return f"assets/{pure.as_posix()}"


def export_archive(conn: sqlite3.Connection, assets_dir: str | Path, destination: str | Path, *, app_version: str = "") -> dict[str, Any]:
    migration = migrate(conn)
    conn.commit()
    payloads = _payloads(conn)
    root = Path(assets_dir).resolve()
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for section, path in SECTION_FILES.items():
        payload = payloads[path]
        counts[section] = sum(1 for line in payload.splitlines() if line.strip())
        files.append({"path": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    manifest = {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_FORMAT_VERSION,
        "schema_version": migration.get("schema_version"),
        "generated_at": _now(),
        "app_version": app_version,
        "scope": "editorial_archive",
        "counts": counts,
        "files": files,
        "excluded": ["settings", "metrics_daily", "page_views", "join_requests", "conference_*", "runtime jobs"],
    }
    readme = (
        "MIFP Content Archive\n"
        "====================\n\n"
        "This package contains portable editorial records, relationships, provenance,\n"
        "quality decisions and managed assets. Runtime settings, analytics, membership\n"
        "requests and conference-builder state are intentionally excluded.\n"
    ).encode("utf-8")
    checksums: list[str] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, payload in payloads.items():
            zf.writestr(path, payload)
            checksums.append(f"{hashlib.sha256(payload).hexdigest()}  {path}")
        zf.writestr(README, readme)
        checksums.append(f"{hashlib.sha256(readme).hexdigest()}  {README}")
        if table_exists(conn, "assets"):
            for row in conn.execute("SELECT path,storage_status FROM assets ORDER BY id"):
                if row["storage_status"] != "local" or not row["path"]:
                    continue
                archive_path = _safe_asset_archive_path(str(row["path"]))
                source = (root / archive_path.removeprefix("assets/")).resolve()
                if root not in source.parents or not source.is_file():
                    continue
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                zf.write(source, archive_path)
                checksums.append(f"{digest}  {archive_path}")
                files.append({"path": archive_path, "size": source.stat().st_size, "sha256": digest})
        manifest["files"] = files
        manifest_payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        zf.writestr(MANIFEST, manifest_payload)
        zf.writestr(CHECKSUMS, ("\n".join(checksums) + "\n").encode("utf-8"))
    return {"path": str(target), "bytes": target.stat().st_size, "manifest": manifest}


def _validate_member(name: str) -> str:
    if "\\" in name or "\x00" in name:
        raise ValueError(f"Unsafe ZIP member: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"Unsafe ZIP member: {name!r}")
    return pure.as_posix()


def inspect_archive(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    with zipfile.ZipFile(target, "r") as zf:
        infos = zf.infolist()
        if len(infos) > _MAX_FILES:
            raise ValueError("Archive contains too many files")
        total = sum(info.file_size for info in infos)
        if total > _MAX_UNPACKED:
            raise ValueError("Archive is too large when unpacked")
        names = {_validate_member(info.filename) for info in infos if not info.is_dir()}
        if MANIFEST not in names:
            raise ValueError("Archive is missing manifest.json")
        manifest = json.loads(zf.read(MANIFEST))
        if manifest.get("format") != ARCHIVE_FORMAT:
            raise ValueError("Unsupported archive format")
        version = int(manifest.get("format_version") or 0)
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported archive version: {version}")
        for required in SECTION_FILES.values():
            if required not in names:
                raise ValueError(f"Archive is missing {required}")
        declared = {str(item.get("path")): item for item in manifest.get("files") or [] if isinstance(item, dict)}
        errors: list[str] = []
        for name, item in declared.items():
            if name not in names:
                errors.append(f"Missing declared file: {name}")
                continue
            raw = zf.read(name)
            declared_size = item.get("size")
            if declared_size is not None and int(declared_size) != len(raw):
                errors.append(f"Size mismatch: {name}")
            if item.get("sha256") and hashlib.sha256(raw).hexdigest() != item["sha256"]:
                errors.append(f"Checksum mismatch: {name}")
        undeclared_assets = sorted(name for name in names if name.startswith("assets/") and name not in declared)
        if undeclared_assets:
            errors.append(f"Undeclared asset files: {', '.join(undeclared_assets[:5])}")
        return {"manifest": manifest, "files": len(names), "unpacked_bytes": total, "errors": errors, "valid": not errors}


def validate_archive(path: str | Path) -> dict[str, Any]:
    return inspect_archive(path)


def _read_jsonl(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for number, raw in enumerate(zf.read(name).decode("utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        item = json.loads(raw)
        if not isinstance(item, dict):
            raise ValueError(f"{name}:{number} is not an object")
        output.append(item)
    return output


def _upsert_named(conn: sqlite3.Connection, table: str, payload: dict[str, Any], identity: str) -> tuple[int, str]:
    table_columns = columns(conn, table)
    allowed = table_columns - {"id", "created_at", "updated_at"}
    clean = {key: value for key, value in payload.items() if key in allowed}
    value = clean.get(identity)
    row = conn.execute(f'SELECT id FROM "{table}" WHERE "{identity}"=?', (value,)).fetchone() if value not in (None, "") else None
    if row:
        update = {key: value for key, value in clean.items() if key != identity and value is not None}
        if update:
            assignments = ",".join(f'"{key}"=?' for key in update)
            if "updated_at" in table_columns:
                assignments += ",updated_at=CURRENT_TIMESTAMP"
            conn.execute(
                f'UPDATE "{table}" SET ' + assignments + " WHERE id=?",
                (*update.values(), int(row["id"])),
            )
        return int(row["id"]), "updated"
    names = list(clean)
    cur = conn.execute(
        f'INSERT INTO "{table}"(' + ",".join(f'"{name}"' for name in names) + ") VALUES(" + ",".join("?" for _ in names) + ")",
        tuple(clean[name] for name in names),
    )
    return int(cur.lastrowid), "inserted"


def import_archive(
    conn: sqlite3.Connection,
    assets_dir: str | Path,
    archive_path: str | Path,
    *,
    dry_run: bool = False,
    skip_assets: bool = False,
) -> dict[str, Any]:
    inspection = inspect_archive(archive_path)
    if not inspection["valid"]:
        raise ValueError("Archive validation failed: " + "; ".join(inspection["errors"]))
    migrate(conn)
    root = Path(assets_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"inserted": {}, "updated": {}, "assets_copied": 0, "dry_run": dry_run}
    with zipfile.ZipFile(archive_path, "r") as zf:
        entities = _read_jsonl(zf, SECTION_FILES["entities"])
        assets = _read_jsonl(zf, SECTION_FILES["assets"])
        links = _read_jsonl(zf, SECTION_FILES["entity_links"])
        asset_links = _read_jsonl(zf, SECTION_FILES["asset_links"])
        relations = _read_jsonl(zf, SECTION_FILES["relations"])
        sections = {key: _read_jsonl(zf, path) for key, path in SECTION_FILES.items() if key not in {"entities", "assets", "entity_links", "asset_links", "relations"}}
        conn.execute("SAVEPOINT mifp_archive_import")
        try:
            role_ids: dict[str, int] = {}
            if table_exists(conn, "roles"):
                role_ids = {str(row["name"]): int(row["id"]) for row in conn.execute("SELECT id,name FROM roles")}
            pending_parents: list[tuple[str, str]] = []
            for record in entities:
                typ = str(record.get("type") or "")
                spec = BY_TYPE.get(typ)
                if not spec or not isinstance(record.get("data"), dict):
                    raise ValueError(f"Invalid entity record: {record!r}")
                data = dict(record["data"])
                data["uid"] = str(record.get("uid") or data.get("uid") or "")
                if not data["uid"]:
                    raise ValueError(f"Entity without uid: {typ}")
                if typ == "member":
                    role = str(data.pop("role", "") or "")
                    if role:
                        if role not in role_ids:
                            cur = conn.execute("INSERT OR IGNORE INTO roles(name,label) VALUES(?,?)", (role, role.replace("_", " ").title()))
                            row = conn.execute("SELECT id FROM roles WHERE name=?", (role,)).fetchone()
                            role_ids[role] = int(row["id"])
                        data["role_id"] = role_ids[role]
                if typ == "event":
                    parent_uid = str(data.pop("parent_event_uid", "") or "")
                    if parent_uid:
                        pending_parents.append((data["uid"], parent_uid))
                    data.pop("parent_event_id", None)
                _, action = _upsert_named(conn, spec.table, data, "uid")
                summary[action][typ] = summary[action].get(typ, 0) + 1
            for child_uid, parent_uid in pending_parents:
                child = conn.execute("SELECT id FROM events WHERE uid=?", (child_uid,)).fetchone()
                parent = conn.execute("SELECT id FROM events WHERE uid=?", (parent_uid,)).fetchone()
                if child and parent and child["id"] != parent["id"]:
                    conn.execute("UPDATE events SET parent_event_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (parent["id"], child["id"]))
            for item in assets:
                if not item.get("uid"):
                    raise ValueError("Asset without uid")
                _, action = _upsert_named(conn, "assets", item, "uid")
                summary[action]["asset"] = summary[action].get("asset", 0) + 1
            entity_by_uid = {str(row["uid"]): (spec.type_name, int(row["id"])) for spec in ENTITY_SPECS for row in conn.execute(f'SELECT id,uid FROM "{spec.table}" WHERE uid IS NOT NULL')}
            asset_by_uid = {str(row["uid"]): int(row["id"]) for row in conn.execute("SELECT id,uid FROM assets WHERE uid IS NOT NULL")}
            for item in links:
                target = entity_by_uid.get(str(item.get("entity_uid") or ""))
                if not target:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO entity_links(entity_type,entity_id,url,label,role,is_primary,sort_order) VALUES(?,?,?,?,?,?,?)",
                    (target[0], target[1], item.get("url"), item.get("label"), item.get("role", "reference"), int(item.get("is_primary") or 0), int(item.get("sort_order") or 0)),
                )
            for item in asset_links:
                target = entity_by_uid.get(str(item.get("entity_uid") or ""))
                asset_id = asset_by_uid.get(str(item.get("asset_uid") or ""))
                if target and asset_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order) VALUES(?,?,?,?,?,?)",
                        (asset_id, target[0], target[1], item.get("role", "attachment"), int(item.get("is_primary") or 0), int(item.get("sort_order") or 0)),
                    )
            for item in relations:
                source = entity_by_uid.get(str(item.get("source_uid") or ""))
                target = entity_by_uid.get(str(item.get("target_uid") or ""))
                if source and target:
                    conn.execute(
                        "INSERT OR IGNORE INTO entity_relations(source_type,source_id,target_type,target_id,role,sort_order) VALUES(?,?,?,?,?,?)",
                        (source[0], source[1], target[0], target[1], item.get("role", "related"), int(item.get("sort_order") or 0)),
                    )
            _import_provenance(conn, sections)
            _import_quality(conn, sections, entity_by_uid)
            if dry_run:
                conn.execute("ROLLBACK TO mifp_archive_import")
                conn.execute("RELEASE mifp_archive_import")
            else:
                for item in ([] if skip_assets else inspection["manifest"].get("files") or []):
                    name = str(item.get("path") or "")
                    if not name.startswith("assets/"):
                        continue
                    relative = PurePosixPath(name).relative_to("assets")
                    target = (root / Path(*relative.parts)).resolve()
                    if root not in target.parents:
                        raise ValueError(f"Unsafe asset target: {name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    summary["assets_copied"] += 1
                conn.execute("RELEASE mifp_archive_import")
                conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK TO mifp_archive_import")
                conn.execute("RELEASE mifp_archive_import")
            raise
    return summary


def _import_provenance(conn: sqlite3.Connection, sections: dict[str, list[dict[str, Any]]]) -> None:
    for item in sections.get("source_systems", []):
        _upsert_named(conn, "source_systems", item, "uid")
    systems = {str(row["uid"]): int(row["id"]) for row in conn.execute("SELECT id,uid FROM source_systems")}
    for item in sections.get("source_runs", []):
        payload = dict(item)
        payload["source_system_id"] = systems.get(str(payload.pop("source_system_uid", "") or ""))
        _upsert_named(conn, "source_runs", payload, "uid")
    runs = {str(row["uid"]): int(row["id"]) for row in conn.execute("SELECT id,uid FROM source_runs")}
    for item in sections.get("source_records", []):
        payload = dict(item)
        payload["source_system_id"] = systems.get(str(payload.pop("source_system_uid", "") or ""))
        payload["source_run_id"] = runs.get(str(payload.pop("source_run_uid", "") or ""))
        _upsert_named(conn, "source_records", payload, "uid")
    source_records = {str(row["uid"]): int(row["id"]) for row in conn.execute("SELECT id,uid FROM source_records")}
    for item in sections.get("canonical_mappings", []):
        payload = dict(item)
        payload["source_record_id"] = source_records.get(str(payload.pop("source_record_uid", "") or ""))
        if payload["source_record_id"]:
            conn.execute(
                "INSERT OR IGNORE INTO canonical_mappings(source_record_id,entity_type,entity_uid,mapping_kind,confidence,decision_note) VALUES(?,?,?,?,?,?)",
                (payload["source_record_id"], payload.get("entity_type"), payload.get("entity_uid"), payload.get("mapping_kind", "canonical"), payload.get("confidence"), payload.get("decision_note")),
            )


def _import_quality(conn: sqlite3.Connection, sections: dict[str, list[dict[str, Any]]], entity_by_uid: dict[str, tuple[str, int]]) -> None:
    for item in sections.get("merge_exclusions", []):
        allowed = columns(conn, "merge_exclusions") - {"id", "created_at", "updated_at"}
        payload = {key: value for key, value in item.items() if key in allowed}
        names = list(payload)
        if names:
            conn.execute(
                "INSERT OR IGNORE INTO merge_exclusions(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")",
                tuple(payload[name] for name in names),
            )
    for item in sections.get("resolved_pairs", []):
        allowed = columns(conn, "resolved_pairs") - {"id", "created_at", "updated_at"}
        payload = {key: value for key, value in item.items() if key in allowed}
        names = list(payload)
        if names:
            conn.execute(
                "INSERT OR IGNORE INTO resolved_pairs(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")",
                tuple(payload[name] for name in names),
            )
    for item in sections.get("aliases", []):
        target = entity_by_uid.get(str(item.get("canonical_uid") or ""))
        if target:
            conn.execute(
                "INSERT OR IGNORE INTO content_aliases(entity_type,old_slug,canonical_entity_id,canonical_slug,bundle_id) VALUES(?,?,?,?,NULL)",
                (target[0], item.get("old_slug"), target[1], item.get("canonical_slug")),
            )

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
SUPPORTED_FORMAT_VERSIONS = {1, 2}
QUALITY_FINGERPRINT_ACTIONS = {
    "", "aggregated_event", "clean_record", "date_placeholder", "invalid_record",
    "inverted_date_range", "junk_record", "merge_records", "missing_asset_file",
    "missing_date", "multiple_primary_links", "name_inversion", "page_fragment",
    "placeholder_title", "split_aggregated_record",
}


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
) -> dict[str, Any]:
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
            zf.write(path, archive_path)
            manifest["files"].append({
                "path": db_path,
                "archive_path": archive_path,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        zf.writestr(
            ZIP_MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        )
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
) -> int:
    """Write a portable ZIP directly to disk and return its byte size."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_bundle_zip(conn, scope, assets_dir, destination, app_version=app_version)
    return destination.stat().st_size




def bundle_to_jsonl_file(
    conn: sqlite3.Connection,
    scope: str,
    assets_dir: Path,
    destination: Path,
    *,
    app_version: str = "",
) -> dict[str, Any]:
    """Write a self-contained JSONL v2 package equivalent to the ZIP export.

    Canonical record lines remain ordinary JSONL records. Package metadata,
    durable state, and local assets use a reserved ``_mifp`` envelope so the
    importer can restore the same information without an accompanying folder.
    Legacy record-only JSONL files remain supported by ``import_jsonl_payload``.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_export_bundle(conn, scope)
    records = bundle.get("records") or []
    durable_state = _durable_state(conn) if scope == "all" else None
    asset_rows = _asset_rows_for_scope(conn, scope, records)
    records_payload = _records_to_jsonl(records)
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
        raise ValueError("state.json failed integrity verification")
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

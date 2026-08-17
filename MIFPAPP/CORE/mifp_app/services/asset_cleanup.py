from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from ..runtime_storage import require_free_space
from .assets import resolve_db_asset_path
from .dashboard_repository import asset_usage


@dataclass
class AssetCleanupPlan:
    unused_db_assets: list[dict[str, Any]]
    missing_file_assets: list[dict[str, Any]]
    orphan_files: list[dict[str, Any]]


@dataclass
class AssetExportManifest:
    version: int = 1
    exported_at: str = ""
    export_type: str = ""
    assets: list[dict[str, Any]] = field(default_factory=list)


EXPORT_DIR_NAME = "assets_exports"
ZIP_MAX_COMPRESSION_RATIO = 1000


def _export_dir(assets_dir: Path) -> Path:
    return assets_dir.parent / EXPORT_DIR_NAME


def build_asset_export_plan(
    conn, assets_dir: Path, *, only_unused: bool = False, kind_filter: list[str] | None = None, status_filter: list[str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assets_dir = assets_dir.resolve()
    usage_rows = asset_usage(conn)
    usage_by_id = {int(r["id"]): int(r.get("usage_count") or 0) for r in usage_rows}

    local_files: list[dict[str, Any]] = []
    missing_ext: list[dict[str, Any]] = []

    rows = conn.execute("SELECT * FROM assets ORDER BY id").fetchall()
    for row in rows:
        d = dict(row)
        aid = int(d["id"])
        d["usage_count"] = usage_by_id.get(aid, 0)

        if only_unused and d["usage_count"] != 0:
            continue

        kind = d.get("kind") or "other"
        if kind_filter and kind not in kind_filter:
            continue

        if status_filter:
            uc = d["usage_count"]
            is_missing = d.get("storage_status") == "missing"
            if "used" in status_filter and uc == 0 and not is_missing:
                continue
            if "unused" in status_filter and uc != 0:
                continue
            if "missing" in status_filter and not is_missing:
                continue

        is_ext = int(d.get("is_external") or 0)
        storage = str(d.get("storage_status") or "local")
        if is_ext or storage == "external":
            d["file_included"] = False
            missing_ext.append(d)
            continue

        p = resolve_db_asset_path(assets_dir, d.get("path"))
        if p.is_file():
            d["file_included"] = True
            local_files.append(d)
        else:
            d["file_included"] = False
            missing_ext.append(d)

    return local_files, missing_ext


def export_assets_to_zip(
    conn, assets_dir: Path, *, only_unused: bool = False, kind_filter: list[str] | None = None, status_filter: list[str] | None = None, export_dir: Path | None = None
) -> Path | None:
    assets_dir = assets_dir.resolve()
    local_files, missing_ext = build_asset_export_plan(
        conn, assets_dir, only_unused=only_unused, kind_filter=kind_filter, status_filter=status_filter
    )
    all_assets = local_files + missing_ext
    if not all_assets:
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = "unused" if only_unused else "filtered" if (kind_filter or status_filter) else "full"
    out_dir = export_dir.resolve() if export_dir else _export_dir(assets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{stamp}_{label}.zip"
    estimated_bytes = sum(
        int(asset.get("size") or 0) for asset in local_files
    )
    require_free_space(
        out_dir,
        operation_bytes=max(estimated_bytes, 1),
        reserve_bytes=int(getattr(Config, "STORAGE_MIN_FREE_BYTES", 0)),
    )

    manifest = AssetExportManifest(
        version=1,
        exported_at=datetime.now().isoformat(timespec="seconds"),
        export_type=label,
        assets=all_assets,
    )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".asset-export-",
            suffix=".tmp",
            dir=out_dir,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(asdict(manifest), indent=2, ensure_ascii=False))
            for asset in local_files:
                p = resolve_db_asset_path(assets_dir, asset.get("path"))
                if p.is_file():
                    rel = str(p.relative_to(assets_dir))
                    zf.write(p, f"files/{rel}")
        os.replace(temporary_path, zip_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return zip_path


def _validate_asset_archive_path(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or "\x00" in name or "\\" in name:
        raise ValueError(f"Zip Slip: unsafe archive path {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Zip Slip: unsafe archive path {name!r}")
    return name


def _validate_asset_zip(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > Config.IMPORT_MAX_FILES:
        raise ValueError(f"ZIP package exceeds maximum file count: {Config.IMPORT_MAX_FILES}")
    unpacked = sum(info.file_size for info in infos)
    if unpacked > Config.IMPORT_MAX_UNPACKED_BYTES:
        raise ValueError(f"ZIP package expands beyond maximum size: {Config.IMPORT_MAX_UNPACKED_BYTES} bytes")
    seen: set[str] = set()
    for info in infos:
        name = _validate_asset_archive_path(info.filename.rstrip("/"))
        if name in seen:
            raise ValueError(f"ZIP contains duplicate file name: {name}")
        seen.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise ValueError(f"ZIP contains a symbolic link: {name}")
        if info.compress_size == 0 and info.file_size > 0:
            raise ValueError(f"ZIP member has invalid compressed size: {name}")
        if info.compress_size > 0 and info.file_size > 1024 * 1024:
            if info.file_size / info.compress_size > ZIP_MAX_COMPRESSION_RATIO:
                raise ValueError(f"ZIP member has suspicious compression ratio: {name}")


def extract_zip_manifest(zip_path: Path) -> AssetExportManifest:
    if zip_path.stat().st_size > Config.IMPORT_MAX_ZIP_BYTES:
        raise ValueError(f"ZIP package exceeds maximum size: {Config.IMPORT_MAX_ZIP_BYTES} bytes")
    with zipfile.ZipFile(zip_path, "r") as zf:
        _validate_asset_zip(zf)
        if "manifest.json" not in zf.namelist():
            raise ValueError("ZIP package is missing manifest.json")
        data = json.loads(zf.read("manifest.json"))
    if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
        raise ValueError("ZIP manifest must contain an assets list")
    if len(data["assets"]) > Config.IMPORT_MAX_FILES:
        raise ValueError(f"ZIP manifest exceeds maximum asset count: {Config.IMPORT_MAX_FILES}")
    if any(not isinstance(asset, dict) for asset in data["assets"]):
        raise ValueError("ZIP manifest contains an invalid asset record")
    return AssetExportManifest(**data)


def import_assets_from_zip(
    conn, assets_dir: Path, zip_path: Path, *, dry_run: bool = False
) -> dict[str, Any]:
    assets_dir = assets_dir.resolve()
    manifest = extract_zip_manifest(zip_path)
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "asset_files_missing": [],
    }

    with zipfile.ZipFile(zip_path, "r") as zf:
        for asset in manifest.assets:
            source_url = asset.get("source_url") or ""
            checksum = asset.get("checksum") or ""
            original_filename = asset.get("original_filename") or asset.get("filename") or "unknown"
            rel_path = asset.get("path") or f"imported/{original_filename}"
            _validate_asset_archive_path(str(rel_path))

            existing = None
            if checksum:
                existing = conn.execute("SELECT id FROM assets WHERE checksum=?", (checksum,)).fetchone()
            if not existing and source_url:
                existing = conn.execute("SELECT id FROM assets WHERE source_url=?", (source_url,)).fetchone()
            if not existing and rel_path:
                existing = conn.execute("SELECT id FROM assets WHERE path=?", (rel_path,)).fetchone()

            if existing:
                result["skipped"] += 1
                continue

            kind = asset.get("kind") or "other"
            size = asset.get("size") or 0
            alt_text = asset.get("alt_text") or None
            caption = asset.get("caption") or None
            file_included = asset.get("file_included", False)
            storage_status = asset.get("storage_status") or "local"

            if file_included:
                archive_path = f"files/{rel_path}"
                if archive_path not in zf.namelist():
                    file_included = False
                    result["asset_files_missing"].append(archive_path)

            if dry_run:
                result["inserted"] += 1
                continue

            if file_included:
                archive_path = f"files/{rel_path}"
                target = (assets_dir / rel_path).resolve()
                try:
                    target.relative_to(assets_dir)
                except ValueError as exc:
                    raise ValueError(f"Zip Slip: attempted path traversal in {archive_path}") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(archive_path) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                storage_status = "local"

            conn.execute(
                """INSERT OR IGNORE INTO assets
                   (filename, original_filename, path, kind, mime_type, size, checksum,
                    alt_text, caption, source_url, storage_status, is_external,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'), datetime('now'))""",
                (
                    original_filename,
                    original_filename,
                    rel_path,
                    kind,
                    asset.get("mime_type") or None,
                    size,
                    checksum or None,
                    alt_text,
                    caption,
                    source_url,
                    storage_status,
                    int(bool(asset.get("is_external"))) if not file_included else 0,
                ),
            )
            result["inserted"] += 1

    if not dry_run:
        conn.commit()
    return result


def import_assets_from_jsonl(conn, jsonl_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Import asset metadata from a JSONL file without downloading files."""
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > Config.IMPORT_MAX_JSONL_LINES:
        raise ValueError(f"JSONL exceeds maximum line count: {Config.IMPORT_MAX_JSONL_LINES}")
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            result["errors"].append("Invalid JSON line")
            continue
        if not isinstance(rec, dict):
            result["errors"].append("Invalid JSON record")
            continue

        source_url = rec.get("source_url") or ""
        checksum = rec.get("checksum") or ""
        path = rec.get("path") or ""
        filename = rec.get("filename") or rec.get("original_filename") or Path(path).name or "unknown"
        _validate_asset_archive_path(str(path or f"imported/{filename}"))

        existing = None
        if checksum:
            existing = conn.execute("SELECT id FROM assets WHERE checksum=?", (checksum,)).fetchone()
        if not existing and source_url:
            existing = conn.execute("SELECT id FROM assets WHERE source_url=?", (source_url,)).fetchone()
        if not existing and path:
            existing = conn.execute("SELECT id FROM assets WHERE path=?", (path,)).fetchone()

        if existing:
            result["skipped"] += 1
            continue

        if dry_run:
            result["inserted"] += 1
            continue

        conn.execute(
            """INSERT OR IGNORE INTO assets
               (filename, original_filename, path, kind, mime_type, size, checksum,
                alt_text, caption, source_url, storage_status, is_external,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'), datetime('now'))""",
            (
                filename,
                rec.get("original_filename") or filename,
                path or f"imported/{filename}",
                rec.get("kind") or "other",
                rec.get("mime_type") or None,
                rec.get("size") or 0,
                checksum or None,
                rec.get("alt_text") or None,
                rec.get("caption") or None,
                source_url,
                rec.get("storage_status") or ("external" if source_url else "missing"),
                int(bool(rec.get("is_external"))) if "is_external" in rec else int(bool(source_url)),
            ),
        )
        result["inserted"] += 1

    if not dry_run:
        conn.commit()
    return result


def list_exported_zips(assets_dir: Path, export_dir: Path | None = None) -> list[dict[str, Any]]:
    out_dir = export_dir.resolve() if export_dir else _export_dir(assets_dir)
    if not out_dir.exists():
        return []
    zips = []
    for p in sorted(out_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.suffix == ".zip":
            zips.append({
                "filename": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            })
    return zips


def build_asset_cleanup_plan(conn, assets_dir, *, scan_orphans: bool = True):
    assets_dir = Path(assets_dir).resolve()
    usage_by_id = {int(r["id"]): int(r.get("usage_count") or 0) for r in asset_usage(conn)}
    rows = [dict(r) for r in conn.execute("SELECT * FROM assets ORDER BY id DESC").fetchall()]

    db_file_paths: set[Path] = set()
    unused_db_assets: list[dict[str, Any]] = []
    missing_file_assets: list[dict[str, Any]] = []

    for row in rows:
        aid = int(row["id"])
        row["usage_count"] = usage_by_id.get(aid, 0)
        try:
            path = resolve_db_asset_path(assets_dir, row.get("path"))
        except ValueError:
            missing_file_assets.append(row)
            continue
        if row.get("path"):
            db_file_paths.add(path.resolve())
            fallback = (assets_dir / Path(str(row.get("path"))).name).resolve()
            db_file_paths.add(fallback)
        is_external = int(row.get("is_external") or 0) == 1 or str(row.get("storage_status") or "") == "external"
        if row["usage_count"] == 0:
            unused_db_assets.append(row)
        if not is_external and row.get("path") and not path.is_file() and not (assets_dir / Path(str(row.get("path"))).name).is_file():
            missing_file_assets.append(row)

    orphan_files: list[dict[str, Any]] = []
    if scan_orphans and assets_dir.exists():
        for path in assets_dir.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved not in db_file_paths:
                orphan_files.append({
                    "path": str(path.relative_to(assets_dir)),
                    "size": path.stat().st_size,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                })

    return AssetCleanupPlan(
        unused_db_assets=unused_db_assets,
        missing_file_assets=missing_file_assets,
        orphan_files=orphan_files,
    )


def asset_library_summary(conn, assets_dir, *, scan_orphans: bool = True) -> dict[str, Any]:
    """Single source of truth for the asset library page metrics.

    Counts are derived from the local filesystem, ``is_external``,
    ``source_url`` and ``asset_recovery_state`` — never from the potentially
    stale ``storage_status`` column. Returns both totals and the id sets the
    page uses to annotate rows and filter the table.
    """
    assets_dir = Path(assets_dir).resolve()
    plan = build_asset_cleanup_plan(conn, assets_dir, scan_orphans=scan_orphans)
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM assets").fetchall()]
    usage_by_id = {int(r["id"]): int(r.get("usage_count") or 0) for r in asset_usage(conn)}
    recovery_state = {
        int(item["asset_id"]): dict(item)
        for item in conn.execute(
            "SELECT asset_id, attempts, terminal, next_attempt_at FROM asset_recovery_state"
        ).fetchall()
    }

    missing_ids = {int(item["id"]) for item in plan.missing_file_assets}
    unused_ids = {int(item["id"]) for item in plan.unused_db_assets}
    external_ids = {
        int(item["id"]) for item in all_rows
        if int(item.get("is_external") or 0) == 1
        or str(item.get("path") or "").startswith("external/")
    }

    duplicate_rows = conn.execute(
        """
        SELECT checksum FROM assets
        WHERE checksum IS NOT NULL AND checksum<>''
        GROUP BY checksum HAVING COUNT(*) > 1
        """
    ).fetchall()
    duplicate_checksums = {str(item["checksum"]) for item in duplicate_rows}
    duplicate_signatures = {
        (str(item["display_name"]), int(item["size"]))
        for item in conn.execute(
            """
            SELECT LOWER(COALESCE(NULLIF(original_filename,''), filename)) AS display_name,
                   size
            FROM assets
            WHERE size IS NOT NULL AND size>0
            GROUP BY display_name, size HAVING COUNT(*) > 1
            """
        ).fetchall()
    }
    metadata_ids = {
        int(item["id"]) for item in all_rows
        if not item.get("checksum")
        or (
            item.get("kind") == "image"
            and (not item.get("alt_text") or not item.get("width") or not item.get("height"))
        )
    }
    duplicate_ids = {
        int(item["id"]) for item in all_rows
        if (
            item.get("checksum") and str(item["checksum"]) in duplicate_checksums
        ) or (
            item.get("size")
            and (
                str(item.get("original_filename") or item.get("filename") or "").lower(),
                int(item["size"]),
            ) in duplicate_signatures
        )
    }

    by_id = {int(item["id"]): item for item in all_rows}
    now_text = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    recoverable_ids: set[int] = set()
    error_ids: set[int] = set()
    terminal = 0
    deferred = 0
    for aid in missing_ids:
        item = by_id.get(aid)
        if item is None:
            continue
        state = recovery_state.get(aid, {})
        is_terminal = int(state.get("terminal") or 0) == 1
        has_source = bool(str(item.get("source_url") or "").strip())
        if has_source and not is_terminal:
            recoverable_ids.add(aid)
        else:
            error_ids.add(aid)
        if is_terminal:
            terminal += 1
        elif state.get("next_attempt_at") and str(state["next_attempt_at"]) > now_text:
            deferred += 1

    used_ids = set(usage_by_id) - unused_ids
    return {
        "total": len(all_rows),
        "used": len(used_ids),
        "unused": len(unused_ids),
        "missing": len(missing_ids),
        "external": len(external_ids),
        "recoverable": len(recoverable_ids),
        "errors": len(error_ids),
        "terminal": terminal,
        "deferred": deferred,
        "metadata": len(metadata_ids),
        "duplicates": len(duplicate_ids),
        "orphan_count": len(plan.orphan_files),
        "used_ids": used_ids,
        "unused_ids": unused_ids,
        "missing_ids": missing_ids,
        "external_ids": external_ids,
        "recoverable_ids": recoverable_ids,
        "error_ids": error_ids,
        "metadata_ids": metadata_ids,
        "duplicate_ids": duplicate_ids,
        "plan": plan,
    }

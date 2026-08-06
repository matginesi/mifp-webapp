from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from .database import table_exists
from .registry import ENTITY_SPECS


def _asset_path(root: Path, value: str) -> Path:
    relative = value.removeprefix("assets/").lstrip("/")
    return (root / relative).resolve()


def archive_health(conn: sqlite3.Connection, assets_dir: str | Path) -> dict[str, Any]:
    root = Path(assets_dir).resolve()
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for spec in ENTITY_SPECS:
        if not table_exists(conn, spec.table):
            findings.append({"severity": "critical", "code": "missing_table", "table": spec.table})
            continue
        total = int(conn.execute(f'SELECT COUNT(*) FROM "{spec.table}"').fetchone()[0])
        counts[spec.table] = total
        missing_uid = int(conn.execute(
            f'SELECT COUNT(*) FROM "{spec.table}" WHERE uid IS NULL OR TRIM(uid)=\'\''
        ).fetchone()[0])
        if missing_uid:
            findings.append({"severity": "critical", "code": "missing_uid", "table": spec.table, "count": missing_uid})
        duplicate_uid = conn.execute(
            f'SELECT uid,COUNT(*) AS n FROM "{spec.table}" '
            "WHERE uid IS NOT NULL AND TRIM(uid)!='' GROUP BY uid HAVING COUNT(*)>1 LIMIT 20"
        ).fetchall()
        for row in duplicate_uid:
            findings.append({"severity": "critical", "code": "duplicate_uid", "table": spec.table, "uid": row["uid"], "count": row["n"]})
        missing_title = int(conn.execute(
            f'SELECT COUNT(*) FROM "{spec.table}" WHERE {spec.title_field} IS NULL OR TRIM({spec.title_field})=\'\''
        ).fetchone()[0])
        if missing_title:
            findings.append({"severity": "warning", "code": "missing_title", "table": spec.table, "count": missing_title})

    if table_exists(conn, "assets"):
        assets = [dict(row) for row in conn.execute("SELECT * FROM assets ORDER BY id")]
        counts["assets"] = len(assets)
        seen_files: set[Path] = set()
        for asset in assets:
            path_text = str(asset.get("path") or "").strip()
            if not path_text:
                findings.append({"severity": "critical", "code": "asset_without_path", "asset_uid": asset.get("uid")})
                continue
            path = _asset_path(root, path_text)
            if root not in path.parents and path != root:
                findings.append({"severity": "critical", "code": "unsafe_asset_path", "path": path_text})
                continue
            if asset.get("storage_status") == "local":
                if not path.is_file():
                    findings.append({"severity": "critical", "code": "missing_asset_file", "asset_uid": asset.get("uid"), "path": path_text})
                else:
                    seen_files.add(path)
                    expected = str(asset.get("content_sha256") or asset.get("checksum") or "").strip().lower()
                    if expected and len(expected) == 64:
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        if digest != expected:
                            findings.append({"severity": "critical", "code": "asset_checksum_mismatch", "asset_uid": asset.get("uid"), "path": path_text})
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file() and path not in seen_files:
                    findings.append({"severity": "warning", "code": "unregistered_asset_file", "path": path.relative_to(root).as_posix()})

    if table_exists(conn, "asset_links"):
        orphans = conn.execute(
            "SELECT al.id FROM asset_links al LEFT JOIN assets a ON a.id=al.asset_id WHERE a.id IS NULL LIMIT 100"
        ).fetchall()
        for row in orphans:
            findings.append({"severity": "critical", "code": "orphan_asset_link", "id": row["id"]})

    score = max(0, 100 - 10 * sum(f["severity"] == "critical" for f in findings) - 2 * sum(f["severity"] == "warning" for f in findings))
    return {
        "score": score,
        "counts": counts,
        "summary": {
            "critical": sum(f["severity"] == "critical" for f in findings),
            "warnings": sum(f["severity"] == "warning" for f in findings),
            "total": len(findings),
        },
        "findings": findings,
    }

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..db.connection import table_exists
from .dashboard_repository import search_logs

CONTENT_TARGETS: tuple[dict[str, str], ...] = (
    {
        "code": "members_affiliation",
        "table": "members",
        "label": "Members without affiliation",
        "severity": "warning",
        "where": "TRIM(COALESCE(affiliation,''))=''",
        "title": "display_name",
        "section": "members",
    },
    {
        "code": "members_country",
        "table": "members",
        "label": "Members without country",
        "severity": "warning",
        "where": "TRIM(COALESCE(country,''))=''",
        "title": "display_name",
        "section": "members",
    },
    {
        "code": "members_role",
        "table": "members",
        "label": "Members without role",
        "severity": "warning",
        "where": "role_id IS NULL",
        "title": "display_name",
        "section": "members",
    },
    {
        "code": "events_date",
        "table": "events",
        "label": "Events without a usable date",
        "severity": "warning",
        "where": "TRIM(COALESCE(start_date,''))='' AND TRIM(COALESCE(date_text,''))=''",
        "title": "title",
        "section": "events",
    },
    {
        "code": "events_location",
        "table": "events",
        "label": "Events without location",
        "severity": "info",
        "where": "TRIM(COALESCE(location,''))=''",
        "title": "title",
        "section": "events",
    },
    {
        "code": "news_date",
        "table": "news",
        "label": "News without a date",
        "severity": "warning",
        "where": "TRIM(COALESCE(date,''))='' AND TRIM(COALESCE(date_text,''))=''",
        "title": "title",
        "section": "news",
    },
    {
        "code": "news_summary",
        "table": "news",
        "label": "News without summary",
        "severity": "info",
        "where": "TRIM(COALESCE(summary,''))=''",
        "title": "title",
        "section": "news",
    },
    {
        "code": "publications_year",
        "table": "publications",
        "label": "Publications without year",
        "severity": "warning",
        "where": "year IS NULL",
        "title": "title",
        "section": "publications",
    },
    {
        "code": "publications_reference",
        "table": "publications",
        "label": "Publications without DOI or linked file",
        "severity": "info",
        "where": (
            "TRIM(COALESCE(doi,''))='' AND NOT EXISTS ("
            "SELECT 1 FROM asset_links al WHERE al.entity_type='publication' "
            "AND al.entity_id=publications.id)"
        ),
        "title": "title",
        "section": "publications",
    },
    {
        "code": "sponsors_logo",
        "table": "sponsors",
        "label": "Sponsors without logo",
        "severity": "warning",
        "where": (
            "NOT EXISTS (SELECT 1 FROM asset_links al WHERE al.entity_type='sponsor' "
            "AND al.entity_id=sponsors.id AND al.role='logo')"
        ),
        "title": "name",
        "section": "sponsors",
    },
    {
        "code": "sponsors_link",
        "table": "sponsors",
        "label": "Sponsors without destination link",
        "severity": "info",
        "where": (
            "NOT EXISTS (SELECT 1 FROM entity_links el WHERE el.entity_type='sponsor' "
            "AND el.entity_id=sponsors.id)"
        ),
        "title": "name",
        "section": "sponsors",
    },
    {
        "code": "pages_summary",
        "table": "pages",
        "label": "Public text records without summary",
        "severity": "info",
        "where": "TRIM(COALESCE(summary,''))=''",
        "title": "title",
        "section": "pages",
    },
)


SENSITIVE_SETTING_PARTS = (
    "password",
    "secret",
    "token",
    "private",
    "credential",
    "smtp_",
    "api_key",
)

_INCIDENT_VOLATILE = re.compile(
    r"\b(?:[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{20,}|\d{2,})\b",
    re.IGNORECASE,
)
_INCIDENT_PATH = re.compile(r"(?<!https:)(?<!http:)(?:/[A-Za-z0-9._-]+){2,}")


def _safe_recovery_error(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "404" in text or "not found" in text:
        return "Remote file was not found"
    if "timeout" in text or "timed out" in text:
        return "Remote server timed out"
    if any(term in text for term in ("blocked", "private address", "not allowed", "credentials")):
        return "URL was blocked by the remote-download safety policy"
    if any(term in text for term in ("too large", "size limit", "exceeds")):
        return "Remote file exceeds the configured size limit"
    if any(term in text for term in ("invalid", "mime", "html", "signature")):
        return "Downloaded content failed file validation"
    return "Recovery failed; inspect the correlated server log"


def _safe_run_stats(value: Any) -> dict[str, int | float | bool]:
    raw = _json_dict(value)
    return {
        str(key)[:50]: item
        for key, item in raw.items()
        if isinstance(item, (int, float, bool)) and not isinstance(item, str)
    }


def _scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> int:
    row = conn.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0) if row else 0


def _json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def content_quality_checks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for target in CONTENT_TARGETS:
        if not table_exists(conn, target["table"]):
            continue
        table = target["table"]
        where = target["where"]
        title_col = target["title"]
        count = _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {where}")
        samples = [
            {"id": int(row["id"]), "title": str(row["item_title"] or f"Record {row['id']}")}
            for row in conn.execute(
                f"SELECT id, {title_col} AS item_title FROM {table} "
                f"WHERE {where} ORDER BY id DESC LIMIT 5"
            ).fetchall()
        ]
        checks.append({**target, "count": count, "samples": samples})

    for table, section in (
        ("members", "members"),
        ("events", "events"),
        ("news", "news"),
        ("publications", "publications"),
        ("research_areas", "research"),
        ("pages", "pages"),
    ):
        if not table_exists(conn, table):
            continue
        count = _scalar(
            conn,
            f"SELECT COUNT(*) FROM {table} "
            "WHERE COALESCE(review_status,'draft') IN ('draft','review','quarantined','duplicate')",
        )
        checks.append(
            {
                "code": f"{table}_review",
                "table": table,
                "label": f"{table.replace('_', ' ').title()} awaiting editorial review",
                "severity": "warning" if count else "info",
                "section": section,
                "count": count,
                "samples": [],
            }
        )
    return checks


def data_quality_workflow_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the operational Data Quality state without duplicating analyzer logic."""
    empty: dict[str, Any] = {
        "run": None,
        "open": 0,
        "bundled": 0,
        "resolved": 0,
        "rejected": 0,
        "deferred": 0,
        "completed": 0,
        "total": 0,
        "bundle_id": None,
        "queued": 0,
    }
    if not table_exists(conn, "quality_runs") or not table_exists(conn, "quality_findings"):
        return empty
    run = conn.execute(
        """SELECT id,status,started_at,completed_at,duration_ms,error_message
           FROM quality_runs ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if not run:
        return empty
    result = {**empty, "run": dict(run)}
    for row in conn.execute(
        """SELECT status,COUNT(*) AS count
           FROM quality_findings WHERE run_id=? GROUP BY status""",
        (int(run["id"]),),
    ):
        status = str(row["status"])
        if status in {"open", "bundled", "resolved", "rejected", "deferred"}:
            result[status] = int(row["count"] or 0)
    result["completed"] = result["resolved"] + result["rejected"] + result["deferred"]
    result["total"] = (
        result["open"] + result["bundled"] + result["resolved"]
        + result["rejected"] + result["deferred"]
    )
    if table_exists(conn, "quality_bundles") and table_exists(conn, "quality_bundle_items"):
        bundle = conn.execute(
            """SELECT id FROM quality_bundles
               WHERE status IN ('draft','validated') ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if bundle:
            result["bundle_id"] = int(bundle["id"])
            result["queued"] = _scalar(
                conn,
                """SELECT COUNT(*) FROM quality_bundle_items
                   WHERE bundle_id=? AND status='pending'""",
                (result["bundle_id"],),
            )
    return result


def process_activity(conn: sqlite3.Connection) -> dict[str, Any]:
    imports: list[dict[str, Any]] = []
    if table_exists(conn, "import_runs"):
        for row in conn.execute(
            "SELECT id,name,source_kind,started_at,completed_at,status,stats_json,notes "
            "FROM import_runs ORDER BY id DESC LIMIT 40"
        ).fetchall():
            item = dict(row)
            item["stats"] = _safe_run_stats(item.pop("stats_json", None))
            imports.append(item)

    recovery: list[dict[str, Any]] = []
    if table_exists(conn, "asset_recovery_state"):
        recovery = []
        for row in conn.execute(
                "SELECT rs.asset_id,a.filename,rs.attempts,rs.last_attempt_at,rs.next_attempt_at,"
                "rs.last_error,rs.terminal FROM asset_recovery_state rs "
                "JOIN assets a ON a.id=rs.asset_id ORDER BY rs.updated_at DESC LIMIT 40"
            ).fetchall():
            item = dict(row)
            item["last_error"] = _safe_recovery_error(item.get("last_error"))
            recovery.append(item)
    return {"imports": imports, "recovery": recovery}


def _directory_size(path: Path, *, max_files: int = 50000) -> tuple[int, int, bool]:
    total = 0
    files = 0
    limited = False
    if not path.is_dir():
        return total, files, limited
    for child in path.rglob("*"):
        if files >= max_files:
            limited = True
            break
        if not child.is_file():
            continue
        files += 1
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return total, files, limited


def _mount_info(path: Path) -> dict[str, Any]:
    result = {"mount_point": "", "filesystem": "", "separate_mount": False}
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return result
    try:
        resolved = path.resolve()
        candidates: list[tuple[int, str, str]] = []
        for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            left, separator, right = line.partition(" - ")
            if not separator:
                continue
            fields = left.split()
            right_fields = right.split()
            if len(fields) < 5 or not right_fields:
                continue
            mount_root = Path(fields[4].replace("\\040", " "))
            try:
                resolved.relative_to(mount_root)
            except ValueError:
                continue
            candidates.append((len(mount_root.parts), str(mount_root), right_fields[0]))
        if candidates:
            _, mount_point, filesystem = max(candidates)
            result.update(
                {
                    "mount_point": mount_point,
                    "filesystem": filesystem,
                    "separate_mount": Path(mount_point) != Path("/"),
                }
            )
    except OSError:
        pass
    return result


def storage_health(config: dict[str, Any], *, scan_sizes: bool = True) -> dict[str, Any]:
    specs = (
        ("Database", Path(config["DATABASE_PATH"]), "file"),
        ("Assets", Path(config["ASSETS_DIR"]), "directory"),
        ("Exports", Path(config["EXPORT_DIR"]), "directory"),
        ("Backups", Path(config["DATABASE_PATH"]).parent / "backups", "directory"),
        ("Logs", Path(config["LOG_DIR"]), "directory"),
    )
    docker = Path("/.dockerenv").exists()
    items: list[dict[str, Any]] = []
    for label, target, kind in specs:
        directory = target.parent if kind == "file" else target
        exists = target.is_file() if kind == "file" else target.is_dir()
        writable = os.access(target if exists else directory, os.W_OK)
        readable = os.access(target if exists else directory, os.R_OK)
        try:
            usage = os.statvfs(directory)
            total_bytes = usage.f_blocks * usage.f_frsize
            free_bytes = usage.f_bavail * usage.f_frsize
        except OSError:
            total_bytes = free_bytes = 0
        if kind == "file":
            try:
                size = target.stat().st_size
            except OSError:
                size = 0
            files, limited = (1 if exists else 0), False
        elif scan_sizes:
            size, files, limited = _directory_size(target)
        else:
            size, files, limited = 0, 0, False
        mount = _mount_info(directory)
        persistence = "host filesystem"
        if docker:
            persistence = "separate mount" if mount["separate_mount"] else "container filesystem — verify volume"
        items.append(
            {
                "label": label,
                "path": str(target),
                "kind": kind,
                "exists": exists,
                "readable": readable,
                "writable": writable,
                "size": size,
                "files": files,
                "scan_limited": limited,
                "total_bytes": total_bytes,
                "free_bytes": free_bytes,
                "free_percent": round((free_bytes * 100 / total_bytes), 1) if total_bytes else 0,
                "persistence": persistence,
                **mount,
            }
        )
    return {"docker": docker, "items": items}


def backup_inventory(database_path: Path) -> dict[str, Any]:
    backup_dir = database_path.parent / "backups"
    backups: list[dict[str, Any]] = []
    if backup_dir.is_dir():
        for path in backup_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            backups.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
                    "age_hours": round((datetime.now(UTC).timestamp() - stat.st_mtime) / 3600, 1),
                }
            )
        backups.sort(key=lambda item: item["modified_at"], reverse=True)
    return {
        "directory": str(backup_dir),
        "items": backups[:100],
        "total": len(backups),
        "latest": backups[0] if backups else None,
    }


def verify_backup(database_path: Path, filename: str) -> dict[str, Any]:
    backup_dir = (database_path.parent / "backups").resolve()
    candidate = (backup_dir / Path(filename).name).resolve()
    try:
        candidate.relative_to(backup_dir)
    except ValueError as exc:
        raise ValueError("Invalid backup filename") from exc
    if candidate.parent != backup_dir or not candidate.is_file():
        raise FileNotFoundError("Backup not found")
    if candidate.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("Unsupported backup file")
    uri = f"file:{candidate}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        quick = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        tables = _scalar(
            conn,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        )
        schema_rows = conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
        schema_digest = hashlib.sha256(
            "\n".join(str(row[2]) for row in schema_rows).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
    finally:
        conn.close()
    return {
        "name": candidate.name,
        "ok": quick == ["ok"],
        "quick_check": quick[:20],
        "tables": tables,
        "schema_digest": schema_digest,
        "size": candidate.stat().st_size,
    }


def safe_settings(config: dict[str, Any], database_settings: dict[str, str]) -> list[dict[str, Any]]:
    # Runtime configuration is never overridden by the general-purpose
    # database settings table. That table is reserved for allowlisted site copy.
    del database_settings
    keys = (
        "ENV",
        "DATABASE_PATH",
        "ASSETS_DIR",
        "EXPORT_DIR",
        "LOG_DIR",
        "MAX_CONTENT_LENGTH",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "LOG_OUTPUT",
        "LOG_RETENTION_DAYS",
        "PAGE_VIEWS_RETENTION_DAYS",
        "PRIVACY_SAFE_METRICS_ENABLED",
        "PRIVACY_SAFE_METRICS_RETENTION_DAYS",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_SAMESITE",
        "TRUST_PROXY",
        "AUTO_MIGRATE_ON_STARTUP",
        "ALLOW_DB_DUMP",
        "MAIL_PROVIDER",
    )
    items = []
    for key in keys:
        value = config.get(key)
        env_present = key in os.environ
        source = "environment" if env_present else "application default"
        if any(part in key.lower() for part in SENSITIVE_SETTING_PARTS):
            value = "Configured" if value else "Not configured"
        elif isinstance(value, Path):
            value = str(value)
        elif key == "MAX_CONTENT_LENGTH":
            value = f"{int(value or 0) // (1024 * 1024)} MB"
        items.append(
            {
                "key": key,
                "value": str(value),
                "source": source,
                "restart": source == "environment",
                "readonly": source != "database",
            }
        )
    return items


def global_search(conn: sqlite3.Connection, query: str, limit_per_table: int = 8) -> list[dict[str, Any]]:
    query = query.strip()[:120]
    if len(query) < 2:
        return []
    targets = (
        ("members", "Members", "display_name", "members"),
        ("news", "News", "title", "news"),
        ("events", "Events", "title", "events"),
        ("publications", "Publications", "title", "publications"),
        ("research_areas", "Research areas", "title", "research"),
        ("sponsors", "Sponsors", "name", "sponsors"),
        ("pages", "Public text records", "title", "pages"),
        ("assets", "Assets", "filename", "assets"),
    )
    results: list[dict[str, Any]] = []
    pattern = f"%{query}%"
    for table, group, title_col, section in targets:
        if not table_exists(conn, table):
            continue
        search_cols = [title_col]
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for optional in ("slug", "original_filename", "source_url"):
            if optional in columns:
                search_cols.append(optional)
        where = " OR ".join(f"{column} LIKE ?" for column in search_cols)
        rows = conn.execute(
            f"SELECT id,{title_col} AS item_title "
            f"{',slug' if 'slug' in columns else ''} FROM {table} "
            f"WHERE {where} ORDER BY id DESC LIMIT ?",
            (*([pattern] * len(search_cols)), limit_per_table),
        ).fetchall()
        for row in rows:
            results.append(
                {
                    "table": table,
                    "group": group,
                    "section": section,
                    "id": int(row["id"]),
                    "title": str(row["item_title"] or f"Record {row['id']}"),
                    "slug": str(row["slug"] or "") if "slug" in row.keys() else "",
                }
            )
    return results


def incident_groups(log_dir: Path, limit: int = 500) -> dict[str, Any]:
    rows = search_logs(log_dir, q=None, level="ALL", limit=limit)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for row in rows:
        if row.get("logger") == "mifp.audit":
            if len(audit) < 40:
                audit.append(row)
            continue
        level = str(row.get("level") or "INFO").upper()
        if level not in {"CRITICAL", "ERROR", "WARNING"}:
            continue
        message = " ".join(str(row.get("message") or "Unknown event").split())
        signature = _INCIDENT_PATH.sub("[path]", _INCIDENT_VOLATILE.sub("#", message))[:240]
        logger = str(row.get("logger") or row.get("file") or "application")
        key = (logger, signature)
        group = groups.setdefault(
            key,
            {
                "logger": logger,
                "signature": signature,
                "count": 0,
                "highest_level": level,
                "first_seen": row.get("when"),
                "last_seen": row.get("when"),
                "request_id": row.get("request_id"),
                "location": row.get("location") or row.get("where"),
                "sample": message[:500],
            },
        )
        group["count"] += 1
        if level in {"CRITICAL", "ERROR"}:
            group["highest_level"] = level
        when = str(row.get("when") or "")
        if when and (not group["first_seen"] or when < str(group["first_seen"])):
            group["first_seen"] = when
        if when and (not group["last_seen"] or when > str(group["last_seen"])):
            group["last_seen"] = when
    incidents = sorted(
        groups.values(),
        key=lambda item: (
            0 if item["highest_level"] in {"CRITICAL", "ERROR"} else 1,
            -int(item["count"]),
            str(item["last_seen"] or ""),
        ),
    )
    return {"incidents": incidents[:100], "audit": audit}


def link_hygiene(conn: sqlite3.Connection) -> dict[str, Any]:
    sources: list[tuple[str, int, str, str]] = []
    if table_exists(conn, "entity_links"):
        sources.extend(
            ("Entity link", int(row["id"]), str(row["url"] or ""), str(row["entity_type"] or "record"))
            for row in conn.execute("SELECT id,url,entity_type FROM entity_links ORDER BY id DESC LIMIT 5000")
        )
    if table_exists(conn, "events"):
        sources.extend(
            ("Event website", int(row["id"]), str(row["remote_url"] or ""), str(row["title"] or "event"))
            for row in conn.execute(
                "SELECT id,remote_url,title FROM events WHERE TRIM(COALESCE(remote_url,''))!='' "
                "ORDER BY id DESC LIMIT 2000"
            )
        )
    if table_exists(conn, "assets"):
        sources.extend(
            ("Asset source", int(row["id"]), str(row["source_url"] or ""), str(row["filename"] or "asset"))
            for row in conn.execute(
                "SELECT id,source_url,filename FROM assets WHERE TRIM(COALESCE(source_url,''))!='' "
                "ORDER BY id DESC LIMIT 5000"
            )
        )

    counts = {"ok": 0, "warning": 0, "danger": 0}
    issues: list[dict[str, Any]] = []
    for source, record_id, value, label in sources:
        parsed = urlparse(value)
        severity = "ok"
        reason = "Valid HTTPS URL"
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            severity, reason = "danger", "Unsupported or incomplete URL"
        elif parsed.username or parsed.password:
            severity, reason = "danger", "URL contains embedded credentials"
        else:
            host = parsed.hostname.lower().rstrip(".")
            blocked = host in {"localhost", "localhost.localdomain"} or host.endswith(".local")
            try:
                address = ipaddress.ip_address(host)
                blocked = blocked or address.is_private or address.is_loopback or address.is_link_local
            except ValueError:
                pass
            if blocked:
                severity, reason = "danger", "URL targets a local or private address"
            elif parsed.scheme == "http":
                severity, reason = "warning", "Unencrypted HTTP URL"
        counts[severity] += 1
        if severity != "ok" and len(issues) < 200:
            issues.append(
                {
                    "source": source,
                    "record_id": record_id,
                    "label": label,
                    "url": value,
                    "severity": severity,
                    "reason": reason,
                }
            )
    return {"counts": counts, "issues": issues, "total": len(sources)}

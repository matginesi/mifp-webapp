from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from ..db.connection import begin_immediate
from ..utils.logger import get_logger, log_event_throttled
from .metrics_service import get_public_traffic_summary

PUBLIC_TABLES = {
    "members": {
        "title": "Members",
        "pk": "id",
        "fields": ["slug", "first_name", "last_name", "display_name", "affiliation", "country", "email", "role_id", "field", "bio", "review_status", "is_active", "sort_order"],
        "search": ["first_name", "last_name", "display_name", "affiliation", "country", "email", "field", "bio"],
        "order": "sort_order ASC, last_name ASC, first_name ASC, id DESC",
    },
    "news": {
        "title": "News",
        "pk": "id",
        "fields": ["title", "slug", "news_type", "card_layout", "date", "date_text", "date_precision", "summary", "body", "review_status", "is_featured", "sort_order"],
        "search": ["title", "news_type", "date_text", "summary", "body"],
        "order": "(COALESCE(date,date_text,'') != '') DESC, COALESCE(date,date_text,'') DESC, COALESCE(sort_order,id) DESC, id DESC",
    },
    "events": {
        "title": "Events",
        "pk": "id",
        "fields": ["title", "slug", "start_date", "end_date", "date_text", "date_precision", "location", "description", "event_type", "series_key", "parent_event_id", "review_status", "is_featured", "remote_url", "sort_order"],
        "search": ["title", "location", "description", "event_type", "series_key"],
        "order": "id DESC",  # final event ordering is done by temporal_sort_events()
    },
    "publications": {
        "title": "Publications",
        "pk": "id",
        "fields": ["title", "slug", "year", "authors", "journal", "doi", "abstract", "date_text", "date_precision", "review_status", "sort_order"],
        "search": ["title", "authors", "journal", "doi", "abstract"],
        "order": "COALESCE(year,0) DESC, id DESC",
    },
    "research_areas": {
        "title": "Research Areas",
        "pk": "id",
        "fields": ["title", "slug", "summary", "description", "review_status", "sort_order"],
        "search": ["title", "summary", "description"],
        "order": "sort_order ASC, title ASC",
    },
    "sponsors": {
        "title": "Sponsors",
        "pk": "id",
        "fields": ["name", "slug", "description", "sponsor_type", "tier", "is_active", "sort_order"],
        "search": ["name", "description", "sponsor_type", "tier"],
        "order": "sort_order ASC, name ASC",
    },
    "pages": {
        "title": "Pages",
        "pk": "id",
        "fields": ["title", "slug", "type", "summary", "body", "version", "effective_date", "nav_group", "menu_order", "review_status", "sort_order"],
        "search": ["title", "slug", "type", "summary", "body"],
"order": "type ASC, title ASC",
    },
}


def _where_clause(meta: dict[str, Any], q: str | None) -> tuple[str, list[Any]]:
    if not q:
        return "", []
    parts = [f"{col} LIKE ?" for col in meta["search"]]
    return " WHERE " + " OR ".join(parts), [f"%{q}%"] * len(parts)


def list_records(conn: sqlite3.Connection, table: str, q: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    assert table in PUBLIC_TABLES
    meta = PUBLIC_TABLES[table]
    where, params = _where_clause(meta, q)

    if table == "events":
        fetch_limit = max(limit * 5, 1000)
        sql = f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ?"
        rows = [dict(r) for r in conn.execute(sql, (*params, fetch_limit)).fetchall()]
        return temporal_sort_events(rows)[:limit]

    sql = f"SELECT * FROM {table}{where} ORDER BY {meta['order']} LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (*params, limit)).fetchall()]


def list_records_paginated(conn: sqlite3.Connection, table: str, q: str | None = None, page: int = 1, per_page: int = 50) -> dict[str, Any]:
    """Return paginated records with total count.

    Returns a dict with keys: records, total, page, per_page, total_pages, total_filtered.
    """
    assert table in PUBLIC_TABLES
    meta = PUBLIC_TABLES[table]
    where, params = _where_clause(meta, q)

    # Get total count (with filter if q is set)
    count_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}{where}", params).fetchone()
    total_filtered = int(count_row["cnt"]) if count_row else 0
    total = int(conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()["cnt"])

    total_pages = max(1, (total_filtered + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    if table == "events":
        fetch_limit = max(per_page * 5, 1000)
        sql = f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ?"
        all_rows = [dict(r) for r in conn.execute(sql, (*params, fetch_limit)).fetchall()]
        sorted_rows = temporal_sort_events(all_rows)
        records = sorted_rows[offset:offset + per_page]
    else:
        sql = f"SELECT * FROM {table}{where} ORDER BY {meta['order']} LIMIT ? OFFSET ?"
        records = [dict(r) for r in conn.execute(sql, (*params, per_page, offset)).fetchall()]

    return {
        "records": records,
        "total": total,
        "total_filtered": total_filtered,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }

_MONTHS = {
    "jan": 1, "january": 1, "gen": 1, "gennaio": 1,
    "feb": 2, "february": 2, "febbraio": 2,
    "mar": 3, "march": 3, "marzo": 3,
    "apr": 4, "april": 4, "aprile": 4,
    "may": 5, "maggio": 5,
    "jun": 6, "june": 6, "giu": 6, "giugno": 6,
    "jul": 7, "july": 7, "lug": 7, "luglio": 7,
    "aug": 8, "august": 8, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "september": 9, "set": 9, "settembre": 9,
    "oct": 10, "october": 10, "ott": 10, "ottobre": 10,
    "nov": 11, "november": 11, "novembre": 11,
    "dec": 12, "december": 12, "dic": 12, "dicembre": 12,
}


def _valid_date(year: int, month: int = 1, day: int = 1) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _event_date_from_text(value: Any) -> date | None:
    """Best-effort parser for event dates from ISO fields, titles or descriptions."""
    text = str(value or "").strip()
    if not text:
        return None

    # ISO-ish: 2024-09-05, 2024/09/05, 2024.09.05, and partial 2024-09 / 2024.
    m = re.search(r"\b(20\d{2}|19\d{2})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?\b", text)
    if m:
        year = int(m.group(1))
        month = int(m.group(2) or 1)
        day = int(m.group(3) or 1)
        d = _valid_date(year, month, day)
        if d:
            return d

    # European numeric: 05/09/2024, 05-09-2024.
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2}|19\d{2})\b", text)
    if m:
        d = _valid_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d:
            return d

    # Textual: 5 September 2024 / September 5, 2024 / 04 - 06 September 2024.
    m = re.search(r"\b(?:\d{1,2}\s*(?:-|–|—|to|al)?\s*)?(\d{1,2})?\s*([A-Za-zÀ-ÿ]+)\s*,?\s*(20\d{2}|19\d{2})\b", text, re.I)
    if m:
        month_value = _MONTHS.get(m.group(2).lower().rstrip("."))
        if month_value:
            d = _valid_date(int(m.group(3)), month_value, int(m.group(1) or 1))
            if d:
                return d

    m = re.search(r"\b([A-Za-zÀ-ÿ]+)\s+(\d{1,2}),?\s*(20\d{2}|19\d{2})\b", text, re.I)
    if m:
        month_value = _MONTHS.get(m.group(1).lower().rstrip("."))
        if month_value:
            d = _valid_date(int(m.group(3)), month_value, int(m.group(2)))
            if d:
                return d

    return None


def _event_effective_date(row: dict[str, Any]) -> date | None:
    for key in ("start_date", "end_date", "date_text", "title", "description"):
        d = _event_date_from_text(row.get(key))
        if d:
            return d
    return None


def temporal_sort_events(rows: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """Sort events in a human-friendly temporal order.

    - upcoming/future events first, nearest to today first;
    - past events next, most recent first;
    - undated events at the bottom, stable by id/title.
    """
    today = today or date.today()

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        d = _event_effective_date(row)
        row_id = int(row.get("id") or 0)
        title = str(row.get("title") or "").lower()
        if d is None:
            return (3, title, -row_id)
        if d >= today:
            return (0, d.toordinal(), title, row_id)
        return (1, -d.toordinal(), title, -row_id)

    return sorted(rows, key=key)


def get_record(conn: sqlite3.Connection, table: str, record_id: int) -> dict[str, Any] | None:
    assert table in PUBLIC_TABLES
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    return dict(row) if row else None


def save_record(
    conn: sqlite3.Connection,
    table: str,
    data: dict[str, Any],
    record_id: int | None = None,
    *,
    commit: bool = True,
) -> int:
    assert table in PUBLIC_TABLES
    meta = PUBLIC_TABLES[table]
    fields = [f for f in meta["fields"] if f in data]
    cleaned = {f: (None if data.get(f) == "" else data.get(f)) for f in fields}
    if table in {"events", "news"} and cleaned.get("date_precision") is None:
        cleaned["date_precision"] = "unknown"
    elif table == "publications" and cleaned.get("date_precision") is None:
        cleaned["date_precision"] = "year"
    for k, v in list(cleaned.items()):
        if k.startswith("is_") or k.endswith("_active") or k in {"sort_order", "year", "role_id", "menu_order", "parent_event_id", "source_priority", "source_order", "display_order"}:
            if v is None:
                del cleaned[k]
                continue
            try:
                cleaned[k] = int(v)
            except (TypeError, ValueError):
                cleaned[k] = None
    begin_immediate(
        conn,
        operation=f"dashboard save {table}",
    )
    if record_id:
        assignments = ", ".join([f"{f}=?" for f in cleaned])
        conn.execute(f"UPDATE {table} SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*cleaned.values(), record_id))
        if commit:
            conn.commit()
        return record_id
    database_defaults = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        if row["dflt_value"] is not None
    }
    cleaned = {
        key: value for key, value in cleaned.items()
        if value is not None or key not in database_defaults
    }
    cols = list(cleaned.keys())
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({placeholders})", tuple(cleaned.values()))
    if commit:
        conn.commit()
    return int(cur.lastrowid or 0)


def count_table(conn: sqlite3.Connection, table: str) -> int:
    assert table in PUBLIC_TABLES or table == "assets"
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def dashboard_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: count_table(conn, table) for table in ["members", "events", "news", "publications", "research_areas", "pages", "assets", "sponsors"]}


def assets_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT COALESCE(kind,'other') AS kind, COUNT(*) AS total, COALESCE(SUM(size),0) AS bytes FROM assets GROUP BY kind ORDER BY total DESC").fetchall()]


def page_type_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT type, COUNT(*) AS total FROM pages GROUP BY type ORDER BY total DESC").fetchall()]


def database_model_health(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Report structural/model issues without deleting or mutating data."""
    checks: list[dict[str, Any]] = []

    def add(key: str, label: str, count: int, severity: str, action: str) -> None:
        checks.append({"key": key, "label": label, "count": int(count or 0), "severity": severity, "action": action})

    add(
        "duplicate_page_identity",
        "Duplicate page identity",
        conn.execute("SELECT COUNT(*) FROM (SELECT slug, type, COUNT(*) c FROM pages GROUP BY slug,type HAVING c>1)").fetchone()[0],
        "warning",
        "Repository reads choose a canonical row; future imports should upsert by slug/type instead of inserting another page.",
    )
    add(
        "duplicate_news_slug",
        "Duplicate news slugs",
        conn.execute("SELECT COUNT(*) FROM (SELECT slug, COUNT(*) c FROM news WHERE slug IS NOT NULL GROUP BY slug HAVING c>1)").fetchone()[0],
        "warning",
        "Importer should normalize/upsert news by slug or source URL before insert.",
    )
    add(
        "members_without_role",
        "Members without role",
        conn.execute("SELECT COUNT(*) FROM members WHERE role_id IS NULL").fetchone()[0],
        "info",
        "Dashboard treats missing role as ordinary profile, but scraper/importers should set role='member'.",
    )
    add(
        "orphan_asset_links",
        "Asset links pointing nowhere",
        conn.execute(
            """
            SELECT COUNT(*)
            FROM asset_links al
            LEFT JOIN assets a ON a.id=al.asset_id
            WHERE a.id IS NULL
            """
        ).fetchone()[0],
        "error",
        "Asset link cleanup should remove only relation rows, not content records.",
    )
    add(
        "missing_canonical_sponsor_how_to",
        "Missing sponsor how-to page",
        conn.execute(
            """
            SELECT CASE WHEN EXISTS (
              SELECT 1 FROM pages
              WHERE slug IN ('sponsors-how-to','how-to-become-a-sponsor')
            ) THEN 0 ELSE 1 END
            """
        ).fetchone()[0],
        "warning",
        "Remote scraper should export /sponsors-how-to-become-a-sponsor as slug sponsors-how-to.",
    )
    return checks


def recent_rows(conn: sqlite3.Connection, table: str, limit: int = 8) -> list[dict[str, Any]]:
    assert table in PUBLIC_TABLES
    label = "title"
    if table == "members":
        label = "display_name"
    if table == "sponsors":
        label = "name"
    return [dict(r) for r in conn.execute(f"SELECT id, {label} AS label, created_at, updated_at FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def list_assets(conn: sqlite3.Connection, q: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if q:
        return [dict(r) for r in conn.execute("SELECT * FROM assets WHERE filename LIKE ? OR original_filename LIKE ? OR caption LIKE ? OR source_url LIKE ? ORDER BY id DESC LIMIT ?", (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", limit)).fetchall()]
    return [dict(r) for r in conn.execute("SELECT * FROM assets ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def list_roles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT id, name, label FROM roles ORDER BY name").fetchall()]


def search_logs(
    log_dir: Path, q: str | None = None, level: str | None = None,
    log_file: str | None = None, event: str | None = None,
    request_id: str | None = None, since: str | None = None,
    until: str | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    """Return parsed text or JSONL log entries for the dashboard table."""
    rows: list[dict[str, Any]] = []
    all_files = sorted(list(log_dir.glob("*.log*")) + list(log_dir.glob("*.jsonl*")), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    if log_file:
        files = [p for p in all_files if p.name.startswith(log_file)]
    else:
        files = all_files[:8]
    for path in files[:8]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        entries = _parse_json_log_entries(path.name, lines[-3000:]) if ".jsonl" in path.name else _group_log_entries(path.name, lines[-3000:])
        for entry in reversed(entries):
            lvl = entry.get("level") or "LOG"
            blob = "\n".join(str(entry.get(k) or "") for k in ["when", "level", "event", "logger", "location", "message", "trace", "file", "request_id"])
            blob += "\n" + json.dumps(entry.get("details") or {}, ensure_ascii=False, default=str)
            if level and level != "ALL" and lvl != level:
                continue
            if q and q.lower() not in blob.lower():
                continue
            if event and event.lower() not in str(entry.get("event") or "").lower():
                continue
            if request_id and request_id.lower() not in str(entry.get("request_id") or "").lower():
                continue
            entry_time = str(entry.get("when") or "")[:16]
            if since and (not entry_time or entry_time < since[:16]):
                continue
            if until and (not entry_time or entry_time > until[:16]):
                continue
            rows.append(entry)
            if len(rows) >= limit:
                return rows
    return rows


def search_logs_paginated(
    log_dir: Path, q: str | None = None, level: str | None = None,
    log_file: str | None = None, event: str | None = None,
    request_id: str | None = None, since: str | None = None,
    until: str | None = None, page: int = 1, per_page: int = 50,
) -> dict[str, Any]:
    """Return paginated log entries and severity totals for the current scope.

    Severity totals intentionally ignore the active severity filter. This lets the
    dashboard show a stable overview while an operator drills into one level.
    All other filters (source, query, correlation and time range) still apply.
    """
    scoped_rows = search_logs(
        log_dir, q=q, level="ALL", log_file=log_file, event=event,
        request_id=request_id, since=since, until=until, limit=10000,
    )
    level_counts: dict[str, int] = {}
    for row in scoped_rows:
        row_level = str(row.get("level") or "LOG").upper()
        level_counts[row_level] = level_counts.get(row_level, 0) + 1

    normalized_level = str(level or "ALL").upper()
    all_rows = (
        scoped_rows
        if normalized_level == "ALL"
        else [row for row in scoped_rows if str(row.get("level") or "LOG").upper() == normalized_level]
    )
    total = len(all_rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "rows": all_rows[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "level_counts": level_counts,
        "scoped_total": len(scoped_rows),
    }


def delete_old_logs(log_dir: Path, days: int = 30) -> int:
    """Delete log files older than `days` days. Returns count of deleted files."""
    import time
    now = time.time()
    cutoff = now - days * 86400
    deleted = 0
    for pattern in ("*.log*", "*.jsonl*"):
        for path in log_dir.glob(pattern):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                deleted += 1
    return deleted


def _parse_json_log_entries(filename: str, lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        core_keys = {
            "timestamp", "level", "logger", "event", "stream", "module",
            "function", "line", "message", "stack_trace", "exception",
            "request_id", "status", "duration_ms",
        }
        details = {key: value for key, value in data.items() if key not in core_keys}
        entries.append({
            "file": filename,
            "when": data.get("timestamp", ""),
            "level": str(data.get("level") or "LOG").upper(),
            "process": "",
            "logger": data.get("logger", ""),
            "event": data.get("event", ""),
            "stream": data.get("stream", ""),
            "where": data.get("logger", ""),
            "location": f"{data.get('module','')}.{data.get('function','')}:{data.get('line','')}",
            "module": data.get("module", ""),
            "function": data.get("function", ""),
            "message": str(data.get("message") or "")[:1200],
            "trace": str(data.get("stack_trace") or data.get("exception") or "")[:6000],
            "request_id": data.get("request_id", ""),
            "status": data.get("status", ""),
            "duration_ms": data.get("duration_ms", ""),
            "details": details,
            "detail_items": _log_detail_items(details),
            "raw": line[:12000],
        })
    return entries


def _group_log_entries(filename: str, lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: str | None = None
    trace: list[str] = []
    header_re = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

    def flush() -> None:
        nonlocal current, trace
        if current is None:
            return
        entries.append(_parse_log_line(filename, current, _guess_level(current), trace))
        current = None
        trace = []

    for line in lines:
        if header_re.match(line):
            flush()
            current = line
            trace = []
        else:
            if current is None:
                current = f" | LOG | | | | {line}"
                trace = []
            else:
                trace.append(line)
    flush()
    return entries


def _parse_log_line(filename: str, line: str, level: str, trace: list[str] | None = None) -> dict[str, Any]:
    parts = [p.strip() for p in line.split(" | ")]
    when = parts[0] if len(parts) > 0 else ""
    lvl = parts[1] if len(parts) > 1 and parts[1] else level
    stream = parts[2] if len(parts) > 2 else ""
    logger = parts[3] if len(parts) > 3 else ""
    location = logger
    context = parts[4] if len(parts) > 4 else ""
    message_parts = parts[5:] if len(parts) > 5 else [parts[-1] if parts else line]
    details: dict[str, Any] = {}
    if message_parts and message_parts[-1].lstrip().startswith("{"):
        try:
            payload = json.loads(message_parts[-1])
        except (json.JSONDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            details = payload
            message_parts = message_parts[:-1]
    message = " | ".join(message_parts).strip()
    module = func = ""
    if location:
        base = location.split(":", 1)[0]
        if "." in base:
            module, func = base.rsplit(".", 1)
        else:
            func = base
    trace_text = "\n".join(trace or []).strip()
    return {
        "file": filename,
        "when": when,
        "level": lvl.strip().upper() or level,
        "process": "",
        "stream": stream,
        "logger": logger,
        "where": logger,
        "location": location,
        "module": module,
        "function": func,
        "message": message[:1200],
        "trace": trace_text[:6000],
        "event": _extract_context_value(context, "event"),
        "request_id": _extract_context_value(context, "rid"),
        "status": details.get("status", _extract_context_value(context, "status")),
        "duration_ms": details.get("duration_ms", _extract_context_value(context, "duration_ms")),
        "details": details,
        "detail_items": _log_detail_items(details),
        "raw": (line + ("\n" + trace_text if trace_text else ""))[:12000],
    }


def _log_detail_items(details: dict[str, Any]) -> list[dict[str, str]]:
    """Return safe, presentation-ready fields for the dashboard log drawer."""
    items: list[dict[str, str]] = []
    for key, value in details.items():
        if value is None:
            display = "—"
        elif isinstance(value, bool):
            display = "Yes" if value else "No"
        elif isinstance(value, (dict, list, tuple)):
            display = json.dumps(value, ensure_ascii=False, default=str)
        else:
            display = str(value)
        items.append({
            "key": str(key),
            "label": str(key).replace("_", " ").strip().title(),
            "display": display,
        })
    return items


def _extract_context_value(context: str, key: str) -> str:
    m = re.search(rf"\b{re.escape(key)}=([^\s]+)", context or "")
    return m.group(1) if m else ""


def _guess_level(line: str) -> str:
    for level in ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]:
        if level in line:
            return level
    return "LOG"


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    assert table in PUBLIC_TABLES
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]




def table_schema(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    assert table in PUBLIC_TABLES
    return [
        {"name": r[1], "type": r[2], "notnull": bool(r[3]), "default": r[4], "pk": bool(r[5])}
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def display_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    essentials = {
        "members": ["id", "first_name", "last_name", "display_name", "role_id", "affiliation", "country", "review_status", "is_active"],
        "news": ["id", "title", "news_type", "card_layout", "date", "review_status", "is_featured"],
        "events": ["id", "title", "start_date", "end_date", "review_status", "is_featured"],
        "publications": ["id", "title", "year", "review_status"],
        "research_areas": ["id", "title", "review_status"],
        "sponsors": ["id", "name", "sponsor_type", "tier", "is_active"],
        "pages": ["id", "title", "type", "effective_date", "review_status"],
    }
    existing = [c["name"] for c in table_schema(conn, table)]
    wanted = essentials.get(table)
    if not wanted:
        return existing
    return [c for c in wanted if c in existing]


def editable_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    readonly = {"id", "created_at", "updated_at", "checksum", "size"}
    return [c["name"] for c in table_schema(conn, table) if c["name"] not in readonly]

def asset_usage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("""
        SELECT a.id, a.filename, a.path, a.kind, a.size,
               (SELECT COUNT(*) FROM asset_links al JOIN members m ON al.entity_type='member' AND m.id=al.entity_id WHERE al.asset_id=a.id)
             + (SELECT COUNT(*) FROM asset_links al JOIN events e ON al.entity_type='event' AND e.id=al.entity_id WHERE al.asset_id=a.id)
             + (SELECT COUNT(*) FROM asset_links al JOIN news n ON al.entity_type='news' AND n.id=al.entity_id WHERE al.asset_id=a.id)
             + (SELECT COUNT(*) FROM asset_links al JOIN publications p ON al.entity_type='publication' AND p.id=al.entity_id WHERE al.asset_id=a.id)
             + (SELECT COUNT(*) FROM asset_links al JOIN research_areas r ON al.entity_type='research_area' AND r.id=al.entity_id WHERE al.asset_id=a.id)
             + (SELECT COUNT(*) FROM asset_links al JOIN pages pg ON al.entity_type='page' AND pg.id=al.entity_id WHERE al.asset_id=a.id)
             + (SELECT COUNT(*) FROM asset_links al JOIN sponsors s ON al.entity_type='sponsor' AND s.id=al.entity_id WHERE al.asset_id=a.id) AS usage_count
        FROM assets a
        ORDER BY usage_count ASC, a.id DESC
    """).fetchall()]


def unused_assets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [r for r in asset_usage(conn) if int(r.get("usage_count") or 0) == 0]


def dashboard_alerts(conn: sqlite3.Connection, log_dir: Path | None = None, assets_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return a list of alerts (editorial + technical) for the dashboard hub."""
    alerts: list[dict[str, Any]] = []

    # Editorial: events upcoming within 30 days
    upcoming = conn.execute(
        "SELECT id, title, start_date FROM events WHERE COALESCE(end_date,start_date) >= date('now') AND start_date <= date('now','+30 days') ORDER BY start_date LIMIT 5"
    ).fetchall()
    for row in upcoming:
        alerts.append({
            "type": "info",
            "label": "Upcoming event",
            "message": f"{row['title']} — {row['start_date']}",
            "action_url": "/dashboard/content/events",
        })

    # Editorial: content with drafts older than 14 days
    for tbl, label in [("news", "News"), ("events", "Events"), ("publications", "Publications")]:
        old = conn.execute(
            f"SELECT COUNT(*) AS c FROM {tbl} WHERE COALESCE(review_status,'draft')!='published' AND updated_at < datetime('now','-14 days')"
        ).fetchone()
        if old and old["c"]:
            alerts.append({
                "type": "warning",
                "label": f"Draft {label}",
                "message": f"{old['c']} unpublished items older than 14 days",
                "action_url": f"/dashboard/content/{tbl}",
            })

    # Technical: assets with missing files (disk truth, not stale storage_status)
    if assets_dir is not None:
        try:
            from .asset_cleanup import asset_library_summary

            summary = asset_library_summary(conn, Path(assets_dir), scan_orphans=False)
            missing_count = summary["missing"]
        except Exception:
            summary = {}
            missing_count = 0
        if missing_count:
            alerts.append({
                "type": "warning" if summary.get("recoverable") else "error",
                "label": "Missing asset files",
                "message": (
                    f"{missing_count} records without a local file · "
                    f"{summary.get('recoverable', 0)} have a recovery URL · "
                    f"{summary.get('errors', 0)} need a source or stopped retrying"
                ),
                "action_url": "/dashboard/assets",
            })

    # Technical: recent errors from audit log
    try:
        selected_log_dir = Path(log_dir) if log_dir is not None else Path(os.environ.get("MIFP_LOG_DIR", "logs"))
        errs = search_logs(selected_log_dir, q=None, level="ERROR", limit=5)
        if errs:
            alerts.append({
                "type": "error",
                "label": "Recent errors",
                "message": f"{len(errs)} errors in the last log entries",
                "action_url": "/dashboard/logs",
            })
    except Exception as exc:
        log_event_throttled(
            get_logger("dashboard"),
            "dashboard.alert_log_read_failed",
            "Dashboard could not inspect recent log errors",
            interval_seconds=60,
            error_type=type(exc).__name__,
        )

    return alerts


def privacy_safe_visit_stats(conn: sqlite3.Connection, days: int = 30) -> dict[str, Any]:
    return get_public_traffic_summary(conn, days=days)

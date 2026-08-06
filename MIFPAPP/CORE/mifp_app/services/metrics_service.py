from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import current_app, has_app_context

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")
_LONG_TOKEN_RE = re.compile(r"(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{16,}")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
_LONG_NUMBER_RE = re.compile(r"\d{5,}")

_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "svg", "avif"}
_DOC_EXTS = {"doc", "docx", "xls", "xlsx", "csv", "json", "txt"}
_PAGE_EXTS = {"", "html", "htm"}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _safe_scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int((row[0] if row else 0) or 0)
    except sqlite3.Error:
        return 0


def _safe_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _metric_date(days_back: int) -> str:
    return (date.today() - timedelta(days=days_back)).isoformat()


def classify_asset_key(filename: str | None) -> str:
    ext = Path(str(filename or "")).suffix.lower().lstrip(".")
    if ext == "pdf":
        return "pdf"
    if ext in {"doc", "docx"}:
        return "document"
    if ext in {"xls", "xlsx", "csv", "json"}:
        return "data"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext == "zip":
        return "zip"
    return "other"


def normalize_metric_path(path: str) -> str:
    raw = str(path or "/").strip()
    split = urlsplit(raw)
    clean = split.path or raw.split("?", 1)[0].split("#", 1)[0] or "/"
    if not clean.startswith("/"):
        clean = "/" + clean
    clean = re.sub(r"/+", "/", clean)
    if clean.lower().startswith("/media/"):
        return f"/media/{classify_asset_key(clean)}"
    clean = _EMAIL_RE.sub("[redacted]", clean)
    clean = _UUID_RE.sub("[token]", clean)
    clean = _LONG_TOKEN_RE.sub("[token]", clean)
    clean = _LONG_NUMBER_RE.sub("[number]", clean)
    if clean != "/":
        clean = clean.rstrip("/")

    lowered = clean.lower()
    if lowered.startswith("/dashboard"):
        return "/dashboard/[admin]"
    if lowered.startswith("/auth") or lowered.startswith("/login"):
        return "/auth/[admin]"
    if lowered.startswith("/reset-password/"):
        return "/reset-password/[token]"
    if len(clean) > 180:
        clean = clean[:180].rstrip("/")
    return clean or "/"


def response_time_bucket(duration_ms: float) -> str:
    if duration_ms < 100:
        return "lt_100ms"
    if duration_ms < 500:
        return "lt_500ms"
    if duration_ms < 1000:
        return "lt_1s"
    if duration_ms < 5000:
        return "lt_5s"
    return "gte_5s"


def increment_daily(conn: sqlite3.Connection, scope: str, metric_name: str, metric_key: str = "", amount: int = 1) -> None:
    conn.execute(
        """
        INSERT INTO metrics_daily(date, scope, metric_name, metric_key, metric_value)
        VALUES(date('now'), ?, ?, ?, ?)
        ON CONFLICT(date, scope, metric_name, metric_key)
        DO UPDATE SET
            metric_value = metric_value + excluded.metric_value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (scope, metric_name, metric_key or "", int(amount)),
    )


def set_daily(conn: sqlite3.Connection, scope: str, metric_name: str, metric_key: str, value: int) -> None:
    conn.execute(
        """
        INSERT INTO metrics_daily(date, scope, metric_name, metric_key, metric_value)
        VALUES(date('now'), ?, ?, ?, ?)
        ON CONFLICT(date, scope, metric_name, metric_key)
        DO UPDATE SET
            metric_value = excluded.metric_value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (scope, metric_name, metric_key or "", int(value)),
    )


def get_metric_range(conn: sqlite3.Connection, days: int = 30, scope: str | None = None) -> dict[str, Any]:
    days = max(1, min(int(days or 30), 3660))
    since = _metric_date(days - 1)
    params: list[Any] = [since]
    where = "date >= ?"
    if scope:
        where += " AND scope = ?"
        params.append(scope)
    rows = _safe_rows(
        conn,
        f"""
        SELECT date, scope, metric_name, metric_key, metric_value
        FROM metrics_daily
        WHERE {where}
        ORDER BY date, scope, metric_name, metric_key
        """,
        tuple(params),
    )
    return {"days": days, "since": since, "rows": rows}


def get_public_traffic_summary(conn: sqlite3.Connection, days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    since_week = _metric_date(6)
    since_month = _metric_date(days - 1)
    previous_start = _metric_date((days * 2) - 1)
    previous_end = _metric_date(days)
    previous_week_start = _metric_date(13)
    previous_week_end = _metric_date(7)
    today = date.today().isoformat()
    total_expr = "COALESCE(SUM(metric_value),0)"
    today_total = _safe_scalar(conn, f"SELECT {total_expr} FROM metrics_daily WHERE scope='public_site' AND date=? AND metric_name='page_view'", (today,))
    week_total = _safe_scalar(conn, f"SELECT {total_expr} FROM metrics_daily WHERE scope='public_site' AND date>=? AND metric_name='page_view'", (since_week,))
    month_total = _safe_scalar(conn, f"SELECT {total_expr} FROM metrics_daily WHERE scope='public_site' AND date>=? AND metric_name='page_view'", (since_month,))
    previous_total = _safe_scalar(
        conn,
        f"SELECT {total_expr} FROM metrics_daily WHERE scope='public_site' AND date BETWEEN ? AND ? AND metric_name='page_view'",
        (previous_start, previous_end),
    )
    previous_week_total = _safe_scalar(
        conn,
        f"SELECT {total_expr} FROM metrics_daily WHERE scope='public_site' AND date BETWEEN ? AND ? AND metric_name='page_view'",
        (previous_week_start, previous_week_end),
    )
    by_day = _safe_rows(
        conn,
        """
        SELECT date AS day, SUM(metric_value) AS hits
        FROM metrics_daily
        WHERE scope='public_site' AND metric_name='page_view' AND date>=?
        GROUP BY date
        ORDER BY date
        """,
        (since_month,),
    )
    top_pages = _safe_rows(
        conn,
        """
        SELECT metric_key AS path, SUM(metric_value) AS hits
        FROM metrics_daily
        WHERE scope='public_site' AND metric_name='page_view' AND date>=?
        GROUP BY metric_key
        ORDER BY hits DESC, metric_key
        LIMIT 10
        """,
        (since_month,),
    )
    status_breakdown = _safe_rows(
        conn,
        """
        SELECT metric_name AS status, SUM(metric_value) AS total
        FROM metrics_daily
        WHERE scope='public_site' AND metric_name LIKE 'http_%' AND date>=?
        GROUP BY metric_name
        ORDER BY total DESC, metric_name
        """,
        (since_month,),
    )
    top_404 = _safe_rows(
        conn,
        """
        SELECT metric_key AS path, SUM(metric_value) AS hits
        FROM metrics_daily
        WHERE scope='technical' AND metric_name='http_404' AND date>=?
        GROUP BY metric_key
        ORDER BY hits DESC, metric_key
        LIMIT 10
        """,
        (since_month,),
    )
    errors_404 = _safe_scalar(
        conn,
        "SELECT COALESCE(SUM(metric_value),0) FROM metrics_daily WHERE scope='technical' AND metric_name='http_404' AND date>=?",
        (since_month,),
    )
    errors_5xx = _safe_scalar(
        conn,
        "SELECT COALESCE(SUM(metric_value),0) FROM metrics_daily WHERE scope='technical' AND metric_name='http_5xx' AND date>=?",
        (since_month,),
    )
    downloads = _safe_rows(
        conn,
        """
        SELECT metric_key AS kind, SUM(metric_value) AS total
        FROM metrics_daily
        WHERE scope='public_download' AND metric_name='download' AND date>=?
        GROUP BY metric_key
        ORDER BY total DESC, metric_key
        """,
        (since_month,),
    )
    def delta_percent(current: int, previous: int) -> float | None:
        if previous == 0:
            return 0.0 if current == 0 else None
        return round(((current - previous) / previous) * 100, 1)

    return {
        "days": days,
        "today": {"total": today_total},
        "week": {
            "total": week_total,
            "previous_total": previous_week_total,
            "delta_percent": delta_percent(week_total, previous_week_total),
        },
        "month": {
            "total": month_total,
            "previous_total": previous_total,
            "delta_percent": delta_percent(month_total, previous_total),
        },
        "period": {
            "total": month_total,
            "previous_total": previous_total,
            "delta_percent": delta_percent(month_total, previous_total),
        },
        "top_pages": top_pages,
        "by_day": by_day,
        "status_breakdown": status_breakdown,
        "top_404": top_404,
        "errors_404": errors_404,
        "errors_5xx": errors_5xx,
        "downloads": downloads,
        "privacy_safe": True,
    }


def get_content_quality_summary(conn: sqlite3.Connection) -> dict[str, int]:
    data = {
        "members_total": _safe_scalar(conn, "SELECT COUNT(*) FROM members"),
        "members_without_affiliation": _safe_scalar(conn, "SELECT COUNT(*) FROM members WHERE COALESCE(TRIM(affiliation),'')=''"),
        "members_without_country": _safe_scalar(conn, "SELECT COUNT(*) FROM members WHERE COALESCE(TRIM(country),'')=''"),
        "members_without_role": _safe_scalar(conn, "SELECT COUNT(*) FROM members WHERE role_id IS NULL"),
        "events_without_date": _safe_scalar(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(TRIM(start_date),'')=''"),
        "events_without_place": _safe_scalar(conn, "SELECT COUNT(*) FROM events WHERE COALESCE(TRIM(location),'')=''"),
        "events_without_image": _safe_scalar(conn, "SELECT COUNT(*) FROM events WHERE id NOT IN (SELECT entity_id FROM asset_links WHERE entity_type='event' AND role IN ('cover','gallery'))"),
        "news_without_date": _safe_scalar(conn, "SELECT COUNT(*) FROM news WHERE COALESCE(TRIM(date),'')=''"),
        "news_without_image": _safe_scalar(conn, "SELECT COUNT(*) FROM news WHERE id NOT IN (SELECT entity_id FROM asset_links WHERE entity_type='news' AND role IN ('cover','gallery'))"),
        "publications_without_doi": _safe_scalar(conn, "SELECT COUNT(*) FROM publications WHERE COALESCE(TRIM(doi),'')=''"),
        "publications_without_link": _safe_scalar(conn, "SELECT COUNT(*) FROM publications WHERE id NOT IN (SELECT entity_id FROM entity_links WHERE entity_type='publication') AND id NOT IN (SELECT entity_id FROM asset_links WHERE entity_type='publication')"),
        "sponsors_without_logo": _safe_scalar(conn, "SELECT COUNT(*) FROM sponsors WHERE id NOT IN (SELECT entity_id FROM asset_links WHERE entity_type='sponsor' AND role='logo')"),
        "asset_records": _safe_scalar(conn, "SELECT COUNT(*) FROM assets"),
        "asset_links": _safe_scalar(conn, "SELECT COUNT(*) FROM asset_links"),
        "asset_unused_db": _safe_scalar(conn, "SELECT COUNT(*) FROM assets WHERE id NOT IN (SELECT asset_id FROM asset_links WHERE asset_id IS NOT NULL)"),
        "pages_without_translation": 0,
    }
    if has_app_context():
        try:
            from .asset_cleanup import build_asset_cleanup_plan

            plan = build_asset_cleanup_plan(conn, current_app.config["ASSETS_DIR"])
            data["asset_missing_files"] = len(plan.missing_file_assets)
            data["asset_orphan_files"] = len(plan.orphan_files)
            data["asset_unused_db"] = len(plan.unused_db_assets)
        except Exception:
            data.setdefault("asset_missing_files", 0)
            data.setdefault("asset_orphan_files", 0)
    return data


def get_import_export_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    def count_value(value: Any) -> int:
        if value is None or isinstance(value, bool):
            return 0
        if isinstance(value, dict):
            return sum(count_value(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return len(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    runs = _safe_rows(conn, "SELECT id, name, source_kind, status, stats_json, started_at, completed_at FROM import_runs ORDER BY id DESC LIMIT 20")
    totals = {"records_read": 0, "records_imported": 0, "records_skipped": 0, "import_errors": 0}
    recent_runs: list[dict[str, Any]] = []
    for row in runs:
        stats = {}
        try:
            stats = json.loads(row.get("stats_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            stats = {}
        read = count_value(stats.get("records_read") or stats.get("read") or stats.get("total"))
        explicit_imported = stats.get("records_imported") or stats.get("imported")
        imported = count_value(explicit_imported) if explicit_imported is not None else (
            count_value(stats.get("inserted") or stats.get("created")) + count_value(stats.get("updated"))
        )
        skipped = count_value(stats.get("records_skipped") or stats.get("skipped") or stats.get("discarded"))
        errors = count_value(stats.get("errors_count") or stats.get("errors"))
        totals["records_read"] += read
        totals["records_imported"] += imported
        totals["records_skipped"] += skipped
        totals["import_errors"] += errors
        row.update({"records_read": read, "records_imported": imported, "records_skipped": skipped, "errors_count": errors})
        recent_runs.append(row)
    return {
        "recent_import_runs": recent_runs[:5],
        "records_read": totals["records_read"],
        "records_imported": totals["records_imported"],
        "records_skipped": totals["records_skipped"],
        "import_errors": totals["import_errors"],
    }


def sanitize_unknown_question(text: str) -> str:
    clean = str(text or "").lower().strip()
    clean = _URL_RE.sub("[url]", clean)
    clean = _EMAIL_RE.sub("[email]", clean)
    clean = _PHONE_RE.sub("[phone]", clean)
    clean = _LONG_TOKEN_RE.sub("[token]", clean)
    clean = _LONG_NUMBER_RE.sub("[number]", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:160]


def safe_extra_json(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    forbidden = {"ip", "client_ip", "user_agent", "user_agent_hash", "referrer", "email", "phone", "session", "csrf"}
    clean = {k: v for k, v in data.items() if str(k).lower() not in forbidden}
    return json.dumps(clean, ensure_ascii=False, default=str) if clean else None

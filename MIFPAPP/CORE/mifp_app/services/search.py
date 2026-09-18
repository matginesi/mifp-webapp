from __future__ import annotations

import html as _html
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

from flask import url_for

_LOGGER = logging.getLogger(__name__)

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 120
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
# Upper bound on rows scanned per target. MIFP's largest searchable table is
# well under this, and the cap keeps an unauthenticated search request bounded.
_SCAN_LIMIT = 5000

_TAG_RE = re.compile(r"<[^>]+>")

TYPE_LABELS = {
    "event": "Events",
    "archive_event": "Archive events",
    "news": "News",
    "member": "Members",
    "publication": "Publications",
    "research_area": "Research areas",
    "page": "Pages",
    "sponsor": "Sponsors",
    "asset": "Assets",
    "conference_site": "Conference sites",
    "conference_person": "Conference people",
}


def fold(value: Any) -> str:
    text = str(value or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def normalize_query(raw: Any) -> str | None:
    text = " ".join(str(raw or "").split())
    if len(text) < MIN_QUERY_LENGTH:
        return None
    return text[:MAX_QUERY_LENGTH]


@dataclass(frozen=True)
class SearchTarget:
    type: str
    from_sql: str
    id_expr: str
    title_expr: str
    subtitle_expr: str
    text_columns: tuple[str, ...]
    public_where: str | None
    dashboard_where: str | None
    public_url: Callable[[dict[str, Any]], str] | None
    dashboard_url: Callable[[dict[str, Any]], str] | None
    dashboard_text_columns: tuple[str, ...] = ()
    date_expr: str | None = None
    extra_exprs: tuple[tuple[str, str], ...] = ()
    result_type: Callable[[dict[str, Any]], str] | None = None


def _event_public_url(row: dict[str, Any]) -> str:
    slug = str(row.get("_slug") or "")
    category = row.get("_archive_category")
    year = row.get("_archive_year")
    if category and year and slug:
        return url_for("public.archive_detail", category=category, year=int(year), slug=slug)
    if slug:
        return url_for("public.event_detail", slug=slug)
    return url_for("public.events")


def _event_dashboard_url(row: dict[str, Any]) -> str:
    title = row.get("_title") or ""
    if row.get("_archive_category") and row.get("_archive_year"):
        return url_for("dashboard.archive_page", q=title)
    return url_for("dashboard.events", q=title)


def _event_result_type(row: dict[str, Any]) -> str:
    if row.get("_archive_category") and row.get("_archive_year"):
        return "archive_event"
    return "event"


def _news_public_url(row: dict[str, Any]) -> str:
    slug = str(row.get("_slug") or "")
    if not slug:
        return url_for("public.news")
    return url_for("public.news_detail", slug=slug)


def _news_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.content", section="news", q=row.get("_title") or "")


def _member_public_url(row: dict[str, Any]) -> str:
    return url_for("public.members", q=row.get("_title") or "")


def _member_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.content", section="members", q=row.get("_title") or "")


def _publication_public_url(row: dict[str, Any]) -> str:
    slug = str(row.get("_slug") or "")
    base = url_for("public.publications")
    return f"{base}#publication-{slug}" if slug else base


def _publication_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.content", section="publications", q=row.get("_title") or "")


def _research_public_url(row: dict[str, Any]) -> str:
    return url_for("public.research") + "#research-directory"


def _research_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.content", section="research", q=row.get("_title") or "")


_PAGE_PUBLIC_ROUTES = {
    "about": "public.about",
    "manifesto": "public.manifesto",
    "privacy": "public.privacy",
    "cookie_policy": "public.cookie_policy",
    "code_of_conduct": "public.code_of_conduct",
}


def _page_public_url(row: dict[str, Any]) -> str:
    endpoint = _PAGE_PUBLIC_ROUTES.get(str(row.get("_type") or ""))
    return url_for(endpoint) if endpoint else url_for("public.home")


def _page_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.institutional")


def _sponsor_public_url(row: dict[str, Any]) -> str:
    slug = str(row.get("_slug") or "")
    if not slug:
        return url_for("public.sponsors")
    return url_for("public.sponsor_detail", slug=slug)


def _sponsor_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.content", section="sponsors", q=row.get("_title") or "")


def _asset_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.assets_page", q=row.get("_title") or "")


def _conference_site_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.conference_sites")


def _conference_person_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.conference_sites")


SEARCH_TARGETS: tuple[SearchTarget, ...] = (
    SearchTarget(
        type="event",
        from_sql="events e LEFT JOIN event_archive_entries a ON a.event_id = e.id",
        id_expr="e.id",
        title_expr="e.title",
        subtitle_expr="CASE WHEN a.id IS NOT NULL THEN 'Archive event' ELSE 'Event' END",
        date_expr="COALESCE(e.start_date, e.end_date, e.date_text)",
        text_columns=(
            "e.description", "e.location", "e.date_text", "e.series_key",
            "e.speakers_json", "e.chairs_json", "e.committee_json",
            "a.acronym", "a.summary", "a.topics_json", "a.people_json",
        ),
        extra_exprs=(
            ("_slug", "e.slug"),
            ("_archive_category", "a.category"),
            ("_archive_year", "a.archive_year"),
        ),
        public_where="COALESCE(e.review_status,'draft')='published'",
        dashboard_where="1=1",
        public_url=_event_public_url,
        dashboard_url=_event_dashboard_url,
        result_type=_event_result_type,
    ),
    SearchTarget(
        type="news",
        from_sql="news n",
        id_expr="n.id",
        title_expr="n.title",
        subtitle_expr="'News'",
        date_expr="COALESCE(n.date, n.date_text)",
        text_columns=("n.summary", "n.body", "n.news_type", "n.date_text"),
        extra_exprs=(("_slug", "n.slug"),),
        public_where="COALESCE(n.review_status,'draft')='published'",
        dashboard_where="1=1",
        public_url=_news_public_url,
        dashboard_url=_news_dashboard_url,
    ),
    SearchTarget(
        type="member",
        from_sql="members m",
        id_expr="m.id",
        title_expr="m.display_name",
        subtitle_expr="TRIM(COALESCE(m.affiliation,'') || CASE WHEN COALESCE(m.country,'')<>'' THEN ' · ' || m.country ELSE '' END)",
        date_expr=None,
        text_columns=("m.first_name", "m.last_name", "m.affiliation", "m.country", "m.field"),
        extra_exprs=(("_slug", "m.slug"),),
        public_where="COALESCE(m.review_status,'draft')='published' AND m.is_active=1",
        dashboard_where="1=1",
        dashboard_text_columns=("m.bio",),
        public_url=_member_public_url,
        dashboard_url=_member_dashboard_url,
    ),
    SearchTarget(
        type="publication",
        from_sql="publications p",
        id_expr="p.id",
        title_expr="p.title",
        subtitle_expr="TRIM(COALESCE(p.authors,'') || CASE WHEN COALESCE(p.journal,'')<>'' THEN ' · ' || p.journal ELSE '' END)",
        date_expr="CAST(p.year AS TEXT)",
        text_columns=("p.authors", "p.journal", "p.abstract", "p.doi"),
        extra_exprs=(("_slug", "p.slug"),),
        public_where="COALESCE(p.review_status,'draft')='published'",
        dashboard_where="1=1",
        public_url=_publication_public_url,
        dashboard_url=_publication_dashboard_url,
    ),
    SearchTarget(
        type="research_area",
        from_sql="research_areas r",
        id_expr="r.id",
        title_expr="r.title",
        subtitle_expr="'Research area'",
        date_expr=None,
        text_columns=("r.summary", "r.description"),
        extra_exprs=(("_slug", "r.slug"),),
        public_where="COALESCE(r.review_status,'draft')='published'",
        dashboard_where="1=1",
        public_url=_research_public_url,
        dashboard_url=_research_dashboard_url,
    ),
    SearchTarget(
        type="page",
        from_sql="pages pg",
        id_expr="pg.id",
        title_expr="pg.title",
        subtitle_expr="pg.type",
        date_expr=None,
        text_columns=("pg.summary", "pg.body"),
        extra_exprs=(("_type", "pg.type"), ("_slug", "pg.slug")),
        public_where="COALESCE(pg.review_status,'draft')='published'",
        dashboard_where="1=1",
        public_url=_page_public_url,
        dashboard_url=_page_dashboard_url,
    ),
    SearchTarget(
        type="sponsor",
        from_sql="sponsors s",
        id_expr="s.id",
        title_expr="s.name",
        subtitle_expr="TRIM(COALESCE(s.sponsor_type,'') || CASE WHEN COALESCE(s.tier,'')<>'' THEN ' · ' || s.tier ELSE '' END)",
        date_expr=None,
        text_columns=("s.description",),
        extra_exprs=(("_slug", "s.slug"),),
        public_where="s.is_active=1",
        dashboard_where="1=1",
        public_url=_sponsor_public_url,
        dashboard_url=_sponsor_dashboard_url,
    ),
    SearchTarget(
        type="asset",
        from_sql="assets a",
        id_expr="a.id",
        title_expr="a.filename",
        subtitle_expr="TRIM(COALESCE(a.kind,'') || CASE WHEN COALESCE(a.mime_type,'')<>'' THEN ' · ' || a.mime_type ELSE '' END)",
        date_expr="a.created_at",
        text_columns=("a.original_filename", "a.alt_text", "a.caption", "a.source_url"),
        extra_exprs=(),
        public_where=None,
        dashboard_where="1=1",
        public_url=None,
        dashboard_url=_asset_dashboard_url,
    ),
    SearchTarget(
        type="conference_site",
        from_sql="conference_sites c",
        id_expr="c.id",
        title_expr="c.title",
        subtitle_expr="TRIM(COALESCE(c.acronym,'') || CASE WHEN COALESCE(c.city,'')<>'' THEN ' · ' || c.city ELSE '' END)",
        date_expr="c.start_date",
        text_columns=("c.acronym", "c.city", "c.venue", "c.description"),
        extra_exprs=(("_slug", "c.slug"),),
        public_where=None,
        dashboard_where="1=1",
        public_url=None,
        dashboard_url=_conference_site_url,
    ),
    SearchTarget(
        type="conference_person",
        from_sql="conference_people cp",
        id_expr="cp.id",
        title_expr="cp.name",
        subtitle_expr="TRIM(COALESCE(cp.role,'') || CASE WHEN COALESCE(cp.affiliation,'')<>'' THEN ' · ' || cp.affiliation ELSE '' END)",
        date_expr=None,
        text_columns=("cp.affiliation", "cp.contribution_title"),
        extra_exprs=(),
        public_where=None,
        dashboard_where="1=1",
        public_url=None,
        dashboard_url=_conference_person_url,
    ),
)


def _plain(value: Any) -> str:
    text = _html.unescape(str(value or ""))
    text = _TAG_RE.sub(" ", text)
    return " ".join(text.split())


def _snippet(text: str, folded_query: str, width: int = 160) -> str:
    folded = fold(text)
    pos = folded.find(folded_query)
    start = max(0, pos - 40) if pos > 0 else 0
    snippet = text[start:start + width].strip()
    if start > 0:
        snippet = "…" + snippet
    if start + width < len(text):
        snippet = snippet.rstrip() + "…"
    return snippet


def _score(title_folded: str, folded_query: str) -> int:
    if title_folded == folded_query:
        return 0
    if title_folded.startswith(folded_query):
        return 1
    if folded_query in title_folded:
        return 2
    return 3


def _date_rank(value: Any) -> float:
    numbers = re.findall(r"\d+", str(value or ""))
    if not numbers:
        return 0.0
    year = int(numbers[0][:4]) if numbers[0] else 0
    month = int(numbers[1][:2]) if len(numbers) > 1 else 0
    day = int(numbers[2][:2]) if len(numbers) > 2 else 0
    return float(year * 10000 + month * 100 + day)


def _dedupe(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    for result in results:
        key = (result["type"], result["id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def _build_result(
    data: dict[str, Any],
    target: SearchTarget,
    folded_query: str,
    url_builder: Callable[[dict[str, Any]], str],
    column_count: int,
) -> dict[str, Any] | None:
    title = _plain(data.get("_title"))
    score = _score(fold(title), folded_query)
    excerpt = ""
    if score == 3:
        matched = False
        for index in range(column_count):
            text = _plain(data.get(f"_c{index}"))
            if text and folded_query in fold(text):
                excerpt = _snippet(text, folded_query)
                matched = True
                break
        if not matched:
            return None
    url = url_builder(data)
    result_type = target.result_type(data) if target.result_type else target.type
    return {
        "type": result_type,
        "group": TYPE_LABELS.get(result_type, result_type.replace("_", " ").title()),
        "id": int(data["_id"]),
        "title": title or f"Record {data['_id']}",
        "subtitle": _plain(data.get("_subtitle")),
        "excerpt": excerpt,
        "date": _plain(data.get("_date")),
        "url": url,
        "score": score,
    }


def _empty_page(query: str, limit: int, offset: int) -> dict[str, Any]:
    return {
        "query": query,
        "results": [],
        "total": 0,
        "limit": limit,
        "offset": offset,
        "has_more": False,
        "failed": False,
    }


def run_search(
    conn: sqlite3.Connection,
    query: Any,
    *,
    scope: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    normalized = normalize_query(query)
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    if normalized is None:
        return _empty_page("", limit, offset)
    folded_query = fold(normalized)
    results: list[dict[str, Any]] = []
    failed = False
    for target in SEARCH_TARGETS:
        if scope == "public":
            where = target.public_where
            url_builder = target.public_url
            text_columns = target.text_columns
        elif scope == "dashboard":
            where = target.dashboard_where
            url_builder = target.dashboard_url
            text_columns = (*target.text_columns, *target.dashboard_text_columns)
        else:
            continue
        if not where or url_builder is None:
            continue
        columns = (target.title_expr, *text_columns)
        select_parts = [
            f"{target.id_expr} AS _id",
            f"{target.title_expr} AS _title",
            f"{target.subtitle_expr} AS _subtitle",
        ]
        if target.date_expr:
            select_parts.append(f"{target.date_expr} AS _date")
        for alias, expr in target.extra_exprs:
            select_parts.append(f"{expr} AS {alias}")
        select_parts.extend(f"{column} AS _c{index}" for index, column in enumerate(columns))
        sql = (
            f"SELECT {', '.join(select_parts)} FROM {target.from_sql} "
            f"WHERE ({where}) ORDER BY {target.id_expr} ASC LIMIT ?"
        )
        try:
            rows = conn.execute(sql, (_SCAN_LIMIT,)).fetchall()
        except sqlite3.Error:
            _LOGGER.exception("search target failed type=%s", target.type)
            failed = True
            continue
        for row in rows:
            try:
                item = _build_result(dict(row), target, folded_query, url_builder, len(columns))
            except Exception:
                _LOGGER.exception("search destination failed type=%s", target.type)
                failed = True
                continue
            if item is not None:
                results.append(item)
    results = _dedupe(results)
    results.sort(key=lambda item: (item["score"], -_date_rank(item["date"]), -item["id"]))
    total = len(results)
    page = results[offset:offset + limit]
    return {
        "query": normalized,
        "results": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "failed": failed,
    }

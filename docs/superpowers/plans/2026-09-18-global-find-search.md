# Global Find / Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Find/Search as one global search service shared by a new public `/search` page and the existing dashboard Find, then perform a focused, evidence-based cleanup.

**Architecture:** A single `services/search.py` module holds all query normalization, accent/case folding, per-entity target definitions, ranking, deduplication, and result normalization. Public and dashboard routes call it with a `scope`; only visibility clauses and destination URL builders differ. Search reads canonical tables directly, so no index or reindex step exists.

**Tech Stack:** Python 3, Flask, SQLite (no FTS, no new dependencies), Jinja templates, pytest.

> **Post-implementation amendments (final review + CI fix):** the delivered code additionally
> (a) moves `members.bio` to a new `SearchTarget.dashboard_text_columns` field so it
> is dashboard-only, (b) sets `failed=True` when a destination URL builder raises
> (instead of silently dropping the row) and falls back to listing URLs for empty
> slugs, and (c) performs accent/case folding in Python rather than SQL. The original
> SQL-side folding built ~52 nested `replace()` calls per column and overflowed
> SQLite's parser stack on CI (`sqlite3.OperationalError: parser stack overflow`).
> SQL now only applies visibility and bounds the scan
> (`ORDER BY id ASC LIMIT _SCAN_LIMIT = 5000`); `total` is the number of matched
> records found. See `services/search.py` for the final implementation.

## Global Constraints

- No new runtime dependencies; no external search service.
- No database schema change and no FTS index.
- All search input is untrusted: parameterized SQL only; identifiers come from server-side constants.
- Public scope returns only `review_status='published'` / active records; never emails, admin notes, paths, secrets, or internal tables.
- No UI redesign: keep the dashboard Find page and public navbar layout; only add one small navbar search icon and one results page.
- Python files use `from __future__ import annotations`; no comments that merely restate code.
- Run tests with: `pytest <path> -v`.
- Existing suite must still pass: `bash test_all.sh` (or `pytest TESTS -q`).

---

### Task 1: Search service core with Event / Archive Event

**Files:**
- Create: `MIFPAPP/CORE/mifp_app/services/search.py`
- Test: `TESTS/webapp/test_global_search.py`

**Interfaces:**
- Produces:
  - `normalize_query(raw: object) -> str | None`
  - `fold(value: object) -> str`
  - `sql_fold(column: str) -> str`
  - `SearchTarget` dataclass
  - `SEARCH_TARGETS: tuple[SearchTarget, ...]`
  - `run_search(conn, query, *, scope, limit=DEFAULT_LIMIT, offset=0) -> dict` returning keys `query, results, total, limit, offset, has_more, failed`.
  - Each result: `type, group, id, title, subtitle, excerpt, date, url, score`.

- [ ] **Step 1: Write the failing test file**

Create `TESTS/webapp/test_global_search.py`:

```python
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "MIFPAPP"
    / "CORE"
    / "mifp_app"
    / "db"
    / "schema.sql"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def _seed_events(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO events(id, slug, title, start_date, location, description, "
        "review_status, speakers_json, chairs_json, committee_json) "
        "VALUES (1, 'quantum-optics-2024', 'Quantum Optics 2024', '2024-06-01', "
        "'Rome', 'Advances in photonics', 'published', "
        "'[{\"name\":\"Maria Rossi\"}]', '[{\"name\":\"Giulia Verdi\"}]', "
        "'[{\"name\":\"Anna Neri\"}]')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, location, review_status) "
        "VALUES (2, 'hidden-draft', 'Hidden Draft Event', 'Nowhere', 'draft')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, start_date, location, description, review_status) "
        "VALUES (3, 'old-photonics-1999', 'Old Photonics Meeting', '1999-09-01', "
        "'Catania', 'A historical photonics meeting', 'published')"
    )
    conn.execute(
        "INSERT INTO event_archive_entries(id, event_id, source_schema, public_path, "
        "category, archive_year, acronym, summary, topics_json, people_json) "
        "VALUES (1, 3, 'v3', '/archive/meetings/1999/old-photonics-1999/', "
        "'meetings', 1999, 'OPM', 'Historical photonics summary', "
        "'[\"photonics\"]', '{\"speakers\":[\"Luca Bianchi\"]}')"
    )
    conn.commit()


@pytest.fixture
def app(tmp_path):
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "CONFERENCES_DIR": str(tmp_path / "conferences"),
            "LOG_DIR": str(tmp_path / "logs"),
            "SECRET_KEY": "search-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        LOG_DIR=tmp_path / "logs",
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.manage import init_database

    init_database(Path(app.config["DATABASE_PATH"]))
    yield app


@pytest.fixture
def request_ctx(app):
    with app.test_request_context("/"):
        yield


def _search(conn, query, scope="public", **kwargs):
    from mifp_app.services.search import run_search

    return run_search(conn, query, scope=scope, **kwargs)


def test_normalize_query_trims_and_collapses():
    from mifp_app.services.search import normalize_query

    assert normalize_query("  Quantum   Optics  ") == "Quantum Optics"
    assert normalize_query("a") is None
    assert normalize_query("") is None
    assert normalize_query(None) is None


def test_event_by_title_public(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Quantum Optics")
    titles = [r["title"] for r in page["results"]]
    assert "Quantum Optics 2024" in titles
    assert all(r["type"] == "event" for r in page["results"] if r["title"].startswith("Quantum"))


def test_archive_event_by_title_is_not_duplicated(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Old Photonics")
    matches = [r for r in page["results"] if r["title"] == "Old Photonics Meeting"]
    assert len(matches) == 1
    assert matches[0]["type"] == "archive_event"
    assert "/archive/meetings/1999/old-photonics-1999/" in matches[0]["url"]


def test_search_by_location(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Catania")
    assert [r["title"] for r in page["results"]] == ["Old Photonics Meeting"]


def test_search_by_topic(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "photonics")
    assert "Old Photonics Meeting" in [r["title"] for r in page["results"]]


def test_search_by_speaker(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Maria Rossi")
    assert "Quantum Optics 2024" in [r["title"] for r in page["results"]]


def test_search_by_chair(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Giulia Verdi")
    assert "Quantum Optics 2024" in [r["title"] for r in page["results"]]


def test_search_by_committee(request_ctx):
    conn = _conn()
    _seed_events(conn)
    page = _search(conn, "Anna Neri")
    assert "Quantum Optics 2024" in [r["title"] for r in page["results"]]


def test_case_insensitive(request_ctx):
    conn = _conn()
    _seed_events(conn)
    assert _search(conn, "QUANTUM")["results"]
    assert _search(conn, "quantum")["results"]


def test_accent_insensitive_both_directions(request_ctx):
    conn = _conn()
    conn.execute(
        "INSERT INTO events(id, slug, title, location, review_status) "
        "VALUES (9, 'citta', 'Città della Scienza', 'Perù', 'published')"
    )
    conn.commit()
    assert "Città della Scienza" in [r["title"] for r in _search(conn, "citta")["results"]]
    assert "Città della Scienza" in [r["title"] for r in _search(conn, "città")["results"]]
    assert "Città della Scienza" in [r["title"] for r in _search(conn, "peru")["results"]]


def test_leading_trailing_repeated_whitespace(request_ctx):
    conn = _conn()
    _seed_events(conn)
    assert _search(conn, "   Quantum    Optics   ")["results"]


def test_empty_and_short_query_return_nothing(request_ctx):
    conn = _conn()
    _seed_events(conn)
    for query in ("", " ", "a", None):
        page = _search(conn, query)
        assert page["results"] == []
        assert page["total"] == 0
        assert page["failed"] is False


def test_wildcard_characters_are_literal(request_ctx):
    conn = _conn()
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (20, 'pct', '100% Physics', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (21, 'pctx', '100X Physics', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (22, 'underscore', 'Under_score Meeting', 'published')"
    )
    conn.execute(
        "INSERT INTO events(id, slug, title, review_status) "
        "VALUES (23, 'underspace', 'Under score Meeting', 'published')"
    )
    conn.commit()
    percent_titles = [r["title"] for r in _search(conn, "100%")["results"]]
    assert "100% Physics" in percent_titles
    assert "100X Physics" not in percent_titles
    underscore_titles = [r["title"] for r in _search(conn, "Under_score")["results"]]
    assert "Under_score Meeting" in underscore_titles
    assert "Under score Meeting" not in underscore_titles


def test_public_visibility_excludes_drafts(request_ctx):
    conn = _conn()
    _seed_events(conn)
    assert "Hidden Draft Event" not in [r["title"] for r in _search(conn, "Hidden Draft")["results"]]
    assert "Hidden Draft Event" in [
        r["title"] for r in _search(conn, "Hidden Draft", scope="dashboard")["results"]
    ]


def test_result_limit_and_has_more(request_ctx):
    conn = _conn()
    for i in range(5):
        conn.execute(
            "INSERT INTO events(id, slug, title, review_status) VALUES (?, ?, ?, 'published')",
            (100 + i, f"series-{i}", f"Series Meeting {i}"),
        )
    conn.commit()
    page = _search(conn, "Series Meeting", limit=2)
    assert len(page["results"]) == 2
    assert page["total"] == 5
    assert page["has_more"] is True
    second = _search(conn, "Series Meeting", limit=2, offset=2)
    assert len(second["results"]) == 2
    assert {r["id"] for r in page["results"]}.isdisjoint({r["id"] for r in second["results"]})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mifp_app.services.search'`.

- [ ] **Step 3: Implement the search service core**

Create `MIFPAPP/CORE/mifp_app/services/search.py`:

```python
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
_CANDIDATE_LIMIT = 200

_ACCENT_MAP = {
    "à": "a", "á": "a", "â": "a", "ã": "a", "ä": "a", "å": "a",
    "À": "A", "Á": "A", "Â": "A", "Ã": "A", "Ä": "A", "Å": "A",
    "è": "e", "é": "e", "ê": "e", "ë": "e",
    "È": "E", "É": "E", "Ê": "E", "Ë": "E",
    "ì": "i", "í": "i", "î": "i", "ï": "i",
    "Ì": "I", "Í": "I", "Î": "I", "Ï": "I",
    "ò": "o", "ó": "o", "ô": "o", "õ": "o", "ö": "o",
    "Ò": "O", "Ó": "O", "Ô": "O", "Õ": "O", "Ö": "O",
    "ù": "u", "ú": "u", "û": "u", "ü": "u",
    "Ù": "U", "Ú": "U", "Û": "U", "Ü": "U",
    "ç": "c", "Ç": "C", "ñ": "n", "Ñ": "N", "ý": "y", "Ý": "Y",
}
_FOLD_CACHE: dict[str, str] = {}
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


def sql_fold(column: str) -> str:
    expr = _FOLD_CACHE.get(column)
    if expr is None:
        expr = f"lower({column})"
        for src, dst in _ACCENT_MAP.items():
            expr = f"replace({expr},'{src}','{dst}')"
        _FOLD_CACHE[column] = expr
    return expr


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


def _like_term(folded: str) -> str:
    escaped = folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
    return url_for("public.event_detail", slug=slug)


def _event_dashboard_url(row: dict[str, Any]) -> str:
    title = row.get("_title") or ""
    if row.get("_archive_category") and row.get("_archive_year"):
        return url_for("dashboard.archive_page", q=title)
    return url_for("dashboard.events", q=title)


def _event_result_type(row: dict[str, Any]) -> str:
    if row.get("_archive_category") and row.get("_archive_year"):
        return "archive_event"
    return "event"


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


def _excerpt(data: dict[str, Any], target: SearchTarget, folded_query: str) -> str:
    for index in range(len(target.text_columns)):
        text = _plain(data.get(f"_c{index}"))
        if text and folded_query in fold(text):
            return _snippet(text, folded_query)
    return ""


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
) -> dict[str, Any] | None:
    try:
        url = url_builder(data)
    except Exception:
        _LOGGER.warning("search destination failed type=%s id=%s", target.type, data.get("_id"))
        return None
    title = _plain(data.get("_title"))
    result_type = target.result_type(data) if target.result_type else target.type
    return {
        "type": result_type,
        "group": TYPE_LABELS.get(result_type, result_type.replace("_", " ").title()),
        "id": int(data["_id"]),
        "title": title or f"Record {data['_id']}",
        "subtitle": _plain(data.get("_subtitle")),
        "excerpt": _excerpt(data, target, folded_query),
        "date": _plain(data.get("_date")),
        "url": url,
        "score": _score(fold(title), folded_query),
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
    term = _like_term(folded_query)
    results: list[dict[str, Any]] = []
    failed = False
    for target in SEARCH_TARGETS:
        if scope == "public":
            where = target.public_where
            url_builder = target.public_url
        elif scope == "dashboard":
            where = target.dashboard_where
            url_builder = target.dashboard_url
        else:
            continue
        if not where or url_builder is None:
            continue
        columns = (target.title_expr, *target.text_columns)
        match_sql = " OR ".join(
            f"{sql_fold(column)} LIKE ? ESCAPE '\\'" for column in columns
        )
        select_parts = [
            f"{target.id_expr} AS _id",
            f"{target.title_expr} AS _title",
            f"{target.subtitle_expr} AS _subtitle",
        ]
        if target.date_expr:
            select_parts.append(f"{target.date_expr} AS _date")
        for alias, expr in target.extra_exprs:
            select_parts.append(f"{expr} AS {alias}")
        select_parts.extend(f"{column} AS _c{index}" for index, column in enumerate(target.text_columns))
        sql = (
            f"SELECT {', '.join(select_parts)} FROM {target.from_sql} "
            f"WHERE ({where}) AND ({match_sql}) LIMIT ?"
        )
        params: list[Any] = [term] * len(columns)
        params.append(_CANDIDATE_LIMIT)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            _LOGGER.exception("search target failed type=%s", target.type)
            failed = True
            continue
        for row in rows:
            item = _build_result(dict(row), target, folded_query, url_builder)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: PASS (all Task 1 tests).

- [ ] **Step 5: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/services/search.py TESTS/webapp/test_global_search.py
git commit -m "feat(search): add global search service with event and archive targets"
```

---

### Task 2: Remaining public targets

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/search.py` (add URL builders and targets)
- Test: `TESTS/webapp/test_global_search.py` (extend)

**Interfaces:**
- Consumes: `SearchTarget`, `SEARCH_TARGETS`, `run_search` from Task 1.
- Produces: targets for `news`, `member`, `publication`, `research_area`, `page`, `sponsor`.

- [ ] **Step 1: Write the failing tests**

Append to `TESTS/webapp/test_global_search.py`:

```python
def _seed_content(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO news(id, slug, title, summary, body, review_status) "
        "VALUES (1, 'lab-news', 'New Laboratory Opened', 'A new lab', 'Details about the lab', 'published')"
    )
    conn.execute(
        "INSERT INTO news(id, slug, title, review_status) "
        "VALUES (2, 'draft-news', 'Draft News Item', 'draft')"
    )
    conn.execute(
        "INSERT INTO members(id, slug, display_name, first_name, last_name, affiliation, country, field, bio, review_status, is_active) "
        "VALUES (1, 'giulia-verdi', 'Giulia Verdi', 'Giulia', 'Verdi', 'Perugia University', 'Italy', 'photonics', 'Researcher', 'published', 1)"
    )
    conn.execute(
        "INSERT INTO members(id, slug, display_name, review_status, is_active) "
        "VALUES (2, 'draft-member', 'Draft Member', 'draft', 1)"
    )
    conn.execute(
        "INSERT INTO members(id, slug, display_name, review_status, is_active) "
        "VALUES (3, 'inactive-member', 'Inactive Member', 'published', 0)"
    )
    conn.execute(
        "INSERT INTO publications(id, slug, title, year, authors, journal, abstract, review_status) "
        "VALUES (1, 'metamaterials-review', 'Metamaterials Review', 2023, 'Rossi, Bianchi', 'Optics Today', 'A review of metamaterials', 'published')"
    )
    conn.execute(
        "INSERT INTO research_areas(id, slug, title, summary, description, review_status) "
        "VALUES (1, 'nanophotonics', 'Nanophotonics', 'Light at the nanoscale', 'Nanophotonics research', 'published')"
    )
    conn.execute(
        "INSERT INTO pages(id, slug, title, type, summary, body, review_status) "
        "VALUES (1, 'about', 'About MIFP', 'about', 'Institute profile', 'Body of the about page', 'published')"
    )
    conn.execute(
        "INSERT INTO sponsors(id, slug, name, description, is_active) "
        "VALUES (1, 'acme', 'Acme Optics', 'Sponsor of photonics', 1)"
    )
    conn.execute(
        "INSERT INTO sponsors(id, slug, name, is_active) VALUES (2, 'inactive-sponsor', 'Dormant Sponsor', 0)"
    )
    conn.commit()


def test_news_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "New Laboratory")
    assert page["results"][0]["type"] == "news"
    assert page["results"][0]["url"] == "/news/lab-news"


def test_member_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Giulia Verdi")
    assert page["results"][0]["type"] == "member"
    assert page["results"][0]["url"] == "/members?q=Giulia+Verdi"


def test_publication_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Metamaterials Review")
    assert page["results"][0]["type"] == "publication"
    assert page["results"][0]["url"].startswith("/publications")


def test_research_area_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Nanophotonics")
    assert page["results"][0]["type"] == "research_area"


def test_page_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "About MIFP")
    assert page["results"][0]["type"] == "page"
    assert page["results"][0]["url"] == "/about"


def test_sponsor_search(request_ctx):
    conn = _conn()
    _seed_content(conn)
    page = _search(conn, "Acme Optics")
    assert page["results"][0]["type"] == "sponsor"
    assert page["results"][0]["url"] == "/sponsors/acme"


def test_public_visibility_for_content(request_ctx):
    conn = _conn()
    _seed_content(conn)
    titles = [r["title"] for r in _search(conn, "Draft")["results"]]
    assert "Draft News Item" not in titles
    assert "Draft Member" not in titles
    assert "Dormant Sponsor" not in [r["title"] for r in _search(conn, "Dormant")["results"]]


def test_dashboard_visibility_includes_drafts(request_ctx):
    conn = _conn()
    _seed_content(conn)
    titles = [r["title"] for r in _search(conn, "Draft", scope="dashboard")["results"]]
    assert "Draft News Item" in titles
    assert "Draft Member" in titles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: FAIL (news/member/... targets return no results).

- [ ] **Step 3: Add URL builders and target entries**

In `MIFPAPP/CORE/mifp_app/services/search.py`, add these builders after `_event_result_type`:

```python
def _news_public_url(row: dict[str, Any]) -> str:
    return url_for("public.news_detail", slug=row.get("_slug"))


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
    return url_for("public.sponsor_detail", slug=row.get("_slug"))


def _sponsor_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.content", section="sponsors", q=row.get("_title") or "")
```

Then add these entries inside `SEARCH_TARGETS` after the event target:

```python
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
        dashboard_text_columns=("m.bio",),
        extra_exprs=(("_slug", "m.slug"),),
        public_where="COALESCE(m.review_status,'draft')='published' AND m.is_active=1",
        dashboard_where="1=1",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: PASS (Task 1 and Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/services/search.py TESTS/webapp/test_global_search.py
git commit -m "feat(search): add news, member, publication, research, page and sponsor targets"
```

---

### Task 3: Dashboard-only targets (assets and conference records)

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/search.py`
- Test: `TESTS/webapp/test_global_search.py`

**Interfaces:**
- Consumes: Task 1/2 service.
- Produces: targets `asset`, `conference_site`, `conference_person` available only in `scope="dashboard"`.

- [ ] **Step 1: Write the failing tests**

Append to `TESTS/webapp/test_global_search.py`:

```python
def _seed_dashboard(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO assets(id, filename, original_filename, path, alt_text, caption, source_url, kind) "
        "VALUES (1, 'conference-hall.jpg', 'hall.jpg', 'image/conference-hall.jpg', "
        "'Conference hall', 'Main hall', 'https://example.org/hall.jpg', 'image')"
    )
    conn.execute(
        "INSERT INTO conference_sites(id, slug, title, acronym, year, city, venue, description) "
        "VALUES (1, 'plmcn-2024', 'PLMCN 2024', 'PLMCN', 2024, 'Rome', 'Aula Magna', 'A conference on light matter')"
    )
    conn.execute(
        "INSERT INTO conference_people(id, conference_id, name, affiliation, role, contribution_title) "
        "VALUES (1, 1, 'Anna Neri', 'Sapienza', 'speaker', 'Terahertz spectroscopy')"
    )
    conn.commit()


def test_assets_only_in_dashboard(request_ctx):
    conn = _conn()
    _seed_dashboard(conn)
    assert _search(conn, "conference-hall")["results"] == []
    page = _search(conn, "conference-hall", scope="dashboard")
    assert page["results"][0]["type"] == "asset"
    assert page["results"][0]["url"].startswith("/dashboard/assets?q=")


def test_conference_site_and_person_only_in_dashboard(request_ctx):
    conn = _conn()
    _seed_dashboard(conn)
    assert _search(conn, "PLMCN")["results"] == []
    site = _search(conn, "PLMCN", scope="dashboard")
    assert site["results"][0]["type"] == "conference_site"
    person = _search(conn, "Anna Neri", scope="dashboard")
    assert person["results"][0]["type"] == "conference_person"


def test_asset_alt_text_and_caption_searchable(request_ctx):
    conn = _conn()
    _seed_dashboard(conn)
    assert _search(conn, "Main hall", scope="dashboard")["results"]
    assert _search(conn, "example.org/hall", scope="dashboard")["results"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: FAIL (dashboard-only targets absent).

- [ ] **Step 3: Add dashboard-only targets**

Add builders after `_sponsor_dashboard_url`:

```python
def _asset_dashboard_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.assets_page", q=row.get("_title") or "")


def _conference_site_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.conference_sites")


def _conference_person_url(row: dict[str, Any]) -> str:
    return url_for("dashboard.conference_sites")
```

Append these entries to `SEARCH_TARGETS`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/services/search.py TESTS/webapp/test_global_search.py
git commit -m "feat(search): add dashboard asset and conference targets"
```

---

### Task 4: Public search route, template, and navbar control

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/public.py`
- Create: `MIFPAPP/CORE/mifp_app/templates/public/search.html`
- Modify: `MIFPAPP/CORE/mifp_app/templates/public/_navbar.html`
- Test: `TESTS/webapp/test_global_search.py`

**Interfaces:**
- Consumes: `run_search`, `normalize_query` from Tasks 1-3.
- Produces: endpoint `public.search` at `GET /search`; template `public/search.html`.

- [ ] **Step 1: Write the failing route tests**

Append to `TESTS/webapp/test_global_search.py`:

```python
@pytest.fixture
def client(app):
    return app.test_client()


def _seed_app_db(app) -> None:
    from mifp_app.db.connection import connect

    conn = connect(app.config["DATABASE_PATH"])
    _seed_events(conn)
    _seed_content(conn)
    conn.commit()
    conn.close()


def test_public_search_route_renders_results(app, client):
    _seed_app_db(app)
    response = client.get("/search?q=Quantum+Optics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Quantum Optics 2024" in body
    assert "Hidden Draft Event" not in body


def test_public_search_route_empty_query_does_not_dump(app, client):
    _seed_app_db(app)
    response = client.get("/search")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Quantum Optics 2024" not in body


def test_public_search_route_has_navbar_control(app, client):
    response = client.get("/search")
    assert response.status_code == 200
    assert 'action="/search"' in response.get_data(as_text=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest TESTS/webapp/test_global_search.py -k "route or navbar" -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add the public route**

In `MIFPAPP/CORE/mifp_app/routes/public.py`, add to the service imports:

```python
from ..services.search import run_search
```

Add the route immediately after the `archive_detail` function (around line 239):

```python
@bp.get("/search")
def search():
    raw_query = request.args.get("q", "")
    page_number = max(1, request.args.get("page", 1, type=int) or 1)
    limit = 20
    try:
        with connect_readonly(current_app.config["DATABASE_PATH"]) as conn:
            result = run_search(
                conn,
                raw_query,
                scope="public",
                limit=limit,
                offset=(page_number - 1) * limit,
            )
    except Exception:
        current_app.logger.exception("public search failed")
        result = {
            "query": "",
            "results": [],
            "total": 0,
            "limit": limit,
            "offset": 0,
            "has_more": False,
            "failed": True,
        }
    return render_template("public/search.html", result=result, page=page_number)
```

- [ ] **Step 4: Create the public search template**

Create `MIFPAPP/CORE/mifp_app/templates/public/search.html`:

```html
{% extends "public/base.html" %}
{% block title %}Search — MIFP{% endblock %}
{% block meta_description %}Search events, archive records, news, members, publications, research areas and pages across the MIFP website.{% endblock %}
{% block extra_css %}<link href="{{ url_for('static', filename='css/archive.css', v=static_version) }}" rel="stylesheet">{% endblock %}

{% block content %}
<section class="page-hero">
  <div class="container-wide">
    <ol class="mifp-breadcrumb"><li><a href="{{ url_for('public.home') }}">Home</a></li><li aria-current="page">Search</li></ol>
    <div class="eyebrow-row"><span class="num serif italic">—</span><span class="eyebrow eyebrow-red">Site search</span></div>
    <h1>Search MIFP</h1>
    <p>Find events, historical archive records, news, members, publications, research areas and pages.</p>
    <div class="accent-bar"></div>
  </div>
</section>

<section class="section-sm archive-index-section">
  <div class="container-wide">
    <form class="archive-filters" method="get" action="{{ url_for('public.search') }}" role="search" aria-label="Search the site">
      <label class="archive-search"><span>Search</span><span class="archive-input-wrap"><i class="bi bi-search" aria-hidden="true"></i><input name="q" value="{{ result.query }}" placeholder="Title, name, topic, location" autofocus autocomplete="off"></span></label>
      <div class="archive-filter-actions"><button class="btn btn-primary" type="submit">Search</button></div>
    </form>

    {% if result.failed and not result.results %}
    <div class="empty-state"><div class="empty-state-icon"><i class="bi bi-exclamation-triangle"></i></div><p>Search is temporarily unavailable.</p><p>Please try again in a moment.</p></div>
    {% elif result.query %}
    <div class="section-head archive-results-head">
      <div class="section-head-left"><div class="eyebrow-row"><span class="num serif italic">—</span><span class="eyebrow eyebrow-blue">Results</span></div><h2>{{ result.total }} match{{ '' if result.total == 1 else 'es' }}</h2></div>
    </div>
    {% if result.results %}
    <div class="archive-event-list">
      {% for item in result.results %}
      <article class="archive-event-row">
        <div class="archive-event-date"><strong>{{ item.group }}</strong><span>{{ item.date }}</span></div>
        <div class="archive-event-copy">
          <div class="archive-event-tags"><span class="tag red">{{ item.group }}</span></div>
          <h3><a href="{{ item.url }}">{{ item.title }}</a></h3>
          {% if item.subtitle %}<p>{{ item.subtitle }}</p>{% endif %}
          {% if item.excerpt %}<p>{{ item.excerpt }}</p>{% endif %}
        </div>
        <a class="archive-event-action" href="{{ item.url }}" aria-label="Open {{ item.title }}"><i class="bi bi-chevron-right"></i></a>
      </article>
      {% endfor %}
    </div>
    {% if result.has_more %}
    <p><a class="btn btn-outline" href="{{ url_for('public.search', q=result.query, page=page + 1) }}">More results</a></p>
    {% endif %}
    {% else %}
    <div class="empty-state"><div class="empty-state-icon"><i class="bi bi-search"></i></div><p>No results for “{{ result.query }}”.</p><p>Try a different title, name, topic or location.</p></div>
    {% endif %}
    {% endif %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 5: Add the navbar control**

In `MIFPAPP/CORE/mifp_app/templates/public/_navbar.html`, inside `.nav-right`, add before the admin link (line ~91):

```html
      <a href="{{ url_for('public.search') }}" class="nav-admin" aria-label="Search" title="Search">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <span class="visually-hidden">Search</span>
      </a>
```

And in the mobile menu, add before the login link (line ~163):

```html
      <a href="{{ url_for('public.search') }}" class="nav-mobile-link">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        Search
      </a>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/public.py MIFPAPP/CORE/mifp_app/templates/public/search.html MIFPAPP/CORE/mifp_app/templates/public/_navbar.html TESTS/webapp/test_global_search.py
git commit -m "feat(search): add public /search route, template and navbar control"
```

---

### Task 5: Migrate the dashboard Find and remove the old helper

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/routes/dashboard_control.py`
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/control/search.html`
- Modify: `MIFPAPP/CORE/mifp_app/services/control_center.py`
- Test: `TESTS/webapp/test_global_search.py`

**Interfaces:**
- Consumes: `run_search` from Tasks 1-3.
- Produces: dashboard route uses the shared service; `control_center.global_search` removed.

- [ ] **Step 1: Write the failing dashboard route test**

Append to `TESTS/webapp/test_global_search.py`:

```python
@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
    return client


def test_dashboard_search_uses_shared_global_service(app, admin_client):
    # "Quantum Optics 2024" matches only through its description ("photonics"),
    # a field the replaced title/slug/filename/source_url helper did not search.
    # ("Old Photonics Meeting" also matches, by title.)
    _seed_app_db(app)
    response = admin_client.get("/dashboard/search?q=photonics")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Quantum Optics 2024" in body
    assert "Old Photonics Meeting" in body


def test_dashboard_search_includes_drafts_and_assets(app, admin_client):
    _seed_app_db(app)
    from mifp_app.db.connection import connect

    conn = connect(app.config["DATABASE_PATH"])
    _seed_dashboard(conn)
    conn.commit()
    conn.close()
    drafts = admin_client.get("/dashboard/search?q=Hidden+Draft")
    assert "Hidden Draft Event" in drafts.get_data(as_text=True)
    assets = admin_client.get("/dashboard/search?q=conference-hall")
    assert "conference-hall.jpg" in assets.get_data(as_text=True)


def test_dashboard_pages_result_links_to_institutional(app, admin_client):
    # Assert the RESULT anchor, not the sidebar link that appears on every
    # dashboard page.
    import re

    _seed_app_db(app)
    response = admin_client.get("/dashboard/search?q=About+MIFP")
    body = response.get_data(as_text=True)
    assert re.search(
        r'href="/dashboard/institutional"[^>]*>.*?About MIFP', body, re.DOTALL
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest TESTS/webapp/test_global_search.py -k dashboard_search -v`
Expected: FAIL (old route does not find drafts/assets, wrong page URL).

- [ ] **Step 3: Rewrite the dashboard route**

In `MIFPAPP/CORE/mifp_app/routes/dashboard_control.py`:

Replace the `global_search` import (line 19) with:

```python
    safe_settings,
    storage_health,
    verify_backup,
)
from ..services.search import run_search
```

(Remove only `global_search,` from the `control_center` import block; keep the rest.)

Replace the `dashboard_search` function (lines 710-726) with:

```python
@bp.get("/search")
@login_required
def dashboard_search():
    raw_query = request.args.get("q", "")
    try:
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            result = run_search(conn, raw_query, scope="dashboard", limit=50)
    except Exception:
        current_app.logger.exception("dashboard search failed")
        result = {
            "query": "",
            "results": [],
            "total": 0,
            "limit": 50,
            "offset": 0,
            "has_more": False,
            "failed": True,
        }
    return render_template(
        "dashboard/control/search.html",
        query=result["query"],
        results=result["results"],
        failed=result["failed"],
    )
```

- [ ] **Step 4: Update the dashboard template**

Replace lines 13-22 of `MIFPAPP/CORE/mifp_app/templates/dashboard/control/search.html` with:

```html
{% if failed and not results %}
<section class="search-results" aria-live="polite">
  <div class="empty is-spacious">Search is temporarily unavailable. Check the server log.</div>
</section>
{% elif query|length >= 2 %}
<section class="search-results" aria-live="polite">
  <div class="section-heading"><div><h2>Results</h2><p>{{ results|length }} matches for “{{ query }}”</p></div></div>
  {% for group in results|groupby('group') %}
  <section class="search-group"><h3>{{ group.grouper }}</h3>
    {% for item in group.list %}<a href="{{ item.url }}"><span><b>{{ item.title }}</b><small>{{ item.subtitle or item.date or item.type }}</small></span><i class="bi bi-arrow-right"></i></a>{% endfor %}
  </section>
  {% else %}<div class="empty is-spacious">No managed records match this search.</div>{% endfor %}
</section>
{% endif %}
```

- [ ] **Step 5: Remove the replaced helper**

Delete the `global_search` function from `MIFPAPP/CORE/mifp_app/services/control_center.py` (lines 545-587).

- [ ] **Step 6: Verify nothing else references the old helper**

Run: `grep -rn "global_search" MIFPAPP TESTS`
Expected: no matches.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest TESTS/webapp/test_global_search.py -v`
Expected: PASS.

- [ ] **Step 8: Run the dashboard-related suites**

Run: `pytest TESTS/webapp/test_dashboard_actions.py TESTS/webapp/test_dashboard_security.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add MIFPAPP/CORE/mifp_app/routes/dashboard_control.py MIFPAPP/CORE/mifp_app/templates/dashboard/control/search.html MIFPAPP/CORE/mifp_app/services/control_center.py TESTS/webapp/test_global_search.py
git commit -m "feat(search): share global search service with dashboard Find"
```

---

### Task 6: Focused cleanup and full verification

**Files:**
- Modify: only files proven dead or stale by tracing references.
- Test: full suite.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a cleanup report (in the final engineering report, not a file).

- [ ] **Step 1: Audit search-related duplication**

Run each and record the answer:

```bash
grep -rn "LIKE ?" MIFPAPP/CORE/mifp_app/services MIFPAPP/CORE/mifp_app/routes
grep -rn "def .*search" MIFPAPP/CORE/mifp_app
grep -rn "global_search\|search_results\|q=" MIFPAPP/CORE/mifp_app --include=*.py
```

Classify each hit as ACTIVE / DUPLICATE / DEAD. Do not merge
`dashboard_archive._archive_rows` with `public_repository.list_archive_entries`
unless they encode identical policy (they do not: different visibility and
fields). Record retained duplicates with the reason.

- [ ] **Step 2: Remove proven dead code**

For any symbol proven unreachable (no import, no route, no template/JS/script/CI
reference), delete it. Specifically confirm the old `global_search` is gone and
that `dashboard_control.py` has no unused imports.

- [ ] **Step 3: Remove stale comments**

Delete comments in changed files that describe the removed implementation.
Keep comments that explain security assumptions, visibility rules, or the accent
folding rationale.

- [ ] **Step 4: Run the full test suite**

Run: `pytest TESTS -q`
Expected: PASS.

- [ ] **Step 5: Run the repository hygiene check**

Run: `python3 tools/check_repo_hygiene.py`
Expected: PASS (no generated data committed).

- [ ] **Step 6: Report**

Record in the final engineering report:
- dead files/functions removed;
- duplicates consolidated or retained and why;
- unused dependencies removed (expected: none);
- obsolete configuration removed (expected: none);
- legacy code retained and why;
- TODO/FIXME resolution (expected: none introduced);
- net architectural simplification.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(search): remove replaced search helper and stale comments"
```

---

## Self-Review

**Spec coverage:**
- One search pipeline → Task 1 (`run_search`).
- Public + dashboard → Tasks 4 and 5.
- Global scope (events, archive, news, members, publications, research, pages, sponsors, assets, conferences) → Tasks 1-3.
- Accent/case/whitespace normalization → Task 1 (`fold`, `sql_fold`, `normalize_query`) with tests.
- SQL safety → parameterized queries + `ESCAPE`, constant identifiers.
- Ranking and dedup → Task 1 (`_score`, `_dedupe`).
- Bounds/pagination → Task 1 (`limit`/`offset`/`has_more`) and Task 4 (More results).
- Visibility boundaries → Task 2/3 `public_where` vs `dashboard_where`, tests.
- Failure handling → Tasks 4/5 route `try/except` + `failed` flag.
- Import/update/delete correctness → direct table reads; Task 6 verification.
- No FTS/schema change/external service → Global Constraints.
- Tests → Task 1-5 test file.
- Focused cleanup → Task 6.

**Placeholder scan:** no TBD/TODO; every code step contains complete code.

**Type consistency:** `run_search(conn, query, *, scope, limit, offset)` and result
keys (`type, group, id, title, subtitle, excerpt, date, url, score`) are used
identically in service, routes, templates, and tests. `SearchTarget` fields are
consistent across all target entries.

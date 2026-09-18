# Global Find / Search — Design

Date: 2026-09-18
Status: Approved (design)
Scope: Rebuild Find/Search as a real global search for both the public site
and the authenticated dashboard, followed by a focused, evidence-based cleanup.

> **Post-implementation amendments (final review):**
> - `members.bio` is **dashboard-only**. The member target uses
>   `dashboard_text_columns=("m.bio",)`; public scope does not match or return it
>   (§5.3 wins over the §5.1 wording).
> - `SearchTarget` carries `dashboard_text_columns` for scope-specific fields.
> - A URL-builder exception now sets `failed=True` instead of silently dropping
>   the row; empty/NULL slugs fall back to the relevant listing page.
> - `total` is computed exactly per target via `COUNT(*)`; candidates are fetched
>   with a deterministic `ORDER BY id ASC LIMIT _CANDIDATE_LIMIT (1000)`.

## 1. Problem statement

The current Find/Search is unreliable and not global.

### 1.1 Existing implementations found

- Dashboard Find route: `dashboard_control.dashboard_search` (`/control/search`).
- Dashboard search helper: `control_center.global_search` (`services/control_center.py:545`).
- Dashboard Find template: `templates/dashboard/control/search.html`.
- Public scoped filters (not a global Find):
  - `public.archive` (`/archive/?q=`) → `public_repository.list_archive_entries`.
  - `public.news` (`/news?q=`) → `public_repository.list_news_page`.
  - `public.members` (`/members?q=`) → `public_repository.list_members_page`.
  - `public.publications` client-side filter (`static/js` + `data-search` attribute).
- Dashboard scoped filters: `dashboard_content.content` / `dashboard.events` via
  `dashboard_repository.list_records*`, and `dashboard_archive._archive_rows`.
- Asset picker autocomplete: `dashboard_assets.asset_search_json` (`/assets/search.json`).
- Log search (unrelated domain): `dashboard_repository.search_logs*`.

There is no public global search route and no search control in the public navbar.

### 1.2 Root causes of the broken/unreliable Find

1. **Not global.** `global_search` searches only `title`/`slug`/`filename`/
   `source_url` on `members`, `news`, `events`, `publications`, `research_areas`,
   `sponsors`, `pages`, `assets`. It ignores descriptions, summaries, bodies,
   locations, dates, speakers, chairs, committee members, topics, and asset
   metadata.
2. **Archive Events excluded.** `event_archive_entries` is never searched, so
   historical events are invisible.
3. **Broken destinations.** `pages` results link to `dashboard.site_texts`
   without a `q` parameter, so the user lands on an unfiltered page. This is the
   most visible "search is broken" symptom.
4. **No failure handling.** A database error propagates as HTTP 500; there is no
   logging-and-safe-render path.
5. **No relevance ordering.** Results are ordered by `id DESC` per table, not by
   match quality.
6. **No tests.** There is no regression coverage for search.

## 2. Goals

- One clear search pipeline used by both public and dashboard Find.
- Genuinely global across the meaningful, existing MIFP content model.
- Public Find returns only publicly visible data.
- Correct internal destinations for every result, including Archive Events.
- Bounded, deduplicated, deterministically ranked results.
- No external search service, no FTS index, no schema change.
- No UI redesign: keep the existing dashboard Find page; add only a minimal
  public search control consistent with the existing navbar.
- A focused, evidence-based cleanup of code touched by this work.

## 3. Non-goals

- No Elasticsearch/Meilisearch/Typesense/RedisSearch.
- No SQLite FTS5 (see §6 rationale).
- No new frontend framework, SPA, search dashboard, sidebar, or modal ecosystem.
- No search of internal/technical tables (`settings`, `source_*`, `quality_*`,
  `import_*`) or private PII (`join_requests`, emails, admin notes).
- No full-repository cleanup sweep in this change.

## 4. Architecture

### 4.1 Search service

New module: `MIFPAPP/CORE/mifp_app/services/search.py`.

Public API:

- `normalize_query(raw: object) -> str | None`
  - Coerce to string, strip, collapse internal whitespace, truncate to 120
    characters.
  - Return `None` when empty or shorter than `MIN_QUERY_LENGTH` (2).
- `search(conn, query, *, scope, limit=20, offset=0) -> dict`
  - `scope` is `"public"` or `"dashboard"`.
  - Returns:
    ```python
    {
        "query": str,          # normalized query ("" when invalid)
        "results": list[dict], # normalized, ranked, deduplicated, sliced
        "total": int,          # total matches before slicing
        "limit": int,
        "offset": int,
        "has_more": bool,
        "failed": bool,        # True only when a target raised unexpectedly
    }
    ```
  - Invalid/empty query returns an empty page without touching the database.

Result item shape:

```python
{
    "type": "event" | "archive_event" | "news" | "member" | "publication"
          | "research_area" | "page" | "sponsor" | "asset"
          | "conference_site" | "conference_person",
    "id": int,
    "title": str,
    "subtitle": str,   # context, e.g. "Event · 2024" or affiliation
    "excerpt": str,    # plain-text snippet around the match (may be "")
    "date": str,       # ISO-ish or "", for display/sort tie-break only
    "url": str,        # correct internal destination
    "score": int,      # lower is better; not exposed as UI text
}
```

`url` is always an internal Flask URL. Destination builders call `url_for`
directly; `search()` is only invoked from within a Flask request context. The
service must not hardcode absolute external URLs.

### 4.2 Target definitions

A module-level constant `SEARCH_TARGETS` describes each searchable entity. Each
target is a controlled server-side definition containing:

- `key` / `type` label,
- `table`,
- `id_column`,
- `title_expr` (SQL expression, controlled),
- `subtitle_expr` (SQL expression, controlled),
- `date_expr` (SQL expression or `None`),
- `text_columns` (list of controlled column names searched with folded LIKE),
- `json_columns` (list of controlled JSON-array columns searched in Python),
- `visibility_public` (SQL fragment) and `visibility_dashboard`,
- `destination` (Python callable building the URL from the selected row).

All table and column identifiers come from this constant, never from user input.
All comparison values are bound parameters.

### 4.3 Data flow

```
HTTP request (public /search, or dashboard /control/search)
  -> route extracts q (+ page/offset)
  -> search.normalize_query(q)
  -> search.search(conn, query, scope=..., limit=..., offset=...)
       -> per target: SQL folded-LIKE prefilter with visibility clause
       -> Python scoring, excerpt extraction, JSON-column matching
       -> normalize to result shape, compute destination URL
       -> deduplicate by (type, id)
       -> deterministic sort
       -> total count + bounded slice
  -> route renders existing/new template
```

## 5. Searchable domains

### 5.1 Public scope (published/active only)

| Type | Source | Searchable fields | Destination |
|---|---|---|---|
| Event / Archive Event | `events` (+ `event_archive_entries`) | title, description, location, date_text, speakers, chairs, committee | `public.event_detail` or `public.archive_detail` |
| Archive metadata | `event_archive_entries` | acronym, summary, topics, people | `public.archive_detail` |
| News | `news` | title, summary, body | `public.news_detail` |
| Member | `members` | display/first/last name, affiliation, country, field (bio is dashboard-only) | `public.members?q=<name>` |
| Publication | `publications` | title, authors, journal, abstract, year | `public.publications#publication-<slug>` |
| Research area | `research_areas` | title, summary, description | `public.research#research-directory` |
| Page | `pages` | title, summary, body | dedicated institutional route |
| Sponsor | `sponsors` | name, description | `public.sponsor_detail` |

Visibility:
- `events`, `news`, `publications`, `research_areas`, `pages`:
  `COALESCE(review_status,'draft')='published'`.
- `members`: published **and** `is_active=1`.
- `sponsors`: `is_active=1`.
- An event with archive metadata is emitted once, as `archive_event`.

### 5.2 Dashboard scope

Same as public but without the published/active restriction, plus:

| Type | Source | Searchable fields | Destination |
|---|---|---|---|
| Asset | `assets` | filename, original_filename, alt_text, caption, source_url | `dashboard.assets_page?q=` |
| Conference site | `conference_sites` | title, acronym, city, venue, description | `dashboard.conference_sites` |
| Conference person | `conference_people` | name, affiliation, contribution_title | `dashboard.conference_sites` |

Dashboard destinations:
- events → `dashboard.events?q=`
- archive → `dashboard.archive_page?q=`
- pages → `dashboard.institutional`
- assets → `dashboard.assets_page?q=`
- others → `dashboard.content?section=...&q=`

### 5.3 Explicitly non-searchable

- `join_requests` (PII), `members.email`, `members.bio` admin-only fields are not
  exposed publicly; public member search never returns email.
- `settings`, `source_*`, `canonical_mappings`, `import_*`, `quality_*`,
  `merge_exclusions`, `resolved_pairs`, `asset_recovery_state`,
  `schema_migrations`, `roles` (technical), `metrics_daily`.
- No authentication, session, secret, path, or audit data.

## 6. Matching strategy

### 6.1 Accent and case handling

SQLite `LIKE` is ASCII-case-insensitive and accent-sensitive. To match Italian
and international names/titles without changing stored data or adding indexes:

- Define a controlled `FOLD(col)` SQL expression once:
  `replace(...(lower(col))...)` over a fixed constant list of accented
  characters (`à á â ä è é ê ë ì í î ï ò ó ô ö ù ú û ü ç ñ`, etc.).
- Fold the query in Python: `NFKD` normalize, strip combining marks, casefold,
  collapse whitespace.
- Compare with `FOLD(col) LIKE ? ESCAPE '\'` using `%folded_term%`.
- Also run the raw query as an additional bound variant to catch edge cases.

This makes matching case-insensitive and accent-insensitive in both directions,
is deterministic, and requires no schema or index maintenance. It does not
modify stored data.

### 6.2 Why not FTS5

The entire database is small (currently ~0.5 MB; largest searchable table is
`assets` at ~1.5k rows). FTS5 would add triggers or rebuild steps that must stay
consistent across inserts, updates, deletes, imports, archive imports, restores,
and migrations. That is disproportionate complexity for this dataset.
Folded `LIKE` over bounded per-target scans is simpler, correct by construction,
and fast enough. Performance is re-evaluated only if real data size demands it.

### 6.3 SQL safety

- All identifiers come from `SEARCH_TARGETS`.
- All values are bound parameters.
- `ESCAPE` is used for `%`/`_` in the term.
- Ordering and limits are server-controlled constants.

## 7. Result ranking and deduplication

Deterministic score (lower is better):

1. `0` — folded title exactly equals the folded query.
2. `1` — title starts with the query.
3. `2` — title contains the query.
4. `3` — match only in another field (subtitle/body/people/topic/etc.).

Tie-break: `date` descending where present, then `id` descending. No probabilistic
or heavy ranking.

Deduplication key is `(type, id)`. Because each target issues one row per record
with OR-joined fields, a record that matches multiple fields still appears once.
An archived event is represented only by the `archive_event` target.

Historical events are never filtered out for being old; age affects only
tie-breaks.

## 8. Bounds and pagination

- `MIN_QUERY_LENGTH = 2`; empty/short query returns no results (never dumps the
  database).
- Default `limit = 20`, `MAX_LIMIT = 50`.
- Public route uses `?page=` (20 per page) and renders a compact "More results"
  link when `has_more`.
- Dashboard Find keeps its existing single-page grouped display but is bounded to
  a server-side cap.
- No infinite scroll.

## 9. Visibility and security

- Public scope filters on `review_status='published'` and active flags as above.
- Public excerpts are plain text: HTML tags stripped, whitespace collapsed,
  truncated; no raw body HTML, no paths, no internal IDs beyond what the public
  URL needs.
- Dashboard scope may include drafts and asset/conference metadata but still
  excludes PII and technical tables.
- Results never include database paths, secrets, tokens, or filesystem data.
- The same core service serves both scopes; only the visibility fragment and the
  destination builders differ.

## 10. Error handling

- Route handlers wrap `search()` in `try/except` for `sqlite3.Error` and generic
  exceptions.
- On failure: log with `current_app.logger.exception(...)` and render a compact
  error note through existing UI conventions.
- `failed=True` distinguishes a real failure from a legitimate zero-result query;
  the UI must not show "0 results" when the query failed.
- Malformed per-record data (e.g. invalid `speakers_json`) is skipped without
  aborting the whole search.

## 11. Routes and UI

### 11.1 Public

- New route `public.search` at `GET /search` with `?q=` and `?page=`.
- New template `templates/public/search.html` extending `public/base.html`,
  reusing existing search/list styles (archive search bar, compact result rows).
- Results are grouped/labelled by type (Event, Archive Event, News, Member,
  Publication, Page, Sponsor).
- One small `bi-search` icon link is added to the existing public navbar
  `.nav-right` before Admin, and to the mobile menu. No other navigation change.

### 11.2 Dashboard

- Keep route `dashboard.dashboard_search` and template
  `templates/dashboard/control/search.html` and the existing "Find" placement.
- The route calls the shared service and produces correct destinations.
- `control_center.global_search` is removed after the route migrates.

## 12. Import/export, updates, deletes

Because search reads the canonical tables directly, there is no index to
reindex. Search is therefore automatically correct after:

- record create/update/delete,
- database import, archive import, ZIP import,
- restore, and
- migration.

No manual reindex command exists or is required.

## 13. Tests

New `TESTS/webapp/test_global_search.py`, using the in-memory schema pattern
already used in `TESTS/webapp/test_public_content_sanitization.py` for service
tests, and `create_app` for route tests.

Coverage:

- search Event by title;
- search Archive Event by title (and that it is not duplicated as a plain Event);
- search by location;
- search by topic;
- search by speaker;
- search by chair / committee;
- search News;
- search Members;
- search Publications, Research areas, Pages, Sponsors;
- dashboard-only: assets and conference records;
- case-insensitive behavior;
- Unicode / accented names in both directions;
- leading/trailing/repeated whitespace;
- no duplicate logical results;
- public visibility boundaries (draft/archived members, inactive sponsors,
  unpublished events/news/pages, email never exposed);
- empty query and one-character query;
- malformed input (non-string, very long, `%`/`_` wildcard characters);
- result limit and pagination / `has_more`;
- correct result URLs;
- search after insert, update, and delete;
- search immediately after an import fixture.

## 14. Focused cleanup

Performed only after Find tests pass. Evidence-based, minimal, behavior-preserving.

- Remove the replaced `global_search` from `services/control_center.py` and its
  now-unused import in `dashboard_control.py`.
- Remove only code/imports/locals proven unreachable by tracing imports, route
  registration, templates, JS, scripts, CI, and tests.
- Update stale comments/docstrings adjacent to changed code.
- Do not consolidate `dashboard_archive._archive_rows` with
  `public_repository.list_archive_entries` unless they are proven to encode the
  same policy (currently they do not: different visibility and fields).
- Report ambiguous candidates instead of deleting them.

Cleanup report must list: dead files/functions removed, duplicates consolidated,
unused dependencies removed, obsolete configuration removed, legacy code retained
and why, obsolete workflows/scripts removed, TODO/FIXME resolution, and the net
architectural simplification. Cosmetic changes are not reported as improvements.

## 15. Acceptance criteria

- Public Find and dashboard Find both return global, correct, deduplicated
  results across current and historical Events and the other listed domains.
- Public Find never exposes non-public or private data.
- Every result URL resolves to a valid current internal page.
- Search is correct immediately after imports/updates/deletes/restores with no
  reindex step.
- Empty/invalid queries are cheap and safe.
- Search failures are logged and surfaced, never silently shown as zero results.
- No external search infrastructure, no schema change, no UI redesign.
- The full existing test suite still passes; new search tests pass.
- The cleanup removes only proven-dead code and reduces duplication without
  altering behavior.

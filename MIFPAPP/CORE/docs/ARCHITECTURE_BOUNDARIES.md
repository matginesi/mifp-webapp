# MIFP data boundaries

## How canonical Events come into existence

Events are **ingested, then reviewed — never hand-authored in the dashboard**:

```text
external / scraper / archive authoring source
        ↓  canonical JSONL v2, historical archive package, importers
canonical Event ingestion
        ↓
MIFP dashboard review / edit / manage   (/dashboard/events — update-only)
        ↓
public Events catalogue
```

`POST /dashboard/events` is an **update-only** endpoint: it requires an existing
event id and never inserts a row. The dashboard events screen is the review and
management surface (inline edit, archive metadata, asset links, delete). Adding a
"new event" form there would duplicate the ingestion contract and re-introduce
hand-authored records that the pipelines cannot reconcile, so it is deliberately
absent. The same rule applies to the generic content workspace, which redirects
`/dashboard/content/events` to `/dashboard/events`.

## MIFP Archive Core

Long-lived editorial truth: canonical content, assets, relations, provenance and
persistent data-quality decisions.

## MIFP Webapp Runtime

Replaceable application state: settings, privacy-safe metrics, page views, join
requests, job state and operational exports.

## Conference Builder

Independent conference workspaces and generated sites. A conference may be linked
to a canonical MIFP event, but its build state is not part of the editorial archive.
Conference sites are authored in the external Conference Editor; the MIFP webapp
is the operational/catalogue side and does not become a conference authoring tool.

## Recovery formats

| Format | Purpose | Portable to another system |
|---|---|---:|
| Canonical JSONL v2 / ZIP | Scraper output, database import and dashboard portability | Yes |
| MIFP Content Archive CLI | Offline migration and long-term preservation | Separate tool |
| SQLite backup | Exact disaster recovery | No, application-specific |
| Conference package | Deploy or move one conference site | Conference-only |
| PDF/XLSX/DOCX/CSV | Human review and reporting | No import contract |

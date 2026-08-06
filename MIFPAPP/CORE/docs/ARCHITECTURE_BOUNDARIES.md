# MIFP data boundaries

## MIFP Archive Core

Long-lived editorial truth: canonical content, assets, relations, provenance and
persistent data-quality decisions.

## MIFP Webapp Runtime

Replaceable application state: settings, privacy-safe metrics, page views, join
requests, job state and operational exports.

## Conference Builder

Independent conference workspaces and generated sites. A conference may be linked
to a canonical MIFP event, but its build state is not part of the editorial archive.

## Recovery formats

| Format | Purpose | Portable to another system |
|---|---|---:|
| Canonical JSONL v2 / ZIP | Scraper output, database import and dashboard portability | Yes |
| MIFP Content Archive CLI | Offline migration and long-term preservation | Separate tool |
| SQLite backup | Exact disaster recovery | No, application-specific |
| Conference package | Deploy or move one conference site | Conference-only |
| PDF/XLSX/DOCX/CSV | Human review and reporting | No import contract |

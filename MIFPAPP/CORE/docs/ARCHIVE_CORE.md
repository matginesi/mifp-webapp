# MIFP Archive Core

`mifp_archive` is the framework-independent data layer for the MIFP editorial archive.
It uses only the Python standard library and SQLite; it does not import Flask.

## Boundaries

The Content Archive contains:

- canonical members, events, news, publications, research areas, pages and sponsors;
- stable `uid` values independent from SQLite row IDs and slugs;
- assets and asset links;
- entity links and cross-entity relations;
- source systems, acquisition runs, raw source records and canonical mappings;
- durable merge exclusions, resolved pairs and content aliases.

It intentionally excludes:

- webapp settings;
- metrics and page views;
- join requests and other personal operational data;
- background-job and asset-recovery state;
- conference-builder workspaces;
- authentication secrets.

Use a verified SQLite backup for disaster recovery of the exact running application.
Use the Content Archive for migration, long-term preservation and rebuilding elsewhere.

## Commands

Run commands from `MIFPAPP/CORE/`:

```bash
python -m mifp_archive migrate \
  --db ./data/mifp.db

python -m mifp_archive health \
  --db ./data/mifp.db \
  --assets ./data/assets

python -m mifp_archive export \
  --db ./data/mifp.db \
  --assets ./data/assets \
  --out ./data/exports/mifp-content-archive.zip

python -m mifp_archive validate \
  ./data/exports/mifp-content-archive.zip

python -m mifp_archive import \
  ./data/exports/mifp-content-archive.zip \
  --db /tmp/mifp-test/mifp.db \
  --assets /tmp/mifp-test/assets \
  --dry-run

python -m mifp_archive import \
  ./data/exports/mifp-content-archive.zip \
  --db /tmp/mifp-test/mifp.db \
  --assets /tmp/mifp-test/assets
```

The archive CLI remains a separate offline migration and preservation tool. The dashboard
uses canonical JSONL v2 and ZIP packages shared with `SCRAPERS/` and `DATABASE/`.

## Package structure

```text
manifest.json
README.txt
checksums.sha256
data/
  entities.jsonl
  assets.jsonl
  asset-links.jsonl
  entity-links.jsonl
  relations.jsonl
provenance/
  source-systems.jsonl
  source-runs.jsonl
  source-records.jsonl
  canonical-mappings.jsonl
quality/
  aliases.jsonl
  merge-exclusions.jsonl
  resolved-pairs.jsonl
assets/
  ... managed files ...
```

All references across records use stable UIDs. SQLite numeric IDs are never part of
the portable contract.

## Schema version 8

Migration 8 adds:

- `uid` to canonical content tables and assets;
- automatic UID assignment triggers;
- `schema_migrations`;
- `source_systems`, `source_runs`, `source_records`, `canonical_mappings`;
- `content_sha256` and `source_url_sha256` for assets;
- automatic reconstruction of provenance from legacy `import_runs` and
  `import_records`.

The legacy `assets.checksum` column remains for compatibility. New code writes the
more explicit digest columns as well.

## Recommended migration test

1. Export the Content Archive from the current database.
2. Create an empty directory.
3. Import the archive into an empty SQLite database and empty assets directory.
4. Run `health` on the imported copy.
5. Start the webapp against the imported database.
6. Compare content counts, UIDs, links and visible assets.

The import command is idempotent by UID and supports `--dry-run`.

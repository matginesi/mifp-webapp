# MIFP repository boundaries

The repository is intentionally split into three independent areas.

## 1. `SCRAPERS/`

Contains the working local and remote scrapers and the artifact assembler.
Scraper internals must remain independent from Flask and SQLite.

Rules:
- keep final artifacts in `SCRAPERS/OUTPUTS/`;
- final artifacts are canonical `*.jsonl` files plus exactly one import ZIP;
- never open, create, migrate, update, or delete `mifp.db`;
- do not import modules from `MIFPAPP/CORE/` or `MIFPAPP/DATABASE/`;
- preserve the extraction logic in `scrape_local.py`, `scrape_remote.py`,
  `_remote_aruba.py`, and `_remote_events.py` unless a scraper-specific fix is
  explicitly requested.

Run with:

```bash
bash SCRAPERS/run_all.sh --scrapers all --fresh
```

## 2. `MIFPAPP/CORE/`

Contains the Flask web application, public site, dashboard, templates, static
files, configuration schema, and application code. CORE reads persistent paths
from configuration; it does not own generated data.

Development entry point:

```bash
./mifp local
```

Production entry point:

```bash
./mifp local
./mifp production
```

## 3. `MIFPAPP/DATABASE/`

Owns persistent application state:
- `mifp.db`;
- downloaded/imported assets;
- conference assets;
- exports, backups, and logs;
- explicit JSONL-to-database import tools.

Build or refresh the database only with:

```bash
bash MIFPAPP/DATABASE/build.sh --fresh
```

## Required flow

```text
SCRAPERS source sites
        ↓
SCRAPERS/OUTPUTS/*.jsonl + MIFP_IMPORT.zip
        ↓ explicit command only
MIFPAPP/DATABASE/mifp.db + assets
        ↓ configured read/write access
MIFPAPP/CORE dashboard + webapp
```

Do not reintroduce `NEW_SCRAPER/`, `IMPORT_DATA/`, `NEW_IMPORT_DATA/`, a data
directory inside CORE, or database writes inside the scraper pipeline.

## Root command contract

- `mifp` is the single public launcher for local, Docker, production, scraper, database and test workflows.
- - - keep the three start modes explicit: `local`, `docker`, and `production`.

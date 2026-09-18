# MIFP repository boundaries

The repository is intentionally split into independent areas. Production runs
only through the CI/CD pipeline; the root `mifp` launcher is local-only.

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
files, configuration schema, application code, and the Docker build context.
CORE reads persistent paths from configuration; it does not own generated data.

Development entry point:

```bash
./mifp local
```

Docker build context stays `MIFPAPP/CORE` (see `Dockerfile`); the compose files
here are local-only (`compose.local.yaml`).

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

## 4. `deploy/` and `.github/workflows/`

- `deploy/` holds the VPS release artifacts: `compose.production.yaml` (no
  `build:`; GHCR image, `127.0.0.1:8000`, host data at `/opt/mifp/data`),
  `Caddyfile`, `.env.production.example`, `configure.py`, `deploy.sh`, and `bootstrap-vps.sh`.
- Historical/public conference sites live on the host under `/opt/mifp/events`;
  Caddy serves them directly at `events.mifp.eu`. PHP-FPM is host-side,
  dedicated and deny-by-default; writable PHP state is `/opt/mifp/events-private`.
- `.github/workflows/ci-cd.yml` runs only for pushes to `main` (or explicit
  manual dispatch): it runs the versioned webapp + scraper + database quick
  suite and builds/pushes the image from `MIFPAPP/CORE`.
- The repository deliberately has no Dependabot configuration and no workflow
  that creates branches, pull requests, issues or commits. Dependency/security
  alerts may remain enabled as GitHub-side read-only signals.
- `.github/workflows/ghcr-cleanup.yml` is manual-only maintenance; it is never
  scheduled. Releasing to the VPS is a manual `deploy/deploy.sh` step, never a
  CI job.

Rules:
- never add a `build:` section back to the production compose;
- never run production containers from the local `mifp` launcher;
- keep `deploy/.env.production.example` versioned despite the `.env.*` ignore rules.

## Required flow

```text
SCRAPERS source sites
        ↓
SCRAPERS/OUTPUTS/*.jsonl + MIFP_IMPORT.zip
        ↓ explicit command only
MIFPAPP/DATABASE/mifp.db + assets
        ↓ configured read/write access (local/dashboard)
MIFPAPP/CORE dashboard + webapp
        ↓ GitHub Actions
GHCR immutable image -> VPS (deploy/) + persistent VPS data -> Caddy -> https
```

Do not reintroduce `NEW_SCRAPER/`, `IMPORT_DATA/`, `NEW_IMPORT_DATA/`, a data
directory inside CORE, or database writes inside the scraper pipeline.

## Root command contract

- `mifp` is the single public launcher for local development and maintenance:
  `setup`, `init`, `local`, `docker-local` (alias `docker`), `scrape`, `database`,
  `refresh`, `test`, `admin`, `hash`, `status`, `logs`, `stop`, `clean`, `zip`,
  `doctor`.
- There is no production start mode in the launcher: production deployment is
  exclusively CI/CD + `deploy/deploy.sh` on the VPS.

## Repository hygiene

- Never commit scraper outputs, SQLite databases, exports, backups, uploads, logs, runtime configuration/state, or generated archives.
- `SCRAPERS/OUTPUTS/` and the runtime subdirectories of `MIFPAPP/DATABASE/` are data, not source.
- Run `python3 tools/check_repo_hygiene.py` before committing repository-structure changes; CI runs the same fail-closed check.
- Do not weaken the hygiene checker to accommodate generated data. Move the data outside Git or clean the Git history instead.

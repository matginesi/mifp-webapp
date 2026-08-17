from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


MAIN_TABLES = ("news", "events", "assets", "members", "sponsors")


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return -1


def _db_asset_path(assets_dir: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw))
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == assets_dir.name:
        return assets_dir.parent / path
    return assets_dir / path


def validate_pipeline(db_path: Path, assets_dir: Path) -> int:
    db_path = db_path.resolve()
    assets_dir = assets_dir.resolve()
    errors: list[str] = []

    log.info("[STEP] final validation")
    log.info(f"[VALIDATE] DB input: {db_path}")
    log.info(f"[VALIDATE] Assets input: {assets_dir}")

    if not db_path.exists():
        log.error(f"[ERROR] Final DB does not exist: {db_path}")
        return 1
    db_size = db_path.stat().st_size
    if db_size <= 0:
        errors.append(f"Final DB is empty: {db_path}")
    if not assets_dir.exists():
        errors.append(f"Assets directory does not exist: {assets_dir}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing_tables = [t for t in ("news", "events", "assets") if t not in tables]
    if missing_tables:
        errors.append(f"Missing required table(s): {', '.join(missing_tables)}")

    counts = {table: _count(conn, table) for table in MAIN_TABLES if table in tables}
    for table in ("news", "events", "members"):
        if table in counts and counts[table] <= 0:
            errors.append(f"Table {table} has no rows")
    if "assets" in counts and counts["assets"] <= 0:
        log.warning("[WARN] Table assets has no rows (asset downloads may have been rate-limited)")


    asset_rows = []
    if "assets" in tables:
        asset_rows = conn.execute("SELECT id, path, filename, is_external, storage_status FROM assets WHERE path IS NOT NULL").fetchall()
    present_asset_files = 0
    missing_asset_files: list[str] = []
    for row in asset_rows:
        if int(row["is_external"] or 0):
            continue
        path = _db_asset_path(assets_dir, row["path"])
        fallback = assets_dir / Path(str(row["path"] or row["filename"] or "")).name
        if (path and path.is_file()) or fallback.is_file():
            present_asset_files += 1
        else:
            missing_asset_files.append(str(row["path"] or row["filename"] or row["id"]))

    disk_files = [p for p in assets_dir.rglob("*") if p.is_file()] if assets_dir.exists() else []
    if asset_rows and present_asset_files <= 0:
        errors.append("Assets table has records, but no referenced files were found on disk")

    log.info("[SUMMARY]")
    log.info(f"DB: {db_path}")
    log.info(f"DB size: {db_size} bytes")
    for table in MAIN_TABLES:
        if table in counts:
            log.info(f"{table.capitalize()}: {counts[table]}")
    log.info(f"Asset files: {len(disk_files)}")
    log.info(f"Referenced asset files present: {present_asset_files}")
    log.info(f"Missing asset files: {len(missing_asset_files)}")
    if missing_asset_files:
        log.info("[SUMMARY] First missing asset files:")
        for raw in missing_asset_files[:30]:
            log.info(f"  - {raw}")
    log.info(f"Exports dir: {assets_dir.parent / 'exports'}")

    if errors:
        log.error("[ERROR] Final validation failed:")
        for error in errors:
            log.error(f"  - {error}")
        return 1
    log.info("Exit: 0")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    parser = argparse.ArgumentParser(description="Validate the final MIFP scraper/build output used by the webapp.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return validate_pipeline(args.db, args.assets_dir)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Main runner for build_database package."""

import argparse
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

from .config import WEBAPP, DATABASE_DIR, DEFAULT_JSONL_DIR, SINGLETON_CANONICAL, COUNTRY_HINTS, NOISE_LINES
from .utils import clean, db_connect, exec_schema
from .assets import download_all_assets, prime_downloaded_asset_cache
from .importers import (
    add_member, add_sponsor, add_event, add_page, add_news,
    add_publication, add_research_area,
)
from .postprocess import populate_asset_links, _set_primary_assets


def cleanup_import_noise(conn):
    """Remove exact scraper artefacts that are known navigation/feed fragments."""
    news_deleted = conn.execute(
        """DELETE FROM news
           WHERE lower(title) LIKE 'http%'
              OR lower(title) LIKE '%balsamic gin%'
              OR lower(title) LIKE '%massimilianos%'
              OR lower(title) LIKE '%max''s brasserie%'"""
    ).rowcount
    publications_deleted = conn.execute(
        """DELETE FROM publications
           WHERE lower(title) IN ('atom','rss')
              OR lower(title) LIKE 'atom%'
              OR lower(title) LIKE 'rss%'
              OR title LIKE 'Folder Path:%'
              OR title LIKE 'File:% Uploaded%'"""
    ).rowcount
    if news_deleted or publications_deleted:
        log.info(f"  Removed import noise: {news_deleted} news, {publications_deleted} publications")
    return news_deleted + publications_deleted


def build_report(conn, db_stats):
    """Print a build report."""
    log.info("=" * 60)
    log.info("BUILD REPORT")
    log.info("=" * 60)
    
    for table in db_stats:
        if table == 'start_time':
            continue
        row = conn.execute(f'SELECT COUNT(*) as count FROM {table}').fetchone()
        count = row[0] if row else 0
        log.info(f"  {table}: {count}")
    
    log.info("=" * 60)


def load_all_jsonl_dirs(conn, jsonl_dirs, section='all', fresh=True):
    """Load all JSONL files from one or more directories."""
    if not isinstance(jsonl_dirs, list):
        jsonl_dirs = [jsonl_dirs]
    
    total_members_imported = 0
    total_members_updated = 0
    total_sponsors_imported = 0
    total_sponsors_updated = 0
    total_events_imported = 0
    total_events_updated = 0
    total_pages_imported = 0
    total_pages_updated = 0
    total_news_imported = 0
    total_news_updated = 0
    total_publications_imported = 0
    total_publications_updated = 0
    total_research_imported = 0
    total_research_updated = 0
    
    sections = [section] if section != 'all' else ['members', 'sponsors', 'events', 'pages', 'news', 'publications', 'research_areas']
    
    # File pattern mapping for flat directory structure
    section_patterns = {
        'members': ['members.jsonl'],
        'sponsors': ['sponsors.jsonl'],
        'events': ['events_summary*.jsonl', 'events.jsonl'],
        'pages': ['pages_all.jsonl', 'pages_old_mifp.jsonl', 'pages.jsonl', 'pages_events_mifp.jsonl'],
        'news': ['news.jsonl', 'news*.jsonl'],
        'publications': ['publications.jsonl'],
        'research_areas': ['research_areas.jsonl'],
    }
    
    for jsonl_dir in jsonl_dirs:
        if not os.path.exists(jsonl_dir):
            log.warning(f"Warning: JSONL directory {jsonl_dir} does not exist")
            continue
        
        # Collect directories to scan (the jsonl_dir itself + immediate subdirectories)
        dirs_to_scan = [Path(jsonl_dir)]
        for sub in os.listdir(jsonl_dir):
            sub_path = Path(jsonl_dir) / sub
            if sub_path.is_dir() and not sub.startswith('.'):
                dirs_to_scan.append(sub_path)
        
        for section_name in sections:
            found_files = []
            seen_paths = set()
            
            # Try each directory
            for scan_dir in dirs_to_scan:
                # First try subdirectory (e.g., members/members.jsonl)
                section_dir = scan_dir / section_name
                files = list(section_dir.glob('*.jsonl')) if section_dir.exists() else []
                
                # Fall back to flat directory matching (e.g., members.jsonl at root)
                if not files:
                    patterns = section_patterns.get(section_name, [f'{section_name}*.jsonl'])
                    for pattern in patterns:
                        files = sorted(scan_dir.glob(pattern))
                        if files:
                            break
                
                for f in files:
                    abs_f = str(f.resolve())
                    if abs_f not in seen_paths:
                        seen_paths.add(abs_f)
                        found_files.append(f)
            
            for filename in found_files:
                with open(filename, 'r', encoding='utf-8') as f:
                    records = [json.loads(line) for line in f if line.strip()]
                
                log.info(f"\nProcessing {section_name} from {filename.name}: {len(records)} records")
                
                for record in records:
                    if section_name == 'members':
                        total_members_imported, total_members_updated = add_member(
                            conn, record, total_members_imported, total_members_updated
                        )
                    elif section_name == 'sponsors':
                        total_sponsors_imported, total_sponsors_updated = add_sponsor(
                            conn, record, total_sponsors_imported, total_sponsors_updated
                        )
                    elif section_name == 'events':
                        total_events_imported, total_events_updated = add_event(
                            conn, record, total_events_imported, total_events_updated
                        )
                    elif section_name == 'pages':
                        total_pages_imported, total_pages_updated = add_page(
                            conn, record, total_pages_imported, total_pages_updated
                        )
                    elif section_name == 'news':
                        total_news_imported, total_news_updated = add_news(
                            conn, record, total_news_imported, total_news_updated
                        )
                    elif section_name == 'publications':
                        total_publications_imported, total_publications_updated = add_publication(
                            conn, record, total_publications_imported, total_publications_updated
                        )
                    elif section_name == 'research_areas':
                        total_research_imported, total_research_updated = add_research_area(
                            conn, record, total_research_imported, total_research_updated
                        )
    
    return (
        total_members_imported, total_members_updated,
        total_sponsors_imported, total_sponsors_updated,
        total_events_imported, total_events_updated,
        total_pages_imported, total_pages_updated,
        total_news_imported, total_news_updated,
        total_publications_imported, total_publications_updated,
        total_research_imported, total_research_updated,
    )


def apply_migrations(conn):
    """Apply database migrations."""
    migrations_dir = WEBAPP / 'mifp_app' / 'db' / 'migrations.py'
    if not migrations_dir.exists():
        log.warning("Warning: migrations.py not found")
        return
    
    with open(migrations_dir, 'r') as f:
        migrations_code = f.read()
    
    # Execute migrations in its own namespace so _schema_path() resolves correctly
    ns = globals().copy()
    ns['__file__'] = str(migrations_dir)
    exec(compile(migrations_code, str(migrations_dir), 'exec'), ns)
    
    # Apply the migrations
    changes = ns['migrate_content_schema'](conn)
    if changes.get('created_tables'):
        log.info(f"Created {len(changes['created_tables'])} tables")
    if changes.get('changed_columns'):
        log.info(f"Updated {len(changes['changed_columns'])} columns")


def main():
    parser = argparse.ArgumentParser(description='Build MIFP database from scraper JSONL files')
    parser.add_argument('--webapp-dir', type=str, default=str(WEBAPP), help='Webapp directory path')
    parser.add_argument('--jsonl-dir', action='append', help='JSONL directory path (can be specified multiple times)')
    parser.add_argument('--db', default=None, help='Database file path (default: MIFPAPP/DATABASE/mifp.db)')
    parser.add_argument('--section', default='all', help='Section to import (members, sponsors, events, pages, research, news, publications, or all)')
    parser.add_argument('--fresh', action='store_true', help='Drop and recreate the database')
    parser.add_argument('--skip-downloads', action='store_true', help='Skip asset downloads')
    parser.add_argument('--assets-dir', type=str, default=None, help='Assets directory path (default: MIFPAPP/DATABASE/assets)')
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    args = parser.parse_args()
    
    # Handle webapp-dir
    webapp_dir = Path(args.webapp_dir)
    
    # Override assets dir if provided
    if args.assets_dir:
        from . import config as bd_config
        from . import assets as bd_assets
        bd_config.ASSETS_DIR = Path(args.assets_dir)
        bd_assets.ASSETS_DIR = Path(args.assets_dir)
    
    # Handle jsonl-dir (append means multiple can be specified)
    if not args.jsonl_dir:
        args.jsonl_dir = [str(DEFAULT_JSONL_DIR)]
    
    # Build db_path from webapp_dir if not specified
    if args.db:
        db_path = Path(args.db)
    else:
        db_path = DATABASE_DIR / 'mifp.db'
    
    # Connect to database
    conn = db_connect(db_path)
    
    if args.fresh:
        log.info("Recreating database from scratch...")
        conn.close()
        db_path.unlink(missing_ok=True)
        conn = db_connect(db_path)
        exec_schema(conn)
        log.info("Database recreated from scratch")
    
    # Apply migrations
    apply_migrations(conn)
    prime_downloaded_asset_cache(args.jsonl_dir)
    os.environ["MIFP_BUILD_DOWNLOAD_ASSETS"] = "0" if args.skip_downloads else "1"
    
    # Import data
    log.info(f"\nImporting from {args.jsonl_dir}...")
    (members_i, members_u,
     sponsors_i, sponsors_u,
      events_i, events_u,
      pages_i, pages_u,
      news_i, news_u,
      pubs_i, pubs_u,
      research_i, research_u) = load_all_jsonl_dirs(conn, args.jsonl_dir, args.section, args.fresh)

    cleanup_import_noise(conn)

    # Download assets (always on, unless --skip-downloads)
    if not args.skip_downloads:
        log.info("\nDownloading assets...")
        n = download_all_assets(conn, args.jsonl_dir)
        log.info(f"  Downloaded {n} new assets")
    
    # Populate asset links
    populate_asset_links(conn)
    
    # Set primary assets
    _set_primary_assets(conn)

    # Build report (after all operations for accurate counts)
    db_stats = {
        'start_time': None,
        'members': conn.execute('SELECT COUNT(*) FROM members').fetchone()[0],
        'sponsors': conn.execute('SELECT COUNT(*) FROM sponsors').fetchone()[0],
        'events': conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],
        'pages': conn.execute('SELECT COUNT(*) FROM pages').fetchone()[0],
        'news': conn.execute('SELECT COUNT(*) FROM news').fetchone()[0],
        'publications': conn.execute('SELECT COUNT(*) FROM publications').fetchone()[0],
        'research_areas': conn.execute('SELECT COUNT(*) FROM research_areas').fetchone()[0],
        'assets': conn.execute('SELECT COUNT(*) FROM assets').fetchone()[0],
    }
    
    build_report(conn, db_stats)
    
    conn.commit()
    log.info("\nBuild complete!")


if __name__ == '__main__':
    main()

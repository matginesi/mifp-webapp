from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 8

def _schema_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Keep migrations self-contained for the scraper's standalone executor."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for row in conn.execute(f"PRAGMA table_info(\"{table}\")"):
        if str(row["name"]) == column:
            return True
    return False


def _execute_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_schema_path().read_text(encoding="utf-8"))


def _seed_default_roles(conn: sqlite3.Connection) -> int:
    inserted = 0
    for name, label in (("staff", "Staff"),):
        cur = conn.execute(
            "INSERT OR IGNORE INTO roles(name, label) VALUES(?, ?)",
            (name, label),
        )
        inserted += max(int(cur.rowcount or 0), 0)
    return inserted


def _extend_data_quality_entity_types(conn: sqlite3.Connection) -> int:
    """Allow every importable entity in Data Quality without losing history.

    Runs inside the transaction opened by ``migrate_content_schema``, which
    disables foreign key enforcement before the transaction starts (see the
    comment there). A mid-rebuild failure therefore rolls the whole step back.
    """
    finding_sql = str(conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='quality_findings'"
    ).fetchone()[0] or "")
    alias_sql = str(conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_aliases'"
    ).fetchone()[0] or "")
    if "research_area" in finding_sql and "research_area" in alias_sql:
        return 0

    conn.execute(
        """
        CREATE TABLE quality_findings_v6 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN (
                'clean_record','enrich_record','merge_records',
                'split_aggregated_record','repair_relations_or_assets'
            )),
            entity_type TEXT NOT NULL CHECK(entity_type IN (
                'member','event','news','publication','research_area','page',
                'sponsor','asset'
            )),
            record_ids_json TEXT NOT NULL,
            classification TEXT NOT NULL CHECK(classification IN (
                'exact_duplicate','strong_candidate','ambiguous',
                'related_not_duplicate','blocked','invalid_record',
                'needs_cleaning','aggregated_record','keep_separate',
                'junk_technical_record','page_fragment_attached'
            )),
            score REAL NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            contradictions_json TEXT NOT NULL DEFAULT '[]',
            plan_json TEXT NOT NULL DEFAULT '{}',
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN (
                'open','bundled','resolved','rejected','deferred'
            )),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES quality_runs(id) ON DELETE CASCADE,
            UNIQUE(run_id, fingerprint)
        );
        """
    )
    conn.execute("INSERT INTO quality_findings_v6 SELECT * FROM quality_findings")
    conn.execute("DROP TABLE quality_findings")
    conn.execute("ALTER TABLE quality_findings_v6 RENAME TO quality_findings")

    conn.execute(
        """
        CREATE TABLE content_aliases_v6 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL CHECK(entity_type IN (
                'member','event','news','publication','research_area','page','sponsor'
            )),
            old_slug TEXT NOT NULL,
            canonical_entity_id INTEGER NOT NULL,
            canonical_slug TEXT NOT NULL,
            bundle_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (bundle_id) REFERENCES quality_bundles(id) ON DELETE SET NULL,
            UNIQUE(entity_type, old_slug)
        );
        """
    )
    conn.execute("INSERT INTO content_aliases_v6 SELECT * FROM content_aliases")
    conn.execute("DROP TABLE content_aliases")
    conn.execute("ALTER TABLE content_aliases_v6 RENAME TO content_aliases")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quality_findings_run "
        "ON quality_findings(run_id, action_type, classification, score)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_aliases_lookup "
        "ON content_aliases(entity_type, old_slug)"
    )
    return 2


def _backfill_legacy_provenance(conn: sqlite3.Connection) -> dict[str, int]:
    """Mirror existing import audit rows into the portable provenance model."""
    report = {"source_runs_backfilled": 0, "source_records_backfilled": 0, "canonical_mappings_backfilled": 0}
    if not table_exists(conn, "import_runs") or not table_exists(conn, "import_records"):
        return report
    if int(conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0] or 0) == 0:
        return report
    conn.execute(
        "INSERT OR IGNORE INTO source_systems(uid,name,kind,description) VALUES(?,?,?,?)",
        (
            "source_system_legacy_imports",
            "MIFP legacy import pipeline",
            "legacy_import",
            "Automatically reconstructed from import_runs/import_records.",
        ),
    )
    system = conn.execute(
        "SELECT id FROM source_systems WHERE uid='source_system_legacy_imports'"
    ).fetchone()
    if not system:
        return report
    system_id = int(system["id"])
    for run in conn.execute("SELECT * FROM import_runs ORDER BY id"):
        uid = f"source_run_import_{int(run['id']):08d}"
        cur = conn.execute(
            "INSERT OR IGNORE INTO source_runs("
            "uid,source_system_id,scraper_version,parser_version,started_at,completed_at,status,stats_json,notes"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                uid,
                system_id,
                "legacy",
                "legacy-importer",
                run["started_at"],
                run["completed_at"],
                run["status"],
                run["stats_json"],
                run["notes"],
            ),
        )
        report["source_runs_backfilled"] += max(int(cur.rowcount or 0), 0)
    run_ids = {
        int(row["id"]): f"source_run_import_{int(row['id']):08d}"
        for row in conn.execute("SELECT id FROM import_runs")
    }
    source_run_ids = {
        str(row["uid"]): int(row["id"])
        for row in conn.execute("SELECT id,uid FROM source_runs")
    }
    entity_tables = {
        "member": "members",
        "event": "events",
        "news": "news",
        "publication": "publications",
        "research_area": "research_areas",
        "page": "pages",
        "sponsor": "sponsors",
    }
    for record in conn.execute("SELECT * FROM import_records ORDER BY id"):
        raw_payload = str(record["raw_json"] or "{}")
        raw_sha256 = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
        uid = f"source_record_import_{int(record['id']):012d}"
        run_uid = run_ids.get(int(record["import_run_id"])) if record["import_run_id"] else None
        source_run_id = source_run_ids.get(run_uid or "")
        entity_type = str(record["entity_type"] or "")
        table = entity_tables.get(entity_type)
        entity = (
            conn.execute(f'SELECT uid FROM "{table}" WHERE id=?', (record["entity_id"],)).fetchone()
            if table and record["entity_id"] is not None
            else None
        )
        mapping_status = "mapped" if entity and entity["uid"] else "unmapped"
        cur = conn.execute(
            "INSERT OR IGNORE INTO source_records("
            "uid,source_run_id,source_system_id,external_id,source_url,source_path,fetched_at,"
            "raw_sha256,raw_payload,record_type,mapping_status"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                uid,
                source_run_id,
                system_id,
                str(record["id"]),
                record["source_url"],
                record["source_path"],
                record["created_at"],
                raw_sha256,
                raw_payload,
                entity_type or None,
                mapping_status,
            ),
        )
        report["source_records_backfilled"] += max(int(cur.rowcount or 0), 0)
        if entity and entity["uid"]:
            source_record = conn.execute(
                "SELECT id FROM source_records WHERE uid=?", (uid,)
            ).fetchone()
            cur = conn.execute(
                "INSERT OR IGNORE INTO canonical_mappings("
                "source_record_id,entity_type,entity_uid,mapping_kind,confidence,decision_note"
                ") VALUES(?,?,?,?,?,?)",
                (
                    int(source_record["id"]),
                    entity_type,
                    str(entity["uid"]),
                    "canonical",
                    1.0,
                    "Reconstructed from legacy import_records.entity_id",
                ),
            )
            report["canonical_mappings_backfilled"] += max(int(cur.rowcount or 0), 0)
    return report

_CANONICAL_UID_TABLES = {
    "assets": "asset",
    "members": "member",
    "events": "event",
    "news": "news",
    "publications": "publication",
    "research_areas": "research_area",
    "pages": "page",
    "sponsors": "sponsor",
}


def _ensure_archive_identity_schema(conn: sqlite3.Connection) -> dict[str, int]:
    """Add portable identities and split asset digests without breaking legacy APIs."""
    added_columns = 0
    backfilled_uids = 0
    for table, prefix in _CANONICAL_UID_TABLES.items():
        if not table_exists(conn, table):
            continue
        if not _column_exists(conn, table, "uid"):
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN uid TEXT')
            added_columns += 1
        cur = conn.execute(
            f'UPDATE "{table}" SET uid=? || lower(hex(randomblob(16))) '
            "WHERE uid IS NULL OR TRIM(uid)=''",
            (prefix + "_",),
        )
        backfilled_uids += max(int(cur.rowcount or 0), 0)
        conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid ON "{table}"(uid)'
        )
        conn.execute(f'DROP TRIGGER IF EXISTS assign_{table}_uid')
        conn.execute(
            f"""
            CREATE TRIGGER assign_{table}_uid
            AFTER INSERT ON "{table}"
            WHEN NEW.uid IS NULL OR TRIM(NEW.uid)=''
            BEGIN
                UPDATE "{table}"
                SET uid='{prefix}_' || lower(hex(randomblob(16)))
                WHERE id=NEW.id;
            END
            """
        )

    if table_exists(conn, "assets"):
        for column in ("content_sha256", "source_url_sha256"):
            if not _column_exists(conn, "assets", column):
                conn.execute(f'ALTER TABLE assets ADD COLUMN {column} TEXT')
                added_columns += 1
        conn.execute(
            "UPDATE assets SET content_sha256=checksum "
            "WHERE content_sha256 IS NULL AND storage_status='local' "
            "AND checksum GLOB '[0-9a-fA-F]*' AND length(checksum)=64"
        )
        rows = conn.execute(
            "SELECT id,source_url FROM assets "
            "WHERE source_url IS NOT NULL AND TRIM(source_url)!='' "
            "AND (source_url_sha256 IS NULL OR TRIM(source_url_sha256)='')"
        ).fetchall()
        for row in rows:
            digest = hashlib.sha256(str(row["source_url"]).encode("utf-8")).hexdigest()
            conn.execute(
                "UPDATE assets SET source_url_sha256=? WHERE id=?",
                (digest, int(row["id"])),
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_content_sha256 ON assets(content_sha256)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assets_source_url_sha256 ON assets(source_url_sha256)"
        )

    provenance_report = _backfill_legacy_provenance(conn)

    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,name,checksum) VALUES(?,?,?)",
        (8, "portable archive identities and provenance", "mifp-schema-v8"),
    )
    return {
        "archive_columns_added": added_columns,
        "archive_uids_backfilled": backfilled_uids,
        **provenance_report,
    }


def migrate_content_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Initialize the canonical v2 schema.

    MIFP content databases are regenerated by the scraper/import pipeline. This
    migration intentionally does not add legacy columns or compatibility tables:
    `schema.sql` is the single runtime contract for webapp, scraper and manual
    imports.

    The legacy column ALTERs and the entity-type table rebuild run inside one
    transaction so a partial failure rolls back to the pre-step schema instead
    of leaving half-applied columns behind. A transaction is required rather
    than a savepoint: under legacy sqlite3 isolation, DDL statements execute in
    autocommit mode unless an explicit transaction is open, so a bare savepoint
    marker cannot hold ``ALTER``/``DROP``/``CREATE`` statements. Foreign key
    enforcement is toggled off *before* the transaction opens because
    ``PRAGMA foreign_keys`` is a silent no-op while a transaction is pending.
    """
    before = _tables(conn)
    _execute_schema(conn)
    roles_seeded = _seed_default_roles(conn) if table_exists(conn, "roles") else 0
    # The seed above is DML, which leaves an implicit transaction open; release
    # it so the foreign_keys toggle below is not silently deferred.
    conn.commit()

    archive_report: dict[str, int] = {
        "archive_columns_added": 0,
        "archive_uids_backfilled": 0,
        "source_runs_backfilled": 0,
        "source_records_backfilled": 0,
        "canonical_mappings_backfilled": 0,
    }
    original_fk = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if original_fk:
        conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            legacy_columns = 0
            if table_exists(conn, "events") and not _column_exists(conn, "events", "remote_url"):
                conn.execute("ALTER TABLE events ADD COLUMN remote_url TEXT")
                legacy_columns += 1
            if table_exists(conn, "conference_sites") and not _column_exists(conn, "conference_sites", "config_json"):
                conn.execute("ALTER TABLE conference_sites ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'")
                legacy_columns += 1
            if not _column_exists(conn, "quality_runs", "progress_pct"):
                conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_pct INTEGER DEFAULT 0")
                legacy_columns += 1
            if not _column_exists(conn, "quality_runs", "progress_message"):
                conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_message TEXT DEFAULT ''")
                legacy_columns += 1
            legacy_columns += _extend_data_quality_entity_types(conn)
            archive_report = _ensure_archive_identity_schema(conn)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        if original_fk:
            conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    after = _tables(conn)
    return {
        "schema_version": SCHEMA_VERSION,
        "created_tables": sorted(after - before),
        "roles_seeded": roles_seeded,
        "legacy_columns_added": legacy_columns,
        **archive_report,
    }

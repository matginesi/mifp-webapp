from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..db.connection import table_exists
from .admin_safety import backup_sqlite_database
from .assets import resolve_db_asset_path
from .importers import TYPE_TO_TABLE


@dataclass(frozen=True)
class MaintenanceFinding:
    code: str
    category: str
    count: int
    record_ids: tuple[int, ...] = ()
    detail: str = ""


@dataclass
class MaintenancePlan:
    created_at: str
    checks: dict[str, Any]
    safe_fixes: list[MaintenanceFinding] = field(default_factory=list)
    review_required: list[MaintenanceFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "checks": self.checks,
            "safe_fixes": [asdict(item) for item in self.safe_fixes],
            "review_required": [asdict(item) for item in self.review_required],
        }


_ENTITY_TABLES = {typ: table for typ, table in TYPE_TO_TABLE.items()}


def _ids(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> tuple[int, ...]:
    return tuple(int(row[0]) for row in conn.execute(sql, params).fetchall())


def _pragma_rows(conn: sqlite3.Connection, pragma: str) -> list[str]:
    return [str(row[0]) for row in conn.execute(f"PRAGMA {pragma}").fetchall()]


def _entity_exists(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> bool:
    table = _ENTITY_TABLES.get(entity_type)
    if not table or not table_exists(conn, table):
        return False
    return conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (entity_id,)).fetchone() is not None


def analyze_database(conn: sqlite3.Connection, assets_dir: Path) -> MaintenancePlan:
    """Build a read-only maintenance plan; no rows or files are changed."""
    checks = {
        "quick_check": _pragma_rows(conn, "quick_check"),
        "integrity_check": _pragma_rows(conn, "integrity_check"),
        "foreign_key_check": [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()],
    }
    safe: list[MaintenanceFinding] = []
    review: list[MaintenanceFinding] = []

    if table_exists(conn, "asset_links"):
        missing_assets = _ids(
            conn,
            "SELECT al.id FROM asset_links al LEFT JOIN assets a ON a.id=al.asset_id WHERE a.id IS NULL",
        )
        if missing_assets:
            safe.append(MaintenanceFinding("orphan_asset_reference", "safe", len(missing_assets), missing_assets))
        orphan_entities = tuple(
            int(row["id"])
            for row in conn.execute("SELECT id,entity_type,entity_id FROM asset_links").fetchall()
            if not _entity_exists(conn, str(row["entity_type"]), int(row["entity_id"]))
        )
        if orphan_entities:
            safe.append(MaintenanceFinding("orphan_asset_link", "safe", len(orphan_entities), orphan_entities))

    if table_exists(conn, "entity_links"):
        orphan_links = tuple(
            int(row["id"])
            for row in conn.execute("SELECT id,entity_type,entity_id FROM entity_links").fetchall()
            if not _entity_exists(conn, str(row["entity_type"]), int(row["entity_id"]))
        )
        if orphan_links:
            safe.append(MaintenanceFinding("orphan_entity_link", "safe", len(orphan_links), orphan_links))

    if table_exists(conn, "entity_relations"):
        orphan_relations: list[int] = []
        self_relations: list[int] = []
        for row in conn.execute("SELECT id,source_type,source_id,target_type,target_id FROM entity_relations").fetchall():
            if not _entity_exists(conn, row["source_type"], int(row["source_id"])) or not _entity_exists(
                conn, row["target_type"], int(row["target_id"])
            ):
                orphan_relations.append(int(row["id"]))
            elif row["source_type"] == row["target_type"] and row["source_id"] == row["target_id"]:
                self_relations.append(int(row["id"]))
        if orphan_relations:
            safe.append(MaintenanceFinding("orphan_entity_relation", "safe", len(orphan_relations), tuple(orphan_relations)))
        if self_relations:
            safe.append(MaintenanceFinding("self_entity_relation", "safe", len(self_relations), tuple(self_relations)))

    if table_exists(conn, "assets"):
        unused = _ids(
            conn,
            "SELECT a.id FROM assets a LEFT JOIN asset_links al ON al.asset_id=a.id "
            "WHERE al.id IS NULL AND COALESCE(a.is_external,0)=0",
        )
        if unused:
            review.append(MaintenanceFinding("unused_asset", "review", len(unused), unused))
        missing_files: list[int] = []
        unsafe_paths: list[int] = []
        for row in conn.execute(
            "SELECT id,path FROM assets WHERE COALESCE(is_external,0)=0 AND storage_status!='external'"
        ).fetchall():
            try:
                path = resolve_db_asset_path(assets_dir, row["path"])
            except (OSError, ValueError):
                unsafe_paths.append(int(row["id"]))
                continue
            if not path.is_file():
                missing_files.append(int(row["id"]))
        if missing_files:
            review.append(MaintenanceFinding("missing_asset_file", "review", len(missing_files), tuple(missing_files)))
        if unsafe_paths:
            review.append(MaintenanceFinding("unsafe_asset_path", "review", len(unsafe_paths), tuple(unsafe_paths)))

    if table_exists(conn, "import_runs"):
        interrupted = _ids(
            conn,
            "SELECT id FROM import_runs WHERE status='running' AND started_at < datetime('now','-1 day')",
        )
        if interrupted:
            safe.append(MaintenanceFinding("interrupted_import_run", "safe", len(interrupted), interrupted))

    return MaintenancePlan(
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        checks=checks,
        safe_fixes=safe,
        review_required=review,
    )


def apply_safe_fixes(
    conn: sqlite3.Connection,
    db_path: Path,
    assets_dir: Path,
    *,
    confirmed: bool,
) -> dict[str, Any]:
    """Apply only unambiguous row repairs after confirmation and verified backup."""
    if not confirmed:
        raise ValueError("Explicit confirmation is required")
    plan = analyze_database(conn, assets_dir)
    backup = backup_sqlite_database(db_path, label="maintenance")
    if backup is None:
        raise RuntimeError("Database backup could not be created")
    applied: dict[str, int] = {}
    conn.execute("SAVEPOINT database_maintenance")
    try:
        for finding in plan.safe_fixes:
            if not finding.record_ids:
                continue
            placeholders = ",".join("?" for _ in finding.record_ids)
            if finding.code in {"orphan_asset_reference", "orphan_asset_link"}:
                table = "asset_links"
            elif finding.code == "orphan_entity_link":
                table = "entity_links"
            elif finding.code in {"orphan_entity_relation", "self_entity_relation"}:
                table = "entity_relations"
            elif finding.code == "interrupted_import_run":
                conn.execute(
                    f"UPDATE import_runs SET status='failed', completed_at=CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) AND status='running'",
                    finding.record_ids,
                )
                applied[finding.code] = conn.execute("SELECT changes()").fetchone()[0]
                continue
            else:
                continue
            conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", finding.record_ids)
            applied[finding.code] = conn.execute("SELECT changes()").fetchone()[0]
        post = analyze_database(conn, assets_dir)
        if post.checks["quick_check"] != ["ok"] or post.checks["foreign_key_check"]:
            raise sqlite3.DatabaseError("post-maintenance database verification failed")
        conn.execute("RELEASE SAVEPOINT database_maintenance")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT database_maintenance")
        conn.execute("RELEASE SAVEPOINT database_maintenance")
        conn.rollback()
        raise
    return {
        "backup": backup.name,
        "applied": applied,
        "review_required": len(post.review_required),
        "checks": post.checks,
    }

from __future__ import annotations

import sqlite3


def delete_entity_references(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> None:
    """Remove polymorphic references to an entity while preserving import audit rows."""
    conn.execute("DELETE FROM entity_links WHERE entity_type=? AND entity_id=?", (entity_type, entity_id))
    conn.execute("DELETE FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, entity_id))
    conn.execute(
        "DELETE FROM entity_relations WHERE (source_type=? AND source_id=?) OR (target_type=? AND target_id=?)",
        (entity_type, entity_id, entity_type, entity_id),
    )
    conn.execute(
        "UPDATE import_records SET entity_id=NULL WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    )


def move_entity_references(
    conn: sqlite3.Connection,
    entity_type: str,
    old_id: int,
    canonical_id: int,
) -> None:
    """Move polymorphic references from a duplicate entity to its canonical entity."""
    if old_id == canonical_id:
        return
    for row in conn.execute(
        "SELECT url,label,role,is_primary,sort_order FROM entity_links WHERE entity_type=? AND entity_id=?",
        (entity_type, old_id),
    ).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO entity_links(entity_type,entity_id,url,label,role,is_primary,sort_order)
               VALUES(?,?,?,?,?,?,?)""",
            (entity_type, canonical_id, row["url"], row["label"], row["role"], row["is_primary"], row["sort_order"]),
        )
    for row in conn.execute(
        "SELECT asset_id,role,is_primary,sort_order FROM asset_links WHERE entity_type=? AND entity_id=?",
        (entity_type, old_id),
    ).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order)
               VALUES(?,?,?,?,?,?)""",
            (row["asset_id"], entity_type, canonical_id, row["role"], row["is_primary"], row["sort_order"]),
        )

    conn.execute(
        "UPDATE import_records SET entity_id=? WHERE entity_type=? AND entity_id=?",
        (canonical_id, entity_type, old_id),
    )
    conn.execute(
        "UPDATE OR IGNORE entity_relations SET source_id=? WHERE source_type=? AND source_id=?",
        (canonical_id, entity_type, old_id),
    )
    conn.execute(
        "UPDATE OR IGNORE entity_relations SET target_id=? WHERE target_type=? AND target_id=?",
        (canonical_id, entity_type, old_id),
    )
    conn.execute("DELETE FROM entity_links WHERE entity_type=? AND entity_id=?", (entity_type, old_id))
    conn.execute("DELETE FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, old_id))
    conn.execute(
        "DELETE FROM entity_relations WHERE (source_type=? AND source_id=?) OR (target_type=? AND target_id=?)",
        (entity_type, old_id, entity_type, old_id),
    )
    conn.execute("DELETE FROM entity_relations WHERE source_type=target_type AND source_id=target_id")

from __future__ import annotations

import sqlite3
from pathlib import Path


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    return conn


def test_sponsor_quality_uses_sponsor_schema_not_review_status() -> None:
    from mifp_app.services.control_center import content_quality_checks

    conn = _conn()
    conn.execute("INSERT INTO sponsors(name, slug, is_active) VALUES('Sponsor', 'sponsor', 1)")

    checks = {item["code"]: item for item in content_quality_checks(conn)}

    assert checks["sponsors_logo"]["count"] == 1
    assert checks["sponsors_link"]["count"] == 1
    assert "sponsors_review" not in checks

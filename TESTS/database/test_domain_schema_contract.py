from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_PATH = ROOT / "MIFPAPP" / "CORE" / "mifp_app" / "domain.py"
SCHEMA_PATH = ROOT / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
SCRAPERS_DIR = ROOT / "SCRAPERS"


def _load_domain():
    spec = importlib.util.spec_from_file_location("mifp_domain_contract", DOMAIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _quoted_values(fragment: str) -> frozenset[str]:
    return frozenset(re.findall(r"'([^']+)'", fragment))


def _check_values(schema: str, column: str, *, table: str | None = None) -> frozenset[str]:
    source = schema
    if table:
        match = re.search(
            rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}\s*\((.*?)\n\);",
            schema,
            flags=re.S,
        )
        assert match, f"table {table!r} not found in schema"
        source = match.group(1)
    match = re.search(
        rf"\b{re.escape(column)}\b[^\n]*CHECK\s*\(\s*{re.escape(column)}\s+IN\s*\(([^)]*)\)\s*\)",
        source,
    )
    assert match, f"CHECK constraint for {column!r} not found"
    return _quoted_values(match.group(1))


def test_domain_enums_match_sqlite_constraints():
    domain = _load_domain()
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    assert domain.REVIEW_STATUSES == _check_values(schema, "review_status", table="members")
    assert domain.DATE_PRECISIONS == _check_values(schema, "date_precision", table="events")
    assert domain.EVENT_TYPES == _check_values(schema, "event_type", table="events")
    assert domain.NEWS_TYPES == _check_values(schema, "news_type", table="news")
    assert domain.PAGE_TYPES == _check_values(schema, "type", table="pages")
    assert domain.ASSET_ROLES == _check_values(schema, "role", table="asset_links")
    assert domain.ASSET_KINDS == _check_values(schema, "kind", table="assets")
    assert domain.ASSET_STORAGE_STATUSES == _check_values(schema, "storage_status", table="assets")


def test_scraper_interchange_vocabulary_is_runtime_compatible():
    domain = _load_domain()
    sys.path.insert(0, str(SCRAPERS_DIR))
    try:
        import import_artifacts  # type: ignore
    finally:
        sys.path.pop(0)

    assert frozenset(import_artifacts.VALID_REVIEW) == domain.REVIEW_STATUSES
    assert frozenset(import_artifacts.LINK_ROLES) == domain.LINK_ROLES
    assert frozenset(import_artifacts.ASSET_ROLES) == domain.ASSET_ROLES
    for entity_type, fields in import_artifacts.ALLOWED_FIELDS.items():
        assert entity_type in domain.ENTITY_DATA_FIELDS
        assert frozenset(fields) <= domain.ENTITY_DATA_FIELDS[entity_type]

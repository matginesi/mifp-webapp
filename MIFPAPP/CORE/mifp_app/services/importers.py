from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..config import Config
from ..db.connection import table_columns
from ..utils.text_utils import normalize_url, slugify
from .assets import (
    download_asset,
    infer_kind_from_url,
    resolve_db_asset_path,
    sha256_file,
    store_asset,
    store_external_asset,
)
from .data_quality.normalizers import clean_boilerplate, person_name

TYPE_TO_TABLE = {
    "event": "events",
    "news": "news",
    "member": "members",
    "publication": "publications",
    "research_area": "research_areas",
    "page": "pages",
    "sponsor": "sponsors",
}
IMPORT_TYPES = {"event", "news", "member", "publication", "research_area", "page", "sponsor"}

TABLE_TO_TYPE = {table: typ for typ, table in TYPE_TO_TABLE.items()}

TOP_KEYS = {"type", "data", "links", "assets", "meta"}
REVIEW_STATUSES = {"draft", "review", "published", "quarantined", "duplicate"}
LINK_ROLES = {"primary", "website", "source", "doi", "publisher", "registration", "program", "document", "social", "other"}
ASSET_ROLES = {"cover", "gallery", "attachment", "logo", "document", "profile", "banner"}
ASSET_KINDS = {"image", "document", "pdf", "video", "other"}
ASSET_STORAGE_STATUSES = {"local", "external", "missing"}
ASSET_DATA_FIELDS = {
    "filename", "original_filename", "path", "mime_type", "size", "kind",
    "alt_text", "caption", "source_url", "storage_status", "is_external",
    "width", "height", "duration_seconds", "checksum",
}
# Identity/metadata carried by dashboard-produced exports so a re-import can
# restore the same asset row (uid/checksum/path) instead of minting a new one.
ASSET_LINK_FIELDS = {
    "path", "url", "role", "kind", "caption", "alt_text", "is_primary", "sort_order",
    "uid", "checksum", "content_sha256", "source_url_sha256", "storage_status",
    "is_external", "filename", "original_filename", "mime_type", "size",
    "width", "height", "duration_seconds",
}

DATA_FIELDS = {
    "event": {
        "slug", "title", "start_date", "end_date", "date_text", "date_precision",
        "location", "description", "event_type", "series_key", "parent_event_id",
        "parent_event_slug", "review_status", "is_featured", "sort_order", "remote_url",
    },
    "news": {
        "slug", "title", "news_type", "card_layout", "date", "date_text",
        "date_precision", "date_is_inferred", "date_inference_rule",
        "original_date_text", "summary", "body", "review_status", "is_featured",
        "source_kind", "source_priority", "source_order", "display_order", "sort_order",
    },
    "member": {
        "slug", "first_name", "last_name", "display_name", "affiliation", "country",
        "email", "role", "role_id", "field", "bio", "review_status", "is_active", "sort_order",
        "normalized_affiliation", "normalized_name",
    },
    "publication": {
        "slug", "title", "year", "authors", "journal", "doi", "abstract",
        "date_text", "date_precision", "review_status", "sort_order",
    },
    "research_area": {"slug", "title", "summary", "description", "review_status", "sort_order"},
    "page": {
        "slug", "title", "type", "summary", "body", "version", "effective_date",
        "nav_group", "menu_order", "review_status", "sort_order",
    },
    "sponsor": {"slug", "name", "description", "sponsor_type", "tier", "is_active", "sort_order"},
}

for _fields in DATA_FIELDS.values():
    _fields.add("uid")
ASSET_DATA_FIELDS.update({"uid", "content_sha256", "source_url_sha256"})

REQUIRED_FIELDS = {
    "event": {"title"},
    "news": {"title"},
    "member": {"display_name"},
    "publication": {"title"},
    "research_area": {"title"},
    "page": {"title"},
    "sponsor": {"name"},
}

COUNTRY_HINTS: dict[str, str] = {
    "uk": "United Kingdom", "united kingdom": "United Kingdom",
    "southampton": "United Kingdom", "england": "United Kingdom",
    "london": "United Kingdom", "cambridge": "United Kingdom",
    "oxford": "United Kingdom", "manchester": "United Kingdom",
    "glasgow": "United Kingdom", "edinburgh": "United Kingdom",
    "usa": "United States", "united states": "United States",
    "u.s.": "United States", "new york": "United States",
    "boston": "United States", "chicago": "United States",
    "texas": "United States", "california": "United States",
    "florida": "United States", "washington": "United States",
    "arizona state": "United States",
    "germany": "Germany", "deutschland": "Germany",
    "berlin": "Germany", "munich": "Germany", "hamburg": "Germany",
    "france": "France", "paris": "France", "lyon": "France",
    "marseille": "France", "montpellier": "France", "grenoble": "France",
    "italy": "Italy", "italia": "Italy",
    "rome": "Italy", "milan": "Italy", "milano": "Italy",
    "pisa": "Italy", "pavia": "Italy", "trento": "Italy",
    "naples": "Italy", "napoli": "Italy", "turino": "Italy",
    "torino": "Italy", "bologna": "Italy",
    "spain": "Spain", "madrid": "Spain", "barcelona": "Spain",
    "donostia": "Spain", "bilbao": "Spain",
    "japan": "Japan", "tokyo": "Japan", "kyoto": "Japan",
    "china": "China", "beijing": "China", "shanghai": "China",
    "hong kong": "China",
    "russia": "Russia", "moscow": "Russia", "st petersburg": "Russia",
    "india": "India", "mumbai": "India", "delhi": "India",
    "switzerland": "Switzerland", "zurich": "Switzerland",
    "geneva": "Switzerland", "lausanne": "Switzerland",
    "netherlands": "Netherlands", "amsterdam": "Netherlands",
    "delft": "Netherlands", "eindhoven": "Netherlands",
    "poland": "Poland", "warsaw": "Poland", "krakow": "Poland",
    "wroclaw": "Poland",
    "sweden": "Sweden", "stockholm": "Sweden",
    "denmark": "Denmark", "copenhagen": "Denmark",
    "finland": "Finland", "helsinki": "Finland",
    "norway": "Norway", "oslo": "Norway",
    "austria": "Austria", "vienna": "Austria",
    "belgium": "Belgium", "brussels": "Belgium",
    "leuven": "Belgium",
    "ireland": "Ireland", "dublin": "Ireland",
    "portugal": "Portugal", "lisbon": "Portugal",
    "brazil": "Brazil", "sao paulo": "Brazil",
    "australia": "Australia",
    "czech": "Czech Republic", "czech republic": "Czech Republic",
    "hungary": "Hungary", "budapest": "Hungary",
    "greece": "Greece", "athens": "Greece",
    "turkey": "Turkey", "istanbul": "Turkey",
    "israel": "Israel", "tel aviv": "Israel",
    "jerusalem": "Israel", "beer sheva": "Israel",
    "ben gurion": "Israel", "weizmann": "Israel",
    "ukraine": "Ukraine", "kyiv": "Ukraine",
    "singapore": "Singapore",
    "south korea": "South Korea", "seoul": "South Korea",
    "taiwan": "Taiwan",
}


def _ascii_lower(text: Any) -> str:
    return unicodedata.normalize("NFKD", str(text or "").lower()).encode("ascii", "ignore").decode()


def infer_country(text: Any) -> str:
    t = _ascii_lower(text)
    if not t:
        return ""
    for key, country in COUNTRY_HINTS.items():
        key_norm = _ascii_lower(key)
        if not key_norm:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(key_norm)}(?![a-z0-9])", t):
            return country
    return ""


from .job_manager import JobCancelled


class ImportValidationError(ValueError):
    pass


def import_jsonl(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    dry_run: bool = False,
    assets_dir: Path | None = None,
    asset_source_dir: Path | None = None,
    import_assets: bool = True,
    progress: Callable[[int, int], None] | None = None,
    asset_detail: Callable[[str], None] | None = None,
    force_import: bool = False,
    source_name: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    records = _read_records(Path(path))
    summary: dict[str, Any] = {
        "read": len(records),
        "inserted": {},
        "updated": {},
        "linked_assets": 0,
        "linked_links": 0,
        "errors": [],
        "asset_errors": [],
        "skipped": 0,
        "rolled_back": 0,
        "dry_run": dry_run,
    }
    run_id = None
    if not dry_run:
        run_id = _start_import_run(conn, Path(path), len(records), source_name=source_name)
    for idx, record in enumerate(records, start=1):
        if cancel_check and cancel_check():
            raise JobCancelled("Import cancelled by administrator")
        savepoint = f"import_record_{idx}"
        before_inserted = dict(summary["inserted"])
        before_updated = dict(summary["updated"])
        before_linked_assets = summary["linked_assets"]
        before_linked_links = summary["linked_links"]
        before_asset_errors = len(summary["asset_errors"])
        if not dry_run:
            conn.execute(f"SAVEPOINT {savepoint}")
        try:
            typ, data, links, assets, meta = _validate_record(record, idx)
            # A portable export captured assets/links exactly as they live in the
            # database (meta.exported_from_id). Re-import must restore them
            # verbatim; the `.pdf`/`.docx` link-to-asset promotion below is only
            # meant for raw scraper JSONL feeds, not for round-tripping.
            portable_restore = bool(meta.get("exported_from_id"))
            if links and not portable_restore:
                promoted: list[dict[str, Any]] = []
                remaining: list[dict[str, Any]] = []
                for li, link in enumerate(links, start=1):
                    url = str(link.get("url") or "")
                    if re.search(r'\.(?:pdf|docx?|xlsx?|pptx?)$', urlparse(url).path.lower()):
                        promoted.append({
                            "url": url,
                            "role": "document",
                            "caption": link.get("label"),
                            "is_primary": 0,
                            "sort_order": li,
                        })
                    else:
                        remaining.append(link)
                if promoted:
                    assets = (assets or []) + promoted
                    links = remaining
            if dry_run:
                summary["inserted"][typ] = summary["inserted"].get(typ, 0) + 1
                continue
            table = TYPE_TO_TABLE[typ]
            entity_id, action = _upsert_entity(
                conn,
                typ,
                data,
                links,
                force_import=force_import,
                portable_restore=portable_restore,
            )
            summary[action][typ] = summary[action].get(typ, 0) + 1
            summary["linked_links"] += _replace_links(conn, typ, entity_id, links)
            if import_assets:
                if asset_detail and assets:
                    asset_detail(f"Processing {len(assets)} asset(s) for record {idx}…")
                asset_errors: list[str] = []
                summary["linked_assets"] += _replace_assets(
                    conn,
                    typ,
                    entity_id,
                    assets,
                    assets_dir=assets_dir or Config.ASSETS_DIR,
                    asset_source_dir=asset_source_dir,
                    errors=asset_errors,
                    on_asset=(lambda a_i, a_t: asset_detail(f"Asset {a_i}/{a_t} for record {idx}")) if asset_detail else None,
                )
                summary["asset_errors"].extend(
                    {"line": idx, "error": message} for message in asset_errors
                )
            if run_id is not None:
                _record_import_row(conn, run_id, idx, typ, table, entity_id, data, links, meta)
            if not dry_run:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except (ImportValidationError, ValueError, TypeError, sqlite3.Error, OSError) as exc:
            summary["inserted"] = before_inserted
            summary["updated"] = before_updated
            summary["linked_assets"] = before_linked_assets
            summary["linked_links"] = before_linked_links
            del summary["asset_errors"][before_asset_errors:]
            if not dry_run:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                summary["rolled_back"] += 1
            summary["skipped"] += 1
            summary["errors"].append({"line": idx, "error": str(exc)})
        if progress and (idx == 1 or idx % 100 == 0 or idx == len(records)):
            progress(idx, len(records))
    if cancel_check and cancel_check():
        raise JobCancelled("Import cancelled by administrator")
    if not dry_run:
        _restore_portable_references(conn, records, summary)
    if run_id is not None:
        status = "completed_with_errors" if summary["errors"] or summary["asset_errors"] else "completed"
        conn.execute(
            "UPDATE import_runs SET completed_at=CURRENT_TIMESTAMP, status=?, stats_json=? WHERE id=?",
            (status, json.dumps(summary, ensure_ascii=False, default=str), run_id),
        )
    if not dry_run and commit:
        conn.commit()
    return summary


def _read_records(path: Path) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ImportValidationError("Import file is not available") from exc
    if size > Config.IMPORT_MAX_JSONL_BYTES:
        raise ImportValidationError(
            f"JSON/JSONL import exceeds maximum size: {Config.IMPORT_MAX_JSONL_BYTES} bytes"
        )
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportValidationError("Import file must use UTF-8 encoding") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        document = None
    if isinstance(document, dict):
        return [document]
    if isinstance(document, list):
        if len(document) > Config.IMPORT_MAX_JSONL_LINES:
            raise ImportValidationError(f"JSON import exceeds maximum record count: {Config.IMPORT_MAX_JSONL_LINES}")
        if not all(isinstance(item, dict) for item in document):
            raise ImportValidationError("JSON array must contain record objects")
        return document

    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if line_no > Config.IMPORT_MAX_JSONL_LINES:
            raise ImportValidationError(f"JSONL import exceeds maximum line count: {Config.IMPORT_MAX_JSONL_LINES}")
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImportValidationError(f"Line {line_no}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise ImportValidationError(f"Line {line_no}: JSONL record must be an object")
        records.append(item)
    return records


def _validate_record(record: dict[str, Any], line_no: int) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    unknown_top = set(record) - TOP_KEYS
    if unknown_top:
        raise ImportValidationError(f"Line {line_no}: unknown top-level keys: {', '.join(sorted(unknown_top))}")
    typ = str(record.get("type") or "").strip()
    if typ not in IMPORT_TYPES:
        raise ImportValidationError(f"Line {line_no}: invalid type: {typ!r}")
    data = record.get("data")
    if not isinstance(data, dict):
        raise ImportValidationError(f"Line {line_no}: data must be an object")
    unknown_data = set(data) - DATA_FIELDS[typ]
    if unknown_data:
        raise ImportValidationError(f"Line {line_no}: unknown {typ} data fields: {', '.join(sorted(unknown_data))}")
    meta = record.get("meta", {})
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ImportValidationError(f"Line {line_no}: meta must be an object")
    if typ == "member" and "exported_from_id" not in meta:
        data = _normalize_member_name(dict(data))
    missing = [f for f in REQUIRED_FIELDS[typ] if not str(data.get(f) or "").strip()]
    if missing:
        raise ImportValidationError(f"Line {line_no}: missing required fields: {', '.join(missing)}")
    portable_restore = "exported_from_id" in meta
    clean = _normalize_data(typ, dict(data), portable_restore=portable_restore)
    # A database can legitimately contain multiple legacy rows with a NULL
    # slug. Do not manufacture colliding identities while restoring an export.
    if "exported_from_id" in meta and not data.get("slug"):
        clean["slug"] = None
    links = _validate_links(record.get("links", []), line_no)
    assets = _validate_assets(record.get("assets", []), line_no)
    return typ, clean, links, assets, meta


def _validate_asset_db_path(path: str, line_no: int) -> None:
    if "\x00" in path or "\\" in path:
        raise ImportValidationError(f"Line {line_no}: unsafe asset path")
    p = Path(path)
    if p.is_absolute() or any(part in {"", ".", ".."} for part in p.parts):
        raise ImportValidationError(f"Line {line_no}: unsafe asset path")


def _normalize_member_name(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("display_name"):
        if not data.get("first_name") or not data.get("last_name"):
            dn = data["display_name"].strip()
            if "," in dn:
                a, b = (p.strip() for p in dn.split(",", 1))
                data.setdefault("last_name", a)
                data.setdefault("first_name", b)
            else:
                idx = dn.rfind(" ")
                if idx != -1:
                    data.setdefault("first_name", dn[:idx])
                    data.setdefault("last_name", dn[idx + 1:])
                else:
                    data.setdefault("first_name", dn)
        return data
    full = " ".join(str(data.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    if full:
        data["display_name"] = full
    return data


def _normalize_data(
    typ: str, data: dict[str, Any], *, portable_restore: bool = False
) -> dict[str, Any]:
    if "uid" in data:
        data["uid"] = str(data.get("uid") or "").strip() or None
    title = data.get("name") if typ == "sponsor" else data.get("display_name") if typ == "member" else data.get("title")
    data["slug"] = slugify(str(data.get("slug") or title))
    if typ != "sponsor":
        status = str(data.get("review_status") or "published").strip().lower()
        if status not in REVIEW_STATUSES:
            raise ImportValidationError(f"Invalid review_status: {status}")
        data["review_status"] = status
    if "is_featured" in data:
        data["is_featured"] = int(bool(data["is_featured"]))
    if "is_active" in data:
        data["is_active"] = int(bool(data["is_active"]))
    if not portable_restore and typ == "member" and not data.get("country") and data.get("affiliation"):
        inferred = infer_country(data["affiliation"])
        if inferred:
            data["country"] = inferred
    for key in ("sort_order", "source_priority", "source_order", "display_order", "menu_order", "parent_event_id", "year"):
        if key in data and data[key] not in (None, ""):
            data[key] = int(data[key])
    if isinstance(data.get("authors"), list):
        data["authors"] = ", ".join(str(x).strip() for x in data["authors"] if str(x).strip())
    for field in () if portable_restore else {
        "event": ("description",),
        "news": ("summary", "body"),
        "publication": ("authors", "abstract"),
        "member": ("bio",),
        "sponsor": ("description",),
    }.get(typ, ()):
        if data.get(field):
            cleaned, removed = clean_boilerplate(data[field])
            # Only deterministic, segment-level removals are applied during
            # import. Large reductions remain visible to Data quality review.
            if removed and len(cleaned) >= len(str(data[field])) * 0.6:
                data[field] = cleaned
    if "date_precision" in data:
        raw = str(data["date_precision"]).strip().lower().replace("-", "_").replace(" ", "_")
        PRECISION_ALIASES = {
            "day": "day", "date": "day", "day_date": "day",
            "month": "month",
            "year": "year",
            "range": "range", "day_range": "range", "date_range": "range",
        }
        data["date_precision"] = PRECISION_ALIASES.get(raw, "unknown")
    if (
        not portable_restore
        and
        typ == "event"
        and data.get("date_precision") == "range"
        and str(data.get("start_date") or "").endswith("-01-01")
        and str(data.get("end_date") or "").endswith("-12-31")
        and str(data.get("start_date"))[:4] == str(data.get("end_date"))[:4]
    ):
        data["end_date"] = None
        data["date_precision"] = "year"
        data["date_text"] = str(data["start_date"])[:4]
    return data


def _validate_links(raw: Any, line_no: int) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ImportValidationError(f"Line {line_no}: links must be a list")
    links: list[dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ImportValidationError(f"Line {line_no}: links[{idx}] must be an object")
        unknown = set(item) - {"url", "role", "label", "is_primary", "sort_order"}
        if unknown:
            raise ImportValidationError(f"Line {line_no}: links[{idx}] unknown keys: {', '.join(sorted(unknown))}")
        url = normalize_url(item.get("url"))
        if not url:
            raise ImportValidationError(f"Line {line_no}: links[{idx}] has invalid url")
        role = str(item.get("role") or "primary").strip().lower()
        if role not in LINK_ROLES:
            raise ImportValidationError(f"Line {line_no}: links[{idx}] invalid role: {role}")
        links.append({
            "url": url,
            "role": role,
            "label": item.get("label"),
            "is_primary": int(bool(item.get("is_primary", idx == 1))),
            "sort_order": int(item.get("sort_order") or idx),
        })
    return links


def _validate_assets(raw: Any, line_no: int) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ImportValidationError(f"Line {line_no}: assets must be a list")
    assets: list[dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ImportValidationError(f"Line {line_no}: assets[{idx}] must be an object")
        unknown = set(item) - ASSET_LINK_FIELDS
        if unknown:
            raise ImportValidationError(f"Line {line_no}: assets[{idx}] unknown keys: {', '.join(sorted(unknown))}")
        if not item.get("path") and not item.get("url"):
            raise ImportValidationError(f"Line {line_no}: assets[{idx}] requires path or url")
        role = str(item.get("role") or "attachment").strip().lower()
        if role not in ASSET_ROLES:
            raise ImportValidationError(f"Line {line_no}: assets[{idx}] invalid role: {role}")
        spec = {
            "path": item.get("path"),
            "url": item.get("url"),
            "role": role,
            "kind": item.get("kind"),
            "caption": item.get("caption"),
            "alt_text": item.get("alt_text"),
            "is_primary": int(bool(item.get("is_primary", idx == 1))),
            "sort_order": int(item.get("sort_order") or idx),
        }
        for key in ASSET_LINK_FIELDS - {"path", "url", "role", "kind", "caption", "alt_text", "is_primary", "sort_order"}:
            if key in item and item[key] not in (None, ""):
                spec[key] = item[key]
        assets.append(spec)
    return assets


def _merge_fields(conn: sqlite3.Connection, table: str, row_id: int, updates: dict[str, Any]) -> None:
    """Enrich an existing row without replacing curated values."""
    non_empty = {k: v for k, v in updates.items() if v not in (None, "")}
    if not non_empty:
        return
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if not row:
        return
    existing = dict(row)
    assignments: list[str] = []
    params: list[Any] = []
    for k, v in non_empty.items():
        if k not in existing:
            continue
        old = existing.get(k)
        replace_identity_title = False
        if k == "title" and old not in (None, ""):
            old_words = set(_identity_text(old).split())
            new_words = set(_identity_text(v).split())
            if not (old_words < new_words and len(str(v)) >= len(str(old)) + 3):
                continue
            replace_identity_title = True
        if k in {"name", "display_name", "slug"} and old not in (None, ""):
            continue
        if k in {"is_featured", "is_active"}:
            new_value = 1 if bool(old) or bool(v) else 0
            if new_value != int(old or 0):
                assignments.append(f"{k}=?")
                params.append(new_value)
            continue
        if replace_identity_title or old in (None, "") or _should_replace_low_info_value(k, old, v):
            assignments.append(f"{k}=?")
            params.append(v)
    if assignments:
        sets = ", ".join(assignments)
        conn.execute(
            f"UPDATE {table} SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*params, row_id],
        )


def _upsert_entity(
    conn: sqlite3.Connection,
    typ: str,
    data: dict[str, Any],
    links: list[dict[str, Any]] | None = None,
    force_import: bool = False,
    portable_restore: bool = False,
) -> tuple[int, str]:
    table = TYPE_TO_TABLE[typ]
    if typ == "member":
        role_name = str(data.pop("role", "") or "").strip()
        # role_id is local to a database; resolve a portable role name instead.
        data.pop("role_id", None)
        if role_name:
            conn.execute(
                "INSERT OR IGNORE INTO roles(name, label) VALUES(?, ?)",
                (role_name, role_name.replace("_", " ").title()),
            )
            role_row = conn.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
            if role_row:
                data["role_id"] = int(role_row["id"])
    elif typ == "event":
        # Event IDs are also database-local. The slug reference is restored
        # after all records have been imported.
        data.pop("parent_event_slug", None)
        data.pop("parent_event_id", None)
    cols = table_columns(conn, table)
    payload = {k: v for k, v in data.items() if k in cols and k != "id"}
    if not force_import:
        if portable_restore:
            row = (
                conn.execute(f"SELECT * FROM {table} WHERE slug=?", (payload["slug"],)).fetchone()
                if payload.get("slug")
                else None
            )
        else:
            row = _find_existing_entity(conn, typ, payload, links or [])
        if row:
            updates = {k: v for k, v in payload.items() if k != "slug"}
            _merge_fields(conn, table, int(row["id"]), updates)
            return int(row["id"]), "updated"
    # A forced copy still needs a valid public identity. Keep the source slug
    # recognizable and add the smallest available suffix.
    if force_import and payload.get("slug"):
        base_slug = str(payload["slug"])
        candidate = base_slug
        suffix = 2
        while conn.execute(f"SELECT 1 FROM {table} WHERE slug=?", (candidate,)).fetchone():
            candidate = f"{base_slug}-{suffix}"
            suffix += 1
        payload["slug"] = candidate
    columns = list(payload)
    placeholders = ",".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
        tuple(payload[c] for c in columns),
    )
    return int(cur.lastrowid or 0), "inserted"


def _restore_portable_references(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    """Resolve stable cross-record references after every imported row exists."""
    for line_no, record in enumerate(records, start=1):
        if record.get("type") != "event" or not isinstance(record.get("data"), dict):
            continue
        data = record["data"]
        child_slug = str(data.get("slug") or "").strip()
        parent_slug = str(data.get("parent_event_slug") or "").strip()
        if not child_slug or not parent_slug:
            continue
        child = conn.execute("SELECT id FROM events WHERE slug=?", (child_slug,)).fetchone()
        parent = conn.execute("SELECT id FROM events WHERE slug=?", (parent_slug,)).fetchone()
        if child and parent and int(child["id"]) != int(parent["id"]):
            conn.execute(
                "UPDATE events SET parent_event_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(parent["id"]), int(child["id"])),
            )
        elif child and not parent:
            summary["errors"].append({
                "line": line_no,
                "error": f"Parent event not found: {parent_slug}",
            })


def _upsert_asset_record(conn: sqlite3.Connection, data: dict[str, Any]) -> tuple[int, str]:
    cols = table_columns(conn, "assets")
    payload = {k: v for k, v in data.items() if k in cols and k != "id"}
    row = None
    if payload.get("uid"):
        row = conn.execute("SELECT id FROM assets WHERE uid=?", (payload["uid"],)).fetchone()
    if not row and payload.get("content_sha256"):
        row = conn.execute("SELECT id FROM assets WHERE content_sha256=?", (payload["content_sha256"],)).fetchone()
    if not row and payload.get("checksum"):
        row = conn.execute("SELECT id FROM assets WHERE checksum=?", (payload["checksum"],)).fetchone()
    if not row and payload.get("source_url"):
        row = conn.execute("SELECT id FROM assets WHERE source_url=?", (payload["source_url"],)).fetchone()
    if not row and payload.get("path"):
        row = conn.execute("SELECT id FROM assets WHERE path=?", (payload["path"],)).fetchone()
    if row:
        _merge_asset_metadata(conn, int(row["id"]), payload)
        return int(row["id"]), "updated"
    columns = list(payload)
    placeholders = ",".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO assets({','.join(columns)}) VALUES({placeholders})",
        tuple(payload[c] for c in columns),
    )
    return int(cur.lastrowid or 0), "inserted"


def _merge_asset_metadata(conn: sqlite3.Connection, asset_id: int, updates: dict[str, Any]) -> None:
    row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        return
    existing = dict(row)
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key not in existing or key in {"path", "checksum", "uid", "content_sha256"} or value in (None, ""):
            continue
        old = existing.get(key)
        if old in (None, "") or (key == "storage_status" and old == "missing" and value in {"local", "external"}):
            assignments.append(f"{key}=?")
            params.append(value)
        elif key == "is_external":
            new_value = 1 if bool(old) or bool(value) else 0
            if new_value != int(old or 0):
                assignments.append(f"{key}=?")
                params.append(new_value)
    if assignments:
        conn.execute(
            f"UPDATE assets SET {', '.join(assignments)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*params, asset_id],
        )


def _replace_links(conn: sqlite3.Connection, typ: str, entity_id: int, links: list[dict[str, Any]]) -> int:
    if not links:
        return 0
    linked = 0
    for link in links:
        existing = conn.execute(
            """
            SELECT id, label, is_primary, sort_order
            FROM entity_links
            WHERE entity_type=? AND entity_id=? AND url=? AND role=?
            """,
            (typ, entity_id, link["url"], link["role"]),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE entity_links
                SET label=COALESCE(NULLIF(label,''), ?),
                    is_primary=CASE WHEN is_primary=1 THEN 1 ELSE ? END,
                    sort_order=MIN(sort_order, ?)
                WHERE id=?
                """,
                (link.get("label"), link["is_primary"], link["sort_order"], existing["id"]),
            )
            continue
        conn.execute(
            """
            INSERT INTO entity_links(entity_type, entity_id, url, label, role, is_primary, sort_order)
            VALUES(?,?,?,?,?,?,?)
            """,
            (typ, entity_id, link["url"], link.get("label"), link["role"], link["is_primary"], link["sort_order"]),
        )
        linked += 1
    _ensure_single_primary_entity_link(conn, typ, entity_id)
    return linked


def _replace_assets(
    conn: sqlite3.Connection,
    typ: str,
    entity_id: int,
    assets: list[dict[str, Any]],
    *,
    assets_dir: Path | None = None,
    asset_source_dir: Path | None = None,
    errors: list[str] | None = None,
    on_asset: Callable[[int, int], None] | None = None,
) -> int:
    if not assets:
        return 0
    linked = 0
    for idx, spec in enumerate(assets, start=1):
        if on_asset:
            on_asset(idx, len(assets))
        try:
            asset_id = _materialize_asset(
                conn, spec, assets_dir or Config.ASSETS_DIR, asset_source_dir=asset_source_dir
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            if errors is None:
                raise
            source = str(spec.get("path") or spec.get("url") or "asset")
            errors.append(f"{source}: {exc}")
            continue
        if not asset_id:
            continue
        existing = conn.execute(
            """
            SELECT id, role, is_primary, sort_order
            FROM asset_links
            WHERE asset_id=? AND entity_type=? AND entity_id=? AND role=?
            """,
            (asset_id, typ, entity_id, spec["role"]),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE asset_links
                SET is_primary=CASE WHEN is_primary=1 THEN 1 ELSE ? END,
                    sort_order=MIN(sort_order, ?)
                WHERE id=?
                """,
                (spec["is_primary"], spec["sort_order"], existing["id"]),
            )
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO asset_links(asset_id, entity_type, entity_id, role, is_primary, sort_order)
            VALUES(?,?,?,?,?,?)
            """,
            (asset_id, typ, entity_id, spec["role"], spec["is_primary"], spec["sort_order"]),
        )
        linked += 1
    _ensure_primary_assets(conn, typ, entity_id)
    return linked


def _materialize_asset(
    conn: sqlite3.Connection,
    spec: dict[str, Any],
    assets_dir: Path,
    *,
    asset_source_dir: Path | None = None,
) -> int:
    kind = spec.get("kind")
    db_path = str(spec.get("path") or "").strip()
    has_identity = bool(
        str(spec.get("uid") or "").strip()
        or str(spec.get("checksum") or "").strip()
        or str(spec.get("content_sha256") or "").strip()
    )
    if has_identity and db_path:
        return _restore_identity_asset(conn, spec, assets_dir, asset_source_dir=asset_source_dir)
    if db_path:
        path = Path(db_path)
        if not path.is_absolute():
            source_dir = asset_source_dir or assets_dir
            path = source_dir.parent / path if path.parts[:1] == ("assets",) else source_dir / path
        if not path.is_file():
            url = spec.get("url") or ""
            if url:
                return _download_or_defer_asset(conn, str(url), assets_dir, spec, kind)
            return 0
        source_url = str(spec.get("url") or "").strip() or None
        original_filename = unquote(Path(urlparse(source_url).path).name) if source_url else None
        return store_asset(
            conn, path, assets_dir, kind=kind, caption=spec.get("caption"),
            alt_text=spec.get("alt_text"), source_url=source_url,
            original_filename=original_filename or None,
            commit=False,
        )
    url = str(spec.get("url") or "").strip()
    final_kind = kind or infer_kind_from_url(url)
    return _download_or_defer_asset(conn, url, assets_dir, spec, final_kind)


def _restore_identity_asset(
    conn: sqlite3.Connection,
    spec: dict[str, Any],
    assets_dir: Path,
    *,
    asset_source_dir: Path | None = None,
) -> int:
    """Restore an exported asset faithfully, preserving uid/checksum and DB path.

    Dashboard exports now carry the asset identity. When one is present the
    authoritative row is matched by uid/checksum/source_url/path and the
    packaged file is placed at its original DB-tracked path, instead of being
    re-stored under a fresh hash-suffixed name.
    """
    db_path = str(spec.get("path") or "").strip()
    _validate_asset_db_path(db_path, 0)
    target = resolve_db_asset_path(assets_dir, db_path)
    source_dir = asset_source_dir or assets_dir
    source = (
        source_dir.parent / Path(*Path(db_path).parts[1:])
        if Path(db_path).parts[:1] == ("assets",)
        else source_dir / Path(db_path)
    )
    local_file: Path | None = None
    if source.is_file():
        local_file = source
    elif target.is_file():
        local_file = target
    if local_file is not None and local_file.resolve() != target.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file, target)
    storage_status = str(spec.get("storage_status") or "").strip().lower()
    if storage_status not in ASSET_STORAGE_STATUSES:
        storage_status = "local" if local_file is not None else "external" if spec.get("is_external") else "missing"
    if local_file is None and storage_status == "local":
        storage_status = "missing"
    if local_file is not None and storage_status == "missing":
        storage_status = "local"
    file_checksum = sha256_file(local_file) if local_file is not None else None
    checksum = str(spec.get("checksum") or "").strip() or file_checksum or None
    source_url = str(spec.get("url") or "").strip() or None
    payload = {
        "uid": str(spec.get("uid") or "").strip() or None,
        "filename": str(spec.get("filename") or "").strip() or target.name or "asset",
        "original_filename": str(spec.get("original_filename") or "").strip() or None,
        "path": db_path,
        "mime_type": str(spec.get("mime_type") or "").strip() or None,
        "size": spec.get("size") if isinstance(spec.get("size"), int) else None,
        "kind": str(spec.get("kind") or "other").strip().lower() or "other",
        "alt_text": spec.get("alt_text"),
        "caption": spec.get("caption"),
        "source_url": source_url,
        "storage_status": storage_status,
        "is_external": int(bool(spec.get("is_external")) or storage_status == "external"),
        "width": spec.get("width") if isinstance(spec.get("width"), int) else None,
        "height": spec.get("height") if isinstance(spec.get("height"), int) else None,
        "duration_seconds": spec.get("duration_seconds") if isinstance(spec.get("duration_seconds"), (int, float)) else None,
        "checksum": checksum,
        "content_sha256": str(spec.get("content_sha256") or "").strip()
        or (file_checksum if storage_status == "local" else checksum)
        or None,
        "source_url_sha256": str(spec.get("source_url_sha256") or "").strip()
        or (hashlib.sha256(source_url.encode("utf-8")).hexdigest() if source_url else None),
    }
    asset_id, _status = _upsert_asset_record(conn, payload)
    return asset_id


def _download_or_defer_asset(
    conn: sqlite3.Connection,
    url: str,
    assets_dir: Path,
    spec: dict[str, Any],
    kind: str | None,
) -> int:
    """Try once during import, then preserve a recoverable external record.

    The post-import recovery queue owns subsequent attempts and its persistent
    cooldown. This prevents one unreachable URL from blocking a large JSONL.
    """
    try:
        return download_asset(
            conn,
            url,
            assets_dir,
            kind=kind,
            caption=spec.get("caption"),
            alt_text=spec.get("alt_text"),
            max_retries=1,
            commit=False,
        )
    except Exception:
        return store_external_asset(
            conn,
            url,
            kind=kind or "other",
            caption=spec.get("caption"),
            alt_text=spec.get("alt_text"),
            commit=False,
        )


def _ensure_primary_assets(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> None:
    roles = [row["role"] for row in conn.execute(
        "SELECT DISTINCT role FROM asset_links WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    ).fetchall()]
    for role in roles:
        primary = conn.execute(
            """
            SELECT id FROM asset_links
            WHERE entity_type=? AND entity_id=? AND role=? AND is_primary=1
            ORDER BY sort_order ASC, id ASC LIMIT 1
            """,
            (entity_type, entity_id, role),
        ).fetchone()
        if primary:
            conn.execute(
                """
                UPDATE asset_links
                SET is_primary=CASE WHEN id=? THEN 1 ELSE 0 END
                WHERE entity_type=? AND entity_id=? AND role=?
                """,
                (primary["id"], entity_type, entity_id, role),
            )
            continue
        first = conn.execute(
            """
            SELECT id FROM asset_links
            WHERE entity_type=? AND entity_id=? AND role=?
            ORDER BY sort_order ASC, id ASC LIMIT 1
            """,
            (entity_type, entity_id, role),
        ).fetchone()
        if first:
            conn.execute("UPDATE asset_links SET is_primary=1 WHERE id=?", (first["id"],))


def _ensure_single_primary_entity_link(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> None:
    primary = conn.execute(
        """
        SELECT id FROM entity_links
        WHERE entity_type=? AND entity_id=? AND is_primary=1
        ORDER BY sort_order ASC, id ASC LIMIT 1
        """,
        (entity_type, entity_id),
    ).fetchone()
    if primary:
        conn.execute(
            """
            UPDATE entity_links
            SET is_primary=CASE WHEN id=? THEN 1 ELSE 0 END
            WHERE entity_type=? AND entity_id=?
            """,
            (primary["id"], entity_type, entity_id),
        )
        return
    first = conn.execute(
        """
        SELECT id FROM entity_links
        WHERE entity_type=? AND entity_id=?
        ORDER BY sort_order ASC, id ASC LIMIT 1
        """,
        (entity_type, entity_id),
    ).fetchone()
    if first:
        conn.execute("UPDATE entity_links SET is_primary=1 WHERE id=?", (first["id"],))


def _start_import_run(
    conn: sqlite3.Connection,
    path: Path,
    total: int,
    *,
    source_name: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO import_runs(name, source_kind, source_path, status, stats_json)
        VALUES(?, 'jsonl-v2', ?, 'running', ?)
        """,
        (source_name or path.name, str(path), json.dumps({"records": total}, ensure_ascii=False)),
    )
    return int(cur.lastrowid or 0)


def _record_import_row(
    conn: sqlite3.Connection,
    run_id: int,
    line_no: int,
    typ: str,
    table: str,
    entity_id: int,
    data: dict[str, Any],
    links: list[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    source_url = next(
        (str(item.get("url") or "") for item in links if item.get("role") in {"primary", "source", "website"}),
        None,
    )
    identity_payload, identity_hash = _import_identity(data)
    conn.execute(
        """
        INSERT INTO import_records(import_run_id, entity_type, entity_id, source_url, content_hash, raw_json)
        VALUES(?,?,?,?,?,?)
        """,
        (
            run_id,
            typ,
            entity_id,
            source_url,
            identity_hash,
            json.dumps(
                {"line_no": line_no, "table": table, "data": identity_payload, "meta": meta},
                ensure_ascii=False,
                default=str,
            ),
        ),
    )


def _should_replace_low_info_value(key: str, old: Any, new: Any) -> bool:
    if new in (None, ""):
        return False
    if key == "year":
        try:
            return int(old or 0) < 1000 and int(new) >= 1000
        except (TypeError, ValueError):
            return False
    if key in {"summary", "abstract", "body", "description", "bio", "affiliation", "journal", "location", "date_text"}:
        old_text = str(old or "").strip()
        # Ordinary imports enrich empty/placeholder fields. Choosing between
        # two real values belongs to the explicit merge workflow, so curated
        # text is never silently replaced merely because the import is longer.
        return old_text.casefold() in {"-", "x", "n/a", "none", "unknown", "tbd", "not available"}
    return False


def _identity_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold()).encode("ascii", "ignore").decode()
    text = text.replace("&", " and ")
    text = re.sub(r"\b(?:prof(?:essor)?|dr|phd|mr|mrs|ms)\.?\b", " ", text)
    text = re.sub(r"\btwo[ -]dimensional\b", " 2d ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _year(value: Any) -> str:
    match = re.search(r"\b(?:19|20)\d{2}\b", str(value or ""))
    return match.group(0) if match else ""


def _event_series(value: Any) -> str:
    text = _identity_text(value).replace(" ", "")
    for series in ("plmcn", "icp2dc", "terametanano", "qlin", "isnp", "oecs", "2dcp", "star", "newmare", "metanano"):
        if series in text:
            return series
    if "marchmeeting" in text:
        return "march-meeting"
    return ""


def _display_value(typ: str, row: dict[str, Any]) -> str:
    return str(row.get("display_name") if typ == "member" else row.get("name") if typ == "sponsor" else row.get("title") or "")


def _identity_keys(typ: str, data: dict[str, Any], urls: list[str] | None = None) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    title = _display_value(typ, data)
    normalized = _identity_text(title)
    if typ == "member":
        email = str(data.get("email") or "").strip().casefold()
        if email:
            keys.append((f"member:email:{email}", "same email address"))
        parsed_name = person_name(title)
        if len(parsed_name.normal) >= 2:
            # Token order is deliberately ignored here because legacy member
            # feeds alternate between "Given Surname" and "Surname Given".
            name_key = "|".join(sorted(parsed_name.normal))
            keys.append((f"member:name:{name_key}", "same complete person name"))
    elif typ == "publication":
        doi = _normalized_doi(data.get("doi"))
        if doi:
            keys.append((f"publication:doi:{doi}", "same DOI"))
    elif typ == "event":
        year = _year(data.get("start_date") or data.get("date_text") or title)
        series = _event_series((data.get("series_key") or "") + " " + title)
        if series and year:
            keys.append((f"event:series:{series}:{year}", f"same event series and year ({series.upper()} {year})"))
    elif typ == "news":
        if normalized and len(normalized) > 5:
            date = str(data.get("date") or "").strip()
            if date:
                keys.append((f"news:title:{normalized}:{date[:10]}", "same title and date"))
    elif normalized:
        keys.append((f"{typ}:title:{normalized}", f"same normalized {_display_label(typ)}"))
    for url in urls or []:
        clean_url = normalize_url(url)
        if clean_url:
            keys.append((f"{typ}:url:{clean_url.rstrip('/').casefold()}", "same source URL"))
    return keys


def _normalized_doi(value: Any) -> str:
    doi = str(value or "").strip().casefold()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    return unquote(doi).strip().rstrip(".,;")


def _display_label(typ: str) -> str:
    return "name" if typ in {"member", "sponsor"} else "title"


def _entity_urls(conn: sqlite3.Connection, typ: str, entity_id: int) -> list[str]:
    return [str(row[0]) for row in conn.execute(
        """SELECT url FROM entity_links
           WHERE entity_type=? AND entity_id=? AND role IN ('primary','doi','publisher','website','source')""",
        (typ, entity_id),
    ).fetchall()]


def _find_existing_entity(
    conn: sqlite3.Connection, typ: str, data: dict[str, Any], links: list[dict[str, Any]]
) -> sqlite3.Row | None:
    table = TYPE_TO_TABLE[typ]
    uid = str(data.get("uid") or "").strip()
    if uid:
        row = conn.execute(f"SELECT id FROM {table} WHERE uid=?", (uid,)).fetchone()
        if row:
            return row
    slug = str(data.get("slug") or "")
    if slug:
        row = conn.execute(f"SELECT id FROM {table} WHERE slug=?", (slug,)).fetchone()
        if row:
            return row
    _identity_payload, identity_hash = _import_identity(data)
    identity_urls = [
        str(link.get("url") or "") for link in links
        if link.get("role") in {"primary", "doi", "publisher", "website", "source"}
    ]
    provenance = conn.execute(
        """
        SELECT entity_id FROM import_records
        WHERE entity_type=?
          AND (content_hash=? OR (? <> '' AND source_url=?))
        ORDER BY id DESC
        """,
        (typ, identity_hash, identity_urls[0] if identity_urls else "", identity_urls[0] if identity_urls else ""),
    ).fetchall()
    for item in provenance:
        row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (item["entity_id"],)).fetchone()
        if row:
            return row
    incoming_keys = {key for key, _reason in _identity_keys(typ, data, identity_urls)}
    if not incoming_keys:
        return None
    member_candidates: list[sqlite3.Row] = []
    incoming_email = str(data.get("email") or "").strip().casefold()
    for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall():
        existing = dict(row)
        keys = {key for key, _reason in _identity_keys(typ, existing, _entity_urls(conn, typ, int(row["id"])))}
        if incoming_keys & keys:
            if typ == "member":
                existing_email = str(existing.get("email") or "").strip().casefold()
                if incoming_email and existing_email and incoming_email != existing_email:
                    continue
                member_candidates.append(row)
                continue
            return row
    if typ == "member" and member_candidates:
        if incoming_email:
            exact_email = [
                row for row in member_candidates
                if str(row["email"] or "").strip().casefold() == incoming_email
            ]
            if len(exact_email) == 1:
                return exact_email[0]
        known_emails = {
            str(row["email"] or "").strip().casefold()
            for row in member_candidates if str(row["email"] or "").strip()
        }
        # Do not guess between homonyms already distinguished by email.
        if len(known_emails) <= 1:
            return member_candidates[0]
    return None


def _import_identity(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    payload = {
        key: data.get(key)
        for key in ("slug", "title", "name", "display_name", "date", "body", "summary", "description")
        if data.get(key) not in (None, "")
    }
    # Slugs are aliases, not content identity: a merged source may return later
    # with its old slug and must still resolve to the surviving entity.
    digest_payload = {key: value for key, value in payload.items() if key != "slug"}
    digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return payload, digest

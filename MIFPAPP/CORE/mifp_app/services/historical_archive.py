"""Validation and import for ``mifp-historical-event-v1`` source packages.

The package is an ingest format, not a second content model. Each record is
resolved to a canonical ``events`` row and the historical-only fields are
stored in ``event_archive_entries``. Binary members are copied one at a time
through the normal asset service so large PDFs never become Python byte blobs.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from ..domain import DATE_PRECISIONS, EVENT_TYPES
from ..utils.text_utils import slugify
from .assets import AssetWriteSession, infer_kind, store_asset
from .data_quality.normalizers import clean_boilerplate
from .errors import JobCancelled
from .portability_contract import ZIP_MAX_COMPRESSION_RATIO

ARCHIVE_SCHEMA = "mifp-historical-event-v1"
_EVENT_PATH = re.compile(
    r"^archive/(?P<category>[a-z][a-z0-9-]*)/(?P<year>[0-9]{4})/"
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/event\.json$"
)
_PUBLIC_PATH = re.compile(
    r"^archive/(?P<category>[a-z][a-z0-9-]*)/(?P<year>[0-9]{4})/"
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)


class HistoricalArchiveError(ValueError):
    pass


def build_historical_archive_guide() -> str:
    """Return the agent-facing contract for historical archive ZIPs."""
    example = {
        "schema": ARCHIVE_SCHEMA,
        "uid": "event_mifp_2014_quantum_meeting",
        "slug": "mediterranean-quantum-meeting",
        "public_path": "archive/conferences/2014/mediterranean-quantum-meeting",
        "public_url": "https://events.mifp.eu/archive/conferences/2014/mediterranean-quantum-meeting/",
        "title": "Mediterranean Quantum Meeting",
        "acronym": "MQM 2014",
        "category": "conferences",
        "event_type": "conference",
        "series_key": "mediterranean-quantum-meetings",
        "dates": {
            "start": "2014-05-01",
            "end": "2014-05-02",
            "text": "1–2 May 2014",
            "precision": "range",
        },
        "location": {"display": "Rome, Italy"},
        "summary": "A recovered MIFP scientific meeting.",
        "description": "Researchers met to discuss quantum field theory.",
        "topics": ["Quantum field theory", "Fundamental interactions"],
        "topics_note": "Topics reconstructed from the programme.",
        "people": {
            "participants": [],
            "chairs": [{"name": "Ada Example", "affiliation": "MIFP"}],
            "speakers": [],
            "committee": [{"name": "Grace Example", "affiliation": "MIFP"}],
        },
        "program": [{"date": "2014-05-01", "time": "09:00", "title": "Opening session"}],
        "documents": [{
            "label": "Programme",
            "path": "assets/documents/programme.pdf",
            "kind": "pdf",
            "source_url": "https://old.example.org/programme.pdf",
        }],
        "images": [{
            "role": "logo",
            "caption": "Event logo",
            "alt": "Mediterranean Quantum Meeting logo",
            "path": "assets/images/logo.png",
            "source_url": "https://old.example.org/logo.png",
        }],
        "sources": [{"label": "Historical event page", "url": "https://old.example.org/mqm-2014/"}],
        "recovery": {"confidence": "high", "notes": ["Recovered from a static backup."]},
        "not_recovered": ["Some participant affiliations were unavailable."],
    }
    event_types = ", ".join(f"`{value}`" for value in sorted(EVENT_TYPES))
    precisions = ", ".join(f"`{value}`" for value in sorted(DATE_PRECISIONS))
    example_json = json.dumps(example, ensure_ascii=False, indent=2)
    lines = [
        "# MIFP Historical Archive package guide for agents and LLMs", "",
        "Use this document as a strict generation contract. It describes the ZIP accepted by the **Archive** "
        "dashboard importer, not the general Import / Export JSONL format.", "",
        f"Schema identifier: `{ARCHIVE_SCHEMA}`", "",
        "## Required output", "",
        "Create one ZIP file. Every historical event has its own directory and exactly one UTF-8 `event.json`.", "",
        "```text",
        "archive/",
        "  <category>/",
        "    <YYYY>/",
        "      <slug>/",
        "        event.json",
        "        assets/",
        "          documents/<file>",
        "          images/<file>",
        "```", "",
        "Example record path: "
        "`archive/conferences/2014/mediterranean-quantum-meeting/event.json`.", "",
        "The values of `category`, the four-digit year, and `slug` in the path must agree with the record. "
        "`public_path` must be the same path without `/event.json`. Paths use `/`, never absolute paths, `..`, "
        "backslashes, drive letters, or symbolic links.", "",
        "## Identity and canonical Events", "",
        "Archive records extend the canonical Events catalogue; they do not create an independent catalogue. "
        "The importer first matches `uid`, then `slug`. If those values identify two different existing events, "
        "validation reports a conflict and the import writes nothing. Use stable, deterministic identifiers and "
        "never reuse an identifier for a different event.", "",
        "A new event is published as a non-featured canonical Event. For an existing event, the importer fills "
        "missing canonical fields but does not overwrite curated non-empty fields. Re-importing the same package "
        "is safe and updates the one-to-one archive extension.", "",
        "## Minimum data required for every archived event", "",
        "Every `event.json` must contain enough information to render a useful Archive entry without consulting "
        "the original website. The following six content groups are mandatory:", "",
        "1. **Title** — `title` must be a non-empty human-readable event title.",
        "2. **Dates** — `dates` must contain at least one non-empty value among `start`, `end`, or `text`.",
        "3. **Logo** — `images` must contain exactly one descriptor with `role: \"logo\"`; its `path` must point to a real packaged image in the ZIP.",
        "4. **Location** — `location.display` must be non-empty.",
        "5. **People** — `people` must explicitly contain the four arrays `participants`, `chairs`, `speakers`, and `committee`. Use an empty array only when that role was genuinely not recovered; record material gaps in `not_recovered`.",
        "6. **Short description** — `summary` must be a concise, non-empty description suitable for the Archive listing.", "",
        "Do not substitute a generic event image for the logo when a real event logo is available. If the historical "
        "source does not contain a recoverable logo, the record is incomplete for this package contract and must be "
        "reported for manual review rather than silently omitting the field.", "",
        "## `event.json` fields", "",
        "| Field | Required | Shape and rule |", "|---|---:|---|",
        f"| `schema` | yes | Exact string `{ARCHIVE_SCHEMA}`. |",
        "| `uid` | yes | Stable event identity; keep it unchanged across reruns. |",
        "| `slug` | yes | Lowercase URL slug using `a-z`, digits and single hyphens; must match the directory. |",
        "| `public_path` | yes | Exact `archive/<category>/<YYYY>/<slug>` path, without leading/trailing slash. |",
        "| `title` | yes | Human-readable event title. |",
        "| `category` | yes | Lowercase path token beginning with a letter; must match the directory. |",
        "| `public_url` | no | Original historical public URL, when known. |",
        "| `acronym` | no | Historical acronym or short label. |",
        f"| `event_type` | no | Defaults to `other`. Allowed: {event_types}. |",
        "| `series_key` | no | Stable lowercase key connecting events in the same series. |",
        f"| `dates` | yes | Object with `start`, `end`, `text`, and `precision`; at least one of start/end/text must be non-empty. Allowed precision: {precisions}. |",
        "| `location` | yes | Object with a non-empty `display` value for the public location string. |",
        "| `summary` | yes | Concise, non-empty archive-list description. |",
        "| `description` | no | Longer recovered description. Remove navigation, cookie and template boilerplate. |",
        "| `topics` | no | Array of topic strings. |",
        "| `topics_note` | no | Note explaining how topics were recovered or inferred. |",
        "| `people` | yes | Object containing all four arrays: `participants`, `chairs`, `speakers`, `committee`. |",
        "| `program` | no | Array of programme entries; preserve recovered dates, times, titles and speakers. |",
        "| `documents` | no | Array of packaged document descriptors. |",
        "| `images` | yes | Packaged image descriptors; exactly one item must have `role: \"logo\"` and reference a real ZIP member. |",
        "| `sources` | no | Array of HTTP(S) URL strings or objects with `url` and optional `label`. |",
        "| `recovery` | no | Object containing provenance, confidence and recovery notes. |",
        "| `not_recovered` | no | Array stating material known to be missing; never invent missing facts. |", "",
        "Dates use ISO `YYYY-MM-DD` when the exact day is known. Use `dates.text` to preserve an original, "
        "less precise historical expression. The year directory is authoritative for archive navigation and must "
        "represent the event's archive year.", "",
        "## People and programme guidance", "",
        "People entries should use `name` and may include `affiliation`, `role`, `country`, or a source note. "
        "The `people` object must always expose `participants`, `chairs`, `speakers`, and `committee` separately. "
        "Do not merge people based only on a similar name, and do not move a person between roles without evidence. "
        "When one person has multiple documented roles, include that person in each applicable role group. Programme "
        "entries should preserve source order; omit unknown fields instead of guessing them.", "",
        "## Packaged documents and images", "",
        "Each `documents[]` or `images[]` item must contain a relative `path`. The path is resolved from the "
        "directory containing that event's `event.json`, so every referenced file must be present in the ZIP.", "",
        "Document descriptors may contain `label`, `kind`, `source_url`; image descriptors may contain `role`, `caption`, "
        "`alt`, `kind`, `source_url`. The event logo must use `role: \"logo\"`. Put PDFs under `assets/documents/` and "
        "images under `assets/images/`. Do not "
        "embed Base64 or remote-only placeholders. The importer copies packaged binaries into the managed asset "
        "store and records valid HTTP(S) historical sources as links.", "",
        "The ZIP is rejected for unsafe members, duplicate member names, excessive expanded size or suspicious "
        "compression ratios. Keep only the files needed for the recovered records.", "",
        "## Complete example", "", "```json", example_json, "```", "",
        "The example JSON belongs at:", "",
        "```text",
        "archive/conferences/2014/mediterranean-quantum-meeting/event.json",
        "```", "",
        "and its two referenced files belong at:", "",
        "```text",
        "archive/conferences/2014/mediterranean-quantum-meeting/assets/documents/programme.pdf",
        "archive/conferences/2014/mediterranean-quantum-meeting/assets/images/logo.png",
        "```", "",
        "## Data-quality rules", "",
        "- Transcribe source facts; do not infer dates, affiliations, people or programme items without evidence.",
        "- Record uncertainty in `recovery` and known gaps in `not_recovered`.",
        "- Remove menus, cookie notices, footer text, repeated headers and unrelated page chrome.",
        "- Keep `uid`, `slug`, `category`, year and `public_path` mutually consistent.",
        "- Do not return an event record until title, dates, logo, location, people-role groups and summary are populated according to this contract.",
        "- Deduplicate records inside the package: no repeated UID or slug.",
        "- Preserve source URLs and labels so an editor can audit the recovery.",
        "- Never include credentials, private notes, prompts or chain-of-thought.", "",
        "## Validation workflow", "",
        "1. Open Dashboard → Archive and select the ZIP.",
        "2. Run **Validate only** first. This checks paths, identities, records and asset references without writing.",
        "3. Resolve every identity conflict and missing asset warning.",
        "4. Run **Import archive** only after the preview is correct.", "",
        "## Copy/paste task prompt for an agent", "",
        "```text",
        "Create a MIFP Historical Archive ZIP from the supplied source material.",
        "Treat MIFP_LLM_HISTORICAL_ARCHIVE_GUIDE.md as a strict contract.",
        "Generate one event.json per event. Every event must include title, dates, one packaged logo, location,",
        "participants/chairs/speakers/committee arrays, and a concise summary. Package every referenced asset,",
        "preserve provenance and uncertainty, and do not invent missing facts.",
        "Before returning the ZIP, verify every path and the identity/path consistency rules.",
        "```", "",
        "## Final checklist", "",
        f"- [ ] Every record uses schema `{ARCHIVE_SCHEMA}`.",
        "- [ ] Every `event.json` is valid UTF-8 JSON with no Markdown fences.",
        "- [ ] Category, year, slug and `public_path` match the ZIP path exactly.",
        "- [ ] UIDs and slugs are stable and unique within the package.",
        "- [ ] Every event has title, usable dates, location and a concise summary.",
        "- [ ] Every event has `participants`, `chairs`, `speakers`, and `committee` arrays.",
        "- [ ] Every event has exactly one `images[]` item with `role: \"logo\"`.",
        "- [ ] Every referenced document and image exists at its relative path, including the logo.",
        "- [ ] Source URLs, recovery notes and known gaps are preserved.",
        "- [ ] The package has been run through **Validate only** before import.", "",
    ]
    return "\n".join(lines)


def _safe_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > Config.IMPORT_MAX_FILES:
        raise HistoricalArchiveError("Archive exceeds the configured file-count limit")
    if sum(info.file_size for info in infos) > Config.IMPORT_MAX_UNPACKED_BYTES:
        raise HistoricalArchiveError("Archive expands beyond the configured size limit")
    output: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if not name or "\x00" in name or "\\" in name or name.startswith(("/", "./")):
            raise HistoricalArchiveError(f"Unsafe ZIP member path: {name!r}")
        parts = PurePosixPath(name.rstrip("/")).parts
        if not parts or ":" in parts[0] or any(part in {"", ".", ".."} for part in parts):
            raise HistoricalArchiveError(f"Unsafe ZIP member path: {name!r}")
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise HistoricalArchiveError(f"ZIP contains a symbolic link: {name}")
        if name in output:
            raise HistoricalArchiveError(f"ZIP contains a duplicate member: {name}")
        if info.compress_size == 0 and info.file_size:
            raise HistoricalArchiveError(f"ZIP member has an invalid compressed size: {name}")
        if info.compress_size and info.file_size > 1024 * 1024:
            if info.file_size / info.compress_size > ZIP_MAX_COMPRESSION_RATIO:
                raise HistoricalArchiveError(f"ZIP member has a suspicious compression ratio: {name}")
        output[name] = info
    return output


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HistoricalArchiveError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HistoricalArchiveError(f"{label} must be a list")
    return value


def _validate_event(raw: bytes, member: str, members: dict[str, zipfile.ZipInfo]) -> dict[str, Any]:
    if len(raw) > Config.IMPORT_MAX_MANIFEST_BYTES:
        raise HistoricalArchiveError(f"{member} exceeds the structured-record size limit")
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalArchiveError(f"Invalid JSON in {member}") from exc
    event = _object(event, member)
    match = _EVENT_PATH.fullmatch(member)
    if not match:
        raise HistoricalArchiveError(f"Unexpected event.json path: {member}")
    if event.get("schema") != ARCHIVE_SCHEMA:
        raise HistoricalArchiveError(f"Unsupported archive schema in {member}: {event.get('schema')!r}")
    for required in ("uid", "slug", "title", "public_path", "category"):
        if not str(event.get(required) or "").strip():
            raise HistoricalArchiveError(f"{member} is missing {required}")
    slug = slugify(str(event["slug"]))
    expected_path = f"archive/{match['category']}/{match['year']}/{match['slug']}"
    if slug != event["slug"] or event["slug"] != match["slug"]:
        raise HistoricalArchiveError(f"Slug/path mismatch in {member}")
    if str(event["category"]) != match["category"] or str(event["public_path"]).strip("/") != expected_path:
        raise HistoricalArchiveError(f"Validated public_path does not match {member}")
    public_match = _PUBLIC_PATH.fullmatch(str(event["public_path"]).strip("/"))
    if not public_match:
        raise HistoricalArchiveError(f"Unsafe public_path in {member}")
    dates = _object(event.get("dates"), f"{member}.dates")
    if not any(str(dates.get(key) or "").strip() for key in ("start", "end", "text")):
        raise HistoricalArchiveError(f"{member}.dates must contain start, end, or text")
    location = _object(event.get("location"), f"{member}.location")
    if not str(location.get("display") or "").strip():
        raise HistoricalArchiveError(f"{member}.location.display is required")
    if not str(event.get("summary") or "").strip():
        raise HistoricalArchiveError(f"{member}.summary is required")
    people = _object(event.get("people"), f"{member}.people")
    required_people_roles = ("participants", "chairs", "speakers", "committee")
    for role in required_people_roles:
        if role not in people:
            raise HistoricalArchiveError(f"{member}.people.{role} is required")
        _list(people[role], f"{member}.people.{role}")
    for role, rows in people.items():
        _list(rows, f"{member}.people.{role}")
    images = _list(event.get("images"), f"{member}.images")
    logo_items = [
        _object(item, f"{member}.images[{index}]")
        for index, item in enumerate(images, 1)
        if isinstance(item, dict) and str(item.get("role") or "").strip().casefold() == "logo"
    ]
    if len(logo_items) != 1:
        raise HistoricalArchiveError(f"{member}.images must contain exactly one item with role=logo")
    logo_path = str(logo_items[0].get("path") or "").strip()
    event_type = str(event.get("event_type") or "other").strip().casefold()
    if event_type not in EVENT_TYPES:
        raise HistoricalArchiveError(f"Invalid event_type in {member}: {event_type}")
    precision = str(dates.get("precision") or "unknown").strip().casefold()
    if precision not in DATE_PRECISIONS:
        raise HistoricalArchiveError(f"Invalid date precision in {member}: {precision}")
    base = str(PurePosixPath(member).parent)
    referenced: list[dict[str, Any]] = []
    missing_assets: list[str] = []
    for group, default_kind in (("documents", "document"), ("images", "image")):
        for index, item in enumerate(_list(event.get(group), f"{member}.{group}"), 1):
            item = _object(item, f"{member}.{group}[{index}]")
            relative = str(item.get("path") or "")
            if not relative or "\\" in relative:
                raise HistoricalArchiveError(f"Invalid asset path in {member}")
            rel_parts = PurePosixPath(relative).parts
            if PurePosixPath(relative).is_absolute() or any(p in {"", ".", ".."} for p in rel_parts):
                raise HistoricalArchiveError(f"Unsafe asset path in {member}: {relative}")
            archive_name = f"{base}/{relative}"
            info = members.get(archive_name)
            if not info:
                # The source builder may content-deduplicate a shared binary
                # while retaining the same content-addressed relative name in
                # more than one event record. Resolve only an unambiguous
                # archive-wide suffix match; never guess by a loose basename.
                suffix = "/" + relative
                matches = [candidate for candidate in members if candidate.endswith(suffix)]
                if len(matches) == 1:
                    archive_name = matches[0]
                    info = members[archive_name]
            if not info or archive_name.endswith("/"):
                if group == "images" and relative == logo_path:
                    raise HistoricalArchiveError(f"Required logo file is missing from ZIP: {archive_name}")
                missing_assets.append(archive_name)
                continue
            referenced.append({"group": group, "kind": item.get("kind") or default_kind,
                               "member": archive_name, "spec": item})
    event["_member"] = member
    event["_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    event["_year"] = int(match["year"])
    event["_assets"] = referenced
    event["_missing_assets"] = missing_assets
    event["_dates"] = dates
    event["_location"] = location
    event["_people"] = people
    event["_precision"] = precision
    event["_event_type"] = event_type
    return event


def _resolve_event(conn: sqlite3.Connection, event: dict[str, Any]) -> tuple[int | None, str, list[str]]:
    uid_row = conn.execute("SELECT * FROM events WHERE uid=?", (event["uid"],)).fetchone()
    slug_row = conn.execute("SELECT * FROM events WHERE slug=?", (event["slug"],)).fetchone()
    if uid_row and slug_row and int(uid_row["id"]) != int(slug_row["id"]):
        return None, "conflict", ["UID and slug resolve to different canonical events"]
    row = uid_row or slug_row
    if not row:
        return None, "create", []
    archive = conn.execute("SELECT source_record_sha256 FROM event_archive_entries WHERE event_id=?", (row["id"],)).fetchone()
    missing = []
    canonical = _canonical_fields(event)
    for key, value in canonical.items():
        if key not in {"uid", "slug", "is_featured"} and value not in (None, "") and row[key] in (None, ""):
            missing.append(key)
    if archive and archive["source_record_sha256"] == event["_raw_sha256"] and not missing:
        return int(row["id"]), "unchanged", []
    action = "fill missing fields" if missing else ("update archive" if archive else "attach")
    return int(row["id"]), action, missing


def _canonical_fields(event: dict[str, Any]) -> dict[str, Any]:
    dates = event["_dates"]
    description = str(event.get("description") or "").strip()
    if description:
        cleaned, removed = clean_boilerplate(description)
        if removed and len(cleaned) >= len(description) * 0.6:
            description = cleaned
    return {
        "uid": str(event["uid"]).strip(), "slug": event["slug"],
        "title": str(event["title"]).strip(), "start_date": dates.get("start") or None,
        "end_date": dates.get("end") or None, "date_text": dates.get("text") or None,
        "date_precision": event["_precision"],
        "location": event["_location"].get("display") or None,
        "description": description or None, "event_type": event["_event_type"],
        "series_key": event.get("series_key") or None, "is_featured": 0,
        "review_status": "published",
    }


def inspect_historical_archive(path: Path, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file() or path.stat().st_size > Config.IMPORT_MAX_ZIP_BYTES:
        raise HistoricalArchiveError("Archive is unavailable or exceeds the configured upload limit")
    actions: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    years: list[int] = []
    records: list[dict[str, Any]] = []
    documents = images = people_count = missing_assets = 0
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise HistoricalArchiveError("Uploaded file is not a valid ZIP archive") from exc
    with archive as zf:
        members = _safe_members(zf)
        event_members = sorted(name for name in members if name.endswith("/event.json"))
        if not event_members:
            raise HistoricalArchiveError("Archive contains no historical event records")
        seen_uid: set[str] = set()
        seen_slug: set[str] = set()
        for name in event_members:
            event = _validate_event(zf.read(name), name, members)
            if event["uid"] in seen_uid or event["slug"] in seen_slug:
                raise HistoricalArchiveError(f"Duplicate event identity in package: {name}")
            seen_uid.add(event["uid"]); seen_slug.add(event["slug"])
            event_id, action, warnings = _resolve_event(conn, event) if conn else (None, "create", [])
            actions[action] += 1
            categories[event["category"]] += 1
            years.append(event["_year"])
            documents += len(event.get("documents") or [])
            images += len(event.get("images") or [])
            people_count += sum(len(rows) for rows in event["_people"].values())
            missing_assets += len(event["_missing_assets"])
            warnings.extend(f"Referenced package file is missing: {item}" for item in event["_missing_assets"])
            records.append({"uid": event["uid"], "slug": event["slug"], "title": event["title"],
                            "category": event["category"], "year": event["_year"],
                            "action": action, "event_id": event_id, "warnings": warnings})
    return {"schema": ARCHIVE_SCHEMA, "package_sha256": _sha256_file(path),
            "events": len(records), "documents": documents, "images": images,
            "people": people_count, "categories": dict(categories),
            "missing_assets": missing_assets,
            "year_min": min(years), "year_max": max(years),
            "actions": dict(actions), "records": records}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _install_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, temp_root: Path) -> Path:
    suffix = PurePosixPath(info.filename).suffix or ".bin"
    fd, name = tempfile.mkstemp(prefix="archive-asset-", suffix=suffix, dir=temp_root)
    with __import__("os").fdopen(fd, "wb") as target, zf.open(info) as source:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    return Path(name)


def import_historical_archive(
    conn: sqlite3.Connection, path: Path, assets_dir: Path, *, dry_run: bool = False,
    source_name: str | None = None, cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[str, int], None] | None = None, commit: bool = True,
) -> dict[str, Any]:
    preview = inspect_historical_archive(path, conn)
    if dry_run:
        preview["dry_run"] = True
        return preview
    if preview["actions"].get("conflict"):
        raise HistoricalArchiveError(
            f"Package contains {preview['actions']['conflict']} canonical identity conflict(s); no changes were made"
        )
    run = conn.execute(
        "INSERT INTO import_runs(name,source_kind,source_path,status,stats_json) VALUES(?,?,?,?,?)",
        (source_name or Path(path).name, "historical-archive", str(Path(path).name), "running", "{}"),
    )
    run_id = int(run.lastrowid)
    writer = AssetWriteSession(Path(assets_dir))
    installed_assets = linked_assets = linked_links = 0
    try:
        with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="mifp-archive-assets-") as temp:
            members = _safe_members(zf)
            event_names = sorted(name for name in members if name.endswith("/event.json"))
            for index, name in enumerate(event_names, 1):
                if cancel_check and cancel_check():
                    raise JobCancelled("Historical archive import cancelled")
                if progress:
                    progress(f"Importing historical event {index}/{len(event_names)}", 5 + 90 * index // len(event_names))
                raw = zf.read(name)
                event = _validate_event(raw, name, members)
                event_id, action, missing = _resolve_event(conn, event)
                if action == "conflict":
                    raise HistoricalArchiveError(f"Identity conflict for {event['slug']}")
                canonical = _canonical_fields(event)
                if event_id is None:
                    columns = list(canonical)
                    cur = conn.execute(
                        f"INSERT INTO events({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                        tuple(canonical[key] for key in columns),
                    )
                    event_id = int(cur.lastrowid)
                else:
                    for field in missing:
                        conn.execute(f"UPDATE events SET {field}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                     (canonical[field], event_id))
                    conn.execute("UPDATE events SET is_featured=0 WHERE id=?", (event_id,))
                conn.execute(
                    "INSERT INTO event_archive_entries(event_id,source_schema,public_path,original_public_url,"
                    "category,archive_year,acronym,summary,topics_json,topics_note,people_json,programme_json,"
                    "recovery_json,not_recovered_json,media_json,source_record_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(event_id) DO UPDATE SET source_schema=excluded.source_schema,"
                    "public_path=excluded.public_path,original_public_url=excluded.original_public_url,"
                    "category=excluded.category,archive_year=excluded.archive_year,acronym=excluded.acronym,"
                    "summary=excluded.summary,topics_json=excluded.topics_json,topics_note=excluded.topics_note,"
                    "people_json=excluded.people_json,programme_json=excluded.programme_json,"
                    "recovery_json=excluded.recovery_json,not_recovered_json=excluded.not_recovered_json,"
                    "media_json=excluded.media_json,source_record_sha256=excluded.source_record_sha256,updated_at=CURRENT_TIMESTAMP",
                    (event_id, ARCHIVE_SCHEMA, event["public_path"], event.get("public_url"), event["category"],
                     event["_year"], event.get("acronym") or None, event.get("summary") or None,
                     _json(event.get("topics") or []), event.get("topics_note") or None,
                     _json(event["_people"]), _json(event.get("program") or []),
                     _json(event.get("recovery") or {}), _json(event.get("not_recovered") or []),
                     _json({"documents": event.get("documents") or [], "images": event.get("images") or []}),
                     event["_raw_sha256"]),
                )
                for order, source in enumerate(event.get("sources") or [], 1):
                    url = source.get("url") if isinstance(source, dict) else source
                    label = source.get("label") if isinstance(source, dict) else "Historical source"
                    if str(url or "").startswith(("http://", "https://")):
                        cur = conn.execute("INSERT OR IGNORE INTO entity_links(entity_type,entity_id,url,label,role,is_primary,sort_order) VALUES('event',?,?,?,?,0,?)",
                                           (event_id, str(url), label, "source", order))
                        linked_links += max(cur.rowcount, 0)
                first_image_id: int | None = None
                for order, asset in enumerate(event["_assets"], 1):
                    temp_path = _install_member(zf, members[asset["member"]], Path(temp))
                    spec = asset["spec"]
                    try:
                        kind = str(asset["kind"] or infer_kind(temp_path)).casefold()
                        if kind == "document" and temp_path.suffix.casefold() == ".pdf":
                            kind = "pdf"
                        asset_id = store_asset(
                            conn, temp_path, Path(assets_dir), kind=kind,
                            caption=spec.get("label") or spec.get("caption"),
                            alt_text=spec.get("alt"), source_url=spec.get("source_url"),
                            original_filename=PurePosixPath(str(spec["path"])).name,
                            commit=False, file_session=writer,
                        )
                        installed_assets += 1
                    finally:
                        temp_path.unlink(missing_ok=True)
                    role = "document" if asset["group"] == "documents" else "gallery"
                    cur = conn.execute("INSERT OR IGNORE INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order) VALUES(?,'event',?,?,?,?)",
                                       (asset_id, event_id, role, 0, order))
                    linked_assets += max(cur.rowcount, 0)
                    if role == "gallery" and first_image_id is None:
                        first_image_id = asset_id
                if first_image_id and not conn.execute("SELECT 1 FROM asset_links WHERE entity_type='event' AND entity_id=? AND role IN ('cover','logo')", (event_id,)).fetchone():
                    conn.execute("INSERT OR IGNORE INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order) VALUES(?,'event',?,'cover',1,0)", (first_image_id, event_id))
                    linked_assets += 1
                conn.execute("INSERT INTO import_records(import_run_id,entity_type,entity_id,source_url,source_path,content_hash,raw_json) VALUES(?,?,?,?,?,?,?)",
                             (run_id, "event", event_id, event.get("public_url"), name, event["_raw_sha256"], raw.decode("utf-8")))
        result = {**preview, "dry_run": False, "run_id": run_id, "assets_processed": installed_assets,
                  "asset_links_added": linked_assets, "source_links_added": linked_links}
        conn.execute("UPDATE import_runs SET status='completed',completed_at=CURRENT_TIMESTAMP,stats_json=? WHERE id=?", (_json(result), run_id))
        if commit:
            conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        writer.rollback()
        try:
            cur = conn.execute("UPDATE import_runs SET status='failed',completed_at=CURRENT_TIMESTAMP,notes=? WHERE id=?", (str(exc)[:1000], run_id))
            if not cur.rowcount:
                conn.execute(
                    "INSERT INTO import_runs(name,source_kind,source_path,status,completed_at,stats_json,notes) "
                    "VALUES(?,?,?,?,CURRENT_TIMESTAMP,'{}',?)",
                    (source_name or Path(path).name, "historical-archive", Path(path).name, "failed", str(exc)[:1000]),
                )
            if commit:
                conn.commit()
        except sqlite3.Error:
            pass
        raise

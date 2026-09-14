#!/usr/bin/env python3
"""Create deterministic dashboard JSONL v2 and ZIP artifacts from scraper data."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from artifact_normalizer import normalize_all

log = logging.getLogger(__name__)

PORTABLE_TYPES = ("member", "event", "news", "publication", "research_area", "sponsor")
CONTENT_PACKAGE_FORMAT = "mifp-content"
CONTENT_PACKAGE_VERSION = 1

TYPE_FILES = {
    "member": "members.jsonl",
    "event": "events.jsonl",
    "news": "news.jsonl",
    "publication": "publications.jsonl",
    "research_area": "research_areas.jsonl",
    "sponsor": "sponsors.jsonl",
}
ALLOWED_FIELDS = {
    "event": {"slug", "title", "start_date", "end_date", "date_text", "date_precision", "location", "description", "event_type", "series_key", "parent_event_id", "review_status", "is_featured", "sort_order"},
    "news": {"slug", "title", "news_type", "card_layout", "date", "date_text", "date_precision", "date_is_inferred", "date_inference_rule", "original_date_text", "summary", "body", "review_status", "is_featured", "source_kind", "source_priority", "source_order", "display_order", "sort_order"},
    "member": {"slug", "first_name", "last_name", "display_name", "affiliation", "country", "email", "role", "field", "bio", "review_status", "is_active", "sort_order"},
    "publication": {"slug", "title", "year", "authors", "journal", "doi", "abstract", "date_text", "date_precision", "review_status", "sort_order"},
    "research_area": {"slug", "title", "summary", "description", "review_status", "sort_order"},
    "sponsor": {"slug", "name", "description", "sponsor_type", "tier", "is_active", "sort_order"},
}
VALID_REVIEW = {"draft", "review", "published", "quarantined", "duplicate"}
LINK_ROLES = {"primary", "website", "source", "doi", "publisher", "registration", "program", "document", "social", "other"}
ASSET_ROLES = {"cover", "gallery", "attachment", "logo", "document", "profile"}
HTML_PREFIXES = (b"<!doctype html", b"<html", b"<head", b"<body")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slugify(value: Any) -> str:
    text = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:120] or "record"


def normalize_url(value: Any) -> str:
    raw = clean(value)
    if not raw.startswith(("http://", "https://")):
        return ""
    parts = urlsplit(raw)
    if not parts.hostname:
        return ""
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((scheme, host + port, path, parts.query, ""))


def _review_status(value: Any) -> str:
    status = clean(value).casefold().replace("needs_review", "review")
    return status if status in VALID_REVIEW else "published"


def _link(url: Any, role: str, label: Any = "", primary: bool = False) -> dict[str, Any] | None:
    normalized = normalize_url(url)
    if not normalized:
        return None
    safe_role = role if role in LINK_ROLES else "other"
    return {"url": normalized, "role": safe_role, "label": clean(label), "is_primary": bool(primary)}


def _asset(item: Any, default_role: str) -> dict[str, Any] | None:
    source = item if isinstance(item, dict) else {"url": item}
    url = normalize_url(source.get("url") or source.get("source_url"))
    path = clean(source.get("path") or source.get("local_path"))
    if path:
        path = path.replace("\\", "/").lstrip("/")
        if ".." in Path(path).parts:
            path = ""
    if not url and not path:
        return None
    role = clean(source.get("role") or default_role).casefold()
    if role not in ASSET_ROLES:
        role = default_role if default_role in ASSET_ROLES else "attachment"
    result: dict[str, Any] = {
        "role": role,
        "alt_text": clean(source.get("alt_text") or source.get("label")),
        "caption": clean(source.get("caption")),
        "is_primary": bool(source.get("is_primary") or role in {"cover", "logo", "profile"}),
    }
    if url:
        result["url"] = url
    if path:
        result["path"] = path
    return result


def _dedupe(items: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        marker = clean(item.get(key)).casefold()
        if not marker or marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def canonicalize(record: dict[str, Any], source: str) -> dict[str, Any] | None:
    typ = clean(record.get("type")).casefold()
    if typ not in PORTABLE_TYPES:
        return None
    title_key = "display_name" if typ == "member" else "name" if typ == "sponsor" else "title"
    title = clean(record.get(title_key) or record.get("name") or record.get("title"))
    if not title:
        return None
    data = {key: record[key] for key in ALLOWED_FIELDS[typ] if record.get(key) not in (None, "", [], {})}
    data[title_key] = title
    data.setdefault("slug", slugify(record.get("slug") or title))
    if typ != "sponsor":
        data["review_status"] = _review_status(record.get("review_status"))
    if typ == "event":
        if record.get("date") and not data.get("start_date"):
            data["start_date"] = record["date"]
        if record.get("place") and not data.get("location"):
            data["location"] = record["place"]
    if typ == "member":
        data.setdefault("first_name", clean(record.get("first_name")))
        data.setdefault("last_name", clean(record.get("last_name")))
        data.setdefault("is_active", True)
    if typ == "sponsor":
        data.setdefault("is_active", True)

    links: list[dict[str, Any]] = []
    for item in record.get("links") or []:
        if isinstance(item, dict):
            candidate = _link(item.get("url"), clean(item.get("role") or "other"), item.get("label") or item.get("text"), bool(item.get("is_primary")))
            if candidate:
                links.append(candidate)
    for key, role, label in (("url", "primary", "Website"), ("home_url", "primary", "Website"), ("website", "website", "Website"), ("pdf_url", "document", "Document"), ("doi", "doi", "DOI")):
        candidate = _link(record.get(key), role, label, role == "primary")
        if candidate:
            links.append(candidate)

    assets: list[dict[str, Any]] = []
    for key, role in (("images", "gallery"), ("documents", "document"), ("assets", "attachment")):
        for item in record.get(key) or []:
            candidate = _asset(item, role)
            if candidate:
                assets.append(candidate)
    for key, role in (("image", "cover"), ("cover_url", "cover"), ("logo_url", "logo"), ("profile_image", "profile")):
        candidate = _asset(record.get(key), role)
        if candidate:
            assets.append(candidate)

    source_url = normalize_url(record.get("source_url") or record.get("canonical_url") or record.get("page_url"))
    meta: dict[str, Any] = {"source": source}
    if source_url:
        meta["source_url"] = source_url
    if record.get("scraped_at"):
        meta["scraped_at"] = record["scraped_at"]
    return {
        "type": typ,
        "data": data,
        "links": _dedupe(links, "url"),
        "assets": _dedupe(assets, "url") if any(a.get("url") for a in assets) else assets,
        "meta": meta,
    }


def information_score(record: dict[str, Any]) -> int:
    data = record.get("data") or {}
    return sum(min(len(clean(value)), 500) for value in data.values()) + 80 * len(record.get("links") or []) + 100 * len(record.get("assets") or [])


def identity(record: dict[str, Any]) -> tuple[str, str]:
    typ, data = record["type"], record["data"]
    links = record.get("links") or []
    primary = next((normalize_url(link.get("url")) for link in links if link.get("role") in {"primary", "website", "doi", "document"}), "")
    if typ == "member":
        email = clean(data.get("email")).casefold()
        return typ, "email:" + email if email else "name:" + slugify(data.get("display_name"))
    if typ == "event":
        if primary:
            return typ, "url:" + primary.rstrip("/")
        year = clean(data.get("start_date") or data.get("date_text"))[:4]
        series = clean(data.get("series_key")).casefold()
        return typ, ("series:" + series + ":" + year) if series and year else "title:" + slugify(data.get("title")) + ":" + year
    if typ == "publication":
        doi = clean(data.get("doi")).casefold().removeprefix("https://doi.org/").removeprefix("doi:")
        return typ, "doi:" + doi if doi else ("url:" + primary if primary else "title:" + slugify(data.get("title")) + ":" + str(data.get("year") or ""))
    if typ == "news":
        return typ, "url:" + primary if primary else "title:" + slugify(data.get("title")) + ":" + clean(data.get("date"))[:10]
    if typ == "sponsor":
        return typ, "name:" + slugify(data.get("name"))
    return typ, "slug:" + clean(data.get("slug")).casefold()


def merge_records(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates = 0
    fused = 0
    for incoming in records:
        key = identity(incoming)
        current = merged.get(key)
        if current is None:
            merged[key] = incoming
            continue
        duplicates += 1
        base, extra = (incoming, current) if information_score(incoming) > information_score(current) else (current, incoming)
        base = json.loads(json.dumps(base, ensure_ascii=False))
        for field, value in (extra.get("data") or {}).items():
            if base["data"].get(field) in (None, "", [], {}):
                base["data"][field] = value
        base["links"] = _dedupe([*(base.get("links") or []), *(extra.get("links") or [])], "url")
        asset_items = [*(base.get("assets") or []), *(extra.get("assets") or [])]
        seen_assets: set[str] = set()
        base["assets"] = []
        for asset in asset_items:
            marker = clean(asset.get("url") or asset.get("path")).casefold()
            if marker and marker not in seen_assets:
                seen_assets.add(marker)
                base["assets"].append(asset)
        sources = []
        for item in (base.get("meta") or {}, extra.get("meta") or {}):
            source = clean(item.get("source"))
            source_url = clean(item.get("source_url"))
            if source or source_url:
                sources.append({"source": source, "source_url": source_url})
        base["meta"] = {**(base.get("meta") or {}), "sources": list({json.dumps(x, sort_keys=True): x for x in sources}.values())}
        merged[key] = base
        fused += 1
    output = sorted(merged.values(), key=lambda item: (item["type"], clean(item["data"].get("slug"))))
    return output, {"duplicates_removed": duplicates, "records_fused": fused}


def read_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if set(item) <= {"type", "data", "links", "assets", "meta"} and isinstance(item.get("data"), dict):
                if item.get("type") in PORTABLE_TYPES:
                    typ = item["type"]
                    item["data"] = {
                        key: value for key, value in item["data"].items()
                        if key in ALLOWED_FIELDS[typ]
                    }
                    rows.append(item)
            else:
                canonical = canonicalize(item, "unknown")
                if canonical:
                    rows.append(canonical)
    return rows


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def write_artifacts(records: list[dict[str, Any]], output_dir: Path, zip_name: str, source_dirs: Iterable[Path] = ()) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records, merge_stats = merge_records(records)
    counts = dict(sorted(Counter(row["type"] for row in records).items()))
    zip_path = output_dir / zip_name
    tmp_zip = zip_path.with_suffix(zip_path.suffix + ".tmp")
    manifest_files: list[dict[str, Any]] = []
    packaged: dict[str, str] = {}
    digest_paths: dict[str, str] = {}
    roots = [path.resolve() for path in source_dirs if path.exists()]
    zip_sources: dict[str, tuple[Path, str]] = {}
    for root in roots:
        for source_zip in root.glob("*.zip") if root.is_dir() else []:
            try:
                with zipfile.ZipFile(source_zip, "r") as source_archive:
                    for member in source_archive.namelist():
                        normalized = member.replace("\\", "/").lstrip("/")
                        if not normalized.startswith("assets/") or normalized.endswith("/") or ".." in Path(normalized).parts:
                            continue
                        zip_sources.setdefault(normalized, (source_zip, normalized))
                        zip_sources.setdefault(normalized.removeprefix("assets/"), (source_zip, normalized))
            except zipfile.BadZipFile:
                log.warning("ignoring invalid asset source ZIP: %s", source_zip)

    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        def package_asset_bytes(data: bytes, source_name: str) -> str | None:
            if not data or data.lstrip()[:64].lower().startswith(HTML_PREFIXES):
                return None
            digest = hashlib.sha256(data).hexdigest()
            archive_path = digest_paths.get(digest)
            if archive_path:
                return archive_path
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source_name) or "asset.bin"
            archive_path = f"assets/{digest[:16]}_{safe_name}"
            archive.writestr(archive_path, data)
            digest_paths[digest] = archive_path
            manifest_files.append({
                "path": archive_path.removeprefix("assets/"),
                "archive_path": archive_path,
                "sha256": digest,
                "size": len(data),
                "bytes": len(data),
            })
            return archive_path

        for record in records:
            kept_assets: list[dict[str, Any]] = []
            for asset in record.get("assets") or []:
                raw_path = clean(asset.get("path"))
                local_file = None
                source_zip_entry = zip_sources.get(raw_path) or zip_sources.get(f"assets/{raw_path}")
                for root in roots:
                    candidate = (root / raw_path).resolve()
                    if candidate.is_relative_to(root) and candidate.is_file():
                        local_file = candidate
                        break
                if local_file is not None:
                    data = local_file.read_bytes()
                    source_name = local_file.name
                elif source_zip_entry is not None:
                    source_zip, member = source_zip_entry
                    with zipfile.ZipFile(source_zip, "r") as source_archive:
                        data = source_archive.read(member)
                    source_name = Path(member).name
                    log.debug("recovered packaged asset %s from %s", raw_path, source_zip.name)
                else:
                    if raw_path:
                        raise ValueError(f"asset referenced by records is missing from package sources: {raw_path}")
                    kept_assets.append(asset)
                    continue
                archive_path = package_asset_bytes(data, source_name)
                if not archive_path:
                    continue
                packaged[raw_path] = archive_path
                copied = dict(asset)
                copied["path"] = archive_path.removeprefix("assets/")
                kept_assets.append(copied)
            record["assets"] = kept_assets

        # Preserve every successfully downloaded scraper asset, even if a
        # normalizer could not confidently attach it to one canonical entity.
        # This makes the scraper ZIP a real data+assets artifact instead of a
        # records-only shell. Referenced assets above and these cache assets are
        # deduplicated by SHA-256.
        for source_zip, member in sorted(set(zip_sources.values()), key=lambda item: (str(item[0]), item[1])):
            try:
                with zipfile.ZipFile(source_zip, "r") as source_archive:
                    data = source_archive.read(member)
            except (KeyError, OSError, zipfile.BadZipFile):
                log.warning("could not preserve asset %s from %s", member, source_zip)
                continue
            package_asset_bytes(data, Path(member).name)

        for root in roots:
            cache_file = root / "assets_unique.jsonl"
            if not cache_file.is_file():
                continue
            for line_no, line in enumerate(cache_file.read_text(encoding="utf-8-sig").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    cached = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("ignoring invalid asset cache row %s:%s", cache_file, line_no)
                    continue
                local_path = clean(cached.get("local_path") or cached.get("path"))
                if not local_path:
                    continue
                candidate = (root / local_path).resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    continue
                try:
                    data = candidate.read_bytes()
                except OSError:
                    continue
                package_asset_bytes(data, candidate.name)

        records_payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records)
        records_bytes = records_payload.encode("utf-8")
        archive.writestr("records.jsonl", records_bytes)
        manifest = {
            "format": CONTENT_PACKAGE_FORMAT,
            "format_version": CONTENT_PACKAGE_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": "all",
            "records": len(records),
            "records_sha256": hashlib.sha256(records_bytes).hexdigest(),
            "counts": counts,
            "files": manifest_files,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    os.replace(tmp_zip, zip_path)
    # Write the unpacked JSONL only after local asset paths have been rewritten.
    # The directory and ZIP therefore expose the exact same canonical records.
    for typ, filename in TYPE_FILES.items():
        _write_jsonl(output_dir / filename, (row for row in records if row["type"] == typ))
    _write_jsonl(output_dir / "records.jsonl", records)
    asset_references = sum(len(record.get("assets") or []) for record in records)
    return {
        "records": len(records),
        "counts": counts,
        "asset_references": asset_references,
        "packaged_assets": len(manifest_files),
        "zip": str(zip_path),
        **merge_stats,
    }


def build_from_raw(input_dirs: list[Path], output_dir: Path, source: str, zip_name: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mifp-normalized-") as temporary:
        staging = Path(temporary)
        normalize_all(input_dirs, staging)
        flat_files = [staging / name for name in TYPE_FILES.values()]
        records: list[dict[str, Any]] = []
        for path in flat_files:
            if not path.exists():
                continue
            for raw in read_records([path]):
                if raw.get("meta", {}).get("source") == "unknown":
                    raw["meta"]["source"] = source
                records.append(raw)
        report = write_artifacts(records, output_dir, zip_name, [*input_dirs, staging])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", action="append", type=Path, default=[])
    parser.add_argument("--records", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", choices=("local", "remote", "combined"), required=True)
    parser.add_argument("--zip-name", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)-8s | %(message)s")
    if args.records:
        records = read_records(args.records)
        report = write_artifacts(records, args.output, args.zip_name, args.input_dir)
    else:
        if not args.input_dir:
            parser.error("at least one --input-dir or --records is required")
        report = build_from_raw(args.input_dir, args.output, args.source, args.zip_name)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

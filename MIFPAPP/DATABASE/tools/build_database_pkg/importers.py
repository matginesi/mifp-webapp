#!/usr/bin/env python3
"""Canonical v2 DB import helpers for scraper JSONL records."""

from __future__ import annotations

import os
from datetime import date
from urllib.parse import urlparse

from .assets import add_asset
from .utils import clean, slugify, insert_or_update, infer_country


REVIEW_STATUSES = {"draft", "review", "published", "archived", "quarantined", "duplicate"}
NEWS_INFERRED_DATE_RULE = "scraper_inferred"


def add_member(conn, jsonl_record, members_imported, members_updated):
    data = _record_data(jsonl_record)
    display_name = clean(data.get("display_name") or data.get("name") or data.get("title"))
    if not display_name:
        return members_imported, members_updated
    first, last = _split_name(display_name)
    payload = {
        "slug": _slug(data, display_name),
        "first_name": clean(data.get("first_name") or first),
        "last_name": clean(data.get("last_name") or last),
        "display_name": display_name,
        "affiliation": clean(data.get("affiliation") or data.get("institution")),
        "country": clean(data.get("country") or infer_country(data.get("affiliation") or "")),
        "email": clean(data.get("email")),
        "field": clean(data.get("field") or data.get("research_interests")),
        "bio": clean(data.get("bio") or data.get("biography")),
        "review_status": _review_status(data),
        "is_active": _bool(data.get("is_active"), 1),
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "members", "slug", payload["slug"])
    member_id = insert_or_update(conn, "members", "slug", payload["slug"], payload)
    _link_assets(conn, "member", member_id, data, profile_keys=("photo_url", "image_url", "image"))
    return _count(existed, members_imported, members_updated)


def add_sponsor(conn, jsonl_record, sponsors_imported, sponsors_updated):
    data = _record_data(jsonl_record)
    name = clean(data.get("name") or data.get("title"))
    if not name:
        return sponsors_imported, sponsors_updated
    payload = {
        "slug": _slug(data, name),
        "name": name,
        "description": clean(data.get("description") or data.get("body")),
        "sponsor_type": clean(data.get("sponsor_type") or "sponsor"),
        "tier": clean(data.get("tier") or data.get("level")),
        "is_active": _bool(data.get("is_active"), 1),
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "sponsors", "slug", payload["slug"])
    sponsor_id = insert_or_update(conn, "sponsors", "slug", payload["slug"], payload)
    _replace_entity_links(conn, "sponsor", sponsor_id, _links_from(data, ("website_url", "website", "url", "link")))
    _link_assets(conn, "sponsor", sponsor_id, data, logo_keys=("logo_url", "image_url", "image"))
    return _count(existed, sponsors_imported, sponsors_updated)


def add_event(conn, jsonl_record, events_imported, events_updated):
    data = _record_data(jsonl_record)
    title = clean(data.get("title") or data.get("name"))
    if not title:
        return events_imported, events_updated
    start = clean(data.get("start_date") or data.get("date_start") or data.get("date"))
    end = clean(data.get("end_date") or data.get("date_end"))
    payload = {
        "slug": _slug(data, title),
        "title": title,
        "start_date": start or None,
        "end_date": end or None,
        "date_text": clean(data.get("date_text") or data.get("date_label") or start or end),
        "date_precision": _date_precision(data.get("date_precision"), "day" if start else "unknown"),
        "location": clean(data.get("location")),
        "description": clean(data.get("description") or data.get("body")),
        "event_type": clean(data.get("event_type") or "other"),
        "series_key": clean(data.get("series_key")),
        "parent_event_id": _int_or_none(data.get("parent_event_id")),
        "review_status": _review_status(data),
        "is_featured": 1 if _is_forthcoming(start, end) else 0,
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "events", "slug", payload["slug"])
    event_id = insert_or_update(conn, "events", "slug", payload["slug"], payload)
    _replace_entity_links(conn, "event", event_id, _links_from(data, ("event_url", "url", "link", "external_link", "home_url")))
    _link_assets(conn, "event", event_id, data, cover_keys=("image_url", "cover_url", "logo_url"))
    return _count(existed, events_imported, events_updated)


def add_page(conn, jsonl_record, pages_imported, pages_updated):
    data = _record_data(jsonl_record)
    title = clean(data.get("title") or data.get("name"))
    if not title:
        return pages_imported, pages_updated
    payload = {
        "slug": _slug(data, title),
        "title": title,
        "type": clean(data.get("type") or "custom"),
        "summary": clean(data.get("summary") or data.get("excerpt")),
        "body": clean(data.get("body") or data.get("text") or data.get("content")),
        "version": clean(data.get("version")),
        "effective_date": clean(data.get("effective_date")),
        "nav_group": clean(data.get("nav_group")),
        "menu_order": _int(data.get("menu_order"), 0),
        "review_status": _review_status(data),
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "pages", "slug", payload["slug"])
    page_id = insert_or_update(conn, "pages", "slug", payload["slug"], payload)
    _replace_entity_links(conn, "page", page_id, _links_from(data, ("url", "link", "external_link")))
    _link_assets(conn, "page", page_id, data, document_keys=("pdf_url", "document_url"))
    return _count(existed, pages_imported, pages_updated)


def add_news(conn, jsonl_record, news_imported, news_updated):
    data = _record_data(jsonl_record)
    title = clean(data.get("title") or data.get("name"))
    if not title:
        return news_imported, news_updated
    news_date = clean(data.get("date") or data.get("published_date") or data.get("start_date"))
    payload = {
        "slug": _slug(data, title),
        "title": title,
        "news_type": clean(data.get("news_type") or data.get("category") or "general"),
        "card_layout": clean(data.get("card_layout")),
        "date": news_date or None,
        "date_text": clean(data.get("date_text") or news_date),
        "date_precision": _date_precision(data.get("date_precision"), "day" if news_date else "unknown"),
        "date_is_inferred": _bool(data.get("date_is_inferred"), 0),
        "date_inference_rule": clean(data.get("date_inference_rule")),
        "original_date_text": clean(data.get("original_date_text")),
        "summary": clean(data.get("summary") or data.get("excerpt")),
        "body": clean(data.get("body") or data.get("description") or data.get("text")),
        "review_status": _review_status(data),
        "is_featured": _bool(data.get("is_featured"), 0),
        "source_kind": clean(data.get("source_kind") or data.get("source") or "scraper"),
        "source_priority": _int(data.get("source_priority"), 50),
        "source_order": _int(data.get("source_order"), 0),
        "display_order": _int_or_none(data.get("display_order")),
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "news", "slug", payload["slug"])
    news_id = insert_or_update(conn, "news", "slug", payload["slug"], payload)
    _replace_entity_links(conn, "news", news_id, _links_from(data, ("url", "link", "external_link")))
    _link_assets(conn, "news", news_id, data, cover_keys=("image_url", "cover_url", "image"), document_keys=("pdf_url", "document_url"))
    return _count(existed, news_imported, news_updated)


def add_news_asset(conn, news_id, asset_info, role, sort_order=0):
    url = _asset_url(asset_info)
    if not url:
        return None
    return add_asset(conn, url, role, "news", news_id, role=role, sort_order=sort_order)


def add_publication(conn, jsonl_record, publications_imported, publications_updated):
    data = _record_data(jsonl_record)
    title = clean(data.get("title") or data.get("name"))
    if not title:
        return publications_imported, publications_updated
    payload = {
        "slug": _slug(data, title),
        "title": title,
        "year": _int_or_none(data.get("year")),
        "authors": _join(data.get("authors")),
        "journal": clean(data.get("journal")),
        "doi": clean(data.get("doi")),
        "abstract": clean(data.get("abstract") or data.get("summary")),
        "date_text": clean(data.get("date_text") or data.get("year")),
        "date_precision": _date_precision(data.get("date_precision"), "year"),
        "review_status": _review_status(data),
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "publications", "slug", payload["slug"])
    publication_id = insert_or_update(conn, "publications", "slug", payload["slug"], payload)
    links = _links_from(data, ("external_link", "url", "link"))
    if payload.get("doi"):
        links.append({"url": f"https://doi.org/{payload['doi']}", "role": "doi", "label": "DOI"})
    _replace_entity_links(conn, "publication", publication_id, links)
    _link_assets(conn, "publication", publication_id, data, document_keys=("pdf_url", "document_url", "external_link"))
    return _count(existed, publications_imported, publications_updated)


def add_research_area(conn, jsonl_record, areas_imported, areas_updated):
    data = _record_data(jsonl_record)
    title = clean(data.get("title") or data.get("name"))
    if not title:
        return areas_imported, areas_updated
    payload = {
        "slug": _slug(data, title),
        "title": title,
        "summary": clean(data.get("summary")),
        "description": clean(data.get("description") or data.get("body")),
        "review_status": _review_status(data),
        "sort_order": _int(data.get("sort_order"), 0),
    }
    existed = _exists(conn, "research_areas", "slug", payload["slug"])
    area_id = insert_or_update(conn, "research_areas", "slug", payload["slug"], payload)
    _link_assets(conn, "research_area", area_id, data, cover_keys=("image_url", "cover_url"), document_keys=("pdf_url", "document_url"))
    return _count(existed, areas_imported, areas_updated)


def _record_data(record):
    if isinstance(record.get("data"), dict):
        return {**record["data"], "_links": record.get("links") or [], "_assets": record.get("assets") or []}
    return dict(record)


def _replace_entity_links(conn, entity_type, entity_id, links):
    conn.execute("DELETE FROM entity_links WHERE entity_type=? AND entity_id=?", (entity_type, entity_id))
    for idx, link in enumerate(_dedupe_links(links), start=1):
        conn.execute(
            "INSERT OR IGNORE INTO entity_links(entity_type, entity_id, url, label, role, is_primary, sort_order) VALUES(?,?,?,?,?,?,?)",
            (entity_type, entity_id, link["url"], link.get("label"), link.get("role") or "primary", 1 if idx == 1 else 0, idx),
        )


def _link_assets(conn, entity_type, entity_id, data, cover_keys=(), document_keys=(), logo_keys=(), profile_keys=()):
    if os.environ.get("MIFP_BUILD_DOWNLOAD_ASSETS") != "1":
        return
    specs = []
    for role, keys in (("cover", cover_keys), ("document", document_keys), ("logo", logo_keys), ("profile", profile_keys)):
        for key in keys:
            url = clean(data.get(key))
            if url:
                specs.append((role, url))
    for entry in data.get("documents") or []:
        url = _asset_url(entry)
        if url:
            specs.append(("document", url))
    for entry in data.get("images") or []:
        url = _asset_url(entry)
        if url:
            specs.append(("gallery", url))
    for asset in data.get("_assets") or []:
        if isinstance(asset, dict):
            url = _asset_url(asset)
            role = clean(asset.get("role") or "attachment")
            if url:
                specs.append((role, url))
    for idx, (role, url) in enumerate(_dedupe_asset_specs(specs), start=1):
        add_asset(conn, url, role, entity_type, entity_id, role=role, sort_order=idx)


def _links_from(data, keys):
    links = []
    for key in keys:
        url = _normalize_url(data.get(key))
        if url:
            links.append({"url": url, "role": "primary", "label": None})
    for link in data.get("_links") or data.get("links") or []:
        if isinstance(link, dict):
            url = _normalize_url(link.get("url"))
            if url:
                links.append({"url": url, "role": clean(link.get("role") or "primary"), "label": clean(link.get("label"))})
    return links


def _dedupe_links(links):
    out, seen = [], set()
    for link in links:
        url = _normalize_url(link.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({**link, "url": url})
    return out


def _dedupe_asset_specs(specs):
    out, seen = [], set()
    for role, url in specs:
        if not _looks_like_asset(url):
            continue
        key = (role, url)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _asset_url(value):
    if isinstance(value, dict):
        return clean(value.get("url") or value.get("path"))
    return clean(value)


def _normalize_url(value):
    raw = clean(value)
    if not raw or raw.startswith(("#", "/")):
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.geturl()


def _looks_like_asset(url):
    return bool(url) and url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".doc", ".docx"))


def _review_status(data):
    raw = clean(data.get("review_status") or data.get("status") or "published").lower()
    if raw in {"needs_review", "pending", "candidate"}:
        raw = "review"
    return raw if raw in REVIEW_STATUSES else "published"


def _split_name(name):
    parts = [p for p in str(name).split() if p]
    if len(parts) <= 1:
        return name, ""
    return " ".join(parts[:-1]), parts[-1]


def _slug(data, fallback):
    return slugify(data.get("slug") or fallback)[:160] or "item"


def _exists(conn, table, key, value):
    return conn.execute(f"SELECT id FROM {table} WHERE {key}=?", (value,)).fetchone() is not None


def _count(existed, imported, updated):
    return (imported, updated + 1) if existed else (imported + 1, updated)


def _bool(value, default=0):
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "published"} else 0
    return 1 if value else 0


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _join(value):
    if isinstance(value, list):
        return ", ".join(clean(v) for v in value if clean(v))
    return clean(value)


def _is_forthcoming(start, end):
    value = clean(end or start)
    return bool(value and value[:10] >= date.today().isoformat())


def _date_precision(value, default="unknown"):
    raw = clean(value or default).lower()
    aliases = {"full": "day", "date": "day", "datetime": "day", "exact": "day"}
    raw = aliases.get(raw, raw)
    return raw if raw in {"day", "month", "year", "range", "unknown"} else default

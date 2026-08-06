from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

import bleach
from bleach.css_sanitizer import CSSSanitizer
from markupsafe import escape

from ..config import Config
from .assets import db_asset_file_is_valid, resolve_db_asset_path
from .dashboard_repository import PUBLIC_TABLES

MediaUrl = Callable[[str], str]

PUBLIC_REVIEW_FILTER = "COALESCE(review_status,'draft') = 'published'"
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

NEWS_TYPE_LABELS = {
    "general": "General",
    "announcement": "Announcement",
    "publication_highlight": "Publication",
    "agreement": "Agreement",
    "award": "Award",
    "event_highlight": "Event",
    "institutional": "Institutional",
    "sponsor": "Sponsor",
    "memorial": "Memorial",
    "science_commentary": "Science Commentary",
}


def _media_filename(path: str | None) -> str | None:
    if not path:
        return None
    return path.split("/", 1)[1] if "/" in path else path


def _existing_asset_path(db_path: str | None) -> Path | None:
    if not db_path:
        return None
    candidates = [resolve_db_asset_path(Config.ASSETS_DIR, db_path)]
    filename = _media_filename(db_path)
    if filename:
        candidates.append(Config.ASSETS_DIR / filename)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _local_media_url(path: Path, media_url: MediaUrl) -> str:
    try:
        return media_url(str(path.relative_to(Config.ASSETS_DIR)))
    except ValueError:
        return media_url(path.name)


def _asset_signature_ok(path: Path, kind: str | None) -> bool:
    kind = (kind or "").lower()
    if kind not in {"image", "pdf"}:
        return True
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    if kind == "pdf":
        return head.startswith(b"%PDF")
    return (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith((b"GIF87a", b"GIF89a"))
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
        or head.lstrip().startswith(b"<svg")
        or head.startswith(b"<?xml")
    )


def asset_url(conn, asset_id: int | None, media_url: MediaUrl) -> str | None:
    if not asset_id:
        return None
    asset = conn.execute("SELECT path, kind, is_external, source_url FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not asset:
        return None
    local_path = _existing_asset_path(asset["path"])
    if local_path and _asset_signature_ok(local_path, asset["kind"]):
        return _local_media_url(local_path, media_url)
    if asset["is_external"] and asset["source_url"]:
        return asset["source_url"]
    return None


def document_asset_url(conn, asset_id: int | None, media_url: MediaUrl) -> str | None:
    if not asset_id:
        return None
    asset = conn.execute("SELECT path, kind, mime_type, filename, is_external, source_url FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not asset:
        return None
    local_path = _existing_asset_path(asset["path"])
    if local_path and db_asset_file_is_valid(Config.ASSETS_DIR, asset["path"], kind=asset["kind"], mime_type=asset["mime_type"], filename=asset["filename"]):
        return _local_media_url(local_path, media_url)
    if asset["is_external"] and asset["source_url"]:
        return asset["source_url"]
    return None


def cover_url(conn, row: dict[str, Any], media_url: MediaUrl) -> str | None:
    return asset_url(conn, row.get("cover_asset_id"), media_url)


def _table_columns(conn, table: str) -> set[str]:
    assert table in PUBLIC_TABLES
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _normalize_external_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value or value.startswith(("mailto:", "tel:", "#", "/")):
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return parsed.geturl()


def _normalize_publication_external_url(value: str | None) -> str | None:
    url = _normalize_external_url(value)
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host in {"old.mifp.eu", "www.old.mifp.eu"}:
        if url.lower().split("?", 1)[0].endswith(".pdf"):
            return url
        return None
    return url


def _clean_asset_display_name(*values: str | None, kind: str | None = None) -> str:
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        parsed = urlparse(raw)
        candidate = Path(unquote(parsed.path or raw)).name if parsed.scheme else Path(unquote(raw)).name
        candidate = candidate.split("?", 1)[0].split("#", 1)[0]
        candidate = re.sub(r"^[a-f0-9]{16,}[-_]*", "", candidate, flags=re.I)
        candidate = re.sub(r"^(asset|file)[-_]*", "", candidate, flags=re.I)
        candidate = re.sub(r"[-_]{2,}", "-", candidate)
        candidate = candidate.replace("_", " ").replace("-", " ")
        candidate = re.sub(r"\s+", " ", candidate).strip(" ._-")
        candidate = re.sub(r"\.(pdf|docx?|xlsx?|pptx?)\s+\1$", r".\1", candidate, flags=re.I)
        if candidate and not re.fullmatch(r"[a-f0-9]{16,}(\.\w+)?", candidate, flags=re.I):
            return candidate[:80]
    if (kind or "").lower() == "pdf":
        return "Download PDF"
    if (kind or "").lower() in {"document", "doc"}:
        return "Download document"
    return "Download file"


def _doc_dedupe_key(doc: dict[str, Any]) -> str:
    url = str(doc.get("url") or doc.get("source_url") or "").strip()
    if url:
        parsed = urlparse(url)
        return f"url:{parsed.scheme.lower()}://{parsed.netloc.lower()}{unquote(parsed.path).rstrip('/')}"
    checksum = str(doc.get("checksum") or "").strip()
    if checksum:
        return f"checksum:{checksum}"
    filename = _clean_asset_display_name(doc.get("filename"), doc.get("path"), kind=doc.get("kind")).lower()
    return f"file:{filename}"


def _looks_like_html(value: str | None) -> bool:
    return bool(value and re.search(r"</?[a-z][\s>/]", value, re.I))


_ALLOWED_HTML_TAGS = {
    "p", "br", "b", "i", "u", "em", "strong", "sub", "sup", "span", "div",
    "a", "img", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "hr", "table", "thead", "tbody", "tr", "th", "td", "caption",
}
_ALLOWED_HTML_ATTRS = {
    "a": ("href", "title", "rel", "target"),
    "img": ("src", "alt", "title", "width", "height"),
    "span": ("style",),
    "div": ("style",),
    "td": ("style", "colspan", "rowspan"),
    "th": ("style", "colspan", "rowspan"),
    "*": ("id", "class"),
}


_CSS_SANITIZER = CSSSanitizer(allowed_css_properties={
    "color", "background-color", "font-size", "font-weight", "font-style",
    "text-align", "text-decoration", "padding", "margin", "border",
    "width", "height", "max-width", "max-height",
})


def sanitize_html(html: str) -> str:
    return bleach.clean(html, tags=_ALLOWED_HTML_TAGS, attributes=_ALLOWED_HTML_ATTRS, css_sanitizer=_CSS_SANITIZER, strip=True)


def _plain_text_to_html(value: str | None) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    if _looks_like_html(text):
        return sanitize_html(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    return "\n".join(f"<p>{escape(p)}</p>" for p in paragraphs)


def _normalized_excerpt(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _clean_excerpt(*values: str | None, limit: int = 260) -> str:
    for value in values:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:limit].rstrip()
    return ""


def _dedupe_public_links(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        url = _normalize_external_url(row.get("url"))
        if not url:
            continue
        parsed = urlparse(url)
        key = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{unquote(parsed.path).rstrip('/')}"
        if key in seen:
            continue
        seen.add(key)
        links.append({"url": url, "label": row.get("label") or "Open link"})
    return links


def _summary_is_body_excerpt(summary: str | None, body: str | None) -> bool:
    summary_norm = _normalized_excerpt(summary)
    body_norm = _normalized_excerpt(body)
    if not summary_norm or not body_norm:
        return False
    if body_norm.startswith(summary_norm):
        return True
    probe = summary_norm[: min(len(summary_norm), 140)]
    return len(probe) >= 80 and body_norm.startswith(probe)


def event_entity_links(conn, event_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT url, label, role
        FROM entity_links
        WHERE entity_type='event' AND entity_id=?
        ORDER BY sort_order ASC, id ASC
        """,
        (event_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def event_documents(conn, event_id: int, media_url: MediaUrl) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT al.asset_id, a.filename, a.original_filename, a.path, a.kind, a.source_url, a.caption, a.checksum
        FROM asset_links al
        JOIN assets a ON a.id=al.asset_id
        WHERE al.entity_type='event'
          AND al.entity_id=?
          AND (al.role IN ('document','attachment') OR a.kind IN ('pdf','document'))
        ORDER BY al.sort_order ASC, al.id ASC
        """,
        (event_id,),
    ).fetchall()
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        url = document_asset_url(conn, row["asset_id"], media_url)
        if not url:
            continue
        doc_type = (row["caption"] or "").strip()
        if doc_type not in ("Program", "Proceedings", "Brochure", "Poster"):
            doc_type = ""
        item = {
            "url": url,
            "filename": _clean_asset_display_name(None, row["original_filename"], row["filename"], row["source_url"], row["path"], kind=row["kind"]),
            "doc_type": doc_type,
            "kind": row["kind"],
            "asset_id": row["asset_id"],
            "checksum": row["checksum"],
        }
        key = _doc_dedupe_key(item)
        if key not in seen:
            docs.append(item)
            seen.add(key)
    return docs


def enrich_event(conn, event: dict[str, Any], media_url: MediaUrl) -> dict[str, Any]:
    cover = cover_url(conn, event, media_url)
    if not cover:
        al = conn.execute(
            "SELECT al.asset_id FROM asset_links al "
            "JOIN assets a ON a.id=al.asset_id "
            "WHERE al.entity_type='event' AND al.entity_id=? AND al.role IN ('cover','logo') "
            "LIMIT 1",
            (event["id"],),
        ).fetchone()
        if al:
            cover = asset_url(conn, al["asset_id"], media_url)
    if not cover and event.get("slug"):
        # Imports can contain the event logo before its asset_link is created.
        # An exact URL path segment is a deterministic association (for
        # example /PLMCN-2026/) and avoids guessing from generic filenames.
        slug_segment = f"/{str(event['slug']).strip().lower()}/"
        candidate = conn.execute(
            """
            SELECT id
            FROM assets
            WHERE kind='image'
              AND instr(lower(COALESCE(source_url,'')), ?) > 0
            ORDER BY
              CASE WHEN lower(COALESCE(source_url,'')) LIKE '%logo%' THEN 0 ELSE 1 END,
              id ASC
            LIMIT 1
            """,
            (slug_segment,),
        ).fetchone()
        if candidate:
            cover = asset_url(conn, candidate["id"], media_url)
    event["cover_url"] = cover
    event["entity_links"] = event_entity_links(conn, event["id"])
    event["documents"] = event_documents(conn, event["id"], media_url)
    event["description_html"] = _plain_text_to_html(event.get("description"))
    return event


def news_asset_info(conn, news_row: dict[str, Any], media_url: MediaUrl) -> dict[str, Any]:
    cover = cover_url(conn, news_row, media_url)
    entity_id = news_row["id"]

    if not cover:
        al = conn.execute(
            """
            SELECT al.asset_id
            FROM asset_links al
            JOIN assets a ON a.id=al.asset_id
            WHERE al.entity_type='news' AND al.entity_id=? AND al.role='cover'
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        if al:
            cover = asset_url(conn, al["asset_id"], media_url)

    gallery_rows = conn.execute(
        """
        SELECT al.asset_id
        FROM asset_links al
        JOIN assets a ON a.id=al.asset_id
        WHERE al.entity_type='news' AND al.entity_id=? AND al.role='gallery'
        ORDER BY al.sort_order ASC, al.id ASC
        """,
        (entity_id,),
    ).fetchall()
    gallery_images = [url for r in gallery_rows if (url := asset_url(conn, r["asset_id"], media_url))]

    doc_rows = conn.execute(
        """
        SELECT al.asset_id, a.filename, a.original_filename, a.path, a.kind, a.source_url, a.is_external, a.caption, a.checksum
        FROM asset_links al
        JOIN assets a ON a.id=al.asset_id
        WHERE al.entity_type='news' AND al.entity_id=? AND al.role='document'
        ORDER BY al.sort_order ASC, al.id ASC
        """,
        (entity_id,),
    ).fetchall()
    documents = []
    seen_docs: set[str] = set()
    for doc in doc_rows:
        doc_url = document_asset_url(conn, doc["asset_id"], media_url)
        if doc_url:
            item = {
                "url": doc_url,
                "filename": _clean_asset_display_name(doc["caption"], doc["original_filename"], doc["filename"], doc["source_url"], doc["path"], kind=doc["kind"]),
                "kind": doc["kind"],
                "asset_id": doc["asset_id"],
                "checksum": doc["checksum"],
            }
            item["related_publications"] = [dict(row) for row in conn.execute(
                """
                SELECT DISTINCT p.title, p.slug, p.year, p.doi
                FROM asset_links al
                JOIN publications p ON p.id=al.entity_id
                WHERE al.asset_id=? AND al.entity_type='publication'
                  AND p.review_status='published'
                ORDER BY p.year DESC, p.title
                """,
                (doc["asset_id"],),
            ).fetchall()]
            key = _doc_dedupe_key(item)
            if key not in seen_docs:
                documents.append(item)
                seen_docs.add(key)

    link_rows = [dict(r) for r in conn.execute(
        """
        SELECT url, label
        FROM entity_links
        WHERE entity_type='news' AND entity_id=? AND COALESCE(url,'') != ''
        ORDER BY sort_order ASC, id ASC
        """,
        (entity_id,),
    ).fetchall()]
    return {
        "cover_url": cover,
        "gallery_images": gallery_images,
        "documents": documents,
        "external_links": _dedupe_public_links(link_rows),
    }


def enrich_news(conn, row: dict[str, Any], media_url: MediaUrl) -> dict[str, Any]:
    info = news_asset_info(conn, row, media_url)
    row["cover_url"] = info["cover_url"]
    row["gallery_images"] = info["gallery_images"]
    row["documents"] = info["documents"]
    row["external_links"] = info["external_links"]
    row["primary_image"] = info["cover_url"] or (info["gallery_images"][0] if info["gallery_images"] else None)
    row["excerpt"] = _clean_excerpt(
        row.get("summary") if not _summary_is_body_excerpt(row.get("summary"), row.get("body")) else None,
        row.get("body"),
    )
    badges: list[str] = []
    if row.get("news_type"):
        badges.append(NEWS_TYPE_LABELS.get(str(row.get("news_type")), str(row.get("news_type")).replace("_", " ").title()))
    if info["documents"]:
        badges.append("PDF" if len(info["documents"]) == 1 else f"{len(info['documents'])} documents")
    if row["primary_image"]:
        badges.append("Image")
    if info["external_links"]:
        badges.append("Link")
    row["badges"] = badges[:4]
    layout = row.get("card_layout") or ""
    if not layout:
        has_cover = bool(info["cover_url"])
        gallery_count = len(info["gallery_images"])
        if gallery_count > 1 and has_cover:
            layout = "text_gallery"
        elif has_cover:
            layout = "text_image"
        else:
            layout = "text"
    row["effective_layout"] = layout
    row["body_html"] = _plain_text_to_html(row.get("body"))
    row["show_summary_block"] = bool(row.get("summary")) and not _summary_is_body_excerpt(row.get("summary"), row.get("body"))
    return row


def list_forthcoming_events(conn, media_url: MediaUrl, limit: int = 6) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT *
            FROM events
            WHERE COALESCE(is_featured,0)=1
              AND {PUBLIC_REVIEW_FILTER}
            ORDER BY COALESCE(start_date,end_date,'9999-99-99') ASC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    return [enrich_event(conn, row, media_url) for row in rows]


def list_public_events(conn, media_url: MediaUrl) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT * FROM events
            WHERE {PUBLIC_REVIEW_FILTER}
            ORDER BY COALESCE(start_date,end_date,'0000-00-00') DESC, id DESC
            """
        ).fetchall()
    ]
    today = date.today().isoformat()
    upcoming = [
        r for r in rows
        if str(r.get("end_date") or r.get("start_date") or "") >= today
        or r.get("is_featured")
    ]
    past = [r for r in rows if r not in upcoming]
    return (
        [enrich_event(conn, r, media_url) for r in upcoming],
        [enrich_event(conn, r, media_url) for r in past],
    )


def get_public_event(conn, slug: str, media_url: MediaUrl) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM events WHERE slug=? AND {PUBLIC_REVIEW_FILTER}",
        (slug,),
    ).fetchone()
    return enrich_event(conn, dict(row), media_url) if row else None


def list_recent_news(conn, media_url: MediaUrl, limit: int = 10) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT *
            FROM news
            WHERE {PUBLIC_REVIEW_FILTER}
            ORDER BY
              COALESCE(date, date_text, '0000-00-00') DESC,
              CASE WHEN display_order IS NOT NULL THEN 0 ELSE 1 END,
              COALESCE(display_order, 0),
              source_priority ASC,
              sort_order ASC,
              id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    return [enrich_news(conn, row, media_url) for row in rows]


def list_news_page(conn, media_url: MediaUrl, news_type: str | None, search: str | None, page: int, per_page: int, *, year: str | None = None, content_filter: str | None = None) -> dict[str, Any]:
    types = sorted({
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT news_type FROM news WHERE {PUBLIC_REVIEW_FILTER} AND news_type IS NOT NULL"
        ).fetchall()
    })
    years = [
        str(r[0])
        for r in conn.execute(
            f"""
            SELECT DISTINCT substr(COALESCE(date,date_text),1,4) AS year
            FROM news
            WHERE {PUBLIC_REVIEW_FILTER}
              AND year GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY year DESC
            """
        ).fetchall()
    ]
    where_parts = [PUBLIC_REVIEW_FILTER]
    params: list[Any] = []
    if news_type:
        where_parts.append("news_type=?")
        params.append(news_type)
    if search:
        where_parts.append("(title LIKE ? OR summary LIKE ? OR body LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    if year and re.fullmatch(r"\d{4}", year):
        where_parts.append("substr(COALESCE(date,date_text),1,4)=?")
        params.append(year)
    if content_filter == "pdf":
        where_parts.append("""
            (
              EXISTS (
                SELECT 1 FROM asset_links al JOIN assets a ON a.id=al.asset_id
                WHERE al.entity_type='news' AND al.entity_id=news.id
                  AND (al.role='document' OR a.kind IN ('pdf','document') OR a.mime_type='application/pdf' OR lower(a.filename) LIKE '%.pdf' OR lower(a.path) LIKE '%.pdf')
              )
            )
        """)
    elif content_filter == "image":
        where_parts.append("""
            (
              EXISTS (
                SELECT 1 FROM asset_links al JOIN assets a ON a.id=al.asset_id
                WHERE al.entity_type='news' AND al.entity_id=news.id
                  AND (al.role IN ('cover','gallery') OR a.kind='image')
              )
            )
        """)
    elif content_filter == "link":
        where_parts.append("""
            (
              EXISTS (
                SELECT 1 FROM entity_links el
                WHERE el.entity_type='news' AND el.entity_id=news.id AND COALESCE(el.url,'') != ''
              )
            )
        """)
    elif content_filter == "event":
        where_parts.append("news_type='event_highlight'")
    where_clause = " AND ".join(where_parts)
    total = conn.execute(f"SELECT COUNT(*) FROM news WHERE {where_clause}", params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT *
            FROM news
            WHERE {where_clause}
            ORDER BY
              COALESCE(date, date_text, '0000-00-00') DESC,
              date_is_inferred ASC,
              CASE WHEN display_order IS NOT NULL THEN 0 ELSE 1 END,
              COALESCE(display_order, 0),
              source_priority ASC,
              sort_order ASC,
              id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, offset),
        ).fetchall()
    ]
    return {
        "news": [enrich_news(conn, row, media_url) for row in rows],
        "types": types,
        "years": years,
        "total": total,
        "total_pages": total_pages,
    }


def get_public_news(conn, slug: str, media_url: MediaUrl) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM news WHERE slug=? AND {PUBLIC_REVIEW_FILTER}",
        (slug,),
    ).fetchone()
    return enrich_news(conn, dict(row), media_url) if row else None


def get_public_page(conn, page_type: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT * FROM pages
        WHERE type=? AND {PUBLIC_REVIEW_FILTER}
        ORDER BY
          length(COALESCE(body,'')) DESC,
          updated_at DESC,
          id DESC
        LIMIT 1
        """,
        (page_type,),
    ).fetchone()
    return dict(row) if row else None


def get_public_page_by_slug(conn, slug: str) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT *
        FROM pages
        WHERE slug=? AND {PUBLIC_REVIEW_FILTER}
        ORDER BY
          length(COALESCE(body,'')) DESC,
          updated_at DESC,
          id DESC
        LIMIT 1
        """,
        (slug,),
    ).fetchone()
    return dict(row) if row else None


def get_sponsor_how_to_page(conn) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT *
        FROM pages
        WHERE {PUBLIC_REVIEW_FILTER}
          AND (
            slug IN ('sponsors-how-to', 'how-to-become-a-sponsor')
            OR lower(title) LIKE '%become a sponsor%'
          )
        ORDER BY
          length(COALESCE(body,'')) DESC,
          updated_at DESC,
          id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def list_home_sponsors(conn, media_url: MediaUrl) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT DISTINCT s.id, s.*
            FROM sponsors s
            WHERE s.is_active=1
            ORDER BY s.sort_order ASC, s.name ASC
            """
        ).fetchall()
    ]
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for sponsor in rows:
        sid = sponsor.get("id")
        if sid is not None and sid in seen:
            continue
        if sid is not None:
            seen.add(sid)
        link = conn.execute(
            """
            SELECT url FROM entity_links
            WHERE entity_type='sponsor' AND entity_id=? AND role IN ('primary','website')
            ORDER BY is_primary DESC, sort_order ASC, id ASC
            LIMIT 1
            """,
            (sid,),
        ).fetchone()
        logo = conn.execute(
            """
            SELECT asset_id FROM asset_links
            WHERE entity_type='sponsor' AND entity_id=? AND role='logo'
            ORDER BY is_primary DESC, sort_order ASC, id ASC
            LIMIT 1
            """,
            (sid,),
        ).fetchone()
        sponsor["website_url"] = _normalize_external_url(link["url"] if link else None)
        sponsor["logo_url"] = asset_url(conn, logo["asset_id"], media_url) if logo else None
        if sponsor.get("body"):
            sponsor["body"] = sanitize_html(sponsor["body"])
        deduped.append(sponsor)
    return deduped


def get_public_sponsor(conn, slug: str, media_url: MediaUrl) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.*
        FROM sponsors s
        WHERE s.slug = ? AND s.is_active = 1
        """,
        (slug,),
    ).fetchone()
    if not row:
        return None
    sponsor = dict(row)
    if sponsor.get("body"):
        sponsor["body"] = sanitize_html(sponsor["body"])
    link = conn.execute(
        """
        SELECT url FROM entity_links
        WHERE entity_type='sponsor' AND entity_id=? AND role IN ('primary','website')
        ORDER BY is_primary DESC, sort_order ASC, id ASC
        LIMIT 1
        """,
        (sponsor["id"],),
    ).fetchone()
    logo = conn.execute(
        """
        SELECT asset_id FROM asset_links
        WHERE entity_type='sponsor' AND entity_id=? AND role='logo'
        ORDER BY is_primary DESC, sort_order ASC, id ASC
        LIMIT 1
        """,
        (sponsor["id"],),
    ).fetchone()
    sponsor["website_url"] = _normalize_external_url(link["url"] if link else None)
    sponsor["logo_url"] = asset_url(conn, logo["asset_id"], media_url) if logo else None
    sponsor["linked_events"] = [
        enrich_event(conn, dict(r), media_url)
        for r in conn.execute(
            f"""
            SELECT e.*
            FROM events e
            JOIN entity_relations er
              ON er.source_type='sponsor' AND er.source_id=? AND er.target_type='event' AND er.target_id=e.id
            WHERE {PUBLIC_REVIEW_FILTER}
            ORDER BY COALESCE(e.start_date,e.end_date,'0000-00-00') DESC, e.id DESC
            """,
            (sponsor["id"],),
        ).fetchall()
    ]
    return sponsor


def get_home_context(conn, media_url: MediaUrl) -> dict[str, Any]:
    settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {
        "forthcoming_events": list_forthcoming_events(conn, media_url),
        "news": list_recent_news(conn, media_url, limit=6),
        "manifesto": get_public_page(conn, "manifesto"),
        "total_events": int(conn.execute(f"SELECT COUNT(*) FROM events WHERE {PUBLIC_REVIEW_FILTER}").fetchone()[0] or 0),
        "total_news": int(conn.execute(f"SELECT COUNT(*) FROM news WHERE {PUBLIC_REVIEW_FILTER}").fetchone()[0] or 0),
        "member_count": int(conn.execute("SELECT COUNT(*) FROM members WHERE is_active=1").fetchone()[0] or 0),
        "country_count": int(conn.execute("SELECT COUNT(DISTINCT country) FROM members WHERE is_active=1 AND country IS NOT NULL AND country != ''").fetchone()[0] or 0),
        "sponsors": list_home_sponsors(conn, media_url),
        "sponsor_how_to": get_sponsor_how_to_page(conn),
        "site_settings": {r["key"]: r["value"] for r in settings_rows},
    }


def list_member_roles(conn) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT r.id, r.name, r.label, COUNT(m.id) AS member_count
            FROM roles r
            LEFT JOIN members m ON m.role_id=r.id AND m.is_active=1
            GROUP BY r.id
            ORDER BY r.label ASC
            """
        ).fetchall()
    ]


def list_members_page(conn, media_url: MediaUrl, search: str | None, role_filter: str | None, page: int, per_page: int) -> dict[str, Any]:
    where_parts = ["m.is_active=1"]
    params: list[Any] = []
    if search:
        where_parts.append("(m.display_name LIKE ? OR m.first_name LIKE ? OR m.last_name LIKE ? OR m.affiliation LIKE ? OR m.country LIKE ? OR m.field LIKE ?)")
        params.extend([f"%{search}%"] * 6)
    if role_filter:
        where_parts.append("r.name=?")
        params.append(role_filter)
    where_clause = " AND ".join(where_parts)
    total = conn.execute(
        f"SELECT COUNT(*) FROM members m LEFT JOIN roles r ON m.role_id=r.id WHERE {where_clause}",
        params,
    ).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT m.*, r.label AS role_label, r.name AS role_name
            FROM members m
            LEFT JOIN roles r ON m.role_id=r.id
            WHERE {where_clause}
            ORDER BY m.sort_order ASC, m.last_name ASC, m.first_name ASC
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, offset),
        ).fetchall()
    ]
    for member in rows:
        member["image_url"] = None
        if not member["image_url"]:
            al = conn.execute(
                """
                SELECT al.asset_id
                FROM asset_links al
                JOIN assets a ON a.id=al.asset_id
                WHERE al.entity_type='member' AND al.entity_id=? AND al.role IN ('profile','cover','gallery')
                LIMIT 1
                """,
                (member["id"],),
            ).fetchone()
            member["image_url"] = asset_url(conn, al["asset_id"], media_url) if al else None
    return {"members": rows, "roles": list_member_roles(conn), "total": total, "total_pages": total_pages}


def list_public_research(conn, media_url: MediaUrl) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT *
            FROM research_areas
            WHERE {PUBLIC_REVIEW_FILTER}
            ORDER BY sort_order ASC, title ASC
            """
        ).fetchall()
    ]
    for area in rows:
        al = conn.execute(
            "SELECT asset_id FROM asset_links WHERE entity_type='research_area' AND entity_id=? AND role='cover' ORDER BY is_primary DESC, sort_order ASC LIMIT 1",
            (area["id"],),
        ).fetchone()
        cover = asset_url(conn, al["asset_id"], media_url) if al else None
        area["cover_url"] = cover
        doc_row = conn.execute(
            "SELECT asset_id FROM asset_links WHERE entity_type='research_area' AND entity_id=? AND role='document' ORDER BY is_primary DESC, sort_order ASC LIMIT 1",
            (area["id"],),
        ).fetchone()
        doc = document_asset_url(conn, doc_row["asset_id"], media_url) if doc_row else None
        area["document_url"] = doc
        link_rows = conn.execute(
            """
            SELECT url, label, role, is_primary
            FROM entity_links
            WHERE entity_type='research_area' AND entity_id=?
            ORDER BY is_primary DESC, sort_order ASC, id ASC
            """,
            (area["id"],),
        ).fetchall()
        area["external_links"] = _dedupe_public_links(link_rows)
        if area.get("description"):
            area["description"] = sanitize_html(area["description"])
        if area.get("summary"):
            area["summary"] = sanitize_html(area["summary"])
    return rows


PUBLICATION_TITLE_BLACKLIST = frozenset({
    "mifp publications", "members' publications", "publications", "atom", "rss",
    "privacy policy", "mifp publications76c3", "publications5a94", "publications76c3",
    "publications795a",
})


def list_public_publications(conn, media_url: MediaUrl) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT *
            FROM publications
            WHERE {PUBLIC_REVIEW_FILTER}
            ORDER BY COALESCE(year,0) DESC, id DESC
            """
        ).fetchall()
    ]
    rows = [r for r in rows if r.get("title", "").lower().strip() not in PUBLICATION_TITLE_BLACKLIST]
    for pub in rows:
        doc_row = conn.execute(
            "SELECT asset_id FROM asset_links WHERE entity_type='publication' AND entity_id=? AND role='document' ORDER BY is_primary DESC, sort_order ASC LIMIT 1",
            (pub["id"],),
        ).fetchone()
        cover = document_asset_url(conn, doc_row["asset_id"], media_url) if doc_row else None
        pub["document_url"] = cover
        pub["authors_list"] = [a.strip() for a in (pub.get("authors") or "").split(",") if a.strip()]
        pub["doi"] = pub.get("doi")
        link_row = conn.execute(
            """
            SELECT url FROM entity_links
            WHERE entity_type='publication' AND entity_id=?
            ORDER BY is_primary DESC, sort_order ASC, id ASC
            LIMIT 1
            """,
            (pub["id"],),
        ).fetchone()
        pub["external_link"] = _normalize_publication_external_url(link_row["url"] if link_row else None)
        pub["source_url"] = None
        if pub["document_url"]:
            pub["download_url"] = pub["document_url"]
            pub["download_label"] = "PDF"
        elif pub["external_link"]:
            pub["download_url"] = pub["external_link"]
            pub["download_label"] = "PDF" if pub["external_link"].lower().endswith(".pdf") else "View"
        elif pub["doi"]:
            pub["download_url"] = f"https://doi.org/{pub['doi']}"
            pub["download_label"] = "DOI"
        else:
            pub["download_url"] = None
            pub["download_label"] = None
    return rows


def sitemap_dynamic_entries(conn) -> list[dict[str, Any]]:
    events = [
        {"kind": "event", "slug": row["slug"], "lastmod": row["updated_at"]}
        for row in conn.execute(f"SELECT slug, updated_at FROM events WHERE {PUBLIC_REVIEW_FILTER}").fetchall()
    ]
    news = [
        {"kind": "news", "slug": row["slug"], "lastmod": row["date"] or row["updated_at"]}
        for row in conn.execute(f"SELECT slug, date, updated_at FROM news WHERE {PUBLIC_REVIEW_FILTER}").fetchall()
    ]
    return events + news

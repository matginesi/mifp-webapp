from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from flask import request, url_for


DEFAULT_META_DESCRIPTION = (
    "Mediterranean Institute of Fundamental Physics — Promoting scientific research "
    "and international collaboration across the Mediterranean."
)

SEO_SETTING_DEFAULTS: dict[str, str] = {
    "seo_indexing_enabled": "1",
    "seo_canonical_origin": "",
    "seo_google_site_verification": "",
    "seo_default_description": DEFAULT_META_DESCRIPTION,
}

SEO_SETTING_KEYS = frozenset(SEO_SETTING_DEFAULTS)


@dataclass(frozen=True)
class SeoIssue:
    severity: str
    code: str
    label: str
    detail: str
    action_url: str | None = None


def normalize_origin(value: str | None) -> str:
    """Return an origin-only http(s) URL, or an empty string when invalid."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def settings_from_conn(conn) -> dict[str, str]:
    values = dict(SEO_SETTING_DEFAULTS)
    rows = conn.execute(
        "SELECT key,value FROM settings WHERE key IN (?,?,?,?)",
        tuple(sorted(SEO_SETTING_KEYS)),
    ).fetchall()
    values.update({str(row["key"]): str(row["value"] or "") for row in rows})
    return values


def preferred_origin(settings: dict[str, str] | None = None) -> str:
    configured = normalize_origin((settings or {}).get("seo_canonical_origin"))
    if configured:
        return configured

    # Fall back to the live request, but prefer the apex host when both apex and
    # www reach Flask. Production Caddy also permanently redirects www -> apex.
    scheme = request.scheme or "https"
    host = request.host
    if host.lower().startswith("www."):
        host = host[4:]
    return f"{scheme}://{host}".rstrip("/")


def _current_canonical_path() -> str:
    endpoint = request.endpoint
    if endpoint:
        try:
            return url_for(endpoint, **(request.view_args or {}))
        except Exception:
            pass
    return request.path or "/"


def canonical_url(settings: dict[str, str] | None = None) -> str:
    return preferred_origin(settings) + _current_canonical_path()


def absolute_url(path: str, settings: dict[str, str] | None = None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return preferred_origin(settings) + "/"
    parts = urlsplit(raw)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return raw
    if not raw.startswith("/"):
        raw = "/" + raw
    return preferred_origin(settings) + raw


def robots_directive(settings: dict[str, str] | None = None) -> str:
    values = settings or {}
    if str(values.get("seo_indexing_enabled", "1")) != "1":
        return "noindex,nofollow"
    # Internal search/faceted URL variants should be crawlable through their
    # links but should not become duplicate index entries. Their canonical link
    # points to the clean route without the query string.
    if request.endpoint == "public.search" or bool(request.args):
        return "noindex,follow"
    return "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"


def _date_only(value: object) -> str | None:
    raw = str(value or "").strip()
    return raw[:10] if len(raw) >= 10 else None


def _max_timestamp(conn, query: str, params: tuple = ()) -> str | None:
    row = conn.execute(query, params).fetchone()
    if not row:
        return None
    return _date_only(row[0])


def _newest(*values: str | None) -> str | None:
    candidates = [value for value in values if value]
    return max(candidates) if candidates else None


def _settings_lastmod(conn, keys: tuple[str, ...]) -> str | None:
    if not keys:
        return None
    placeholders = ",".join("?" for _ in keys)
    return _max_timestamp(
        conn,
        f"SELECT MAX(updated_at) FROM settings WHERE key IN ({placeholders})",
        keys,
    )


def _static_lastmods(conn) -> dict[str, str | None]:
    members_updated = _max_timestamp(conn, "SELECT MAX(updated_at) FROM members WHERE is_active=1")
    events_updated = _max_timestamp(
        conn,
        "SELECT MAX(e.updated_at) FROM events e WHERE COALESCE(e.review_status,'draft')='published'",
    )
    archive_updated = _max_timestamp(
        conn,
        "SELECT MAX(CASE WHEN a.updated_at > e.updated_at THEN a.updated_at ELSE e.updated_at END) "
        "FROM event_archive_entries a JOIN events e ON e.id=a.event_id "
        "WHERE COALESCE(e.review_status,'draft')='published'",
    )
    news_updated = _max_timestamp(
        conn,
        "SELECT MAX(updated_at) FROM news WHERE COALESCE(review_status,'draft')='published'",
    )
    publications_updated = _max_timestamp(
        conn,
        "SELECT MAX(updated_at) FROM publications WHERE COALESCE(review_status,'draft')='published'",
    )
    research_updated = _max_timestamp(
        conn,
        "SELECT MAX(updated_at) FROM research_areas WHERE COALESCE(review_status,'draft')='published'",
    )
    sponsors_updated = _max_timestamp(conn, "SELECT MAX(updated_at) FROM sponsors WHERE is_active=1")

    page_updates = {
        str(row["type"]): _date_only(row["lastmod"])
        for row in conn.execute(
            "SELECT type,MAX(updated_at) AS lastmod FROM pages "
            "WHERE COALESCE(review_status,'draft')='published' GROUP BY type"
        ).fetchall()
    }
    sponsor_how_to = _max_timestamp(
        conn,
        "SELECT MAX(updated_at) FROM pages WHERE COALESCE(review_status,'draft')='published' "
        "AND (slug IN ('sponsors-how-to','how-to-become-a-sponsor') OR lower(title) LIKE '%become a sponsor%')",
    )

    home_copy_updated = _settings_lastmod(
        conn,
        (
            "hero_eyebrow", "hero_lead", "copy.home_primary_cta", "copy.home_secondary_cta",
            "copy.home_news_link", "copy.home_motto_latin", "copy.home_motto_translation",
            "events_eyebrow", "events_section_title", "events_section_subtitle",
            "news_eyebrow", "news_section_title", "news_section_subtitle",
        ),
    )
    members_copy_updated = _settings_lastmod(
        conn, ("copy.members_title", "copy.members_intro", "copy.members_search", "copy.members_empty")
    )
    events_copy_updated = _settings_lastmod(
        conn, ("copy.events_title", "copy.events_intro", "copy.events_upcoming", "copy.events_past", "copy.events_empty")
    )
    news_copy_updated = _settings_lastmod(
        conn, ("copy.news_title", "copy.news_intro", "copy.news_search", "copy.news_empty")
    )
    publications_copy_updated = _settings_lastmod(
        conn, ("copy.publications_title", "copy.publications_intro", "copy.publications_search", "copy.publications_empty")
    )
    research_copy_updated = _settings_lastmod(
        conn, ("copy.research_title", "copy.research_intro", "copy.research_pdf", "copy.research_empty")
    )
    sponsors_copy_updated = _settings_lastmod(
        conn, ("copy.sponsors_title", "copy.sponsors_intro", "copy.sponsors_empty", "copy.sponsors_cta_title", "copy.sponsors_cta_button")
    )

    home_updated = _newest(
        home_copy_updated, members_updated, events_updated, news_updated,
        publications_updated, research_updated, sponsors_updated, *(page_updates.values()),
    )
    about_dynamic_updated = _newest(
        page_updates.get("about"), members_updated, events_updated, news_updated,
        publications_updated, research_updated, sponsors_updated,
    )
    return {
        "public.home": home_updated,
        "public.events": _newest(events_updated, events_copy_updated),
        "public.archive": archive_updated,
        "public.news": _newest(news_updated, news_copy_updated),
        "public.publications": _newest(publications_updated, publications_copy_updated),
        "public.research": _newest(research_updated, research_copy_updated),
        "public.about": about_dynamic_updated,
        "public.privacy": page_updates.get("privacy"),
        "public.cookie_policy": page_updates.get("cookie_policy"),
        "public.manifesto": page_updates.get("manifesto"),
        "public.members": _newest(members_updated, members_copy_updated),
        "public.code_of_conduct": page_updates.get("code_of_conduct"),
        "public.sponsors": _newest(sponsors_updated, sponsors_copy_updated),
        "public.sponsor_how_to": sponsor_how_to,
    }


def build_sitemap_entries(conn, origin: str) -> list[dict[str, str | None]]:
    """Build the complete public sitemap from the canonical database state."""
    clean_origin = normalize_origin(origin) or origin.rstrip("/")
    static_endpoints = (
        "public.home",
        "public.events",
        "public.archive",
        "public.news",
        "public.publications",
        "public.research",
        "public.about",
        "public.privacy",
        "public.cookie_policy",
        "public.manifesto",
        "public.members",
        "public.code_of_conduct",
        "public.sponsors",
        "public.sponsor_how_to",
    )
    lastmods = _static_lastmods(conn)
    entries: list[dict[str, str | None]] = [
        {
            "kind": "static",
            "loc": clean_origin + url_for(endpoint),
            "lastmod": lastmods.get(endpoint),
        }
        for endpoint in static_endpoints
    ]

    for row in conn.execute(
        "SELECT e.slug,e.updated_at,a.category AS archive_category,a.archive_year,a.updated_at AS archive_updated_at "
        "FROM events e LEFT JOIN event_archive_entries a ON a.event_id=e.id "
        "WHERE COALESCE(e.review_status,'draft')='published' AND COALESCE(e.slug,'')<>''"
    ).fetchall():
        if row["archive_category"] and row["archive_year"]:
            path = url_for(
                "public.archive_detail",
                category=row["archive_category"],
                year=int(row["archive_year"]),
                slug=row["slug"],
            )
        else:
            path = url_for("public.event_detail", slug=row["slug"])
        entries.append(
            {
                "kind": "event",
                "loc": clean_origin + path,
                "lastmod": _newest(_date_only(row["updated_at"]), _date_only(row["archive_updated_at"])),
            }
        )

    for row in conn.execute(
        "SELECT slug,updated_at FROM news "
        "WHERE COALESCE(review_status,'draft')='published' AND COALESCE(slug,'')<>''"
    ).fetchall():
        entries.append(
            {
                "kind": "news",
                "loc": clean_origin + url_for("public.news_detail", slug=row["slug"]),
                "lastmod": _date_only(row["updated_at"]),
            }
        )

    for row in conn.execute(
        "SELECT slug,updated_at FROM sponsors WHERE is_active=1 AND COALESCE(slug,'')<>''"
    ).fetchall():
        entries.append(
            {
                "kind": "sponsor",
                "loc": clean_origin + url_for("public.sponsor_detail", slug=row["slug"]),
                "lastmod": _date_only(row["updated_at"]),
            }
        )

    # Preserve route order for the static surface, then keep generated URLs
    # deterministic for stable ETags and easy diffing in Search Console.
    static = [entry for entry in entries if entry["kind"] == "static"]
    dynamic = sorted(
        (entry for entry in entries if entry["kind"] != "static"),
        key=lambda entry: str(entry["loc"]),
    )
    return static + dynamic


def seo_dashboard_snapshot(conn, settings: dict[str, str], origin: str) -> dict:
    entries = build_sitemap_entries(conn, origin)
    issues: list[SeoIssue] = []

    if not normalize_origin(settings.get("seo_canonical_origin")):
        issues.append(
            SeoIssue(
                "warning",
                "canonical_origin_fallback",
                "Canonical origin is using the request host",
                "Set the production origin (normally https://mifp.eu) so canonical, sitemap and structured-data URLs stay stable behind every proxy.",
            )
        )
    if settings.get("seo_indexing_enabled", "1") != "1":
        issues.append(
            SeoIssue(
                "error",
                "indexing_disabled",
                "Public indexing is disabled",
                "Public pages emit noindex,nofollow while crawling remains allowed so search engines can observe the rule.",
            )
        )

    checks = (
        (
            "news",
            "Published news missing SEO summary",
            "SELECT COUNT(*) FROM news WHERE COALESCE(review_status,'draft')='published' "
            "AND (COALESCE(slug,'')='' OR COALESCE(title,'')='' OR length(trim(COALESCE(summary,'')))<40)",
            "News items should have a stable slug, title and useful summary for snippets/social previews.",
            "/dashboard/content/news",
        ),
        (
            "events",
            "Published events missing basic Event metadata",
            "SELECT COUNT(*) FROM events WHERE COALESCE(review_status,'draft')='published' AND "
            "(COALESCE(slug,'')='' OR COALESCE(title,'')='' OR COALESCE(start_date,'')='' OR "
            "length(trim(COALESCE(description,'')))<40 OR COALESCE(location,'')='')",
            "Event pages should have a stable slug, start date, useful description and venue/location text.",
            "/dashboard/events",
        ),
        (
            "sponsors",
            "Active sponsors missing descriptive metadata",
            "SELECT COUNT(*) FROM sponsors WHERE is_active=1 AND "
            "(COALESCE(slug,'')='' OR COALESCE(name,'')='' OR length(trim(COALESCE(description,'')))<30)",
            "Sponsor detail pages should have a stable slug and a useful organization description.",
            "/dashboard/content/sponsors",
        ),
    )
    diagnostic_counts: dict[str, int] = {}
    for code, label, sql, detail, action_url in checks:
        count = int(conn.execute(sql).fetchone()[0] or 0)
        diagnostic_counts[code] = count
        if count:
            issues.append(SeoIssue("warning", code, f"{label}: {count}", detail, action_url))

    published_event_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM events WHERE COALESCE(review_status,'draft')='published'"
        ).fetchone()[0]
        or 0
    )
    if published_event_count:
        issues.append(
            SeoIssue(
                "info",
                "event_postal_address_model",
                "Event rich-result addresses are not structured yet",
                "MIFP currently stores a free-text event location. Google Event rich results require a Place with a structured PostalAddress, so the markup remains useful Schema.org data but full Event rich-result eligibility needs address fields in the event model.",
                "/dashboard/events",
            )
        )

    latest = max((entry["lastmod"] for entry in entries if entry.get("lastmod")), default=None)
    return {
        "entries": entries,
        "url_count": len(entries),
        "latest_lastmod": latest,
        "issues": issues,
        "diagnostic_counts": diagnostic_counts,
        "indexing_enabled": settings.get("seo_indexing_enabled", "1") == "1",
        "canonical_origin": normalize_origin(settings.get("seo_canonical_origin")) or origin,
        "google_verification_configured": bool(settings.get("seo_google_site_verification", "").strip()),
    }

#!/usr/bin/env python3
"""Focused scraper for the legacy Aruba SuperSite at https://old.mifp.eu/.

The generic crawler is intentionally not used here: Aruba/Flazio pages expose
useful static HTML, while rendered Chromium output can contain editor chrome.
This script therefore parses the static DOM first and uses rendering only as a
diagnostic fallback when requested.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.parse import urlunparse

import requests
from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

BASE_URL = "https://old.mifp.eu"
SCRAPER_VERSION = "aruba-remote-2026-05-26"
DEFAULT_PATHS = {
    "home": "/",
    "members": "/mifp-members",
    "scientific_council": "/mifp-scientific-council",
    "code_of_conduct": "/mifp-code-of-conduct",
    "privacy": "/mifp-privacy-policy",
    "join_privacy": "/join-us-privacy-policy",
    "join_us": "/join-us-how-to-become-a-member",
    "sponsors_index": "/sponsors-index",
    "sponsor_max_brasserie": "/sponsors-max-brasserie",
    "sponsors_how_to": "/sponsors-how-to-become-a-sponsor",
    "sponsors_cocktail": "/sponsors-mifp-cocktail",
    "contacts": "/contacts",
}
ADMIN_MARKERS = (
    "Cambia immagine",
    "Notifiche",
    "Gestisci sito",
    "Modifica sito",
    "Pubblica sito",
)
NOISE_LINES = {
    "mifp",
    "research",
    "events",
    "archive events",
    "sponsors",
    "join us",
    "contacts",
    "search",
    "index",
    "members",
    "scientific council",
}
PERSON_REJECT = re.compile(r"\b(menu|index|event|sponsor|privacy|conduct|search|contact|member|archive|research)\b", re.I)

# --- Document extensions for news parsing ---
DOC_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.csv', '.json', '.jsonl'}

# --- Navigation/footer noise sets ---
_NAV_NOISE = {
    'mifp', 'research', 'events', 'archive events', 'sponsors', 'join us',
    'contacts', 'search', 'index', 'members', 'scientific council',
    'code of conduct', 'privacy policy', 'cookie policy', 'privacy',
    'home', 'instagram', 'facebook', 'twitter', 'linkedin',
    'to top', 'download manifesto',
    'how to become a sponsor', 'how to become a member',
}

_FORTHCOMING_EVENT_NOISE = {
    'forthcoming events', 'forthcoming event',
}

_SPONSOR_NOISE = {
    "max's brasserie", "max's cocktails", 'mifp cocktail', 'massimiliano romano',
    'balsamic gin', "max's cocktails",
}

# Section header headings that are NOT news titles (just category labels)
_SECTION_LABEL_NOISE = {
    'agreement', 'agreements', 'awards', 'publications',
    'mifp publications', "members' publications", 'pubblications',
    'mifp pubblications', "members' pubblications", 'georgia', 'news', 'mifp news',
}

# Generic download CTAs that should NOT appear as body text
_GENERIC_DOWNLOAD_LABELS = {
    'download', 'download pdf', 'download agreement', 'download paper',
    'download here', 'pdf', 'full text', 'click here to download',
    'download document', 'download file', 'view pdf', 'open pdf',
    'agreement pdf',
}

# Footer noise patterns (matched against single lines)
_FOOTER_NOISE_SET = {
    'all rights reserved', 'powered by', 'created with',
    'copyright', '©',
}

# Strong title opening patterns (news title candidates from body text)
_TITLE_OPENERS = {
    'mifp and', 'congratulation', 'congratulations', 'agreement',
    'professor', 'dr.', 'award', 'prize', 'announcement',
    'partnership', 'signing', 'honorary', 'passed away',
    'nobel', 'pleased to announce', 'proud to announce',
}

# Navigation link URL patterns to skip during linearization
_NAV_LINK_PATTERNS = {
    '/mifp-members', '/mifp-scientific-council', '/mifp-code-of-conduct',
    '/mifp-privacy-policy', '/join-us-privacy-policy',
    '/join-us-how-to-become-a-member', '/sponsors-index',
    '/sponsors-max-brasserie', '/sponsors-how-to-become-a-sponsor',
    '/sponsors-mifp-cocktail', '/contacts', '/events-meetings',
    '/events-schools', '/events-workshops', '/events-conferences',
    '/manifesto-of-solidarity', '/mifp-manifesto-of-solidarity',
}

# Navigation link text to skip (case-insensitive)
_NAV_LINK_TEXTS = {
    'mifp', 'code of conduct', 'privacy policy', 'members',
    'scientific council', 'research', 'projects', 'events',
    'index', 'sponsors', 'join us', 'contacts',
    "members' presentations", "members' publications", 'mifp publications',
    "members' pubblications", 'mifp pubblications',
    'archive events', 'meetings', 'schools', 'workshops', 'conferences',
    "max's brasserie", 'how to become a sponsor', 'mifp cocktail',
    'how to become a member', 'march meeting 2023', 'plmcn 2023',
    'icnp 2023', 'download manifesto', 'instagram', 'facebook',
    'twitter', 'linkedin', 'home',
}

_NAV_CLUSTER_PHRASES = {
    'archive events',
    'conferences',
    'code of conduct',
    'contacts',
    'events',
    'how to become a member',
    'how to become a sponsor',
    'icnp',
    'index',
    'join us',
    'march meeting',
    'max s brasserie',
    'members',
    'members presentations',
    'members publications',
    'members pubblications',
    'meetings',
    'mifp publications',
    'mifp pubblications',
    'plmcn',
    'privacy policy',
    'projects',
    'research',
    'schools',
    'scientific council',
    'search',
    'sponsors',
}

# URL domains that indicate editor chrome / external noise
_EDITOR_CHROME_DOMAINS = {
    'static.supersite.aruba.it',
}


@dataclass
class FetchResult:
    key: str
    url: str
    final_url: str
    status_code: int
    source: str
    html: str
    elapsed_ms: int
    error: str | None = None


@dataclass
class ArubaScrape:
    pages: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    sponsors: list[dict[str, Any]] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    discarded_members: list[dict[str, str]] = field(default_factory=list)
    fetches: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    # Debug fields for news pipeline
    _home_news_tokens: list[dict[str, Any]] = field(default_factory=list)
    _home_news_segments: list[dict[str, Any]] = field(default_factory=list)
    _skipped_tokens: dict[str, int] = field(default_factory=dict)


def clean_space(value: str | None) -> str:
    value = (value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = value.lower().replace("\u2019", "")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def normalize_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_space(value).lower()).strip()


def is_navigation_cluster(text: str) -> bool:
    """Detect Aruba menu blocks that arrive as one concatenated text node."""
    norm = normalize_key(text)
    if not norm:
        return True
    words = norm.split()
    if len(words) < 3:
        return False
    if len(words) > 12 and re.search(r'[.!?]', text):
        return False
    signals = sum(1 for phrase in _NAV_CLUSTER_PHRASES if re.search(rf'\b{re.escape(phrase)}\b', norm))
    if signals >= 3:
        return True
    if signals >= 2 and len(words) <= 12 and not re.search(r'\b(announce|award|congratulation|conference|paper|published|science)\b', norm):
        return True
    return False


def normalized_excerpt(value: str | None) -> str:
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip().lower()


def summary_is_body_excerpt(summary: str | None, body: str | None) -> bool:
    summary_norm = normalized_excerpt(summary)
    body_norm = normalized_excerpt(body)
    if not summary_norm or not body_norm:
        return False
    if body_norm.startswith(summary_norm):
        return True
    probe = summary_norm[:min(len(summary_norm), 140)]
    return len(probe) >= 80 and body_norm.startswith(probe)


def split_name(display_name: str) -> tuple[str | None, str | None]:
    parts = clean_space(display_name).split()
    if len(parts) < 2:
        return None, display_name
    return parts[0], " ".join(parts[1:])


def absolute_url(url: str, page_url: str = BASE_URL) -> str:
    if url.startswith("//"):
        return "https:" + url
    return urljoin(page_url, url)


def canonical_old_url(url: str, page_url: str = BASE_URL) -> str:
    if not str(url or "").strip():
        return ""
    raw = absolute_url(str(url or ""), page_url)
    parsed = urlparse(raw)
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    host = parsed.netloc.lower()
    if host == "www.old.mifp.eu":
        host = "old.mifp.eu"
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((scheme, host, path, "", "", ""))


def kind_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return "image"
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")):
        return "document"
    return "other"


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def fetch_static(session: requests.Session, key: str, path: str, timeout: int) -> FetchResult:
    url = absolute_url(path)
    start = time.monotonic()
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        elapsed = int((time.monotonic() - start) * 1000)
        response.encoding = response.encoding or "utf-8"
        return FetchResult(key, url, response.url, response.status_code, "static", response.text, elapsed)
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return FetchResult(key, url, url, 0, "static", "", elapsed, str(exc))


def fetch_rendered(url: str, wait_ms: int, timeout: int) -> str | None:
    chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if not chromium:
        return None
    command = [
        chromium,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        f"--virtual-time-budget={wait_ms}",
        "--dump-dom",
        url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    if any(marker in result.stdout for marker in ADMIN_MARKERS):
        return None
    return result.stdout


def soup_from(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def drop_chrome(soup: BeautifulSoup) -> BeautifulSoup:
    for selector in ("script", "style", "noscript", "iframe", "form", "ul.nav", ".show-menu", "[data-type='footer']"):
        for node in list(soup.select(selector)):
            node.decompose()
    return soup


def top_px(tag: Tag) -> int | None:
    style = tag.get("style") or ""
    match = re.search(r"\btop:\s*(-?\d+)px", style)
    return int(match.group(1)) if match else None


def content_text_lines(soup: BeautifulSoup) -> list[str]:
    soup = drop_chrome(soup)
    lines: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        parent_top = None
        for parent in [tag, *tag.parents]:
            if isinstance(parent, Tag):
                parent_top = top_px(parent)
                if parent_top is not None:
                    break
        if parent_top is not None and parent_top < 80:
            continue
        text = clean_space(tag.get_text(" ", strip=True))
        if not text or text.lower() in NOISE_LINES:
            continue
        if "\u00a9 2010-2022 mediterranean institute" in text.lower():
            continue
        key = normalize_key(text)
        if key and key not in seen:
            lines.append(text)
            seen.add(key)
    return lines


def extract_links_and_assets(soup: BeautifulSoup, page_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    links: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    seen_assets: set[str] = set()
    seen_links: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        url = canonical_old_url(src, page_url)
        if url in seen_assets:
            continue
        seen_assets.add(url)
        assets.append({
            "url": url,
            "source_url": url,
            "kind": kind_from_url(url),
            "role": "gallery",
            "alt_text": clean_space(img.get("alt")),
            "caption": clean_space(img.get("title")),
            "page_url": page_url,
        })
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href or href.startswith(("javascript:", "#")):
            continue
        if href.startswith(("mailto:", "tel:")):
            continue
        url = canonical_old_url(href, page_url)
        label = clean_space(a.get_text(" ", strip=True)) or clean_space(a.get("title"))
        kind = kind_from_url(url)
        if kind in {"image", "pdf", "document"}:
            if url in seen_assets:
                continue
            seen_assets.add(url)
            assets.append({
                "url": url,
                "source_url": url,
                "kind": kind,
                "role": "document" if kind in {"pdf", "document"} else "gallery",
                "caption": label,
                "page_url": page_url,
            })
        else:
            if url in seen_links:
                continue
            seen_links.add(url)
            links.append({
                "url": url,
                "label": label,
                "is_internal": int(urlparse(url).netloc.endswith("old.mifp.eu")),
                "page_url": page_url,
            })
    # Inline PDF URLs from raw HTML — catches JS, data-attr, onclick URLs
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf', str(soup), re.IGNORECASE):
        url = str(m.group(0)).split("?")[0].split("#")[0]
        if url not in seen_assets:
            seen_assets.add(url)
            assets.append({
                "url": url,
                "source_url": url,
                "kind": "pdf",
                "role": "document",
                "caption": "",
                "page_url": page_url,
            })
    return links, assets


def valid_member_name(name: str) -> tuple[bool, str]:
    name = clean_space(name)
    if len(name) < 5 or len(name) > 90:
        return False, "bad_length"
    if PERSON_REJECT.search(name):
        return False, "looks_like_navigation"
    if not re.search(r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff]", name) or len(name.split()) < 2:
        return False, "not_a_person_name"
    return True, ""


def member_record(name: str, affiliation: str, role: str, source_url: str, sort_order: int) -> dict[str, Any]:
    first, last = split_name(name)
    normalized_name = normalize_key(name)
    normalized_affiliation = normalize_key(affiliation)
    return {
        "type": "member",
        "display_name": clean_space(name),
        "first_name": first,
        "last_name": last,
        "affiliation": clean_space(affiliation),
        "role": role,
        "review_status": "published",
        "is_active": 1,
        "sort_order": sort_order,
        "normalized_name": normalized_name,
        "normalized_affiliation": normalized_affiliation,
        "quality_flags_json": json.dumps({"source": "aruba_remote", "source_url": source_url}, ensure_ascii=False),
    }


def parse_members_table(soup: BeautifulSoup, source_url: str, scrape: ArubaScrape) -> None:
    found = 0
    seen: set[tuple[str, str]] = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [clean_space(td.get_text(" ", strip=True)).lower() for td in rows[0].find_all(["td", "th"])]
        if "member" not in header or "affiliation" not in header:
            continue
        for row in rows[1:]:
            cells = [clean_space(td.get_text(" ", strip=True)) for td in row.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            name, affiliation = cells[1], cells[2]
            ok, reason = valid_member_name(name)
            if not ok:
                scrape.discarded_members.append({"name": name, "affiliation": affiliation, "reason": reason, "source_url": source_url})
                continue
            key = (normalize_key(name), normalize_key(affiliation))
            if key in seen:
                scrape.discarded_members.append({"name": name, "affiliation": affiliation, "reason": "duplicate_in_page", "source_url": source_url})
                continue
            seen.add(key)
            found += 1
            scrape.members.append(member_record(name, affiliation, "member", source_url, found))


def parse_scientific_council(soup: BeautifulSoup, source_url: str, scrape: ArubaScrape) -> None:
    lines = content_text_lines(soup)
    found = 0
    for line in lines:
        if not line.lower().startswith("prof. "):
            continue
        text = re.sub(r"^Prof\.\s*", "", line, flags=re.I).strip()
        parts = [clean_space(x) for x in text.split(",") if clean_space(x)]
        if len(parts) < 2:
            continue
        name = parts[0]
        affiliation = ", ".join(parts[1:])
        ok, reason = valid_member_name(name)
        if not ok:
            scrape.discarded_members.append({"name": name, "affiliation": affiliation, "reason": reason, "source_url": source_url})
            continue
        found += 1
        scrape.members.append(member_record(name, affiliation, "scientific_council", source_url, found))


def page_record(title: str, slug: str, page_type: str, body: str, source_url: str, assets: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "page",
        "title": title,
        "slug": slug,
        "page_type": page_type,
        "body": body,
        "summary": body[:500],
        "source_path": source_url,
        "source_url": source_url,
        "canonical_url": canonical_old_url(source_url),
        "scraper_version": SCRAPER_VERSION,
        "is_published": 1,
        "review_status": "published",
        "sort_order": 0,
        "assets": assets,
        "links": links,
    }


def validate_page(page_type: str, body: str) -> tuple[bool, str]:
    low = body.lower()
    if len(body) < 120:
        return False, "too_short"
    if "404" in low or "resource not found" in low or "component not found" in low:
        return False, "error_page"
    required = {
        "privacy": ("personal data", "cookies", "data controller"),
        "code_of_conduct": ("code of conduct", "scientific council", "no discrimination"),
        "manifesto": ("manifesto", "solidarity"),
    }
    terms = required.get(page_type)
    if terms and not all(term in low for term in terms):
        return False, "missing_expected_markers"
    if sum(1 for line in body.splitlines() if line.lower() in NOISE_LINES) > 4:
        return False, "navigation_noise"
    return True, "ok"


def parse_policy_page(key: str, soup: BeautifulSoup, source_url: str, scrape: ArubaScrape, links: list[dict[str, Any]], assets: list[dict[str, Any]]) -> None:
    if key == "join_privacy":
        return
    mapping = {
        "code_of_conduct": ("Code of Conduct", "code-of-conduct", "code_of_conduct"),
        "privacy": ("Privacy Policy", "privacy", "privacy"),
        "manifesto": ("Manifesto of Solidarity", "manifesto", "manifesto"),
    }
    if key not in mapping:
        return
    title, slug, page_type = mapping[key]
    lines = content_text_lines(soup)
    body = "\n\n".join(lines)
    ok, reason = validate_page(page_type, body)
    scrape.fetches.append({"url": source_url, "policy_type": page_type, "valid": ok, "validation": reason, "body_chars": len(body)})
    if not ok:
        return
    doc_assets = [a for a in assets if a.get("kind") in {"pdf", "document"}]
    for asset in doc_assets:
        asset["role"] = "document"
    scrape.pages.append(page_record(title, slug, page_type, body, source_url, doc_assets, links))


def parse_basic_page(key: str, soup: BeautifulSoup, source_url: str, scrape: ArubaScrape, links: list[dict[str, Any]], assets: list[dict[str, Any]]) -> None:
    if key in {"members", "scientific_council", "code_of_conduct", "privacy", "join_privacy", "events_meetings", "events_schools", "events_workshops", "events_conferences"}:
        return
    lines = content_text_lines(soup)
    body = "\n\n".join(lines)
    if len(body) < 80:
        return
    page_type_by_key = {
        "home": "legacy_home",
        "sponsor_max_brasserie": "sponsor",
        "sponsors_how_to": "sponsor",
        "contacts": "contact",
    }
    slug_by_key = {
        "home": "home",
        "sponsor_max_brasserie": "sponsor-max-brasserie",
        "sponsors_how_to": "sponsors-how-to",
        "contacts": "contacts",
    }
    title = lines[0] if lines else key.replace("_", " ").title()

    body_lower = body.lower()
    if "news e forthcoming events" in body_lower:
        page_type = "news"
        slug = "news-e-forthcoming-events"
    else:
        page_type = page_type_by_key.get(key, "custom")
        slug = slug_by_key.get(key, slugify(key))

    scrape.pages.append(page_record(
        title,
        slug,
        page_type,
        body,
        source_url,
        assets[:8],
        links[:20],
    ))


def parse_sponsor(key: str, soup: BeautifulSoup, source_url: str, scrape: ArubaScrape, links: list[dict[str, Any]], assets: list[dict[str, Any]]) -> None:
    if key == "sponsor_max_brasserie":
        lines = content_text_lines(soup)
        body = "\n".join(line for line in lines if "\u00a9 2010-2022" not in line)
        website = None
        for link in links:
            if "massimilianos-cocktails.co.uk" in link["url"]:
                website = link["url"]
                break
        logo_assets = [
            {**a, "role": "logo"}
            for a in assets
            if a.get("kind") == "image" and "19a73b2e70c1b5d25b903f972863a3a3eedde5dd" not in a.get("url", "") and not a.get("url", "").endswith(".svg")
        ]
        scrape.sponsors.append({
            "type": "sponsor",
            "name": "Max's Brasserie",
            "slug": "maxs-brasserie",
            "description": body[:1200],
            "website_url": website,
            "sponsor_type": "sponsor",
            "tier": "sponsor",
            "is_active": 1,
            "sort_order": 10,
            "images": logo_assets[:1],
            "links": [{"url": website, "label": "Website", "role": "website"}] if website else [],
        })
        return


    
def unique_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        url = canonical_old_url(item.get("url") or item.get("source_url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({**item, "url": url})
    return out


def extract_structured_page(soup: BeautifulSoup, page_url: str) -> dict[str, Any]:
    clean = drop_chrome(copy.deepcopy(soup))
    title = clean_space((clean.find("h1") or clean.find("title") or clean).get_text(" ", strip=True))
    headings = [clean_space(h.get_text(" ", strip=True)) for h in clean.find_all(re.compile("^h[1-4]$"))]
    lines = content_text_lines(clean)
    links, assets = extract_links_and_assets(clean, page_url)
    documents = [a for a in assets if a.get("kind") in {"pdf", "document"}]
    images = [a for a in assets if a.get("kind") == "image"]
    warnings: list[str] = []
    if len("\n".join(lines)) < 120:
        warnings.append("content_too_short")
    if len(lines) != len({normalize_key(line) for line in lines}):
        warnings.append("duplicate_text_blocks")
    return {
        "title": title,
        "headings": headings,
        "clean_text": "\n".join(lines),
        "blocks": lines,
        "images": images,
        "documents": documents,
        "links": links,
        "canonical_url": canonical_old_url(page_url),
        "source_url": page_url,
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scraper_version": SCRAPER_VERSION,
        "confidence": 0.8 if lines and not warnings else 0.45,
        "warnings": warnings,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Home news extraction pipeline
# ---------------------------------------------------------------------------

def looks_like_news_title(text: str) -> bool:
    """Return True if text looks like a plausible news/blog title."""
    if not text or len(text) < 8 or len(text) > 200:
        return False
    low = text.lower().strip()
    # Must not start with http
    if low.startswith("http://") or low.startswith("https://"):
        return False
    # Must not be purely numeric
    if re.match(r'^[\d\s.,\-/]+$', text):
        return False
    # Must not be in noise sets
    if low in _NAV_NOISE or low in _NAV_LINK_TEXTS or low in _SECTION_LABEL_NOISE or low in _FORTHCOMING_EVENT_NOISE or low in _SPONSOR_NOISE:
        return False
    if is_navigation_cluster(text):
        return False
    # Must not be a generic download label
    if len(low) < 60 and low in _GENERIC_DOWNLOAD_LABELS:
        return False
    # Must not be footer noise
    if low in _FOOTER_NOISE_SET:
        return False
    # Must not contain "balsamic gin" or "massimilianos"
    if "balsamic gin" in low or "massimilianos" in low:
        return False
    # Must contain at least one alphabetic character
    if not re.search(r'[A-Za-z\u00c0-\u00ff]', text):
        return False
    # Must not be just a hash+extension pattern (e.g. "19a73b2e70c1b5d25b903f972863a3a3eedde5dd.webp")
    if re.match(r'^[a-f0-9]{20,}\.\w{2,5}$', low):
        return False
    # Must not be just a filename.extension
    if re.match(r'^[\w\-]{1,60}\.\w{1,10}$', low) and ' ' not in text:
        return False
    return True


def is_navigation_or_footer_noise(text: str) -> bool:
    """Return True if text is navigation/footer noise or a generic download label."""
    low = text.lower().strip()
    if not low or len(low) < 3:
        return True
    if low in _NAV_NOISE or low in _NAV_LINK_TEXTS:
        return True
    if low in {'search index', 'events index', 'sponsors index'}:
        return True
    if low in _SECTION_LABEL_NOISE:
        return True
    if is_navigation_cluster(text):
        return True
    # Generic download label (short text that is ONLY a download CTA)
    if len(low) < 60 and low in _GENERIC_DOWNLOAD_LABELS:
        return True
    # Footer noise patterns
    if low in _FOOTER_NOISE_SET:
        return True
    # Copyright text
    if re.match(r'^\u00a9\s*\d{4}', low) or re.match(r'^copyright\s', low):
        return True
    # Cookie/privacy notice fragments
    if any(phrase in low for phrase in [
        'cookie policy', 'privacy policy', 'i accept', 'accept all cookies',
        'manage cookies', 'this site uses cookies', 'we use cookies',
        'all rights reserved', 'powered by', 'read more',
    ]):
        return True
    return False


def is_forthcoming_event_noise(text: str) -> bool:
    """Return True if text is forthcoming event section noise."""
    low = text.lower().strip()
    if low in _FORTHCOMING_EVENT_NOISE:
        return True
    # PLMCN/QLIN/TBC patterns
    if re.match(r'^(PLMCN|QLIN|TBC)\s*[-–:]?\s*$', low):
        return True
    return False


def looks_like_event_title_pattern(text: str) -> bool:
    """Detect event-code titles like 'PLMNC 2023 - Medellin, Colombia' or 'ICP2DC6 - 2022, Yerevan Armenia'."""
    if not text or len(text) < 8 or len(text) > 180:
        return False
    # Pattern: uppercase acronym + year (e.g. "PLMNC 2023", "ICP2DC6 - 2022")
    if re.search(r'\b[A-Z][A-Z0-9]{1,10}\s*[-–]?\s*\d{4}\b', text):
        return True
    # Pattern: ends with country after comma (e.g. "Medellin, Colombia", "Yerevan, Armenia")
    if re.search(r',\s*[A-Z][a-z]+\s*$', text):
        return True
    # Pattern: ends with " - City" (e.g. "Conference Name - Paris")
    if re.search(r'\s+[-–]\s+[A-Z][a-zA-Z]+$', text):
        return True
    return False


def has_strong_title_signal(text: str) -> bool:
    """Check if text has structural signals of being a news title.

    A strong title signal means the text should start a new news item
    even if it appears as a <p> tag (not a heading).
    """
    if not text or len(text) < 8 or len(text) > 180:
        return False
    low = text.lower().strip()

    # Event code patterns
    if looks_like_event_title_pattern(text):
        return True

    # Reject texts that look like "X PDF" or "X DOC" (generic download labels, not titles)
    if len(text.split()) <= 3 and re.search(r'\b(pdf|doc|docx|zip)$', low):
        return False

    # Institutional openings
    if any(low.startswith(opener) for opener in _TITLE_OPENERS):
        return True

    # Title Case heuristic: short text, no period, starts uppercase, >=60% capitalized words
    if text.endswith('.'):
        return False
    if not text[0].isupper():
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 20:
        return False
    capitalized = sum(1 for w in words if w and w[0].isupper())
    if capitalized / len(words) >= 0.6:
        return True

    return False


def _is_nav_link(url: str, text: str) -> bool:
    """Check if a link is a navigation link that should be skipped."""
    parsed = urlparse(url)
    low_text = text.lower().strip()
    low_path = parsed.path.lower().rstrip('/')
    # Check by URL path
    if low_path in _NAV_LINK_PATTERNS:
        return True
    # Check by text
    if low_text in _NAV_LINK_TEXTS:
        return True
    # Links that are just anchors on same page
    if parsed.netloc and parsed.netloc != 'old.mifp.eu' and parsed.netloc != '':
        if not parsed.path.endswith(('.pdf', '.doc', '.docx', '.png', '.jpg', '.jpeg', '.gif', '.webp')):
            # External links to social media etc.
            if any(s in parsed.netloc for s in ['facebook.com', 'twitter.com', 'instagram.com', 'linkedin.com', 'youtube.com']):
                return True
    return False


def _extract_date_from_text(text: str) -> tuple[str, str]:
    """Best-effort extraction of a date from news text.
    Returns (iso_date, date_precision) where precision is 'day'|'month'|'year'|'unknown'.
    """
    if not text:
        return '', 'unknown'
    # Month name mapping
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    low = text.lower()
    # Pattern: "17/11/2018" or "17-11-2018"
    m = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f'{y:04d}-{mo:02d}-{d:02d}', 'day'
    # Pattern: "29th of June 2018" or "June 29, 2018" or "29 June 2018"
    m = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(' + '|'.join(months.keys()) + r')\s+(\d{4})\b', low)
    if m:
        d, mo_num, y = int(m.group(1)), months[m.group(2)], int(m.group(3))
        if 1 <= d <= 31:
            return f'{y:04d}-{mo_num:02d}-{d:02d}', 'day'
    m = re.search(r'\b(' + '|'.join(months.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b', low)
    if m:
        mo_num, d, y = months[m.group(1)], int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31:
            return f'{y:04d}-{mo_num:02d}-{d:02d}', 'day'
    # Pattern: "February 2nd, 2016"
    m = re.search(r'\b(' + '|'.join(months.keys()) + r')\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b', low)
    if m:
        mo_num, d, y = months[m.group(1)], int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31:
            return f'{y:04d}-{mo_num:02d}-{d:02d}', 'day'
    # Pattern: "April 12-15. 2026" (range with year)
    m = re.search(r'\b(' + '|'.join(months.keys()) + r')\s+\d{1,2}[-–]\.?\s*(\d{4})\b', low)
    if m:
        mo_num, y = months[m.group(1)], int(m.group(2))
        return f'{y:04d}-{mo_num:02d}-01', 'month'
    # Pattern: "December 2017" (month + year)
    m = re.search(r'\b(' + '|'.join(months.keys()) + r')\s+(\d{4})\b', low)
    if m:
        mo_num, y = months[m.group(1)], int(m.group(2))
        return f'{y:04d}-{mo_num:02d}-01', 'month'
    # Pattern: "in 2018" or "2014"
    m = re.search(r'\b(?:in\s+)?((?:19|20)\d{2})\b', text)
    if m:
        y = int(m.group(1))
        if 1990 <= y <= 2030:
            return f'{y:04d}-01-01', 'year'
    return '', 'unknown'


def _extract_event_dates(text: str) -> tuple[str, str, float]:
    """Extract date range from event listing text.
    Returns (start_date, end_date, confidence) where dates are YYYY-MM-DD.
    confidence: 0.7=full day range, 0.6=month+year, 0.4=year only, 0.0=none.
    """
    if not text:
        return '', '', 0.0
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    month_alt = '|'.join(months.keys())
    low = text.lower()

    # "12-15 April 2026"  — day range + month + year
    m = re.search(
        r'\b(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(?:of\s+)?(' + month_alt + r')\s+(\d{4})\b',
        low
    )
    if m:
        sd, ed, mo, yr = int(m.group(1)), int(m.group(2)), months[m.group(3)], int(m.group(4))
        if 1 <= sd <= 31 and 1 <= ed <= 31 and 1 <= mo <= 12:
            return f'{yr:04d}-{mo:02d}-{sd:02d}', f'{yr:04d}-{mo:02d}-{ed:02d}', 0.7

    # "April 12-15, 2026" — month + day range + year
    m = re.search(
        r'\b(' + month_alt + r')\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),?\s+(\d{4})\b',
        low
    )
    if m:
        mo, sd, ed, yr = months[m.group(1)], int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 1 <= sd <= 31 and 1 <= ed <= 31 and 1 <= mo <= 12:
            return f'{yr:04d}-{mo:02d}-{sd:02d}', f'{yr:04d}-{mo:02d}-{ed:02d}', 0.7

    # "12 April 2026" — single day
    m = re.search(
        r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(' + month_alt + r')\s+(\d{4})\b',
        low
    )
    if m:
        d, mo, yr = int(m.group(1)), months[m.group(2)], int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            ds = f'{yr:04d}-{mo:02d}-{d:02d}'
            return ds, ds, 0.7

    # "April 2026" — month + year
    m = re.search(r'\b(' + month_alt + r')\s+(\d{4})\b', low)
    if m:
        mo, yr = months[m.group(1)], int(m.group(2))
        if 1 <= mo <= 12:
            return f'{yr:04d}-{mo:02d}-01', f'{yr:04d}-{mo:02d}-28', 0.6

    # "2026" — year only
    m = re.search(r'\b(20\d{2})\b', low)
    if m:
        yr = int(m.group(1))
        return f'{yr:04d}-01-01', f'{yr:04d}-12-31', 0.4

    return '', '', 0.0


_EVENT_SECTION_NOISE = {
    'events', 'meetings', 'schools', 'workshops', 'conferences',
    'archive events', 'forthcoming events',
    'events - meetings', 'events - schools', 'events - workshops',
    'events - conferences', 'meetings - events', 'schools - events',
    'workshops - events', 'conferences - events',
}


def _looks_like_event_listing_title(text: str) -> bool:
    """Return True if text looks like an event title on a listing page."""
    if not text or len(text) < 5:
        return False
    low = text.lower().strip()
    if low in _EVENT_SECTION_NOISE:
        return False
    if is_navigation_or_footer_noise(text):
        return False
    return looks_like_event_title_pattern(text) or has_strong_title_signal(text)


def parse_event_listings(
    key: str, soup: BeautifulSoup, source_url: str,
    event_type: str, scrape: ArubaScrape,
) -> None:
    """Parse an event listing page and append event records to scrape.events."""
    soup = drop_chrome(copy.deepcopy(soup))
    page_url = source_url.rstrip('/')

    candidates = _extract_event_candidates_from_text(soup, page_url)

    for c in candidates:
        title = c.get('title', '').strip()
        if not title or len(title) < 3:
            continue

        # Extract date from title (most listing events embed year)
        start_date, end_date, conf = _extract_event_dates(title)
        if not start_date:
            m = re.search(r'\b(20\d{2})\b', title)
            if m:
                yr = int(m.group(1))
                start_date, end_date, conf = f'{yr:04d}-01-01', f'{yr:04d}-12-31', 0.4
            else:
                continue  # no date at all, skip

        scrape.events.append({
            'title': title,
            'start_date': start_date,
            'end_date': end_date,
            'event_type': event_type,
            'review_status': 'published',
            'description': '',
            'location': '',
            'url': c.get('event_url', ''),
            'confidence': 0.7,
        })


def _extract_event_candidates_from_text(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    """Extract event names from page text-bearing inline/block elements.

    Aruba listing pages typically list events as <span> or <p> text
    inside a content div, with no heading tags per event.
    """
    found: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for tag in soup.descendants:
        if not isinstance(tag, Tag):
            continue
        tn = tag.name.lower()
        if tn in ('script', 'style', 'noscript', 'iframe', 'form', 'input', 'button', 'select', 'textarea'):
            continue

        text = clean_space(tag.get_text(' ', strip=True))
        if not text or len(text) < 5:
            continue
        if is_navigation_or_footer_noise(text):
            continue
        if text.lower().strip() in _EVENT_SECTION_NOISE:
            continue

        # Text-bearing elements. Only process leaf elements — skip parents whose
        # children already contain the text.
        if tn in ('span', 'p', 'b', 'strong', 'i', 'em', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            # Skip if this element's text is entirely within child text-bearing elements
            child_text_tags = tag.find_all(['span', 'b', 'strong', 'i', 'em', 'u', 'a'])
            if child_text_tags:
                child_texts = ''.join(c.get_text(' ', strip=True) for c in child_text_tags)
                if clean_space(child_texts) == text:
                    continue  # children already capture this text

            # Check for link inside
            event_url = ''
            a_tag = tag.find('a', href=True) if tn != 'a' else tag
            if a_tag and a_tag.name == 'a' and a_tag.get('href'):
                href = urljoin(page_url, a_tag['href'])
                if 'events.mifp.eu' in href.lower():
                    event_url = href

            if event_url or _looks_like_event_listing_title(text):
                norm = re.sub(r'[^a-z0-9]+', '', text.lower())
                if norm not in seen_titles:
                    seen_titles.add(norm)
                    found.append({'title': text, 'event_url': event_url})

    return found


def _classify_link_role(url: str, label: str) -> str:
    """Classify a link as external, paper, source, or more_info based on URL and label."""
    low_url = url.lower()
    low_label = label.lower()
    # Academic/paper links
    if any(d in low_url for d in ['doi.org', 'arxiv.org', 'pubmed', 'nature.com',
                                     'science.org', 'aps.org', 'iop.org', 'springer.com',
                                     'wiley.com', 'elsevier.com', 'pnas.org']):
        return 'paper'
    # PDF links are documents, not links
    if low_url.endswith('.pdf'):
        return 'source'
    # Label-based classification
    if any(kw in low_label for kw in ['read more', 'more info', 'more details',
                                        'full text', 'full article', 'scopri']):
        return 'more_info'
    if any(kw in low_label for kw in ['download', 'pdf', 'paper', 'article',
                                        'publication', 'journal']):
        return 'paper'
    return 'external'


def _extract_img_urls(tag: Tag, page_url: str) -> list[str]:
    """Extract all image URLs from an img tag including srcset and background-image."""
    urls = []
    # Primary src
    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
        val = tag.get(attr)
        if val and not val.startswith("data:"):
            urls.append(absolute_url(val, page_url))
    # srcset entries
    srcset = tag.get("srcset")
    if srcset:
        for entry in srcset.split(","):
            parts = entry.strip().split()
            if parts and parts[0] and not parts[0].startswith("data:"):
                urls.append(absolute_url(parts[0], page_url))
    # background-image in inline style
    style = tag.get("style") or ""
    bg_match = re.search(r'background-image\s*:\s*url\(["\']?([^"\'()]+)["\']?\)', style, re.I)
    if bg_match and not bg_match.group(1).startswith("data:"):
        urls.append(absolute_url(bg_match.group(1), page_url))
    return urls


def _document_tokens_from_links(tag: Tag, page_url: str, dom_index: int) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for child_a in tag.find_all("a"):
        href = child_a.get("href", "")
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        url = absolute_url(href, page_url)
        url_path = urlparse(url).path.lower()
        _, ext = os.path.splitext(url_path)
        if ext not in DOC_EXTENSIONS:
            continue
        tokens.append({
            "kind": "document",
            "url": url,
            "text": clean_space(child_a.get_text(" ") or child_a.get("title") or child_a.get("href")),
            "tag": "a",
            "dom_index": dom_index,
        })
    return tokens


def _is_document_cta_text(text: str, document_tokens: list[dict[str, Any]]) -> bool:
    if not document_tokens:
        return False
    low = clean_space(text).lower()
    if len(low) > 120:
        return False
    return bool(re.search(r"\b(download|paper|article|pdf|document|full text)\b", low))


def linearize_home_content(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Walk the DOM in document order, producing a flat list of content tokens.
    Filters out navigation, footer, cookie banners, and editor chrome.
    Returns (tokens_list, skipped_counts_dict).
    """
    soup = copy.copy(soup)

    # Remove unwanted elements
    for selector in (
        "script", "style", "noscript", "iframe", "nav", "form", "footer",
        "[data-type='footer']", ".show-menu",
    ):
        for node in list(soup.select(selector)):
            node.decompose()

    # Cookie banner elements
    for tag in soup.find_all(True, attrs={"id": re.compile(r'cookie|consent|gdpr', re.I)}):
        tag.decompose()
    for tag in soup.find_all(True, attrs={"class": re.compile(r'cookie|consent|gdpr', re.I)}):
        tag.decompose()

    # Menu bars and editor chrome
    for selector in (".pulsantiera_fade_orizzontale", ".orizzontalemenu_new", ".yscrollbar"):
        for node in list(soup.select(selector)):
            node.decompose()

    # Shadow elements
    for node in list(soup.select("[class*='comp_ombra']")):
        node.decompose()

    # Elements containing admin markers
    for tag in soup.find_all(string=re.compile('|'.join(re.escape(m) for m in ADMIN_MARKERS), re.I)):
        parent = tag.parent
        if parent:
            parent.decompose()

    # Build a set of tags whose text has already been captured via a parent.
    # This prevents <a> inside <p> from creating duplicate text tokens.
    captured_parents: set[int] = set()  # ids of parent tags whose text was captured

    tokens: list[dict] = []
    dom_index = 0
    skipped = {
        "navigation": 0, "footer_noise": 0, "forthcoming_events": 0,
        "hash_filenames": 0, "editor_chrome_images": 0,
        "nav_links": 0, "sponsor_content": 0,
    }

    for tag in soup.descendants:
        if not isinstance(tag, Tag):
            continue

        tag_name = tag.name.lower()

        # Headings
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = clean_space(tag.get_text(" ", strip=True))
            if not text:
                continue
            if is_navigation_or_footer_noise(text):
                skipped["navigation"] += 1
                continue
            tokens.append({"kind": "heading", "text": text, "tag": tag_name, "dom_index": dom_index})
            dom_index += 1

        # Paragraphs and text blocks
        elif tag_name == "p":
            text = clean_space(tag.get_text(" ", strip=True))
            if not text:
                continue
            if is_navigation_or_footer_noise(text):
                skipped["footer_noise"] += 1
                continue
            # Skip hash+extension filenames
            if re.match(r'^[a-f0-9]{20,}\.\w{2,5}$', text.lower()):
                skipped["hash_filenames"] += 1
                continue
            document_tokens = _document_tokens_from_links(tag, page_url, dom_index)
            # Mark child <a> tags as captured so they don't produce duplicate link tokens
            for child_a in tag.find_all('a'):
                captured_parents.add(id(child_a))
            if not _is_document_cta_text(text, document_tokens):
                tokens.append({"kind": "text", "text": text, "tag": "p", "dom_index": dom_index})
                dom_index += 1
            for doc_token in document_tokens:
                doc_token["dom_index"] = dom_index
                tokens.append(doc_token)
                dom_index += 1

        # List items (often contain news content on Aruba sites)
        elif tag_name == "li":
            text = clean_space(tag.get_text(" ", strip=True))
            if not text or len(text) < 5:
                continue
            if is_navigation_or_footer_noise(text):
                skipped["footer_noise"] += 1
                continue
            document_tokens = _document_tokens_from_links(tag, page_url, dom_index)
            # Mark child <a> tags as captured
            for child_a in tag.find_all('a'):
                captured_parents.add(id(child_a))
            if not _is_document_cta_text(text, document_tokens):
                tokens.append({"kind": "text", "text": text, "tag": "li", "dom_index": dom_index})
                dom_index += 1
            for doc_token in document_tokens:
                doc_token["dom_index"] = dom_index
                tokens.append(doc_token)
                dom_index += 1

        # Images
        elif tag_name == "img":
            urls = _extract_img_urls(tag, page_url)
            if not urls:
                continue
            alt = clean_space(tag.get("alt") or "")
            for url in urls:
                # Skip editor chrome images
                parsed = urlparse(url)
                if parsed.netloc in _EDITOR_CHROME_DOMAINS:
                    skipped["editor_chrome_images"] += 1
                    continue
                tokens.append({"kind": "image", "url": url, "alt_text": alt, "tag": "img", "dom_index": dom_index})
                dom_index += 1

        # Links
        elif tag_name == "a":
            # Skip if text was already captured by a parent <p> or <li>
            if id(tag) in captured_parents:
                continue
            href = tag.get("href", "")
            if not href or href.startswith(("javascript:", "#", "mailto:")):
                continue
            text = clean_space(tag.get_text())
            url = absolute_url(href, page_url)

            # Filter navigation links
            if _is_nav_link(url, text):
                skipped["nav_links"] += 1
                continue

            # Check if it's a document link
            url_path = urlparse(url).path.lower()
            _, ext = os.path.splitext(url_path)
            if ext in DOC_EXTENSIONS:
                tokens.append({"kind": "document", "url": url, "text": text, "tag": "a", "dom_index": dom_index})
                dom_index += 1
            else:
                # Skip links ending with just "#" or javascript
                if url.endswith("#") or url.startswith("javascript:"):
                    continue
                tokens.append({"kind": "link", "url": url, "text": text, "tag": "a", "dom_index": dom_index})
                dom_index += 1

    return tokens, skipped


def parse_home_news(soup: BeautifulSoup, page_url: str) -> tuple[list[dict], list[dict], dict]:
    """Parse the home page into news items.

    Uses DOM linearization to extract tokens, then segments them into
    individual news items based on heading/title proximity heuristics.
    Returns: (raw_news_items, all_tokens, skipped_counts)
    """
    tokens, skipped = linearize_home_content(soup, page_url)

    news_items: list[dict] = []
    current_item: dict | None = None
    pending_content: list[tuple[str, dict]] = []
    gap_since_last_commit = True
    heading_created_item = False
    in_forthcoming_zone = False
    in_sponsor_zone = False

    def flush_pending(target: dict | None):
        nonlocal pending_content, gap_since_last_commit
        if target is None:
            pending_content.clear()
            gap_since_last_commit = True
            return
        for kind, token in pending_content:
            if kind == "text":
                target["body_parts"].append(token["text"])
            elif kind == "image":
                target["images"].append({
                    "url": token["url"],
                    "alt_text": token.get("alt_text", ""),
                    "source_url": page_url,
                })
            elif kind == "document":
                target["documents"].append({
                    "url": token["url"],
                    "text": token.get("text", ""),
                    "source_url": page_url,
                    "kind": kind_from_url(token["url"]),
                })
            elif kind == "link":
                target["links"].append({
                    "url": token["url"],
                    "text": token.get("text", ""),
                    "role": _classify_link_role(token["url"], token.get("text", "")),
                    "source_url": page_url,
                })
            target["end_dom_index"] = token.get("dom_index", target.get("end_dom_index", 0))
        pending_content.clear()
        gap_since_last_commit = False

    def on_skip():
        """Called when a token is skipped (noise, section label, etc). Commits
        pending content to the current item and marks a structural gap."""
        nonlocal current_item, gap_since_last_commit
        if current_item and not gap_since_last_commit:
            flush_pending(current_item)
        gap_since_last_commit = True

    for token in tokens:
        kind = token["kind"]
        text = token.get("text", "")

        # Check for forthcoming events zone
        if kind in ("heading", "text") and "forthcoming event" in text.lower():
            in_forthcoming_zone = True
            skipped["forthcoming_events"] += 1
            on_skip()
            continue

        # Check for sponsor zone (Max's Brasserie etc.)
        if text.lower().strip() in _SPONSOR_NOISE:
            in_sponsor_zone = True
            skipped["sponsor_content"] += 1
            on_skip()
            continue
        if in_sponsor_zone:
            if kind == "heading" and looks_like_news_title(text):
                in_sponsor_zone = False
            elif text.lower().strip() in _SPONSOR_NOISE:
                skipped["sponsor_content"] += 1
                on_skip()
                continue
            else:
                # Non-sponsor content (images, body text) — exit zone and buffer
                in_sponsor_zone = False

        if in_forthcoming_zone:
            exit_pattern = re.compile(r'^(?:mifp\s+and|congratulation|agreement|award|professor)', re.I)
            should_exit = (
                kind in ("heading", "text")
                and exit_pattern.match(text.strip())
            )
            if should_exit:
                in_forthcoming_zone = False
            else:
                skipped["forthcoming_events"] += 1
                on_skip()
                continue

        # Check if this token starts a new news item
        is_new_title = False
        if kind == "heading" and looks_like_news_title(text) and not is_forthcoming_event_noise(text):
            # Skip section label headings that are just category labels
            if text.lower().strip() in _SECTION_LABEL_NOISE:
                skipped["navigation"] += 1
                on_skip()
                continue
            is_new_title = True
            heading_created_item = True
        elif kind == "text" and 8 <= len(text) <= 120:
            # Prevent first text token after a heading from creating a new title —
            # it's body text for the heading, even if it looks title-like.
            prevent = heading_created_item
            heading_created_item = False
            if (not prevent
                and not is_navigation_or_footer_noise(text)
                and not is_forthcoming_event_noise(text)
                and looks_like_news_title(text)
                and has_strong_title_signal(text)):
                is_new_title = True

        if is_new_title:
            if current_item:
                if not gap_since_last_commit:
                    flush_pending(current_item)
                news_items.append(current_item)
            current_item = {
                "title": text,
                "body_parts": [],
                "images": [],
                "documents": [],
                "links": [],
                "start_dom_index": token["dom_index"],
                "end_dom_index": token["dom_index"],
            }
            flush_pending(current_item)
            gap_since_last_commit = False
        elif current_item:
            pending_content.append((kind, token))

    # End of loop: flush remaining pending to current item
    if current_item:
        flush_pending(current_item)
        news_items.append(current_item)
    elif pending_content:
        # Orphan content with no item at all — discard
        skipped["orphan_content"] += len(pending_content)
        pending_content.clear()

    # Post-processing: Aruba sometimes emits a real title as a standalone block,
    # followed by the subtitle/body/assets as the next title-looking block. Merge
    # that orphan title forward so the item is not lost or attached to the
    # previous news card.
    def item_has_content(item: dict) -> bool:
        title_key = normalize_key(item.get('title', ''))
        body_parts = [
            part for part in (item.get('body_parts') or [])
            if normalize_key(part) and normalize_key(part) != title_key
        ]
        return bool(body_parts) or bool(item.get('images')) or bool(item.get('documents')) or bool(item.get('links'))

    forward_merged: list[dict] = []
    i = 0
    while i < len(news_items):
        item = news_items[i]
        has_content = item_has_content(item)
        if not has_content and i + 1 < len(news_items):
            next_item = news_items[i + 1]
            next_has_content = item_has_content(next_item)
            if next_has_content:
                merged = copy.deepcopy(next_item)
                if merged.get('title'):
                    merged.setdefault('body_parts', []).insert(0, merged['title'])
                merged['title'] = item.get('title', merged.get('title', ''))
                merged['start_dom_index'] = item.get('start_dom_index', merged.get('start_dom_index', 0))
                forward_merged.append(merged)
                i += 2
                continue
        forward_merged.append(item)
        i += 1

    # Merge any remaining orphan items into the previous item.
    merged_items: list[dict] = []
    for item in forward_merged:
        if is_navigation_cluster(item.get('title', '')):
            skipped["navigation"] += 1
            continue
        has_content = item_has_content(item)
        if not has_content and merged_items:
            prev = merged_items[-1]
            prev['body_parts'].append(item['title'])
        else:
            merged_items.append(item)

    return merged_items, tokens, skipped


def normalize_news_record(raw_post: dict, index: int, page_url: str) -> dict:
    """Transform a raw news dict into the final JSONL record format."""
    warnings: list[str] = []
    title = raw_post.get("title", "")
    body_parts = [part for part in raw_post.get("body_parts", []) if normalize_key(part) != normalize_key(title)]
    
    # Clean download labels from body_parts
    cleaned_body_parts = []
    for part in body_parts:
        low = part.lower().strip()
        if len(low) < 60 and low in _GENERIC_DOWNLOAD_LABELS:
            continue
        cleaned_body_parts.append(part)
    body_parts = cleaned_body_parts
    
    body = "\n\n".join(body_parts)

    # Check for likely merged news: body contains multiple later title-looking
    # blocks, or a late title-like block after substantial body text. The first
    # body line is often a subtitle/continuation of the title on Aruba pages.
    embedded_title_positions = []
    for pos, part in enumerate(body_parts):
        if pos == 0:
            continue
        if has_strong_title_signal(part) and normalize_key(part) != normalize_key(title):
            embedded_title_positions.append(pos)
    if len(embedded_title_positions) >= 2 or (
        embedded_title_positions and sum(len(p) for p in body_parts[:embedded_title_positions[0]]) >= 180
    ):
        warnings.append("possible_merged_news")
    if not body:
        warnings.append("no_body_detected")
    if len(body) < 40:
        warnings.append("very_short_body")
    if not title:
        warnings.append("no_title_detected")
    images = raw_post.get("images", [])
    documents = raw_post.get("documents", [])
    links = raw_post.get("links", [])

    # Summary
    summary = raw_post.get("summary") or (body[:250] if body else "")
    if summary_is_body_excerpt(summary, body):
        summary = ""

    # Validate title is not noise
    if title and title.lower().strip() in _NAV_NOISE:
        warnings.append("title_is_navigation_noise")
    if title and len(title) < 8:
        warnings.append("very_short_title")

    # --- Date extraction from body and title ---
    full_text = title + " " + body
    extracted_date, date_precision = _extract_date_from_text(full_text)
    date_is_inferred = 0 if extracted_date else 1
    date_text = ""
    if extracted_date and date_precision == 'day':
        date_text = extracted_date
    elif extracted_date and date_precision == 'month':
        # Show as "Month YYYY"
        try:
            parts = extracted_date.split('-')
            month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December']
            date_text = f"{month_names[int(parts[1])]} {parts[0]}"
        except (ValueError, IndexError):
            date_text = extracted_date[:4]
    elif extracted_date and date_precision == 'year':
        date_text = extracted_date[:4]

    # News type detection
    title_low = title.lower()
    body_low = body.lower()
    combined_low = title_low + " " + body_low
    if "agreement" in title_low or "cooperation" in title_low or "memorandum" in title_low or "collaborative agreement" in combined_low:
        news_type = "agreement"
    elif "award" in title_low or "prize" in title_low or "congratulation" in title_low or "congratulations" in title_low:
        news_type = "award"
    elif "publication" in title_low or "published" in title_low:
        news_type = "publication_highlight"
    elif "passed away" in title_low or "memorial" in title_low:
        news_type = "institutional"
    elif "nobel" in title_low:
        news_type = "award"
    elif any(kw in combined_low for kw in ['conference', 'workshop', 'school', 'meeting', 'plmcn', 'icp2dc']):
        news_type = "event_highlight"
    elif "honorary" in title_low or "professorship" in title_low:
        news_type = "award"
    else:
        news_type = "general"

    # Quality/confidence
    has_assets = bool(images) or bool(documents)
    has_links = bool(links)
    body_long_enough = len(body) >= 40
    title_suspicious = len(title) < 8 or is_navigation_or_footer_noise(title)

    if title and body_long_enough and (has_assets or has_links):
        confidence = 0.82
        review_status = "published"
        is_published = 1
    elif title and body_long_enough:
        confidence = 0.70
        review_status = "published"
        is_published = 1
    elif title and (has_assets or has_links) and not body_long_enough and not title_suspicious:
        confidence = 0.62
        review_status = "published"
        is_published = 1
    elif title_suspicious or not body_long_enough:
        confidence = 0.35
        review_status = "needs_review"
        is_published = 0
    else:
        confidence = 0.35
        review_status = "needs_review"
        is_published = 0

    # Force is_published=0 for needs_review
    if review_status == "needs_review":
        is_published = 0

    # Normalize images with roles
    normalized_images = []
    for i, img in enumerate(images):
        normalized_images.append({
            "url": img["url"],
            "alt_text": img.get("alt_text", ""),
            "caption": img.get("caption", ""),
            "role": "cover" if i == 0 else "gallery",
            "sort_order": i,
            "source_url": img.get("source_url", page_url),
        })

    # Normalize documents
    normalized_documents = []
    for i, doc in enumerate(documents):
        doc_kind = doc.get("kind", kind_from_url(doc["url"]))
        normalized_documents.append({
            "url": doc["url"],
            "text": doc.get("text", ""),
            "kind": doc_kind,
            "caption": doc.get("text", ""),
            "sort_order": i,
            "source_url": doc.get("source_url", page_url),
        })

    # Normalize links
    normalized_links = []
    for link in links:
        normalized_links.append({
            "url": link["url"],
            "text": link.get("text", ""),
            "role": link.get("role", "external"),
            "source_url": link.get("source_url", page_url),
        })

    # Combined assets for assets_unique.jsonl compatibility
    combined_assets = []
    for img in normalized_images:
        combined_assets.append({**img, "kind": "image"})
    for doc in normalized_documents:
        combined_assets.append({**doc})

    # Quality flags
    quality_flags = {
        "confidence": round(confidence, 2),
        "method": "home_dom_timeline_segmentation",
        "source_url": page_url,
        "has_body": bool(body),
        "body_length": len(body),
        "num_images": len(images),
        "num_documents": len(documents),
        "num_links": len(links),
        "has_date": bool(extracted_date),
        "date_precision": date_precision,
        "warnings": warnings,
    }

    return {
        "type": "news",
        "source": "aruba_remote_home",
        "source_url": page_url,
        "canonical_url": canonical_old_url(page_url),
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scraper_version": SCRAPER_VERSION,
        "title": title,
        "summary": summary[:500],
        "body": body[:8000],
        "date": extracted_date,
        "date_text": date_text,
        "date_precision": date_precision,
        "date_is_inferred": date_is_inferred,
        "sort_order": (index + 1) * 10,
        "news_type": news_type,
        "is_published": is_published,
        "review_status": review_status,
        "links": normalized_links,
        "images": normalized_images,
        "documents": normalized_documents,
        "assets": combined_assets,
        "extraction_warnings": warnings,
        "quality_flags_json": json.dumps(quality_flags, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# Output and main
# ---------------------------------------------------------------------------

def write_outputs(output_dir: Path, scrape: ArubaScrape) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    scrape.assets = unique_by_url(scrape.assets)
    scrape.links = unique_by_url(scrape.links)
    payload = {
        "source_url": BASE_URL,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "members": scrape.members,
        "pages": scrape.pages,
        "sponsors": scrape.sponsors,
    }
    (output_dir / "aruba_remote.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_jsonl(output_dir / "members.jsonl", scrape.members)
    write_jsonl(output_dir / "pages.jsonl", scrape.pages)
    write_jsonl(output_dir / "sponsors.jsonl", scrape.sponsors)
    write_jsonl(output_dir / "assets_unique.jsonl", scrape.assets)
    write_jsonl(output_dir / "links_by_page.jsonl", scrape.links)

    # News output
    write_jsonl(output_dir / "news.jsonl", scrape.news)

    # Event output
    write_jsonl(output_dir / "events_summary.jsonl", scrape.events)

    # Debug outputs
    debug_tokens = getattr(scrape, '_home_news_tokens', [])
    if debug_tokens:
        write_jsonl(output_dir / "home_news_tokens_debug.jsonl", debug_tokens)

    debug_segments = getattr(scrape, '_home_news_segments', [])
    if debug_segments:
        (output_dir / "home_news_segments_debug.json").write_text(
            json.dumps(debug_segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    report = {
        "source_url": BASE_URL,
        "pages_requested": len(DEFAULT_PATHS),
        "pages_importable": len(scrape.pages),
        "members_found": len([m for m in scrape.members if m.get("role") == "member"]),
        "scientific_council_found": len([m for m in scrape.members if m.get("role") == "scientific_council"]),
        "sponsors_found": len(scrape.sponsors),
        "assets_found": len(scrape.assets),
        "links_found": len(scrape.links),
        "news_found": len(scrape.news),
        "news_published": len([n for n in scrape.news if n.get("is_published") == 1]),
        "news_needs_review": len([n for n in scrape.news if n.get("review_status") == "needs_review"]),
        "news_images_found": sum(len(n.get("images", [])) for n in scrape.news),
        "news_documents_found": sum(len(n.get("documents", [])) for n in scrape.news),
        "news_links_found": sum(len(n.get("links", [])) for n in scrape.news),
        "events_found": len(scrape.events),
        "skipped_home_tokens_by_reason": getattr(scrape, '_skipped_tokens', {}),
        "discarded_member_rows": scrape.discarded_members,
        "fetches": scrape.fetches,
        "errors": scrape.errors,
    }
    (output_dir / "scrape_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def scrape(args: argparse.Namespace) -> ArubaScrape:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "curl/8.5.0",
        "Accept": "*/*",
        "Accept-Language": "en,it;q=0.9",
    })
    scrape_obj = ArubaScrape()
    paths = dict(DEFAULT_PATHS)
    paths.update({
        "manifesto": "/manifesto-of-solidarity",
        "manifesto_alt": "/mifp-manifesto-of-solidarity",
        "events_meetings": "/events-meetings",
        "events_schools": "/events-schools",
        "events_workshops": "/events-workshops",
        "events_conferences": "/events-conferences",
    })
    for key, path in paths.items():
        result = fetch_static(session, key, path, args.timeout)
        html = result.html
        if result.error:
            scrape_obj.errors.append({"url": result.url, "error": result.error})
        if args.render_fallback and (result.status_code >= 400 or len(html) < 1000):
            rendered = fetch_rendered(result.url, args.render_wait_ms, args.timeout + 10)
            if rendered:
                html = rendered
                result.source = "rendered_fallback"
        snapshot_name = f"{len(scrape_obj.fetches)+1:03d}_{slugify(key)}.html"
        if args.save_snapshots:
            snap_dir = args.output / "snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            (snap_dir / snapshot_name).write_text(html, encoding="utf-8", errors="ignore")
        result_hash = stable_hash(html) if html else None
        scrape_obj.fetches.append({
            "key": key,
            "url": result.url,
            "final_url": result.final_url,
            "canonical_url": canonical_old_url(result.final_url),
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "scraper_version": SCRAPER_VERSION,
            "status_code": result.status_code,
            "source": result.source,
            "elapsed_ms": result.elapsed_ms,
            "html_chars": len(html),
            "content_hash": result_hash,
            "snapshot": snapshot_name if args.save_snapshots else None,
            "error": result.error,
        })
        if not html or result.status_code >= 400:
            continue
        soup = soup_from(html)
        structured = extract_structured_page(soup, result.final_url)
        if structured["warnings"]:
            scrape_obj.errors.append({
                "url": result.final_url,
                "warning": ",".join(structured["warnings"]),
                "type": "content_warning",
            })
        links, assets = extract_links_and_assets(soup, result.final_url)
        scrape_obj.links.extend(links)
        scrape_obj.assets.extend(assets)
        if key == "members":
            parse_members_table(soup, result.final_url, scrape_obj)
        elif key == "scientific_council":
            parse_scientific_council(soup, result.final_url, scrape_obj)
        elif key in ("events_meetings", "events_schools", "events_workshops", "events_conferences"):
            event_type = key.replace("events_", "")
            event_type_singular = event_type[:-1]  # meetings->meeting, schools->school, etc.
            n_before = len(scrape_obj.events)
            parse_event_listings(key, soup, result.final_url, event_type_singular, scrape_obj)
            n_after = len(scrape_obj.events)
            log.info(f"  {key}: {n_after - n_before} events extracted")

        # Home news extraction
        if key == "home":
            raw_news_items, all_tokens, skipped = parse_home_news(soup, result.final_url)
            scrape_obj._home_news_tokens = all_tokens
            scrape_obj._home_news_segments = raw_news_items
            scrape_obj._skipped_tokens = skipped
            for i, raw in enumerate(raw_news_items):
                record = normalize_news_record(raw, i, result.final_url)
                scrape_obj.news.append(record)
                # Add images/documents to scrape.assets for assets_unique.jsonl
                for img in record.get("images", []):
                    scrape_obj.assets.append({
                        "url": img["url"],
                        "source_url": img.get("source_url", img["url"]),
                        "kind": "image",
                        "role": img.get("role", "gallery"),
                        "alt_text": img.get("alt_text", ""),
                        "caption": img.get("caption", ""),
                        "page_url": result.final_url,
                    })
                for doc in record.get("documents", []):
                    scrape_obj.assets.append({
                        "url": doc["url"],
                        "source_url": doc.get("source_url", doc["url"]),
                        "kind": doc.get("kind", "pdf"),
                        "role": "document",
                        "caption": doc.get("caption", ""),
                        "page_url": result.final_url,
                    })
            log.info(f"  Home news extraction: {len(raw_news_items)} news items, {len(all_tokens)} tokens, skipped: {skipped}")

        parse_policy_page(key, soup, result.final_url, scrape_obj, links, assets)
        parse_sponsor(key, soup, result.final_url, scrape_obj, links, assets)
        parse_basic_page(key, soup, result.final_url, scrape_obj, links, assets)
    member_seen: set[tuple[str, str, str]] = set()
    deduped_members: list[dict[str, Any]] = []
    for member in scrape_obj.members:
        key = (member["role"], member["normalized_name"], member["normalized_affiliation"])
        if key in member_seen:
            scrape_obj.discarded_members.append({
                "name": member["display_name"],
                "affiliation": member.get("affiliation") or "",
                "reason": "duplicate_across_pages",
                "source_url": json.loads(member["quality_flags_json"])["source_url"],
            })
            continue
        member_seen.add(key)
        deduped_members.append(member)
    scrape_obj.members = deduped_members

    # Dedup events across listing pages by normalized title
    event_seen: set[str] = set()
    deduped_events: list[dict[str, Any]] = []
    for event in scrape_obj.events:
        key = slugify(event.get('title', ''))
        if key in event_seen:
            continue
        event_seen.add(key)
        deduped_events.append(event)
    scrape_obj.events = deduped_events

    return scrape_obj


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    parser = argparse.ArgumentParser(description="Scrape the legacy Aruba/Flazio MIFP website.")
    parser.add_argument("--output", type=Path, default=Path("SCRAPERS/OUTPUTS"))
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--render-fallback", action="store_true")
    parser.add_argument("--render-wait-ms", type=int, default=5000)
    parser.add_argument("--save-snapshots", action="store_true")
    parser.add_argument("--include-manifesto-candidates", action="store_true")
    args = parser.parse_args(argv)
    result = scrape(args)
    write_outputs(args.output, result)
    report_path = args.output / "scrape_report.json"
    log.info(f"Aruba scrape complete: members={len(result.members)} sponsors={len(result.sponsors)} pages={len(result.pages)} news={len(result.news)} events={len(result.events)} assets={len(unique_by_url(result.assets))}")
    log.info(f"Report: {report_path}")
    fatal_errors = [err for err in result.errors if err.get("type") != "content_warning"]
    if fatal_errors:
        log.warning(f"Fatal Aruba scrape errors: {len(fatal_errors)}")
        return 1
    if result.errors:
        log.warning(f"Non-fatal Aruba scrape warnings: {len(result.errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

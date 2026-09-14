#!/usr/bin/env python3
"""
MIFP full scraper
=================

Crawls:
- https://old.mifp.eu/
- https://events.mifp.eu/ conference subsites

Outputs:
- JSONL complete page records, ordered by discovery order
- JSONL assets and links
- JSONL event summary, one row per conference/event site when possible
- TXT clean text per page
- Excel workbook with pages, events, assets, links, errors

Run:
    python scrape_remote.py --config config.remote.json

Notes:
- The crawler only follows HTML pages on allowed_page_domains.
- Asset URLs are kept even if they are on files.supersite.aruba.it or other configured domains.
- If events.mifp.eu has no public index, add missing conference home URLs to config.json -> event_seed_urls.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import ipaddress
import json
import logging
import mimetypes
import queue
import re
import shutil
import subprocess
import threading
import time
import traceback
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

warnings.simplefilter("ignore", InsecureRequestWarning)

log = logging.getLogger(__name__)

HTML_EXTENSIONS = {"", ".html", ".htm", ".php", ".asp", ".aspx"}
MONTHS: dict[str, int] = {}
for i, name in enumerate(calendar.month_name):
    if name:
        MONTHS[name.lower()] = i
for i, name in enumerate(calendar.month_abbr):
    if name:
        MONTHS[name.lower()] = i

NOISE_SELECTORS = """
script, style, noscript, svg script, canvas, iframe,
form, input, button, select, textarea,
[class*="cookie"], [id*="cookie"],
[class*="privacy-banner"], [id*="privacy-banner"],
[class*="popup"], [id*="popup"],
[class*="modal"], [id*="modal"]
"""

MENUISH_EXACT = {
    "home", "read more", "learn more", "discover", "to top", "register", "register now",
    "registration", "privacy policy", "cookie policy", "all rights reserved", "scopri di più",
}

# Section/menu labels that must never be accepted as an event place.
# Many events.mifp.eu pages are one-page conference sites with anchor menus;
# without this guard, a fallback such as "line before date" can incorrectly
# return "Social Program", "Accommodation", "Registration", etc. as place.
NON_PLACE_LABELS = {
    "home", "general information", "program", "program & book of abstracts",
    "book of abstracts", "submission", "sponsors", "sponsor", "organizing institution",
    "committee", "committees", "keynotes", "invited speakers", "speakers",
    "location", "venue", "accommodation", "accomodation", "social program",
    "registration", "register", "privacy", "privacy & cookies", "privacy policy",
    "contacts", "contact", "disclaimer", "important note", "visa regime for foreign citizens",
    "abstract submission", "important dates", "payment methods", "registration plans",
}

PLACE_BAD_WORDS = {
    "program", "registration", "abstract", "deadline", "notification", "submission",
    "privacy", "cookie", "sponsor", "committee", "speaker", "keynote", "accommodation",
    "accomodation", "social program", "payment", "fee", "poster", "oral presentation",
}


@dataclass
class Config:
    raw: dict[str, Any]
    config_path: Path
    output_dir: Path
    start_urls: list[str]
    event_seed_urls: list[str]
    curated_pages: list[dict[str, Any]]
    follow_old_internal_links: bool
    follow_event_internal_links: bool
    discover_events_from_old_links: bool
    restrict_event_internal_to_same_site: bool
    follow_external_event_sites: bool
    external_event_allowed_domains: set[str]
    allowed_page_domains: set[str]
    asset_domains_keep_as_urls: set[str]
    asset_extensions: set[str]
    image_extensions: set[str]
    document_extensions: set[str]
    workers: int
    max_pages: int
    max_depth: int
    timeout: int
    sleep_seconds: float
    verify_ssl: bool
    user_agent: str
    discover_sitemaps: bool
    download_assets: bool
    max_asset_download_mb: int
    download_only_asset_domains: set[str]
    render_old_mifp: bool
    render_wait_ms: int
    rendered_snapshot_dir: Path | None
    excel_filename: str
    max_cell_chars: int
    drop_exact_lines_case_insensitive: set[str]
    min_line_chars: int
    deduplicate_lines: bool
    logo_keywords: list[str]

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        config_path = Path(path).resolve()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        crawl = raw.get("crawl", {})
        downloads = raw.get("downloads", {})
        rendering = raw.get("rendering", {})
        excel = raw.get("excel", {})
        cleanup = raw.get("text_cleanup", {})
        event_extraction = raw.get("event_extraction", {})

        output_dir = Path(raw.get("output_dir", "output_mifp_scrape")).expanduser()
        if not output_dir.is_absolute():
            output_dir = (config_path.parent / output_dir).resolve()

        return cls(
            raw=raw,
            config_path=config_path,
            output_dir=output_dir,
            start_urls=list(raw.get("start_urls", [])),
            event_seed_urls=list(raw.get("event_seed_urls", [])),
            curated_pages=list(raw.get("curated_pages", [])),
            follow_old_internal_links=bool(crawl.get("follow_old_internal_links", False)),
            follow_event_internal_links=bool(crawl.get("follow_event_internal_links", True)),
            discover_events_from_old_links=bool(crawl.get("discover_events_from_old_links", True)),
            restrict_event_internal_to_same_site=bool(crawl.get("restrict_event_internal_to_same_site", True)),
            follow_external_event_sites=bool(crawl.get("follow_external_event_sites", True)),
            external_event_allowed_domains={
                d.lower().strip() for d in raw.get("external_event_allowed_domains", []) if str(d).strip()
            },
            allowed_page_domains={d.lower() for d in raw.get("allowed_page_domains", [])},
            asset_domains_keep_as_urls={d.lower() for d in raw.get("asset_domains_keep_as_urls", [])},
            asset_extensions={e.lower() for e in raw.get("asset_extensions", [])},
            image_extensions={e.lower() for e in raw.get("image_extensions", [])},
            document_extensions={e.lower() for e in raw.get("document_extensions", [])},
            workers=int(crawl.get("workers", 32)),
            max_pages=int(crawl.get("max_pages", 2000)),
            max_depth=int(crawl.get("max_depth", 8)),
            timeout=int(crawl.get("request_timeout_seconds", 35)),
            sleep_seconds=float(crawl.get("sleep_between_requests_seconds", 0.0)),
            verify_ssl=bool(crawl.get("verify_ssl", False)),
            user_agent=str(crawl.get("user_agent", "MIFP-Scraper/1.0")),
            discover_sitemaps=bool(raw.get("discover_sitemaps", True)),
            download_assets=bool(downloads.get("download_assets", False)),
            max_asset_download_mb=int(downloads.get("max_asset_download_mb", 100)),
            download_only_asset_domains={d.lower() for d in downloads.get("download_only_asset_domains", [])},
            render_old_mifp=bool(rendering.get("render_old_mifp", True)),
            render_wait_ms=int(rendering.get("render_wait_ms", 2500)),
            rendered_snapshot_dir=(
                (output_dir / str(rendering.get("snapshot_dir", "rendered_snapshots"))).resolve()
                if rendering.get("save_snapshots", True) else None
            ),
            excel_filename=str(excel.get("filename", "mifp_scrape_export.xlsx")),
            max_cell_chars=int(excel.get("max_cell_chars", 32000)),
            drop_exact_lines_case_insensitive={
                str(x).strip().lower() for x in cleanup.get("drop_exact_lines_case_insensitive", [])
            } | MENUISH_EXACT,
            min_line_chars=int(cleanup.get("min_line_chars", 2)),
            deduplicate_lines=bool(cleanup.get("deduplicate_lines", True)),
            logo_keywords=[str(x).lower() for x in event_extraction.get("prefer_logo_keywords", ["logo"])],
        )


def sha1_short(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:n]


def slugify(text: str, max_len: int = 90) -> str:
    text = re.sub(r"https?://", "", text, flags=re.I)
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-._")
    text = re.sub(r"-+", "-", text)
    return (text[:max_len] or "page").lower()


def normalize_url(url: str, base: str | None = None, keep_fragment: bool = False) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url or url.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    if base:
        url = urljoin(base, url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")

    # Drop tracking query params but preserve meaningful ones.
    query_items = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        lk = k.lower()
        if lk.startswith("utm_") or lk in {"fbclid", "gclid", "mc_cid", "mc_eid"}:
            continue
        query_items.append((k, v))
    query = urlencode(query_items, doseq=True)

    fragment = parsed.fragment if keep_fragment else ""
    return urlunparse((parsed.scheme.lower(), host, path, "", query, fragment))


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def extension_of(url: str) -> str:
    path = urlparse(url).path.lower()
    suffix = Path(path).suffix.lower()
    return suffix


def looks_like_html_page(url: str, cfg: Config) -> bool:
    ext = extension_of(url)
    if ext in cfg.asset_extensions:
        return False
    return ext in HTML_EXTENSIONS or not ext


EVENT_AUXILIARY_LINK_RE = re.compile(
    r"(program|programme|schedule|venue|location|speaker|organizer|organiser|committee|registration|abstract|pdf|book)",
    re.I,
)


def is_safe_external_event_url(url: str, cfg: Config) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    if not host or host not in cfg.external_event_allowed_domains:
        return False
    hostname = parsed.hostname or ""
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass
    return True


def looks_like_event_auxiliary_link(url: str, label: str = "") -> bool:
    ext = extension_of(url)
    if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}:
        return True
    parsed = urlparse(url)
    hay = " ".join([parsed.path, parsed.query, label or ""])
    return bool(EVENT_AUXILIARY_LINK_RE.search(hay))


def should_crawl_page(url: str, cfg: Config, depth: int) -> bool:
    if depth > cfg.max_depth:
        return False
    host = domain_of(url)
    if host not in cfg.allowed_page_domains and not is_safe_external_event_url(url, cfg):
        return False
    return looks_like_html_page(url, cfg)


def should_follow_discovered_link(parent_url: str, child_url: str, cfg: Config) -> bool:
    """Decide whether a discovered HTML link should be crawled.

    Curated old.mifp.eu pages are scraped exactly as configured: menu links are
    not followed by default. Links from those pages to events.mifp.eu are useful
    indexes, so they can be followed. Once inside an event subsite, we only crawl
    pages belonging to the same first path segment, e.g. PLMCN-2026/*.
    """
    parent_host = domain_of(parent_url)
    child_host = domain_of(child_url)

    if child_host == "old.mifp.eu":
        return cfg.follow_old_internal_links

    if child_host == "events.mifp.eu":
        if parent_host != "events.mifp.eu":
            return cfg.discover_events_from_old_links
        if not cfg.follow_event_internal_links:
            return False
        if cfg.restrict_event_internal_to_same_site:
            parent_key = event_site_key(parent_url)
            child_key = event_site_key(child_url)
            return bool(parent_key and child_key and parent_key == child_key)
        return True

    if cfg.follow_external_event_sites and is_safe_external_event_url(child_url, cfg):
        if parent_host in cfg.allowed_page_domains:
            return looks_like_html_page(child_url, cfg)
        if parent_host in cfg.external_event_allowed_domains:
            return child_host == parent_host and looks_like_event_auxiliary_link(child_url)

    return False


def is_asset_url(url: str, cfg: Config) -> bool:
    ext = extension_of(url)
    if ext in cfg.asset_extensions:
        return True
    host = domain_of(url)
    # files.supersite.aruba.it often serves assets without a stable extension in old pages.
    if host in cfg.asset_domains_keep_as_urls and "files.supersite.aruba.it" in host:
        return True
    return False


def classify_asset(url: str, cfg: Config) -> str:
    ext = extension_of(url)
    if ext in cfg.image_extensions:
        return "image"
    if ext in cfg.document_extensions:
        return "document"
    guessed, _ = mimetypes.guess_type(url)
    if guessed:
        if guessed.startswith("image/"):
            return "image"
        if guessed in {"application/pdf"} or guessed.startswith("application/"):
            return "document"
    return "asset"


def make_session(cfg: Config) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=cfg.workers * 2, pool_maxsize=cfg.workers * 4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": cfg.user_agent})
    return session


_thread_local = threading.local()


def get_thread_session(cfg: Config) -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = make_session(cfg)
        _thread_local.session = session
    return session


def response_text(resp: requests.Response) -> str:
    if not resp.encoding:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def fetch_url(url: str, cfg: Config) -> dict[str, Any]:
    session = get_thread_session(cfg)
    started = time.time()
    resp = session.get(url, timeout=cfg.timeout, verify=cfg.verify_ssl, allow_redirects=True)
    elapsed = round(time.time() - started, 3)
    content_type = resp.headers.get("content-type", "")
    final_url = normalize_url(resp.url) or resp.url
    return {
        "requested_url": url,
        "final_url": final_url,
        "status_code": resp.status_code,
        "content_type": content_type,
        "elapsed_seconds": elapsed,
        "headers": dict(resp.headers),
        "text": response_text(resp) if "text/html" in content_type.lower() or looks_like_html_page(final_url, cfg) else "",
        "bytes": len(resp.content or b""),
        "rendered": False,
    }


def fetch_rendered_url(url: str, cfg: Config) -> dict[str, Any] | None:
    """Render a page with a local browser when the static HTML is too thin.

    Aruba SuperSite pages expose much of the useful content in the browser DOM.
    Playwright is preferred when installed; otherwise Chromium's --dump-dom is
    enough for these static old.mifp.eu pages.
    """
    started = time.time()
    try:
        from playwright.sync_api import sync_playwright  # type: ignore

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
            page = browser.new_page(viewport={"width": 1280, "height": 1800}, user_agent=cfg.user_agent)
            response = page.goto(url, wait_until="networkidle", timeout=cfg.timeout * 1000)
            page.wait_for_timeout(cfg.render_wait_ms)
            html = page.content()
            final_url = normalize_url(page.url) or page.url
            status = response.status if response else 200
            browser.close()
            return {
                "requested_url": url,
                "final_url": final_url,
                "status_code": status,
                "content_type": "text/html; rendered=playwright",
                "elapsed_seconds": round(time.time() - started, 3),
                "headers": {},
                "text": html,
                "bytes": len(html.encode("utf-8", errors="ignore")),
                "rendered": True,
                "renderer": "playwright",
            }
    except Exception:
        pass

    chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if not chromium:
        return None
    try:
        proc = subprocess.run(
            [
                chromium,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--virtual-time-budget={max(cfg.render_wait_ms, 1000)}",
                "--dump-dom",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(cfg.timeout, 20),
        )
        html = proc.stdout or ""
        if proc.returncode != 0 or not html.strip():
            return None
        return {
            "requested_url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html; rendered=chromium",
            "elapsed_seconds": round(time.time() - started, 3),
            "headers": {},
            "text": html,
            "bytes": len(html.encode("utf-8", errors="ignore")),
            "rendered": True,
            "renderer": "chromium",
        }
    except Exception:
        return None


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def prune_old_mifp_chrome(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove SuperSite navigation/footer/cookie chrome before text extraction."""
    soup_copy = BeautifulSoup(str(soup), "lxml")
    for selector in [
        "script", "style", "noscript", "iframe", "form",
        "ul.nav", ".show-menu", "[data-type='footer']",
        "[class*='cookie']", "[id*='cookie']", "[class*='popup']", "[id*='popup']",
    ]:
        for tag in soup_copy.select(selector):
            tag.decompose()
    for tag in soup_copy.find_all("a"):
        label = clean_label(tag.get_text(" ", strip=True) or tag.get("title") or "")
        href = str(tag.get("href") or "")
        if label.lower() in MENUISH_EXACT and not re.search(r"\.(pdf|docx?|zip)\b", href, re.I):
            tag.decompose()
    return soup_copy


def visible_text_lines(soup: BeautifulSoup, cfg: Config) -> list[str]:
    soup_copy = BeautifulSoup(str(soup), "lxml")
    for tag in soup_copy.select(NOISE_SELECTORS):
        tag.decompose()
    for tag in soup_copy.find_all(style=True):
        if getattr(tag, "attrs", None) is None:
            continue
        style = tag.get("style") or ""
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", style, re.I):
            tag.decompose()
    for tag in soup_copy.select("[hidden], [aria-hidden='true']"):
        tag.decompose()

    raw = soup_copy.get_text("\n", strip=True)
    lines: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < cfg.min_line_chars:
            continue
        low = line.lower()
        if low in cfg.drop_exact_lines_case_insensitive:
            continue
        if cfg.deduplicate_lines:
            key = low
            if key in seen:
                continue
            seen.add(key)
        lines.append(line)
    return lines


def old_mifp_text_lines(soup: BeautifulSoup, cfg: Config) -> list[str]:
    return visible_text_lines(prune_old_mifp_chrome(soup), cfg)


def extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.get_text(strip=True):
        return re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True))
    h1 = soup.find("h1")
    if h1:
        return re.sub(r"\s+", " ", h1.get_text(" ", strip=True))
    return ""


def extract_headings(soup: BeautifulSoup) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$", re.I)):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if text:
            out.append({"level": tag.name.lower(), "text": text})
    return out


def parse_srcset(srcset: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for part in srcset.split(","):
        raw = part.strip().split(" ")[0].strip()
        u = normalize_url(raw, base_url)
        if u:
            urls.append(u)
    return urls


def extract_background_urls(style: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"url\((['\"]?)(.*?)\1\)", style or "", re.I):
        u = normalize_url(match.group(2), base_url)
        if u:
            urls.append(u)
    return urls


def clean_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def extract_links_and_assets(soup: BeautifulSoup, base_url: str, cfg: Config) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    links: list[dict[str, Any]] = []
    assets_by_url: dict[str, dict[str, Any]] = {}

    def add_asset(url: str, kind_hint: str, label: str = "", source_tag: str = "", source_attr: str = "") -> None:
        normalized = normalize_url(url, base_url)
        if not normalized:
            return
        if not is_asset_url(normalized, cfg) and kind_hint != "image":
            return
        host = domain_of(normalized)
        if host not in cfg.asset_domains_keep_as_urls and host not in cfg.allowed_page_domains:
            # Keep useful external documents too, but mark them as external.
            pass
        existing = assets_by_url.get(normalized)
        item = {
            "url": normalized,
            "download_url": normalized,
            "domain": host,
            "kind": kind_hint if kind_hint else classify_asset(normalized, cfg),
            "extension": extension_of(normalized),
            "label": clean_label(label),
            "source_tag": source_tag,
            "source_attr": source_attr,
            "is_external": host not in cfg.allowed_page_domains,
        }
        if existing:
            if item["label"] and item["label"] not in existing.get("label", ""):
                existing["label"] = clean_label((existing.get("label", "") + " | " + item["label"]).strip(" |"))
            return
        assets_by_url[normalized] = item

    for a in soup.find_all("a", href=True):
        href = normalize_url(a.get("href"), base_url)
        label = clean_label(a.get_text(" ", strip=True) or a.get("title") or a.get("aria-label") or "")
        if not href:
            continue
        host = domain_of(href)
        ext = extension_of(href)
        record = {
            "url": href,
            "domain": host,
            "label": label,
            "extension": ext,
            "is_internal_page": (
                (host in cfg.allowed_page_domains or is_safe_external_event_url(href, cfg))
                and looks_like_html_page(href, cfg)
            ),
            "is_asset": is_asset_url(href, cfg),
        }
        links.append(record)
        if record["is_asset"]:
            add_asset(href, classify_asset(href, cfg), label=label, source_tag="a", source_attr="href")

    for img in soup.find_all("img"):
        label = clean_label(img.get("alt") or img.get("title") or img.get("aria-label") or "")
        for attr in ["src", "data-src", "data-original", "data-lazy-src"]:
            if img.get(attr):
                add_asset(str(img.get(attr)), "image", label=label, source_tag="img", source_attr=attr)
        if img.get("srcset"):
            for u in parse_srcset(str(img.get("srcset")), base_url):
                add_asset(u, "image", label=label, source_tag="img", source_attr="srcset")

    for tag in soup.find_all(style=True):
        for u in extract_background_urls(tag.get("style") or "", base_url):
            add_asset(u, "image", label=clean_label(tag.get_text(" ", strip=True))[:160], source_tag=tag.name, source_attr="style")

    for source in soup.find_all("source"):
        if source.get("srcset"):
            for u in parse_srcset(str(source.get("srcset")), base_url):
                add_asset(u, "image", label="", source_tag="source", source_attr="srcset")
        if source.get("src"):
            add_asset(str(source.get("src")), "asset", label="", source_tag="source", source_attr="src")

    # Inline PDF URLs from raw HTML — catches JS, data-attr, onclick URLs
    for m in re.finditer(r'https?://[^\s"\'<>]+\.pdf', str(soup), re.IGNORECASE):
        url = str(m.group(0)).split("?")[0].split("#")[0]
        if url not in assets_by_url:
            add_asset(url, "pdf", label="", source_tag="inline", source_attr="text")

    return links, list(assets_by_url.values())


def remove_ordinal_suffixes(text: str) -> str:
    return re.sub(r"(\d{1,2})\s*(st|nd|rd|th)\b", r"\1", text, flags=re.I)


def month_num(month: str) -> int | None:
    return MONTHS.get(month.strip().lower().rstrip("."))


def safe_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def normalize_date_range(raw_text: str) -> dict[str, Any]:
    """Heuristic parser for conference date ranges in MIFP pages."""
    text = remove_ordinal_suffixes(raw_text)
    text = re.sub(r"\b(st|nd|rd|th)\b\s*-\s*", "-", text, flags=re.I)
    text = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip()

    patterns: list[tuple[str, str]] = [
        # 15-21 December, 2022 / 8-13 April 2025
        (r"(?P<d1>\d{1,2})\s*-\s*(?P<d2>\d{1,2})\s+(?:of\s+)?(?P<m1>[A-Za-z]+),?\s*(?P<y>20\d{2}|19\d{2})", "same_month"),
        # April 10-16, 2022 / April 12-15. 2026
        (r"(?P<m1>[A-Za-z]+)\s+(?P<d1>\d{1,2})\s*-\s*(?P<d2>\d{1,2})\s*[,.]?\s*(?P<y>20\d{2}|19\d{2})", "same_month"),
        # February 27 - March 2 2024
        (r"(?P<m1>[A-Za-z]+)\s+(?P<d1>\d{1,2})\s*-\s*(?P<m2>[A-Za-z]+)\s+(?P<d2>\d{1,2}),?\s*(?P<y>20\d{2}|19\d{2})", "cross_month"),
        # from 3 to 8 of February 2015
        (r"from\s+(?P<d1>\d{1,2})\s+to\s+(?P<d2>\d{1,2})\s+of\s+(?P<m1>[A-Za-z]+)\s+(?P<y>20\d{2}|19\d{2})", "same_month"),
        # 27 to 31 of May 2019
        (r"(?P<d1>\d{1,2})\s+to\s+(?P<d2>\d{1,2})\s+of\s+(?P<m1>[A-Za-z]+)\s+(?P<y>20\d{2}|19\d{2})", "same_month"),
        # July 15-22, 2018
        (r"(?P<m1>[A-Za-z]+)\s+(?P<d1>\d{1,2})\s*-\s*(?P<d2>\d{1,2})\s*,?\s*(?P<y>20\d{2}|19\d{2})", "same_month"),
    ]

    for pattern, kind in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        gd = m.groupdict()
        y = int(gd["y"])
        d1 = int(gd["d1"])
        d2 = int(gd["d2"])
        m1 = month_num(gd["m1"])
        m2 = month_num(gd.get("m2") or gd["m1"])
        if not m1 or not m2:
            continue
        start = safe_date(y, m1, d1)
        end = safe_date(y, m2, d2)
        if start and end:
            return {"start_date": start, "end_date": end, "date_raw": m.group(0), "date_confidence": 0.95}

    # Single date fallback, useful for important-date tables but lower confidence.
    single_patterns = [
        r"(?P<m1>[A-Za-z]+)\s+(?P<d1>\d{1,2}),?\s*(?P<y>20\d{2}|19\d{2})",
        r"(?P<d1>\d{1,2})\s+(?:of\s+)?(?P<m1>[A-Za-z]+),?\s*(?P<y>20\d{2}|19\d{2})",
    ]
    for pattern in single_patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        gd = m.groupdict()
        y = int(gd["y"])
        d1 = int(gd["d1"])
        m1 = month_num(gd["m1"])
        if not m1:
            continue
        one = safe_date(y, m1, d1)
        if one:
            return {"start_date": one, "end_date": one, "date_raw": m.group(0), "date_confidence": 0.55}

    return {"start_date": "", "end_date": "", "date_raw": "", "date_confidence": 0.0}


def event_site_key(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "events.mifp.eu":
        return ""
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return "events-root"
    return parts[0].lower()


def external_event_site_key(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    head = parts[0].lower() if parts else "home"
    return slugify(f"{parsed.netloc}-{head}", 80)


def likely_event_title(title: str, headings: list[dict[str, str]], lines: list[str], url: str) -> str:
    candidates: list[str] = []
    for h in headings:
        if h["level"] in {"h1", "h2"} and h["text"]:
            candidates.append(h["text"])
    if title:
        candidates.append(re.sub(r"\s+-\s+MIFP\s*$", "", title, flags=re.I).strip())
    for line in lines[:15]:
        if 4 <= len(line) <= 140 and not line.lower().startswith(("home", "image", "copyright")):
            candidates.append(line)
    # Prefer a candidate containing an acronym/year, otherwise first meaningful one.
    for c in candidates:
        if re.search(r"\b(PLMCN|TERAMETANANO|QLIN|ICP2DC|2DCP|March Meeting|Workshop|Conference|School)\b", c, re.I):
            return c.strip()
    return (candidates[0].strip() if candidates else slugify(event_site_key(url)).upper())


def strip_date_from_place_candidate(text: str) -> str:
    """Remove common conference date fragments from a place candidate."""
    t = remove_ordinal_suffixes(clean_label(text))
    t = t.replace("–", "-").replace("—", "-")
    # City - Country, 12-15 April 2026 -> City - Country
    t = re.sub(
        r"[,|]?\s*\b\d{1,2}\s*(?:-|to)\s*\d{1,2}\s+(?:of\s+)?[A-Za-z]+,?\s*(?:19|20)\d{2}\b.*$",
        "",
        t,
        flags=re.I,
    )
    # City - Country 8-13 April 2025 -> City - Country
    t = re.sub(
        r"\s+\b\d{1,2}\s*(?:-|to)\s*\d{1,2}\s+(?:of\s+)?[A-Za-z]+,?\s*(?:19|20)\d{2}\b.*$",
        "",
        t,
        flags=re.I,
    )
    # City - Country, April 8-13 2025 -> City - Country
    t = re.sub(
        r"[,|]?\s*\b[A-Za-z]+\s+\d{1,2}\s*(?:-|to)\s*\d{1,2},?\s*(?:19|20)\d{2}\b.*$",
        "",
        t,
        flags=re.I,
    )
    # Remove explicit label left-overs.
    t = re.sub(r"^\s*(location|venue|place|address)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"^the\s+", "", t, flags=re.I)
    # Xiamen University in Xiamen - China -> Xiamen University, Xiamen - China
    m_in = re.match(r"^(.+?)\s+in\s+([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'.\s]+\s+-\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'.\s]+)$", t)
    if m_in and not m_in.group(1).strip().lower().endswith(("held", "place")):
        t = f"{m_in.group(1).strip()}, {m_in.group(2).strip()}"
    return clean_label(t.strip(" |,-;"))


def is_bad_place_candidate(text: str) -> bool:
    c = strip_date_from_place_candidate(text)
    low = c.lower().strip(" #:|-/")
    if not c or len(c) < 3:
        return True
    if low in NON_PLACE_LABELS:
        return True
    if low.startswith(("image:", "download", "view ", "go to ", "click ")):
        return True
    if "will be held" in low or "will take place" in low:
        return True
    if low.startswith("international conference") or "conference on" in low:
        return True
    if "conference" in low and len(c) > 80 and "center" not in low and "centre" not in low:
        return True
    if re.fullmatch(r"[#\-*|\s]+", c):
        return True
    if len(c) > 180:
        return True
    # A single generic section title is almost never a place.
    if any(bad == low for bad in PLACE_BAD_WORDS):
        return True
    # Strong negative: phrase is mostly a navigation string.
    if " | " in c and sum(1 for x in c.split("|") if x.strip().lower() in NON_PLACE_LABELS) >= 2:
        return True
    return False


def score_place_candidate(candidate: str, source: str, raw: str) -> tuple[str, float, str] | None:
    place = strip_date_from_place_candidate(candidate)
    if is_bad_place_candidate(place):
        return None

    low = place.lower()
    score = 0.35
    if source in {"location_label", "venue_label", "place_label"}:
        score = 0.93
    elif source == "address_label":
        score = 0.72
    elif source == "hero_city_country":
        score = 0.88
    elif source == "held_sentence":
        score = 0.86
    elif source == "section_after_heading":
        score = 0.68
    elif source == "near_date":
        score = 0.55

    # Positive location signals.
    if re.search(r"\b[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'.]+\s+-\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'.]+", place):
        score += 0.08
    if re.search(r"\b(university|college|institute|center|centre|hotel|campus|academy|laborator|quantum|address|street|st\.?|road|rd\.?|avenue|via)\b", low, re.I):
        score += 0.06
    if "," in place:
        score += 0.03

    # Negative signals that indicate schedule/deadline text rather than place.
    if any(bad in low for bad in PLACE_BAD_WORDS):
        score -= 0.25
    if re.search(r"\b(deadline|opening|closing|notification|refunding|early registration)\b", low):
        score -= 0.35

    score = max(0.0, min(0.99, score))
    if score < 0.45:
        return None
    return place, score, raw


def extract_place(lines: list[str], full_text: str) -> tuple[str, float, str]:
    """Extract event place without confusing menu/section labels for places.

    The events.mifp.eu pages often contain anchor menus such as:
    "Location | Venue | Accommodation | Social Program | Registration".
    A naive fallback around the first date can therefore pick "Social Program".
    This function collects multiple candidates, rejects section labels, and then
    returns the highest-scoring real location/venue candidate.
    """
    candidates: list[tuple[str, float, str]] = []

    def add(candidate: str, source: str, raw: str) -> None:
        scored = score_place_candidate(candidate, source, raw)
        if scored:
            candidates.append(scored)

    # Direct labels, especially "At a glance" blocks:
    #   Location: Quantum College, Yerevan - Armenia
    for i, line in enumerate(lines[:140]):
        m = re.search(r"\b(Location|Venue|Place|Address)\s*:\s*(.+)$", line, re.I)
        if m:
            label = m.group(1).lower()
            source = "address_label" if label == "address" else f"{label}_label"
            add(m.group(2), source, line)
            continue

        low_line = line.strip().lower().strip("#:")
        if low_line in {"location", "venue", "place", "address"}:
            for j in range(i + 1, min(i + 5, len(lines))):
                nxt = lines[j]
                if is_bad_place_candidate(nxt):
                    continue
                source = "address_label" if low_line == "address" else "section_after_heading"
                add(nxt, source, line + " / " + nxt)
                break

    # Hero line patterns:
    #   Yerevan - Armenia, 12th - 15th April, 2026
    #   Xiamen - China 8th-13th April 2025
    for line in lines[:80]:
        raw = line
        cleaned = remove_ordinal_suffixes(line).replace("–", "-").replace("—", "-")
        if normalize_date_range(cleaned).get("start_date"):
            # Take the part before a date, if it still looks location-like.
            add(cleaned, "hero_city_country", raw)
        # A pure city-country line immediately followed by a date line.
        if re.fullmatch(r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'.\s]+\s*-\s*[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'.\s]+", clean_label(line)):
            add(line, "hero_city_country", raw)

    # Sentences with "held/take place". Capture enough but stop before dates.
    sentence_patterns = [
        r"(?:conference|workshop|school|meeting|event)\s+will\s+(?:take\s+place|be\s+held)\s+(?:at|in)\s+(?P<place>[^.\n]+?)(?:\s+on\s+|\s+from\s+|\s+during\s+|\.|$)",
        r"will\s+take\s+place\s+(?:at|in)\s+(?P<place>[^.\n]+?)(?:\s+on\s+|\s+from\s+|\s+during\s+|\.|$)",
        r"will\s+be\s+held\s+(?:at|in)\s+(?P<place>[^.\n]+?)(?:\s+on\s+|\s+from\s+|\s+during\s+|\.|$)",
        r"held\s+(?:at|in)\s+(?P<place>[A-Z][^.\n]+?)(?:\s+on\s+|\s+from\s+|\s+during\s+|\.|$)",
    ]
    for pattern in sentence_patterns:
        for m in re.finditer(pattern, full_text, re.I):
            add(m.group("place"), "held_sentence", m.group(0))

    # Nearby-date fallback. Use only if the candidate is not a known section/menu label.
    for i, line in enumerate(lines[:80]):
        if not normalize_date_range(line).get("start_date"):
            continue
        same_line_place = strip_date_from_place_candidate(line)
        add(same_line_place, "near_date", line)
        if i > 0:
            add(lines[i - 1], "near_date", lines[i - 1] + " / " + line)

    if not candidates:
        return "", 0.0, ""

    # Dedupe while preserving the highest score per normalized candidate.
    best_by_key: dict[str, tuple[str, float, str]] = {}
    for place, score, raw in candidates:
        key = re.sub(r"\W+", "", place.lower())
        if key not in best_by_key or score > best_by_key[key][1]:
            best_by_key[key] = (place, score, raw)

    best = max(best_by_key.values(), key=lambda x: x[1])
    return best


def select_logo_url(assets: list[dict[str, Any]], cfg: Config) -> tuple[str, list[str]]:
    image_assets = [a for a in assets if a.get("kind") == "image"]
    scored: list[tuple[int, str]] = []
    for a in image_assets:
        hay = " ".join([a.get("url", ""), a.get("label", "")]).lower()
        score = 0
        for kw in cfg.logo_keywords:
            if kw and kw in hay:
                score += 10
        # Penalize obvious photos/backgrounds.
        if any(x in hay for x in ["background", "hero", "hotel", "venue", "panorama", "participants", "speaker"]):
            score -= 4
        if score > 0:
            scored.append((score, a["url"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [u for _, u in scored]
    if candidates:
        return candidates[0], candidates[:10]
    if image_assets:
        return image_assets[0]["url"], [a["url"] for a in image_assets[:10]]
    return "", []


def extract_event_metadata(url: str, title: str, headings: list[dict[str, str]], lines: list[str], assets: list[dict[str, Any]], cfg: Config) -> dict[str, Any]:
    host = domain_of(url)
    is_external_event_site = host != "events.mifp.eu" and host in cfg.external_event_allowed_domains
    if host != "events.mifp.eu" and not is_external_event_site:
        return {}
    full_text = "\n".join(lines)
    event_title = likely_event_title(title, headings, lines, url)

    # Date search: favor early hero lines and sentences containing conference terms.
    date_candidates: list[str] = []
    date_candidates.extend(lines[:80])
    for sent in re.split(r"(?<=[.!?])\s+|\n+", full_text):
        if re.search(r"conference|workshop|school|meeting|take place|held", sent, re.I):
            date_candidates.append(sent)

    best_date = {"start_date": "", "end_date": "", "date_raw": "", "date_confidence": 0.0}
    best_date_score = 0.0
    for candidate in date_candidates:
        parsed = normalize_date_range(candidate)
        parsed_score = float(parsed["date_confidence"])
        if parsed.get("start_date") and parsed.get("end_date") and parsed["start_date"] != parsed["end_date"]:
            parsed_score += 0.05
        if parsed_score > best_date_score:
            best_date = parsed
            best_date_score = parsed_score

    place, place_conf, place_raw = extract_place(lines, full_text)
    logo_url, logo_candidates = select_logo_url(assets, cfg)

    confidence = 0.0
    if event_title:
        confidence += 0.25
    if best_date.get("start_date"):
        confidence += 0.35 * float(best_date.get("date_confidence", 0.0))
    if place:
        confidence += 0.25 * place_conf
    if logo_url:
        confidence += 0.15

    return {
        "event_site_key": external_event_site_key(url) if is_external_event_site else event_site_key(url),
        "event_title": event_title,
        "place": place,
        "start_date": best_date.get("start_date", ""),
        "end_date": best_date.get("end_date", ""),
        "date_raw": best_date.get("date_raw", ""),
        "logo_url": logo_url,
        "logo_candidates": logo_candidates,
        "source_url": url,
        "external_source_url": url if is_external_event_site else "",
        "extracted_from_external_site": bool(is_external_event_site),
        "confidence": round(confidence, 3),
        "extraction_notes": {
            "date_confidence": best_date.get("date_confidence", 0.0),
            "place_confidence": place_conf,
            "place_raw": place_raw,
        },
    }


def discover_sitemap_urls(cfg: Config) -> list[str]:
    if not cfg.discover_sitemaps:
        return []
    session = make_session(cfg)
    urls: list[str] = []
    candidates = []
    for start in cfg.start_urls + cfg.event_seed_urls:
        parsed = urlparse(start)
        root = f"{parsed.scheme}://{parsed.netloc}/"
        candidates.extend([urljoin(root, "sitemap.xml"), urljoin(root, "robots.txt")])
    seen_candidates: set[str] = set()
    for candidate in candidates:
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        try:
            resp = session.get(candidate, timeout=cfg.timeout, verify=cfg.verify_ssl)
            if resp.status_code >= 400:
                continue
            text = response_text(resp)
            if candidate.endswith("robots.txt"):
                for line in text.splitlines():
                    m = re.match(r"\s*Sitemap\s*:\s*(\S+)", line, re.I)
                    if m:
                        seen_candidates.add(m.group(1).strip())
                        try:
                            sr = session.get(m.group(1).strip(), timeout=cfg.timeout, verify=cfg.verify_ssl)
                            if sr.status_code < 400:
                                text += "\n" + response_text(sr)
                        except Exception:
                            pass
            for m in re.finditer(r"<loc>\s*(.*?)\s*</loc>", text, re.I | re.S):
                u = normalize_url(m.group(1).strip())
                if u and should_crawl_page(u, cfg, 0):
                    urls.append(u)
        except Exception:
            continue
    # Keep order, dedupe.
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


@dataclass
class CrawlState:
    cfg: Config
    q: queue.Queue = field(default_factory=queue.Queue)
    seen: set[str] = field(default_factory=set)
    depth_by_url: dict[str, int] = field(default_factory=dict)
    order_by_url: dict[str, int] = field(default_factory=dict)
    meta_by_url: dict[str, dict[str, Any]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    active_count: int = 0
    next_order: int = 1
    lock: threading.RLock = field(default_factory=threading.RLock)
    stop_event: threading.Event = field(default_factory=threading.Event)

    def enqueue(self, url: str, depth: int, total_bar: tqdm | None = None, meta: dict[str, Any] | None = None) -> bool:
        normalized = normalize_url(url)
        if not normalized:
            return False
        if not should_crawl_page(normalized, self.cfg, depth):
            return False
        with self.lock:
            if len(self.seen) >= self.cfg.max_pages:
                return False
            if normalized in self.seen:
                return False
            self.seen.add(normalized)
            self.depth_by_url[normalized] = depth
            self.order_by_url[normalized] = self.next_order
            self.meta_by_url[normalized] = dict(meta or {})
            self.next_order += 1
            self.q.put(normalized)
            if total_bar is not None:
                total_bar.total = (total_bar.total or 0) + 1
                total_bar.refresh()
            return True

    def should_stop(self) -> bool:
        with self.lock:
            return self.q.empty() and self.active_count == 0


def scrape_page(url: str, cfg: Config, order: int, depth: int, seed_meta: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
    try:
        fetched = fetch_url(url, cfg)
        html = fetched.get("text") or ""
        final_url = fetched.get("final_url") or url
        if not html:
            return None, [], {
                "url": url,
                "final_url": final_url,
                "status_code": fetched.get("status_code"),
                "error": "non_html_or_empty_response",
                "content_type": fetched.get("content_type", ""),
            }

        soup = soup_from_html(html)
        static_lines = old_mifp_text_lines(soup, cfg) if domain_of(final_url) == "old.mifp.eu" else visible_text_lines(soup, cfg)
        render_used = False
        renderer = ""
        render_reason = ""
        if cfg.render_old_mifp and domain_of(final_url) == "old.mifp.eu" and len(static_lines) < 8:
            rendered = fetch_rendered_url(final_url, cfg)
            if rendered and (rendered.get("text") or ""):
                rendered_soup = soup_from_html(rendered["text"])
                rendered_lines = old_mifp_text_lines(rendered_soup, cfg)
                rendered_blob = "\n".join(rendered_lines[:80]).lower()
                rendered_is_admin_shell = any(x in rendered_blob for x in [
                    "cambia immagine", "notifiche", "segna tutte come gia lette",
                    "informazioni su questo sito", "dominio:", "amministratore:",
                ])
                if len(rendered_lines) > len(static_lines) and not rendered_is_admin_shell:
                    fetched = rendered
                    html = rendered["text"]
                    soup = rendered_soup
                    final_url = rendered.get("final_url") or final_url
                    render_used = True
                    renderer = str(rendered.get("renderer") or "browser")
                    render_reason = f"static_lines={len(static_lines)} rendered_lines={len(rendered_lines)}"
                    if cfg.rendered_snapshot_dir:
                        cfg.rendered_snapshot_dir.mkdir(parents=True, exist_ok=True)
                        (cfg.rendered_snapshot_dir / f"{order:05d}_{slugify(final_url)}.html").write_text(html, encoding="utf-8")
        title = extract_title(soup)
        headings = extract_headings(soup)
        lines = old_mifp_text_lines(soup, cfg) if domain_of(final_url) == "old.mifp.eu" else visible_text_lines(soup, cfg)
        clean_text = "\n".join(lines)
        link_soup = prune_old_mifp_chrome(soup) if domain_of(final_url) == "old.mifp.eu" else soup
        links, assets = extract_links_and_assets(link_soup, final_url, cfg)

        discovered: list[str] = []
        for link in links:
            if link.get("is_internal_page"):
                discovered.append(link["url"])

        metadata = extract_event_metadata(final_url, title, headings, lines, assets, cfg)
        parsed = urlparse(final_url)
        if parsed.netloc.lower() == "events.mifp.eu":
            source_group = "events"
        elif parsed.netloc.lower() in cfg.external_event_allowed_domains:
            source_group = "event_external"
        else:
            source_group = "old"
        seed_meta = seed_meta or {}
        configured_section = seed_meta.get("section", "events" if source_group in {"events", "event_external"} else "uncategorized_old")
        configured_kind = seed_meta.get("kind", "event_site_page" if source_group == "events" else ("external_event_page" if source_group == "event_external" else "old_page"))
        configured_title = seed_meta.get("title", "")

        record = {
            "order": order,
            "depth": depth,
            "source_group": source_group,
            "configured_section": configured_section,
            "configured_kind": configured_kind,
            "configured_title": configured_title,
            "url": url,
            "final_url": final_url,
            "domain": parsed.netloc.lower(),
            "path": parsed.path,
            "event_site_key": metadata.get("event_site_key") or event_site_key(final_url),
            "status_code": fetched.get("status_code"),
            "content_type": fetched.get("content_type"),
            "elapsed_seconds": fetched.get("elapsed_seconds"),
            "bytes": fetched.get("bytes"),
            "title": title,
            "h1": next((h["text"] for h in headings if h["level"] == "h1"), ""),
            "headings": headings,
            "text": clean_text,
            "text_lines": lines,
            "text_sha1": sha1_short(clean_text, 40),
            "links": links,
            "assets": assets,
            "asset_count": len(assets),
            "link_count": len(links),
            "event_metadata": metadata,
            "rendered": render_used,
            "renderer": renderer,
            "render_reason": render_reason,
            "main_text_line_count": len(lines),
        }
        return record, discovered, None
    except Exception as exc:  # noqa: BLE001 - scraper must keep going.
        return None, [], {"url": url, "error": repr(exc), "type": type(exc).__name__, "traceback": traceback.format_exc(limit=8)}


def worker_loop(worker_id: int, state: CrawlState, total_bar: tqdm, worker_bar: tqdm) -> None:
    cfg = state.cfg
    while not state.stop_event.is_set():
        try:
            url = state.q.get(timeout=0.5)
        except queue.Empty:
            if state.should_stop():
                return
            continue

        with state.lock:
            state.active_count += 1
            depth = state.depth_by_url.get(url, 0)
            order = state.order_by_url.get(url, 0)
            seed_meta = state.meta_by_url.get(url, {})

        worker_bar.reset(total=1)
        worker_bar.set_description_str(f"W{worker_id:02d}")
        worker_bar.set_postfix_str(slugify(url, 50))
        try:
            record, discovered, error = scrape_page(url, cfg, order=order, depth=depth, seed_meta=seed_meta)
            new_count = 0
            if record:
                with state.lock:
                    state.records.append(record)
                for child in discovered:
                    if not should_follow_discovered_link(final_url := record.get("final_url", url), child, cfg):
                        continue
                    child_meta = {}
                    child_host = domain_of(child)
                    if child_host == "events.mifp.eu":
                        child_meta = {
                            "section": "event_sites",
                            "kind": "event_site_page",
                            "title": f"Event site: {event_site_key(child) or child}",
                            "discovered_from": final_url,
                        }
                    elif child_host in cfg.external_event_allowed_domains:
                        child_meta = {
                            "section": "event_sites",
                            "kind": "external_event_page",
                            "title": f"External event site: {external_event_site_key(child) or child}",
                            "discovered_from": final_url,
                        }
                    if state.enqueue(child, depth + 1, total_bar=total_bar, meta=child_meta):
                        new_count += 1
                worker_bar.set_postfix_str(f"ok assets={record.get('asset_count', 0)} +{new_count} {slugify(url, 35)}")
            if error:
                with state.lock:
                    state.errors.append({"order": order, "depth": depth, **error})
                worker_bar.set_postfix_str(f"err {slugify(url, 42)}")
            if cfg.sleep_seconds > 0:
                time.sleep(cfg.sleep_seconds)
        finally:
            total_bar.update(1)
            worker_bar.update(1)
            with state.lock:
                state.active_count -= 1
            state.q.task_done()


def crawl_all(cfg: Config) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    state = CrawlState(cfg=cfg)

    seed_items: list[tuple[str, dict[str, Any]]] = []

    for idx, page in enumerate(cfg.curated_pages, start=1):
        url = str(page.get("url", "")).strip()
        if not url:
            continue
        meta = {
            "seed_order": idx,
            "section": page.get("section", "curated"),
            "kind": page.get("kind", "curated_page"),
            "title": page.get("title", ""),
            "is_curated_seed": True,
        }
        seed_items.append((url, meta))

    for url in cfg.start_urls:
        seed_items.append((url, {"section": "start_urls", "kind": "start_url", "title": url}))

    for url in cfg.event_seed_urls:
        seed_items.append((url, {
            "section": "event_sites",
            "kind": "event_site_home",
            "title": f"Event site: {event_site_key(url) or url}",
            "is_event_seed": True,
        }))

    for url in discover_sitemap_urls(cfg):
        seed_items.append((url, {
            "section": "event_sites" if domain_of(url) == "events.mifp.eu" else "sitemap",
            "kind": "sitemap_page",
            "title": url,
            "discovered_from": "sitemap",
        }))

    log.info(f"Seed URLs: {len(seed_items)}")

    total_bar = tqdm(total=0, position=0, desc="TOTAL", unit="page", dynamic_ncols=True)
    for url, meta in seed_items:
        state.enqueue(url, 0, total_bar=total_bar, meta=meta)

    worker_bars = [
        tqdm(total=1, position=i + 1, desc=f"W{i + 1:02d}", unit="page", leave=False, dynamic_ncols=True)
        for i in range(cfg.workers)
    ]
    threads: list[threading.Thread] = []
    for i in range(cfg.workers):
        t = threading.Thread(target=worker_loop, args=(i + 1, state, total_bar, worker_bars[i]), daemon=True)
        threads.append(t)
        t.start()

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log.error("\nInterrupted. Writing partial results...")
        state.stop_event.set()
        for t in threads:
            t.join(timeout=2)
    finally:
        for b in worker_bars:
            b.close()
        total_bar.close()

    records = sorted(state.records, key=lambda r: int(r.get("order", 0)))
    errors = sorted(state.errors, key=lambda r: int(r.get("order", 0)))
    return records, errors


def json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, default=json_default) + "\n")


def flatten_links(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in records:
        for idx, link in enumerate(r.get("links", []), start=1):
            rows.append({
                "page_order": r.get("order"),
                "page_url": r.get("final_url"),
                "page_title": r.get("title"),
                "link_order": idx,
                **link,
            })
    return rows


def flatten_assets(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_page_asset: set[tuple[str, str]] = set()
    for r in records:
        for idx, asset in enumerate(r.get("assets", []), start=1):
            key = (str(r.get("final_url")), str(asset.get("url")))
            if key in seen_page_asset:
                continue
            seen_page_asset.add(key)
            rows.append({
                "page_order": r.get("order"),
                "page_url": r.get("final_url"),
                "page_title": r.get("title"),
                "asset_order": idx,
                **asset,
            })
    return rows


def dedupe_assets(asset_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for row in asset_rows:
        url = row.get("url")
        if not url:
            continue
        current = by_url.get(url)
        if not current:
            by_url[url] = {
                "url": url,
                "download_url": row.get("download_url", url),
                "domain": row.get("domain", ""),
                "kind": row.get("kind", ""),
                "extension": row.get("extension", ""),
                "labels": row.get("label", ""),
                "first_page_order": row.get("page_order"),
                "first_page_url": row.get("page_url"),
                "used_on_pages_count": 1,
            }
        else:
            current["used_on_pages_count"] += 1
            lab = row.get("label", "")
            if lab and lab not in current.get("labels", ""):
                current["labels"] = clean_label((current.get("labels", "") + " | " + lab).strip(" |"))
    return sorted(by_url.values(), key=lambda x: (str(x.get("kind", "")), str(x.get("url", ""))))


def best_event_summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r.get("source_group") not in {"events", "event_external"} and not (r.get("event_metadata") or {}).get("extracted_from_external_site"):
            continue
        meta = r.get("event_metadata") or {}
        key = meta.get("event_site_key") or r.get("event_site_key") or event_site_key(r.get("final_url", ""))
        if key:
            grouped[key].append(r)

    summaries: list[dict[str, Any]] = []
    for key, pages in grouped.items():
        # Prefer root-ish page and best confidence.
        def score_page(r: dict[str, Any]) -> tuple[float, int]:
            meta = r.get("event_metadata") or {}
            path = urlparse(r.get("final_url", "")).path.strip("/")
            depth_bonus = 0.2 if path.lower() == key.lower() else 0.0
            return (float(meta.get("confidence", 0.0)) + depth_bonus, -int(r.get("order", 0)))

        best = max(pages, key=score_page)
        meta = best.get("event_metadata") or {}
        if not meta:
            meta = extract_event_metadata(best.get("final_url", ""), best.get("title", ""), best.get("headings", []), best.get("text_lines", []), best.get("assets", []), Config.load(best.get("_config_path", "config.json"))) if False else {}
        summaries.append({
            "event_site_key": key,
            "home_url": best.get("final_url", ""),
            "title_name": meta.get("event_title", best.get("title", "")),
            "place": meta.get("place", ""),
            "start_date": meta.get("start_date", ""),
            "end_date": meta.get("end_date", ""),
            "date_raw": meta.get("date_raw", ""),
            "description": ("\n".join(best.get("text_lines") or [])[:500]).strip(),
            "logo_url": meta.get("logo_url", ""),
            "logo_candidates": " | ".join(meta.get("logo_candidates", [])[:8]) if isinstance(meta.get("logo_candidates"), list) else "",
            "source_url": meta.get("source_url", best.get("final_url", "")),
            "external_source_url": meta.get("external_source_url", ""),
            "extracted_from_external_site": bool(meta.get("extracted_from_external_site")),
            "confidence": meta.get("confidence", 0.0),
            "pages_in_site": len(pages),
            "page_orders": ",".join(str(p.get("order")) for p in sorted(pages, key=lambda x: int(x.get("order", 0)))),
        })

    def sort_key(row: dict[str, Any]) -> tuple[str, str]:
        return (row.get("start_date") or "9999-99-99", row.get("title_name") or "")

    return sorted(summaries, key=sort_key, reverse=True)


def maybe_download_assets(asset_rows: list[dict[str, Any]], cfg: Config) -> None:
    if not cfg.download_assets:
        return
    out_dir = cfg.output_dir / "assets_downloaded"
    out_dir.mkdir(parents=True, exist_ok=True)
    session = make_session(cfg)
    total = len(asset_rows)
    with tqdm(total=total, desc="ASSETS", unit="asset", dynamic_ncols=True) as bar:
        for row in asset_rows:
            url = row.get("url")
            if not url:
                bar.update(1)
                continue
            host = domain_of(url)
            if cfg.download_only_asset_domains and host not in cfg.download_only_asset_domains:
                bar.update(1)
                continue
            try:
                head = session.head(url, timeout=cfg.timeout, verify=cfg.verify_ssl, allow_redirects=True)
                size = int(head.headers.get("content-length", "0") or "0")
                if size and size > cfg.max_asset_download_mb * 1024 * 1024:
                    row["download_status"] = "skipped_too_large"
                    bar.update(1)
                    continue
                resp = session.get(url, timeout=cfg.timeout, verify=cfg.verify_ssl, stream=True)
                if resp.status_code >= 400:
                    row["download_status"] = f"http_{resp.status_code}"
                    bar.update(1)
                    continue
                ext = extension_of(url) or mimetypes.guess_extension(resp.headers.get("content-type", "").split(";")[0]) or ".bin"
                filename = f"{sha1_short(url, 16)}_{slugify(Path(urlparse(url).path).name or 'asset', 80)}"
                if not filename.lower().endswith(ext.lower()):
                    filename += ext
                dest = out_dir / filename
                with dest.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                if not downloaded_asset_is_valid(dest, row.get("kind"), resp.headers.get("content-type")):
                    dest.unlink(missing_ok=True)
                    row["download_status"] = "invalid_payload"
                    bar.update(1)
                    continue
                row["download_status"] = "downloaded"
                row["local_path"] = str(dest.relative_to(cfg.output_dir))
            except Exception as exc:  # noqa: BLE001
                row["download_status"] = f"error:{type(exc).__name__}"
            bar.update(1)


def downloaded_asset_is_valid(path: Path, kind: str | None = None, content_type: str | None = None) -> bool:
    try:
        head = path.read_bytes()[:512]
    except OSError:
        return False
    if not head:
        return False
    stripped = head.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<html" in stripped[:256]:
        return False
    ext = path.suffix.lower()
    kind = (kind or "").lower()
    ctype = (content_type or "").split(";", 1)[0].lower()
    if kind == "image" or ctype.startswith("image/"):
        return (
            head.startswith(b"\xff\xd8\xff")
            or head.startswith(b"\x89PNG\r\n\x1a\n")
            or head.startswith((b"GIF87a", b"GIF89a"))
            or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
            or head.lstrip().startswith(b"<svg")
        )
    if kind == "pdf" or ctype == "application/pdf" or ext == ".pdf":
        return head.startswith(b"%PDF")
    if kind == "document" or ext in {".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}:
        if ext == ".pdf":
            return head.startswith(b"%PDF")
        return head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"))
    return True


def truncate_excel_value(value: Any, max_chars: int) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and len(value) > max_chars:
        return value[: max_chars - 30] + "\n...[TRUNCATED_FOR_EXCEL]"
    return value


def export_excel(records: list[dict[str, Any]], errors: list[dict[str, Any]], asset_rows: list[dict[str, Any]], link_rows: list[dict[str, Any]], event_rows: list[dict[str, Any]], cfg: Config) -> Path:
    xlsx = cfg.output_dir / cfg.excel_filename

    pages_rows: list[dict[str, Any]] = []
    for r in records:
        meta = r.get("event_metadata") or {}
        pages_rows.append({
            "order": r.get("order"),
            "source_group": r.get("source_group"),
            "configured_section": r.get("configured_section"),
            "configured_kind": r.get("configured_kind"),
            "configured_title": r.get("configured_title"),
            "event_site_key": r.get("event_site_key"),
            "url": r.get("url"),
            "final_url": r.get("final_url"),
            "status_code": r.get("status_code"),
            "title": r.get("title"),
            "h1": r.get("h1"),
            "text_chars": len(r.get("text", "")),
            "text": r.get("text", ""),
            "asset_count": r.get("asset_count"),
            "link_count": r.get("link_count"),
            "event_title": meta.get("event_title", ""),
            "event_place": meta.get("place", ""),
            "event_start_date": meta.get("start_date", ""),
            "event_end_date": meta.get("end_date", ""),
            "event_logo_url": meta.get("logo_url", ""),
            "event_confidence": meta.get("confidence", ""),
        })

    # Excel cells have hard limits; JSONL and TXT contain the complete record/text.
    def df(rows: list[dict[str, Any]]) -> pd.DataFrame:
        clean_rows = []
        for row in rows:
            clean_rows.append({k: truncate_excel_value(v, cfg.max_cell_chars) for k, v in row.items()})
        return pd.DataFrame(clean_rows)

    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        df(pages_rows).to_excel(writer, sheet_name="pages", index=False)
        df(event_rows).to_excel(writer, sheet_name="events", index=False)
        df(asset_rows).to_excel(writer, sheet_name="assets_by_page", index=False)
        df(dedupe_assets(asset_rows)).to_excel(writer, sheet_name="assets_unique", index=False)
        df(link_rows).to_excel(writer, sheet_name="links", index=False)
        df(errors).to_excel(writer, sheet_name="errors", index=False)

        for sheet in writer.book.worksheets:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for column_cells in sheet.columns:
                max_len = 8
                col_letter = column_cells[0].column_letter
                for cell in column_cells[:200]:
                    try:
                        max_len = max(max_len, min(70, len(str(cell.value or "")) + 2))
                    except Exception:
                        pass
                sheet.column_dimensions[col_letter].width = max_len

    return xlsx


def write_text_files(records: list[dict[str, Any]], cfg: Config) -> None:
    text_dir = cfg.output_dir / "pages_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    for r in records:
        order = int(r.get("order", 0))
        name = f"{order:05d}_{r.get('source_group', 'page')}_{slugify(r.get('title') or r.get('final_url') or 'page')}.txt"
        path = text_dir / name
        path.write_text(r.get("text", ""), encoding="utf-8")
        r["text_file"] = str(path.relative_to(cfg.output_dir))


def write_outputs(records: list[dict[str, Any]], errors: list[dict[str, Any]], cfg: Config) -> None:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    write_text_files(records, cfg)

    old_records = [r for r in records if r.get("source_group") == "old"]
    event_page_records = [r for r in records if r.get("source_group") == "events"]
    external_event_page_records = [r for r in records if r.get("source_group") == "event_external"]
    asset_rows = flatten_assets(records)
    link_rows = flatten_links(records)
    event_rows = best_event_summaries(records)
    old_pages_report = []
    for r in old_records:
        text = r.get("text") or ""
        links = r.get("links") or []
        assets = r.get("assets") or []
        old_pages_report.append({
            "url": r.get("final_url") or r.get("url"),
            "section": r.get("configured_section"),
            "kind": r.get("configured_kind"),
            "rendered": bool(r.get("rendered")),
            "renderer": r.get("renderer"),
            "text_lines": len(r.get("text_lines") or []),
            "links": len(links),
            "assets": len(assets),
            "has_members": bool(re.search(r"\bMembers of MIFP\b|\b#\s*Member\s+Affiliation\b", text, re.I)),
            "has_sponsor": bool(re.search(r"\bMax.?s Brasserie\b|\bsponsor\b", text, re.I)),
            "has_policy_markers": bool(re.search(r"\bPrivacy Policy\b|\bCODE OF CONDUCT\b|\bManifesto of Solidarity\b", text, re.I)),
        })

    maybe_download_assets(asset_rows, cfg)

    write_jsonl(cfg.output_dir / "pages_all.jsonl", records)
    write_jsonl(cfg.output_dir / "pages_old_mifp.jsonl", old_records)
    write_jsonl(cfg.output_dir / "pages_events_mifp.jsonl", event_page_records)
    write_jsonl(cfg.output_dir / "pages_event_external.jsonl", external_event_page_records)
    write_jsonl(cfg.output_dir / "assets_by_page.jsonl", asset_rows)
    write_jsonl(cfg.output_dir / "assets_unique.jsonl", dedupe_assets(asset_rows))
    write_jsonl(cfg.output_dir / "links_by_page.jsonl", link_rows)
    write_jsonl(cfg.output_dir / "events_summary.jsonl", event_rows)
    write_jsonl(cfg.output_dir / "errors.jsonl", errors)
    (cfg.output_dir / "scrape_report.json").write_text(json.dumps({
        "old_mifp_rendering": {
            "pages": len(old_records),
            "rendered_pages": sum(1 for r in old_records if r.get("rendered")),
            "thin_pages": [r.get("final_url") or r.get("url") for r in old_records if len(r.get("text_lines") or []) < 8],
        },
        "checks": old_pages_report,
        "errors": errors[:200],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "project_name": cfg.raw.get("project_name", "mifp_full_scrape"),
        "config_path": str(cfg.config_path),
        "output_dir": str(cfg.output_dir),
        "pages_total": len(records),
        "old_pages_total": len(old_records),
        "events_pages_total": len(event_page_records),
        "events_summary_total": len(event_rows),
        "assets_by_page_total": len(asset_rows),
        "assets_unique_total": len(dedupe_assets(asset_rows)),
        "links_total": len(link_rows),
        "errors_total": len(errors),
        "files": {
            "pages_all": "pages_all.jsonl",
            "old_pages": "pages_old_mifp.jsonl",
            "event_pages": "pages_events_mifp.jsonl",
            "events_summary": "events_summary.jsonl",
            "assets_by_page": "assets_by_page.jsonl",
            "assets_unique": "assets_unique.jsonl",
            "links_by_page": "links_by_page.jsonl",
            "errors": "errors.jsonl",
            "scrape_report": "scrape_report.json",
            "excel": cfg.excel_filename,
            "text_dir": "pages_text/",
        },
    }
    (cfg.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    xlsx = export_excel(records, errors, asset_rows, link_rows, event_rows, cfg)

    log.info("\nDone.")
    log.info(f"Output dir: {cfg.output_dir}")
    log.info(f"Pages: {len(records)} | Event sites: {len(event_rows)} | Assets unique: {manifest['assets_unique_total']} | Errors: {len(errors)}")
    log.info(f"Excel: {xlsx}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    parser = argparse.ArgumentParser(description="Scrape old.mifp.eu and events.mifp.eu into JSONL + Excel.")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    args = parser.parse_args()

    cfg = Config.load(args.config)
    log.info(f"Config: {cfg.config_path}")
    log.info(f"Output: {cfg.output_dir}")
    log.info(f"Workers: {cfg.workers}")

    records, errors = crawl_all(cfg)
    write_outputs(records, errors, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

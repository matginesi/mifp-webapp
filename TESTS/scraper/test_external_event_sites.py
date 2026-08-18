from __future__ import annotations

import json

from scrape_remote import (
    Config,
    best_event_summaries,
    extract_event_metadata,
    extract_headings,
    extract_links_and_assets,
    is_safe_external_event_url,
    should_follow_discovered_link,
    soup_from_html,
    visible_text_lines,
)


def _cfg(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "output_dir": str(tmp_path / "out"),
                "allowed_page_domains": ["old.mifp.eu", "events.mifp.eu"],
                "external_event_allowed_domains": ["conference.test"],
                "asset_domains_keep_as_urls": ["events.mifp.eu", "conference.test"],
                "asset_extensions": [".pdf", ".jpg", ".png"],
                "image_extensions": [".jpg", ".png"],
                "document_extensions": [".pdf"],
                "crawl": {
                    "follow_external_event_sites": True,
                    "follow_event_internal_links": True,
                    "discover_events_from_old_links": True,
                    "restrict_event_internal_to_same_site": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return Config.load(path)


def test_external_event_links_are_safe_and_bounded(tmp_path):
    cfg = _cfg(tmp_path)

    assert is_safe_external_event_url("https://conference.test/", cfg)
    assert not is_safe_external_event_url("javascript:alert(1)", cfg)
    assert not is_safe_external_event_url("file:///tmp/index.html", cfg)
    assert not is_safe_external_event_url("https://evil.test/", cfg)

    assert should_follow_discovered_link(
        "https://events.mifp.eu/PLMCN-2026/",
        "https://conference.test/",
        cfg,
    )
    assert should_follow_discovered_link(
        "https://conference.test/",
        "https://conference.test/program.html",
        cfg,
    )
    assert not should_follow_discovered_link(
        "https://conference.test/",
        "https://conference.test/unrelated/archive.html",
        cfg,
    )


def test_external_event_page_extracts_metadata_and_assets(tmp_path):
    cfg = _cfg(tmp_path)
    html = """
    <html>
      <head><title>Quantum Light Conference 2026</title></head>
      <body>
        <h1>Quantum Light Conference 2026</h1>
        <p>The conference will be held in Rome, Italy from 10-12 June 2026.</p>
        <p>Program and book of abstracts are available.</p>
        <img src="/banner.jpg" alt="Conference banner">
        <a href="/program.pdf">Download program PDF</a>
        <a href="/speakers.html">Speakers</a>
        <a href="mailto:info@example.test">Mail</a>
      </body>
    </html>
    """
    url = "https://conference.test/"
    soup = soup_from_html(html)
    links, assets = extract_links_and_assets(soup, url, cfg)
    lines = visible_text_lines(soup, cfg)
    meta = extract_event_metadata(url, "Quantum Light Conference 2026", extract_headings(soup), lines, assets, cfg)

    assert meta["extracted_from_external_site"] is True
    assert meta["external_source_url"] == url
    assert meta["event_title"] == "Quantum Light Conference 2026"
    assert meta["start_date"] == "2026-06-10"
    assert meta["end_date"] == "2026-06-12"
    assert any(a["kind"] == "image" and a["url"].endswith("/banner.jpg") for a in assets)
    assert any(a["kind"] == "document" and a["url"].endswith("/program.pdf") for a in assets)
    assert any(l["is_internal_page"] and l["url"].endswith("/speakers.html") for l in links)

    rows = best_event_summaries(
        [
            {
                "source_group": "event_external",
                "domain": "conference.test",
                "final_url": url,
                "title": "Quantum Light Conference 2026",
                "text_lines": lines,
                "headings": extract_headings(soup),
                "assets": assets,
                "event_metadata": meta,
                "order": 1,
            }
        ]
    )
    assert rows[0]["extracted_from_external_site"] is True
    assert rows[0]["external_source_url"] == url

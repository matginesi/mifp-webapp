#!/usr/bin/env python3
"""Remote MIFP scraper: legacy Aruba pages plus event-site crawling."""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from _remote_aruba import (  # re-exported public extraction helpers
    canonical_old_url,
    extract_links_and_assets as extract_aruba_links_and_assets,
    extract_structured_page,
    has_strong_title_signal,
    is_navigation_cluster,
    looks_like_event_title_pattern,
    looks_like_news_title,
    normalize_news_record,
    parse_home_news,
    parse_members_table,
    scrape as scrape_aruba,
    soup_from as soup_from_aruba,
    write_outputs as write_aruba_outputs,
)
from _remote_events import (  # re-exported public event/crawl helpers
    Config,
    best_event_summaries,
    crawl_all,
    extract_event_metadata,
    extract_headings,
    extract_links_and_assets,
    is_safe_external_event_url,
    normalize_date_range,
    should_follow_discovered_link,
    soup_from_html,
    visible_text_lines,
    write_outputs as write_event_outputs,
)

log = logging.getLogger(__name__)


def _validated_config(path: Path, output: Path, workers: int | None) -> tuple[Config, Path]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    crawl = raw.setdefault("crawl", {})
    if workers is not None:
        crawl["workers"] = workers
    if not 1 <= int(crawl.get("workers", 1)) <= 128:
        raise ValueError("crawl.workers must be between 1 and 128")
    if not 1 <= int(crawl.get("max_pages", 1)) <= 100_000:
        raise ValueError("crawl.max_pages must be between 1 and 100000")
    if not 0 <= int(crawl.get("max_depth", 0)) <= 20:
        raise ValueError("crawl.max_depth must be between 0 and 20")
    if not 1 <= int(crawl.get("request_timeout_seconds", 1)) <= 300:
        raise ValueError("crawl.request_timeout_seconds must be between 1 and 300")
    if not raw.get("event_seed_urls") and not raw.get("curated_pages") and not raw.get("start_urls"):
        raise ValueError("remote config has no seed or curated URLs")
    if not bool(crawl.get("verify_ssl", True)):
        log.warning("TLS verification is disabled by configuration for legacy-site compatibility")
    raw["output_dir"] = str((output / "events").resolve())
    temporary = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False)
    with temporary:
        json.dump(raw, temporary, ensure_ascii=False, indent=2)
    temp_path = Path(temporary.name)
    return Config.load(temp_path), temp_path


def run(config_path: Path, output: Path, workers: int | None, render_fallback: bool) -> dict:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    config, temporary_config = _validated_config(config_path.resolve(), output.resolve(), workers)
    try:
        event_records, event_errors = crawl_all(config)
        write_event_outputs(event_records, event_errors, config)
    finally:
        temporary_config.unlink(missing_ok=True)

    aruba_output = output / "aruba"
    aruba_args = SimpleNamespace(
        output=aruba_output,
        timeout=config.timeout,
        render_fallback=render_fallback,
        render_wait_ms=config.render_wait_ms,
        save_snapshots=bool(config.raw.get("rendering", {}).get("save_snapshots", False)),
        include_manifesto_candidates=True,
    )
    aruba = scrape_aruba(aruba_args)
    write_aruba_outputs(aruba_output, aruba)
    successful_event_pages = len(event_records)
    successful_aruba_pages = sum(1 for item in aruba.fetches if int(item.get("status_code") or 0) < 400)
    final_counts = {
        "event_pages": successful_event_pages,
        "aruba_pages": successful_aruba_pages,
        "members": len(aruba.members),
        "news": len(aruba.news),
        "events": len(aruba.events),
        "sponsors": len(aruba.sponsors),
        "pages": len(aruba.pages),
    }
    errors = [*event_errors, *aruba.errors]
    report = {
        "source": "remote",
        "sources_processed": len(config.start_urls) + len(config.event_seed_urls) + len(config.curated_pages),
        "pages_found": successful_event_pages + len(aruba.fetches),
        "pages_succeeded": successful_event_pages + successful_aruba_pages,
        "pages_failed": len(errors),
        "raw_records": final_counts,
        "errors": errors[:100],
        "duration_seconds": round(time.monotonic() - started, 3),
        "output": str(output.resolve()),
    }
    (output / "remote_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["pages_succeeded"] == 0:
        raise RuntimeError("remote scraping produced no successful pages")
    max_failures = max(10, report["pages_found"] // 2)
    if report["pages_failed"] > max_failures:
        raise RuntimeError(f"remote failure threshold exceeded: {report['pages_failed']} > {max_failures}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.remote.json"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("output") / "raw" / "remote")
    parser.add_argument("--workers", "--threads", type=int)
    parser.add_argument("--render-fallback", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s | %(message)s",
    )
    try:
        report = run(args.config, args.output, args.workers, args.render_fallback)
    except Exception as exc:
        log.exception("Remote scraper failed") if args.verbose else log.error("Remote scraper failed: %s", exc)
        return 1
    log.info("Remote scrape complete | pages=%d | failures=%d | output=%s", report["pages_succeeded"], report["pages_failed"], report["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

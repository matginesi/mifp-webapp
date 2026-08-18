from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scrape_remote import (
    has_strong_title_signal,
    is_navigation_cluster,
    looks_like_event_title_pattern,
    looks_like_news_title,
    normalize_news_record,
    parse_home_news,
    soup_from_aruba as soup_from,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _parse_fixture(filename: str):
    html = (FIXTURE_DIR / filename).read_text(encoding="utf-8")
    soup = soup_from(html)
    news_items, tokens, skipped = parse_home_news(soup, "https://old.mifp.eu/")
    records = [normalize_news_record(item, i, "https://old.mifp.eu/") for i, item in enumerate(news_items)]
    return records, tokens, skipped


class TestTitleDetection:
    def test_looks_like_event_title_plmnc(self):
        assert looks_like_event_title_pattern("PLMNC 2023 - Medellin, Colombia")

    def test_looks_like_event_title_icp2dc(self):
        assert looks_like_event_title_pattern("ICP2DC6 - 2022, Yerevan Armenia")

    def test_looks_like_event_title_negative(self):
        assert not looks_like_event_title_pattern("This is just a normal sentence.")

    def test_has_strong_signal_event_code(self):
        assert has_strong_title_signal("PLMNC 2023 - Medellin, Colombia")

    def test_has_strong_signal_institutional(self):
        assert has_strong_title_signal("MIFP and University of Amazonia Join Forces")

    def test_has_strong_signal_congratulation(self):
        assert has_strong_title_signal("Congratulation to Dr. Nikita Kavokine")

    def test_has_strong_signal_title_case(self):
        assert has_strong_title_signal("New Breakthrough in Polariton Condensation")

    def test_has_strong_signal_negative_download(self):
        assert not has_strong_title_signal("Download")

    def test_has_strong_signal_negative_period(self):
        assert not has_strong_title_signal("This is a sentence ending with a period.")

    def test_not_noise_in_title(self):
        assert not looks_like_news_title("Instagram")
        assert not looks_like_news_title("Max's Brasserie")
        assert not looks_like_news_title("Download PDF")
        assert not looks_like_news_title("Research MIFP Pubblications Projects Research Members' Presentations Members' Pubblications")

    def test_navigation_cluster_detection(self):
        assert is_navigation_cluster("Archive Events Meetings Schools Workshops Conferences")
        assert is_navigation_cluster("Research MIFP Pubblications Projects Research Members' Presentations Members' Pubblications")
        assert not is_navigation_cluster("Congratulations to Prof. Alexey Kavokin on their Science Perspective")


class TestHomeSegmentation:
    def test_produces_separate_news_items(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        assert len(records) >= 5, f"Expected >=5 news items, got {len(records)}"

    def test_first_news_title_correct(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        first = records[0]
        assert "Alexey Kavokin" in first["title"] or "Kavokin" in first["title"]

    def test_first_news_body_contains_science(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        first = records[0]
        body = first.get("body", "")
        assert "Parisi" in body or "polariton" in body or "Science" in body

    def test_first_news_no_noise_in_body(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        first = records[0]
        body = first.get("body", "").lower()
        assert "agreement" not in body
        assert "instagram" not in body
        assert "max's brasserie" not in body
        assert "contacts" not in body or "contacts" in first["title"].lower()
        assert "archive events" not in body

    def test_download_label_in_documents_not_body(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        first = records[0]
        body = first.get("body", "").lower()
        documents = first.get("documents", [])
        links_combined = [l.get("text", "") for l in first.get("links", [])]
        all_doc_texts = [d.get("text", "").lower() for d in documents] + [t.lower() for t in links_combined]
        has_cta_in_body = "download paper" in body or "download here" in body
        has_cta_in_docs = any("download" in t for t in all_doc_texts)
        assert has_cta_in_docs, "Download CTA should appear in documents/links"
        assert not has_cta_in_body, "Download CTA should NOT appear in body text"

    def test_amazonia_is_separate_news(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        titles = [r["title"] for r in records]
        amazonia_titles = [t for t in titles if "Amazonia" in t or "Amazonía" in t]
        assert len(amazonia_titles) >= 1, f"No Amazonia title found in {titles}"

    def test_no_noise_titles(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        bad_titles = ["download", "instagram", "search", "contacts", "events", "sponsors"]
        for record in records:
            title_low = record["title"].lower().strip()
            assert title_low not in bad_titles, f"Bad title: '{record['title']}'"

    def test_plmnc_separate_record(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        titles = [r["title"] for r in records]
        plmnc_titles = [t for t in titles if "PLMNC" in t]
        assert len(plmnc_titles) >= 1, f"No PLMNC title found in {titles}"

    def test_extraction_warnings_present(self):
        records, _, _ = _parse_fixture("old_mifp_home_mock.html")
        for record in records:
            assert "extraction_warnings" in record
            assert isinstance(record["extraction_warnings"], list)

    def test_forthcoming_events_are_not_imported_as_news(self):
        html = """
        <main>
          <h1>MIFP News</h1>
          <h2>Forthcoming Events</h2>
          <h3>PLMCN-2025</h3>
          <p>TBC</p>
          <p>April 12-15. 2026</p>
          <h3>Congratulations to Prof. Alexey Kavokin</h3>
          <p>The new paper was published in Science with collaborators from MIFP.</p>
        </main>
        """
        soup = BeautifulSoup(html, "html.parser")
        news_items, _, _ = parse_home_news(soup, "https://old.mifp.eu/")
        records = [normalize_news_record(item, i, "https://old.mifp.eu/") for i, item in enumerate(news_items)]
        titles = [record["title"] for record in records]
        assert "PLMCN-2025" not in titles
        assert any("Congratulations" in title for title in titles)

    def test_orphan_title_merges_into_following_content(self):
        html = """
        <main>
          <h2>Congratulation to Dr. Nikita Kavokine</h2>
          <p>For the prize: PRISM-2024 - Junior PRISM Category</p>
          <a href="https://example.org/prism">Read more</a>
          <img src="/nikita.jpg" alt="Nikita">
          <h2>Next News Item</h2>
          <p>This is a separate news item with enough body text to be published.</p>
        </main>
        """
        soup = BeautifulSoup(html, "html.parser")
        news_items, _, _ = parse_home_news(soup, "https://old.mifp.eu/")
        records = [normalize_news_record(item, i, "https://old.mifp.eu/") for i, item in enumerate(news_items)]

        first = records[0]
        assert first["title"] == "Congratulation to Dr. Nikita Kavokine"
        assert "PRISM-2024" in first["body"]
        assert first["is_published"] == 1

    def test_asset_only_news_with_strong_title_is_published(self):
        record = normalize_news_record({
            "title": "Congratulation to Professor Federico Capasso For the prize: PRISM prize 2023 - Senior PRISM category",
            "body_parts": [],
            "images": [{"url": "https://example.org/capasso.png"}],
            "documents": [],
            "links": [],
        }, 0, "https://old.mifp.eu/")

        assert record["review_status"] == "published"
        assert record["is_published"] == 1

    def test_subtitle_first_body_line_is_not_flagged_as_merged(self):
        record = normalize_news_record({
            "title": "A Historic Experiment Redesigned",
            "body_parts": [
                "MIFP Members Sven Hoefling and Alexey Kavokin",
                "discuss in Nature the remarkable new observation by the group from Dortmund.",
            ],
            "images": [],
            "documents": [],
            "links": [],
        }, 0, "https://old.mifp.eu/")

        assert "possible_merged_news" not in record["extraction_warnings"]

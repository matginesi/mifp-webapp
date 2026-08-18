from __future__ import annotations

import json
import zipfile
from pathlib import Path

from artifact_normalizer import normalize_all
from import_artifacts import build_from_raw
from scrape_local import classify


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_local_classifier_recognizes_historical_publication_slug():
    assert classify("research-mifp-pubblications/index.html", "MIFP Pubblications", "") == ("publications", "publications")
    assert classify("research-research1/index.html", "Research", "") == ("research", "research")


def test_historical_publications_and_research_pages_are_not_empty(tmp_path: Path):
    raw = tmp_path / "raw"
    asset = raw / "assets_downloaded" / "pdf" / "paper-one.pdf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"%PDF-1.4\nreal paper\n")

    publication_text = (
        "MIFP Pubblications "
        "First substantial publication title Abstract one. Authors Alice A., Bob B. Download "
        "Second substantial publication title Abstract two. Authors Carol C. Download"
    )
    research_text = (
        "Research The members of MIFP conduct theoretical and experimental research in many areas of "
        "Solid State Physics and Astrophysics. The CERN collaboration works on gaseous detectors for elementary particles. "
        "Research includes light-matter coupling in nanostructures, superconductivity, organic solar cells, "
        "and quantum cavity electrodynamics."
    )
    _write_jsonl(raw / "pages_all.jsonl", [
        {
            "configured_section": "publications",
            "configured_kind": "publications",
            "configured_title": "MIFP Pubblications",
            "url": "https://old.mifp.eu/research-mifp-pubblications",
            "text": publication_text,
            "headings": [
                "MIFP Pubblications",
                "First substantial publication title",
                "Authors",
                "Second substantial publication title",
                "Authors",
            ],
            "assets": [
                {
                    "url": "https://files.example/paper-one.pdf",
                    "download_url": "https://files.example/paper-one.pdf",
                    "kind": "pdf",
                    "extension": ".pdf",
                    "local_path": "assets_downloaded/pdf/paper-one.pdf",
                }
            ],
        },
        {
            "configured_section": "research",
            "configured_kind": "research",
            "configured_title": "Research",
            "url": "https://old.mifp.eu/research-research1",
            "text": research_text,
            "headings": ["Research"],
            "assets": [],
        },
    ])
    _write_jsonl(raw / "assets_unique.jsonl", [{
        "url": "https://files.example/paper-one.pdf",
        "download_url": "https://files.example/paper-one.pdf",
        "kind": "pdf",
        "local_path": "assets_downloaded/pdf/paper-one.pdf",
    }])

    normalized = tmp_path / "normalized"
    counts = normalize_all([raw], normalized)

    assert counts["publications"] == 2
    assert counts["research_areas"] >= 5
    publication_rows = [json.loads(line) for line in (normalized / "publications.jsonl").read_text().splitlines()]
    assert publication_rows[0]["documents"][0]["local_path"] == "assets_downloaded/pdf/paper-one.pdf"


def test_scraper_zip_preserves_downloaded_assets_even_if_unlinked(tmp_path: Path):
    raw = tmp_path / "raw"
    asset = raw / "assets_downloaded" / "pdf" / "orphan.pdf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"%PDF-1.4\nunlinked but downloaded\n")
    _write_jsonl(raw / "assets_unique.jsonl", [{
        "url": "https://files.example/orphan.pdf",
        "kind": "pdf",
        "local_path": "assets_downloaded/pdf/orphan.pdf",
    }])
    # Minimal content keeps the artifact valid while the PDF is intentionally
    # not linked to the record: the ZIP must still preserve downloaded assets.
    _write_jsonl(raw / "members.jsonl", [{"display_name": "Ada Example"}])

    output = tmp_path / "out"
    report = build_from_raw([raw], output, "local", "MIFP_IMPORT.zip")

    assert report["records"] == 1
    assert report["packaged_assets"] == 1
    with zipfile.ZipFile(output / "MIFP_IMPORT.zip") as archive:
        names = archive.namelist()
        assert any(name.startswith("assets/") and name.endswith("_orphan.pdf") for name in names)

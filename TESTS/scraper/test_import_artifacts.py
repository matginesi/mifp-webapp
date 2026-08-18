from __future__ import annotations

import json
import zipfile
from pathlib import Path

from import_artifacts import canonicalize, write_artifacts
from validate_artifacts import validate


def test_canonical_zip_matches_jsonl_and_dashboard_import(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    record = canonicalize(
        {
            "type": "news",
            "title": "Twenty years of MIFP",
            "body": "A complete historical record with a related publication.",
            "date": "2024-01-02",
            "documents": [{"url": "https://old.mifp.eu/history.pdf"}],
        },
        "local",
    )
    assert record is not None
    output = tmp_path / "import"
    report = write_artifacts([record], output, "MIFP_IMPORT.zip", [source])
    assert report["records"] == 1
    with zipfile.ZipFile(output / "MIFP_IMPORT.zip") as archive:
        assert set(archive.namelist()) == {"manifest.json", "records.jsonl"}
        assert archive.read("records.jsonl") == (output / "records.jsonl").read_bytes()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["counts"] == {"news": 1}
    assert validate(output)["counts"] == {"news": 1}


def test_merge_keeps_one_identity_and_fills_missing_information(tmp_path: Path):
    short = canonicalize({"type": "sponsor", "name": "MIFP Partner"}, "local")
    rich = canonicalize(
        {"type": "sponsor", "name": "MIFP Partner", "description": "Long institutional partnership", "website": "https://partner.test/"},
        "remote",
    )
    report = write_artifacts([short, rich], tmp_path, "MIFP_IMPORT.zip")
    rows = [json.loads(line) for line in (tmp_path / "records.jsonl").read_text().splitlines()]
    assert report["duplicates_removed"] == 1
    assert len(rows) == 1
    assert rows[0]["data"]["description"] == "Long institutional partnership"
    assert "review_status" not in rows[0]["data"]
    assert validate(tmp_path)["counts"] == {"sponsor": 1}


def test_combined_artifact_recovers_assets_from_intermediate_zip(tmp_path: Path):
    source = tmp_path / "intermediate"
    source.mkdir()
    asset_bytes = b"%PDF-1.4\nintermediate asset\n"
    with zipfile.ZipFile(source / "MIFP_LOCAL_IMPORT.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("assets/local-paper.pdf", asset_bytes)

    record = {
        "type": "news",
        "data": {"title": "Packaged news", "slug": "packaged-news"},
        "links": [],
        "assets": [{"path": "local-paper.pdf", "role": "document", "kind": "pdf"}],
        "meta": {"source": "local"},
    }
    output = tmp_path / "combined"
    report = write_artifacts([record], output, "MIFP_IMPORT.zip", [source])

    assert report["packaged_assets"] == 1
    with zipfile.ZipFile(output / "MIFP_IMPORT.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        packaged_path = manifest["files"][0]["archive_path"]
        assert archive.read(packaged_path) == asset_bytes
        rows = [json.loads(line) for line in archive.read("records.jsonl").decode().splitlines()]
        assert rows[0]["assets"][0]["path"] == packaged_path.removeprefix("assets/")

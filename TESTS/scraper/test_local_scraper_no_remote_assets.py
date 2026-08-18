from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_local_scraper_keeps_only_assets_present_in_local_dump(tmp_path):
    root = tmp_path / "site"
    out = tmp_path / "out"
    (root / "www.mifp.eu" / "images").mkdir(parents=True)
    (root / "www.mifp.eu" / "docs").mkdir(parents=True)
    (root / "www.mifp.eu" / "images" / "local.jpg").write_bytes(b"local image bytes")
    (root / "www.mifp.eu" / "docs" / "local.pdf").write_bytes(b"%PDF local bytes")
    (root / "www.mifp.eu" / "index.html").write_text(
        """
        <html><head><title>Home</title></head><body>
          <div class="module">
            <strong>Important local news item</strong>
            <p>This local news body is long enough to be extracted and includes assets.</p>
            <img src="images/local.jpg" alt="Local image">
            <img src="https://old.mifp.eu/images/missing.jpg" alt="Missing image">
            <a href="docs/local.pdf">Local PDF</a>
            <a href="https://old.mifp.eu/docs/missing.pdf">Missing PDF</a>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "SCRAPERS/scrape_local.py",
            "--root",
            str(root),
            "--output",
            str(out),
            "--base-url",
            "https://old.mifp.eu/",
        ],
        check=True,
    )

    assets = _read_jsonl(out / "assets_unique.jsonl")
    news = _read_jsonl(out / "news.jsonl")

    assert {Path(a["url"]).name for a in assets} == {"local.jpg", "local.pdf"}
    assert all(a.get("local_path") for a in assets)
    assert all("missing" not in a["url"] for a in assets)
    assert news[0]["images"][0]["kind"] == "image"
    assert all("missing" not in a["url"] for a in news[0]["images"] + news[0]["documents"])

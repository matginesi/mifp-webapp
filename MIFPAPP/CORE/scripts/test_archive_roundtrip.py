#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mifp_archive.database import connect
from mifp_archive.health import archive_health
from mifp_archive.package import export_archive, import_archive, validate_archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a full MIFP Content Archive round-trip")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--keep", type=Path, help="Keep the reconstructed database/assets here")
    args = parser.parse_args()

    managed = tempfile.TemporaryDirectory(prefix="mifp-archive-roundtrip-") if not args.keep else None
    root = args.keep or Path(managed.name)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "archive.zip"
    rebuilt_db = root / "rebuilt.db"
    rebuilt_assets = root / "assets"

    with connect(args.db) as source:
        exported = export_archive(source, args.assets, archive)
        source_counts = exported["manifest"]["counts"]

    validation = validate_archive(archive)
    if not validation["valid"]:
        raise SystemExit(json.dumps(validation, indent=2))

    with connect(rebuilt_db) as target:
        imported = import_archive(target, rebuilt_assets, archive)
        health = archive_health(target, rebuilt_assets)
        rebuilt_counts = {
            "entities": sum(target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "members", "events", "news", "publications", "research_areas", "pages", "sponsors"
            )),
            "assets": target.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
        }

    expected = {"entities": source_counts.get("entities", 0), "assets": source_counts.get("assets", 0)}
    ok = rebuilt_counts == expected and health["summary"]["critical"] == 0
    print(json.dumps({
        "ok": ok,
        "archive": str(archive),
        "rebuilt_db": str(rebuilt_db),
        "expected": expected,
        "rebuilt": rebuilt_counts,
        "import": imported,
        "health": health["summary"],
    }, indent=2))
    if managed:
        managed.cleanup()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

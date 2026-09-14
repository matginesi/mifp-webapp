#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


TOP_KEYS = {"type", "data", "links", "assets", "meta"}
TYPES = {"event", "news", "member", "publication", "research_area", "page", "sponsor"}


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{line_no}: invalid JSON: {exc}")
            continue
        if not isinstance(item, dict):
            errors.append(f"{path}:{line_no}: record must be an object")
            continue
        unknown = set(item) - TOP_KEYS
        if unknown:
            errors.append(f"{path}:{line_no}: unknown top-level keys: {', '.join(sorted(unknown))}")
        typ = item.get("type")
        if typ not in TYPES:
            errors.append(f"{path}:{line_no}: invalid type: {typ!r}")
        if not isinstance(item.get("data"), dict):
            errors.append(f"{path}:{line_no}: data must be an object")
        for key in ("links", "assets"):
            if key in item and item[key] is not None and not isinstance(item[key], list):
                errors.append(f"{path}:{line_no}: {key} must be a list")
        if "meta" in item and item["meta"] is not None and not isinstance(item["meta"], dict):
            errors.append(f"{path}:{line_no}: meta must be an object")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MIFP JSONL v2 import files")
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(Path(__file__).resolve().parent / "OUTPUTS")],
        help="Files or directories to validate",
    )
    args = parser.parse_args()
    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.jsonl")))
        else:
            files.append(path)
    errors: list[str] = []
    for path in files:
        errors.extend(validate_file(path))
    if errors:
        print("\n".join(errors))
        return 1
    print(f"OK: {len(files)} JSONL file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

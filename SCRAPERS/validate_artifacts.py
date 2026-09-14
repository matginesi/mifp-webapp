#!/usr/bin/env python3
"""Validate scraper JSONL and ZIP artifacts without opening an application DB."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from import_artifacts import PORTABLE_TYPES, TYPE_FILES


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: record must be an object")
        if row.get("type") not in PORTABLE_TYPES:
            raise ValueError(f"{path}:{line_no}: unsupported type: {row.get('type')!r}")
        if not isinstance(row.get("data"), dict):
            raise ValueError(f"{path}:{line_no}: data must be an object")
        for key in ("links", "assets"):
            if key in row and row[key] is not None and not isinstance(row[key], list):
                raise ValueError(f"{path}:{line_no}: {key} must be a list")
        rows.append(row)
    return rows


def _safe_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe ZIP member path: {name}")


def validate(directory: Path, *, required_types: tuple[str, ...] = (), require_assets: bool = False) -> dict:
    directory = directory.resolve()
    records_path = directory / "records.jsonl"
    if not records_path.is_file():
        raise ValueError(f"missing canonical records file: {records_path}")

    zips = sorted(directory.glob("*.zip"))
    if len(zips) != 1:
        raise ValueError(f"expected exactly one ZIP in {directory}, found {len(zips)}")

    rows = _read_jsonl(records_path)
    counts = Counter(row["type"] for row in rows)
    missing_required = [typ for typ in required_types if counts.get(typ, 0) <= 0]
    if missing_required:
        raise ValueError(
            "required scraper output is empty: " + ", ".join(sorted(missing_required))
        )

    for typ, filename in TYPE_FILES.items():
        path = directory / filename
        if not path.is_file():
            raise ValueError(f"missing per-type JSONL: {path}")
        typed_rows = _read_jsonl(path)
        if any(row["type"] != typ for row in typed_rows):
            raise ValueError(f"{path} contains records of another type")
        if len(typed_rows) != counts.get(typ, 0):
            raise ValueError(f"{path} count differs from records.jsonl")

    with zipfile.ZipFile(zips[0]) as archive:
        names = archive.namelist()
        for name in names:
            _safe_archive_name(name)
        required = {"manifest.json", "records.jsonl"}
        if not required.issubset(names):
            raise ValueError(f"ZIP is missing: {sorted(required - set(names))}")
        zipped_records = archive.read("records.jsonl")
        if zipped_records != records_path.read_bytes():
            raise ValueError("ZIP records.jsonl is not byte-identical to the unpacked file")
        manifest = json.loads(archive.read("manifest.json"))
        if require_assets and not (manifest.get("files") or []):
            raise ValueError("scraper ZIP contains no packaged assets")
        if manifest.get("records") != len(rows):
            raise ValueError("manifest record count differs from records.jsonl")
        if manifest.get("counts") != dict(sorted(counts.items())):
            raise ValueError("manifest type counts differ from records.jsonl")
        for item in manifest.get("files", []):
            archive_path = str(item.get("archive_path") or "")
            _safe_archive_name(archive_path)
            if archive_path not in names:
                raise ValueError(f"manifest asset is missing from ZIP: {archive_path}")
            payload = archive.read(archive_path)
            if len(payload) != int(item.get("bytes", -1)):
                raise ValueError(f"asset size mismatch: {archive_path}")
            if hashlib.sha256(payload).hexdigest() != item.get("sha256"):
                raise ValueError(f"asset checksum mismatch: {archive_path}")

    unexpected = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name != ".gitkeep" and path.suffix.lower() not in {".jsonl", ".zip"}
    )
    if unexpected:
        raise ValueError(f"unexpected final output files: {unexpected}")

    return {
        "records": len(rows),
        "counts": dict(sorted(counts.items())),
        "zip": str(zips[0]),
        "database_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-type", action="append", default=[], choices=PORTABLE_TYPES,
                        help="Fail if the final artifact contains zero records of this type")
    parser.add_argument("--require-assets", action="store_true",
                        help="Fail if the final ZIP contains no packaged assets")
    args = parser.parse_args()
    print(json.dumps(validate(
        args.directory,
        required_types=tuple(args.require_type),
        require_assets=args.require_assets,
    ), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

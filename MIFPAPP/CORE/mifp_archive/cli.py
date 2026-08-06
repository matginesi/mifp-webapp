from __future__ import annotations

import argparse
import json
from pathlib import Path

from .database import connect
from .health import archive_health
from .migrate import migrate
from .package import export_archive, import_archive, validate_archive


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mifp-archive", description="Portable MIFP archive tools")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("migrate", "health"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--db", required=True, type=Path)
        if name == "health":
            cmd.add_argument("--assets", required=True, type=Path)
    cmd = sub.add_parser("export")
    cmd.add_argument("--db", required=True, type=Path)
    cmd.add_argument("--assets", required=True, type=Path)
    cmd.add_argument("--out", required=True, type=Path)
    cmd = sub.add_parser("validate")
    cmd.add_argument("archive", type=Path)
    cmd = sub.add_parser("import")
    cmd.add_argument("archive", type=Path)
    cmd.add_argument("--db", required=True, type=Path)
    cmd.add_argument("--assets", required=True, type=Path)
    cmd.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        result = validate_archive(args.archive)
    else:
        with connect(args.db) as conn:
            if args.command == "migrate":
                result = migrate(conn)
                conn.commit()
            elif args.command == "health":
                migrate(conn)
                result = archive_health(conn, args.assets)
            elif args.command == "export":
                result = export_archive(conn, args.assets, args.out)
            elif args.command == "import":
                result = import_archive(conn, args.assets, args.archive, dry_run=args.dry_run)
            else:  # pragma: no cover
                raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

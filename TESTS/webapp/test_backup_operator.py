from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deploy" / "backup.sh"


def _exe(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _backup_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "mifp"
    data = home / "data"
    for name in ("assets", "conferences", "config"):
        (data / name).mkdir(parents=True, exist_ok=True)
        (data / name / f"{name}.txt").write_text(name, encoding="utf-8")
    (data / "mifp.db").write_bytes(b"SQLite format 3\x00" + b"x" * 200)
    (home / ".env").write_text("MIFP_BACKUP_KEEP='2'\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "id", "#!/bin/sh\n[ \"$1\" = -u ] && echo 0 || /usr/bin/id \"$@\"\n")
    _exe(
        bin_dir / "install",
        "#!/bin/bash\nmode=0755; directory=0; targets=()\nwhile (($#)); do case \"$1\" in -m) mode=$2; shift 2;; -o|-g) shift 2;; -d) directory=1; shift;; *) targets+=(\"$1\"); shift;; esac; done\nif ((directory)); then for target in \"${targets[@]}\"; do mkdir -p \"$target\"; chmod \"$mode\" \"$target\"; done; else cp \"${targets[-2]}\" \"${targets[-1]}\"; chmod \"$mode\" \"${targets[-1]}\"; fi\n",
    )
    _exe(bin_dir / "sha256sum", "#!/bin/sh\n/usr/bin/sha256sum \"$@\"\n")
    _exe(
        bin_dir / "rsync",
        r'''#!/bin/bash
set -eu
src="${@: -2:1}"; dst="${@: -1}"
mkdir -p "$dst"
cp -a "$src". "$dst"/
''',
    )
    _exe(
        bin_dir / "sqlite3",
        r'''#!/usr/bin/env python3
import shutil
import sys

args = sys.argv[1:]
source = next((arg for arg in args if not arg.startswith("-") and not arg.startswith(".")), None)
backup = next((arg for arg in args if arg.startswith(".backup ")), None)
if backup is not None:
    destination = backup[len(".backup "):].strip().strip("'").strip('"')
    if source is None:
        raise SystemExit(2)
    shutil.copyfile(source, destination)
    raise SystemExit(0)
print("ok")
''',
    )
    backup_root = tmp_path / "backups"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "MIFP_HOME": str(home),
            "MIFP_BACKUP_ROOT": str(backup_root),
            "MIFP_BACKUP_LOCK_FILE": str(tmp_path / "backup.lock"),
            "MIFP_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
            "MIFP_BACKUP_QUIESCE": "0",
        }
    )
    return env, data, backup_root


def _snapshots(backup_root: Path) -> list[Path]:
    return sorted((backup_root / "snapshots").glob("snapshot-*"))


def test_backup_is_point_in_time_and_retained(tmp_path: Path) -> None:
    env, data, backup_root = _backup_env(tmp_path)

    subprocess.run(["bash", str(BACKUP)], env=env, check=True, text=True, capture_output=True)
    first = _snapshots(backup_root)[0]
    assert (first / "config" / "config.txt").read_text() == "config"
    assert (first / "assets" / "assets.txt").read_text() == "assets"
    assert (first / "mifp.db").is_file()
    assert (first / "mifp.db.sha256").is_file()
    assert (first / "manifest.json").is_file()

    # A later backup must not mutate the older filesystem snapshot.
    (data / "config" / "config.txt").write_text("config-v2", encoding="utf-8")
    subprocess.run(["bash", str(BACKUP)], env=env, check=True, text=True, capture_output=True)
    second = _snapshots(backup_root)[-1]
    assert first != second
    assert (first / "config" / "config.txt").read_text() == "config"
    assert (second / "config" / "config.txt").read_text() == "config-v2"

    # Retention is per complete point-in-time snapshot, not per DB file.
    (data / "config" / "config.txt").write_text("config-v3", encoding="utf-8")
    subprocess.run(["bash", str(BACKUP)], env=env, check=True, text=True, capture_output=True)
    kept = _snapshots(backup_root)
    assert len(kept) == 2
    assert all((snap / "mifp.db").is_file() for snap in kept)
    assert all((snap / "assets").is_dir() for snap in kept)
    assert all((snap / "conferences").is_dir() for snap in kept)
    assert all((snap / "config").is_dir() for snap in kept)
    assert all((snap / "manifest.json").is_file() for snap in kept)
    assert not any(path.name.startswith(".snapshot-") for path in (backup_root / "snapshots").iterdir())


def test_backup_rejects_symlinks_in_restorable_trees(tmp_path: Path) -> None:
    env, data, backup_root = _backup_env(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (data / "assets" / "unsafe-link").symlink_to(outside)

    result = subprocess.run(
        ["bash", str(BACKUP)],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "link simbolico" in result.stderr
    assert not _snapshots(backup_root)

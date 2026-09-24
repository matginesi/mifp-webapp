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
    package = data / "conferences" / "plmcn-2027" / "packages" / "source.zip"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"private-source-package")
    public_root = tmp_path / "mifp-events" / "PLMCN-2027"
    public_root.mkdir(parents=True)
    (public_root / "index.html").write_text("deployment artifact", encoding="utf-8")
    (data / "mifp.db").write_bytes(b"SQLite format 3\x00" + b"x" * 200)
    (home / ".env").write_text(
        "MIFP_BACKUP_KEEP='2'\nEVENTS_PUBLISH_BACKEND='local-vps'\n",
        encoding="utf-8",
    )
    private_root = tmp_path / "mifp-events-private"
    (private_root / "registrations").mkdir(parents=True)
    (private_root / "uploads").mkdir()
    (private_root / "sessions").mkdir()
    (private_root / "tmp").mkdir()
    (private_root / "registrations/submission.json").write_text("private", encoding="utf-8")
    (private_root / "uploads/proof.pdf").write_bytes(b"proof")
    php_state = home / "events-php-enabled.txt"
    php_state.write_text("PLMCN-2027/regform\n", encoding="utf-8")

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
            "MIFP_EVENTS_PRIVATE_DIR": str(private_root),
            "MIFP_EVENTS_PHP_STATE": str(php_state),
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
    assert (first / "conferences/plmcn-2027/packages/source.zip").read_bytes() == b"private-source-package"
    assert not (first / "events").exists()
    assert (first / "events-private/registrations/submission.json").read_text() == "private"
    assert (first / "events-private/uploads/proof.pdf").read_bytes() == b"proof"
    assert not (first / "events-private/sessions").exists()
    assert (first / "events-php-enabled.txt").read_text() == "PLMCN-2027/regform\n"
    assert '"version": 5' in (first / "manifest.json").read_text()
    assert '"events_backend": "local-vps"' in (first / "manifest.json").read_text()

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
    assert all(not (snap / "events").exists() for snap in kept)
    assert all((snap / "events-private/registrations/submission.json").is_file() for snap in kept)
    assert all((snap / "events-php-enabled.txt").is_file() for snap in kept)
    assert all((snap / "manifest.json").is_file() for snap in kept)
    assert not any(path.name.startswith(".snapshot-") for path in (backup_root / "snapshots").iterdir())


def test_remote_backup_does_not_require_or_claim_local_regform_runtime(tmp_path: Path) -> None:
    env, _data, backup_root = _backup_env(tmp_path)
    home = Path(env["MIFP_HOME"])
    (home / ".env").write_text(
        "MIFP_BACKUP_KEEP='2'\nEVENTS_PUBLISH_BACKEND='remote'\n",
        encoding="utf-8",
    )
    env["MIFP_EVENTS_PRIVATE_DIR"] = str(tmp_path / "absent-private-runtime")
    env["MIFP_EVENTS_PHP_STATE"] = str(tmp_path / "absent-php-policy")

    result = subprocess.run(
        ["bash", str(BACKUP)], env=env, check=False, text=True, capture_output=True
    )

    assert result.returncode == 0, result.stderr
    snapshot = _snapshots(backup_root)[0]
    manifest = (snapshot / "manifest.json").read_text(encoding="utf-8")
    assert '"events_backend": "remote"' in manifest
    assert not (snapshot / "events-private").exists()
    assert not (snapshot / "events-php-enabled.txt").exists()
    assert "verify the remote host backup separately" in (snapshot / "README.txt").read_text()


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


def test_backup_does_not_publish_when_live_db_is_a_symlink(tmp_path: Path) -> None:
    """A symlinked live database must fail loudly, not report a successful
    no-op every night."""
    env, data, backup_root = _backup_env(tmp_path)
    real = data / "real.db"
    (data / "mifp.db").rename(real)
    (data / "mifp.db").symlink_to(real)

    result = subprocess.run(
        ["bash", str(BACKUP)], env=env, check=False, text=True, capture_output=True
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert _snapshots(backup_root) == []


def test_no_prune_preserves_the_source_snapshot_for_restore(tmp_path: Path) -> None:
    """The pre-restore safety snapshot must never rotate away older snapshots:
    the oldest one is exactly what the operator may be restoring."""
    env, _data, backup_root = _backup_env(tmp_path)
    # KEEP=2 from the fixture: three runs normally leave only two snapshots.
    for _ in range(3):
        subprocess.run(["bash", str(BACKUP)], env=env, check=True, text=True, capture_output=True)
    kept = _snapshots(backup_root)
    assert len(kept) == 2, "rotation should keep exactly MIFP_BACKUP_KEEP snapshots"
    oldest_before = kept[0]

    no_prune = dict(env, MIFP_BACKUP_NO_PRUNE="1")
    subprocess.run(["bash", str(BACKUP)], env=no_prune, check=True, text=True, capture_output=True)

    assert oldest_before.is_dir(), "MIFP_BACKUP_NO_PRUNE must not delete the oldest snapshot"
    assert len(_snapshots(backup_root)) == 3

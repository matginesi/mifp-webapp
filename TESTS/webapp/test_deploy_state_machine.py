from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "deploy.sh"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "mifp"
    data = home / "data"
    data.mkdir(parents=True)
    (home / ".env").write_text(
        "MIFP_IMAGE_REPOSITORY='ghcr.io/example/mifp'\nMIFP_DEPLOY_MIN_FREE_MB='1'\n",
        encoding="utf-8",
    )
    (home / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    _write_executable(home / "configure.py", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
    _write_executable(home / "backup.sh", r"""#!/bin/bash
set -eu
root="${MIFP_BACKUP_ROOT:?}"
home="${MIFP_HOME:?}"
stamp="snapshot-test-$(date +%s%N)"
dir="$root/snapshots/$stamp"
mkdir -p "$dir/assets" "$dir/conferences" "$dir/config"
cp "$home/data/mifp.db" "$dir/mifp.db"
for name in assets conferences config; do cp -a "$home/data/$name/." "$dir/$name/" 2>/dev/null || true; done
python3 -c 'import hashlib,json,sys; from pathlib import Path; root=Path(sys.argv[1]); candidates=[root/"mifp.db"]+[p for d in ("assets","conferences","config") for p in sorted((root/d).rglob("*")) if p.is_file() and not p.is_symlink()]; files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates}; (root/"manifest.json").write_text(json.dumps({"format":"mifp-host-snapshot","version":1,"files":files}), encoding="utf-8")' "$dir"
ln -sfn "$stamp" "$root/snapshots/latest"
""")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "docker-state"
    state.mkdir()
    _write_executable(bin_dir / "id", "#!/bin/sh\n[ \"$1\" = -u ] && echo 0 || /usr/bin/id \"$@\"\n")
    _write_executable(bin_dir / "systemctl", "#!/bin/sh\nexit 0\n")
    _write_executable(bin_dir / "caddy", "#!/bin/sh\nexit 0\n")
    _write_executable(bin_dir / "curl", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "rsync",
        r"""#!/bin/bash
set -eu
src="${@: -2:1}"; dst="${@: -1}"
rm -rf "$dst"
mkdir -p "$dst"
cp -a "$src". "$dst"/
""",
    )
    _write_executable(
        bin_dir / "sqlite3",
        '''#!/usr/bin/env python3
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
    _write_executable(
        bin_dir / "docker",
        r'''#!/bin/sh
set -eu
state="${FAKE_DOCKER_STATE:?}"
repo='ghcr.io/example/mifp'
digest_for() {
  case "$1" in
    *sha-a*) printf '%s@sha256:%064d\n' "$repo" 1 ;;
    *sha-b*) printf '%s@sha256:%064d\n' "$repo" 2 ;;
    *@sha256:*) printf '%s\n' "$1" ;;
    *) printf '%s@sha256:%064d\n' "$repo" 9 ;;
  esac
}
if [ "${1:-}" = info ]; then exit 0; fi
if [ "${1:-}" = pull ]; then
  [ "${FAIL_PULL:-0}" = 1 ] && exit 1
  d="$(digest_for "$2")"; touch "$state/$(printf '%s' "$d" | tr '/:@' '___')"; exit 0
fi
if [ "${1:-}" = image ] && [ "${2:-}" = inspect ]; then
  if [ "${3:-}" = --format ]; then
    d="$(digest_for "$5")"; key="$(printf '%s' "$d" | tr '/:@' '___')"; [ -f "$state/$key" ] || exit 1; printf '%s\n' "$d"; exit 0
  fi
  d="$(digest_for "$3")"; key="$(printf '%s' "$d" | tr '/:@' '___')"; [ -f "$state/$key" ]
  exit $?
fi
if [ "${1:-}" = run ]; then
  mount=''
  prev=''
  for arg in "$@"; do
    if [ "$prev" = -v ]; then mount="$arg"; fi
    prev="$arg"
  done
  case "$*" in
    *'mifp_app.db.manage init '*)
      localdir="${mount%%:*}"; mkdir -p "$localdir"; printf 'fake-db' > "$localdir/mifp.db" ;;
    *'mifp_app.db.runtime_check'*)
      localfile="${mount%%:*}"
      perm="$(stat -c %a "$localfile")"
      [ "$perm" = 440 ] || [ "$perm" = 640 ] || { echo "unsafe preflight mode: $perm" >&2; exit 97; } ;;
  esac
  exit 0
fi
if [ "${1:-}" = compose ]; then
  case " $* " in
    *' version '*) exit 0 ;;
    *' down '*) [ "${FAIL_COMPOSE_DOWN:-0}" = 1 ] && exit 41 || exit 0 ;;
    *' ps '*'-q web'*) echo fake-container; exit 0 ;;
    *) exit 0 ;;
  esac
fi
exit 0
''',
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "MIFP_HOME": str(home),
            "MIFP_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
            "MIFP_RUNTIME_UID": str(os.getuid()),
            "MIFP_RUNTIME_GID": str(os.getgid()),
            "FAKE_DOCKER_STATE": str(state),
            "MIFP_BACKUP_ROOT": str(tmp_path / "host-backups"),
        }
    )
    return env, home


def _run(env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DEPLOY), *args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _release(home: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (home / "release.env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )




def _write_snapshot_manifest(snapshot: Path) -> None:
    import hashlib
    import json

    files: dict[str, str] = {}
    for candidate in [snapshot / "mifp.db"] + [
        path
        for dirname in ("assets", "conferences", "config")
        for path in sorted((snapshot / dirname).rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]:
        files[candidate.relative_to(snapshot).as_posix()] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    (snapshot / "manifest.json").write_text(
        json.dumps({"format": "mifp-host-snapshot", "version": 1, "files": files}),
        encoding="utf-8",
    )

def test_first_deploy_then_second_release_and_offline_rollback(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    first = _release(home)
    assert first["CURRENT_IMAGE"].endswith("@sha256:" + "0" * 63 + "1")
    assert (home / "data" / "mifp.db").is_file()

    _run(env, "deploy", "sha-b")
    second = _release(home)
    assert second["CURRENT_IMAGE"].endswith("@sha256:" + "0" * 63 + "2")
    assert second["PREVIOUS_IMAGE"] == first["CURRENT_IMAGE"]

    offline = dict(env, FAIL_PULL="1")
    _run(offline, "rollback")
    rolled = _release(home)
    assert rolled["CURRENT_IMAGE"] == first["CURRENT_IMAGE"]
    assert rolled["PREVIOUS_IMAGE"] == second["CURRENT_IMAGE"]


def test_mutable_or_legacy_deploy_invocation_is_rejected(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)
    result = _run(env, "deploy", "ghcr.io/example/mifp:latest", check=False)
    assert result.returncode != 0
    assert "tag mutabili" in result.stderr

    result = _run(env, "sha-a", check=False)
    assert result.returncode != 0
    assert "Comando sconosciuto" in result.stderr


def test_restore_db_replaces_database_under_current_release(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    release_before = _release(home)
    candidate = tmp_path / "restore.db"
    candidate.write_text("restored-db", encoding="utf-8")

    _run(env, "restore-db", str(candidate))

    assert (home / "data" / "mifp.db").read_text(encoding="utf-8") == "restored-db"
    assert _release(home) == release_before
    backups = list((home / "data" / "backups").glob("pre-restore-*.db"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "fake-db"


def test_schema_upgrade_requires_paired_rollback(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    original_release = _release(home)["CURRENT_IMAGE"]
    candidate = tmp_path / "schema-v-next.db"
    candidate.write_text("upgraded-db", encoding="utf-8")

    _run(env, "upgrade-db", "sha-b", str(candidate))
    upgraded = _release(home)
    assert upgraded["CURRENT_IMAGE"].endswith("@sha256:" + "0" * 63 + "2")
    assert (home / "data" / "mifp.db").read_text(encoding="utf-8") == "upgraded-db"
    checkpoint = (home / "upgrade.env").read_text(encoding="utf-8")
    assert f"PREVIOUS_IMAGE={original_release}" in checkpoint

    # A normal image-only rollback is unsafe after a schema transition.
    refused = _run(env, "rollback", check=False)
    assert refused.returncode != 0
    assert "rollback-upgrade" in refused.stderr

    # The paired rollback restores both the previous image and DB snapshot.
    _run(dict(env, FAIL_PULL="1"), "rollback-upgrade")
    restored = _release(home)
    assert restored["CURRENT_IMAGE"] == original_release
    assert (home / "data" / "mifp.db").read_text(encoding="utf-8") == "fake-db"
    assert not (home / "upgrade.env").exists()


def test_restore_snapshot_restores_database_and_file_trees(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    data = home / "data"
    (data / "assets" / "live.txt").write_text("live", encoding="utf-8")
    (data / "config" / "live.txt").write_text("live", encoding="utf-8")

    snapshot = tmp_path / "candidate-snapshot"
    for name in ("assets", "conferences", "config"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / "mifp.db").write_text("snapshot-db", encoding="utf-8")
    (snapshot / "assets" / "snapshot.txt").write_text("asset", encoding="utf-8")
    (snapshot / "config" / "snapshot.txt").write_text("config", encoding="utf-8")
    _write_snapshot_manifest(snapshot)

    _run(env, "restore-snapshot", str(snapshot))

    assert (data / "mifp.db").read_text(encoding="utf-8") == "snapshot-db"
    assert not (data / "assets" / "live.txt").exists()
    assert (data / "assets" / "snapshot.txt").read_text(encoding="utf-8") == "asset"
    assert not (data / "config" / "live.txt").exists()
    assert (data / "config" / "snapshot.txt").read_text(encoding="utf-8") == "config"


def test_restore_db_does_not_swap_when_service_cannot_stop(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    live = home / "data" / "mifp.db"
    before = live.read_bytes()
    candidate = tmp_path / "restore.db"
    candidate.write_text("restored-db", encoding="utf-8")

    result = _run(dict(env, FAIL_COMPOSE_DOWN="1"), "restore-db", str(candidate), check=False)

    assert result.returncode != 0
    assert "database invariato" in result.stderr
    assert live.read_bytes() == before


def test_restore_snapshot_rejects_tampered_files(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    snapshot = tmp_path / "candidate-snapshot"
    for name in ("assets", "conferences", "config"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / "mifp.db").write_text("snapshot-db", encoding="utf-8")
    (snapshot / "assets" / "asset.txt").write_text("before", encoding="utf-8")
    _write_snapshot_manifest(snapshot)
    (snapshot / "assets" / "asset.txt").write_text("tampered", encoding="utf-8")

    result = _run(env, "restore-snapshot", str(snapshot), check=False)

    assert result.returncode != 0
    assert "Snapshot corrotta" in result.stderr
    assert (home / "data" / "mifp.db").read_text(encoding="utf-8") == "fake-db"

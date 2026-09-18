from __future__ import annotations

import os
import pty
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

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
        "MIFP_IMAGE_REPOSITORY='ghcr.io/example/mifp'\n"
        "MIFP_DEPLOY_MIN_FREE_MB='1'\n"
        "MIFP_DOMAIN='example.invalid'\n",
        encoding="utf-8",
    )
    (home / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    _write_executable(home / "configure.py", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
    shutil.copy2(ROOT / "deploy" / "vps_config.py", home / "vps_config.py")
    shutil.copy2(ROOT / "deploy" / "check-events-archive.py", home / "check-events-archive.py")
    shutil.copy2(ROOT / "deploy" / "Caddyfile", home / "Caddyfile.example")
    shutil.copy2(ROOT / "deploy" / "local-hosts.sh", home / "local-hosts.sh")
    config_dir = tmp_path / "mifp-config"
    config_dir.mkdir()
    (config_dir / "config.env").write_text(
        "ENVIRONMENT=local\nDOMAIN=vpsbox.home.arpa\n"
        "WWW_DOMAIN=www.vpsbox.home.arpa\nEVENTS_DOMAIN=events.vpsbox.home.arpa\n"
        "IMAGE_REPOSITORY=ghcr.io/example/mifp\nADMIN_USERNAME=admin\n"
        "BACKUP_ENABLED=true\n",
        encoding="utf-8",
    )
    (config_dir / "secrets.env").write_text(
        "SECRET_KEY=0123456789abcdef0123456789abcdef\n"
        "ADMIN_PASSWORD_HASH='pbkdf2:sha256:600000$abcd$abcd'\n",
        encoding="utf-8",
    )
    docker_config = tmp_path / "docker-config.json"
    docker_config.write_text('{"auths":{"ghcr.io":{"auth":"test"}}}\n', encoding="utf-8")
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    caddy_dir = tmp_path / "caddy"
    caddy_dir.mkdir()
    (caddy_dir / "Caddyfile").write_text("example.invalid { respond 200 }\n", encoding="utf-8")
    (caddy_dir / "mifp-events-php.caddy").write_text("# empty\n", encoding="utf-8")
    _write_executable(home / "backup.sh", r"""#!/bin/bash
set -eu
root="${MIFP_BACKUP_ROOT:?}"
home="${MIFP_HOME:?}"
stamp="snapshot-test-$(date +%s%N)"
dir="$root/snapshots/$stamp"
mkdir -p "$dir/assets" "$dir/conferences" "$dir/config" "$dir/events" "$dir/events-private"
cp "$home/data/mifp.db" "$dir/mifp.db"
for name in assets conferences config; do cp -a "$home/data/$name/." "$dir/$name/" 2>/dev/null || true; done
cp -a "$home/events/." "$dir/events/" 2>/dev/null || true
cp -a "$home/events-private/." "$dir/events-private/" 2>/dev/null || true
if [ -f "$home/events-php-enabled.txt" ] && [ ! -L "$home/events-php-enabled.txt" ]; then cp "$home/events-php-enabled.txt" "$dir/events-php-enabled.txt"; else : > "$dir/events-php-enabled.txt"; fi
python3 -c 'import hashlib,json,sys; from pathlib import Path; root=Path(sys.argv[1]); candidates=[root/"mifp.db",root/"events-php-enabled.txt"]+[p for d in ("assets","conferences","config","events","events-private") for p in sorted((root/d).rglob("*")) if p.is_file() and not p.is_symlink()]; files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates}; (root/"manifest.json").write_text(json.dumps({"format":"mifp-host-snapshot","version":2,"files":files}), encoding="utf-8")' "$dir"
ln -sfn "$stamp" "$root/snapshots/latest"
""")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Keep subprocess-heavy shell state-machine tests independent from the
    # pytest virtualenv/plugin startup cost.  Production scripts still execute
    # real Python; only the no-op configure helper is short-circuited here.
    _write_executable(
        bin_dir / "python3",
        "#!/bin/sh\ncase \"${1:-}\" in */configure.py) exit 0;; esac\nexec /usr/bin/python3 \"$@\"\n",
    )
    state = tmp_path / "docker-state"
    state.mkdir()
    _write_executable(bin_dir / "id", "#!/bin/sh\n[ \"$1\" = -u ] && echo 0 || /usr/bin/id \"$@\"\n")
    _write_executable(bin_dir / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "install",
        "#!/bin/bash\nmode=0755; directory=0; targets=()\nwhile (($#)); do case \"$1\" in -m) mode=$2; shift 2;; -o|-g) shift 2;; -d) directory=1; shift;; *) targets+=(\"$1\"); shift;; esac; done\nif ((directory)); then for target in \"${targets[@]}\"; do mkdir -p \"$target\"; chmod \"$mode\" \"$target\"; done; else cp \"${targets[-2]}\" \"${targets[-1]}\"; chmod \"$mode\" \"${targets[-1]}\"; fi\n",
    )
    _write_executable(
        bin_dir / "systemctl",
        "#!/bin/sh\n"
        "if [ \"${FAIL_BACKUP_TIMER:-0}\" = 1 ] && "
        "[ \"${3:-}\" = mifp-backup.timer ]; then exit 1; fi\n"
        "exit 0\n",
    )
    _write_executable(bin_dir / "caddy", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "curl",
        "#!/bin/sh\n"
        "[ \"${FAIL_READY:-0}\" = 1 ] && exit 1\n"
        "if [ \"${FAIL_DIGEST_TWO_READY:-0}\" = 1 ] && "
        "grep -q 'sha256:0*2$' \"${FAKE_DOCKER_STATE:?}/active-image\" 2>/dev/null; then exit 1; fi\n"
        "exit 0\n",
    )
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
  if [ "${FAIL_PULL:-0}" = 1 ]; then echo 'unauthorized: authentication required' >&2; exit 1; fi
  echo "Pulling from example/mifp"
  echo "Digest: sha256:verbose-progress-must-not-be-returned"
  d="$(digest_for "$2")"; touch "$state/$(printf '%s' "$d" | tr '/:@' '___')"; exit 0
fi
if [ "${1:-}" = login ]; then
  token=''; IFS= read -r token
  [ "${FAIL_LOGIN:-0}" = 1 ] && exit 1
  [ "$2" = ghcr.io ] && [ "$3" = --username ] && [ "$5" = --password-stdin ] || exit 2
  [ "$token" = "${EXPECTED_REGISTRY_TOKEN:-}" ] || exit 3
  touch "$state/registry-login-ok"
  echo 'Login Succeeded'
  exit 0
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
    *' up '*) printf '%s\n' "${MIFP_IMAGE:?}" > "$state/active-image"; exit 0 ;;
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
            "MIFP_EVENTS_PHP_INCLUDE": str(caddy_dir / "mifp-events-php.caddy"),
            "MIFP_CADDY_CONFIG": str(caddy_dir / "Caddyfile"),
            "MIFP_CONFIG_DIR": str(config_dir),
            "MIFP_DOCKER_CONFIG_FILE": str(docker_config),
            "MIFP_HOSTS_FILE": str(hosts_file),
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


def _run_tty(env: dict[str, str], *args: str, input_text: str) -> tuple[int, str]:
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["bash", str(DEPLOY), *args],
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
    )
    os.close(slave)
    chunks: list[bytes] = []
    answers = iter(input_text.splitlines())
    prompts = [b"GitHub username:", b"GitHub PAT classic"]
    answered = 0
    deadline = time.monotonic() + 10
    while process.poll() is None and time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.1)
        if ready:
            try:
                chunks.append(os.read(master, 4096))
            except OSError:
                break
            combined = b"".join(chunks)
            if answered < len(prompts) and prompts[answered] in combined:
                os.write(master, (next(answers) + "\n").encode())
                answered += 1
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AssertionError(
            "interactive mifpctl command did not terminate: "
            + b"".join(chunks).decode(errors="replace")
        )
    while True:
        try:
            chunks.append(os.read(master, 4096))
        except OSError:
            break
    os.close(master)
    return process.returncode, b"".join(chunks).decode(errors="replace")


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


def test_security_check_passes_on_hardened_preinit_host(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    (home / "events").mkdir()
    (home / "events-private").mkdir()
    config_dir = Path(env["MIFP_CONFIG_DIR"])
    (config_dir / "config.env").chmod(0o640)
    (config_dir / "secrets.env").chmod(0o600)
    Path(env["MIFP_DOCKER_CONFIG_FILE"]).chmod(0o600)
    bin_dir = Path(env["PATH"].split(":", 1)[0])
    _write_executable(bin_dir / "ss", "#!/bin/sh\nexit 0\n")
    # A hardened host must expose an effective sshd policy and an active
    # firewall; security-check verifies both instead of assuming them.
    _write_executable(
        bin_dir / "sshd",
        "#!/bin/sh\n"
        "[ \"$1\" = -T ] || exit 0\n"
        "printf 'port 22\\npasswordauthentication no\\npermitrootlogin prohibit-password\\n'\n",
    )
    _write_executable(
        bin_dir / "ufw",
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  status) printf 'Status: active\\n\\nTo Action From\\n-- ------ ----\\n22/tcp LIMIT Anywhere (v6)\\n80/tcp ALLOW Anywhere (v6)\\n443/tcp ALLOW Anywhere (v6)\\n' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )

    result = _run(env, "security-check")

    assert "Security check: OK" in result.stdout
    assert "Container checks: NOT INITIALIZED" in result.stdout
    assert "SSH password authentication disabled" in result.stdout
    assert "Firewall active" in result.stdout
    assert "Firewall IPv6 rules present" in result.stdout


def test_security_check_fails_when_ssh_password_auth_is_enabled(tmp_path: Path) -> None:
    """The check must not report OK on a host that still allows password SSH."""
    env, home = _env(tmp_path)
    (home / "events").mkdir()
    (home / "events-private").mkdir()
    config_dir = Path(env["MIFP_CONFIG_DIR"])
    (config_dir / "config.env").chmod(0o640)
    (config_dir / "secrets.env").chmod(0o600)
    Path(env["MIFP_DOCKER_CONFIG_FILE"]).chmod(0o600)
    bin_dir = Path(env["PATH"].split(":", 1)[0])
    _write_executable(bin_dir / "ss", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "sshd",
        "#!/bin/sh\n[ \"$1\" = -T ] || exit 0\nprintf 'port 22\\npasswordauthentication yes\\npermitrootlogin yes\\n'\n",
    )
    _write_executable(
        bin_dir / "ufw",
        "#!/bin/sh\ncase \"$1\" in status) printf 'Status: active\\n22/tcp LIMIT Anywhere (v6)\\n80/tcp ALLOW Anywhere (v6)\\n443/tcp ALLOW Anywhere (v6)\\n' ;; *) exit 0 ;; esac\n",
    )

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "PasswordAuthentication" in result.stdout


def test_init_uses_latest_only_as_selector_and_persists_clean_digest(tmp_path: Path) -> None:
    env, home = _env(tmp_path)

    result = _run(env, "init")

    release = _release(home)
    expected = "ghcr.io/example/mifp@sha256:" + "0" * 63 + "9"
    assert release == {"CURRENT_IMAGE": expected, "PREVIOUS_IMAGE": ""}
    assert "Pulling from example/mifp" in result.stderr
    assert "Pulling from example/mifp" not in release["CURRENT_IMAGE"]
    assert ":latest" not in (home / "release.env").read_text(encoding="utf-8")
    assert (home / "data" / "mifp.db").is_file()


def test_init_refuses_incomplete_progressive_configuration(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    config_file = Path(env["MIFP_CONFIG_DIR"]) / "config.env"
    config_file.write_text(
        "ENVIRONMENT=production\nIMAGE_REPOSITORY=ghcr.io/example/mifp\n"
        "ADMIN_USERNAME=admin\nBACKUP_ENABLED=true\n",
        encoding="utf-8",
    )

    result = _run(env, "init", check=False)

    assert result.returncode != 0
    assert "DOMAIN: missing required" in result.stdout
    assert not (home / "release.env").exists()
    assert not (home / "data" / "mifp.db").exists()


def test_init_refuses_an_existing_release(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "init")

    result = _run(env, "init", check=False)

    assert result.returncode != 0
    assert "Release già inizializzata" in result.stderr


def test_init_health_failure_leaves_no_db_or_release_state(tmp_path: Path) -> None:
    env, home = _env(tmp_path)

    result = _run(dict(env, FAIL_READY="1", MIFP_READY_ATTEMPTS="1"), "init", check=False)

    assert result.returncode != 0
    assert "nessuna release o database iniziale" in result.stderr
    assert not (home / "release.env").exists()
    assert not (home / "data" / "mifp.db").exists()


def test_init_backup_timer_failure_is_atomic(tmp_path: Path) -> None:
    env, home = _env(tmp_path)

    result = _run(dict(env, FAIL_BACKUP_TIMER="1"), "init", check=False)

    assert result.returncode != 0
    assert "timer backup non abilitato" in result.stderr
    assert not (home / "release.env").exists()
    assert not (home / "data" / "mifp.db").exists()


def test_deploy_health_failure_restores_previous_release(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)

    result = _run(
        dict(env, FAIL_DIGEST_TWO_READY="1", MIFP_READY_ATTEMPTS="1"),
        "deploy",
        "sha-b",
        check=False,
    )

    assert result.returncode != 0
    assert _release(home) == before
    active = (Path(env["FAKE_DOCKER_STATE"]) / "active-image").read_text(encoding="utf-8").strip()
    assert active == before["CURRENT_IMAGE"]


def test_pull_auth_failure_explains_registry_login(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)

    result = _run(dict(env, FAIL_PULL="1"), "init", check=False)

    assert result.returncode != 0
    assert "GHCR authentication required" in result.stderr
    assert "sudo mifpctl registry-login" in result.stderr


def test_registry_login_uses_password_stdin_without_echoing_token(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)
    token = "github-pat-test-value"

    returncode, output = _run_tty(
        dict(env, EXPECTED_REGISTRY_TOKEN=token),
        "registry-login",
        input_text=f"matteo\n{token}\n",
    )

    assert returncode == 0, output
    assert "GHCR login successful" in output
    assert token not in output
    assert (Path(env["FAKE_DOCKER_STATE"]) / "registry-login-ok").is_file()


def test_registry_login_failure_is_clear_and_does_not_echo_token(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)
    token = "github-pat-rejected-value"

    returncode, output = _run_tty(
        dict(env, EXPECTED_REGISTRY_TOKEN=token, FAIL_LOGIN="1"),
        "registry-login",
        input_text=f"matteo\n{token}\n",
    )

    assert returncode != 0
    assert "GHCR login failed" in output
    assert "read:packages" in output
    assert token not in output


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


def test_restore_snapshot_rejects_injected_php_allowlist(tmp_path: Path) -> None:
    import hashlib
    import json

    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    snapshot = tmp_path / "candidate-snapshot-injected-policy"
    for name in ("assets", "conferences", "config", "events", "events-private"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / "mifp.db").write_text("snapshot-db", encoding="utf-8")
    (snapshot / "events-php-enabled.txt").write_text("safe/regform\nrespond /pwned 200\n", encoding="utf-8")
    files = {}
    for candidate in [snapshot / "mifp.db", snapshot / "events-php-enabled.txt"]:
        files[candidate.relative_to(snapshot).as_posix()] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    (snapshot / "manifest.json").write_text(
        json.dumps({"format": "mifp-host-snapshot", "version": 2, "files": files}),
        encoding="utf-8",
    )

    result = _run(env, "restore-snapshot", str(snapshot), check=False)

    assert result.returncode != 0
    assert "Snapshot corrotta" in result.stderr
    assert (home / "data" / "mifp.db").read_text(encoding="utf-8") == "fake-db"


def test_restore_snapshot_v2_restores_public_and_private_event_trees(tmp_path: Path) -> None:
    import hashlib
    import json

    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")

    (home / "events" / "OLD").mkdir(parents=True)
    (home / "events" / "OLD" / "index.html").write_text("old", encoding="utf-8")
    (home / "events-private" / "registrations").mkdir(parents=True)
    (home / "events-private" / "registrations" / "old.csv").write_text("old", encoding="utf-8")

    snapshot = tmp_path / "candidate-snapshot-v2"
    for name in ("assets", "conferences", "config", "events", "events-private"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / "mifp.db").write_text("snapshot-db", encoding="utf-8")
    (snapshot / "events" / "PLMCN-2025").mkdir()
    (snapshot / "events" / "PLMCN-2025" / "index.html").write_text("historic", encoding="utf-8")
    (snapshot / "events" / "PLMCN-2025" / "regform").mkdir()
    (snapshot / "events" / "PLMCN-2025" / "regform" / "index.php").write_text("<?php echo 'closed';", encoding="utf-8")
    (snapshot / "events-private" / "registrations").mkdir()
    (snapshot / "events-private" / "registrations" / "future.csv").write_text("private", encoding="utf-8")
    (snapshot / "events-php-enabled.txt").write_text("PLMCN-2025/regform\n", encoding="utf-8")

    files: dict[str, str] = {}
    for candidate in [snapshot / "mifp.db", snapshot / "events-php-enabled.txt"] + [
        path
        for dirname in ("assets", "conferences", "config", "events", "events-private")
        for path in sorted((snapshot / dirname).rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]:
        files[candidate.relative_to(snapshot).as_posix()] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    (snapshot / "manifest.json").write_text(
        json.dumps({"format": "mifp-host-snapshot", "version": 2, "files": files}),
        encoding="utf-8",
    )

    _run(env, "restore-snapshot", str(snapshot))

    assert not (home / "events" / "OLD").exists()
    assert (home / "events" / "PLMCN-2025" / "index.html").read_text(encoding="utf-8") == "historic"
    assert not (home / "events-private" / "registrations" / "old.csv").exists()
    assert (home / "events-private" / "registrations" / "future.csv").read_text(encoding="utf-8") == "private"
    assert (home / "events-php-enabled.txt").read_text(encoding="utf-8") == "PLMCN-2025/regform\n"
    rendered = Path(env["MIFP_EVENTS_PHP_INCLUDE"]).read_text(encoding="utf-8")
    assert "PLMCN-2025/regform" in rendered


def test_events_import_is_atomic_and_rollbackable(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    (home / "events-php-enabled.txt").write_text("OLD/regform\n", encoding="utf-8")

    first = tmp_path / "events-backup-1"
    (first / "PLMCN-2025").mkdir(parents=True)
    (first / "PLMCN-2025" / "index.html").write_text("v1", encoding="utf-8")
    _run(env, "events-import", str(first))
    assert (home / "events" / "PLMCN-2025" / "index.html").read_text(encoding="utf-8") == "v1"
    assert (home / "events-php-enabled.txt").read_text(encoding="utf-8") == ""
    assert "OLD/regform" not in Path(env["MIFP_EVENTS_PHP_INCLUDE"]).read_text(encoding="utf-8")

    second = tmp_path / "events-backup-2"
    (second / "PLMCN-2025").mkdir(parents=True)
    (second / "PLMCN-2025" / "index.html").write_text("v2", encoding="utf-8")
    _run(env, "events-import", str(second))
    assert (home / "events" / "PLMCN-2025" / "index.html").read_text(encoding="utf-8") == "v2"
    assert (home / "events.previous" / "PLMCN-2025" / "index.html").read_text(encoding="utf-8") == "v1"

    _run(env, "events-rollback")
    assert (home / "events" / "PLMCN-2025" / "index.html").read_text(encoding="utf-8") == "v1"
    assert (home / "events.previous" / "PLMCN-2025" / "index.html").read_text(encoding="utf-8") == "v2"


def test_events_import_rejects_symlinks(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    source = tmp_path / "events-unsafe"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "leak").symlink_to(outside)

    result = _run(env, "events-import", str(source), check=False)

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not (home / "events").exists()


def test_events_import_rejects_fifo_special_file(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    source = tmp_path / "events-special"
    source.mkdir()
    os.mkfifo(source / "pipe")

    result = _run(env, "events-import", str(source), check=False)

    assert result.returncode != 0
    assert "file speciale" in result.stderr
    assert not (home / "events").exists()


def test_php_enable_rejects_empty_and_traversal_prefixes(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)
    for unsafe in ("/", "../regform", "event/../../outside", "event//regform"):
        result = _run(env, "events-php-enable", unsafe, check=False)
        assert result.returncode != 0
        assert "Path conferenza" in result.stderr


def test_php_execution_requires_explicit_event_prefix(tmp_path: Path) -> None:
    import socket
    import tempfile

    env, home = _env(tmp_path)
    regform = home / "events" / "PLMCN-2027" / "regform"
    regform.mkdir(parents=True)
    (home / "events-private").mkdir()
    (regform / "index.php").write_text("<?php echo 'ok';", encoding="utf-8")
    (home / "php-fpm.service").write_text("php8.3-fpm.service\n", encoding="utf-8")

    include = Path(env["MIFP_EVENTS_PHP_INCLUDE"])
    config = Path(env["MIFP_CADDY_CONFIG"])

    # Linux limits AF_UNIX paths to roughly 108 bytes.  test_all.sh places
    # pytest's tmp_path below an intentionally isolated (and potentially long)
    # runtime directory, so use a short dedicated directory below /tmp.
    with tempfile.TemporaryDirectory(prefix="mifp-socket-", dir="/tmp") as socket_dir:
        socket_path = Path(socket_dir) / "events.sock"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(socket_path))
        except PermissionError:
            sock.close()
            pytest.skip("sandbox does not allow Unix-domain socket creation")
        try:
            php_env = dict(
                env,
                MIFP_EVENTS_PHP_INCLUDE=str(include),
                MIFP_CADDY_CONFIG=str(config),
                MIFP_EVENTS_PHP_SOCKET=str(socket_path),
            )
            _run(php_env, "events-php-enable", "PLMCN-2027/regform")
            _run(php_env, "events-php-enable", "PLMCN-2027/regform")
            rendered = include.read_text(encoding="utf-8")
            assert "path /PLMCN-2027/regform /PLMCN-2027/regform/*" in rendered
            assert f"unix/{socket_path}" in rendered
            assert (home / "events-php-enabled.txt").read_text(encoding="utf-8").count(
                "PLMCN-2027/regform"
            ) == 1

            _run(php_env, "events-php-disable", "PLMCN-2027/regform")
            assert "PLMCN-2027/regform" not in include.read_text(encoding="utf-8")

            pre_init = _run(php_env, "doctor")
            assert "DB: NOT INITIALIZED" in pre_init.stdout
            assert "Release: NOT INITIALIZED" in pre_init.stdout
            assert "HTTPS events: OK" in pre_init.stdout

            _run(php_env, "init")
            post_init = _run(php_env, "doctor")
            assert "DB: OK" in post_init.stdout
            assert "Release: OK" in post_init.stdout
            assert "Application health: OK" in post_init.stdout
        finally:
            sock.close()


def test_security_check_fails_when_docker_tcp_api_is_exposed(tmp_path: Path) -> None:
    """A Docker daemon reachable over a TCP API is a real exposure, so it must
    be an error rather than a warning."""
    env, home = _env(tmp_path)
    (home / "events").mkdir()
    (home / "events-private").mkdir()
    config_dir = Path(env["MIFP_CONFIG_DIR"])
    (config_dir / "config.env").chmod(0o640)
    (config_dir / "secrets.env").chmod(0o600)
    Path(env["MIFP_DOCKER_CONFIG_FILE"]).chmod(0o600)
    bin_dir = Path(env["PATH"].split(":", 1)[0])
    _write_executable(
        bin_dir / "ss",
        "#!/bin/sh\nprintf 'LISTEN 0 128 0.0.0.0:2375 0.0.0.0:*\\n'\n",
    )
    _write_executable(
        bin_dir / "sshd",
        "#!/bin/sh\n[ \"$1\" = -T ] || exit 0\nprintf 'port 22\\npasswordauthentication no\\npermitrootlogin prohibit-password\\n'\n",
    )
    _write_executable(
        bin_dir / "ufw",
        "#!/bin/sh\ncase \"$1\" in status) printf 'Status: active\\n22/tcp LIMIT Anywhere (v6)\\n80/tcp ALLOW Anywhere (v6)\\n443/tcp ALLOW Anywhere (v6)\\n' ;; *) exit 0 ;; esac\n",
    )

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "Docker API TCP port" in result.stdout

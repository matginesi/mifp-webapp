from __future__ import annotations

import os
import pty
import re
import select
import shutil
import stat
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
    (home / ".env").chmod(0o600)
    (home / "compose.yaml").write_text(
        "services:\n"
        "  web:\n"
        "    image: ${MIFP_IMAGE:?required}\n"
        "    ports:\n"
        '      - "127.0.0.1:8000:8000"\n'
        "    volumes:\n"
        '      - "${MIFP_DATA_DIR:-/opt/mifp/data}:/app/data"\n',
        encoding="utf-8",
    )
    _write_executable(home / "configure.py", "#!/usr/bin/env python3\nraise SystemExit(0)\n")
    shutil.copy2(ROOT / "deploy" / "vps_config.py", home / "vps_config.py")
    shutil.copy2(ROOT / "deploy" / "Caddyfile", home / "Caddyfile.example")
    shutil.copy2(ROOT / "deploy" / "local-hosts.sh", home / "local-hosts.sh")
    config_dir = tmp_path / "mifp-config"
    config_dir.mkdir()
    (config_dir / "config.env").write_text(
        "ENVIRONMENT=local\nDOMAIN=vpsbox.home.arpa\n"
        "WWW_DOMAIN=www.vpsbox.home.arpa\nEVENTS_PUBLISH_BACKEND=local-vps\n"
        "EVENTS_PUBLIC_BASE_URL=https://events.vpsbox.home.arpa\n"
        f"EVENTS_LOCAL_ROOT={tmp_path / 'event-sites'}\nEVENTS_REMOTE_PROTOCOL=ftps\n"
        "IMAGE_REPOSITORY=ghcr.io/example/mifp\nADMIN_USERNAME=admin\n"
        "BACKUP_ENABLED=true\n",
        encoding="utf-8",
    )
    (config_dir / "secrets.env").write_text(
        "SECRET_KEY=0123456789abcdef0123456789abcdef\n"
        "ADMIN_PASSWORD_HASH='pbkdf2:sha256:600000$abcd$abcd'\n",
        encoding="utf-8",
    )
    (config_dir / "config.env").chmod(0o640)
    (config_dir / "secrets.env").chmod(0o600)
    secret_material = config_dir / "secrets"
    secret_material.mkdir(mode=0o700)
    for name, value in {
        "mifp_secret_key": "0123456789abcdef0123456789abcdef",
        "mifp_admin_password_hash": "pbkdf2:sha256:600000$abcd$abcd",
        "mifp_smtp_password": "",
        "mifp_events_remote_password": "",
    }.items():
        target = secret_material / name
        target.write_text(value, encoding="utf-8")
        target.chmod(0o400)
    docker_config = tmp_path / "docker-config.json"
    docker_config.write_text('{}\n', encoding="utf-8")
    docker_config.chmod(0o600)
    hosts_file = tmp_path / "hosts"
    hosts_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    caddy_dir = tmp_path / "caddy"
    caddy_dir.mkdir()
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    shutil.copy2(ROOT / "deploy" / "mifp-alert-check.timer", systemd_dir / "mifp-alert-check.timer")
    (caddy_dir / "Caddyfile").write_text("example.invalid { respond 200 }\n", encoding="utf-8")
    (caddy_dir / "mifp-events-php.caddy").write_text("# empty\n", encoding="utf-8")
    php_state = home / "events-php-enabled.txt"
    php_state.write_text("", encoding="utf-8")
    (home / "php-fpm.service").write_text("php8.3-fpm.service\n", encoding="utf-8")
    private_root = tmp_path / "events-private"
    for name in ("registrations", "uploads", "sessions", "tmp"):
        (private_root / name).mkdir(parents=True, exist_ok=True)
    private_root.chmod(0o700)
    events_root = tmp_path / "event-sites"
    events_root.mkdir()
    _write_executable(home / "backup.sh", r"""#!/bin/bash
set -eu
root="${MIFP_BACKUP_ROOT:?}"
home="${MIFP_HOME:?}"
stamp="snapshot-test-$(date +%s%N)"
dir="$root/snapshots/$stamp"
mkdir -p "$dir/assets" "$dir/conferences" "$dir/config"
cp "$home/data/mifp.db" "$dir/mifp.db"
for name in assets conferences config; do cp -a "$home/data/$name/." "$dir/$name/" 2>/dev/null || true; done
python3 -c 'import hashlib,json,sys; from pathlib import Path; root=Path(sys.argv[1]); candidates=[root/"mifp.db"]+[p for d in ("assets","conferences","config") for p in sorted((root/d).rglob("*")) if p.is_file() and not p.is_symlink()]; files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates}; (root/"manifest.json").write_text(json.dumps({"format":"mifp-host-snapshot","version":3,"files":files}), encoding="utf-8")' "$dir"
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
    _write_executable(
        bin_dir / "id",
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = -u ]; then echo 0; exit 0; fi\n"
        "if [ \"${1:-}\" = mifp-events ]; then exit 0; fi\n"
        "exec /usr/bin/id \"$@\"\n",
    )
    _write_executable(bin_dir / "chown", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "install",
        "#!/bin/bash\nmode=0755; directory=0; targets=()\nwhile (($#)); do case \"$1\" in -m) mode=$2; shift 2;; -o|-g) shift 2;; -d) directory=1; shift;; *) targets+=(\"$1\"); shift;; esac; done\nif ((directory)); then for target in \"${targets[@]}\"; do mkdir -p \"$target\"; chmod \"$mode\" \"$target\"; done; else cp \"${targets[-2]}\" \"${targets[-1]}\"; chmod \"$mode\" \"${targets[-1]}\"; fi\n",
    )
    _write_executable(
        bin_dir / "systemctl",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"${FAKE_SYSTEMCTL_LOG:-/dev/null}\"\n"
        "if [ \"${1:-}\" = is-enabled ] && [ \"${2:-}\" = mifp-backup.timer ]; then "
        "  if [ \"${BACKUP_TIMER_DISABLED:-0}\" = 1 ]; then echo disabled; exit 1; fi; "
        "  echo enabled; exit 0; fi\n"
        "if [ \"${FAIL_BACKUP_TIMER:-0}\" = 1 ] && [ \"${1:-}\" = enable ] && "
        "[ \"${3:-}\" = mifp-backup.timer ]; then exit 1; fi\n"
        "exit 0\n",
    )
    _write_executable(bin_dir / "caddy", "#!/bin/sh\nexit 0\n")
    _write_executable(bin_dir / "msmtp", "#!/bin/sh\nexit 0\n")
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
import os
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
if os.environ.get("EMIT_SQLITE_SHM") == "1" and source and "preflight-" in source:
    open(source + "-shm", "wb").close()
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
    *:latest) printf '%s@sha256:%064d\n' "$repo" "${LATEST_DIGEST_NUM:-9}" ;;
    *@sha256:*) printf '%s\n' "$1" ;;
    *) printf '%s@sha256:%064d\n' "$repo" 9 ;;
  esac
}
if [ "${1:-}" = info ]; then exit 0; fi
if [ "${1:-}" = manifest ] && [ "${2:-}" = inspect ]; then
  printf '%s\n' "${DOCKER_CONFIG:-authenticated}" >> "$state/manifest-configs"
  if [ "${FAIL_MANIFEST_AUTH:-0}" = 1 ]; then echo 'denied: requested access to the resource is denied (status: 403)' >&2; exit 1; fi
  if [ "${FAIL_MANIFEST_MISSING:-0}" = 1 ]; then echo 'manifest unknown' >&2; exit 1; fi
  exit 0
fi
if [ "${1:-}" = buildx ] && [ "${2:-}" = imagetools ] && [ "${3:-}" = inspect ]; then
  printf '%s\n' "${DOCKER_CONFIG:-authenticated}" >> "$state/imagetools-configs"
  if [ "${FAIL_IMAGETOOLS_ANON_AUTH:-0}" = 1 ] && [ "${DOCKER_CONFIG:-authenticated}" != authenticated ]; then
    echo 'denied: requested access to the resource is denied (status: 403)' >&2; exit 1
  fi
  if [ "${FAIL_IMAGETOOLS_AUTH:-0}" = 1 ]; then echo 'unauthorized: authentication required' >&2; exit 1; fi
  if [ "${FAIL_IMAGETOOLS_MISSING:-0}" = 1 ]; then echo 'manifest unknown' >&2; exit 1; fi
  if [ "${FAIL_IMAGETOOLS_OTHER:-0}" = 1 ]; then echo 'registry transport failed' >&2; exit 1; fi
  d="$(digest_for "$4")"
  printf '{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","digest":"%s","size":123,"manifests":[]}\n' "${d#*@}"
  exit 0
fi
if [ "${1:-}" = pull ]; then
  count="$(cat "$state/pull-count" 2>/dev/null || printf 0)"; printf '%s\n' "$((count + 1))" > "$state/pull-count"
  if [ "${FAIL_PULL:-0}" = 1 ]; then echo 'unauthorized: authentication required' >&2; exit 1; fi
  echo "Pulling from example/mifp"
  echo "Digest: sha256:verbose-progress-must-not-be-returned"
  for image_ref in "$@"; do :; done
  d="$(digest_for "$image_ref")"; touch "$state/$(printf '%s' "$d" | tr '/:@' '___')"; exit 0
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
    case "${4:-}" in
      *org.mifp.deploy-contract*)
        if [ "${FAKE_LEGACY_DIGEST_ONE:-0}" = 1 ] && printf '%s' "${5:-}" | grep -q 'sha256:0*1$'; then
          printf '<no value>\n'
        else
          printf '%s\n' "${FAKE_DEPLOY_CONTRACT:-2}"
        fi
        exit 0 ;;
    esac
    d="$(digest_for "$5")"; key="$(printf '%s' "$d" | tr '/:@' '___')"; [ -f "$state/$key" ] || exit 1; printf '%s\n' "$d"; exit 0
  fi
  d="$(digest_for "$3")"; key="$(printf '%s' "$d" | tr '/:@' '___')"; [ -f "$state/$key" ]
  exit $?
fi
if [ "${1:-}" = inspect ] && [ "${2:-}" = --format ]; then
  case "$3" in
    *State.Status*) printf '%s|%s|%s\n' "${FAKE_CONTAINER_STATUS:-restarting}" "${FAKE_CONTAINER_EXIT_CODE:-3}" "${FAKE_CONTAINER_ERROR:-}" ;;
    *Config.User*) printf '10001|false|default|true|["no-new-privileges:true"]|[]\n' ;;
    *Config.Env*)
      printf 'FLASK_DEBUG=0\nFLASK_ENV=production\n'
      printf 'SECRET_KEY_FILE=/run/secrets/mifp_secret_key\n'
      printf 'ADMIN_PASSWORD_HASH_FILE=/run/secrets/mifp_admin_password_hash\n'
      printf 'SMTP_PASSWORD_FILE=/run/secrets/mifp_smtp_password\n'
      printf 'EVENTS_REMOTE_PASSWORD_FILE=/run/secrets/mifp_events_remote_password\n'
      [ -z "${FAKE_CONTAINER_SECRET_NAME:-}" ] || printf '%s=%s\n' "$FAKE_CONTAINER_SECRET_NAME" "${FAKE_CONTAINER_SECRET_VALUE:-hidden}"
      ;;
    *Mounts*)
      printf '/var/lib/docker/secrets/one|/run/secrets/mifp_secret_key|bind\n'
      printf '/var/lib/docker/secrets/two|/run/secrets/mifp_admin_password_hash|bind\n'
      printf '/var/lib/docker/secrets/three|/run/secrets/mifp_smtp_password|bind\n'
      printf '/var/lib/docker/secrets/four|/run/secrets/mifp_events_remote_password|bind\n'
      [ -z "${FAKE_SENSITIVE_MOUNT_SOURCE:-}" ] || printf '%s|/mnt/unexpected|bind\n' "$FAKE_SENSITIVE_MOUNT_SOURCE"
      ;;
  esac
  exit 0
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
      [ "${FAIL_DB_PREFLIGHT:-0}" = 1 ] && { echo 'database contract mismatch' >&2; exit 96; }
      localfile="${mount%%:*}"
      perm="$(stat -c %a "$localfile")"
      [ "$perm" = 440 ] || [ "$perm" = 640 ] || { echo "unsafe preflight mode: $perm" >&2; exit 97; } ;;
  esac
  exit 0
fi
if [ "${1:-}" = compose ]; then
  case " $* " in
    *' version '*) exit 0 ;;
    *' config -q '*) [ "${FAIL_COMPOSE_CONFIG:-0}" = 1 ] && { echo 'compose config invalid' >&2; exit 43; } || exit 0 ;;
    *' config --format json '*)
      host_ip="${FAKE_COMPOSE_HOST_IP:-127.0.0.1}"
      printf '{"services":{"web":{"ports":[{"host_ip":"%s","target":8000,"published":"8000","protocol":"tcp"}],"volumes":[{"type":"bind","source":"%s","target":"/app/data"}],"tmpfs":["/tmp:size=256m"],"security_opt":["no-new-privileges:true"],"healthcheck":{"test":["CMD","true"]}}}}\n' "$host_ip" "${MIFP_DATA_DIR:?}"
      exit 0 ;;
    *' down '*) [ "${FAIL_COMPOSE_DOWN:-0}" = 1 ] && exit 41 || exit 0 ;;
    *' ps '*'-q web'*) echo fake-container; exit 0 ;;
    *' up '*)
      [ "${FAIL_COMPOSE_UP:-0}" = 1 ] && { echo 'compose create failed' >&2; exit 42; }
      if [ "${FAIL_HOST_COMPOSE_UP:-0}" = 1 ] && printf '%s' "${MIFP_IMAGE:?}" | grep -q 'sha256:0*2$'; then
        echo 'cannot create secret "mifp_mifp_secret_key" in read-only service web: `file` is the sole supported option' >&2
        exit 42
      fi
      if [ "${FAIL_CANDIDATE_COMPOSE_UP:-0}" = 1 ] && printf '%s' "${MIFP_IMAGE:?}" | grep -q 'sha256:0*2$'; then
        echo 'candidate create failed' >&2; exit 42
      fi
      count="$(cat "$state/compose-up-count" 2>/dev/null || printf 0)"; printf '%s\n' "$((count + 1))" > "$state/compose-up-count"; printf '%s\n' "${MIFP_IMAGE:?}" > "$state/active-image"; exit 0 ;;
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
            "MIFP_SECURITY_ROOT_UID": str(os.getuid()),
            "MIFP_SECURITY_ROOT_GID": str(os.getgid()),
            "MIFP_SECURITY_EVENTS_UID": str(os.getuid()),
            "MIFP_SECURITY_EVENTS_GID": str(os.getgid()),
            "FAKE_DOCKER_STATE": str(state),
            "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
            "MIFP_BACKUP_ROOT": str(tmp_path / "host-backups"),
            "MIFP_EVENTS_PHP_INCLUDE": str(caddy_dir / "mifp-events-php.caddy"),
            "MIFP_EVENTS_PHP_STATE": str(php_state),
            "MIFP_EVENTS_PHP_SOCKET": str(tmp_path / "mifp-events.sock"),
            "MIFP_EVENTS_PRIVATE_DIR": str(tmp_path / "events-private"),
            "MIFP_MAIL_RELAY_CONFIG": str(tmp_path / "msmtprc"),
            "MIFP_SYSTEMD_DIR": str(systemd_dir),
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


def _prepare_security_host(env: dict[str, str]) -> None:
    bin_dir = Path(env["PATH"].split(":", 1)[0])
    _write_executable(bin_dir / "ss", "#!/bin/sh\nexit 0\n")
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
        "  status) printf 'Status: active\\n22/tcp LIMIT Anywhere (v6)\\n80/tcp ALLOW Anywhere (v6)\\n443/tcp ALLOW Anywhere (v6)\\n' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
    )


def _set_running_release(home: Path) -> None:
    (home / "release.env").write_text(
        "CURRENT_IMAGE=ghcr.io/example/mifp@sha256:" + "1" * 64 + "\nPREVIOUS_IMAGE=\n",
        encoding="utf-8",
    )
    (home / "release.env").chmod(0o600)


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


def test_event_caddy_site_is_enabled_only_for_local_vps_backend(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)
    caddy = Path(env["MIFP_CADDY_CONFIG"])

    result = _run(env, "config-set", "EVENTS_PUBLISH_BACKEND", "local-vps", check=False)
    assert result.returncode == 0, result.stderr
    local_config = caddy.read_text(encoding="utf-8")
    assert "events.vpsbox.home.arpa" in local_config
    assert f"root * {tmp_path / 'event-sites'}" in local_config
    assert "file_server" in local_config
    assert "respond @event_php 404" in local_config
    assert "respond @event_hidden 404" in local_config
    assert "not path /.well-known /.well-known/*" in local_config
    assert "event_sensitive_tree" in local_config
    assert "event_database_backup" in local_config
    assert "event_sensitive_file" in local_config
    assert "event_sensitive_extension" in local_config
    assert "file_server @event_public_conference_yaml" in local_config
    assert "index index.html index.htm" in local_config

    def matcher(name: str) -> re.Pattern[str]:
        line = next(
            value.strip()
            for value in local_config.splitlines()
            if f"path_regexp {name} " in value
        )
        return re.compile(line.split(f"path_regexp {name} ", 1)[1])

    hidden = matcher("event_hidden")
    for path in (
        "/.PLMCN.stage-deadbeef/index.html",
        "/.PLMCN.rollback/index.html",
        "/.PLMCN.rollback-old-deadbeef/index.html",
        "/.PLMCN.restore-deadbeef/index.html",
        "/PLMCN/.git/config",
    ):
        assert hidden.search(path), path

    sensitive_tree = matcher("event_sensitive_tree")
    sensitive_file = matcher("event_sensitive_file")
    database_backup = matcher("event_database_backup")
    sensitive_extension = matcher("event_sensitive_extension")
    php = matcher("event_php")
    assert sensitive_tree.search("/PLMCN/.env.production")
    assert sensitive_tree.search("/PLMCN/regform/registrations/private.csv")
    assert sensitive_tree.search("/PLMCN/config/settings.json")
    assert sensitive_file.search("/PLMCN/id_ed25519")
    assert sensitive_file.search("/PLMCN/credentials.json")
    assert database_backup.search("/PLMCN/data.sqlite3")
    assert database_backup.search("/PLMCN/archive.backup")
    assert sensitive_extension.search("/PLMCN/settings.yml")
    assert sensitive_extension.search("/PLMCN/server.key")
    assert php.search("/PLMCN/index.php")
    assert php.search("/PLMCN/code.phtml")
    assert php.search("/PLMCN/archive.phar")

    # The explicit YAML handler precedes the generic YAML deny, while ordinary
    # HTML/assets reach the final static file server.
    assert local_config.index("file_server @event_public_conference_yaml") < local_config.index(
        "respond @event_sensitive_extension 404"
    )
    assert local_config.index("import " + env["MIFP_EVENTS_PHP_INCLUDE"]) < local_config.index(
        "respond @event_php 404"
    )
    assert hidden.search("/.well-known/acme-challenge/token")
    assert "not path /.well-known /.well-known/*" in local_config
    assert "file_server {" in local_config

    result = _run(env, "config-set", "EVENTS_PUBLISH_BACKEND", "remote", check=False)
    assert result.returncode == 0, result.stderr
    remote_config = caddy.read_text(encoding="utf-8")
    assert "events.vpsbox.home.arpa" not in remote_config
    assert "file_server" not in remote_config


def test_backend_transition_stops_remote_php_and_restarts_it_for_local_vps(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)
    systemctl_log = Path(env["FAKE_SYSTEMCTL_LOG"])
    relay = Path(env["MIFP_MAIL_RELAY_CONFIG"])
    relay.write_text("stale local relay credential\n", encoding="utf-8")

    remote = _run(env, "config-set", "EVENTS_PUBLISH_BACKEND", "remote", check=False)
    assert remote.returncode == 0, remote.stderr
    assert "disable --now php8.3-fpm.service" in systemctl_log.read_text(encoding="utf-8")
    assert not relay.exists()

    systemctl_log.write_text("", encoding="utf-8")
    local = _run(env, "config-set", "EVENTS_PUBLISH_BACKEND", "local-vps", check=False)
    assert local.returncode == 0, local.stderr
    assert "enable --now php8.3-fpm.service" in systemctl_log.read_text(encoding="utf-8")


def test_local_vps_mail_change_regenerates_relay_and_secret_removal_fails_closed(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)
    config_file = Path(env["MIFP_CONFIG_DIR"]) / "config.env"
    secrets_file = Path(env["MIFP_CONFIG_DIR"]) / "secrets.env"
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        + "MAIL_PROVIDER=smtp\nSMTP_HOST=smtp.old.example\nSMTP_PORT=587\n"
        + "SMTP_SECURITY=starttls\nSMTP_USERNAME=mailer@example.test\n"
        + "SMTP_FROM_ADDRESS=no-reply@example.test\nSMTP_FROM_NAME=MIFP\n",
        encoding="utf-8",
    )
    secret = "relay-test-secret"
    secrets_file.write_text(
        secrets_file.read_text(encoding="utf-8") + f"SMTP_PASSWORD={secret}\n",
        encoding="utf-8",
    )
    relay = Path(env["MIFP_MAIL_RELAY_CONFIG"])

    changed = _run(env, "config-set", "SMTP_HOST", "smtp.new.example", check=False)

    assert changed.returncode == 0, changed.stderr
    rendered = relay.read_text(encoding="utf-8")
    assert 'host "smtp.new.example"' in rendered
    assert 'user "mailer@example.test"' in rendered
    assert f'password "{secret}"' in rendered
    assert stat.S_IMODE(relay.stat().st_mode) == 0o640
    assert secret not in changed.stdout + changed.stderr

    removed = _run(env, "config-unset", "SMTP_PASSWORD", check=False)

    assert removed.returncode != 0
    assert not relay.exists()
    assert secret not in removed.stdout + removed.stderr
    assert "relay host disabilitato" in removed.stderr


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


def test_compose_failure_is_reported_as_host_error_without_image_rollback(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    runtime_env = home / ".env"
    runtime_mode = runtime_env.stat().st_mode
    runtime_mtime = runtime_env.stat().st_mtime_ns

    result = _run(dict(env, FAIL_COMPOSE_UP="1"), "deploy", "sha-b", check=False)

    assert result.returncode != 0
    assert "Errore host/Compose" in result.stderr
    assert "rollback immagine automatico non tentato" in result.stderr
    assert "Release non pronta: ripristino" not in result.stdout
    assert _release(home) == before
    assert runtime_env.stat().st_mode == runtime_mode
    assert runtime_env.stat().st_mtime_ns == runtime_mtime


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


def test_security_check_rejects_unsafe_runtime_env_permissions(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _prepare_security_host(env)
    (home / ".env").chmod(0o644)

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "Runtime environment must be" in result.stdout
    assert "mode 644" in result.stdout


def test_security_check_rejects_runtime_env_symlink(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _prepare_security_host(env)
    target = tmp_path / "runtime-target.env"
    target.write_text((home / ".env").read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o600)
    (home / ".env").unlink()
    (home / ".env").symlink_to(target)

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "Runtime environment is not a safe regular file" in result.stdout


def test_security_check_rejects_wrong_sensitive_file_owner(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)
    _prepare_security_host(env)
    env["MIFP_SECURITY_ROOT_UID"] = str(os.getuid() + 1)

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "Secrets file must be uid" in result.stdout


@pytest.mark.parametrize("location,key", [("runtime", "SMTP_PASSWORD"), ("config", "RESTIC_PASSWORD")])
def test_security_check_reports_misplaced_secret_key_without_value(
    tmp_path: Path, location: str, key: str
) -> None:
    env, home = _env(tmp_path)
    _prepare_security_host(env)
    secret = "do-not-print-this-secret-value"
    path = home / ".env" if location == "runtime" else Path(env["MIFP_CONFIG_DIR"]) / "config.env"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={secret}\n")

    result = _run(env, "security-check", check=False)
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert f"ERROR: {key} unexpectedly present in {path}" in result.stdout
    assert secret not in combined


@pytest.mark.parametrize(
    "key",
    ["SECRET_KEY", "ADMIN_PASSWORD_HASH", "SMTP_PASSWORD", "EVENTS_REMOTE_PASSWORD", "RESTIC_PASSWORD"],
)
def test_security_check_rejects_every_direct_container_secret_without_printing_value(
    tmp_path: Path, key: str
) -> None:
    env, home = _env(tmp_path)
    _prepare_security_host(env)
    _set_running_release(home)
    secret = "container-secret-must-never-be-printed"
    env.update(FAKE_CONTAINER_SECRET_NAME=key, FAKE_CONTAINER_SECRET_VALUE=secret)

    result = _run(env, "security-check", check=False)
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert f"{key} is exposed to the web container" in result.stdout
    assert secret not in combined


def test_security_check_detects_sensitive_public_tree_path(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)
    _prepare_security_host(env)
    public_root = tmp_path / "event-sites"
    sensitive = public_root / "conference" / "private"
    sensitive.mkdir(parents=True)
    (sensitive / "registrations.sqlite").write_text("private-data", encoding="utf-8")

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "sensitive path in published event tree: conference/private" in result.stdout
    assert "private-data" not in result.stdout + result.stderr


def test_security_check_accepts_safe_metadata_and_container_secret_contract(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _prepare_security_host(env)
    _set_running_release(home)

    result = _run(env, "security-check")

    assert "Secrets file permissions and ownership: OK" in result.stdout
    assert "Direct container secret isolation: OK" in result.stdout
    assert "Container /run/secrets mounts: OK" in result.stdout
    assert "Published event tree sensitive paths: OK" in result.stdout


def test_security_check_rejects_sensitive_host_mount_without_printing_source(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _prepare_security_host(env)
    _set_running_release(home)
    sensitive_source = str(home / ".env")
    env["FAKE_SENSITIVE_MOUNT_SOURCE"] = sensitive_source

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "sensitive host path is mounted at /mnt/unexpected" in result.stdout
    assert sensitive_source not in result.stdout + result.stderr


def test_security_check_warns_when_default_password_ssh_is_enabled(tmp_path: Path) -> None:
    """The selected provider/default SSH policy is visible but not blocking."""
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

    result = _run(env, "security-check")

    assert result.returncode == 0
    assert "WARN: SSH PasswordAuthentication allows password login" in result.stdout
    assert "WARN: SSH PermitRootLogin allows root password login" in result.stdout
    assert "not key-only hardening" in result.stdout
    assert "mifpctl ssh-harden --operator USER" in result.stdout
    assert "Security check: OK (2 warning(s); review WARN lines)" in result.stdout


def test_security_check_still_fails_when_firewall_is_inactive(tmp_path: Path) -> None:
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
    _write_executable(bin_dir / "ufw", "#!/bin/sh\nprintf 'Status: inactive\\n'\n")

    result = _run(env, "security-check", check=False)

    assert result.returncode != 0
    assert "WARN: SSH PasswordAuthentication allows password login" in result.stdout
    assert "ERROR: UFW is not active" in result.stdout
    assert "Security check found problems" in result.stderr


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



def test_update_check_is_read_only_and_uses_anonymous_latest_lookup(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    runtime_env = home / ".env"
    mode_before = runtime_env.stat().st_mode
    mtime_before = runtime_env.stat().st_mtime_ns
    state = Path(env["FAKE_DOCKER_STATE"])
    pulls_before = (state / "pull-count").read_text(encoding="utf-8").strip()
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8").strip()

    result = _run(env, "update-check")

    assert "Update available: YES" in result.stdout
    assert "Current release:" in result.stdout
    assert "Latest available:" in result.stdout
    assert _release(home) == before
    assert runtime_env.stat().st_mode == mode_before
    assert runtime_env.stat().st_mtime_ns == mtime_before
    assert (state / "pull-count").read_text(encoding="utf-8").strip() == pulls_before
    assert (state / "compose-up-count").read_text(encoding="utf-8").strip() == starts_before
    latest = "ghcr.io/example/mifp@sha256:" + "0" * 63 + "9"
    latest_key = latest.translate(str.maketrans("/:@", "___"))
    assert not (state / latest_key).exists(), "update-check must not pull the candidate image"
    configs = (state / "imagetools-configs").read_text(encoding="utf-8").splitlines()
    assert len(configs) == 1
    assert configs[0] != "authenticated"


def test_update_check_reports_already_current_digest(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "init")
    before = _release(home)

    result = _run(env, "update-check")

    assert result.returncode == 0
    assert "Update available: NO" in result.stdout
    assert "System is already up to date." in result.stdout
    assert _release(home) == before


def test_check_runs_full_verbose_preflight_without_switching_release(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = int((state / "compose-up-count").read_text(encoding="utf-8").strip())

    result = _run(dict(env, LATEST_DIGEST_NUM="2"), "check")

    assert "MIFP update preflight" in result.stdout
    for phase_name in (
        "Configuration",
        "Current release",
        "Resolve update channel",
        "Candidate image",
        "Deploy contract",
        "Database compatibility",
        "Compose + secrets",
    ):
        assert phase_name in result.stdout
    assert "READY TO UPDATE" in result.stdout
    assert "Production state: UNCHANGED" in result.stdout
    assert "Run: sudo mifpctl update" in result.stdout
    assert _release(home) == before
    assert int((state / "compose-up-count").read_text(encoding="utf-8").strip()) == starts_before
    assert int((state / "pull-count").read_text(encoding="utf-8").strip()) >= 2


def test_check_reports_up_to_date_without_pulling_or_switching(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "init")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    pulls_before = (state / "pull-count").read_text(encoding="utf-8")
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8")

    result = _run(env, "check")

    assert "System is already up to date." in result.stdout
    assert "Production state: UNCHANGED" in result.stdout
    assert (state / "pull-count").read_text(encoding="utf-8") == pulls_before
    assert (state / "compose-up-count").read_text(encoding="utf-8") == starts_before
    assert _release(home) == before


def test_check_classifies_configuration_failure_and_preserves_production(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    config = Path(env["MIFP_CONFIG_DIR"]) / "config.env"
    config.write_text("ENVIRONMENT=production\nIMAGE_REPOSITORY=ghcr.io/example/mifp\n", encoding="utf-8")

    result = _run(env, "check", check=False)

    assert result.returncode != 0
    assert "BLOCKED" in result.stdout
    assert "Failure class: HOST/CONFIGURATION" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert _release(home) == before


def test_check_classifies_database_incompatibility_without_switch(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8")

    result = _run(dict(env, LATEST_DIGEST_NUM="2", FAIL_DB_PREFLIGHT="1"), "check", check=False)

    assert result.returncode != 0
    assert "Failure class: DATABASE" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert (state / "compose-up-count").read_text(encoding="utf-8") == starts_before
    assert _release(home) == before


def test_check_classifies_compose_secret_preflight_failure_without_switch(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8")

    result = _run(dict(env, LATEST_DIGEST_NUM="2", FAIL_COMPOSE_CONFIG="1"), "check", check=False)

    assert result.returncode != 0
    assert "Failure class: HOST/CONFIGURATION" in result.stdout
    assert "file-backed secret preparation" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert (state / "compose-up-count").read_text(encoding="utf-8") == starts_before
    assert _release(home) == before


def test_check_rejects_newer_deploy_contract_before_switch(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = int((state / "compose-up-count").read_text(encoding="utf-8").strip())

    result = _run(
        dict(env, LATEST_DIGEST_NUM="2", FAKE_DEPLOY_CONTRACT="3"),
        "check",
        check=False,
    )

    assert result.returncode != 0
    assert "Deploy contract incompatibile" in result.stderr
    assert "Failure class: HOST/CONFIGURATION" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert _release(home) == before
    assert int((state / "compose-up-count").read_text(encoding="utf-8").strip()) == starts_before


def test_update_is_always_verbose_and_reports_final_production_state(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")

    result = _run(dict(env, LATEST_DIGEST_NUM="2"), "update")

    assert "MIFP safe update" in result.stdout
    for phase_name in (
        "Configuration",
        "Current release",
        "Resolve update channel",
        "Candidate image",
        "Deploy contract",
        "Database compatibility",
        "Compose + secrets",
        "Start candidate",
        "Application readiness",
    ):
        assert phase_name in result.stdout
    assert "UPDATE COMPLETED" in result.stdout
    assert "Production state: UPDATED" in result.stdout
    assert "Rollback:         sudo mifpctl rollback" in result.stdout
    assert _release(home)["CURRENT_IMAGE"].endswith("@sha256:" + "0" * 63 + "2")


def test_update_rejects_newer_deploy_contract_before_compose_up(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = int((state / "compose-up-count").read_text(encoding="utf-8").strip())

    result = _run(
        dict(env, LATEST_DIGEST_NUM="2", FAKE_DEPLOY_CONTRACT="3"),
        "update",
        check=False,
    )

    assert result.returncode != 0
    assert "Deploy contract incompatibile" in result.stderr
    assert "Failure class: HOST/CONFIGURATION" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert _release(home) == before
    assert int((state / "compose-up-count").read_text(encoding="utf-8").strip()) == starts_before


def test_version_reports_host_tools_and_contract(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)

    result = _run(env, "version")

    assert "MIFP host tools" in result.stdout
    assert "Version:         2026.09.25.1" in result.stdout
    assert "Deploy contract: 2 (legacy default: 1)" in result.stdout


def test_update_resolves_latest_then_reuses_immutable_deploy(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    first = _release(home)

    result = _run(dict(env, LATEST_DIGEST_NUM="2"), "update")

    second = _release(home)
    expected = "ghcr.io/example/mifp@sha256:" + "0" * 63 + "2"
    assert "Update available: YES" in result.stdout
    assert f"Latest available: {expected}" in result.stdout
    assert second["CURRENT_IMAGE"] == expected
    assert second["PREVIOUS_IMAGE"] == first["CURRENT_IMAGE"]
    assert ":latest" not in (home / "release.env").read_text(encoding="utf-8")


def test_update_is_noop_when_latest_digest_is_already_active(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    _run(dict(env, LATEST_DIGEST_NUM="2"), "update")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    pulls_before = (state / "pull-count").read_text(encoding="utf-8").strip()
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8").strip()

    result = _run(dict(env, LATEST_DIGEST_NUM="2"), "update")

    assert "Update available: NO" in result.stdout
    assert "Already up to date." in result.stdout
    assert _release(home) == before
    assert (state / "pull-count").read_text(encoding="utf-8").strip() == pulls_before
    assert (state / "compose-up-count").read_text(encoding="utf-8").strip() == starts_before


def test_update_health_failure_preserves_release_and_restores_running_image(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)

    result = _run(
        dict(env, LATEST_DIGEST_NUM="2", FAIL_DIGEST_TWO_READY="1", MIFP_READY_ATTEMPTS="1"),
        "update",
        check=False,
    )

    assert result.returncode != 0
    assert "Failure class: READINESS" in result.stdout
    assert "Container: restarting" in result.stdout
    assert "Exit code: 3" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: ROLLED BACK SUCCESSFULLY")
    assert _release(home) == before
    active = (Path(env["FAKE_DOCKER_STATE"]) / "active-image").read_text(encoding="utf-8").strip()
    assert active == before["CURRENT_IMAGE"]


def test_update_readiness_and_rollback_failure_requires_manual_attention(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)

    result = _run(
        dict(env, LATEST_DIGEST_NUM="2", FAIL_READY="1", MIFP_READY_ATTEMPTS="1"),
        "update",
        check=False,
    )

    assert result.returncode != 0
    assert "Failure class: READINESS" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: MANUAL ATTENTION REQUIRED")
    assert _release(home) == before


def test_update_runtime_host_compose_failure_does_not_attempt_image_rollback(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8")

    result = _run(
        dict(env, LATEST_DIGEST_NUM="2", FAIL_HOST_COMPOSE_UP="1", MIFP_READY_ATTEMPTS="1"),
        "update",
        check=False,
    )

    assert result.returncode != 0
    assert "cannot create secret" in result.stdout
    assert "Failure class: HOST/CONFIGURATION" in result.stdout
    assert "Automatic image rollback was not attempted" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: MANUAL ATTENTION REQUIRED")
    assert (state / "compose-up-count").read_text(encoding="utf-8") == starts_before
    assert _release(home) == before


def test_update_startup_failure_rolls_back_previous_release(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)

    result = _run(
        dict(env, LATEST_DIGEST_NUM="2", FAIL_CANDIDATE_COMPOSE_UP="1", MIFP_READY_ATTEMPTS="1"),
        "update",
        check=False,
    )

    assert result.returncode != 0
    assert "Failure class: IMAGE/STARTUP" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: ROLLED BACK SUCCESSFULLY")
    assert _release(home) == before
    active = (Path(env["FAKE_DOCKER_STATE"]) / "active-image").read_text(encoding="utf-8").strip()
    assert active == before["CURRENT_IMAGE"]


def test_update_host_compose_failure_does_not_attempt_image_rollback(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)
    state = Path(env["FAKE_DOCKER_STATE"])
    starts_before = (state / "compose-up-count").read_text(encoding="utf-8")

    result = _run(dict(env, LATEST_DIGEST_NUM="2", FAIL_COMPOSE_CONFIG="1"), "update", check=False)

    assert result.returncode != 0
    assert "Failure class: HOST/CONFIGURATION" in result.stdout
    assert "rollback was not attempted" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert (state / "compose-up-count").read_text(encoding="utf-8") == starts_before
    assert _release(home) == before


def test_update_registry_failure_is_classified_and_unchanged(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)

    result = _run(dict(env, FAIL_IMAGETOOLS_OTHER="1"), "update", check=False)

    assert result.returncode != 0
    assert "Failure class: NETWORK/REGISTRY" in result.stdout
    assert result.stdout.rstrip().endswith("Production state: UNCHANGED")
    assert _release(home) == before


def test_legacy_contract_image_can_remain_previous_and_be_rolled_back(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    legacy_env = dict(env, FAKE_LEGACY_DIGEST_ONE="1")
    _run(legacy_env, "first-deploy", "sha-a")
    legacy = _release(home)["CURRENT_IMAGE"]

    _run(dict(legacy_env, LATEST_DIGEST_NUM="2"), "update")
    updated = _release(home)
    assert updated["PREVIOUS_IMAGE"] == legacy

    result = _run(legacy_env, "rollback")

    assert result.returncode == 0
    assert _release(home)["CURRENT_IMAGE"] == legacy


def test_update_check_falls_back_to_optional_registry_credentials(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")

    result = _run(dict(env, FAIL_IMAGETOOLS_ANON_AUTH="1"), "update-check")

    assert result.returncode == 0
    assert "Update available: YES" in result.stdout
    configs = (Path(env["FAKE_DOCKER_STATE"]) / "imagetools-configs").read_text(encoding="utf-8").splitlines()
    assert len(configs) == 2
    assert configs[0] != "authenticated"
    assert configs[1] == "authenticated"
    assert _release(home)["CURRENT_IMAGE"].endswith("@sha256:" + "0" * 63 + "1")


def test_update_check_registry_failure_is_read_only(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    before = _release(home)

    result = _run(dict(env, FAIL_IMAGETOOLS_OTHER="1"), "update-check", check=False)

    assert result.returncode != 0
    assert "Impossibile ispezionare il canale di aggiornamento" in result.stderr
    assert _release(home) == before


def test_status_shows_immutable_release_and_latest_channel_without_network_lookup(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")

    result = _run(dict(env, FAIL_IMAGETOOLS_OTHER="1"), "status")

    assert "Current release:" in result.stdout
    assert "Configured channel: ghcr.io/example/mifp:latest" in result.stdout


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


def test_first_deploy_respects_disabled_backup_timer(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    config_file = Path(env["MIFP_CONFIG_DIR"]) / "config.env"
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace("BACKUP_ENABLED=true", "BACKUP_ENABLED=false"),
        encoding="utf-8",
    )

    # Enabling the timer is forced to fail; a disabled configuration must still
    # complete because first-deploy should issue disable --now, not enable --now.
    result = _run(dict(env, FAIL_BACKUP_TIMER="1"), "first-deploy", "sha-a", check=False)

    assert result.returncode == 0, result.stderr
    assert "Backup timer: disabled by configuration" in result.stdout
    assert (home / "release.env").is_file()


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


def test_registry_check_uses_anonymous_access_without_credentials(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)

    result = _run(env, "registry-check", check=False)

    assert result.returncode == 0
    assert "leggibile anonimamente" in result.stdout
    configs = (Path(env["FAKE_DOCKER_STATE"]) / "manifest-configs").read_text(encoding="utf-8").splitlines()
    assert len(configs) == 1
    assert configs[0] != "authenticated"


def test_registry_check_suggests_login_only_for_real_auth_failure(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)

    denied = _run(dict(env, FAIL_MANIFEST_AUTH="1"), "registry-check", check=False)
    assert denied.returncode != 0
    assert "sudo mifpctl registry-login" in denied.stderr

    missing = _run(dict(env, FAIL_MANIFEST_MISSING="1"), "registry-check", check=False)
    assert missing.returncode != 0
    assert "sudo mifpctl registry-login" not in missing.stderr
    assert "manifest :latest assente" in missing.stderr


def test_config_check_ignores_volume_list_items_when_checking_ports(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    assert '${MIFP_DATA_DIR:-/opt/mifp/data}:/app/data' in (home / "compose.yaml").read_text(encoding="utf-8")

    result = _run(env, "config-check", check=False)

    assert result.returncode == 0, result.stderr
    assert "Compose exposure contract: OK (1 mapping loopback)." in result.stdout


def test_config_check_rejects_real_non_loopback_port(tmp_path: Path) -> None:
    env, _ = _env(tmp_path)

    result = _run(dict(env, FAKE_COMPOSE_HOST_IP="0.0.0.0"), "config-check", check=False)

    assert result.returncode != 0
    assert "Porta pubblicata non loopback" in result.stderr
    assert "0.0.0.0:8000:8000/tcp" in result.stderr


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
    config_dir = Path(env["MIFP_CONFIG_DIR"])
    persisted = (config_dir / "config.env").read_text(encoding="utf-8")
    persisted += (config_dir / "secrets.env").read_text(encoding="utf-8")
    assert "REGISTRY_USERNAME=matteo" not in persisted
    assert token not in persisted


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


def test_restore_snapshot_v2_verifies_but_does_not_restore_retired_event_trees(tmp_path: Path) -> None:
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

    assert (home / "events" / "OLD/index.html").read_text(encoding="utf-8") == "old"
    assert not (home / "events" / "PLMCN-2025").exists()
    assert (home / "events-private/registrations/old.csv").read_text(encoding="utf-8") == "old"
    assert not (home / "events-private/registrations/future.csv").exists()


def test_v4_restore_recovers_private_regform_data_and_suspends_php_until_republish(tmp_path: Path) -> None:
    import hashlib
    import json

    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    private_root = Path(env["MIFP_EVENTS_PRIVATE_DIR"])
    for name in ("registrations", "uploads", "sessions", "tmp"):
        (private_root / name).mkdir(parents=True, exist_ok=True)
    (private_root / "registrations/old.json").write_text("old", encoding="utf-8")

    snapshot = tmp_path / "candidate-snapshot-v4"
    for name in ("assets", "conferences", "config", "events-private/registrations", "events-private/uploads"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / "mifp.db").write_text("snapshot-db", encoding="utf-8")
    (snapshot / "events-private/registrations/submission.json").write_text("restored", encoding="utf-8")
    (snapshot / "events-private/uploads/proof.pdf").write_bytes(b"proof")
    (snapshot / "events-php-enabled.txt").write_text("PLMCN-2027/regform\n", encoding="utf-8")
    candidates = [snapshot / "mifp.db", snapshot / "events-php-enabled.txt"] + [
        path
        for dirname in ("assets", "conferences", "config", "events-private")
        for path in sorted((snapshot / dirname).rglob("*"))
        if path.is_file()
    ]
    files = {
        path.relative_to(snapshot).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in candidates
    }
    (snapshot / "manifest.json").write_text(
        json.dumps({"format": "mifp-host-snapshot", "version": 4, "files": files}),
        encoding="utf-8",
    )

    restored = _run(env, "restore-snapshot", str(snapshot), check=False)
    assert restored.returncode == 0, restored.stderr
    assert not (private_root / "registrations/old.json").exists()
    assert (private_root / "registrations/submission.json").read_text() == "restored"
    assert (private_root / "uploads/proof.pdf").read_bytes() == b"proof"
    assert (home / "events-php-enabled.txt").read_text() == "PLMCN-2027/regform\n"
    include = Path(env["MIFP_EVENTS_PHP_INCLUDE"])
    assert "Suspended until mifpctl events-republish-all" in include.read_text()

    regform = tmp_path / "event-sites/PLMCN-2027/regform"
    regform.mkdir(parents=True)
    (regform / "index.php").write_text("<?php echo 'ok';", encoding="utf-8")
    recovered = _run(env, "events-republish-all", check=False)
    assert recovered.returncode == 0, recovered.stderr
    assert "PLMCN\\-2027/regform" in include.read_text()


def test_v5_remote_restore_does_not_require_or_replace_local_php_state(tmp_path: Path) -> None:
    import hashlib
    import json

    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")
    _run(env, "config-set", "EVENTS_PUBLISH_BACKEND", "remote")
    private_root = Path(env["MIFP_EVENTS_PRIVATE_DIR"])
    (private_root / "registrations/local-phase-one.json").write_text("retained", encoding="utf-8")
    include = Path(env["MIFP_EVENTS_PHP_INCLUDE"])
    include.write_text("# inactive local policy\n", encoding="utf-8")

    snapshot = tmp_path / "candidate-snapshot-v5-remote"
    for name in ("assets", "conferences", "config"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / "mifp.db").write_text("snapshot-db", encoding="utf-8")
    files = {
        "mifp.db": hashlib.sha256((snapshot / "mifp.db").read_bytes()).hexdigest()
    }
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "format": "mifp-host-snapshot",
                "version": 5,
                "events_backend": "remote",
                "files": files,
            }
        ),
        encoding="utf-8",
    )

    restored = _run(env, "restore-snapshot", str(snapshot), check=False)

    assert restored.returncode == 0, restored.stderr
    assert (private_root / "registrations/local-phase-one.json").read_text() == "retained"
    assert include.read_text(encoding="utf-8") == "# inactive local policy\n"


def test_retired_host_event_import_commands_are_not_exposed(tmp_path: Path) -> None:
    env, _home = _env(tmp_path)
    for command in ("events-import", "events-rollback", "events-check"):
        result = _run(env, command, check=False)
        assert result.returncode != 0
        assert "Comando sconosciuto" in result.stderr


def test_php_allowlist_is_regform_only_validated_and_reversible(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    event_root = tmp_path / "event-sites"
    regform = event_root / "PLMCN-2027/regform"
    regform.mkdir(parents=True)
    (regform / "index.php").write_text("<?php echo 'ok';", encoding="utf-8")
    (regform / "template.phtml").write_text("never executable", encoding="utf-8")
    (event_root / "PLMCN-2027/admin.php").write_text("never executable", encoding="utf-8")
    available_socket = next((path for path in Path("/run").rglob("*") if path.is_socket()), None)
    if available_socket is None:
        pytest.skip("No Unix socket is available to exercise the PHP-FPM readiness guard")
    env["MIFP_EVENTS_PHP_SOCKET"] = str(available_socket)

    traversal = _run(env, "events-php-enable", "../PLMCN-2027/regform", check=False)
    assert traversal.returncode != 0
    assert "Path PHP non valido" in traversal.stderr

    enabled = _run(env, "events-php-enable", "PLMCN-2027/regform", check=False)
    assert enabled.returncode == 0, enabled.stderr
    include = Path(env["MIFP_EVENTS_PHP_INCLUDE"]).read_text(encoding="utf-8")
    assert "^/PLMCN\\-2027/regform/(?:.*/)?[^/]+\\.php$" in include
    assert "admin.php" not in include
    assert "phtml" not in include
    assert (home / "events-php-enabled.txt").read_text() == "PLMCN-2027/regform\n"

    disabled = _run(env, "events-php-disable", "PLMCN-2027/regform", check=False)
    assert disabled.returncode == 0, disabled.stderr
    assert (home / "events-php-enabled.txt").read_text() == ""
    assert "path_regexp" not in Path(env["MIFP_EVENTS_PHP_INCLUDE"]).read_text()


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


def test_preflight_snapshot_cleanup_removes_sqlite_sidecars():
    script = DEPLOY.read_text(encoding="utf-8")
    block = script.split("sqlite_snapshot_temp() {", 1)[1].split("\n}", 1)[0]
    assert '"$snapshot-wal"' in block
    assert '"$snapshot-shm"' in block
    assert 'rm -f -- "$snapshot-wal" "$snapshot-shm"' in block



def test_update_cleans_sqlite_preflight_sidecars(tmp_path: Path) -> None:
    env, home = _env(tmp_path)
    _run(env, "first-deploy", "sha-a")

    result = _run(dict(env, LATEST_DIGEST_NUM="2", EMIT_SQLITE_SHM="1"), "update")

    assert result.returncode == 0
    leftovers = list((home / "data" / "tmp").glob("preflight-*.db-shm"))
    assert leftovers == []


def _refresh_env(tmp_path: Path, bundle: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "installed" / "opt-mifp"
    config = tmp_path / "installed" / "etc-mifp"
    systemd = tmp_path / "installed" / "systemd"
    sbin = tmp_path / "installed" / "sbin"
    for directory in (home / "data", config, systemd, sbin):
        directory.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text("runtime-preserved\n", encoding="utf-8")
    (home / "release.env").write_text("release-preserved\n", encoding="utf-8")
    (home / "upgrade.env").write_text("upgrade-preserved\n", encoding="utf-8")
    (home / "data" / "marker").write_text("data-preserved\n", encoding="utf-8")
    backup = tmp_path / "installed" / "backups"
    backup.mkdir()
    (backup / "marker").write_text("backup-preserved\n", encoding="utf-8")
    (config / "config.env").write_text("ENVIRONMENT=production\nDOMAIN=mifp.eu\nWWW_DOMAIN=www.mifp.eu\nIMAGE_REPOSITORY=ghcr.io/example/mifp\nADMIN_USERNAME=admin\n", encoding="utf-8")
    (config / "secrets.env").write_text("SECRET_KEY=0123456789abcdef0123456789abcdef\nADMIN_PASSWORD_HASH=pbkdf2:sha256:600000$abcd$abcd\n", encoding="utf-8")

    bin_dir = tmp_path / "refresh-bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "id", "#!/bin/sh\n[ \"${1:-}\" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id \"$@\"\n")
    _write_executable(
        bin_dir / "docker",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"${REFRESH_COMMAND_LOG:?}\"\n"
        "[ \"${1:-}\" = compose ] && exit 0\nexit 90\n",
    )
    _write_executable(
        bin_dir / "systemctl",
        "#!/bin/sh\nprintf 'systemctl %s\\n' \"$*\" >> \"${REFRESH_COMMAND_LOG:?}\"\nexit 0\n",
    )
    _write_executable(
        bin_dir / "install",
        "#!/bin/bash\n"
        "mode=0755; directory=0; values=()\n"
        "while (($#)); do case \"$1\" in -m) mode=$2; shift 2;; -o|-g) shift 2;; -d) directory=1; shift;; *) values+=(\"$1\"); shift;; esac; done\n"
        "if ((directory)); then for value in \"${values[@]}\"; do mkdir -p \"$value\"; chmod \"$mode\" \"$value\"; done; else cp \"${values[-2]}\" \"${values[-1]}\"; chmod \"$mode\" \"${values[-1]}\"; fi\n",
    )
    for command in ("apt-get", "ufw", "sshd"):
        _write_executable(
            bin_dir / command,
            f"#!/bin/sh\nprintf '{command} invoked\\n' >> \"${{REFRESH_COMMAND_LOG:?}}\"\nexit 91\n",
        )
    env = os.environ.copy()
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        MIFP_HOME=str(home),
        MIFP_CONFIG_DIR=str(config),
        MIFP_SYSTEMD_DIR=str(systemd),
        MIFPCTL_TARGET=str(sbin / "mifpctl"),
        MIFP_REFRESH_LOCK_FILE=str(tmp_path / "refresh.lock"),
        MIFP_RUNTIME_UID=str(os.getuid()),
        MIFP_RUNTIME_GID=str(os.getgid()),
        REFRESH_COMMAND_LOG=str(tmp_path / "refresh-commands.log"),
    )
    return env, home, config


def test_host_tool_refresh_preserves_state_and_avoids_provisioning(tmp_path: Path) -> None:
    bundle = tmp_path / "deploy-bundle"
    shutil.copytree(ROOT / "deploy", bundle)
    env, home, config = _refresh_env(tmp_path, bundle)
    before = {
        "runtime": (home / ".env").read_bytes(),
        "release": (home / "release.env").read_bytes(),
        "upgrade": (home / "upgrade.env").read_bytes(),
        "data": (home / "data" / "marker").read_bytes(),
        "backup": (home.parent / "backups" / "marker").read_bytes(),
        "config": (config / "config.env").read_bytes(),
        "secrets": (config / "secrets.env").read_bytes(),
    }

    result = subprocess.run(
        ["bash", str(bundle / "refresh-host-tools.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert before == {
        "runtime": (home / ".env").read_bytes(),
        "release": (home / "release.env").read_bytes(),
        "upgrade": (home / "upgrade.env").read_bytes(),
        "data": (home / "data" / "marker").read_bytes(),
        "backup": (home.parent / "backups" / "marker").read_bytes(),
        "config": (config / "config.env").read_bytes(),
        "secrets": (config / "secrets.env").read_bytes(),
    }
    assert stat.S_IMODE((home / "deploy.sh").stat().st_mode) == 0o750
    assert stat.S_IMODE((home / "compose.yaml").stat().st_mode) == 0o644
    assert stat.S_IMODE((home / "configure.py").stat().st_mode) == 0o750
    assert stat.S_IMODE((home / "vps_config.py").stat().st_mode) == 0o750
    assert stat.S_IMODE((home / "backup.sh").stat().st_mode) == 0o750
    assert stat.S_IMODE((home / "Caddyfile.example").stat().st_mode) == 0o644
    assert stat.S_IMODE(Path(env["MIFPCTL_TARGET"]).stat().st_mode) == 0o755
    secret_material = config / "secrets"
    assert stat.S_IMODE(secret_material.stat().st_mode) == 0o700
    assert (secret_material / "mifp_secret_key").read_text(encoding="utf-8") == "0123456789abcdef0123456789abcdef"
    assert (secret_material / "mifp_admin_password_hash").read_text(encoding="utf-8") == "pbkdf2:sha256:600000$abcd$abcd"
    assert (secret_material / "mifp_smtp_password").read_text(encoding="utf-8") == ""
    assert (secret_material / "mifp_events_remote_password").read_text(encoding="utf-8") == ""
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o400 for path in secret_material.iterdir())
    assert all(path.stat().st_uid == os.getuid() and path.stat().st_gid == os.getgid() for path in secret_material.iterdir())
    log = Path(env["REFRESH_COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "apt-get invoked" not in log
    assert "ufw invoked" not in log
    assert "sshd invoked" not in log
    assert "restart" not in log
    assert "reload caddy" not in log
    assert "Application images remain unchanged" in result.stdout
    assert "Caddy template changed but the live Caddyfile did not" in result.stdout
    assert "Compose definition changed" in result.stdout


def test_host_tool_refresh_rejects_environment_backed_compose_secrets(tmp_path: Path) -> None:
    bundle = tmp_path / "deploy-bundle"
    shutil.copytree(ROOT / "deploy", bundle)
    env, home, _config = _refresh_env(tmp_path, bundle)
    compose = bundle / "compose.production.yaml"
    text = compose.read_text(encoding="utf-8").replace(
        'file: "${MIFP_SECRETS_DIR:-/etc/mifp/secrets}/mifp_secret_key"',
        "environment: SECRET_KEY",
        1,
    )
    compose.write_text(text, encoding="utf-8")

    result = subprocess.run(
        ["bash", str(bundle / "refresh-host-tools.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "secret environment-backed incompatibili con read_only" in result.stderr
    assert not (home / "deploy.sh").exists()


@pytest.mark.parametrize("failure", ["incomplete", "invalid-shell"])
def test_host_tool_refresh_rejects_invalid_bundle(tmp_path: Path, failure: str) -> None:
    bundle = tmp_path / "deploy-bundle"
    shutil.copytree(ROOT / "deploy", bundle)
    env, home, _config = _refresh_env(tmp_path, bundle)
    if failure == "incomplete":
        (bundle / "backup.sh").unlink()
    else:
        with (bundle / "deploy.sh").open("a", encoding="utf-8") as handle:
            handle.write("\nif then invalid shell\n")

    result = subprocess.run(
        ["bash", str(bundle / "refresh-host-tools.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not (home / "deploy.sh").exists()
    assert (home / ".env").read_text(encoding="utf-8") == "runtime-preserved\n"

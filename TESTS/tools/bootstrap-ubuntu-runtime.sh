#!/usr/bin/env bash
# Run only inside a disposable Ubuntu 24.04 container with the repository at /work.
set -Eeuo pipefail
[[ -e /.dockerenv && "$(id -u)" == 0 ]] || exit 2
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends php8.3-fpm php8.3-cli php8.3-mbstring php8.3-curl \
  caddy msmtp sqlite3 rsync curl gnupg ca-certificates python3-pytest python3-pytest-xdist \
  python3-dotenv python3-yaml >/dev/null
# Package installation has been performed above. Simulate host-only managers,
# but use real FPM/Caddy processes, actual users/permissions and real Compose.
mkdir -p /test-managers /etc/fail2ban/jail.d /etc/docker /run/php /root/.docker
install -d -o caddy -g caddy -m 0750 /run/caddy-test
printf '{}\n' >/root/.docker/config.json
cat >/test-managers/apt-get <<'EOF'
#!/bin/sh
exit 0
EOF
cat >/test-managers/dpkg <<'EOF'
#!/bin/sh
[ "${1:-}" != --configure ] || exit 0
exec /usr/bin/dpkg "$@"
EOF
cat >/test-managers/docker <<'EOF'
#!/bin/sh
case "${1:-}" in
  compose) exec /test-bin/docker-real "$@" ;;
  info) exit 0 ;;
  *) echo "unexpected Docker operation" >&2; exit 2 ;;
esac
EOF
cat >/test-managers/systemctl <<'EOF'
#!/bin/bash
set -eu
command="$1"; shift
unit="${*: -1}"
case "$unit" in
  caddy|caddy.service)
    case "$command" in
      show) cat /run/caddy-test/caddy.pid ;;
      is-active) [[ -f /run/caddy-test/caddy.pid ]] && kill -0 "$(cat /run/caddy-test/caddy.pid)" ;;
      enable|start)
        if [[ ! -f /run/caddy-test/caddy.pid ]] || ! kill -0 "$(cat /run/caddy-test/caddy.pid)" 2>/dev/null; then
          runuser -u caddy -- /usr/bin/caddy start --config /etc/caddy/Caddyfile --adapter caddyfile --pidfile /run/caddy-test/caddy.pid 8>&- 9>&- >/tmp/test-caddy.log 2>&1
        fi ;;
      reload) /usr/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/tmp/test-caddy-reload.log 2>&1 ;;
      restart)
        /usr/bin/caddy stop >/tmp/test-caddy-stop.log 2>&1
        runuser -u caddy -- /usr/bin/caddy start --config /etc/caddy/Caddyfile --adapter caddyfile --pidfile /run/caddy-test/caddy.pid 8>&- 9>&- >/tmp/test-caddy.log 2>&1 ;;
    esac ;;
  php8.3-fpm.service)
    case "$command" in
      is-active) [[ -f /run/php/php8.3-fpm.pid ]] && kill -0 "$(cat /run/php/php8.3-fpm.pid)" ;;
      start) /usr/sbin/php-fpm8.3 --daemonize 8>&- 9>&- ;;
      reload) kill -USR2 "$(cat /run/php/php8.3-fpm.pid)"; sleep 0.3 ;;
      enable) : ;;
      *) echo "unexpected PHP operation: $command" >&2; exit 2 ;;
    esac ;;
  *) : ;;
esac
EOF
cat >/test-managers/ufw <<'EOF'
#!/bin/sh
[ "${1:-}" != status ] || printf 'Status: active\n22/tcp LIMIT Anywhere (v6)\n80/tcp ALLOW Anywhere (v6)\n443/tcp ALLOW Anywhere (v6)\n'
exit 0
EOF
chmod 0755 /test-managers/*
export PATH="/test-managers:$PATH" MIFP_TEST_BOOTSTRAP_UBUNTU=1 PYTHONDONTWRITEBYTECODE=1
cd /work
if ! python3 -m pytest --confcutdir=/work/TESTS/webapp -o addopts='' -n0 -q TESTS/webapp/test_bootstrap_runtime.py; then
  tail -n 60 /tmp/test-caddy.log /tmp/test-caddy-reload.log 2>/dev/null || true
  exit 1
fi

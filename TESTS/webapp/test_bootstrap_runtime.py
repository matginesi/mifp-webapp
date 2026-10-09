"""Opt-in provisioning test inside a disposable Ubuntu 24.04 container.

Real filesystem, service users, PHP-FPM, Caddy and msmtp. Package installation,
Docker daemon and systemd/firewall control are simulated by the container runner;
Compose validation uses the real plugin. Never run on an operational host.
"""
from __future__ import annotations

import os
import hashlib
import pwd
import shlex
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get('MIFP_TEST_BOOTSTRAP_UBUNTU') != '1'
    or os.geteuid() != 0 or not Path('/.dockerenv').exists(),
    reason='requires explicit disposable root Ubuntu container',
)


def test_bootstrap_repeat_upgrade_and_real_php_mail(tmp_path: Path) -> None:
    assert 'VERSION_ID="24.04"' in Path('/etc/os-release').read_text()
    assert not Path('/etc/mifp').exists(), 'container must be fresh'
    bundle = Path('/root/mifp-deploy-test')
    subprocess.run(['rsync', '-rtp', '--chown=root:root', '--chmod=D700,F600', str(ROOT / 'deploy') + '/', str(bundle) + '/'], check=True)
    # Compare staged bytes against the trusted read-only source before executing.
    for source in (ROOT / 'deploy').rglob('*'):
        if source.is_file() and not source.is_symlink():
            staged = bundle / source.relative_to(ROOT / 'deploy')
            assert hashlib.sha256(staged.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
            assert staged.stat().st_uid == 0 and stat.S_IMODE(staged.stat().st_mode) == 0o600
    env = dict(os.environ, MIFP_HOSTS_FILE='/tmp/mifp-test-hosts')
    Path(env['MIFP_HOSTS_FILE']).write_text('127.0.0.1 localhost\n')

    def run(*args, ok=True):
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
        if ok:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    bootstrap = ('bash', str(bundle / 'bootstrap-vps.sh'), '--ssh-port', '22')
    run(*bootstrap, '--domain', 'bootstrap.home.arpa', '--events-backend', 'disabled')
    assert not Path('/srv/mifp-events').exists()
    assert not Path('/etc/php/8.3/fpm/pool.d/mifp-events.conf').exists()
    run('bash', '/opt/mifp/deploy.sh', 'config-set', 'EVENTS_PUBLISH_BACKEND', 'local-vps')
    pool = Path('/etc/php/8.3/fpm/pool.d/mifp-events.conf')
    assert Path('/run/php/mifp-events.sock').is_socket()
    assert '/etc/msmtprc-mifp-events' in pool.read_text()
    assert Path('/opt/mifp/events-php-enabled.txt').read_bytes() == b''
    php = pwd.getpwnam('mifp-events')
    private = Path('/srv/mifp-events-private')
    assert private.stat().st_uid == php.pw_uid
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert stat.S_IMODE((private / 'uploads').stat().st_mode) == 0o750
    marker = private / 'registrations/marker'
    marker.write_text('private synthetic data')
    backup = Path('/var/backups/mifp/synthetic-marker')
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text('synthetic backup')
    # Only synthetic credentials; render from canonical state, never from argv.
    config = Path('/etc/mifp/config.env')
    secrets = Path('/etc/mifp/secrets.env')
    with config.open('a') as handle:
        handle.write("MAIL_PROVIDER=enabled\nSMTP_HOST=127.0.0.1\nSMTP_PORT=2525\nSMTP_SECURITY=none\nSMTP_USERNAME=synthetic\nSMTP_FROM_ADDRESS=test@example.invalid\n")
    with secrets.open('a') as handle:
        handle.write('SMTP_PASSWORD=synthetic-test-password\n')
    run('bash', '/opt/mifp/deploy.sh', 'host-apply', 'all')
    def settings(path):
        result = {}
        for line in path.read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            key, value = line.split('=', 1)
            words = shlex.split(value)
            if words:
                result[key] = words[0]
        if result.get('MAIL_PROVIDER') == 'enabled':
            result['MAIL_PROVIDER'] = 'smtp'
        return result
    before_config, before_secrets = settings(config), settings(secrets)
    before = {p: p.read_bytes() for p in (marker, backup)}
    error_log = Path('/var/log/php-mifp-events.log')
    error_log.write_text('preserve diagnostic log\n')
    run(*bootstrap)
    assert all(p.read_bytes() == contents for p, contents in before.items())
    assert settings(config) == before_config
    assert settings(secrets) == before_secrets
    assert 'preserve diagnostic log' in error_log.read_text()
    relay = Path('/etc/msmtprc-mifp-events')
    info = relay.stat()
    assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (php.pw_uid, php.pw_gid, 0o600)
    assert Path('/etc/msmtprc').stat().st_uid == 0
    assert stat.S_IMODE(Path('/etc/msmtprc').stat().st_mode) == 0o600
    run('runuser', '-u', 'mifp-events', '--', '/usr/bin/msmtp', '--file=' + str(relay), '--pretend', '--', 'test@example.invalid')
    run('bash', '/opt/mifp/deploy.sh', 'mail-diagnose')
    # Actual PHP mail() -> msmtp -> local captured SMTP; no Internet mail.
    import socketserver
    import threading
    messages = []

    class SMTP(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 synthetic ESMTP\r\n')
            in_data = False
            content = []
            for line in self.rfile:
                if in_data:
                    if line == b'.\r\n':
                        messages.append(b''.join(content))
                        self.wfile.write(b'250 captured locally\r\n'); in_data = False
                    else:
                        content.append(line)
                elif line.upper().startswith((b'EHLO', b'HELO')):
                    self.wfile.write(b'250 synthetic\r\n')
                elif line.upper().startswith(b'DATA'):
                    self.wfile.write(b'354 data\r\n'); in_data = True
                elif line.upper().startswith(b'QUIT'):
                    self.wfile.write(b'221 bye\r\n'); break
                else:
                    self.wfile.write(b'250 ok\r\n')

    # Disable authentication for this local SMTP sink, then exercise rotation.
    config.write_text(config.read_text().replace('SMTP_USERNAME=synthetic', 'SMTP_USERNAME='))
    run('bash', '/opt/mifp/deploy.sh', 'host-apply', 'mail')
    with socketserver.TCPServer(('127.0.0.1', 2525), SMTP) as smtp:
        thread = threading.Thread(target=smtp.serve_forever, daemon=True); thread.start()
        try:
            regform = Path('/srv/mifp-events/Synthetic/regform')
            regform.mkdir(parents=True)
            (regform / 'index.php').write_text("<?php session_start(); echo mail('test@example.invalid', 'synthetic bootstrap test', 'captured locally') ? 'transport-accepted' : 'failed';")
            (regform / 'settings.yaml').write_text('private synthetic settings')
            ca = '/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt'
            curl = ('curl', '--retry', '10', '--retry-all-errors', '--retry-delay', '1', '--max-time', '5', '--silent', '--show-error', '--noproxy', '*', '--cacert', ca, '--resolve', 'events.bootstrap.home.arpa:443:127.0.0.1')
            denied = run(*curl, '--output', '/dev/null', '--write-out', '%{http_code}', 'https://events.bootstrap.home.arpa/Synthetic/regform/index.php')
            assert denied.stdout == '404' and not messages
            run('bash', '/opt/mifp/deploy.sh', 'events-php-enable', 'Synthetic/regform')
            result = run(*curl, '--fail', 'https://events.bootstrap.home.arpa/Synthetic/regform/')
            assert result.stdout == 'transport-accepted'
            assert messages and b'captured locally' in messages[0]
            denied = run(*curl, '--output', '/dev/null', '--write-out', '%{http_code}', 'https://events.bootstrap.home.arpa/Synthetic/regform/settings.yaml')
            assert denied.stdout == '404'
            run('bash', '/opt/mifp/deploy.sh', 'events-php-disable', 'Synthetic/regform')
        finally:
            smtp.shutdown(); thread.join(timeout=5)
    for backend in ('remote', 'disabled', 'local-vps'):
        run('bash', '/opt/mifp/deploy.sh', 'config-set', 'EVENTS_PUBLISH_BACKEND', backend)
        assert marker.read_text() == 'private synthetic data'
        assert backup.read_text() == 'synthetic backup'
    run('/usr/sbin/php-fpm8.3', '-t')
    run('/usr/bin/caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile')
    running = run('curl', '--fail', '--silent', 'http://127.0.0.1:2019/config/').stdout
    audit = subprocess.run(['python3', '/opt/mifp/vps_config.py',
        '--config-file', str(config), '--secrets-file', str(secrets),
        '--runtime-env', '/opt/mifp/.env', 'audit-caddy'],
        input=running, capture_output=True, text=True)
    assert audit.returncode == 0, audit.stdout + audit.stderr
    # Reject a tampered bundle before any installation/mutation.
    before = Path('/opt/mifp/deploy.sh').read_bytes()
    (bundle / 'bootstrap-vps.sh').chmod(0o660)
    rejected = run('bash', str(bundle / 'refresh-host-tools.sh'), ok=False)
    assert rejected.returncode != 0 and 'modificabile da gruppo/altri' in rejected.stderr
    assert Path('/opt/mifp/deploy.sh').read_bytes() == before
    (bundle / 'bootstrap-vps.sh').chmod(0o600)
    helper = bundle / 'backup.sh'
    os.chown(helper, php.pw_uid, php.pw_gid)
    rejected = run('bash', str(bundle / 'refresh-host-tools.sh'), ok=False)
    assert rejected.returncode != 0 and 'non root-owned' in rejected.stderr
    os.chown(helper, 0, 0)
    bundle.chmod(0o770)
    rejected = run('bash', str(bundle / 'refresh-host-tools.sh'), ok=False)
    assert rejected.returncode != 0 and 'Directory bundle modificabile' in rejected.stderr
    bundle.chmod(0o700)
    preserved = {path: path.read_bytes() for path in (config, secrets,
        Path('/etc/caddy/Caddyfile'), relay, Path('/etc/msmtprc'))}
    run('bash', str(bundle / 'refresh-host-tools.sh'))
    assert all(path.read_bytes() == content for path, content in preserved.items())
    assert marker.read_text() == 'private synthetic data'

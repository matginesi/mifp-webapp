"""Exercise generated host routing over HTTPS against disposable Caddy/PHP-FPM.

Set MIFP_TEST_CADDY / MIFP_TEST_PHP_FPM to test binaries outside PATH.
No system services, real mail transport, or existing publication trees are used.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from TESTS.webapp.test_deploy_state_machine import _env, _run


@pytest.fixture
def events_runtime():
    caddy = os.environ.get("MIFP_TEST_CADDY") or shutil.which("caddy")
    fpm = os.environ.get("MIFP_TEST_PHP_FPM") or shutil.which("php-fpm8.3") or shutil.which("php-fpm")
    if not caddy or not fpm:
        pytest.skip("Caddy and PHP-FPM are required for the real HTTP routing tests")
    # Keep the Unix socket below the platform pathname length limit.
    with tempfile.TemporaryDirectory(prefix="mifp-events-http-") as directory:
        root = Path(directory)
        env, home = _env(root)
        public = root / "event-sites"
        regform = public / "PLMCN-2027/regform"
        regform.mkdir(parents=True)
        private = root / "private"
        for name in ("sessions", "tmp", "registrations", "uploads"):
            (private / name).mkdir(parents=True)
        probe = """<?php
session_start();
header('Content-Type: application/json');
echo json_encode(['script' => $_SERVER['SCRIPT_NAME'], 'query' => $_GET,
    'post' => $_POST, 'upload_error' => $_FILES['proof']['error'] ?? null,
    'upload_max' => ini_get('upload_max_filesize'), 'post_max' => ini_get('post_max_size')]);
"""
        (regform / "index.php").write_text(probe)
        for relative in ("src/index.php", "registrations/index.php", "settings.yaml", ".env", "settings.json"):
            path = regform / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text("PRIVATE CONTENT")
        (regform / "assets").mkdir()
        (regform / "assets/form.js").write_text("static-form-asset")
        (public / "PLMCN-2027/index.html").write_text("static-conference")
        (public / "PLMCN-2027/conference.yaml").write_text("site: public-conference-yaml")
        (public / "PLMCN-2027/outside.php").write_text("<?php echo 'MUST NOT EXECUTE';")
        (public / "Other/regform").mkdir(parents=True)
        (public / "Other/index.html").write_text("other-static-conference")
        (public / "Other/regform/index.php").write_text(probe)
        sock = root / "php.sock"
        env["MIFP_EVENTS_PHP_SOCKET"] = str(sock)
        pool = root / "php-fpm.conf"
        # Use the real versioned pool's PHP restrictions/limits. Only paths and
        # mail execution are replaced for this disposable test environment.
        bootstrap = (Path(__file__).resolve().parents[2] / "deploy/deploy.sh").read_text()
        settings = []
        for line in bootstrap.splitlines():
            if line.startswith(("php_admin_value[", "php_admin_flag[", "env[MIFP_")):
                if "sendmail_path" in line:
                    line = "php_admin_value[sendmail_path] = /bin/false"
                elif "disable_functions" in line:
                    line += ",mail"
                line = line.replace("$root", str(public)).replace("$EVENTS_PRIVATE_DIR", str(private))
                line = line.replace("$PHP_ERROR_LOG", str(root / "php.log"))
                settings.append(line)
        pool.write_text("[global]\ndaemonize = no\nerror_log = " + str(root / "fpm.log") +
                        "\n[events-test]\nlisten = " + str(sock) +
                        "\npm = static\npm.max_children = 1\n" + "\n".join(settings) + "\n")
        with (root / "fpm-output.log").open("w") as log:
            process = subprocess.Popen([fpm, "--nodaemonize", "--fpm-config", str(pool)], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if sock.exists():
                        break
                    assert process.poll() is None, (root / "fpm-output.log").read_text()
                    time.sleep(0.02)
                assert sock.exists(), (root / "fpm-output.log").read_text()
                result = _run(env, "config-set", "EVENTS_PUBLISH_BACKEND", "local-vps", check=False)
                assert result.returncode == 0, result.stderr
                yield {"root": root, "env": env, "home": home, "public": public, "private": private, "caddy": caddy}
            finally:
                process.terminate()
                process.wait(timeout=10)


@contextlib.contextmanager
def _serve(runtime):
    root, env = runtime["root"], runtime["env"]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    generated = Path(env["MIFP_CADDY_CONFIG"]).read_text()
    event_block = generated[generated.index("events.vpsbox.home.arpa {"):]
    event_block = event_block.replace("events.vpsbox.home.arpa {", f"https://127.0.0.1:{port} {{", 1)
    config = root / "http-test.Caddyfile"
    config.write_text("{\n admin off\n auto_https disable_redirects\n skip_install_trust\n}\n" + event_block)
    run_env = os.environ.copy()
    run_env.update(XDG_DATA_HOME=str(root / "caddy-data"), XDG_CONFIG_HOME=str(root / "caddy-config"))
    with (root / "caddy.log").open("w") as log:
        process = subprocess.Popen([runtime["caddy"], "run", "--config", str(config), "--adapter", "caddyfile"],
                                   env=run_env, stdout=log, stderr=log)
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
                                             urllib.request.ProxyHandler({}))

        def request(path, data=None, headers=None):
            req = urllib.request.Request(f"https://127.0.0.1:{port}" + path, data=data, headers=headers or {})
            try:
                response = opener.open(req, timeout=5)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, response.read().decode(), response.headers

        try:
            for _ in range(100):
                assert process.poll() is None, (root / "caddy.log").read_text()
                try:
                    request("/PLMCN-2027/")
                    break
                except urllib.error.URLError:
                    time.sleep(0.03)
            else:
                pytest.fail("Caddy did not start: " + (root / "caddy.log").read_text())
            yield request
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_approved_entry_urls_php_denials_and_static_sites(events_runtime):
    runtime = events_runtime
    with _serve(runtime) as request:
        for path in ("/PLMCN-2027/regform/", "/PLMCN-2027/regform/index.php"):
            status, body, _ = request(path)
            assert status == 404, (path, status, body)
            assert "<?php" not in body
    enabled = _run(runtime["env"], "events-php-enable", "PLMCN-2027/regform", check=False)
    assert enabled.returncode == 0, enabled.stderr
    with _serve(runtime) as request:
        for path in ("/PLMCN-2027/regform", "/PLMCN-2027/regform/", "/PLMCN-2027/regform/index.php"):
            status, body, headers = request(path + "?source=directory")
            assert status == 200, (path, status, body)
            payload = json.loads(body)
            assert payload["script"] == "/PLMCN-2027/regform/index.php"
            assert payload["query"] == {"source": "directory"}
            cookie = headers["Set-Cookie"].lower()
            assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        status, body, _ = request("/PLMCN-2027/regform/", b"probe=ok", {"Content-Type": "application/x-www-form-urlencoded"})
        assert status == 200 and json.loads(body)["post"] == {"probe": "ok"}
        for path in (
            "/PLMCN-2027/regform/src/", "/PLMCN-2027/regform/src/index.php",
            "/PLMCN-2027/regform/registrations/", "/PLMCN-2027/regform/registrations/index.php",
            "/PLMCN-2027/regform/settings.yaml", "/PLMCN-2027/regform/settings.json",
            "/PLMCN-2027/regform/.env", "/PLMCN-2027/outside.php", "/Other/regform/index.php",
            "/Other/regform/", "/PLMCN-2027/regform/missing.php", "/PLMCN-2027/regform/index.php/extra",
            "/PLMCN-2027/regform/%73rc/index.php", "/PLMCN-2027/regform/%2eenv",
            "/PLMCN-2027/regform/../outside.php", "/PLMCN-2027/regform/template.phtml",
        ):
            status, body, _ = request(path)
            assert status == 404, (path, status, body)
            assert "PRIVATE CONTENT" not in body and "<?php" not in body and "MUST NOT EXECUTE" not in body
        for path, expected in (("/PLMCN-2027/", "static-conference"), ("/Other/", "other-static-conference"),
                               ("/PLMCN-2027/conference.yaml", "site: public-conference-yaml"),
                               ("/PLMCN-2027/regform/assets/form.js", "static-form-asset")):
            assert request(path)[:2] == (200, expected)
    disabled = _run(runtime["env"], "events-php-disable", "PLMCN-2027/regform", check=False)
    assert disabled.returncode == 0, disabled.stderr
    with _serve(runtime) as request:
        assert request("/PLMCN-2027/regform/")[0] == 404
        assert request("/PLMCN-2027/regform/index.php")[0] == 404
        assert request("/Other/")[:2] == (200, "other-static-conference")


def test_fpm_enforces_versioned_upload_and_post_limits(events_runtime):
    runtime = events_runtime
    assert _run(runtime["env"], "events-php-enable", "PLMCN-2027/regform", check=False).returncode == 0
    with _serve(runtime) as request:
        boundary = b"mifp-test-boundary"
        def upload(size):
            return (b"--" + boundary + b'\r\nContent-Disposition: form-data; name="proof"; filename="proof.pdf"\r\n'
                    b"Content-Type: application/pdf\r\n\r\n" + b"x" * size + b"\r\n--" + boundary + b"--\r\n")
        headers = {"Content-Type": "multipart/form-data; boundary=" + boundary.decode()}
        for size, expected_error in ((10 * 1024 * 1024, 0), (10 * 1024 * 1024 + 1, 1)):
            status, body, _ = request("/PLMCN-2027/regform/", upload(size), headers)
            assert status == 200, body
            payload = json.loads(body)
            assert payload["upload_max"] == "10M" and payload["post_max"] == "12M"
            assert payload["upload_error"] == expected_error
        status, body, _ = request("/PLMCN-2027/regform/", upload(13 * 1024 * 1024), headers)
        assert status == 200 and json.loads(body)["upload_error"] is None
        assert not list((runtime["private"] / "registrations").rglob("*"))
        assert not list((runtime["private"] / "uploads").rglob("*"))


def test_static_archive_urls_resources_collision_and_no_php(events_runtime, tmp_path):
    """Disposable archive fixture only; never imports a real historical backup."""
    from mifp_app.services.event_site_publisher import LocalEventSitePublisher, PublicationError
    import pytest
    runtime = events_runtime
    # Mixed case and spelling survive publication and direct Caddy routing.
    slug = 'STATIC-Archive_Fixture'
    source = tmp_path/'sanitized'
    (source/'assets').mkdir(parents=True)
    (source/'papers').mkdir()
    (source/'regform').mkdir()
    (source/'index.htm').write_text('<a href="papers/program.html">Program</a><img src="assets/logo.svg">')
    (source/'papers/program.html').write_text('<a href="../index.htm">Home</a>')
    (source/'assets/logo.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    (source/'assets/site.css').write_text('body{color:black}')
    (source/'assets/site.js').write_text('document.documentElement.dataset.archive="true";')
    (source/'regform/index.html').write_text('<p>Registration closed. Archive for consultation.</p>')
    publisher = LocalEventSitePublisher(runtime['public'])
    publisher.publish(source, slug, replace=False, keep_rollback=True)
    with pytest.raises(PublicationError, match='Destination already exists'):
        publisher.publish(source, slug, replace=False, keep_rollback=True)
    # Simulate unsanitized files already on disk to verify host denial, not only
    # ZIP validation. The static archive procedure itself excludes these files.
    target = runtime['public']/slug
    for relative, value in [('regform/index.php', "<?php echo 'MUST-NOT-EXECUTE';"),
                            ('regform/registrations/registrations.csv', 'PRIVATE'),
                            ('private/uploads/receipt.pdf', 'PRIVATE'), ('private/sessions/state', 'PRIVATE'),
                            ('.env', 'PRIVATE')]:
        entry = target/relative
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(value)
    with _serve(runtime) as request:
        for path, expected in [('', 'papers/program.html'), ('papers/program.html', '../index.htm'),
                               ('assets/logo.svg', '<svg'), ('assets/site.css', 'color:black'),
                               ('assets/site.js', 'dataset.archive'), ('regform/', 'Registration closed')]:
            status, body, _ = request(f'/{slug}/{path}')
            assert status == 200 and expected in body
        for path in ['regform/index.php', 'regform/index.php/anything',
                     'regform/registrations/registrations.csv', 'private/uploads/receipt.pdf',
                     'private/sessions/state', '.env']:
            status, body, _ = request(f'/{slug}/{path}')
            assert status == 404
            assert 'MUST-NOT-EXECUTE' not in body and 'PRIVATE' not in body and '<?php' not in body
        status, body, _ = request(f'/{slug}/regform/index.html', b'email=synthetic%40example.invalid')
        # Caddy serves the same static bytes for POST; no submission handler runs.
        assert status == 200 and body == (source/'regform/index.html').read_text()
        assert not list(runtime['private'].rglob('registrations.csv'))
        assert request('/PLMCN-2027/')[1] == 'static-conference'
    assert (target/'index.htm').read_bytes() == (source/'index.htm').read_bytes()
    assert not list(runtime['public'].glob(f'.{slug}.stage-*'))

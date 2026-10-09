"""Optional checks of the real WEBSITE source, copied to disposable storage."""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from TESTS.webapp.test_deploy_state_machine import _run
from TESTS.webapp.test_events_caddy_runtime import _serve, events_runtime  # noqa: F401


@pytest.fixture
def plmcn_source():
    default = Path(__file__).resolve().parents[3] / "mifp-conference-editor/PLMCN-2027"
    source = Path(os.environ.get("MIFP_TEST_PLMCN_SOURCE", str(default)))
    if not (source / "regform/index.php").is_file():
        pytest.skip("Set MIFP_TEST_PLMCN_SOURCE to the PLMCN-2027 WEBSITE source")
    return source


@pytest.mark.parametrize("upload, post, configured, expected", [
    ("10M", "12M", 16, 10), ("10M", "12M", 5, 5),
    ("20M", "8M", 16, 7), ("2M", "12M", 16, 2), ("10M", "0", 16, 10),
])
def test_real_module_clamps_limit_to_runtime(plmcn_source, upload, post, configured, expected):
    php = shutil.which("php")
    if not php:
        pytest.skip("PHP CLI required")
    result = subprocess.run([
        php, "-d", f"upload_max_filesize={upload}", "-d", f"post_max_size={post}",
        "-r", 'require $argv[1]; echo effective_upload_limit_mb((int)$argv[2]);',
        str(plmcn_source / "regform/src/bootstrap.php"), str(configured),
    ], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(expected)


def test_real_module_php_syntax(plmcn_source):
    php = shutil.which("php")
    if not php:
        pytest.skip("PHP CLI required")
    for source in sorted((plmcn_source / "regform").rglob("*.php")):
        result = subprocess.run([php, "-l", str(source)], text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr


def test_real_form_closed_sessions_and_oversized_request(events_runtime, plmcn_source):
    runtime = events_runtime
    public_site = runtime["public"] / "PLMCN-2027"
    shutil.copytree(plmcn_source / "regform", public_site / "regform", dirs_exist_ok=True)
    shutil.copyfile(plmcn_source / "conference.yaml", public_site / "conference.yaml")
    settings = public_site / "regform/settings.yaml"
    # Admission settings belong to the authoritative event package. Close only
    # this disposable copy and request 16 MiB to exercise the runtime clamp.
    synthetic_settings = re.sub(r"(submit_enabled:\s*)true", r"\1false", settings.read_text())
    synthetic_settings = re.sub(r"(max_upload_mb:\s*)\d+", r"\g<1>16", synthetic_settings)
    settings.write_text(synthetic_settings)
    assert _run(runtime["env"], "events-php-enable", "PLMCN-2027/regform", check=False).returncode == 0
    with _serve(runtime) as request:
        status, body, headers = request("/PLMCN-2027/regform/")
        assert status == 200, body
        assert "Online registration is currently closed." in body
        assert "max 10 MB" in body
        assert re.search(r'"max_upload_mb"\s*:\s*10', html.unescape(body))
        assert re.search(r'"registration_open"\s*:\s*false', html.unescape(body))
        cookie = headers["Set-Cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie
        assert "path=/plmcn-2027/regform/" in cookie
        status, body, _ = request("/PLMCN-2027/regform/", b"first_name=TEST", {"Content-Type": "application/x-www-form-urlencoded"})
        assert status == 200 and "Online registration is currently closed." in body
        # Open only this temporary copy to exercise the oversized-POST branch.
        settings.write_text(settings.read_text().replace("submit_enabled: false", "submit_enabled: true"))
        status, body, _ = request("/PLMCN-2027/regform/", b"x" * (13 * 1024 * 1024),
                                  {"Content-Type": "application/x-www-form-urlencoded"})
        assert status == 200 and "The request exceeds the server upload limit." in body
        assert not list((runtime["private"] / "registrations").rglob("*.*"))
        assert not list((runtime["private"] / "uploads").rglob("*.*"))
        assert list((runtime["private"] / "sessions").iterdir())
        assert not (public_site / "regform/registrations/registrations.csv").exists()


@pytest.mark.parametrize("attachment", [False, True])
def test_real_mailer_uses_local_capture_only(plmcn_source, tmp_path, attachment):
    php = shutil.which("php")
    if not php:
        pytest.skip("PHP CLI required")
    capture = tmp_path / "message.eml"
    sink = tmp_path / "sendmail-sink"
    sink.write_text('#!/bin/sh\ncat > "$MIFP_TEST_MAIL_CAPTURE"\n')
    sink.chmod(0o700)
    proof = tmp_path / "proof.pdf"
    proof.write_bytes(b"%PDF-1.4\nsynthetic test attachment\n")
    code = r'''
require $argv[1];
$mail = ['from_email' => 'sender@example.invalid', 'from_name' => 'Runtime Test'];
if ($argv[2] === '1') {
    $ok = send_admin_mail_with_attachment(['recipient@example.invalid'], 'Test', '<p>Test</p>', 'Test',
        $mail, 'reply@example.invalid', ['tmp_path' => $argv[3], 'extension' => 'pdf', 'mime' => 'application/pdf'], 'TEST-ONLY');
} else {
    $ok = send_simple_html_mail(['recipient@example.invalid'], 'Test', '<p>Test</p>', 'Test', $mail, 'reply@example.invalid');
}
exit($ok ? 0 : 1);
'''
    env = os.environ.copy()
    env["MIFP_TEST_MAIL_CAPTURE"] = str(capture)
    result = subprocess.run([php, "-d", "sendmail_path=" + str(sink), "-r", code,
                             str(plmcn_source / "regform/src/mailer.php"), "1" if attachment else "0", str(proof)],
                            env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    message = capture.read_text()
    assert "recipient@example.invalid" in message
    assert "sender@example.invalid" in message
    assert "Reply-To: <reply@example.invalid>" in message
    assert "text/plain" in message and "text/html" in message
    if attachment:
        assert 'filename="proof-TEST-ONLY.pdf"' in message
        assert "Content-Transfer-Encoding: base64" in message


def test_real_upload_validation_and_private_storage(events_runtime, plmcn_source):
    runtime = events_runtime
    regform = runtime["public"] / "PLMCN-2027/regform"
    shutil.copyfile(plmcn_source / "regform/src/bootstrap.php", regform / "src/bootstrap.php")
    # This probe is created only inside the disposable public tree. It calls
    # the package's real validation/storage functions without registering anyone.
    (regform / "upload-probe.php").write_text('''<?php
require __DIR__ . '/src/bootstrap.php';
header('Content-Type: application/json');
try {
    $proof = validate_upload($_FILES, effective_upload_limit_mb(16) * 1024 * 1024);
    $storage = resolve_upload_storage_path(__DIR__, resolve_storage_path(__DIR__, 'registrations'));
    $saved = store_proof_of_payment($storage, $proof, 'TEST-ONLY');
    echo json_encode(['mime' => $saved['mime']]);
} catch (DomainException $e) {
    http_response_code(422);
    echo json_encode(['error' => $e->getMessage()]);
}
''')
    assert _run(runtime["env"], "events-php-enable", "PLMCN-2027/regform", check=False).returncode == 0
    with _serve(runtime) as request:
        headers = {"Content-Type": "multipart/form-data; boundary=mifp-proof"}
        def multipart(content):
            return (b'--mifp-proof\r\nContent-Disposition: form-data; name="proof_of_payment"; filename="proof.pdf"\r\n'
                    b"Content-Type: application/pdf\r\n\r\n" + content + b"\r\n--mifp-proof--\r\n")
        status, body, _ = request("/PLMCN-2027/regform/upload-probe.php", multipart(b"not a PDF"), headers)
        assert status == 422 and "Only PDF, JPEG and PNG" in body
        status, body, _ = request("/PLMCN-2027/regform/upload-probe.php", multipart(b"%PDF-1.4\nsynthetic\n"), headers)
        assert status == 200 and json.loads(body)["mime"] == "application/pdf"
    saved = list((runtime["private"] / "uploads").rglob("proof-TEST-ONLY.pdf"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"%PDF-1.4\nsynthetic\n"
    assert not list(regform.rglob("proof-TEST-ONLY.pdf"))
    assert not list((runtime["private"] / "registrations").rglob("*"))

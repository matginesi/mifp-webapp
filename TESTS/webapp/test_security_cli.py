from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
from email.message import Message
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "security_audit.py"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _module():
    spec = importlib.util.spec_from_file_location("mifp_security_audit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_security_audit_json_and_strict_exit_are_stable():
    result = _run("audit", "--json", "--strict")
    payload = json.loads(result.stdout)

    assert result.returncode == 0, result.stderr
    assert payload["title"] == "MIFP security audit"
    assert payload["findings"]
    assert {item["status"] for item in payload["findings"]} <= {
        "OK", "WARNING", "CRITICAL", "UNKNOWN", "NOT APPLICABLE"
    }


def test_security_output_never_contains_environment_secret():
    env = os.environ.copy()
    env["SMTP_PASSWORD"] = "cli-secret-that-must-never-render"
    result = _run("report", "--verbose", env=env)

    assert result.returncode == 0
    assert "cli-secret-that-must-never-render" not in result.stdout
    assert "cli-secret-that-must-never-render" not in result.stderr


def test_fallback_secret_scan_reports_location_without_value(tmp_path: Path):
    module = _module()
    source = tmp_path / "settings.py"
    source.write_text(
        'API_KEY = "real-looking-sensitive-value-987654321"\n',
        encoding="utf-8",
    )
    module.ROOT = tmp_path

    matches = module._scan_likely_secrets(["settings.py"])
    assert matches == [("settings.py", 1, "sensitive assignment")]
    assert "real-looking-sensitive-value" not in json.dumps(matches)


def test_missing_trivy_is_an_informative_non_failure(tmp_path: Path):
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    result = _run("image", "example.invalid/mifp:test", env=env)

    assert result.returncode == 0
    assert "Trivy not available" in result.stdout


def test_launcher_exposes_security_subcommand():
    result = subprocess.run(
        ["bash", "mifp", "security", "status", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["findings"]


def test_production_audit_reports_network_failure_as_unknown():
    module = _module()

    def unavailable(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    findings, unreachable = module.production_findings(
        "https://www.mifp.eu", opener=unavailable
    )
    assert unreachable is True
    assert findings[0].status == "UNKNOWN"
    assert "CRITICAL" not in {item.status for item in findings}


def test_production_audit_checks_headers_redirect_and_endpoint_boundary():
    module = _module()
    responses = []

    def response(status: int, headers: dict[str, str]):
        message = Message()
        for key, value in headers.items():
            message[key] = value

        class FakeResponse:
            def __init__(self):
                self.status = status
                self.headers = message

            def getcode(self):
                return self.status

        return FakeResponse()

    responses.extend([
        response(200, {
            "Strict-Transport-Security": "max-age=31536000",
            "Content-Security-Policy": "default-src 'self'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=()",
            "Set-Cookie": "mifp_admin_session=value; Secure; HttpOnly; SameSite=Lax",
        }),
        response(308, {"Location": "https://www.mifp.eu/"}),
        response(404, {}),
        response(200, {}),
    ])

    def opener(*_args, **_kwargs):
        return responses.pop(0)

    findings, unreachable = module.production_findings(
        "https://www.mifp.eu", opener=opener
    )
    by_key = {item.key: item.status for item in findings}
    assert unreachable is False
    assert by_key["https"] == "OK"
    assert by_key["http_redirect"] == "OK"
    assert by_key["endpoint_ready"] == "OK"
    assert by_key["endpoint_health"] == "OK"

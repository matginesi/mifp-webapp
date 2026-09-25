#!/usr/bin/env python3
"""Local-only defensive checks for the MIFP source tree and deployed surface."""
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
VALID_STATUSES = {"OK", "WARNING", "CRITICAL", "UNKNOWN", "NOT APPLICABLE"}
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"smtp[_-]?password|secret[_-]?key|private[_-]?key)\b\s*[:=]\s*[\"']([^\"']{12,})[\"']"
)
_TOKEN_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_PLACEHOLDER_PARTS = {
    "change_me", "changeme", "example", "placeholder", "replace_me",
    "test-only", "test_secret", "mifp_test_suite_secret", "your_",
    "dev-only", "not-for-prod",
}


@dataclass(frozen=True)
class Finding:
    key: str
    group: str
    status: str
    summary: str
    remediation: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid security status: {self.status}")


def _read(relative: str) -> str:
    try:
        return (ROOT / relative).read_text(encoding="utf-8")
    except OSError:
        return ""


def repository_findings() -> list[Finding]:
    config = _read("MIFPAPP/CORE/mifp_app/config.py")
    init = _read("MIFPAPP/CORE/mifp_app/__init__.py")
    assets = _read("MIFPAPP/CORE/mifp_app/services/assets.py")
    compose = _read("deploy/compose.production.yaml")
    dockerfile = _read("MIFPAPP/CORE/Dockerfile")
    caddy = _read("deploy/Caddyfile")
    deploy = _read("deploy/mifpctl") + _read("deploy/deploy.sh")

    findings = [
        Finding("debug_fail_closed", "config", "OK" if "DEBUG=True is not allowed in production" in config else "CRITICAL", "Production configuration rejects debug mode."),
        Finding("secret_fail_closed", "config", "OK" if "SECRET_KEY environment variable is required in production" in config and "_secret_setting('SECRET_KEY')" in config else "CRITICAL", "Production requires an explicit signing key and supports runtime secret files."),
        Finding("csrf", "config", "OK" if "validate_csrf" in init and "csrf.failed" in init else "CRITICAL", "Central CSRF and same-origin validation is present."),
        Finding("secure_cookie", "config", "OK" if "SESSION_COOKIE_HTTPONLY = True" in config and "SESSION_COOKIE_SECURE" in config and "SESSION_COOKIE_SAMESITE" in config else "CRITICAL", "Session cookie security attributes are configured centrally."),
        Finding("trusted_hosts", "config", "OK" if "TRUSTED_HOSTS" in config and "host.rejected" in init else "CRITICAL", "Production Host headers are allowlisted."),
        Finding("rate_limits", "config", "OK" if "LOGIN_IP_MAX_ATTEMPTS" in config and "ADMIN_WRITE_RATE_LIMIT" in config else "WARNING", "Login and dashboard write limits are configured."),
        Finding("headers", "headers", "OK" if all(value in init for value in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy", "Strict-Transport-Security")) else "CRITICAL", "Application security headers are applied in one response hook."),
        Finding("csp_eval", "headers", "OK" if "unsafe-eval" not in init and "unsafe-eval" not in _read("MIFPAPP/CORE/config/webapp.json") else "CRITICAL", "The configured CSP does not allow unsafe-eval."),
        Finding("upload_content", "uploads", "OK" if "validate_asset_file" in assets and "asset_file_is_valid" in assets else "CRITICAL", "Stored assets are validated from file content."),
        Finding("upload_paths", "uploads", "OK" if "relative_to(root)" in assets and "Refusing to overwrite" in assets else "CRITICAL", "Asset paths are confined and publication does not overwrite conflicts."),
        Finding("runtime_non_root", "deployment", "OK" if any(line.startswith("USER ") and line.split()[1].split(":", 1)[0] not in {"0", "root"} for line in dockerfile.splitlines()) else "CRITICAL", "The production image runs as a non-root user."),
        Finding("container_read_only", "deployment", "OK" if "read_only: true" in compose and "cap_drop:" in compose and "no-new-privileges:true" in compose else "CRITICAL", "Production container filesystem and Linux privileges are restricted."),
        Finding("loopback_port", "deployment", "OK" if "127.0.0.1:8000" in compose else "CRITICAL", "The application port is published only on loopback."),
        Finding("docker_secrets", "deployment", "OK" if "SECRET_KEY_FILE" in compose and "/run/secrets/" in compose else "WARNING", "Production credentials are mounted as runtime secrets."),
        Finding("ready_private", "deployment", "OK" if "/ready" in caddy and ("respond @ready 404" in caddy or "abort" in caddy) else "WARNING", "The readiness endpoint is hidden by the public reverse proxy."),
        Finding("host_audit", "deployment", "OK" if "security-check" in deploy else "WARNING", "Host-level checks remain an explicit VPS-side command."),
    ]
    findings.extend(secret_findings())
    findings.extend(filesystem_findings())
    return findings


def _tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [item.decode("utf-8", "replace") for item in result.stdout.split(b"\0") if item]


def secret_findings() -> list[Finding]:
    tracked = _tracked_files()
    if not tracked:
        return [Finding("tracked_secrets", "secrets", "UNKNOWN", "Tracked files could not be enumerated with Git.")]
    unsafe_names = []
    private_keys = []
    for relative in tracked:
        path = Path(relative)
        name = path.name.lower()
        if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.production.example"}):
            unsafe_names.append(relative)
        if path.suffix.lower() in {".pem", ".p12", ".pfx"} or name in {"id_rsa", "id_ed25519"}:
            private_keys.append(relative)
    findings = [
        Finding("tracked_env", "secrets", "CRITICAL" if unsafe_names else "OK", "No runtime .env file is tracked." if not unsafe_names else f"Runtime environment file tracked at {unsafe_names[0]}.", "Remove the file from Git and rotate affected credentials." if unsafe_names else ""),
        Finding("tracked_private_keys", "secrets", "CRITICAL" if private_keys else "OK", "No private-key artifact is tracked." if not private_keys else f"Private-key artifact tracked at {private_keys[0]}.", "Remove the key from history and rotate it." if private_keys else ""),
        Finding("gitignore_env", "secrets", "OK" if ".env" in _read(".gitignore") else "WARNING", "Runtime environment files are excluded by repository policy."),
    ]
    matches = _scan_likely_secrets(tracked)
    findings.append(Finding(
        "credential_patterns",
        "secrets",
        "CRITICAL" if matches else "OK",
        "No credential-shaped value was found in tracked application/configuration files." if not matches else f"A credential-shaped value was found at {matches[0][0]}:{matches[0][1]} (value suppressed).",
        "Remove the value from Git and rotate the credential; verify full history with Gitleaks." if matches else "",
    ))
    return findings


def _scan_likely_secrets(tracked: Iterable[str]) -> list[tuple[str, int, str]]:
    """Return path/line/rule only; matched values never leave this function."""
    matches: list[tuple[str, int, str]] = []
    allowed_suffixes = {".env", ".ini", ".js", ".json", ".py", ".sh", ".toml", ".ts", ".yaml", ".yml"}
    for relative in tracked:
        path = Path(relative)
        if (
            (path.suffix.lower() not in allowed_suffixes and not path.name.startswith(".env"))
            or path.name.endswith(".lock")
            or path.parts[:1] in {("TESTS",), ("docs",)}
            or "vendor" in path.parts
            or relative == "tools/security_audit.py"
        ):
            continue
        try:
            lines = (ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if any(pattern.search(line) for pattern in _TOKEN_PATTERNS):
                matches.append((relative, number, "token/private-key pattern"))
                continue
            assignment = _SECRET_ASSIGNMENT_RE.search(line)
            if not assignment:
                continue
            candidate = assignment.group(1).strip().lower()
            normalized = candidate.replace("-", "_").replace(" ", "_")
            if candidate.startswith(("${", "{{")) or any(part in normalized for part in _PLACEHOLDER_PARTS):
                continue
            if len(set(candidate)) < 8:
                continue
            matches.append((relative, number, "sensitive assignment"))
    return matches


def filesystem_findings() -> list[Finding]:
    tracked = _tracked_files()
    if not tracked:
        return [Finding("tracked_runtime_data", "filesystem", "UNKNOWN", "Tracked files could not be enumerated with Git.")]
    runtime_files = [
        name for name in tracked
        if name.endswith((".db", ".sqlite", ".sqlite3", ".log"))
        or (name.startswith("SCRAPERS/OUTPUTS/") and not name.endswith(".gitkeep"))
        or (
            name.startswith("MIFPAPP/DATABASE/")
            and any(part in name for part in ("/assets/", "/backups/", "/exports/", "/logs/"))
        )
    ]
    return [
        Finding("tracked_runtime_data", "filesystem", "CRITICAL" if runtime_files else "OK", "No database, log, scraper output, upload, export, or backup artifact is tracked." if not runtime_files else f"Runtime data is tracked at {runtime_files[0]}.", "Remove generated data from Git; do not weaken the hygiene policy." if runtime_files else ""),
        Finding("database_boundary", "filesystem", "OK" if "../DATABASE/mifp.db" in _read("MIFPAPP/CORE/mifp_app/config.py") else "WARNING", "Default persistent storage remains outside the production application source tree."),
    ]


def _status_exit(findings: Iterable[Finding], *, strict: bool) -> int:
    statuses = {finding.status for finding in findings}
    if "CRITICAL" in statuses or (strict and "WARNING" in statuses):
        return 1
    return 0


def _render(findings: list[Finding], *, as_json: bool, verbose: bool, title: str = "MIFP security audit") -> None:
    if as_json:
        print(json.dumps({"title": title, "findings": [asdict(item) for item in findings]}, indent=2, sort_keys=True))
        return
    print(title)
    for item in findings:
        print(f"{item.status:<14} [{item.group}] {item.summary}")
        if verbose and item.remediation:
            print(f"  remediation: {item.remediation}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _fetch(url: str, *, timeout: float = 8.0, opener: Callable | None = None):
    request = urllib.request.Request(url, headers={"User-Agent": "MIFP-Security-Audit/1.0"})
    if opener is not None:
        return opener(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def production_findings(url: str, *, opener: Callable | None = None) -> tuple[list[Finding], bool]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("production URL must be an HTTPS origin without credentials")
    origin = urlunsplit(("https", parsed.netloc, "", "", ""))
    findings: list[Finding] = []
    unreachable = False
    try:
        response = _fetch(origin + "/", opener=opener)
        headers = {key.lower(): value for key, value in response.headers.items()}
        status = getattr(response, "status", response.getcode())
        findings.append(Finding("https", "production", "OK" if 200 <= status < 500 else "WARNING", f"HTTPS responded with status {status}."))
        expected = {
            "strict-transport-security": "HSTS",
            "content-security-policy": "Content-Security-Policy",
            "x-content-type-options": "X-Content-Type-Options",
            "referrer-policy": "Referrer-Policy",
            "permissions-policy": "Permissions-Policy",
        }
        for header, label in expected.items():
            findings.append(Finding(f"header_{header}", "production", "OK" if headers.get(header) else "WARNING", f"{label} is present." if headers.get(header) else f"{label} is missing from the live response."))
        server = headers.get("server", "")
        findings.append(Finding("server_disclosure", "production", "WARNING" if any(char.isdigit() for char in server) else "OK", "Server header does not disclose a version." if not any(char.isdigit() for char in server) else "Server header appears to disclose a version."))
        cookies = response.headers.get_all("Set-Cookie") or []
        session_cookies = [cookie for cookie in cookies if "session" in cookie.lower()]
        cookie_ok = all("secure" in cookie.lower() and "httponly" in cookie.lower() and "samesite=" in cookie.lower() for cookie in session_cookies)
        cookie_status = "NOT APPLICABLE" if not session_cookies else ("OK" if cookie_ok else "WARNING")
        cookie_summary = "No session cookie was issued by the public page." if not session_cookies else "Observed session cookies use Secure, HttpOnly and SameSite." if cookie_ok else "An observed session cookie is missing a required browser flag."
        findings.append(Finding("cookies", "production", cookie_status, cookie_summary))
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        unreachable = True
        findings.append(Finding("https", "production", "UNKNOWN", f"HTTPS endpoint is unreachable ({type(exc).__name__})."))
        return findings, unreachable

    http_origin = urlunsplit(("http", parsed.netloc, "", "", ""))
    try:
        if opener is not None:
            response = _fetch(http_origin + "/", opener=opener)
            code = getattr(response, "status", response.getcode())
            location = response.headers.get("Location", "")
        else:
            no_redirect = urllib.request.build_opener(_NoRedirect)
            try:
                response = _fetch(http_origin + "/", opener=no_redirect.open)
                code, location = response.getcode(), response.headers.get("Location", "")
            except urllib.error.HTTPError as exc:
                code, location = exc.code, exc.headers.get("Location", "")
        redirected = code in {301, 302, 307, 308} and location.startswith("https://")
        findings.append(Finding("http_redirect", "production", "OK" if redirected else "WARNING", "HTTP redirects to HTTPS." if redirected else "HTTP did not return an HTTPS redirect."))
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        findings.append(Finding("http_redirect", "production", "UNKNOWN", f"HTTP redirect could not be checked ({type(exc).__name__})."))

    for path, expected_hidden in (("/ready", True), ("/health", False)):
        try:
            response = _fetch(origin + path, opener=opener)
            code = getattr(response, "status", response.getcode())
        except urllib.error.HTTPError as exc:
            code = exc.code
        except (OSError, urllib.error.URLError, TimeoutError):
            findings.append(Finding(f"endpoint_{path[1:]}", "production", "UNKNOWN", f"{path} could not be checked."))
            continue
        ok = code == 404 if expected_hidden else 200 <= code < 300
        findings.append(Finding(f"endpoint_{path[1:]}", "production", "OK" if ok else "WARNING", f"{path} returned {code}; " + ("not publicly exposed." if expected_hidden and ok else "public liveness behaves as expected." if not expected_hidden and ok else "review reverse-proxy exposure.")))
    return findings, unreachable


def _run_optional(command: list[str], unavailable: str) -> int:
    if shutil.which(command[0]) is None and command[0] == "trivy":
        print(unavailable)
        return 0
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, check=False)
    except OSError:
        print(unavailable)
        return 0
    return 0 if result.returncode == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mifp security", description="Local defensive security diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "audit", "config", "headers", "filesystem", "uploads", "report"):
        child = sub.add_parser(name)
        child.add_argument("--json", action="store_true")
        child.add_argument("--verbose", action="store_true")
        child.add_argument("--strict", action="store_true")
    secrets = sub.add_parser("secrets")
    secrets.add_argument("--git-history", action="store_true")
    secrets.add_argument("--json", action="store_true")
    secrets.add_argument("--strict", action="store_true")
    sub.add_parser("dependencies")
    image = sub.add_parser("image")
    image.add_argument("image")
    production = sub.add_parser("production")
    production.add_argument("url")
    production.add_argument("--json", action="store_true")
    production.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "dependencies":
        try:
            __import__("pip_audit")
        except ImportError:
            print("pip-audit not available. Dependency scan skipped.")
            return 0
        result = 0
        for requirements in ("MIFPAPP/CORE/requirements.lock", "MIFPAPP/DATABASE/requirements.txt"):
            result = max(result, _run_optional([sys.executable, "-m", "pip_audit", "-r", requirements], "pip-audit not available. Dependency scan skipped."))
        return result
    if args.command == "image":
        return _run_optional(["trivy", "image", "--no-progress", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", args.image], "Trivy not available. Container image scan skipped.")
    if args.command == "production":
        try:
            findings, unreachable = production_findings(args.url)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        _render(findings, as_json=args.json, verbose=True, title=f"MIFP production security audit: {args.url}")
        return 2 if unreachable else _status_exit(findings, strict=args.strict)

    findings = repository_findings()
    if args.command == "secrets":
        findings = [item for item in findings if item.group == "secrets"]
        if args.git_history:
            if shutil.which("gitleaks") is None:
                findings.append(Finding("git_history", "secrets", "UNKNOWN", "Gitleaks is not available; Git history scan skipped."))
            else:
                result = subprocess.run(["gitleaks", "git", ".", "--no-banner", "--redact", "--config", ".gitleaks.toml"], cwd=ROOT, check=False)
                findings.append(Finding("git_history", "secrets", "OK" if result.returncode == 0 else "CRITICAL", "Gitleaks found no history leak." if result.returncode == 0 else "Gitleaks reported a potential history leak."))
    elif args.command in {"config", "headers", "filesystem", "uploads"}:
        findings = [item for item in findings if item.group == args.command]
    _render(
        findings,
        as_json=args.json,
        verbose=bool(getattr(args, "verbose", False)) or args.command == "report",
    )
    return _status_exit(findings, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())

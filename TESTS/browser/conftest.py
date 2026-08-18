from __future__ import annotations

import os
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash


REPO_ROOT = Path(__file__).resolve().parents[2]
WEBAPP_DIR = REPO_ROOT / "MIFPAPP" / "CORE"
DOT_ENV_PATH = WEBAPP_DIR / ".env"


def _read_env(key: str) -> str:
    if not DOT_ENV_PATH.exists():
        return ""
    for line in DOT_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().upper() == key:
            return v.strip().strip("\"'")
    return ""


def _base_url() -> str:
    return os.getenv("MIFP_TEST_BASE_URL", "http://127.0.0.1:8000")


def _admin_credentials() -> tuple[str, str]:
    user = os.getenv("MIFP_BROWSER_ADMIN_USER", "browser-test-admin")
    pw = os.getenv("MIFP_BROWSER_ADMIN_PASSWORD", "browser-test-password")
    return user, pw


@pytest.fixture(scope="session")
def browser():
    executable = next(
        (
            path for name in (
                "google-chrome-stable", "google-chrome", "chromium", "chromium-browser"
            )
            if (path := shutil.which(name))
        ),
        None,
    )
    if not executable:
        raise RuntimeError("Chrome/Chromium is required for browser tests")
    with sync_playwright() as pw:
        chrom = pw.chromium.launch(
            headless=True,
            executable_path=executable,
        )
        yield chrom
        chrom.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    p = context.new_page()
    yield p
    context.close()


@pytest.fixture
def auth_page(page, live_server):
    base = live_server
    user, pw = _admin_credentials()
    page.goto(f"{base}/login")
    token = page.evaluate("""
        () => document.querySelector('input[name="_csrf_token"]')?.value || ""
    """)
    page.evaluate("""
        ({token, user, pw}) => {
            document.querySelector('input[name="login_username"]').value = user;
            document.querySelector('input[name="login_password"]').value = pw;
            document.querySelector('input[name="_csrf_token"]').value = token;
            document.querySelector('#loginForm').submit();
        }
    """, {"token": token, "user": user, "pw": pw})
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/").rstrip("#") != f"{base}/login", "Login failed"
    return page


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    requested_base = os.getenv("MIFP_TEST_BASE_URL", "").strip()
    if requested_base:
        yield requested_base.rstrip("/")
        return
    env = os.environ.copy()
    env["FLASK_ENV"] = "development"
    env["TESTING"] = "1"
    server_dir = tmp_path_factory.mktemp("browser-server")
    assets_dir = server_dir / "assets"
    assets_dir.mkdir()
    (server_dir / "exports").mkdir()
    (server_dir / "logs").mkdir()
    banner_path = server_dir / "banner_settings.json"
    banner_path.write_text(json.dumps({
        "cookie_banner_enabled": "1",
        "cookie_banner_text": "Browser test cookie notice.",
        "cookie_banner_link_enabled": "1",
        "cookie_banner_dismiss_label": "Dismiss",
        "cookie_banner_theme": "brand",
        "banner_force_show": "browser-test",
    }), encoding="utf-8")
    user, password = _admin_credentials()
    env["SECRET_KEY"] = "browser-test-secret-key"
    env["ADMIN_USERNAME"] = user
    env["ADMIN_PASSWORD_HASH"] = generate_password_hash(password)
    env["DATABASE_PATH"] = str(server_dir / "mifp.db")
    env["ASSETS_DIR"] = str(assets_dir)
    env["EXPORT_DIR"] = str(server_dir / "exports")
    env["CONFERENCES_DIR"] = str(server_dir / "conferences")
    env["LOG_DIR"] = str(server_dir / "logs")
    env["BANNER_SETTINGS_PATH"] = str(banner_path)
    env["LOG_ACCESS_ENABLED"] = "0"
    env["AUTO_SYNC_CONFERENCES_ON_STARTUP"] = "0"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "flask", "run", "--host=127.0.0.1", f"--port={port}"],
        cwd=str(WEBAPP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            try:
                import urllib.request
                urllib.request.urlopen(f"{base}/health", timeout=2)
                logo_source = WEBAPP_DIR / "mifp_app/static/img/logo-mifp.png"
                logo_target = assets_dir / "browser-logo.png"
                shutil.copy2(logo_source, logo_target)
                with sqlite3.connect(server_dir / "mifp.db") as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO roles(id,name,label) VALUES(1,'member','Member')"
                    )
                    sponsor_id = conn.execute(
                        "INSERT INTO sponsors(name,slug,description,is_active) VALUES('Browser Sponsor','browser-sponsor','Test sponsor',1)"
                    ).lastrowid
                    asset_id = conn.execute(
                        """
                        INSERT INTO assets(filename,original_filename,path,kind,mime_type)
                        VALUES('browser-logo.png','browser-logo.png','browser-logo.png','image','image/png')
                        """
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary)
                        VALUES(?,'sponsor',?,'logo',1)
                        """,
                        (asset_id, sponsor_id),
                    )
                    conn.execute(
                        "INSERT INTO publications(title,slug,year,authors,review_status) VALUES('Browser Publication','browser-publication',2026,'Test Author','published')"
                    )
                    conn.execute(
                        "INSERT INTO research_areas(title,slug,summary,description,review_status) VALUES('Browser Research','browser-research','Summary','Description','published')"
                    )
                    conn.commit()
                yield base
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("Server did not start in time")
    finally:
        proc.kill()
        proc.wait(timeout=5)

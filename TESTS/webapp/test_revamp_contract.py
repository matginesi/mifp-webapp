from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "MIFPAPP" / "CORE" / "mifp_app"


def test_dashboard_uses_bounded_page_specific_modules_without_build_step():
    expected = {
        APP / "static/css/dashboard.css",
        APP / "static/css/homepage.css",
        APP / "static/js/homepage.js",
    }
    assert all(path.is_file() and path.stat().st_size > 1000 for path in expected)
    modules = sorted((APP / "static/js/dashboard").glob("*.js"))
    assert 6 <= len(modules) <= 12
    assert all(path.stat().st_size > 300 for path in modules)
    assert not (APP / "static/js/dashboard.js").exists()
    assert not (APP / "static/css/dashboard").exists()
    assert not (APP / "static/css/public").exists()
    for css in expected & set(APP.glob("static/css/*.css")):
        assert "@import" not in css.read_text(encoding="utf-8")
    templates = "\n".join(p.read_text(encoding="utf-8") for p in (APP / "templates").rglob("*.html"))
    for retired in ("common.js", "public-site.js", "lightbox.js", "publications.js", "research.js", "dashboard-conferences.js", "tailwind.css"):
        assert retired not in templates
    assert "dashboard/core.js" in templates
    assert "dashboard/stats.js" in templates
    assert "dashboard/data-quality.js" in templates


def test_retired_assistant_and_graph_assets_are_absent():
    names = {p.name.lower() for p in APP.rglob("*") if p.is_file()}
    assert not any("chatbot" in name or "cytoscape" in name or "graph-editor" in name for name in names)


def test_new_schema_omits_retired_assistant_tables_and_legacy_db_is_compatible(tmp_path):
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as legacy:
        legacy.execute("CREATE TABLE chatbot_faq(id INTEGER PRIMARY KEY, question TEXT)")
    with connect(db_path) as conn:
        migrate_content_schema(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "chatbot_faq" in tables  # non-destructive legacy compatibility
    clean_path = tmp_path / "clean.db"
    with connect(clean_path) as conn:
        migrate_content_schema(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"chatbot_faq", "chatbot_aliases", "chatbot_unanswered"} & tables


def test_local_font_and_vendor_magic_numbers():
    assert (APP / "static/fonts/Inter.woff2").read_bytes()[:4] == b"wOF2"
    assert (APP / "static/css/vendor/fonts/bootstrap-icons.woff2").read_bytes()[:4] == b"wOF2"
    assert (APP / "static/js/vendor/bootstrap.bundle.min.js").read_bytes()[:2] != b"<!"


def test_templates_have_no_executable_inline_handlers_or_remote_embeds():
    templates = "\n".join(p.read_text(encoding="utf-8") for p in (APP / "templates").rglob("*.html"))
    lowered = templates.lower()
    assert "onclick=" not in lowered
    assert "onchange=" not in lowered
    assert "onsubmit=" not in lowered
    assert "<iframe" not in lowered
    assert "cdn." not in lowered


def test_dashboard_toasts_are_mirrored_to_browser_console():
    core = (APP / "static/js/dashboard/core.js").read_text(encoding="utf-8")

    assert "function logToastToConsole" in core
    assert "console.error" not in core  # levels are selected through the bounded map
    assert "error: 'error'" in core
    assert "warning: 'warn'" in core
    assert "success: 'info'" in core
    assert "logToastToConsole(message, type);" in core

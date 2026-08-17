from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "MIFPAPP" / "CORE" / "mifp_app"
CORE = APP.parent


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


def test_public_and_dashboard_theme_contract_is_documented_and_enforced():
    guide = CORE / "docs" / "THEME_SYSTEMS.md"
    assert guide.is_file()
    guide_text = guide.read_text(encoding="utf-8")
    assert "Public theme: Scientific atlas" in guide_text
    assert "Dashboard theme: Control instrument" in guide_text
    assert "Dashboard-page checklist" in guide_text

    dashboard_css = (APP / "static/css/dashboard.css").read_text(encoding="utf-8")
    public_css = (APP / "static/css/homepage.css").read_text(encoding="utf-8")

    # Dashboard state is expressed with flat surfaces and semantic color only.
    assert "linear-gradient(" not in dashboard_css
    assert "radial-gradient(" not in dashboard_css
    assert "--content-bg: #eef1f4" in dashboard_css
    assert "--radius: .3125rem" in dashboard_css
    assert "--radius-lg: .4375rem" in dashboard_css
    assert "--focus-ring: #456f9d" in dashboard_css
    assert "--font-family-editorial:" in dashboard_css
    assert dashboard_css.count("Georgia") == 1
    assert ".dashboard-skip-link:focus-visible { outline: 2px solid var(--focus-ring)" in dashboard_css
    assert ":focus-visible { outline: 3px" not in dashboard_css

    # The public scientific field is the one intentional atmospheric exception.
    assert public_css.count("linear-gradient(") == 3
    assert "radial-gradient(" not in public_css
    assert "--r:    0.375rem" in public_css
    assert "--r-xl: 0.5rem" in public_css
    assert "--focus-ring: #86a8eb" in public_css
    assert ".public-site :focus-visible { outline: 2px solid var(--focus-ring)" in public_css
    assert ".public-site :focus-visible { outline: 3px" not in public_css

    public_templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (APP / "templates" / "public").rglob("*.html")
    )
    assert "linear-gradient(" not in public_templates
    assert "radial-gradient(" not in public_templates


def test_dark_dashboard_workflows_override_global_heading_color():
    dashboard_css = (APP / "static/css/dashboard.css").read_text(encoding="utf-8")
    quality_template = (
        APP / "templates/dashboard/control/quality.html"
    ).read_text(encoding="utf-8")

    assert ".control-quality-workflow-copy h2," in dashboard_css
    assert ".control-quality-workflow-stats b { color: #fff; }" in dashboard_css
    assert ".safety-intro h2 {" in dashboard_css
    assert "role=\"group\" aria-label=\"Latest Data Quality state\"" in quality_template


def test_dashboard_overlays_use_one_theme_contract_without_retired_log_tiles():
    dashboard_css = (APP / "static/css/dashboard.css").read_text(encoding="utf-8")

    assert dashboard_css.count("--dashboard-modal-gutter: clamp(") == 1
    assert ".dashboard-shell .modal-header .modal-title" in dashboard_css
    assert ".dashboard-document .modal" not in dashboard_css
    assert "border-bottom: 2px solid var(--accent)" in dashboard_css
    assert ".log-context-grid" in dashboard_css
    assert ".log-file-card" not in dashboard_css
    assert ".log-level-overview" not in dashboard_css


def test_dashboard_pages_use_the_shared_page_header():
    templates = APP / "templates" / "dashboard"
    pages = [
        path
        for path in templates.rglob("*.html")
        if not path.name.startswith("_") and path.name != "layout.html"
    ]
    missing = [
        str(path.relative_to(templates))
        for path in pages
        if "page_header(" not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"Dashboard pages bypass the shared header: {missing}"


def test_dashboard_page_families_share_the_control_instrument_contract():
    dashboard_css = (APP / "static/css/dashboard.css").read_text(encoding="utf-8")
    guide = (CORE / "docs/THEME_SYSTEMS.md").read_text(encoding="utf-8")

    assert "Shared control-instrument surfaces" in dashboard_css
    assert ".dash-hero {" in dashboard_css
    assert ".dashboard-hero" not in dashboard_css
    assert "background: var(--surface); border: 1px solid var(--border)" in dashboard_css
    assert ".control-quality-workflow,\n.safety-intro" in dashboard_css
    assert "Page-family coverage" in guide
    for undefined_legacy_token in ("var(--brand)", "var(--warning)", "var(--text-4)"):
        assert undefined_legacy_token not in dashboard_css


def test_repository_keeps_one_readme_and_no_orphan_database_tools():
    readmes = [
        path
        for path in ROOT.rglob("README.md")
        if ".git" not in path.parts
        and ".venv" not in path.parts
        and ".pytest_cache" not in path.parts
    ]
    assert readmes == [ROOT / "README.md"]

    assert (ROOT / "SCRAPERS/validate_import_data.py").is_file()
    assert "SCRAPERS_DIR/validate_import_data.py" in (
        ROOT / "SCRAPERS/run_all.sh"
    ).read_text(encoding="utf-8")
    assert not (ROOT / "tools").exists()

    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Non eseguire script Python ad hoc contro il database" in root_readme
    assert "L'immagine Docker ha come contesto `MIFPAPP/CORE`" in root_readme


def test_theme_css_has_no_retired_public_layers():
    dashboard_css = (APP / "static/css/dashboard.css").read_text(encoding="utf-8")
    public_css = (APP / "static/css/homepage.css").read_text(encoding="utf-8")

    for retired in (
        ".cookie-banner-neutral",
        ".btn-amber",
        ".section-lg",
    ):
        assert retired not in public_css
    assert "\n.sponsor-modal {" not in public_css
    for retired in (
        ".dashboard-hero",
        ".unified-toolbar",
        ".port-panel-head",
        ".port-count-card",
        ".zip-manifest-pre",
    ):
        assert retired not in dashboard_css

    # Theme tokens are canonical, not silently replaced by a late :root block.
    assert public_css.count(":root {") == 2  # global tokens + mobile nav-height override


def test_public_directory_and_institutional_pages_share_the_new_contract():
    public_templates = APP / "templates/public"
    public_css = (APP / "static/css/homepage.css").read_text(encoding="utf-8")

    institutional_pages = (
        "about.html",
        "manifesto.html",
        "code_of_conduct.html",
        "research.html",
        "publications.html",
        "sponsor_how_to.html",
        "privacy.html",
        "cookie_policy.html",
    )
    for name in institutional_pages:
        template = (public_templates / name).read_text(encoding="utf-8")
        assert 'include "public/_institutional_nav.html"' in template
        assert "{% block body_class %} institutional-page{% endblock %}" in template

    members = (public_templates / "members.html").read_text(encoding="utf-8")
    sponsors = (public_templates / "sponsors.html").read_text(encoding="utf-8")
    sponsor_modal = (public_templates / "_sponsor_modal.html").read_text(encoding="utf-8")
    assert "member-directory-tools" in members
    assert "member-index" in members
    assert "partner-registry-head" in sponsors
    assert "sponsor-profile-cue" in sponsors
    assert "sponsor-modal-grid" in sponsor_modal

    for selector in (
        ".institutional-index",
        ".institutional-document-layout",
        ".institutional-page .institutional-document .md-body",
        ".research-hero-layout",
        ".research-profile",
        ".research-chart-grid",
        ".member-directory-tools",
        ".partner-registry-head",
        ".sponsor-modal-grid",
    ):
        assert selector in public_css

    research = (public_templates / "research.html").read_text(encoding="utf-8")
    assert "research_stats.areas" in research
    assert 'class="research-overview"' in research
    assert 'style="' not in research

    base = (public_templates / "base.html").read_text(encoding="utf-8")
    pdf = (public_templates / "pdf_page.html").read_text(encoding="utf-8")
    assert 'public-site{% block body_class %}' in base
    assert "--f-body:" in public_css
    assert "--f-display:" in public_css
    assert "--f-data:" in public_css
    assert "--pdf-serif:" in pdf
    assert "--pdf-sans:" in pdf
    assert ".pdf-header .doc-title" in pdf
    assert "font-family: var(--pdf-serif);" in pdf

    conference_css = (APP / "conference_templates/site.css").read_text(encoding="utf-8")
    wip_css = (APP / "static/css/work-in-progress.css").read_text(encoding="utf-8")
    assert "--conference-body:" in conference_css
    assert "--conference-display:" in conference_css
    assert "--wip-body:" in wip_css
    assert "--wip-display:" in wip_css


def test_dashboard_filters_and_asset_preview_links_have_accessible_names():
    assets = (APP / "templates/dashboard/assets.html").read_text(encoding="utf-8")
    joins = (APP / "templates/dashboard/join_requests.html").read_text(encoding="utf-8")
    quality = (APP / "templates/dashboard/data_quality.html").read_text(encoding="utf-8")

    assert 'aria-label="Asset kind"' in assets
    assert 'aria-label="Open external asset {{ a.filename }}"' in assets
    assert 'aria-label="Application status"' in joins
    assert "'Data quality', 'Database cleanup'" not in quality
    assert "'Operations', 'Data quality'" in quality

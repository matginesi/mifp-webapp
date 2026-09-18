"""Frontend coherence contracts.

These guard the structural decisions made by the frontend/CSS/JS coherence pass:
one dashboard operation-totem contract, one shared progress primitive, honest
CSS section markers, and the "raw console only inside the central logger" rule.

They deliberately test structure and contracts, not pixels: the visual identity
is pinned by ``test_revamp_contract.py`` (tokens, gradients, pinned selectors).
"""
from __future__ import annotations

import io
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "MIFPAPP" / "CORE" / "mifp_app"
STATIC = APP / "static"
CSS = STATIC / "css"
JS = STATIC / "js"
TEMPLATES = APP / "templates"

JS_MODULES = sorted(
    path for path in JS.rglob("*.js") if "vendor" not in path.parts
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_raw_console_calls_exist_only_inside_the_central_logger():
    """Every application module logs through window.MIFPLog.

    ``logger.js`` is the one place allowed to touch the console; it selects the
    method through a bounded level map, so a new call site cannot silently add
    an unbounded ``console.log``.
    """
    offenders = {
        path.relative_to(APP).as_posix(): re.findall(r"console\.\w+", _read(path))
        for path in JS_MODULES
        if path.name != "logger.js" and "console." in _read(path)
    }
    assert offenders == {}, f"raw console usage outside logger.js: {offenders}"

    logger = _read(JS / "logger.js")
    assert re.findall(r"console\.\w+", logger) == ["console.log"], (
        "logger.js must reach the console only through its bounded method map"
    )


def test_application_js_uses_namespaced_log_events():
    """Every MIFPLog event id is a dotted, kebab-or-snake, lowercase id.

    Most modules keep a local alias (``contentLog``, ``eventLog``,
    ``transferLog``, …) so the simplest way to miss regressions is to only look
    for literal ``MIFPLog.`` calls — this resolves the aliases instead. The
    codebase uses ``<module>.<event>`` and the three-part
    ``<module>.<operation>.<event>`` form for operations with a lifecycle; both
    are accepted, "Invalid password" is not.
    """
    alias = re.compile(
        r"(?:var|let|const)\s+(\w*[Ll]og)\s*=\s*window\.MIFPLog\b"
    )
    # `contentLog.info('x.y')`, `window.MIFPLog?.warn('x.y')`, `log.error('x.y')`
    call = re.compile(
        r"(?:window\.)?(MIFPLog|\w*[Ll]og)\s*\??\.\s*(\w+)\s*\(\s*['\"]([^'\"]+)['\"]"
    )
    # `dashboardLog('info', 'x.y', {...})` — the level comes first and may be an
    # expression (`dashboardLog(cond ? 'warn' : type, 'ui.toast', …)`).
    helper = re.compile(r"dashboardLog\([^,]+,\s*['\"]([^'\"]+)['\"]")
    events: dict[str, set[str]] = {}
    for path in JS_MODULES:
        if path.name == "logger.js":
            continue
        source = _read(path)
        aliases = set(alias.findall(source))
        for name, _method, event in call.findall(source):
            if name != "MIFPLog" and name not in aliases:
                continue  # a page-local helper such as the archive activity log
            events.setdefault(event, set()).add(path.name)
        for event in helper.findall(source):
            events.setdefault(event, set()).add(path.name)

    assert len(events) >= 20, f"expected the whole event vocabulary, found {sorted(events)}"

    valid = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+$")
    malformed = sorted(event for event in events if not valid.fullmatch(event))
    assert malformed == [], f"log events must be dotted lowercase ids: {malformed}"

    # The vocabulary is namespaced: no bare "toast" or "submit".
    namespaces = {event.split(".")[0] for event in events}
    assert "ui" in namespaces and "api" in namespaces


def test_dashboard_has_one_operation_totem_contract():
    css = _read(CSS / "dashboard.css")
    templates = "\n".join(_read(p) for p in TEMPLATES.rglob("*.html"))

    def base(selector: str) -> int:
        """Rules for ``selector`` starting on their own line (not a descendant
        or attribute-qualified variant, and not a media-query override)."""
        return len(re.findall(rf"(?m)^{re.escape(selector)} \{{", css))

    def responsive(selector: str) -> int:
        return len(re.findall(rf"(?m)^\s+{re.escape(selector)} \{{", css))

    # Exactly one base rule per part of the contract; the only repetition is the
    # single narrow-screen override, which re-flows the grid it must.
    for single in (
        ".operation-totem",
        ".operation-totem--transfer",
        ".operation-totem__icon",
        ".operation-totem__body",
        ".operation-totem__title",
        ".operation-totem__meta",
        ".operation-totem__eyebrow",
        ".operation-totem__state",
        ".operation-totem__facts",
    ):
        assert base(single) == 1, single
    assert base(".operation-totem--transfer") == 1, "one modifier variant only"

    for overridden in (".operation-totem", ".operation-totem__state", ".operation-totem__facts"):
        assert responsive(overridden) == 1, overridden

    # Tone is `data-state` and nothing else: no tone class may creep in.
    for tone in ("success", "warning", "error", "queued", "working", "idle"):
        assert f".operation-totem--{tone}" not in css, tone
        assert f".operation-totem__{tone}" not in css, tone

    for tone in ("success", "error", "warning"):
        assert f'data-state="{tone}"' in css
    assert "data-state=" in templates

    for retired in (
        ".archive-package-totem",
        ".transfer-operation-totem",
        ".transfer-totem-icon",
        ".transfer-totem-copy",
        ".transfer-totem-state",
        ".transfer-totem-facts",
        "archive-package-totem",
        "transfer-operation-totem",
        "transfer-totem-",
    ):
        assert retired not in css, f"legacy totem styling left in dashboard.css: {retired}"
        assert retired not in templates, f"legacy totem class left in a template: {retired}"


def _operation_totem(*args, **kwargs) -> str:
    """Render the real ``operation_totem`` macro exactly as a page would."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    module = env.get_template("dashboard/_components.html").make_module()
    return module.operation_totem(*args, **kwargs)


def test_operation_totem_macro_renders_every_id_the_dashboard_js_addresses():
    """The totem markup is addressed by id from JS; those ids are the contract.

    Renders the shared macro rather than pattern-matching the template source:
    an id only survives if the macro actually emits it as an attribute.
    """
    archive = _operation_totem(
        "bi-file-earmark-zip",
        "archivePackageName",
        "Historical package",
        meta_id="archivePackageSize",
        meta="Waiting for upload",
        state="idle",
        state_id="archivePackageState",
        state_label="Queued",
        totem_id="archivePackageTotem",
    )
    assert 'class="operation-totem"' in archive
    assert 'data-state="idle"' in archive
    assert "operation-totem--" not in archive, "the base totem carries no variant"
    for element_id in ("archivePackageTotem", "archivePackageName", "archivePackageSize", "archivePackageState"):
        assert f'id="{element_id}"' in archive, element_id
    assert ">Queued<" in archive

    transfer = _operation_totem(
        "bi-arrow-left-right",
        "transferTotemTitle",
        "Preparing operation",
        meta_id="transferTotemMeta",
        meta="Waiting for transfer metadata",
        state="working",
        state_id="transferTotemState",
        state_label="Preparing",
        variant="transfer",
        totem_id="transferOperationTotem",
        eyebrow_id="transferTotemEyebrow",
        eyebrow="Data transfer",
        icon_id="transferTotemIcon",
        facts=[
            ("Mode", "\u2014", "transferTotemMode"),
            ("Format", "\u2014", "transferTotemFormat"),
            ("Assets", "\u2014", "transferTotemAssets"),
        ],
    )
    assert 'class="operation-totem operation-totem--transfer"' in transfer
    assert 'data-state="working"' in transfer
    assert ">Data transfer<" in transfer and ">Preparing<" in transfer
    for element_id in (
        "transferOperationTotem",
        "transferTotemIcon",
        "transferTotemEyebrow",
        "transferTotemTitle",
        "transferTotemMeta",
        "transferTotemState",
        "transferTotemMode",
        "transferTotemFormat",
        "transferTotemAssets",
    ):
        assert f'id="{element_id}"' in transfer, element_id

    # No unknown state may reach data-state, and an unknown tone has no style.
    assert _operation_totem("bi-x", "t", "T", state="success").count('data-state="success"') == 1


def test_pages_render_the_totem_through_the_shared_macro():
    """A page must not hand-write totem markup: that is how the copies drifted."""
    for name in ("archive.html", "data_portability.html"):
        source = _read(TEMPLATES / "dashboard" / name)
        assert "import page_header, operation_totem" in source, name
        assert "operation_totem(" in source, name
        assert 'class="operation-totem' not in source, f"{name} hand-writes totem markup"

    # The ids the JS selects must still be passed by the pages.
    archive = _read(TEMPLATES / "dashboard" / "archive.html")
    for element_id in ("archivePackageTotem", "archivePackageName", "archivePackageSize", "archivePackageState"):
        assert f"'{element_id}'" in archive, element_id
    transfer = _read(TEMPLATES / "dashboard" / "data_portability.html")
    for element_id in (
        "transferOperationTotem",
        "transferTotemIcon",
        "transferTotemEyebrow",
        "transferTotemTitle",
        "transferTotemMeta",
        "transferTotemState",
        "transferTotemMode",
        "transferTotemFormat",
        "transferTotemAssets",
    ):
        assert f"'{element_id}'" in transfer, element_id


def test_shared_progress_primitive_is_the_only_implementation():
    core = _read(JS / "dashboard" / "core.js")
    assert core.count("function setProgressBar(") == 1
    assert core.count("function elapsedLabel(") == 1
    assert "setProgressBar: setProgressBar" in core
    assert "elapsedLabel: elapsedLabel" in core

    for module in ("archive-import.js", "data-portability.js"):
        source = _read(JS / "dashboard" / module)
        assert "function setProgressBar(" not in source
        assert "function elapsedLabel(" not in source
        assert "function formatElapsed(" not in source
        assert "window.MIFPUI.setProgressBar(" in source
        assert "window.MIFPUI.elapsedLabel(" in source


def test_css_section_markers_are_honest_reading_aids():
    """The two bundles are canonical.

    The split source directories are asserted absent by
    ``test_revamp_contract.py``; what matters here is that no banner presents a
    section file as a generator input, and that each sheet says out loud that
    the ``SECTION`` banners are only reading aids.
    """
    for name in ("dashboard.css", "homepage.css"):
        css = _read(CSS / name)
        assert "Source section:" not in css, name
        assert "SINGLE CANONICAL BUNDLE" in css, name
        assert "reading aids" in css, name
        assert f"static/css/{name}" not in css, f"{name} must not name itself as a source section"

def test_removed_dead_component_selectors_stay_removed():
    """Classes proven unused across templates, JS and tests."""
    dead = (
        "system-status-strip",
        "mini-chart",
        "dq-phase-highlight",
        "alert-body",
        "alert-icon",
        "col-asset",
        "privacy-badge",
        "is-expanded",
        "cols-2",
        "cols-4",
    )
    css = _read(CSS / "dashboard.css") + _read(CSS / "homepage.css")
    for name in dead:
        assert not re.search(rf"\.{re.escape(name)}(?![\w-])", css), name


def test_browser_logger_redacts_secrets_and_url_values():
    """The central logger keeps its bounded-redaction contract."""
    logger = _read(JS / "logger.js")
    for marker in (
        "SENSITIVE_KEY",      # key-based redaction (password/token/csrf/cookie/...)
        "SECRET_VALUE",       # query values under a sensitive key
        "EMAIL",              # e-mail addresses
        "CREDENTIAL",         # Authorization: Basic/Bearer
        "[REDACTED]",
        "[REDACTED_EMAIL]",
        "[REDACTED_CREDENTIAL]",
        "safePath",           # strips query strings before logging a URL
        "[MAX_DEPTH]",        # recursion bound
        "slice(0, 20)",       # array bound
        "slice(0, 40)",       # object-key bound
        "slice(0, 1000)",     # string bound
    ):
        assert marker in logger, marker
    # A URL must never be logged with its query string or fragment.
    safe_path = logger.split("function safePath", 1)[1].split("function ", 1)[0]
    assert "search" not in safe_path and "hash" not in safe_path
    assert "url.pathname" in safe_path


def test_log_file_and_console_formats_can_differ(tmp_path, monkeypatch):
    """Files are the durable JSONL record; the console is compact text.

    ``LOG_FORMAT`` stays the single fallback so an existing deployment is
    unchanged.
    """
    from mifp_app.utils.logger import setup_logging, shutdown_logging

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    try:
        logger = setup_logging(
            tmp_path, "INFO", output="both",
            log_format="json", file_format="json", console_format="text",
        )
        logger.info("format probe")
        for handler in logging.getLogger("mifp").handlers:
            handler.flush()
    finally:
        shutdown_logging()

    files = sorted(p.name for p in tmp_path.iterdir())
    assert "mifp_app.jsonl" in files, files
    assert "mifp_app.log" not in files, files
    written = captured.getvalue().strip()
    assert written and not written.startswith("{"), "console must be text, not JSON"
    assert "format probe" in written


def test_log_format_fallback_preserves_legacy_single_format(tmp_path, monkeypatch):
    """Setting only LOG_FORMAT (the historic knob) must still drive both."""
    from mifp_app.utils.logger import setup_logging, shutdown_logging

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    try:
        logger = setup_logging(tmp_path, "INFO", json_logs=True, output="both")
        logger.info("legacy probe")
        for handler in logging.getLogger("mifp").handlers:
            handler.flush()
    finally:
        shutdown_logging()

    files = sorted(p.name for p in tmp_path.iterdir())
    assert "mifp_app.jsonl" in files, files
    line = captured.getvalue().strip()
    assert line.startswith("{"), "legacy json_logs=True keeps a JSON console"
    assert json.loads(line)["message"] == "legacy probe"

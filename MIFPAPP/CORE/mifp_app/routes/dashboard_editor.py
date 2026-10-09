"""Authenticated, browser-only Conference Editor and source-built templates."""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from flask import Response, abort, g, render_template_string, send_file, send_from_directory, url_for

from .auth import login_required
from .dashboard import bp

EDITOR_ROOT = Path(__file__).resolve().parents[2] / "CONFERENCE_EDITOR"


@lru_cache(maxsize=3)
def template_archive(name: str) -> bytes:
    """Package immutable source files; no generated archives in Git or on disk."""
    sources = {
        "conference-template.zip": (EDITOR_ROOT / "TEMPLATE", "TEMPLATE/"),
        "badges_template.docx": (EDITOR_ROOT / "document_templates/badges_template", ""),
        "certificate_template.docx": (EDITOR_ROOT / "document_templates/certificate_template", ""),
    }
    root, prefix = sources[name]
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                info = ZipInfo(prefix + path.relative_to(root).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    return output.getvalue()


@bp.get("/conference-editor/")
@login_required
def conference_editor():
    return render_template_string((EDITOR_ROOT / "index.html").read_text(encoding="utf-8"),
                                  csp_nonce=g.csp_nonce,
                                  logger_url=url_for("static", filename="js/logger.js"))


@bp.get("/conference-editor/templates/<name>")
@login_required
def conference_editor_template(name):
    if name not in {"conference-template.zip", "badges_template.docx", "certificate_template.docx"}:
        abort(404)
    return send_file(io.BytesIO(template_archive(name)), download_name=name,
                     mimetype="application/zip" if name.endswith(".zip") else
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@bp.get("/conference-editor/preview/")
@login_required
def conference_editor_preview():
    return Response('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Conference preview</title><style>body{margin:0}iframe{width:100%;height:100vh;border:0}</style>
</head><body><iframe title="Conference preview" sandbox="allow-scripts allow-downloads allow-modals"></iframe>
<script src="../assets/js/preview-shell.js"></script></body></html>''', mimetype="text/html")


@bp.get("/conference-editor/<path:filename>")
@login_required
def conference_editor_asset(filename):
    # Never expose PHP sources, templates or local conference data as assets.
    if filename.startswith("assets/"):
        return send_from_directory(EDITOR_ROOT / "assets", filename.removeprefix("assets/"))
    if filename in {"favicon.ico", "config/workspace.json"}:
        return send_from_directory(EDITOR_ROOT, filename)
    abort(404)

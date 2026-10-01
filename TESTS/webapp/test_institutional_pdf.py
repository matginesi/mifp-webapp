from __future__ import annotations

import io
import socket
from pathlib import Path

import pytest
from pypdf import PdfReader
from werkzeug.security import generate_password_hash


@pytest.fixture
def public_pdf_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mifp.db"))
    monkeypatch.setenv("ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("CONFERENCES_DIR", str(tmp_path / "conferences"))
    monkeypatch.setenv("RUNTIME_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SECRET_KEY", "institutional-pdf-test-key-that-is-long-enough")
    from mifp_app import create_app
    from mifp_app.db.manage import init_database

    init_database(tmp_path / "mifp.db")
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences",
        RUNTIME_CONFIG_DIR=tmp_path / "config",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "RUNTIME_CONFIG_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    return app


def _text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _uris(reader: PdfReader) -> set[str]:
    result: set[str] = set()
    for page in reader.pages:
        for annotation_ref in page.get("/Annots", []):
            annotation = annotation_ref.get_object()
            action = annotation.get("/A")
            if action and action.get("/URI"):
                result.add(str(action["/URI"]))
    return result


def _has_image(reader: PdfReader) -> bool:
    for page in reader.pages:
        resources = page.get("/Resources", {})
        for item_ref in resources.get("/XObject", {}).values():
            if item_ref.get_object().get("/Subtype") == "/Image":
                return True
    return False


def test_institutional_pdf_preserves_content_links_metadata_images_and_unicode(
    public_pdf_app, monkeypatch
) -> None:
    from mifp_app.services.institutional_pdf import render_institutional_pdf

    def reject_network(*_args, **_kwargs):
        raise AssertionError("PDF rendering attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    html = """
      <h1>Reference αβγ — città &amp; ricerca</h1>
      <p>A paragraph with <strong>bold</strong>, <em>italic</em>, <u>underline</u>,
      H<sub>2</sub>O and x<sup>2</sup>.</p>
      <p><a href="https://example.org/evidence?q=mifp">External evidence</a> and
      <a href="mailto:secretary@mifp.eu">secretary@mifp.eu</a>.</p>
      <img src="/static/img/logo-mifp.png" alt="MIFP institutional logo" width="260">
      <img src="https://remote.invalid/tracker.png" alt="Remote image unavailable">
      <blockquote>A quoted note with “typographic quotation marks”.</blockquote>
      <ul><li>First item</li><li>Second item<ul><li>Nested item</li></ul></li></ul>
      <ol><li>Ordered one</li><li>Ordered two</li></ol>
      <table><caption>Programme overview</caption><thead><tr><th>Programme</th><th>Count</th></tr></thead>
      <tbody><tr><td>Quantum systems</td><td style="text-align:right">12</td></tr></tbody></table>
      <pre>line 1\nline 2    aligned</pre>
    """
    payload = render_institutional_pdf(
        title="Reference document",
        content_html=html,
        exported_on="2026-10-01",
        source_url="https://mifp.eu",
        assets_dir=public_pdf_app.config["ASSETS_DIR"],
        static_dir=public_pdf_app.static_folder,
    )

    assert payload.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(payload))
    text = _text(payload)
    assert reader.metadata.title == "Reference document — MIFP"
    assert reader.metadata.author == "Matteo Ginesi"
    for expected in (
        "Reference αβγ — città & ricerca",
        "H2O and x2",
        "External evidence",
        "Remote image unavailable",
        "Nested item",
        "Ordered two",
        "Programme overview",
        "Quantum systems",
        "line 2",
        "Mediterranean Institute of Fundamental Physics — https://mifp.eu",
    ):
        assert expected in text
    assert _uris(reader) >= {
        "https://example.org/evidence?q=mifp",
        "mailto:secretary@mifp.eu",
    }
    assert _has_image(reader)
    assert float(reader.pages[0].mediabox.width) == pytest.approx(595.276, abs=0.01)
    assert float(reader.pages[0].mediabox.height) == pytest.approx(841.89, abs=0.01)


def test_institutional_pdf_splits_long_tables_and_repeats_headers(public_pdf_app) -> None:
    from mifp_app.services.institutional_pdf import render_institutional_pdf

    rows = "".join(
        f"<tr><td>Research programme {index}</td><td>{index * 3}</td><td>Active</td></tr>"
        for index in range(1, 121)
    )
    payload = render_institutional_pdf(
        title="Long table",
        content_html=(
            "<h1>Programme register</h1><table><thead><tr><th>Programme</th><th>Participants</th>"
            f"<th>Status</th></tr></thead><tbody>{rows}</tbody></table><h2>Closing note</h2>"
            "<p>All records are included.</p>"
        ),
        exported_on="2026-10-01",
        source_url="https://mifp.eu",
        assets_dir=public_pdf_app.config["ASSETS_DIR"],
        static_dir=public_pdf_app.static_folder,
    )

    reader = PdfReader(io.BytesIO(payload))
    text = _text(payload)
    assert len(reader.pages) >= 3
    assert text.count("Participants") >= 2
    assert "Research programme 1" in text
    assert "Research programme 120" in text
    assert "Closing note" in text
    assert "All records are included." in text
    for page_number, page in enumerate(reader.pages, 1):
        assert str(page_number) in (page.extract_text() or "")


def test_institutional_pdf_rejects_excessive_content_complexity(public_pdf_app) -> None:
    from mifp_app.services.institutional_pdf import (
        MAX_PDF_HTML_CHARS,
        render_institutional_pdf,
    )

    arguments = {
        "title": "Limits",
        "exported_on": "2026-10-01",
        "source_url": "https://mifp.eu",
        "assets_dir": public_pdf_app.config["ASSETS_DIR"],
        "static_dir": public_pdf_app.static_folder,
    }
    with pytest.raises(ValueError, match="too large"):
        render_institutional_pdf(content_html="x" * (MAX_PDF_HTML_CHARS + 1), **arguments)
    with pytest.raises(ValueError, match="nested too deeply"):
        render_institutional_pdf(content_html="<div>" * 101 + "text" + "</div>" * 101, **arguments)


@pytest.mark.parametrize(
    "route,filename,title",
    [
        ("/pdf/about", "about.pdf", "About MIFP"),
        ("/pdf/manifesto", "manifesto.pdf", "Manifesto of Solidarity"),
        ("/pdf/privacy", "privacy.pdf", "Privacy Policy"),
        ("/pdf/code-of-conduct", "code-of-conduct.pdf", "Code of Conduct"),
        ("/pdf/cookie-policy", "cookie-policy.pdf", "Cookie Policy"),
        ("/pdf/sponsors-how-to", "sponsors-how-to.pdf", "How to Become a Sponsor"),
        ("/pdf/research", "research.pdf", "Research Areas"),
    ],
)
def test_all_public_pdf_routes_use_the_institutional_renderer(
    public_pdf_app, route: str, filename: str, title: str
) -> None:
    response = public_pdf_app.test_client().get(route, base_url="https://mifp.eu")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == f'attachment; filename="{filename}"'
    assert response.data.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(response.data))
    assert reader.metadata.title == f"{title} — MIFP"
    text = _text(response.data)
    assert "MIFP" in text
    assert title in text
    assert "Source: https://mifp.eu" in text
    assert "Mediterranean Institute of Fundamental Physics — https://mifp.eu" in text

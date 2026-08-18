from __future__ import annotations

from scrape_remote import (
    canonical_old_url,
    extract_aruba_links_and_assets as extract_links_and_assets,
    extract_structured_page,
    soup_from_aruba as soup_from,
)


def test_aruba_canonicalizes_urls_and_dedupes_assets():
    html = """
    <html><body>
      <nav>MIFP Events Sponsors To Top</nav>
      <main>
        <h1>Useful Page</h1>
        <p>Real scientific content with enough words to be useful and not just navigation.</p>
        <img src="https://www.old.mifp.eu/files/banner.png?cache=1" alt="Banner">
        <img src="/files/banner.png#again" alt="Banner">
        <a href="/docs/paper.pdf?download=1">Download paper</a>
        <a href="/docs/paper.pdf#copy">Duplicate paper</a>
        <a href="/contacts?utm=1">Contact</a>
      </main>
      <footer>To Top Copyright</footer>
    </body></html>
    """
    soup = soup_from(html)
    links, assets = extract_links_and_assets(soup, "https://www.old.mifp.eu/page/?utm=1#x")
    assert canonical_old_url("https://www.old.mifp.eu/page/?utm=1#x") == "https://old.mifp.eu/page"
    assert len([a for a in assets if a["kind"] == "image"]) == 1
    assert len([a for a in assets if a["kind"] == "pdf"]) == 1
    assert links[0]["url"] == "https://old.mifp.eu/contacts"


def test_aruba_structured_page_removes_noise_and_reports_warning():
    html = "<html><body><nav>Members Sponsors Contacts To Top</nav><main><h1>Title</h1><p>Short.</p></main></body></html>"
    data = extract_structured_page(soup_from(html), "https://old.mifp.eu/test?x=1")
    assert "To Top" not in data["clean_text"]
    assert data["canonical_url"] == "https://old.mifp.eu/test"
    assert "content_too_short" in data["warnings"]

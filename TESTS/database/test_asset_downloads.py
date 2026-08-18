from __future__ import annotations

from build_database_pkg import assets


class DummyResponse:
    def __init__(self, *, status_code: int, content: bytes, url: str, content_type: str):
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class DummySession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_download_asset_does_not_retry_404(monkeypatch, caplog):
    session = DummySession([
        DummyResponse(
            status_code=404,
            content=b"<html>missing</html>",
            url="https://old.mifp.eu/missing.jpg",
            content_type="text/html; charset=UTF-8",
        )
    ])
    monkeypatch.setattr(assets, "_get_session", lambda: session)

    data = assets._download_asset("https://www.mifp.eu/missing.jpg", retries=3, delay=0)

    assert data is None
    assert session.calls == 1
    assert "Unavailable asset" in caplog.text


def test_download_asset_does_not_retry_html_payload(monkeypatch, caplog):
    session = DummySession([
        DummyResponse(
            status_code=200,
            content=b"<!doctype html><html>not an image</html>",
            url="https://old.mifp.eu/not-image.jpg",
            content_type="text/html",
        )
    ])
    monkeypatch.setattr(assets, "_get_session", lambda: session)

    data = assets._download_asset("https://www.mifp.eu/not-image.jpg", retries=3, delay=0)

    assert data is None
    assert session.calls == 1
    assert "HTML response instead of asset" in caplog.text

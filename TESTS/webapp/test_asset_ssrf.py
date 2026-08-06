from __future__ import annotations

from email.message import Message

import pytest
from urllib.request import Request


def test_validate_external_asset_url_blocks_loopback_host():
    from mifp_app.services.assets import validate_external_asset_url

    for url in ("http://127.0.0.1/x.jpg", "http://[::1]/x.jpg", "http://localhost/x.jpg"):
        with pytest.raises(ValueError):
            validate_external_asset_url(url)


def test_validate_external_asset_url_rejects_credentials():
    from mifp_app.services.assets import validate_external_asset_url

    with pytest.raises(ValueError):
        validate_external_asset_url("https://user:pass@example.com/a.jpg")


def test_resolve_allowed_ip_raises_when_any_record_is_blocked(monkeypatch):
    from mifp_app.services import assets

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (2, 1, 6, "", ("93.184.216.34", port)),
            (2, 1, 6, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(assets.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError):
        assets._validate_and_resolve("https://example.com/x.jpg", resolve_dns=True)


def test_resolve_allowed_ip_returns_first_allowed_address(monkeypatch):
    from mifp_app.services import assets

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(assets.socket, "getaddrinfo", fake_getaddrinfo)

    validated, pinned_ip = assets._validate_and_resolve("https://example.com/x.jpg", resolve_dns=True)
    assert validated == "https://example.com/x.jpg"
    assert pinned_ip == "93.184.216.34"


def test_resolve_allowed_ip_raises_on_unresolvable(monkeypatch):
    from mifp_app.services import assets

    monkeypatch.setattr(
        assets.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: (_ for _ in ()).throw(socket_gaierror()),
    )

    with pytest.raises(ValueError):
        assets._validate_and_resolve("https://nope.example/x.jpg", resolve_dns=True)


def socket_gaierror():
    import socket

    return socket.gaierror(-2, "Name or service not known")


def test_download_connects_to_validated_ip(monkeypatch, tmp_path):
    """The fetch must connect to the exact IP validated, not re-resolve."""
    from mifp_app.services import assets

    captured: dict = {}

    class FakeResponse:
        headers = {"Content-Type": "image/jpeg"}

        def read(self, n: int = -1) -> bytes:
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_validate_and_resolve(url: str, *, resolve_dns: bool = False):
        return (url, "93.184.216.34")

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(assets, "_validate_and_resolve", fake_validate_and_resolve)
    monkeypatch.setattr(assets, "urlopen", fake_open)
    monkeypatch.setattr(assets, "validate_asset_file", lambda *a, **k: None)

    assets._download_with_retries(
        "https://example.com/photo.jpg",
        timeout=5.0,
        max_bytes=1024 * 1024,
    )

    assert captured["request"]._mifp_pinned_ip == "93.184.216.34"


def test_redirect_to_blocked_target_is_not_followed(monkeypatch):
    from mifp_app.services import assets

    def fake_validate_and_resolve(url: str, *, resolve_dns: bool = False):
        raise ValueError("Remote asset host resolves to a blocked address: 127.0.0.1")

    monkeypatch.setattr(assets, "_validate_and_resolve", fake_validate_and_resolve)

    handler = assets._SecureRedirectHandler()
    req = Request("https://example.com/a.pdf")
    headers = Message()
    headers["Location"] = "http://127.0.0.1/evil.pdf"

    newreq = handler.redirect_request(req, None, 302, "Found", headers, "http://127.0.0.1/evil.pdf")

    assert newreq is None


def test_redirect_to_allowed_target_is_pinned(monkeypatch):
    from mifp_app.services import assets

    def fake_validate_and_resolve(url: str, *, resolve_dns: bool = False):
        return (url, "93.184.216.34")

    monkeypatch.setattr(assets, "_validate_and_resolve", fake_validate_and_resolve)

    handler = assets._SecureRedirectHandler()
    req = Request("https://example.com/a.pdf")
    headers = Message()
    headers["Location"] = "https://cdn.example.com/b.pdf"

    newreq = handler.redirect_request(req, None, 301, "Moved Permanently", headers, "https://cdn.example.com/b.pdf")

    assert newreq is not None
    assert newreq._mifp_pinned_ip == "93.184.216.34"

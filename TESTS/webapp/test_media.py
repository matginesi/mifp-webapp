from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def app(tmp_path):
    import os
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "hello.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["ASSETS_DIR"] = str(assets_dir)
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_media_path_traversal_is_rejected(client):
    resp = client.get("/media/foo/../../etc/passwd")
    assert resp.status_code == 400


def test_media_double_dot_prefix_is_rejected(client):
    resp = client.get("/media/..%2Fetc/passwd")
    assert resp.status_code == 400


def test_media_valid_file_is_served(client, app):
    (app.config["ASSETS_DIR"] / "hello.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    resp = client.get("/media/hello.png")
    assert resp.status_code == 200
    assert resp.data.startswith(b"\x89PNG")
    assert "attachment" not in resp.headers.get("Content-Disposition", "")


def test_media_svg_is_served_as_attachment(client, app):
    (app.config["ASSETS_DIR"] / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    resp = client.get("/media/logo.svg")
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"] == "attachment"


def test_media_svg_uppercase_extension_is_served_as_attachment(client, app):
    (app.config["ASSETS_DIR"] / "logo.SVG").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    resp = client.get("/media/logo.SVG")
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"] == "attachment"


def test_media_nonexistent_file_returns_404(client):
    resp = client.get("/media/nonexistent.png")
    assert resp.status_code == 404
    assert resp.headers["Cache-Control"] == "no-store, max-age=0"

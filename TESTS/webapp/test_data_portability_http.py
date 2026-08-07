from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def app_with_admin(tmp_path):
    import os
    from werkzeug.security import generate_password_hash
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-pass")
    yield app


def _login(client):
    return client.post("/login", data={
        "login_username": "admin",
        "login_password": "test-pass",
    })


class TestDataPortabilityHTTP:
    def _export_post(self, client, fmt: str) -> tuple[list[dict], dict | None]:
        """POST export, parse NDJSON events, return (events, last_event_with_token)."""
        resp = client.post(f"/dashboard/data-portability/export/{fmt}", data={"_csrf_token": "x"})
        assert resp.status_code == 200
        assert resp.content_type == "application/x-ndjson"
        text = resp.data.decode("utf-8")
        lines = [l for l in text.strip().splitlines() if l.strip()]
        events = [json.loads(l) for l in lines]
        result = None
        download_token = None
        for ev in events:
            if ev.get("event") == "result":
                result = ev
                download_token = ev.get("download_token")
        return events, download_token, result

    def test_export_jsonl(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            events, token, result = self._export_post(client, "jsonl")
            assert len(events) >= 2
            assert events[0]["event"] == "phase"
            assert result is not None
            assert result["ok"] is True
            assert result["filename"].endswith(".jsonl")
            # Verify we can download the exported file
            resp = client.get(f"/dashboard/data-portability/export-dl/{token}")
            assert resp.status_code == 200
            assert "x-ndjson" in resp.content_type or resp.content_type == "application/jsonl"
            text = resp.data.decode("utf-8")
            lines = [l for l in text.strip().splitlines() if l.strip()]
            if lines:
                obj = json.loads(lines[0])
                assert "type" in obj

    def test_export_zip(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            events, token, result = self._export_post(client, "zip")
            assert result is not None
            assert result["ok"] is True
            assert result["filename"].endswith(".zip")
            resp = client.get(f"/dashboard/data-portability/export-dl/{token}")
            assert resp.status_code == 200
            assert resp.content_type == "application/zip"
            with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
                names = zf.namelist()
                assert "manifest.json" in names
                assert "records.jsonl" in names
                # Full portable export carries durable state for lossless re-import.
                assert "state.json" in names
                manifest = json.loads(zf.read("manifest.json"))
                assert manifest["format"] == "mifp-export"
                assert manifest["scope"] == "all"

    def test_export_dl_invalid_token(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.get("/dashboard/data-portability/export-dl/invalid-token")
            assert resp.status_code == 404

    def test_export_invalid_format(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post("/dashboard/data-portability/export/bogus", data={"_csrf_token": "x"})
            assert resp.status_code == 400

    def test_import_jsonl_roundtrip(self, app_with_admin):
        records = [
            {"type": "event", "data": {"title": "HTTP Test Event", "slug": "http-test-event", "start_date": "2026-01-01"}, "links": [], "assets": [], "meta": {}}
        ]
        jsonl_content = "\n".join(json.dumps(r) for r in records) + "\n"
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "data_file": (io.BytesIO(jsonl_content.encode("utf-8")), "test.jsonl"),
                    "dry_run": "0",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200

    def test_import_zip_roundtrip(self, app_with_admin):
        manifest = {"scope": "news", "records": 0, "files": []}
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("records.jsonl", "")
        zip_buf.seek(0)
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "data_file": (zip_buf, "test.zip"),
                    "dry_run": "1",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200

    def test_routes_require_auth(self):
        """Anon requests redirect to login."""
        routes = [
            ("POST", "/dashboard/data-portability/export/jsonl"),
            ("GET", "/dashboard/data-portability/export-dl/some-token"),
        ]
        import tempfile, os
        from werkzeug.security import generate_password_hash
        os.environ["TESTING"] = "1"
        os.environ["DATABASE_PATH"] = ":memory:"
        os.environ["LOG_DIR"] = str(tempfile.mkdtemp())
        os.environ["SECRET_KEY"] = "test"
        os.environ["LOG_ACCESS_ENABLED"] = "0"
        from mifp_app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["ADMIN_USERNAME"] = "admin"
        app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-pass")
        with app.test_client() as client:
            for method, route in routes:
                resp = client.open(route, method=method)
                assert resp.status_code in (302, 401), f"{method} {route} returned {resp.status_code}"

    def test_import_xhr_streams_ndjson(self, app_with_admin):
        records = [
            {"type": "event", "data": {"title": "XHR Event", "slug": "xhr-stream-event", "start_date": "2026-06-01"}, "links": [], "assets": [], "meta": {}}
        ]
        jsonl_content = "\n".join(json.dumps(r) for r in records) + "\n"
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "data_file": (io.BytesIO(jsonl_content.encode("utf-8")), "test.jsonl"),
                    "dry_run": "0",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 200
            assert resp.content_type == "application/x-ndjson"
            text = resp.data.decode("utf-8")
            events = [json.loads(l) for l in text.strip().splitlines() if l.strip()]
            event_types = [e.get("event") for e in events]
            assert "phase" in event_types, f"missing phase event in {event_types}"
            assert "result" in event_types, f"missing result event in {event_types}"
            result = next(e for e in events if e.get("event") == "result")
            assert "ok" in result
            assert "inserted" in result
            assert "by_type" in result

    def test_import_xhr_dry_run_returns_no_inserts(self, app_with_admin):
        records = [
            {"type": "event", "data": {"title": "XHR Dry Run", "slug": "xhr-dry-run", "start_date": "2026-07-01"}, "links": [], "assets": [], "meta": {}}
        ]
        jsonl_content = "\n".join(json.dumps(r) for r in records) + "\n"
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "data_file": (io.BytesIO(jsonl_content.encode("utf-8")), "test.jsonl"),
                    "dry_run": "1",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 200
            text = resp.data.decode("utf-8")
            events = [json.loads(l) for l in text.strip().splitlines() if l.strip()]
            result = next(e for e in events if e.get("event") == "result")
            assert result.get("dry_run") is True, "dry_run flag not preserved in result"

    def test_import_xhr_result_contains_by_type(self, app_with_admin):
        records = [
            {"type": "news", "data": {"title": "XHR News", "slug": "xhr-news-item", "body": "Test body"}},
            {"type": "event", "data": {"title": "XHR Event", "slug": "xhr-event-item", "start_date": "2026-08-01"}},
        ]
        jsonl_content = "\n".join(json.dumps(r) for r in records) + "\n"
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "data_file": (io.BytesIO(jsonl_content.encode("utf-8")), "multi.jsonl"),
                    "dry_run": "0",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 200
            text = resp.data.decode("utf-8")
            events = [json.loads(l) for l in text.strip().splitlines() if l.strip()]
            result = next(e for e in events if e.get("event") == "result")
            by_type = result.get("by_type", {})
            assert isinstance(by_type, dict), f"by_type should be dict, got {type(by_type)}"
            assert "events" in by_type or "news" in by_type or len(by_type) > 0

    def test_export_jsonl_import_roundtrip_faithful(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            events, token, result = self._export_post(client, "jsonl")
            assert token is not None
            dl = client.get(f"/dashboard/data-portability/export-dl/{token}")
            assert dl.status_code == 200
            jsonl_bytes = dl.data
            exported = [json.loads(l) for l in jsonl_bytes.decode("utf-8").strip().splitlines() if l.strip()]
            exported_types = {}
            for rec in exported:
                exported_types[rec["type"]] = exported_types.get(rec["type"], 0) + 1

            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "data_file": (io.BytesIO(jsonl_bytes), "roundtrip.jsonl"),
                    "dry_run": "0",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 200
            assert resp.content_type == "application/x-ndjson"
            text = resp.data.decode("utf-8")
            events = [json.loads(l) for l in text.strip().splitlines() if l.strip()]
            res = next(e for e in events if e.get("event") == "result")
            assert res["ok"] is True
            assert res["errors"] == 0

    def test_export_http_error_streams_ndjson_error(self, app_with_admin, monkeypatch):
        import mifp_app.routes.dashboard as dashboard_routes

        def boom(*args, **kwargs):
            raise RuntimeError("simulated export failure")

        monkeypatch.setattr(dashboard_routes, "bundle_to_zip", boom)
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post("/dashboard/data-portability/export/zip", data={"_csrf_token": "x"})
            assert resp.status_code == 500
            assert resp.content_type == "application/x-ndjson"
            lines = [l for l in resp.data.decode("utf-8").strip().splitlines() if l.strip()]
            events = [json.loads(l) for l in lines]
            errors = [e for e in events if e.get("event") == "error"]
            assert errors, "expected at least one 'error' NDJSON event"
            assert errors[0]["ok"] is False

from __future__ import annotations

import io
import json
import logging
import sqlite3
import zipfile
from pathlib import Path

import pytest


class _RecordCollector(logging.Handler):
    """Capture a logger directly, independent of pytest/root propagation."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def app_with_admin(tmp_path):
    import os
    from werkzeug.security import generate_password_hash
    os.environ["TESTING"] = "1"
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")
    os.environ["EXPORT_DIR"] = str(tmp_path / "exports")
    os.environ["ASSETS_DIR"] = str(tmp_path / "assets")
    os.environ["SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["LOG_ACCESS_ENABLED"] = "0"
    from mifp_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-pass")
    app.config["EXPORT_DIR"] = tmp_path / "exports"
    app.config["ASSETS_DIR"] = tmp_path / "assets"
    app.config["EXPORT_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["ASSETS_DIR"].mkdir(parents=True, exist_ok=True)
    yield app


def _login(client):
    return client.post("/login", data={
        "login_username": "admin",
        "login_password": "test-pass",
    })


class TestDataPortabilityHTTP:
    def _export_post(self, client, fmt: str) -> tuple[list[dict], dict | None]:
        """POST export, parse NDJSON events, return (events, last_event_with_token)."""
        resp = client.post(
            f"/dashboard/data-portability/export/{fmt}",
            data={"_csrf_token": "x", "password": "test-pass"},
        )
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

    def test_llm_import_guide_is_downloadable_and_matches_importer_contract(self, app_with_admin):
        from mifp_app.services.importers import DATA_FIELDS, REQUIRED_FIELDS

        with app_with_admin.test_client() as client:
            denied = client.get("/dashboard/data-portability/import-guide.md")
            assert denied.status_code == 302

            _login(client)
            page = client.get("/dashboard/data-portability")
            assert page.status_code == 200
            assert b"LLM import guide" in page.data
            assert b"/dashboard/data-portability/import-guide.md" in page.data

            response = client.get("/dashboard/data-portability/import-guide.md")

        assert response.status_code == 200
        assert response.mimetype == "text/markdown"
        assert response.headers["Content-Disposition"] == (
            'attachment; filename="MIFP_LLM_IMPORT_GUIDE.md"'
        )
        assert response.headers["Cache-Control"] == "no-store, max-age=0"
        guide = response.get_data(as_text=True)
        assert "one JSON object per line" in guide
        assert "Never merge two news items only because" in guide
        assert "Agents should not generate `state.json`" in guide
        assert "Validate only" in guide
        for typ, fields in DATA_FIELDS.items():
            assert f"### `{typ}`" in guide
            for field in fields:
                assert f"| `{field}` |" in guide
            for required in REQUIRED_FIELDS[typ]:
                assert f"| `{required}` | yes |" in guide

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
                envelope = obj.get("_mifp") if isinstance(obj, dict) else None
                assert isinstance(envelope, dict)
                assert envelope.get("kind") == "manifest"
                assert (envelope.get("data") or {}).get("format") == "mifp-jsonl-v2"

    def test_export_uses_explicit_user_download_control(self, app_with_admin):
        """The browser must not rely on an async synthetic click, which can
        be blocked and consume the one-time download token invisibly."""
        with app_with_admin.test_client() as client:
            _login(client)
            page = client.get("/dashboard/data-portability")
            assert page.status_code == 200
            assert b'id="transferDownload"' in page.data
            assert b"Download export" in page.data
            assert b'id="exportAuthModal"' in page.data
            assert b'id="exportAuthPassword"' in page.data
            assert b'autocomplete="current-password"' in page.data
            assert b"Verify and export" in page.data

        script_path = Path(app_with_admin.static_folder) / "js/dashboard/data-portability.js"
        script = script_path.read_text(encoding="utf-8")
        assert "downloadButton.href = config.exportDlUrl.replace" in script
        assert "anchor.click()" not in script
        assert "startAuthorizedExport(fmt, password)" in script
        assert "&password=" in script

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
                assert manifest["format"] == "mifp-jsonl-v2"
                assert manifest["scope"] == "all"

    def test_export_dl_invalid_token(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.get("/dashboard/data-portability/export-dl/invalid-token")
            assert resp.status_code == 404

    def test_export_requires_password_before_creating_any_file(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            missing = client.post(
                "/dashboard/data-portability/export/zip",
                data={"_csrf_token": "x"},
            )
            wrong = client.post(
                "/dashboard/data-portability/export/zip",
                data={"_csrf_token": "x", "password": "wrong-password"},
            )

        assert missing.status_code == 403
        assert wrong.status_code == 403
        assert b"No export was created" in missing.data
        assert b"No export was created" in wrong.data
        assert not list(Path(app_with_admin.config["EXPORT_DIR"]).glob(".portability-*"))

    def test_export_download_token_is_bound_to_login_session(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            _events, token, _result = self._export_post(client, "jsonl")
            with client.session_transaction() as browser_session:
                browser_session["_csrf_token"] = "a-different-login-session-token"
            rejected = client.get(f"/dashboard/data-portability/export-dl/{token}")

        assert rejected.status_code == 404

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
                    "password": "test-pass",
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
                    "password": "test-pass",
                    "data_file": (zip_buf, "test.zip"),
                    "dry_run": "1",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200

    def test_import_xhr_accepts_multiple_zips(self, app_with_admin):
        def package() -> io.BytesIO:
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({"scope": "all", "records": 0, "files": []}))
                archive.writestr("records.jsonl", "")
            payload.seek(0)
            return payload

        with app_with_admin.test_client() as client:
            _login(client)
            response = client.post(
                "/dashboard/data-portability/import",
                data={
                    "password": "test-pass",
                    "data_file": [(package(), "first.zip"), (package(), "second.zip")],
                    "dry_run": "1",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        assert response.status_code == 200
        assert response.content_type == "application/x-ndjson"
        messages = [json.loads(line) for line in response.data.splitlines() if line]
        result = messages[-1]
        assert result["event"] == "result"
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert [m["file"] for m in messages if m.get("event") == "file_start"] == [
            "first.zip", "second.zip"
        ]

    def test_import_requires_password_before_processing_files(self, app_with_admin):
        record = {
            "type": "news",
            "data": {"title": "Must not import", "slug": "must-not-import"},
            "links": [], "assets": [],
        }
        collector = _RecordCollector()
        audit_logger = logging.getLogger("mifp.audit")
        audit_logger.addHandler(collector)
        try:
            with app_with_admin.test_client() as client:
                _login(client)
                response = client.post(
                    "/dashboard/data-portability/import",
                    data={
                        "password": "wrong-password",
                        "data_file": (
                            io.BytesIO((json.dumps(record) + "\n").encode()),
                            "blocked.jsonl",
                        ),
                    },
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
        finally:
            audit_logger.removeHandler(collector)

        assert response.status_code == 403
        assert response.content_type == "application/x-ndjson"
        result = json.loads(response.data)
        assert result["outcome"] == "authorization_denied"
        assert result["ok"] is False
        assert "No file was processed" in result["message"]
        assert any(
            "portable import password verification failed" in record.getMessage()
            for record in collector.records
        )
        with sqlite3.connect(app_with_admin.config["DATABASE_PATH"]) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM news WHERE slug='must-not-import'"
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM import_runs WHERE name='blocked.jsonl'"
            ).fetchone()[0] == 0

    def test_import_password_failures_are_rate_limited(self, app_with_admin):
        from mifp_app.utils.security import reset_rate_limits

        reset_rate_limits(
            "portable_import_password_failure",
            db_path=str(app_with_admin.config["DATABASE_PATH"]),
        )
        with app_with_admin.test_client() as client:
            _login(client)
            responses = [
                client.post(
                    "/dashboard/data-portability/import",
                    data={"password": "wrong-password"},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                for _ in range(6)
            ]

        assert [response.status_code for response in responses[:5]] == [403] * 5
        assert responses[5].status_code == 429
        assert "Too many failed attempts" in responses[5].get_data(as_text=True)

    def test_oversized_import_returns_actionable_json_and_is_logged(self, app_with_admin):
        app_with_admin.config["MAX_CONTENT_LENGTH"] = 256
        collector = _RecordCollector()
        app_with_admin.logger.addHandler(collector)
        try:
            with app_with_admin.test_client() as client:
                _login(client)
                response = client.post(
                    "/dashboard/data-portability/import",
                    data={"password": "test-pass", "data_file": (io.BytesIO(b"x" * 2048), "large.jsonl")},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
        finally:
            app_with_admin.logger.removeHandler(collector)

        assert response.status_code == 413
        payload = response.get_json()
        assert payload["error"] == "file_too_large"
        assert "upload large packages sequentially" in payload["message"]
        assert payload["request_id"]
        assert any("payload too large" in record.getMessage() for record in collector.records)

    def test_import_page_exposes_upload_limits_to_preflight_selection(self, app_with_admin):
        with app_with_admin.test_client() as client:
            _login(client)
            response = client.get("/dashboard/data-portability")

        assert response.status_code == 200
        assert b'"maxUploadBytes"' in response.data
        assert b'"maxZipBytes"' in response.data
        assert b'"maxZipFiles"' not in response.data
        assert b'id="transferSelectionError"' in response.data
        assert b'id="transferBatchNotice"' in response.data
        assert b'id="importAuthModal"' in response.data
        assert b'id="importAuthPassword"' in response.data
        assert b'autocomplete="current-password"' in response.data

    def test_routes_require_auth(self):
        """Anon requests redirect to login."""
        routes = [
            ("POST", "/dashboard/data-portability/export/jsonl"),
            ("POST", "/dashboard/data-portability/import"),
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
                    "password": "test-pass",
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
                    "password": "test-pass",
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
                    "password": "test-pass",
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
            manifests = [
                row.get("_mifp") for row in exported
                if isinstance(row, dict) and isinstance(row.get("_mifp"), dict)
                and row["_mifp"].get("kind") == "manifest"
            ]
            assert manifests
            assert (manifests[0].get("data") or {}).get("format") == "mifp-jsonl-v2"
            exported_types = {}
            for rec in exported:
                if not isinstance(rec, dict) or "type" not in rec:
                    continue
                exported_types[rec["type"]] = exported_types.get(rec["type"], 0) + 1

            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "password": "test-pass",
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

        monkeypatch.setattr(dashboard_routes, "bundle_to_zip_file", boom)
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/export/zip",
                data={"_csrf_token": "x", "password": "test-pass"},
            )
            assert resp.status_code == 500
            assert resp.content_type == "application/x-ndjson"
            lines = [l for l in resp.data.decode("utf-8").strip().splitlines() if l.strip()]
            events = [json.loads(l) for l in lines]
            errors = [e for e in events if e.get("event") == "error"]
            assert errors, "expected at least one 'error' NDJSON event"
            assert errors[0]["ok"] is False

    def test_import_integrity_error_identifies_file_and_confirms_rollback(
        self, app_with_admin, monkeypatch
    ):
        import mifp_app.routes.dashboard as dashboard_routes

        def reject_archive(*args, **kwargs):
            raise ValueError("state.json does not match the checksum in manifest.json")

        monkeypatch.setattr(dashboard_routes, "_import_zip_dispatch", reject_archive)
        with app_with_admin.test_client() as client:
            _login(client)
            resp = client.post(
                "/dashboard/data-portability/import",
                data={
                    "password": "test-pass",
                    "scope": "all",
                    "data_file": (io.BytesIO(b"not-read-by-test"), "backup 2026.zip"),
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

            assert resp.status_code == 200
            events = [
                json.loads(line)
                for line in resp.data.decode("utf-8").splitlines()
                if line.strip()
            ]
            result = next(event for event in events if event.get("event") == "result")
            assert result["ok"] is False
            assert "backup2026.zip" in result["message"]
            assert "No database changes from this failed batch were committed" in result["message"]

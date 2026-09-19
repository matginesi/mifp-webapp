from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from mifp_app.services.event_import import (
    apply_import,
    destination_url,
    inspect_packages,
    inspect_website,
    normalize_destination,
)
from mifp_app.config import Config


def _website(*, root: str = "PLMCN-2027", yaml_text: str | None = None,
             extra: dict[str, bytes] | None = None) -> bytes:
    yaml_text = yaml_text or """\
template:
  version: 0.4.2
site:
  title: PLMCN 2027
  short_name: PLMCN-2027
  base_url: https://events.mifp.eu/PLMCN-2027/
conference:
  full_name: PLMCN 2027
  acronym: PLMCN
  start_date: '2027-09-20'
  end_date: '2027-09-24'
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{root}/index.html", "<!doctype html><title>PLMCN</title>")
        archive.writestr(f"{root}/conference.yaml", yaml_text)
        archive.writestr(f"{root}/conference.version.json", json.dumps({"version": "0.4.2"}))
        archive.writestr(f"{root}/regform/index.php", "<?php echo 'ok';")
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return output.getvalue()


def _info(*, slug: str = "plmcn-2027", uid: str = "event_plmcn_2027",
          start: str = "2027-09-20", asset: bool = True,
          bad_hash: bool = False, omit_asset: bool = False) -> bytes:
    asset_bytes = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0h1v1z"/></svg>'
    assets = [{
        "path": "logo.svg", "role": "logo", "kind": "image",
        "storage_status": "packaged",
    }] if asset else []
    record = {
        "type": "event",
        "data": {
            "uid": uid, "slug": slug, "title": "PLMCN 2027",
            "start_date": start, "end_date": "2027-09-24",
            "review_status": "review", "event_type": "conference",
            "remote_url": "https://events.mifp.eu/PLMCN-2027/",
        },
        "links": [{"url": "https://events.mifp.eu/PLMCN-2027/", "role": "primary", "is_primary": True}],
        "assets": assets,
    }
    records = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    files = []
    if asset:
        files.append({
            "archive_path": "assets/logo.svg", "size": len(asset_bytes),
            "sha256": "0" * 64 if bad_hash else hashlib.sha256(asset_bytes).hexdigest(),
        })
    manifest = {
        "format": "mifp-content", "format_version": 1, "scope": "all",
        "records": 1, "counts": {"event": 1},
        "records_sha256": hashlib.sha256(records).hexdigest(), "files": files,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("records.jsonl", records)
        if asset and not omit_asset:
            archive.writestr("assets/logo.svg", asset_bytes)
    return output.getvalue()


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update({
        "TESTING": "1", "DATABASE_PATH": str(tmp_path / "mifp.db"),
        "ASSETS_DIR": str(tmp_path / "assets"), "EXPORT_DIR": str(tmp_path / "exports"),
        "CONFERENCES_DIR": str(tmp_path / "conferences"), "EVENTS_ROOT": str(tmp_path / "events"),
        "TMPDIR": str(tmp_path / "tmp"), "LOG_DIR": str(tmp_path / "logs"),
        "EVENTS_DOMAIN": "events.vpsbox.home.arpa", "SECRET_KEY": "event-import-test-secret",
        "LOG_ACCESS_ENABLED": "0",
    })
    from mifp_app import create_app
    from mifp_app.db.manage import init_database

    app = create_app()
    app.config.update(
        TESTING=True, WTF_CSRF_ENABLED=False, DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets", EXPORT_DIR=tmp_path / "exports",
        CONFERENCES_DIR=tmp_path / "conferences", EVENTS_ROOT=tmp_path / "events",
        TMP_DIR=tmp_path / "tmp", LOG_DIR=tmp_path / "logs",
        EVENTS_DOMAIN="events.vpsbox.home.arpa", ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "CONFERENCES_DIR", "EVENTS_ROOT", "TMP_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    init_database(Path(app.config["DATABASE_PATH"]))
    return app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "event-import-csrf"
    return client


def _conn(app):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def test_plmcn_reference_packages_validate_and_combined_import(app, client):
    response = client.post(
        "/dashboard/conferences/import/validate",
        data={
            # Intentionally swapped: role detection must use content.
            "website_package": (io.BytesIO(_info()), "PLMCN-2027_INFO.zip"),
            "info_package": (io.BytesIO(_website()), "PLMCN-2027_WEBSITE.zip"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "SHA-256 verified" in body
    assert "PHP files detected: 1" in body
    assert "events.vpsbox.home.arpa/PLMCN-2027/" in body
    token = body.split('name="token" value="', 1)[1].split('"', 1)[0]

    imported = client.post(
        "/dashboard/conferences/import/apply",
        data={
            "token": token, "destination": "PLMCN-2027",
            "existing_mode": "reject", "keep_rollback": "1",
            "forthcoming": "1", "accept_warnings": "1",
        },
    )
    assert imported.status_code == 302
    assert (Path(app.config["EVENTS_ROOT"]) / "PLMCN-2027/index.html").is_file()
    assert not (Path(app.config["EVENTS_ROOT"]) / "OTHER/index.html").exists()
    with _conn(app) as conn:
        event = conn.execute("SELECT * FROM events WHERE uid='event_plmcn_2027'").fetchone()
        assert event is not None
        assert event["is_featured"] == 1
        assert event["review_status"] == "review"
        site = conn.execute("SELECT * FROM conference_sites").fetchone()
        assert site["public_path"] == "PLMCN-2027"
        assert json.loads(site["package_manifest_json"])["php_execution"] == "disabled"
    conference_page = client.get("/dashboard/conferences").get_data(as_text=True)
    assert "Website installed" in conference_page
    assert "Open" in conference_page
    assert "PHP 1 / disabled" in conference_page
    assert "Import WEBSITE / INFO" in conference_page

    # Same UID/slug updates instead of duplicating; existing path needs replace.
    response = client.post(
        "/dashboard/conferences/import/validate",
        data={"website_package": (io.BytesIO(_website()), "web.zip"), "info_package": (io.BytesIO(_info()), "info.zip")},
        content_type="multipart/form-data",
    )
    token = response.get_data(as_text=True).split('name="token" value="', 1)[1].split('"', 1)[0]
    client.post("/dashboard/conferences/import/apply", data={
        "token": token, "destination": "PLMCN-2027",
        "existing_mode": "replace", "keep_rollback": "1",
        "forthcoming": "0", "accept_warnings": "1",
    })
    with _conn(app) as conn:
        row = conn.execute("SELECT * FROM events WHERE uid='event_plmcn_2027'").fetchone()
        assert row is not None
        assert row["is_featured"] == 0


@pytest.mark.parametrize("payload,message", [
    (lambda: _info(bad_hash=True), "integrity"),
    (lambda: _info(omit_asset=True), "missing"),
])
def test_info_integrity_errors_are_rejected(app, tmp_path, payload, message):
    path = tmp_path / "info.zip"
    path.write_bytes(payload())
    with _conn(app) as conn, pytest.raises(ValueError, match=message):
        inspect_packages(conn, None, path)


@pytest.mark.parametrize("extra,match", [
    ({"../escape": b"x"}, "Unsafe ZIP member"),
    ({"PLMCN-2027/.env": b"SECRET=x"}, "prohibited"),
    ({"PLMCN-2027/regform/registrations/person.csv": b"private"}, "registration"),
])
def test_website_rejects_unsafe_and_private_paths(tmp_path, extra, match):
    path = tmp_path / "web.zip"
    path.write_bytes(_website(extra=extra))
    with pytest.raises(ValueError, match=match):
        inspect_website(path, path.name)


def test_website_rejects_symlink_and_malformed_yaml(tmp_path):
    link_zip = tmp_path / "link.zip"
    with zipfile.ZipFile(link_zip, "w") as archive:
        archive.writestr("PLMCN-2027/index.html", "ok")
        archive.writestr("PLMCN-2027/conference.yaml", "site: {}")
        link = zipfile.ZipInfo("PLMCN-2027/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "target")
    with pytest.raises(ValueError, match="link or special"):
        inspect_website(link_zip, link_zip.name)

    malformed = tmp_path / "bad-yaml.zip"
    malformed.write_bytes(_website(yaml_text="site: ["))
    with pytest.raises(ValueError, match="not valid"):
        inspect_website(malformed, malformed.name)


def test_cross_mismatch_destination_and_configured_domains(app, tmp_path):
    website = tmp_path / "web.zip"
    info = tmp_path / "info.zip"
    website.write_bytes(_website())
    info.write_bytes(_info(slug="other-2027", start="2027-09-21"))
    with _conn(app) as conn:
        inspection = inspect_packages(conn, website, info)
    assert any(check.level == "ERROR" for check in inspection.checks)
    with pytest.raises(ValueError):
        normalize_destination("../outside")
    assert destination_url("events.mifp.eu", "PLMCN-2027") == "https://events.mifp.eu/PLMCN-2027/"
    assert destination_url("events.vpsbox.home.arpa", "PLMCN-2027") == "https://events.vpsbox.home.arpa/PLMCN-2027/"


def test_website_limits_detect_oversize_and_compression_bomb(tmp_path, monkeypatch):
    archive = tmp_path / "large.zip"
    archive.write_bytes(_website(extra={"PLMCN-2027/assets/repeated.txt": b"x" * 32_000}))
    monkeypatch.setattr(Config, "EVENT_IMPORT_MAX_UNPACKED_BYTES", 1_000)
    with pytest.raises(ValueError, match="expands beyond"):
        inspect_website(archive, archive.name)

    monkeypatch.setattr(Config, "EVENT_IMPORT_MAX_UNPACKED_BYTES", 100_000)
    monkeypatch.setattr(Config, "EVENT_IMPORT_MAX_COMPRESSION_RATIO", 2)
    with pytest.raises(ValueError, match="compression ratio"):
        inspect_website(archive, archive.name)


def test_existing_destination_rejected_without_replace(app, tmp_path):
    website = tmp_path / "web.zip"
    info = tmp_path / "info.zip"
    website.write_bytes(_website())
    info.write_bytes(_info(asset=False))
    destination = Path(app.config["EVENTS_ROOT"]) / "PLMCN-2027"
    destination.mkdir()
    (destination / "old.txt").write_text("old")
    with _conn(app) as conn:
        inspection = inspect_packages(conn, website, info, assets_dir=app.config["ASSETS_DIR"])
        with pytest.raises(ValueError, match="already exists"):
            apply_import(
                conn, inspection, website, info, events_root=app.config["EVENTS_ROOT"],
                assets_dir=app.config["ASSETS_DIR"], destination="PLMCN-2027",
                publish_website=True, import_metadata=True, replace=False,
                keep_rollback=True, events_domain="events.vpsbox.home.arpa",
            )
    assert (destination / "old.txt").read_text() == "old"


def test_atomic_replace_rolls_back_and_preserves_siblings(app, tmp_path):
    website = tmp_path / "web.zip"
    info = tmp_path / "info.zip"
    website.write_bytes(_website())
    info.write_bytes(_info(asset=False))
    root = Path(app.config["EVENTS_ROOT"])
    (root / "PLMCN-2027").mkdir()
    (root / "PLMCN-2027/old.txt").write_text("old")
    (root / "OTHER").mkdir()
    (root / "OTHER/index.html").write_text("other")
    with _conn(app) as raw:
        inspection = inspect_packages(raw, website, info, assets_dir=app.config["ASSETS_DIR"])

        class FailingCommit:
            def __getattr__(self, name):
                return getattr(raw, name)

            def commit(self):
                raise sqlite3.OperationalError("forced commit failure")

        with pytest.raises(sqlite3.OperationalError, match="forced"):
            apply_import(
                FailingCommit(), inspection, website, info,
                events_root=root, assets_dir=app.config["ASSETS_DIR"],
                destination="PLMCN-2027", publish_website=True,
                import_metadata=True, replace=True, keep_rollback=True,
                events_domain="events.vpsbox.home.arpa",
            )
    assert (root / "PLMCN-2027/old.txt").read_text() == "old"
    assert (root / "OTHER/index.html").read_text() == "other"
    with _conn(app) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_website_allows_registration_guard_scaffold_but_not_runtime_data(tmp_path):
    safe = {
        "PLMCN-2027/regform/registrations/.gitignore": b"registrations.csv\n.secret.php\n",
        "PLMCN-2027/regform/registrations/.htaccess": (
            b"# deny direct access\n<IfModule mod_rewrite.c>\n"
            b"RewriteEngine On\nRewriteRule ^ - [F,L]\n</IfModule>\n"
        ),
        "PLMCN-2027/regform/registrations/index.php": (
            b"<?php\nhttp_response_code(404);\nexit;\n"
        ),
    }
    path = tmp_path / "safe-scaffold.zip"
    path.write_bytes(_website(extra=safe))
    package = inspect_website(path, path.name)
    assert package.root == "PLMCN-2027"

    bad = tmp_path / "runtime-data.zip"
    bad.write_bytes(_website(extra={
        "PLMCN-2027/regform/registrations/registrations.csv": b"name,email\nA,a@example.test\n"
    }))
    with pytest.raises(ValueError, match="private registration data"):
        inspect_website(bad, bad.name)


def test_info_only_creates_event_without_publishing_files_and_asks_forthcoming(app, client):
    response = client.post(
        "/dashboard/conferences/import/validate",
        data={"info_package": (io.BytesIO(_info()), "PLMCN-2027_INFO.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No website files in this import" in body
    assert "Add this Event to the homepage Forthcoming section?" in body
    assert 'name="publish_website"' not in body
    assert 'name="import_metadata"' not in body
    token = body.split('name="token" value="', 1)[1].split('"', 1)[0]

    imported = client.post(
        "/dashboard/conferences/import/apply",
        data={"token": token, "destination": "plmcn-2027", "forthcoming": "1"},
    )
    assert imported.status_code == 302
    assert imported.headers["Location"].endswith("/dashboard/events")
    assert not (Path(app.config["EVENTS_ROOT"]) / "plmcn-2027").exists()
    with _conn(app) as conn:
        event = conn.execute("SELECT * FROM events WHERE uid='event_plmcn_2027'").fetchone()
        assert event is not None
        assert event["is_featured"] == 1
        assert event["review_status"] == "review"
        assert conn.execute("SELECT COUNT(*) FROM conference_sites").fetchone()[0] == 0


def test_info_import_requires_explicit_forthcoming_choice(app, client):
    response = client.post(
        "/dashboard/conferences/import/validate",
        data={"info_package": (io.BytesIO(_info()), "PLMCN-2027_INFO.zip")},
        content_type="multipart/form-data",
    )
    token = response.get_data(as_text=True).split('name="token" value="', 1)[1].split('"', 1)[0]
    imported = client.post(
        "/dashboard/conferences/import/apply",
        data={"token": token, "destination": "plmcn-2027"},
    )
    assert imported.status_code == 302
    with _conn(app) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_website_only_import_is_visible_in_conference_sites(app, client):
    response = client.post(
        "/dashboard/conferences/import/validate",
        data={"website_package": (io.BytesIO(_website()), "PLMCN-2027_WEBSITE.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    token = body.split('name="token" value="', 1)[1].split('"', 1)[0]
    imported = client.post(
        "/dashboard/conferences/import/apply",
        data={
            "token": token, "destination": "PLMCN-2027",
            "existing_mode": "reject", "keep_rollback": "1", "accept_warnings": "1",
        },
    )
    assert imported.status_code == 302
    with _conn(app) as conn:
        site = conn.execute("SELECT * FROM conference_sites WHERE public_path='PLMCN-2027'").fetchone()
        assert site is not None
        assert site["event_id"] is None
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    page = client.get("/dashboard/conferences").get_data(as_text=True)
    assert "Website installed" in page
    assert "Not linked" in page

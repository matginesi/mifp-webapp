from __future__ import annotations

from mifp_app.db.contract import SCHEMA_VERSION
from mifp_app.services.versioning import (
    application_version,
    conference_snapshot,
    contract_versions,
    release_info,
    versioned_manifest,
)


def test_application_version_is_source_controlled_semver():
    assert application_version() == "1.0.0"


def test_version_contracts_expose_current_schema_and_package_versions():
    versions = {item["name"]: item["version"] for item in contract_versions()}
    assert versions["Database schema"] == str(SCHEMA_VERSION)
    assert versions["MIFP content package"] == "1"
    assert versions["Portable dashboard package"] == "2"
    assert versions["Event WEBSITE package"] == "1"


def test_release_info_is_read_only_environment_metadata(monkeypatch):
    digest = "a" * 64
    monkeypatch.setenv("MIFP_RELEASE_REF", f"ghcr.io/matginesi/mifp-webapp@sha256:{digest}")
    monkeypatch.setenv("MIFP_RELEASE_CHANNEL", "ghcr.io/matginesi/mifp-webapp:latest")
    info = release_info()
    assert info["release_digest"] == digest
    assert info["release_channel"].endswith(":latest")


def test_conference_manifest_keeps_only_one_bounded_previous_snapshot():
    original = {
        "source_format": "legacy-static",
        "source_version": "1.0.0",
        "package_schema_version": 1,
        "package_sha256": "1" * 64,
        "package_manifest_json": '{"validation":"passed","versioning":{"previous":{"label":"old"}}}',
        "imported_at": "2026-09-18 10:00:00",
        "published_at": "2026-09-18 10:00:00",
    }
    previous = conference_snapshot(original)
    assert "versioning" not in previous["package_manifest"]
    manifest = versioned_manifest(
        {"validation": "passed"},
        source_version="1.1.0",
        package_sha256="2" * 64,
        previous=previous,
    )
    assert manifest["versioning"]["current"]["label"] == "1.1.0"
    assert manifest["versioning"]["previous"]["label"] == "1.0.0"
    assert "versioning" not in manifest["versioning"]["previous"]["package_manifest"]

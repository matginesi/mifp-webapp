"""The generated public conference site must not publish internal data.

The internal/legacy builder writes a deployable package that is published at
``events.mifp.eu``. Everything inside it is public by definition, so the JSON
artifacts are built from explicit allowlists rather than from whole database
rows. Uploaded Conference Editor packages are a separate contract and are not
touched here.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from mifp_app.services.conference_sites import (
    PEOPLE_COLUMNS,
    PUBLIC_CONFERENCE_FIELDS,
    PUBLIC_PEOPLE_FIELDS,
    build_site_zip,
    conference_config,
    config_from_form,
    config_to_yaml,
    parse_config_yaml,
)
from werkzeug.datastructures import MultiDict


SITE = {
    "id": 7,
    "slug": "qm27",
    "title": "Quantum Matter 2027",
    "acronym": "QM27",
    "year": 2027,
    "status": "ready",
    "start_date": "2027-02-01",
    "end_date": "2027-02-05",
    "venue": "Palazzo",
    "city": "Rome",
    "country": "Italy",
    "canonical_url": "https://events.mifp.eu/QM27/",
    "deploy_base_path": "/QM27/",
    "registration_url": "https://register.example.org/qm27",
    "contact_email": "team@example.org",
    "description": "A focused international meeting.",
    "config_json": json.dumps(
        {
            "deployment": {"environment": "nginx", "nginx_base_path": "/QM27/"},
            # Legacy client-storage settings that must never round-trip.
            "privacy": {"show_notice": True, "notice_storage_key": "qm27-notice"},
            "appearance": {"default_mode": "light", "remember_theme": True},
        }
    ),
    "created_at": "2026-01-01 00:00:00",
    "updated_at": "2026-01-02 00:00:00",
    "event_id": 42,
    "public_path": "QM27",
    "source_format": "conference-editor",
    "source_version": "3.1.4",
    "package_schema_version": 4,
    "package_sha256": "a" * 64,
    "package_manifest_json": '{"files": 12}',
    "deploy_status": "staged",
    "imported_at": "2026-01-01 00:00:00",
    "published_at": None,
}
PEOPLE = [
    {
        "id": 11,
        "name": "Ada Scientist",
        "email": "ada@example.org",
        "affiliation": "MIFP",
        "country": "Italy",
        "role": "speaker",
        "contribution_title": "Quantum networks",
        "bio": "Private notes about Ada.",
        "website_url": "https://example.org/ada",
        "sort_order": 1,
    },
    {
        "id": 12,
        "name": "Grace Example",
        "email": "grace@example.org",
        "affiliation": "INFN",
        "country": "Italy",
        "role": "chair",
        "contribution_title": "",
        "bio": "",
        "website_url": "",
        "sort_order": 2,
    },
]


def _package(tmp_path: Path) -> dict:
    payload = build_site_zip(SITE, PEOPLE, tmp_path / "assets")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".json") or name.endswith(".html") or name.endswith(".yaml")
        }


# ---------------------------------------------------------------------------
# Static shape of the allowlists
# ---------------------------------------------------------------------------

def test_public_people_projection_excludes_private_and_internal_fields():
    assert "email" not in PUBLIC_PEOPLE_FIELDS
    assert "sort_order" not in PUBLIC_PEOPLE_FIELDS
    assert set(PUBLIC_PEOPLE_FIELDS) < set(PEOPLE_COLUMNS)
    assert PUBLIC_PEOPLE_FIELDS == ("name", "affiliation", "role", "contribution_title")


def test_public_conference_projection_excludes_operational_state():
    for internal in (
        "id", "status", "contact_email", "config_json", "created_at", "updated_at",
        "event_id", "public_path", "source_format", "source_version",
        "package_schema_version", "package_sha256", "package_manifest_json",
        "deploy_status", "imported_at", "published_at",
    ):
        assert internal not in PUBLIC_CONFERENCE_FIELDS, internal


# ---------------------------------------------------------------------------
# Generated package
# ---------------------------------------------------------------------------

def test_generated_people_json_has_no_email_or_internal_fields(tmp_path):
    files = _package(tmp_path)
    people = json.loads(files["people.json"])

    assert [sorted(row) for row in people] == [sorted(PUBLIC_PEOPLE_FIELDS)] * len(people)
    blob = files["people.json"]
    for leaked in ("ada@example.org", "grace@example.org", "Private notes about Ada.", "sort_order"):
        assert leaked not in blob, leaked


def test_generated_people_json_keeps_only_public_values(tmp_path):
    people = json.loads(_package(tmp_path)["people.json"])

    assert people[0] == {
        "name": "Ada Scientist",
        "affiliation": "MIFP",
        "role": "speaker",
        "contribution_title": "Quantum networks",
    }
    assert people[1]["name"] == "Grace Example"


def test_generated_conference_json_uses_the_public_allowlist(tmp_path):
    conference = json.loads(_package(tmp_path)["conference.json"])

    assert sorted(conference) == sorted(PUBLIC_CONFERENCE_FIELDS)
    for leaked in ("config_json", "package_sha256", "deploy_status", "source_format", "team@example.org"):
        assert leaked not in json.dumps(conference), leaked
    assert conference["slug"] == "qm27"
    assert conference["city"] == "Rome"


def test_generated_package_does_not_claim_client_storage(tmp_path):
    """The static builder stores nothing in the browser; the notice is gone."""
    files = _package(tmp_path)

    for name, body in files.items():
        assert "privacy-notice" not in body, name
        assert "stores only your theme and privacy-notice preferences" not in body, name
    assert "notice_storage_key" not in files["config.yaml"]
    assert "remember_theme" not in files["config.yaml"]


def test_generated_pages_still_render_people_and_venue(tmp_path):
    files = _package(tmp_path)

    assert "Ada Scientist" in files["people.html"]
    assert "Quantum networks" in files["people.html"]
    assert "Rome" in files["venue.html"]


# ---------------------------------------------------------------------------
# Retired config round-trip
# ---------------------------------------------------------------------------

def test_retired_config_sections_are_dropped_not_persisted():
    config = conference_config(SITE["config_json"])

    assert "privacy" not in config
    assert "remember_theme" not in config["appearance"]
    yaml_text = config_to_yaml(config)
    assert "privacy:" not in yaml_text
    assert "notice_storage_key" not in yaml_text
    assert "remember_theme" not in yaml_text


def test_legacy_package_with_retired_config_still_imports():
    """An older config.yaml that still has the retired keys must keep working."""
    legacy = (
        b"deployment:\n  environment: nginx\n  nginx_base_path: /QM27/\n"
        b"appearance:\n  default_mode: dark\n  remember_theme: true\n"
        b"privacy:\n  show_notice: true\n  notice_storage_key: qm27-notice\n"
    )
    parsed = parse_config_yaml(legacy)

    assert "privacy" not in parsed
    assert "remember_theme" not in parsed["appearance"]
    assert parsed["appearance"]["default_mode"] == "dark"
    assert parsed["deployment"]["nginx_base_path"] == "/QM27"  # trailing slash normalised


def test_legacy_section_with_unknown_key_is_still_rejected():
    broken = b"privacy:\n  show_notice: true\n  tracking_consent: true\n"
    with pytest.raises(ValueError, match="Unsupported config.yaml key"):
        parse_config_yaml(broken)


def test_config_form_cannot_reintroduce_retired_settings():
    """A hand-crafted POST cannot smuggle a retired setting back in."""
    from mifp_app.services.conference_sites import DEFAULT_CONFERENCE_CONFIG

    form: MultiDict = MultiDict()
    for section, defaults in DEFAULT_CONFERENCE_CONFIG.items():
        for key, value in defaults.items():
            if section == "countdown" and key == "items":
                continue
            form.add(f"config__{section}__{key}", "1" if value is True else str(value))
    form.add("config__appearance__remember_theme", "1")
    form.add("config__privacy__show_notice", "1")
    form.add("config__privacy__notice_storage_key", "sneaky")

    config = config_from_form(form, DEFAULT_CONFERENCE_CONFIG)

    assert "privacy" not in config
    assert "remember_theme" not in config["appearance"]
    yaml_text = config_to_yaml(config)
    assert "privacy" not in yaml_text
    assert "remember_theme" not in yaml_text

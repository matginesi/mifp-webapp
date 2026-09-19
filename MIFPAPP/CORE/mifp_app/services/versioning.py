"""Application and package version metadata exposed to operator-facing views.

The application version is source-controlled in ``MIFPAPP/CORE/VERSION``.
Deployment identity stays immutable: production injects the resolved OCI image
reference, while the dashboard only reads it.  Conference website version state
is stored inside the existing package manifest so no runtime schema migration is
required just to expose/restore the immediately previous imported website.
"""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Mapping

from ..db.contract import SCHEMA_VERSION
from .conference_packages import SUPPORTED_EDITOR_SCHEMA
from .portability_contract import (
    CANONICAL_FORMAT,
    CONTENT_FORMAT,
    CONTENT_FORMAT_VERSION,
    PORTABLE_FORMAT_VERSION,
)

APP_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"
EVENT_WEBSITE_FORMAT_VERSION = 1
CONFERENCE_VERSION_STATE_FORMAT = 1
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?$")
_DIGEST_RE = re.compile(r"@sha256:([0-9a-f]{64})$")


def application_version() -> str:
    """Return the source-controlled application version without guessing."""
    try:
        value = APP_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return value if _VERSION_RE.fullmatch(value) else "unknown"


def release_info() -> dict[str, str]:
    """Read immutable deployment identity passed by the host/CI.

    No registry/network access is performed here.  The dashboard is a read-only
    observer of release state; deploy/update/rollback remain host operations.
    """
    release_ref = os.getenv("MIFP_RELEASE_REF", "").strip()
    channel = os.getenv("MIFP_RELEASE_CHANNEL", "").strip()
    commit = os.getenv("MIFP_BUILD_COMMIT", "").strip()
    build_date = os.getenv("MIFP_BUILD_DATE", "").strip()
    digest_match = _DIGEST_RE.search(release_ref)
    return {
        "app_version": application_version(),
        "release_ref": release_ref,
        "release_digest": digest_match.group(1) if digest_match else "",
        "release_channel": channel,
        "build_commit": commit,
        "build_date": build_date,
    }


def contract_versions() -> list[dict[str, str]]:
    """Stable format/schema versions relevant to operators and exports."""
    return [
        {"name": "Database schema", "format": "sqlite", "version": str(SCHEMA_VERSION)},
        {"name": "MIFP content package", "format": CONTENT_FORMAT, "version": str(CONTENT_FORMAT_VERSION)},
        {"name": "Portable dashboard package", "format": CANONICAL_FORMAT, "version": str(PORTABLE_FORMAT_VERSION)},
        {"name": "Event WEBSITE package", "format": "mifp-event-website", "version": str(EVENT_WEBSITE_FORMAT_VERSION)},
        {"name": "Conference Editor package", "format": "conference-editor", "version": str(SUPPORTED_EDITOR_SCHEMA)},
    ]




def _value(mapping: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return mapping[key]
    except (KeyError, IndexError, TypeError):
        return default

def _clean_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned = copy.deepcopy(value)
    cleaned.pop("versioning", None)
    return cleaned


def site_version_label(source_version: Any, sha256: Any) -> str:
    version = str(source_version or "").strip()
    if version:
        return version
    digest = str(sha256 or "").strip().lower()
    return f"sha-{digest[:12]}" if digest else "unversioned"


def conference_snapshot(site: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize the package state needed to restore one previous website."""
    manifest_raw = _value(site, "package_manifest_json")
    if isinstance(manifest_raw, str):
        try:
            import json

            manifest = json.loads(manifest_raw or "{}")
        except (TypeError, ValueError):
            manifest = {}
    elif isinstance(manifest_raw, dict):
        manifest = manifest_raw
    else:
        manifest = {}
    return {
        "label": site_version_label(_value(site, "source_version"), _value(site, "package_sha256")),
        "source_format": str(_value(site, "source_format") or "internal"),
        "source_version": str(_value(site, "source_version") or ""),
        "package_schema_version": _value(site, "package_schema_version"),
        "package_sha256": str(_value(site, "package_sha256") or ""),
        "package_manifest": _clean_manifest(manifest),
        "imported_at": str(_value(site, "imported_at") or ""),
        "published_at": str(_value(site, "published_at") or ""),
    }


def versioned_manifest(
    manifest: Mapping[str, Any],
    *,
    source_version: str,
    package_sha256: str,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a bounded current/previous version envelope to a package manifest."""
    result = _clean_manifest(dict(manifest))
    state: dict[str, Any] = {
        "format_version": CONFERENCE_VERSION_STATE_FORMAT,
        "current": {
            "label": site_version_label(source_version, package_sha256),
            "source_version": source_version,
            "package_sha256": package_sha256,
            "package_schema_version": EVENT_WEBSITE_FORMAT_VERSION,
        },
    }
    if previous:
        state["previous"] = copy.deepcopy(dict(previous))
    result["versioning"] = state
    return result


def conference_version_state(site: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized current/previous labels from one conference-site row."""
    manifest_raw = _value(site, "package_manifest_json")
    try:
        import json

        manifest = json.loads(manifest_raw or "{}") if isinstance(manifest_raw, str) else (manifest_raw or {})
    except (TypeError, ValueError):
        manifest = {}
    versioning = manifest.get("versioning") if isinstance(manifest, dict) else {}
    previous = versioning.get("previous") if isinstance(versioning, dict) else None
    return {
        "current_label": site_version_label(_value(site, "source_version"), _value(site, "package_sha256")),
        "previous": previous if isinstance(previous, dict) else None,
        "previous_label": str(previous.get("label") or "") if isinstance(previous, dict) else "",
    }

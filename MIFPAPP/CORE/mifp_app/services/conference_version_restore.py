"""Restore the immediately previous imported conference WEBSITE atomically."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .conference_packages import normalize_public_path
from .event_site_publisher import EventSitePublisher
from .versioning import (
    EVENT_WEBSITE_FORMAT_VERSION,
    conference_snapshot,
    conference_version_state,
    versioned_manifest,
)


def restore_previous_website(
    conn,
    site: Mapping[str, Any],
    *,
    publisher: EventSitePublisher,
) -> str:
    """Swap current WEBSITE files/metadata with the retained rollback copy.

    The operation is reversible: the website that was current becomes the new
    rollback copy. Canonical Event metadata is deliberately untouched.
    """
    state = conference_version_state(site)
    previous = state.get("previous")
    if not isinstance(previous, dict):
        raise ValueError("No previous conference website version is recorded.")
    if previous.get("source_format") != "legacy-static":
        raise ValueError("Only a previously published WEBSITE package can be restored.")

    public_path = normalize_public_path(str(site["public_path"] or site["slug"]))
    current_snapshot = conference_snapshot(site)
    previous_manifest = previous.get("package_manifest")
    if not isinstance(previous_manifest, dict):
        previous_manifest = {}
    previous_version = str(previous.get("source_version") or "")
    previous_sha = str(previous.get("package_sha256") or "")
    restored_manifest = versioned_manifest(
        previous_manifest,
        source_version=previous_version,
        package_sha256=previous_sha,
        previous=current_snapshot,
    )

    swapped = False
    try:
        publisher.restore_previous(public_path)
        swapped = True
        conn.execute(
            """UPDATE conference_sites
               SET source_format=?,source_version=?,package_schema_version=?,
                   package_sha256=?,package_manifest_json=?,deploy_status='published',publication_error=NULL,
                   imported_at=CURRENT_TIMESTAMP,published_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                str(previous.get("source_format") or "legacy-static"),
                previous_version,
                previous.get("package_schema_version") or EVENT_WEBSITE_FORMAT_VERSION,
                previous_sha or None,
                json.dumps(restored_manifest, ensure_ascii=False, sort_keys=True),
                int(site["id"]),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        if swapped:
            try:
                publisher.restore_previous(public_path)
            except Exception:
                pass
        raise

    return str(previous.get("label") or previous_version or previous_sha[:12] or "package")

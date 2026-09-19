"""Restore the immediately previous imported conference WEBSITE atomically."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .conference_packages import normalize_public_path
from .event_import import php_execution_status
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
    events_root: Path,
    php_state_path: Path,
) -> str:
    """Swap current WEBSITE files/metadata with the retained rollback copy.

    The operation is reversible: the website that was current becomes the new
    rollback copy. Canonical Event metadata is deliberately untouched.
    """
    state = conference_version_state(site)
    previous = state.get("previous")
    if not isinstance(previous, dict):
        raise ValueError("No previous conference website version is recorded.")

    public_path = normalize_public_path(str(site["public_path"] or site["slug"]))
    root = Path(events_root).resolve()
    final = (root / public_path).resolve()
    try:
        final.relative_to(root)
    except ValueError as exc:
        raise ValueError("Unsafe conference public path.") from exc
    rollback = final.parent / f".{final.name}.rollback"
    if (
        not final.is_dir()
        or final.is_symlink()
        or not rollback.is_dir()
        or rollback.is_symlink()
    ):
        raise ValueError("The previous website files are no longer available for restore.")
    if php_execution_status(public_path, Path(php_state_path)) == "enabled":
        raise ValueError(
            "Disable PHP for this conference with mifpctl before restoring website code."
        )

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

    swap = final.parent / f".{final.name}.restore-{uuid4().hex}"
    swapped = False
    try:
        final.rename(swap)
        rollback.rename(final)
        swap.rename(rollback)
        swapped = True
        conn.execute(
            """UPDATE conference_sites
               SET source_format=?,source_version=?,package_schema_version=?,
                   package_sha256=?,package_manifest_json=?,deploy_status='published',
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
        if swapped and final.is_dir() and rollback.is_dir():
            failed = final.parent / f".{final.name}.restore-failed-{uuid4().hex}"
            final.rename(failed)
            rollback.rename(final)
            failed.rename(rollback)
        elif swap.exists() and not final.exists():
            swap.rename(final)
        raise

    return str(previous.get("label") or previous_version or previous_sha[:12] or "package")

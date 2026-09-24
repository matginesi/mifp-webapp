"""Disaster-recovery republication from retained WEBSITE source packages."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..db.connection import connect
from .event_import import (
    extract_website,
    inspect_website,
    retained_website_package_path,
)
from .event_site_publisher import EventSitePublisher, PublicationError


@dataclass(frozen=True)
class RepublicationResult:
    site_id: int
    public_path: str
    success: bool
    message: str


def republish_all_event_sites(
    *,
    database_path: Path,
    conferences_root: Path,
    temporary_root: Path,
    publisher: EventSitePublisher,
) -> list[RepublicationResult]:
    """Republish every recoverable static site, continuing after each failure."""
    status = publisher.status()
    if not status.available:
        raise PublicationError(f"Event-site publisher unavailable: {status.message}")

    temporary_root = Path(temporary_root)
    temporary_root.mkdir(parents=True, exist_ok=True)
    with connect(Path(database_path)) as conn:
        sites = conn.execute(
            """SELECT id,slug,public_path,package_sha256
               FROM conference_sites
               WHERE source_format='legacy-static' AND deploy_status<>'unpublished'
               ORDER BY public_path,id"""
        ).fetchall()
        results: list[RepublicationResult] = []
        for site in sites:
            site_id = int(site["id"])
            public_path = str(site["public_path"] or "").strip().strip("/")
            try:
                if not public_path:
                    raise ValueError("Publication metadata has no public_path.")
                package = retained_website_package_path(
                    Path(conferences_root),
                    str(site["slug"] or ""),
                    str(site["package_sha256"] or ""),
                )
                inspection = inspect_website(package, package.name)
                conn.execute(
                    """UPDATE conference_sites
                       SET deploy_status='staged',publication_error=NULL,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (site_id,),
                )
                conn.commit()
                with tempfile.TemporaryDirectory(
                    prefix=f"event-republish-{site_id}-", dir=temporary_root
                ) as temporary:
                    source = Path(temporary) / "website"
                    extract_website(package, source, inspection.root)
                    publisher.publish(
                        source,
                        public_path,
                        replace=True,
                        keep_rollback=True,
                    )
                conn.execute(
                    """UPDATE conference_sites
                       SET deploy_status='published',publication_error=NULL,
                           published_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (site_id,),
                )
                conn.commit()
                results.append(
                    RepublicationResult(site_id, public_path, True, "published")
                )
            except Exception as exc:
                if isinstance(exc, (ValueError, PublicationError)):
                    message = str(exc)
                else:
                    message = f"Republication failed ({type(exc).__name__})."
                conn.execute(
                    """UPDATE conference_sites
                       SET deploy_status='failed',publication_error=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (message[:500], site_id),
                )
                conn.commit()
                results.append(
                    RepublicationResult(site_id, public_path or "(missing)", False, message)
                )
        return results

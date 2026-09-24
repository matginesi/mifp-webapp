from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path
from uuid import uuid4

from flask import (
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from ..db.connection import connect
from ..services.conference_packages import (
    MAX_EDITOR_PACKAGE_BYTES,
    load_stored_package,
    looks_like_editor_package,
    normalize_public_path,
    store_editor_package,
)
from ..services.conference_sites import (
    ASSET_ROLES,
    PEOPLE_COLUMNS,
    build_site_zip,
    conference_config,
    config_from_form,
    import_conference_zip,
    normalize_base_path,
    parse_config_yaml,
    parse_people_upload,
    site_asset_dir,
    store_site_asset,
    validate_public_url,
    validate_slug,
)
from ..services.exporters import export_response_payload
from ..services.event_import import destination_url
from ..services.event_site_publisher import publisher_from_config
from ..services.operation_maintenance import maintenance_guarded
from ..services.conference_version_restore import restore_previous_website
from ..services.versioning import conference_version_state
from ..utils.logger import audit_log
from ..utils.text_utils import slugify
from .auth import login_required
from .dashboard import bp

SITE_FIELDS = (
    "title", "acronym", "year", "status", "start_date", "end_date", "venue",
    "city", "country", "canonical_url", "deploy_base_path", "registration_url",
    "contact_email", "description",
)


def _site_values(form) -> dict:
    values = {field: form.get(field, "").strip() for field in SITE_FIELDS}
    if not values["title"]:
        raise ValueError("Conference title is required.")
    values["canonical_url"] = validate_public_url(values["canonical_url"])
    values["registration_url"] = validate_public_url(values["registration_url"])
    values["deploy_base_path"] = normalize_base_path(values["deploy_base_path"] or "/")
    values["status"] = (
        values["status"] if values["status"] in {"draft", "ready", "archived"} else "draft"
    )
    values["year"] = int(values["year"]) if values["year"] else None
    return values


def _event_id(value: str | None) -> int | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        event_id = int(value)
    except ValueError as exc:
        raise ValueError("Linked event is invalid.") from exc
    if event_id <= 0:
        raise ValueError("Linked event is invalid.")
    return event_id


def _validate_event_link(conn, event_id: int | None, *, site_id: int | None = None) -> None:
    if event_id is None:
        return
    if not conn.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError("The selected institutional event no longer exists.")
    linked = conn.execute(
        "SELECT id FROM conference_sites WHERE event_id=?", (event_id,)
    ).fetchone()
    if linked and int(linked["id"]) != site_id:
        raise ValueError("That institutional event is already linked to another conference site.")


def _validate_public_path_unique(
    conn, public_path: str, *, site_id: int | None = None
) -> None:
    row = conn.execute(
        "SELECT id FROM conference_sites WHERE public_path=?", (public_path,)
    ).fetchone()
    if row and int(row["id"]) != site_id:
        raise ValueError("That public conference path is already in use.")


def _event_options(conn) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            """SELECT id,title,start_date,event_type,remote_url
               FROM events
               WHERE review_status <> 'duplicate'
               ORDER BY COALESCE(start_date,'') DESC,title,id DESC"""
        ).fetchall()
    ]


def _conference_event_location(values: dict) -> str | None:
    parts: list[str] = []
    for key in ("venue", "city", "country"):
        value = str(values.get(key) or "").strip()
        if value and value.casefold() not in {item.casefold() for item in parts}:
            parts.append(value)
    return ", ".join(parts) or None


def _unique_event_slug(conn, preferred: str) -> str:
    base = slugify(preferred) or "conference"
    candidate = base
    suffix = 2
    while conn.execute("SELECT 1 FROM events WHERE slug=?", (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _sync_conference_event(
    conn,
    *,
    event_id: int | None,
    site_slug: str,
    public_path: str,
    values: dict,
) -> tuple[int, bool]:
    """Ensure every conference workspace has a canonical Events record.

    Conference Sites owns microsite metadata while Events owns publication
    state.  Non-empty conference metadata is mirrored into the linked event,
    but blank conference fields never erase richer event information and the
    event review status is deliberately left untouched after creation.
    """
    title = str(values.get("title") or "").strip()
    if not title:
        raise ValueError("Conference title is required.")
    location = _conference_event_location(values)
    remote_url = str(values.get("canonical_url") or "").strip()
    if not remote_url:
        remote_url = destination_url(current_app.config["EVENTS_PUBLIC_BASE_URL"], public_path)

    if event_id is None:
        event_slug = _unique_event_slug(conn, site_slug or title)
        has_start = bool(values.get("start_date"))
        has_end = bool(values.get("end_date"))
        precision = (
            "range"
            if has_start and has_end and values.get("start_date") != values.get("end_date")
            else ("day" if has_start or has_end else "unknown")
        )
        cursor = conn.execute(
            """
            INSERT INTO events(
                slug,title,start_date,end_date,date_precision,location,description,
                event_type,review_status,remote_url
            ) VALUES(?,?,?,?,?,?,?,'conference','draft',?)
            """,
            (
                event_slug,
                title,
                values.get("start_date") or None,
                values.get("end_date") or None,
                precision,
                location,
                values.get("description") or None,
                remote_url or None,
            ),
        )
        return int(cursor.lastrowid), True

    if not conn.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError("The selected institutional event no longer exists.")

    updates: dict[str, object] = {"title": title, "event_type": "conference"}
    for field in ("start_date", "end_date", "description"):
        value = values.get(field)
        if value not in {None, ""}:
            updates[field] = value
    if location:
        updates["location"] = location
    if remote_url:
        updates["remote_url"] = remote_url
    if values.get("start_date") or values.get("end_date"):
        updates["date_precision"] = (
            "range"
            if values.get("start_date") and values.get("end_date") and values.get("start_date") != values.get("end_date")
            else "day"
        )
    set_clause = ",".join(f"{field}=?" for field in updates)
    conn.execute(
        f"UPDATE events SET {set_clause},updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (*updates.values(), event_id),
    )
    return event_id, False


def _sync_conference_event_from_site(conn, site: dict) -> tuple[int, bool]:
    values = {field: site.get(field) for field in SITE_FIELDS}
    return _sync_conference_event(
        conn,
        event_id=int(site["event_id"]) if site.get("event_id") else None,
        site_slug=str(site.get("slug") or values.get("title") or "conference"),
        public_path=str(site.get("public_path") or site.get("slug") or "conference"),
        values=values,
    )


def _site(conn, site_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM conference_sites WHERE id=?", (site_id,)).fetchone()
    return dict(row) if row else None


def _conference_runtime_status(site: dict) -> dict:
    """Decorate a conference-site row without probing the publication target."""
    enriched = dict(site)
    try:
        manifest = json.loads(enriched.get("package_manifest_json") or "{}")
    except (TypeError, ValueError):
        manifest = {}
    public_path = str(enriched.get("public_path") or enriched.get("slug") or "").strip("/")
    try:
        normalize_public_path(public_path)
        safe_path = bool(public_path)
    except ValueError:
        safe_path = False
    enriched["website_installed"] = bool(safe_path and enriched.get("deploy_status") == "published")
    try:
        enriched["destination_url"] = (
            destination_url(current_app.config["EVENTS_PUBLIC_BASE_URL"], public_path)
            if safe_path else ""
        )
    except ValueError:
        enriched["destination_url"] = ""
    enriched["php_files"] = int(manifest.get("php_files") or 0)
    enriched["regform_php_files"] = int(manifest.get("regform_php_files") or 0)
    enriched["php_execution"] = "disabled"
    enriched["validation_status"] = str(manifest.get("validation") or "unknown")
    enriched["metadata_linked"] = enriched.get("event_id") is not None
    version_state = conference_version_state(enriched)
    enriched["current_version"] = version_state["current_label"]
    enriched["previous_version"] = version_state["previous_label"]
    enriched["rollback_available"] = bool(
        enriched["website_installed"] and version_state["previous"]
    )
    return enriched


def _people(conn, site_id: int) -> list[dict]:
    return [
        dict(row) for row in conn.execute(
            "SELECT * FROM conference_people WHERE conference_id=? ORDER BY sort_order,name,id",
            (site_id,),
        ).fetchall()
    ]


def _apply_conference_import(site: dict, config_upload=None, package_upload=None) -> tuple[str, int]:
    if config_upload and config_upload.filename and package_upload and package_upload.filename:
        raise ValueError("Choose either config.yaml or a ZIP package, not both.")

    if package_upload and package_upload.filename:
        if not package_upload.filename.lower().endswith(".zip"):
            raise ValueError("Conference packages must use the .zip extension.")
        # Reject oversized uploads from the declared length before buffering the
        # whole body in memory: the parsed-package limit is enforced later, but
        # reading first would already have allocated the full request.
        declared = getattr(package_upload, "content_length", None)
        if declared is not None and declared > MAX_EDITOR_PACKAGE_BYTES:
            raise ValueError(
                f"Conference package exceeds the {MAX_EDITOR_PACKAGE_BYTES // (1024 * 1024)} MB limit."
            )
        raw = package_upload.read(MAX_EDITOR_PACKAGE_BYTES + 1)
        if len(raw) > MAX_EDITOR_PACKAGE_BYTES:
            raise ValueError(
                f"Conference package exceeds the {MAX_EDITOR_PACKAGE_BYTES // (1024 * 1024)} MB limit."
            )
        if looks_like_editor_package(raw):
            stored = store_editor_package(
                raw,
                Path(current_app.config["CONFERENCES_DIR"]),
                site["slug"],
            )
            package = stored.package
            metadata_updates: dict[str, object] = {
                "source_format": package.package_format,
                "source_version": package.source_version,
                "package_schema_version": package.schema_version,
                "package_sha256": package.sha256,
                "package_manifest_json": json.dumps(package.manifest(), ensure_ascii=False),
                "deploy_status": "staged",
            }
            for field in (
                "title", "acronym", "year", "start_date", "end_date", "venue",
                "city", "country", "contact_email", "canonical_url",
            ):
                value = getattr(package, field)
                if value not in {None, ""}:
                    metadata_updates[field] = value
            assignments = ",".join(f"{field}=?" for field in metadata_updates)
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                conn.execute(
                    f"""UPDATE conference_sites
                        SET {assignments},imported_at=CURRENT_TIMESTAMP,
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (*metadata_updates.values(), site["id"]),
                )
                conn.commit()
            return "conference-editor", package.file_count

        config, filenames = import_conference_zip(
            raw,
            Path(current_app.config["CONFERENCES_DIR"]),
            site["slug"],
        )
        source = "legacy-zip"
    elif config_upload and config_upload.filename:
        if not config_upload.filename.lower().endswith((".yaml", ".yml")):
            raise ValueError("Configuration files must use .yaml or .yml.")
        config = parse_config_yaml(config_upload.read())
        filenames = []
        source = "legacy-yaml"
    else:
        raise ValueError("Choose config.yaml or a conference ZIP package.")

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            """UPDATE conference_sites
               SET config_json=?,deploy_base_path=?,registration_url=?,
                   source_format='internal',source_version=NULL,
                   package_schema_version=NULL,package_sha256=NULL,
                   package_manifest_json='{}',deploy_status='unpublished',
                   imported_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                json.dumps(config, ensure_ascii=False),
                config["deployment"]["nginx_base_path"],
                config["registration"]["participant_url"],
                site["id"],
            ),
        )
        if filenames:
            conn.executemany(
                """INSERT INTO conference_assets(conference_id,filename,role)
                   VALUES(?,?,'gallery')
                   ON CONFLICT(conference_id,filename) DO NOTHING""",
                [(site["id"], filename) for filename in filenames],
            )
        conn.commit()
    return source, len(filenames)


def _asset_path(site: dict, filename: str) -> Path | None:
    if not filename or secure_filename(filename) != filename:
        return None
    root = site_asset_dir(Path(current_app.config["CONFERENCES_DIR"]), site["slug"]).resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _asset_view(path: Path, site_id: int, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    return {
        "name": path.name,
        "extension": path.suffix.lower().lstrip(".") or "file",
        "mime_type": mime_type,
        "size": size,
        "size_label": (
            f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024
            else f"{size / 1024:.1f} KB" if size >= 1024
            else f"{size} B"
        ),
        "is_image": mime_type.startswith("image/"),
        "url": url_for("dashboard.conference_asset_file", site_id=site_id, filename=path.name),
        "download_url": url_for(
            "dashboard.conference_asset_file",
            site_id=site_id,
            filename=path.name,
            download="1",
        ),
        "role": metadata.get("role") or "gallery",
        "label": metadata.get("label") or "",
        "person_id": metadata.get("person_id"),
        "person_name": metadata.get("person_name") or "",
    }


@bp.get("/conferences")
@login_required
def conference_sites():
    q = request.args.get("q", "").strip()
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        sql = """SELECT c.*,e.title AS event_title,e.start_date AS event_start_date,
               (SELECT COUNT(*) FROM conference_people p WHERE p.conference_id=c.id) AS people_count
               FROM conference_sites c
               LEFT JOIN events e ON e.id=c.event_id"""
        params: tuple = ()
        if q:
            sql += """ WHERE c.title LIKE ? OR c.acronym LIKE ? OR c.slug LIKE ?
                       OR c.public_path LIKE ? OR c.city LIKE ? OR c.country LIKE ?
                       OR CAST(c.year AS TEXT) LIKE ? OR e.title LIKE ?"""
            term = f"%{q}%"
            params = (term,) * 8
        sql += " ORDER BY COALESCE(c.start_date,'9999'),c.title"
        sites = [_conference_runtime_status(dict(row)) for row in conn.execute(sql, params).fetchall()]
        event_options = _event_options(conn)
    overview = {
        "total": len(sites),
        "installed": sum(1 for site in sites if site["website_installed"]),
        "linked": sum(1 for site in sites if site["metadata_linked"]),
        "failed": sum(1 for site in sites if site["deploy_status"] == "failed"),
    }
    return render_template(
        "dashboard/conferences.html",
        sites=sites,
        q=q,
        event_options=event_options,
        overview=overview,
        publisher_status=publisher_from_config(current_app.config).status(),
    )


@bp.post("/conferences")
@login_required
@maintenance_guarded("conference create and package upload")
def conference_create():
    site_id = None
    slug = None
    imported_source = None
    auto_event_id = None
    try:
        values = _site_values(request.form)
        slug = validate_slug(request.form.get("slug") or values["title"])
        public_path = normalize_public_path(request.form.get("public_path") or slug)
        event_id = _event_id(request.form.get("event_id"))
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            _validate_event_link(conn, event_id)
            _validate_public_path_unique(conn, public_path)
            event_id, event_created = _sync_conference_event(
                conn,
                event_id=event_id,
                site_slug=slug,
                public_path=public_path,
                values=values,
            )
            if event_created:
                auto_event_id = event_id
            cursor = conn.execute(
                f"""INSERT INTO conference_sites(
                        slug,public_path,event_id,{','.join(SITE_FIELDS)}
                    ) VALUES(?,?,?,{','.join('?' for _ in SITE_FIELDS)})""",
                (slug, public_path, event_id, *values.values()),
            )
            conn.commit()
            site_id = int(cursor.lastrowid)
        site_asset_dir(Path(current_app.config["CONFERENCES_DIR"]), slug)
        config_upload = request.files.get("config_file")
        package_upload = request.files.get("package_file")
        if (
            (config_upload and config_upload.filename)
            or (package_upload and package_upload.filename)
        ):
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                created_site = _site(conn, site_id)
            source, imported_count = _apply_conference_import(
                created_site, config_upload, package_upload
            )
            imported_source = source
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                refreshed_site = _site(conn, site_id)
                if refreshed_site:
                    synced_event_id, _ = _sync_conference_event_from_site(conn, refreshed_site)
                    conn.execute(
                        "UPDATE conference_sites SET event_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (synced_event_id, site_id),
                    )
                    conn.commit()
            audit_log(
                "conference.import",
                "conference source imported during creation",
                site_id=site_id,
                source=source,
                imported_files=imported_count,
            )
        uploads = [
            upload for upload in request.files.getlist("assets")
            if upload and upload.filename
        ]
        if uploads:
            if imported_source == "conference-editor":
                raise ValueError(
                    "Additional assets cannot be uploaded beside a Conference Editor package; "
                    "manage them in mifp-conference-editor and export a new package."
                )
            filenames = [
                store_site_asset(
                    Path(current_app.config["CONFERENCES_DIR"]), slug, upload
                )
                for upload in uploads
            ]
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                conn.executemany(
                    """INSERT INTO conference_assets(conference_id,filename,role)
                       VALUES(?,?,'gallery')""",
                    [(site_id, filename) for filename in filenames],
                )
                conn.commit()
        audit_log(
            "conference.create",
            "conference site created",
            site_id=site_id,
            slug=slug,
            public_path=public_path,
            event_id=event_id,
        )
        flash("Conference workspace created.", "success")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id))
    except (ValueError, OSError) as exc:
        if site_id is not None:
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                conn.execute("DELETE FROM conference_sites WHERE id=?", (site_id,))
                if auto_event_id is not None:
                    conn.execute("DELETE FROM events WHERE id=?", (auto_event_id,))
                conn.commit()
            if slug:
                shutil.rmtree(
                    Path(current_app.config["CONFERENCES_DIR"]) / slug,
                    ignore_errors=True,
                )
        flash(str(exc), "error")
        return redirect(url_for("dashboard.conference_sites"))


@bp.route("/conferences/<int:site_id>", methods=["GET", "POST"])
@login_required
def conference_edit(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        if request.method == "POST":
            try:
                values = _site_values(request.form)
                public_path = normalize_public_path(
                    request.form.get("public_path") or site["public_path"] or site["slug"]
                )
                event_id = _event_id(request.form.get("event_id")) or (
                    int(site["event_id"]) if site.get("event_id") else None
                )
                _validate_event_link(conn, event_id, site_id=site_id)
                _validate_public_path_unique(conn, public_path, site_id=site_id)
                event_id, _ = _sync_conference_event(
                    conn,
                    event_id=event_id,
                    site_slug=str(site.get("slug") or values["title"]),
                    public_path=public_path,
                    values=values,
                )
                conn.execute(
                    f"""UPDATE conference_sites
                        SET {','.join(f'{field}=?' for field in SITE_FIELDS)},
                            public_path=?,event_id=?,updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""",
                    (*values.values(), public_path, event_id, site_id),
                )
                conn.commit()
                audit_log(
                    "conference.update",
                    "conference site updated",
                    site_id=site_id,
                    public_path=public_path,
                    event_id=event_id,
                )
                flash("Conference details saved.", "success")
                return redirect(url_for("dashboard.conference_edit", site_id=site_id))
            except (ValueError, TypeError) as exc:
                flash(str(exc), "error")
        site = _site(conn, site_id)
        assert site is not None
        people = _people(conn, site_id)
        event_options = _event_options(conn)
        asset_rows = {
            row["filename"]: dict(row)
            for row in conn.execute(
                """SELECT a.*,p.name AS person_name
                   FROM conference_assets a
                   LEFT JOIN conference_people p ON p.id=a.person_id
                   WHERE a.conference_id=?""",
                (site_id,),
            ).fetchall()
        }
    asset_dir = site_asset_dir(Path(current_app.config["CONFERENCES_DIR"]), site["slug"])
    assets = [
        _asset_view(path, site_id, asset_rows.get(path.name))
        for path in sorted(asset_dir.iterdir(), key=lambda item: item.name.casefold())
        if path.is_file()
    ]
    try:
        package_manifest = json.loads(site.get("package_manifest_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        package_manifest = {}
    return render_template(
        "dashboard/conference_wizard.html",
        site=site,
        people=people,
        assets=assets,
        asset_roles=ASSET_ROLES,
        conference_config=conference_config(site.get("config_json"), site),
        package_manifest=package_manifest,
        event_options=event_options,
    )


@bp.post("/conferences/<int:site_id>/restore-previous")
@login_required
@maintenance_guarded("conference website rollback")
def conference_restore_previous(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        try:
            restored_label = restore_previous_website(
                conn,
                site,
                publisher=publisher_from_config(current_app.config),
            )
        except (ValueError, OSError) as exc:
            current_app.logger.warning(
                "conference previous-version restore rejected site_id=%s error=%s",
                site_id,
                exc,
            )
            flash(str(exc), "error")
            return redirect(url_for("dashboard.conference_sites"))

    audit_log(
        "conference.restore_previous",
        "previous conference website version restored",
        site_id=site_id,
        public_path=site.get("public_path") or site.get("slug"),
        restored_version=restored_label,
    )
    flash(f"Restored previous website version {restored_label}.", "success")
    return redirect(url_for("dashboard.conference_sites"))


@bp.post("/conferences/<int:site_id>/delete")
@login_required
def conference_delete(site_id: int):
    staged_dir: Path | None = None
    original_dir: Path | None = None
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        if request.form.get("confirm_title", "").strip() != site["title"]:
            flash("Type the complete conference title to confirm removal.", "error")
            return redirect(url_for("dashboard.conference_sites"))

        storage_root = Path(current_app.config["CONFERENCES_DIR"]).resolve()
        storage_root.mkdir(parents=True, exist_ok=True)
        original_dir = (storage_root / validate_slug(site["slug"])).resolve()
        try:
            original_dir.relative_to(storage_root)
        except ValueError:
            current_app.logger.error(
                "conference delete rejected unsafe storage path site_id=%s", site_id
            )
            return Response("Unsafe conference storage path", status=409)

        try:
            if original_dir.is_dir():
                staged_dir = storage_root / f".deleting-{site_id}-{uuid4().hex}"
                original_dir.rename(staged_dir)
            conn.execute("DELETE FROM conference_sites WHERE id=?", (site_id,))
            conn.commit()
        except (OSError, ValueError):
            conn.rollback()
            if staged_dir and staged_dir.exists() and original_dir and not original_dir.exists():
                staged_dir.rename(original_dir)
            current_app.logger.exception("conference delete failed site_id=%s", site_id)
            flash("Conference removal failed; database and storage were left unchanged.", "error")
            return redirect(url_for("dashboard.conference_sites"))

    if staged_dir and staged_dir.exists():
        try:
            shutil.rmtree(staged_dir)
        except OSError:
            current_app.logger.exception(
                "conference storage cleanup failed site_id=%s staged_dir=%s",
                site_id,
                staged_dir.name,
            )
            flash("Conference removed, but its staged storage needs manual cleanup.", "warning")
            return redirect(url_for("dashboard.conference_sites"))
    audit_log(
        "conference.delete",
        "conference and storage removed",
        site_id=site_id,
        slug=site["slug"],
    )
    flash("Conference and its stored assets were removed.", "success")
    return redirect(url_for("dashboard.conference_sites"))


@bp.post("/conferences/<int:site_id>/import")
@login_required
@maintenance_guarded("conference package import")
def conference_import(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
    if not site:
        return Response("Conference not found", status=404)
    try:
        source, imported_count = _apply_conference_import(
            site,
            request.files.get("config_file"),
            request.files.get("package_file"),
        )
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            refreshed_site = _site(conn, site_id)
            if refreshed_site:
                event_id, _ = _sync_conference_event_from_site(conn, refreshed_site)
                conn.execute(
                    "UPDATE conference_sites SET event_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (event_id, site_id),
                )
                conn.commit()
    except (ValueError, OSError) as exc:
        current_app.logger.warning(
            "conference import rejected site_id=%s error=%s", site_id, exc
        )
        flash(str(exc), "error")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#configuration")
    audit_log(
        "conference.import",
        "conference configuration package imported",
        site_id=site_id,
        source=source,
        imported_files=imported_count,
    )
    if source == "conference-editor":
        message = f"Imported Conference Editor package ({imported_count} files)."
    else:
        message = f"Imported {source.upper()} configuration"
        if imported_count:
            message += f" and {imported_count} assets."
        else:
            message += "."
    flash(message, "success")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#configuration")


@bp.post("/conferences/<int:site_id>/config")
@login_required
def conference_config_save(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        if site.get("source_format") == "conference-editor":
            flash(
                "This conference is managed by mifp-conference-editor. "
                "Change the source project there and import a new package.",
                "warning",
            )
            return redirect(
                url_for("dashboard.conference_edit", site_id=site_id) + "#configuration"
            )
        try:
            config = config_from_form(
                request.form,
                conference_config(site.get("config_json"), site),
            )
            conn.execute(
                """UPDATE conference_sites
                   SET config_json=?,deploy_base_path=?,registration_url=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (
                    json.dumps(config, ensure_ascii=False),
                    config["deployment"]["nginx_base_path"],
                    config["registration"]["participant_url"],
                    site_id,
                ),
            )
            conn.commit()
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#configuration")
    audit_log("conference.config_update", "conference YAML configuration updated", site_id=site_id)
    flash("Conference YAML configuration saved.", "success")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#configuration")


@bp.post("/conferences/<int:site_id>/people")
@login_required
def conference_person_save(site_id: int):
    person_id = request.form.get("person_id", "").strip()
    values: dict[str, str | int] = {field: request.form.get(field, "").strip() for field in PEOPLE_COLUMNS}
    try:
        if not values["name"]:
            raise ValueError("Person name is required.")
        values["website_url"] = validate_public_url(str(values["website_url"]))
        values["sort_order"] = int(values["sort_order"] or 0)
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            if not _site(conn, site_id):
                return Response("Conference not found", status=404)
            if person_id:
                conn.execute(
                    f"UPDATE conference_people SET {','.join(f'{field}=?' for field in PEOPLE_COLUMNS)},updated_at=CURRENT_TIMESTAMP WHERE id=? AND conference_id=?",
                    (*values.values(), int(person_id), site_id),
                )
            else:
                conn.execute(
                    f"INSERT INTO conference_people(conference_id,{','.join(PEOPLE_COLUMNS)}) VALUES(?,{','.join('?' for _ in PEOPLE_COLUMNS)})",
                    (site_id, *values.values()),
                )
            conn.commit()
        audit_log("conference.person_saved", "conference person saved", site_id=site_id)
        flash("Person saved.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#people")


@bp.post("/conferences/<int:site_id>/people/<int:person_id>/delete")
@login_required
def conference_person_delete(site_id: int, person_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute("DELETE FROM conference_people WHERE id=? AND conference_id=?", (person_id, site_id))
        conn.commit()
    audit_log("conference.person_deleted", "conference person deleted", site_id=site_id, person_id=person_id)
    flash("Person removed.", "success")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#people")


@bp.post("/conferences/<int:site_id>/people/import")
@login_required
@maintenance_guarded("conference people import")
def conference_people_import(site_id: int):
    upload = request.files.get("people_file")
    if not upload or not upload.filename:
        flash("Choose a CSV or Excel file.", "error")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#people")
    try:
        rows = parse_people_upload(upload)
        replace = request.form.get("replace") == "1"
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            if replace:
                conn.execute("DELETE FROM conference_people WHERE conference_id=?", (site_id,))
            conn.executemany(
                f"INSERT INTO conference_people(conference_id,{','.join(PEOPLE_COLUMNS)}) VALUES(?,{','.join('?' for _ in PEOPLE_COLUMNS)})",
                [(site_id, *(row[field] for field in PEOPLE_COLUMNS)) for row in rows],
            )
            conn.commit()
        audit_log("conference.people_import", "conference people imported", site_id=site_id, count=len(rows), replace=replace)
        flash(f"Imported {len(rows)} people.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#people")


@bp.get("/conferences/<int:site_id>/people/export.<fmt>")
@login_required
def conference_people_export(site_id: int, fmt: str):
    if fmt not in {"xlsx", "pdf", "json"}:
        return Response("Unsupported format", status=400)
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        rows = [{field: row.get(field) for field in PEOPLE_COLUMNS} for row in _people(conn, site_id)]
    payload, mimetype, extension = export_response_payload(rows, fmt, f"{site['title']} people")
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    audit_log("conference.people_export", "conference people exported", site_id=site_id, format=fmt, count=len(rows))
    return Response(payload, mimetype=mimetype, headers={
        "Content-Disposition": f"attachment; filename={site['slug']}-people.{extension}",
        "Cache-Control": "no-store, max-age=0",
        "X-Content-Type-Options": "nosniff",
    })


@bp.post("/conferences/<int:site_id>/assets")
@login_required
@maintenance_guarded("conference asset upload")
def conference_asset_upload(site_id: int):
    uploads = [
        upload for upload in request.files.getlist("assets")
        if upload and upload.filename
    ]
    if not uploads:
        legacy_upload = request.files.get("asset")
        uploads = [legacy_upload] if legacy_upload and legacy_upload.filename else []
    if not uploads:
        flash("Choose one or more assets.", "error")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
    if not site:
        return Response("Conference not found", status=404)
    try:
        role = request.form.get("asset_role", "gallery")
        if role not in ASSET_ROLES:
            raise ValueError("Invalid conference asset role.")
        filenames = [
            store_site_asset(Path(current_app.config["CONFERENCES_DIR"]), site["slug"], upload)
            for upload in uploads
        ]
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            conn.executemany(
                """INSERT INTO conference_assets(conference_id,filename,role)
                   VALUES(?,?,?)
                   ON CONFLICT(conference_id,filename) DO UPDATE SET
                   role=excluded.role,updated_at=CURRENT_TIMESTAMP""",
                [(site_id, filename, role) for filename in filenames],
            )
            conn.commit()
        audit_log(
            "conference.asset_upload",
            "conference assets uploaded",
            site_id=site_id,
            count=len(filenames),
            extensions=sorted({Path(name).suffix.lower() for name in filenames}),
        )
        flash(f"Uploaded {len(filenames)} asset{'s' if len(filenames) != 1 else ''}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")


@bp.post("/conferences/<int:site_id>/assets/<filename>/metadata")
@login_required
def conference_asset_metadata(site_id: int, filename: str):
    role = request.form.get("role", "gallery")
    label = request.form.get("label", "").strip()[:160]
    person_value = request.form.get("person_id", "").strip()
    if role not in ASSET_ROLES:
        flash("Invalid asset role.", "error")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        path = _asset_path(site, filename)
        if not path:
            return Response("Asset not found", status=404)
        image_roles = {"hero_logo", "speaker_photo", "sponsor_logo", "gallery"}
        if role in image_roles and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            flash(f"{role.replace('_', ' ').title()} requires an image file.", "error")
            return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
        if role == "program_source" and path.suffix.lower() != ".csv":
            flash("Program source requires a CSV file.", "error")
            return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
        try:
            person_id = int(person_value) if person_value else None
        except ValueError:
            flash("Invalid conference person.", "error")
            return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
        if role == "speaker_photo" and not person_id:
            flash("A speaker photo must be linked to a conference person.", "error")
            return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
        if role != "speaker_photo":
            person_id = None
        if person_id and not conn.execute(
            "SELECT 1 FROM conference_people WHERE id=? AND conference_id=?",
            (person_id, site_id),
        ).fetchone():
            flash("Selected person does not belong to this conference.", "error")
            return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
        conn.execute(
            """INSERT INTO conference_assets(conference_id,filename,role,label,person_id)
               VALUES(?,?,?,?,?)
               ON CONFLICT(conference_id,filename) DO UPDATE SET
               role=excluded.role,label=excluded.label,person_id=excluded.person_id,
               updated_at=CURRENT_TIMESTAMP""",
            (site_id, filename, role, label or None, person_id),
        )
        conn.commit()
    audit_log("conference.asset_metadata", "conference asset assignment updated", site_id=site_id, role=role)
    flash("Asset assignment saved.", "success")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")


@bp.get("/conferences/<int:site_id>/assets/<filename>")
@login_required
def conference_asset_file(site_id: int, filename: str):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
    if not site:
        return Response("Conference not found", status=404)
    path = _asset_path(site, filename)
    if not path:
        return Response("Asset not found", status=404)
    response = send_from_directory(
        str(path.parent),
        path.name,
        as_attachment=request.args.get("download") == "1",
        download_name=path.name,
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/conferences/<int:site_id>/assets/<filename>/delete")
@login_required
def conference_asset_delete(site_id: int, filename: str):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
    if not site:
        return Response("Conference not found", status=404)
    path = _asset_path(site, filename)
    if not path:
        flash("Asset not found.", "error")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")
    extension = path.suffix.lower()
    size = path.stat().st_size
    path.unlink()
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "DELETE FROM conference_assets WHERE conference_id=? AND filename=?",
            (site_id, filename),
        )
        conn.commit()
    audit_log(
        "conference.asset_delete",
        "conference asset deleted",
        site_id=site_id,
        extension=extension,
        bytes=size,
    )
    flash("Asset deleted.", "success")
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#assets")


@bp.get("/conferences/<int:site_id>/build.zip")
@login_required
def conference_build(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
        people = _people(conn, site_id)
        assets = [
            dict(row) for row in conn.execute(
                "SELECT * FROM conference_assets WHERE conference_id=? ORDER BY sort_order,filename",
                (site_id,),
            ).fetchall()
        ]

    try:
        if site.get("source_format") == "conference-editor":
            payload = load_stored_package(
                Path(current_app.config["CONFERENCES_DIR"]),
                site["slug"],
                site.get("package_sha256") or "",
            )
            version = str(site.get("source_version") or "package")
            filename = secure_filename(
                f"{site['public_path'] or site['slug']}-{version}.zip"
            ) or f"{site['slug']}-package.zip"
            audit_log(
                "conference.package_download",
                "validated Conference Editor package downloaded",
                site_id=site_id,
                sha256=site.get("package_sha256"),
                bytes=len(payload),
            )
        else:
            payload = build_site_zip(
                site,
                people,
                Path(current_app.config["CONFERENCES_DIR"]),
                assets,
            )
            filename = f"{site['slug']}-deploy.zip"
            audit_log(
                "conference.build",
                "conference deploy package built",
                site_id=site_id,
                bytes=len(payload),
                people=len(people),
            )
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409

    current_app.logger.info(
        "conference package ready site_id=%s source=%s bytes=%s",
        site_id,
        site.get("source_format") or "internal",
        len(payload),
    )
    return Response(
        payload,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )

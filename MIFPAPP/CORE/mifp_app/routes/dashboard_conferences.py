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
from ..utils.logger import audit_log
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


def _site(conn, site_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM conference_sites WHERE id=?", (site_id,)).fetchone()
    return dict(row) if row else None


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
    config = None
    filenames: list[str] = []
    if package_upload and package_upload.filename:
        if not package_upload.filename.lower().endswith(".zip"):
            raise ValueError("Conference packages must use the .zip extension.")
        config, filenames = import_conference_zip(
            package_upload.read(),
            Path(current_app.config["CONFERENCES_DIR"]),
            site["slug"],
        )
        source = "zip"
    elif config_upload and config_upload.filename:
        if not config_upload.filename.lower().endswith((".yaml", ".yml")):
            raise ValueError("Configuration files must use .yaml or .yml.")
        config = parse_config_yaml(config_upload.read())
        source = "yaml"
    else:
        raise ValueError("Choose config.yaml or a conference ZIP package.")

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            """UPDATE conference_sites
               SET config_json=?,deploy_base_path=?,registration_url=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
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
        sql = """SELECT c.*,
               (SELECT COUNT(*) FROM conference_people p WHERE p.conference_id=c.id) AS people_count
               FROM conference_sites c"""
        params: tuple = ()
        if q:
            sql += """ WHERE c.title LIKE ? OR c.acronym LIKE ? OR c.slug LIKE ?
                       OR c.city LIKE ? OR c.country LIKE ? OR CAST(c.year AS TEXT) LIKE ?"""
            term = f"%{q}%"
            params = (term,) * 6
        sql += " ORDER BY COALESCE(c.start_date,'9999'),c.title"
        sites = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return render_template("dashboard/conferences.html", sites=sites, q=q)


@bp.post("/conferences")
@login_required
def conference_create():
    site_id = None
    slug = None
    try:
        values = _site_values(request.form)
        slug = validate_slug(request.form.get("slug") or values["title"])
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            cursor = conn.execute(
                f"""INSERT INTO conference_sites(slug,{','.join(SITE_FIELDS)})
                    VALUES(?,{','.join('?' for _ in SITE_FIELDS)})""",
                (slug, *values.values()),
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
            source, asset_count = _apply_conference_import(
                created_site, config_upload, package_upload
            )
            audit_log(
                "conference.import",
                "conference source imported during creation",
                site_id=site_id,
                source=source,
                assets=asset_count,
            )
        uploads = [
            upload for upload in request.files.getlist("assets")
            if upload and upload.filename
        ]
        if uploads:
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
        audit_log("conference.create", "conference site created", site_id=site_id, slug=slug)
        flash("Conference workspace created.", "success")
        return redirect(url_for("dashboard.conference_edit", site_id=site_id))
    except (ValueError, OSError) as exc:
        if site_id is not None:
            with connect(current_app.config["DATABASE_PATH"]) as conn:
                conn.execute("DELETE FROM conference_sites WHERE id=?", (site_id,))
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
                conn.execute(
                    f"UPDATE conference_sites SET {','.join(f'{field}=?' for field in SITE_FIELDS)},updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (*values.values(), site_id),
                )
                conn.commit()
                audit_log("conference.update", "conference site updated", site_id=site_id)
                flash("Conference details saved.", "success")
                return redirect(url_for("dashboard.conference_edit", site_id=site_id))
            except (ValueError, TypeError) as exc:
                flash(str(exc), "error")
        site = _site(conn, site_id)
        assert site is not None
        people = _people(conn, site_id)
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
    return render_template(
        "dashboard/conference_wizard.html",
        site=site,
        people=people,
        assets=assets,
        asset_roles=ASSET_ROLES,
        conference_config=conference_config(site.get("config_json"), site),
    )


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
def conference_import(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
    if not site:
        return Response("Conference not found", status=404)
    try:
        source, asset_count = _apply_conference_import(
            site,
            request.files.get("config_file"),
            request.files.get("package_file"),
        )
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
        assets=asset_count,
    )
    flash(
        f"Imported {source.upper()} configuration"
        + (f" and {asset_count} assets." if asset_count else "."),
        "success",
    )
    return redirect(url_for("dashboard.conference_edit", site_id=site_id) + "#configuration")


@bp.post("/conferences/<int:site_id>/config")
@login_required
def conference_config_save(site_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        site = _site(conn, site_id)
        if not site:
            return Response("Conference not found", status=404)
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
        payload = build_site_zip(
            site,
            people,
            Path(current_app.config["CONFERENCES_DIR"]),
            assets,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409
    audit_log("conference.build", "conference deploy package built", site_id=site_id, bytes=len(payload), people=len(people))
    current_app.logger.info("conference package built site_id=%s bytes=%s people=%s", site_id, len(payload), len(people))
    return Response(payload, mimetype="application/zip", headers={
        "Content-Disposition": f"attachment; filename={site['slug']}-deploy.zip",
        "Content-Length": str(len(payload)),
        "Cache-Control": "no-store, max-age=0",
        "X-Content-Type-Options": "nosniff",
    })

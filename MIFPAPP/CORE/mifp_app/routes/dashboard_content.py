from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.wrappers.response import Response

from ..db.connection import connect
from ..services.admin_safety import backup_sqlite_database
from ..services.assets import store_asset
from ..services.dashboard_repository import (
    PUBLIC_TABLES,
    display_columns,
    editable_columns,
    get_record,
    list_records,
    list_records_paginated,
    list_roles,
    save_record,
    temporal_sort_events,
)
from ..utils.logger import audit_log
from ..utils.text_utils import slugify
from ._shared import ENTITY_TYPES, NEWS_TEMPLATES, PRIMARY_ASSET_FIELDS, SECTION_TABLES, admin_error_text
from .auth import login_required
from .dashboard import bp

_MD_DIR = Path(__file__).resolve().parent.parent.parent  # MIFPAPP/CORE/
_CREATE_REQUIRED_FIELDS = {
    "members": {"display_name"},
    "publications": {"title"},
    "research_areas": {"title"},
    "sponsors": {"name"},
}

_CREATE_PRIMARY_ASSETS = {
    "members": ("profile", "Profile image", "image/*"),
    "publications": (
        "document",
        "Publication document",
        ".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "research_areas": ("cover", "Cover image", "image/*"),
    "sponsors": ("logo", "Sponsor logo", "image/*"),
}


def _prepare_new_content_data(table: str, data: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(data)
    if table == "members" and not str(prepared.get("display_name") or "").strip():
        prepared["display_name"] = " ".join(
            str(prepared.get(key) or "").strip()
            for key in ("first_name", "last_name")
        ).strip()
    label = prepared.get("display_name") if table == "members" else (
        prepared.get("name") if table == "sponsors" else prepared.get("title")
    )
    if "slug" in PUBLIC_TABLES[table]["fields"] and not str(prepared.get("slug") or "").strip():
        prepared["slug"] = slugify(str(label or "")) or None
    if "review_status" in PUBLIC_TABLES[table]["fields"] and not prepared.get("review_status"):
        prepared["review_status"] = "draft"
    if "is_active" in PUBLIC_TABLES[table]["fields"] and "is_active" not in prepared:
        prepared["is_active"] = 0
    missing = sorted(
        field
        for field in _CREATE_REQUIRED_FIELDS.get(table, set())
        if not str(prepared.get(field) or "").strip()
    )
    if missing:
        labels = ", ".join(field.replace("_", " ") for field in missing)
        raise ValueError(f"Complete the required field(s): {labels}")
    return prepared


def _validate_new_record_completeness(table: str, data: dict[str, Any]) -> None:
    published = str(data.get("review_status") or "").strip() == "published"
    active = str(data.get("is_active") or "0").strip() == "1"
    required: set[str] = set()
    if published and table == "members":
        required = {"display_name", "affiliation", "country", "role_id"}
    elif published and table == "publications":
        required = {"title", "authors", "year"}
    elif published and table == "research_areas":
        required = {"title", "summary", "description"}
    elif active and table == "sponsors":
        required = {"name", "description", "tier"}
    missing = sorted(field for field in required if not str(data.get(field) or "").strip())
    if missing:
        labels = ", ".join(field.replace("_", " ") for field in missing)
        state = "published" if published else "active"
        raise ValueError(
            f"A {state} record must be complete. Add: {labels}, or save it as draft/inactive."
        )
    if active and table == "sponsors":
        upload = request.files.get("primary_asset")
        if not upload or not upload.filename:
            raise ValueError(
                "An active sponsor requires a logo. Upload it now or save the sponsor as inactive."
            )


def _attach_new_primary_asset(conn, table: str, record_id: int):
    upload = request.files.get("primary_asset")
    if not upload or not upload.filename:
        return None
    role, _label, _accept = _CREATE_PRIMARY_ASSETS[table]
    asset_id = store_asset(
        conn,
        upload,
        current_app.config["ASSETS_DIR"],
        commit=False,
    )
    entity_type = ENTITY_TYPES.get(table, table)
    conn.execute(
        """
        INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary,sort_order)
        VALUES(?,?,?,?,1,0)
        """,
        (asset_id, entity_type, record_id, role),
    )
    return asset_id

INSTITUTIONAL_SLUGS = {
    "about": "About Us",
    "manifesto": "Manifesto",
    "code-of-conduct": "Code of Conduct",
}

_INSTITUTIONAL_FILES = {
    "about": "About.md",
    "manifesto": "Manifesto.md",
    "code-of-conduct": "CodeOfConduct.md",
    "privacy": "Privacy.md",
    "cookie-policy": "cookie-policy.md",
}


def _read_md_file(filename: str) -> str:
    path = _MD_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _write_md_file(filename: str, content: str) -> None:
    path = _MD_DIR / filename
    path.write_text(content, encoding="utf-8")


def _read_banner_config() -> dict[str, str]:
    path = Path(current_app.config["BANNER_SETTINGS_PATH"])
    if path.exists():
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write_banner_config(data: dict[str, str]) -> None:
    import json
    path = Path(current_app.config["BANNER_SETTINGS_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_banner_config()
    current.update(data)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def asset_capabilities(table: str) -> dict[str, Any]:
    fields = PRIMARY_ASSET_FIELDS.get(table, {})
    return {
        "primary_image_field": any("image" in name or "logo" in name or "cover" in name for name in fields.values()),
        "primary_document_field": any("document" in name for name in fields.values()),
    }


def _with_asset_availability(asset: dict[str, Any]) -> dict[str, Any]:
    stored_path = str(asset.get("path") or "")
    filename = stored_path.split("/", 1)[1] if "/" in stored_path else stored_path
    local_file = Path(current_app.config["ASSETS_DIR"]) / filename if filename else None
    asset["preview_available"] = bool(
        asset.get("source_url") or (local_file and local_file.is_file())
    )
    return asset


def _linked_assets(conn, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    return [_with_asset_availability(dict(x)) for x in conn.execute(
        """
        SELECT a.id, a.original_filename, a.filename, a.path, a.kind, al.role, a.source_url
        FROM assets a
        JOIN asset_links al ON al.asset_id=a.id
        WHERE al.entity_type=? AND al.entity_id=?
        ORDER BY al.sort_order ASC, al.id ASC
        """,
        (entity_type, entity_id),
    ).fetchall()]


def _linked_links(conn, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    return [dict(x) for x in conn.execute(
        """
        SELECT id, url, label, role, is_primary
        FROM entity_links
        WHERE entity_type=? AND entity_id=?
        ORDER BY is_primary DESC, sort_order ASC, id ASC
        """,
        (entity_type, entity_id),
    ).fetchall()]


def _linked_assets_map(conn, entity_type: str, entity_ids) -> dict[int, list[dict[str, Any]]]:
    ids = [int(value) for value in entity_ids]
    result: dict[int, list[dict[str, Any]]] = {value: [] for value in ids}
    if not ids:
        return result
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT a.id, a.original_filename, a.filename, a.path, a.kind, al.role,
               a.source_url, al.is_primary, al.entity_id
        FROM assets a
        JOIN asset_links al ON al.asset_id=a.id
        WHERE al.entity_type=? AND al.entity_id IN ({placeholders})
        ORDER BY al.entity_id, al.sort_order ASC, al.id ASC
        """,
        (entity_type, *ids),
    ).fetchall()
    for row in rows:
        item = _with_asset_availability(dict(row))
        result[int(item.pop("entity_id"))].append(item)
    return result


def _linked_links_map(conn, entity_type: str, entity_ids) -> dict[int, list[dict[str, Any]]]:
    ids = [int(value) for value in entity_ids]
    result: dict[int, list[dict[str, Any]]] = {value: [] for value in ids}
    if not ids:
        return result
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, url, label, role, is_primary, entity_id
        FROM entity_links
        WHERE entity_type=? AND entity_id IN ({placeholders})
        ORDER BY entity_id, is_primary DESC, sort_order ASC, id ASC
        """,
        (entity_type, *ids),
    ).fetchall()
    for row in rows:
        item = dict(row)
        result[int(item.pop("entity_id"))].append(item)
    return result


@bp.route("/content/<section>", methods=["GET", "POST"])
@login_required
def content(section):
    table = SECTION_TABLES.get(section)
    if not table:
        flash("Invalid section.", "error")
        return redirect(url_for("dashboard.index"))
    q = request.args.get("q", "").strip() or None
    edit_id = request.args.get("edit", type=int)
    page = request.args.get("page", 1, type=int)

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        if request.method == "POST":
            data = dict(request.form)
            data.pop("_csrf_token", None)
            record_id = data.pop("id", None)
            is_new = not record_id
            current_app.logger.info(
                "content save started section=%s table=%s mode=%s fields=%s",
                section, table, "create" if is_new else "update", sorted(data),
            )
            try:
                if is_new:
                    data = _prepare_new_content_data(table, data)
                    _validate_new_record_completeness(table, data)
                saved_id = save_record(
                    conn, table, data, int(record_id) if record_id else None,
                    commit=False,
                )
                primary_asset_id = (
                    _attach_new_primary_asset(conn, table, saved_id)
                    if is_new and table in _CREATE_PRIMARY_ASSETS
                    else None
                )
                conn.commit()
                audit_log("content.create" if is_new else "content.update", "content save", table=table, record_id=saved_id)
                current_app.logger.info(
                    "content save completed section=%s table=%s mode=%s record_id=%s primary_asset_id=%s",
                    section, table, "create" if is_new else "update", saved_id,
                    primary_asset_id,
                )
                flash("Record saved.", "success")
            except ValueError as exc:
                conn.rollback()
                current_app.logger.warning(
                    "content save rejected section=%s table=%s mode=%s reason=%s",
                    section, table, "create" if is_new else "update", exc,
                )
                flash(str(exc), "error")
                return redirect(url_for("dashboard.content", section=section, new=1))
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                current_app.logger.warning(
                    "content save conflict section=%s table=%s mode=%s error=%s",
                    section, table, "create" if is_new else "update", exc,
                )
                flash("The record conflicts with an existing unique value. Review its slug or identifier.", "error")
            except Exception:
                conn.rollback()
                current_app.logger.exception("content save failed")
                flash(admin_error_text("The record could not be saved. Check the server log."), "error")
            return redirect(url_for("dashboard.content", section=section))

        columns = display_columns(conn, table)
        editable = editable_columns(conn, table)
        paginated = list_records_paginated(conn, table, q=q, page=page, per_page=50)
        records = paginated["records"]
        roles = list_roles(conn)
        role_names = {r["id"]: r.get("label") or r["name"] for r in roles}
        edit_record = get_record(conn, table, edit_id) if edit_id else None
        entity_type = ENTITY_TYPES.get(table, table)
        record_ids = [r["id"] for r in records]
        linked_assets = _linked_assets_map(conn, entity_type, record_ids)
        linked_links = _linked_links_map(conn, entity_type, record_ids)

        primary_asset_ids = {
            record_id: {
                asset["role"]: asset["id"]
                for asset in linked_assets.get(record_id, [])
                if asset.get("is_primary")
            }
            for record_id in record_ids
        }

    meta = {
        "title": PUBLIC_TABLES[table]["title"] if table in PUBLIC_TABLES else section.title(),
        "icon": PUBLIC_TABLES[table].get("icon", "bi-table") if table in PUBLIC_TABLES else "bi-table",
    }
    return render_template(
        "dashboard/content.html",
        section=section,
        meta=meta,
        columns=columns,
        editable=editable,
        records=records,
        roles=roles,
        role_names=role_names,
        edit_record=edit_record,
        table=table,
        linked_assets=linked_assets,
        linked_links=linked_links,
        primary_asset_ids=primary_asset_ids,
        required_fields=_CREATE_REQUIRED_FIELDS.get(table, set()),
        create_primary_asset=_CREATE_PRIMARY_ASSETS.get(table),
        q=q,
        asset_capabilities=asset_capabilities(table),
        news_templates=NEWS_TEMPLATES if section == "news" else None,
        pagination={
            "page": paginated["page"],
            "per_page": paginated["per_page"],
            "total_pages": paginated["total_pages"],
            "total": paginated["total"],
            "total_filtered": paginated["total_filtered"],
        },
    )


@bp.post("/content/<section>/<int:record_id>/delete")
@login_required
def content_delete(section: str, record_id: int):
    return _delete_record(section, record_id)


def _delete_record(section: str, record_id: int) -> Response:
    table = SECTION_TABLES.get(section)
    if not table:
        flash("Invalid section.", "error")
        return redirect(url_for("dashboard.index"))
    entity_type = ENTITY_TYPES.get(table, table)
    try:
        backup_path = backup_sqlite_database(current_app.config["DATABASE_PATH"], label=f"delete-{table}")
        with connect(current_app.config["DATABASE_PATH"]) as conn:
            conn.execute("DELETE FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, record_id))
            conn.execute(f'DELETE FROM "{table}" WHERE id=?', (record_id,))
            conn.commit()
        audit_log("content.delete", "content delete", table=table, record_id=record_id, backup_path=str(backup_path) if backup_path else None)
        flash("Record deleted.", "success")
    except Exception:
        current_app.logger.exception("delete failed")
        flash(admin_error_text("The record could not be deleted. Check the server log."), "error")
    if section == "events":
        return redirect(url_for("dashboard.events"))
    return redirect(url_for("dashboard.content", section=section))


@bp.route("/events/<int:record_id>")
@login_required
def events_record_json(record_id):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        record = get_record(conn, "events", record_id)
        if not record:
            return jsonify({"error": "Not found"}), 404
        assets = _linked_assets(conn, "event", record_id)
        links = _linked_links(conn, "event", record_id)
        cover = None
        for a in assets:
            if a["role"] in {"cover", "logo"}:
                cover = url_for("dashboard.asset_file", filename=a["path"].split("/", 1)[1] if "/" in a["path"] else a["path"])
                break
    return jsonify({"record": dict(record), "assets": assets, "links": links, "cover_url": cover})


def _event_has_publishable_cover(conn, record_id: int | None, form) -> bool:
    """Return whether the submitted event will retain a usable public cover.

    Inline edits do not submit asset fields, so existing cover/logo links must be
    considered. The event wizard explicitly manages the complete asset set; when
    it submits ``manage_event_assets=1`` an empty cover field means no cover.
    """
    submitted_cover = str(form.get("cover_asset_id") or "").strip()
    if submitted_cover:
        return True

    if form.get("manage_event_assets") == "1" or not record_id:
        return False

    return conn.execute(
        """
        SELECT 1
        FROM asset_links al
        JOIN assets a ON a.id=al.asset_id
        WHERE al.entity_type='event'
          AND al.entity_id=?
          AND a.kind='image'
          AND al.role IN ('cover','logo')
        LIMIT 1
        """,
        (record_id,),
    ).fetchone() is not None


def _process_event_assets(conn, event_id: int, form) -> dict[str, int]:
    entity_type = "event"
    linked = {"cover": 0, "documents": 0}

    conn.execute("DELETE FROM asset_links WHERE entity_type=? AND entity_id=?", (entity_type, event_id))

    cover_id = form.get("cover_asset_id")
    if cover_id:
        try:
            cover_id = int(cover_id)
            if not conn.execute("SELECT 1 FROM assets WHERE id=?", (cover_id,)).fetchone():
                raise ValueError("cover asset does not exist")
            conn.execute(
                "INSERT INTO asset_links(asset_id, entity_type, entity_id, role, is_primary, sort_order) VALUES(?,?,?,?,?,?)",
                (cover_id, entity_type, event_id, "cover", 1, 0),
            )
            linked["cover"] = 1
        except (TypeError, ValueError) as exc:
            raise ValueError("The selected event cover is invalid") from exc

    doc_ids = form.getlist("doc_asset_id")

    for i, aid in enumerate(doc_ids):
        if not str(aid or "").strip():
            continue
        try:
            asset_id = int(aid)
            if not conn.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                raise ValueError("document asset does not exist")
            conn.execute(
                "INSERT INTO asset_links(asset_id, entity_type, entity_id, role, is_primary, sort_order) VALUES(?,?,?,?,?,?)",
                (asset_id, entity_type, event_id, "document", 0, i + 1),
            )
            linked["documents"] += 1
        except (TypeError, ValueError) as exc:
            raise ValueError("One of the selected event documents is invalid") from exc

    return linked


@bp.route("/events", methods=["GET", "POST"])
@login_required
def events():
    table = "events"
    today = date.today()

    with connect(current_app.config["DATABASE_PATH"]) as conn:
        if request.method == "POST":
            form_data = dict(request.form)
            form_data.pop("_csrf_token", None)
            record_id = form_data.pop("id", None)
            is_new = not record_id
            current_app.logger.info(
                "event save started mode=%s fields=%s submitted_cover=%s manage_assets=%s documents=%s",
                "create" if is_new else "update",
                sorted(form_data),
                bool(request.form.get("cover_asset_id")),
                request.form.get("manage_event_assets") == "1",
                len([value for value in request.form.getlist("doc_asset_id") if value]),
            )
            try:
                if not str(form_data.get("title") or "").strip():
                    raise ValueError("Event title is required")
                if not form_data.get("review_status"):
                    form_data["review_status"] = "draft"
                if form_data["review_status"] == "published":
                    missing = []
                    if not (form_data.get("start_date") or form_data.get("date_text")):
                        missing.append("date")
                    if not str(form_data.get("description") or "").strip():
                        missing.append("description")
                    if not (form_data.get("location") or form_data.get("remote_url")):
                        missing.append("location or external URL")
                    existing_record_id = int(record_id) if record_id else None
                    if not _event_has_publishable_cover(conn, existing_record_id, request.form):
                        missing.append("cover image")
                    if missing:
                        raise ValueError(
                            "A published event must be complete. Add: "
                            + ", ".join(missing)
                            + ", or save it as draft."
                        )
                start_date = str(form_data.get("start_date") or "")
                end_date = str(form_data.get("end_date") or "")
                if start_date and end_date and end_date < start_date:
                    raise ValueError("Event end date cannot be before its start date")
                saved_id = save_record(
                    conn,
                    table,
                    form_data,
                    int(record_id) if record_id else None,
                    commit=False,
                )
                linked = {"cover": 0, "documents": 0}
                if request.form.get("manage_event_assets") == "1":
                    linked = _process_event_assets(conn, saved_id, request.form)
                conn.commit()
                audit_log("event.create" if is_new else "event.update", "event save", record_id=saved_id)
                current_app.logger.info(
                    "event save completed mode=%s record_id=%s asset_mode=%s cover_links_added=%s document_links_added=%s",
                    "create" if is_new else "update",
                    saved_id,
                    "managed" if request.form.get("manage_event_assets") == "1" else "preserved",
                    linked["cover"],
                    linked["documents"],
                )
                flash("Event saved.", "success")
            except ValueError as exc:
                conn.rollback()
                current_app.logger.warning(
                    "event save rejected mode=%s reason=%s",
                    "create" if is_new else "update", exc,
                )
                flash(str(exc), "error")
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                current_app.logger.warning(
                    "event save conflict mode=%s error=%s",
                    "create" if is_new else "update", exc,
                )
                flash("The event conflicts with an existing slug or identifier.", "error")
            except Exception:
                conn.rollback()
                current_app.logger.exception("event save failed")
                flash(admin_error_text("The event could not be saved. Check the server log."), "error")
            return redirect(url_for("dashboard.events"))

        columns = display_columns(conn, table)
        editable = editable_columns(conn, table)
        all_records = list_records(conn, table, q=request.args.get("q"), limit=500)
        sorted_records = temporal_sort_events(all_records, today)

        entity_type = "event"
        record_ids = [r["id"] for r in all_records]
        linked_assets = _linked_assets_map(conn, entity_type, record_ids)
        linked_links = _linked_links_map(conn, entity_type, record_ids)

    def _event_date(r):
        d = r.get("start_date") or r.get("end_date")
        if d:
            try:
                parts = str(d).split("-")
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            except (IndexError, ValueError):
                pass
        return None

    forthcoming = []
    past = []
    for r in sorted_records:
        d = _event_date(r)
        if d is not None:
            (forthcoming if d >= today else past).append(r)
        else:
            past.append(r)

    meta = {
        "title": "Events",
        "icon": "bi-calendar-event",
    }
    return render_template(
        "dashboard/events.html",
        meta=meta,
        columns=columns,
        editable=editable,
        section="events",
        table=table,
        records=all_records,
        forthcoming=forthcoming,
        past=past,
        linked_assets=linked_assets,
        linked_links=linked_links,
        q=request.args.get("q"),
    )


# ---------------------------------------------------------------------------
# Institutional pages (about, manifesto, code-of-conduct, privacy)
# ---------------------------------------------------------------------------

@bp.route("/institutional", methods=["GET", "POST"])
@login_required
def institutional():
    if request.method == "POST":
        slug = request.form.get("slug", "")
        if slug in INSTITUTIONAL_SLUGS:
            body = request.form.get("body", "").strip()
            filename = _INSTITUTIONAL_FILES.get(slug, slug + ".md")
            _write_md_file(filename, body)
            audit_log("institutional.update", "institutional page saved", slug=slug, file=filename)
            flash(f"{INSTITUTIONAL_SLUGS[slug]} saved.", "success")
        return redirect(url_for("dashboard.institutional"))

    pages = {}
    for slug, label in INSTITUTIONAL_SLUGS.items():
        filename = _INSTITUTIONAL_FILES.get(slug, slug + ".md")
        body = _read_md_file(filename)
        pages[slug] = {"body": body, "filename": filename}
    return render_template("dashboard/institutional.html", pages=pages, slugs=INSTITUTIONAL_SLUGS)


# ---------------------------------------------------------------------------
# Cookie policy management
# ---------------------------------------------------------------------------

@bp.route("/institutional/cookie", methods=["GET", "POST"])
@login_required
def institutional_cookie():
    """Route older bookmarks into the consolidated Privacy & Cookies workspace."""
    if request.method == "POST":
        action = request.form.get("_action", "")
        if action == "save_page":
            body = request.form.get("body", "").strip()
            _write_md_file("cookie-policy.md", body)
            audit_log("cookie.update", "cookie policy saved")
            flash("Cookie policy saved.", "success")
            return redirect(url_for("dashboard.institutional_privacy", tab="cookie"))
        if action == "save_settings":
            current = _read_banner_config()
            banner_theme = request.form.get("cookie_banner_theme", current.get("cookie_banner_theme", "brand"))
            if banner_theme not in {"brand", "neutral"}:
                banner_theme = "brand"
            _write_banner_config({
                "cookie_banner_enabled": "1" if request.form.get("cookie_banner_enabled") == "1" else "0",
                "cookie_banner_text": request.form.get("cookie_banner_text", "").strip()[:500],
                "cookie_banner_link_enabled": "1" if request.form.get("cookie_banner_link_enabled") == "1" else current.get("cookie_banner_link_enabled", "1"),
                "cookie_banner_dismiss_label": request.form.get("cookie_banner_dismiss_label", current.get("cookie_banner_dismiss_label", "Dismiss")).strip()[:40] or "Dismiss",
                "cookie_banner_theme": banner_theme,
            })
            audit_log("cookie.settings", "cookie settings saved")
            flash("Cookie banner settings saved.", "success")
            return redirect(url_for("dashboard.institutional_privacy") + "#banner-settings-title")
    return redirect(url_for("dashboard.institutional_privacy", tab="cookie"))


# ---------------------------------------------------------------------------
# Privacy + Cookie banner management
# ---------------------------------------------------------------------------

@bp.route("/institutional/privacy", methods=["GET", "POST"])
@login_required
def institutional_privacy():
    if request.method == "POST":
        action = request.form.get("_action", "")
        if action == "save_privacy":
            body = request.form.get("body", "").strip()
            _write_md_file("Privacy.md", body)
            audit_log("privacy.update", "privacy page saved")
            flash("Privacy page saved.", "success")
        elif action == "save_cookie":
            body = request.form.get("cookie_body", "").strip()
            _write_md_file("cookie-policy.md", body)
            audit_log("cookie.update", "cookie policy saved from privacy page")
            flash("Cookie policy saved.", "success")
        elif action == "save_banner":
            banner_enabled = "1" if request.form.get("cookie_banner_enabled") == "1" else "0"
            banner_text = request.form.get("cookie_banner_text", "").strip()[:500]
            force_show = _read_banner_config().get("banner_force_show", "0")
            link_enabled = "1" if request.form.get("cookie_banner_link_enabled") == "1" else "0"
            dismiss_label = request.form.get("cookie_banner_dismiss_label", "").strip()[:40] or "Dismiss"
            banner_theme = request.form.get("cookie_banner_theme", "brand")
            if banner_theme not in {"brand", "neutral"}:
                banner_theme = "brand"
            _write_banner_config({
                "cookie_banner_enabled": banner_enabled,
                "cookie_banner_text": banner_text,
                "banner_force_show": force_show,
                "cookie_banner_link_enabled": link_enabled,
                "cookie_banner_dismiss_label": dismiss_label,
                "cookie_banner_theme": banner_theme,
            })
            audit_log("banner.settings", "banner settings saved")
            flash("Banner settings saved.", "success")
        if action == "save_cookie":
            return redirect(url_for("dashboard.institutional_privacy", tab="cookie"))
        if action == "save_banner":
            return redirect(url_for("dashboard.institutional_privacy") + "#banner-settings-title")
        return redirect(url_for("dashboard.institutional_privacy"))

    privacy_body = _read_md_file("Privacy.md")
    cookie_body = _read_md_file("cookie-policy.md")
    settings = _read_banner_config()
    return render_template("dashboard/institutional_privacy.html", privacy_body=privacy_body, cookie_body=cookie_body, settings=settings)


@bp.post("/institutional/privacy/banner/force")
@login_required
def institutional_privacy_force_banner():
    revision = str(time.time_ns())
    _write_banner_config({
        "cookie_banner_enabled": "1",
        "banner_force_show": revision,
    })
    audit_log("banner.force_show", "cookie banner forced for all visitors", revision=revision)
    flash("Cookie banner forced. It will be shown to every visitor on their next page view.", "success")
    return redirect(url_for("dashboard.institutional_privacy") + "#banner-settings-title")

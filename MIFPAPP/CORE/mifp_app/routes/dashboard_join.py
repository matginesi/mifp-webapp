from __future__ import annotations

from typing import Any

from flask import Response, current_app, flash, redirect, render_template, request, session, url_for

from ..db.connection import connect
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import _download_response, bp


# ---------------------------------------------------------------------------
# Join Requests
# ---------------------------------------------------------------------------

JOIN_STATUSES = {"pending", "in_review", "approved", "rejected", "archived"}


@bp.get("/join-requests")
@login_required
def join_requests():
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 20
    where = []
    params = []
    if status in JOIN_STATUSES:
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR affiliation LIKE ? OR field LIKE ?)")
        params.extend([f"%{q}%"] * 5)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        order = "CASE status WHEN 'pending' THEN 0 WHEN 'in_review' THEN 1 WHEN 'approved' THEN 2 WHEN 'rejected' THEN 3 ELSE 4 END, created_at DESC, id DESC"
        count_row = conn.execute(f"SELECT COUNT(*) AS cnt FROM join_requests{clause}", params).fetchone()
        total_filtered = int(count_row["cnt"]) if count_row else 0
        total_pages = max(1, (total_filtered + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page
        requests_list = [dict(r) for r in conn.execute(
            f"""
            SELECT * FROM join_requests
            {clause}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, offset),
        ).fetchall()]
        counts = {r["status"]: r["total"] for r in conn.execute("SELECT status, COUNT(*) AS total FROM join_requests GROUP BY status").fetchall()}
    return render_template(
        "dashboard/join_requests.html",
        requests=requests_list,
        counts=counts,
        current_status=status,
        q=q,
        pagination={"page": page, "total_pages": total_pages, "total_filtered": total_filtered, "per_page": per_page},
    )


def _join_return():
    return redirect(url_for("dashboard.join_requests", status=request.form.get("return_status", ""), q=request.form.get("return_q", "")))


@bp.post("/join-requests/<int:request_id>/update")
@login_required
def join_update(request_id: int):
    status = request.form.get("status", "").strip()
    if status not in JOIN_STATUSES:
        status = "pending"
    admin_notes = request.form.get("admin_notes", "").strip()[:4000]
    decision_note = request.form.get("decision_note", "").strip()[:2000]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            """
            UPDATE join_requests
            SET status=?, admin_notes=?, decision_note=?, reviewed_at=CASE WHEN ? IN ('approved','rejected','archived') THEN CURRENT_TIMESTAMP ELSE reviewed_at END,
                reviewed_by=?
            WHERE id=?
            """,
            (status, admin_notes or None, decision_note or None, status, session.get("admin_username"), request_id),
        )
        conn.commit()
    audit_log("join.update", "join request updated", request_id=request_id, status=status)
    flash("Join request updated.", "success")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/approve")
@login_required
def join_approve(request_id: int):
    create_member = request.form.get("create_member") == "1"
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        row = conn.execute("SELECT * FROM join_requests WHERE id=?", (request_id,)).fetchone()
        member_id = row["member_id"] if row else None
        if row and create_member and not member_id:
            cur = conn.execute(
                """
                INSERT INTO members(first_name,last_name,display_name,email,affiliation,country,field,bio,is_active,review_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,1,'published',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                """,
                (
                    row["first_name"], row["last_name"], f"{row['first_name']} {row['last_name']}", row["email"],
                    row["affiliation"], row["country"], row["field"], row["motivation"]
                ),
            )
            member_id = cur.lastrowid
        conn.execute(
            "UPDATE join_requests SET status='approved', member_id=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
            (member_id, session.get("admin_username"), request_id),
        )
        conn.commit()
    audit_log("join.approve", "join request approved", request_id=request_id, create_member=create_member)
    flash("Join request approved." + (" Member created." if create_member else ""), "success")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/reject")
@login_required
def join_reject(request_id: int):
    decision_note = request.form.get("decision_note", "").strip()[:2000]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute(
            "UPDATE join_requests SET status='rejected', decision_note=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
            (decision_note or None, session.get("admin_username"), request_id),
        )
        conn.commit()
    audit_log("join.reject", "join request rejected", request_id=request_id)
    flash("Join request rejected.", "warning")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/archive")
@login_required
def join_archive(request_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute("UPDATE join_requests SET status='archived', reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?", (session.get("admin_username"), request_id))
        conn.commit()
    audit_log("join.archive", "join request archived", request_id=request_id)
    flash("Join request archived.", "success")
    return _join_return()


@bp.post("/join-requests/<int:request_id>/delete")
@login_required
def join_delete(request_id: int):
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        conn.execute("DELETE FROM join_requests WHERE id=?", (request_id,))
        conn.commit()
    audit_log("join.delete", "join request deleted", request_id=request_id)
    flash("Join request deleted.", "success")
    return _join_return()


_JOIN_EXPORT_COLUMNS = [
    ("first_name", "First name"),
    ("last_name", "Last name"),
    ("email", "Email"),
    ("affiliation", "Affiliation"),
    ("country", "Country"),
    ("position", "Position"),
    ("field", "Field"),
    ("orcid", "ORCID"),
    ("website_url", "Website URL"),
    ("status", "Status"),
    ("created_at", "Submitted at"),
    ("reviewed_at", "Reviewed at"),
    ("decision_note", "Decision note"),
]


def _join_export_rows(status: str = "", q: str = "") -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if status in JOIN_STATUSES:
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR affiliation LIKE ? OR field LIKE ?)")
        params.extend([f"%{q}%"] * 5)
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(col for col, _ in _JOIN_EXPORT_COLUMNS)} FROM join_requests{clause} ORDER BY created_at DESC, id DESC",
            params,
        ).fetchall()]
    return [
        {label: row.get(col) for col, label in _JOIN_EXPORT_COLUMNS}
        for row in rows
    ]


@bp.get("/join-requests/export.<fmt>")
@login_required
def join_export(fmt: str):
    if fmt not in {"csv", "xlsx"}:
        return Response("Invalid export format", status=400)
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    rows = _join_export_rows(status=status, q=q)
    audit_log("join.export", "join request export", format=fmt, count=len(rows), status=status or None)
    return _download_response(rows, fmt, "join_requests", "Join requests")


@bp.post("/join-requests/bulk-approve")
@login_required
def join_bulk_approve():
    ids = [int(i) for i in request.form.getlist("request_ids") if str(i).isdigit()]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        placeholders = ",".join("?" for _ in ids)
        if ids:
            conn.execute(
                f"UPDATE join_requests SET status='approved', reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id IN ({placeholders}) AND status NOT IN ('approved','archived')",
                (session.get("admin_username"), *ids),
            )
            conn.commit()
    audit_log("join.bulk_approve", "join requests bulk approved", count=len(ids))
    flash(f"{len(ids)} join request(s) approved.", "success")
    return _join_return()


@bp.post("/join-requests/bulk-archive")
@login_required
def join_bulk_archive():
    ids = [int(i) for i in request.form.getlist("request_ids") if str(i).isdigit()]
    with connect(current_app.config["DATABASE_PATH"]) as conn:
        placeholders = ",".join("?" for _ in ids)
        if ids:
            conn.execute(
                f"UPDATE join_requests SET status='archived', reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id IN ({placeholders}) AND status NOT IN ('archived')",
                (session.get("admin_username"), *ids),
            )
            conn.commit()
    audit_log("join.bulk_archive", "join requests bulk archived", count=len(ids))
    flash(f"{len(ids)} join request(s) archived.", "success")
    return _join_return()

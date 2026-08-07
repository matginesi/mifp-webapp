from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from flask import current_app, jsonify, render_template, request, session

from ..db.connection import connect
from ..services.data_quality import (
    add_to_bundle,
    analyze,
    apply_bundle,
    bundle_detail,
    count_findings,
    count_workflows,
    create_bundle,
    delete_draft,
    get_finding,
    latest_run,
    list_findings,
    remove_from_bundle,
)
from ..services.data_quality.analyzer import database_fingerprint, finding_workflow
from ..services.data_quality.planner import LABELS, TABLES, records_for
from ..services.data_quality.policies import similarity
from ..services.job_manager import JobQueueFull, get_job_manager
from ..services.operation_maintenance import maintenance_guarded
from ..utils.logger import audit_log
from .auth import login_required
from .dashboard import bp


def _payload() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _workspace_state(conn) -> tuple[dict | None, dict | None, int]:
    """Return the canonical run, editable queue and global open count."""
    run = latest_run(conn)
    open_total = (
        int(conn.execute(
            "SELECT COUNT(*) FROM quality_findings WHERE run_id=? AND status='open'",
            (int(run["id"]),),
        ).fetchone()[0] or 0)
        if run else 0
    )
    bundle_row = conn.execute(
        "SELECT id FROM quality_bundles WHERE status IN ('draft','validated') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    bundle = bundle_detail(conn, int(bundle_row["id"])) if bundle_row else None
    return run, bundle, open_total


@bp.get("/data-quality")
@login_required
def data_quality_page():
    current_app.logger.info("data_quality page load")
    workflow_counts = {"automatic": 0, "manual": 0, "informational": 0}
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        latest, bundle, total = _workspace_state(conn)
        run = None
        if latest:
            has_findings = conn.execute(
                "SELECT COUNT(*) FROM quality_findings WHERE run_id=?",
                (latest["id"],),
            ).fetchone()[0]
            if has_findings:
                run = latest
                workflow_counts = count_workflows(conn, int(run["id"]))
    current_app.logger.info("data_quality page loaded run_id=%s bundle_id=%s open=%s workflow=%s", run["id"] if run else None, bundle["id"] if bundle else None, total, workflow_counts)
    return render_template(
        "dashboard/data_quality.html", run=run, bundle=bundle, total=total,
        workflow_counts=workflow_counts,
    )


@bp.get("/data-quality/state")
@login_required
def data_quality_state():
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        run, bundle, open_total = _workspace_state(conn)
    queue_bundle = bundle or {"items": []}
    queue_html = render_template(
        "dashboard/data_quality/_queue_summary.html",
        bundle=queue_bundle,
    )
    return jsonify({
        "ok": True,
        "run_id": int(run["id"]) if run else None,
        "bundle_id": int(bundle["id"]) if bundle else None,
        "open_total": open_total,
        "queue_count": len(queue_bundle["items"]),
        "queue_html": queue_html,
        "can_apply": bool(queue_bundle["items"]),
    })


@bp.post("/data-quality/analyze")
@login_required
def data_quality_analyze():
    db_path = Path(current_app.config["DATABASE_PATH"])
    assets_dir = Path(current_app.config["ASSETS_DIR"])
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE quality_runs SET status='failed',completed_at=CURRENT_TIMESTAMP,"
            "progress_message='Worker stopped before completion' "
            "WHERE status='running' AND started_at < datetime('now','-2 hours')"
        )
        active = conn.execute(
            "SELECT id FROM quality_runs WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if active:
            conn.rollback()
            return jsonify({
                "ok": False,
                "error": "scan_already_running",
                "message": "A data quality scan is already running.",
                "run_id": int(active["id"]),
            }), 409
        fingerprint = database_fingerprint(conn)
        run_id = conn.execute(
            "INSERT INTO quality_runs(status,fingerprint,progress_pct,progress_message) VALUES('running',?,0,'Starting\u2026')",
            (fingerprint,),
        ).lastrowid
        conn.commit()
    current_app.logger.info("data_quality analyze started run_id=%s", run_id)

    app = current_app._get_current_object()
    def _run():
        try:
            with app.app_context(), connect(db_path) as c:
                result = analyze(c, run_id=run_id, assets_dir=assets_dir)
        except Exception as exc:
            try:
                with connect(db_path) as c:
                    c.execute("UPDATE quality_runs SET status='failed',progress_message=? WHERE id=?", (str(exc)[:200], run_id))
                    c.commit()
            except Exception:
                pass
            raise

    manager = get_job_manager(
        int(current_app.config.get("BACKGROUND_JOB_WORKERS", 2)),
        int(current_app.config.get("BACKGROUND_JOB_MAX_PENDING", 4)),
        db_path=db_path,
    )
    try:
        job_id, _future = manager.submit(f"data-quality:{run_id}", _run)
    except JobQueueFull:
        with connect(db_path) as conn:
            conn.execute(
                "UPDATE quality_runs SET status='failed',completed_at=CURRENT_TIMESTAMP,"
                "progress_message='Background job queue full' WHERE id=?",
                (run_id,),
            )
            conn.commit()
        return jsonify({"ok": False, "error": "job_queue_full"}), 503
    audit_log("data_quality.queued", "data quality scan queued", run_id=run_id, job_id=job_id)
    return jsonify({"ok": True, "run_id": run_id, "job_id": job_id})


@bp.get("/data-quality/analyze-progress")
@login_required
def data_quality_analyze_progress():
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        row = conn.execute(
            "SELECT id,status,progress_pct,progress_message,summary_json,duration_ms FROM quality_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return jsonify({"ok": True, "status": "none", "pct": 0, "message": ""})
    result = {
        "ok": True,
        "run_id": row["id"],
        "status": row["status"],
        "pct": row["progress_pct"] or 0,
        "message": row["progress_message"] or "",
    }
    if row["status"] == "completed":
        result["duration_ms"] = row["duration_ms"] or 0
        try:
            result["summary"] = json.loads(row["summary_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            result["summary"] = {}
        current_app.logger.info("data_quality analyze completed run_id=%s duration_ms=%s", row["id"], result["duration_ms"])
    elif row["status"] == "failed":
        current_app.logger.warning("data_quality analyze failed run_id=%s message=%s", row["id"], row["progress_message"])
    return jsonify(result)


@bp.get("/data-quality/findings")
@login_required
def data_quality_findings():
    run_id = request.args.get("run_id", type=int)
    current_app.logger.info("data_quality list_findings run_id=%s filters=%s", run_id, {k: v for k, v in request.args.items() if k != "run_id"})
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        if not run_id:
            latest = latest_run(conn)
            run_id = int(latest["id"]) if latest else 0
        filters = {
            "action_type": str(request.args.get("action_type") or ""),
            "entity_type": str(request.args.get("entity_type") or ""),
            "classification": str(request.args.get("classification") or ""),
        }
        offset = max(0, int(request.args.get("offset") or 0))
        limit = max(1, min(int(request.args.get("limit") or 30), 500))
        items = list_findings(
            conn,
            run_id, **filters, limit=limit, offset=offset,
        ) if run_id else []
        total = count_findings(conn, run_id, **filters) if run_id else 0
    current_app.logger.info("data_quality findings listed run_id=%s total=%s offset=%s", run_id, total, offset)
    items_html = render_template("dashboard/data_quality/_findings_list.html", findings=items)
    return jsonify({"ok": True, "run_id": run_id, "items": items, "items_html": items_html, "total": total, "offset": offset})


@bp.get("/data-quality/findings/<int:finding_id>")
@login_required
def data_quality_finding(finding_id: int):
    current_app.logger.info("data_quality get_finding finding_id=%s", finding_id)
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        finding = get_finding(conn, finding_id)
        if finding:
            finding = _finding_review_context(conn, finding)
    if not finding:
        current_app.logger.warning("data_quality finding not found finding_id=%s", finding_id)
        return jsonify({"ok": False, "message": "Finding not found"}), 404
    detail_html = render_template("dashboard/data_quality/_finding_detail.html", finding=finding)
    return jsonify({"ok": True, "finding": finding, "detail_html": detail_html})


def _finding_review_context(conn: sqlite3.Connection, finding: dict) -> dict:
    """Attach current source records and safe comparison candidates for review."""
    entity_type = str(finding.get("entity_type") or "")
    if entity_type not in TABLES:
        return finding
    record_ids = [
        int(value)
        for value in (finding.get("record_ids") or [])
        if str(value).isdigit()
    ]
    records = records_for(conn, entity_type, record_ids) if record_ids else []
    plan = dict(finding.get("plan") or {})
    plan["records"] = records
    finding = {**finding, "plan": plan, "source_records": records}

    label_field = LABELS[entity_type]
    source_labels = [
        str(record.get(label_field) or "").strip()
        for record in records
        if record.get(label_field)
    ]
    if not source_labels:
        finding["similar_records"] = []
        return finding

    table = TABLES[entity_type]
    excluded = set(record_ids)
    candidates: list[dict] = []
    for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY id DESC'):
        candidate = dict(row)
        if int(candidate["id"]) in excluded:
            continue
        candidate_label = str(candidate.get(label_field) or "").strip()
        if not candidate_label:
            continue
        score = max(similarity(label, candidate_label) for label in source_labels)
        if score < 0.45:
            continue
        candidate["similarity_score"] = score
        candidates.append(candidate)
    finding["similar_records"] = sorted(
        candidates,
        key=lambda item: (-float(item["similarity_score"]), int(item["id"])),
    )[:6]
    return finding


@bp.post("/data-quality/findings/<int:finding_id>/decision")
@login_required
def data_quality_decision(finding_id: int):
    data = _payload()
    decision = str(data.get("decision") or "")
    current_app.logger.info("data_quality decision finding_id=%s decision=%s entity=%s", finding_id, decision, data.get("finding_type", "?"))
    if decision not in {"keep_separate", "same_series", "false_positive", "ignored_test_data", "reject", "defer", "ignore", "accept"}:
        return jsonify({"ok": False, "message": "Unsupported decision"}), 400
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        finding = get_finding(conn, finding_id)
        if not finding:
            return jsonify({"ok": False, "message": "Finding not found"}), 404
        if decision == "accept":
            workflow = str(finding.get("workflow") or finding_workflow(finding))
            plan = finding.get("plan") or {}
            record_ids = [
                int(value)
                for value in (finding.get("record_ids") or [])
                if str(value).isdigit()
            ]

            # Some manual findings are intentionally review-only.  The main
            # example is an event page fragment: analysis can identify that a
            # row looks like a sub-page, but with no second record there is no
            # safe merge target to execute.  An administrator must be able to
            # close that finding after reviewing it instead of being trapped in
            # an impossible bundle action.  This changes only Data Quality
            # state; it never mutates the content record.
            review_only_manual = (
                workflow == "manual"
                and finding.get("action_type") == "merge_records"
                and (
                    str(plan.get("operation") or "") == "absorb_fragment"
                    or str(finding.get("classification") or "") == "page_fragment_attached"
                )
                and len(set(record_ids)) < 2
            )
            if review_only_manual:
                conn.execute(
                    "UPDATE quality_findings SET status='resolved',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (finding_id,),
                )
                conn.commit()
                current_app.logger.info(
                    "manual finding %s reviewed without database change classification=%s operation=%s",
                    finding_id, finding.get("classification"), plan.get("operation"),
                )
                audit_log(
                    "data_quality.manual_review",
                    "administrator completed a review-only Data Quality finding",
                    finding_id=finding_id,
                    classification=str(finding.get("classification") or ""),
                )
                return jsonify({
                    "ok": True,
                    "decision": decision,
                    "reviewed_without_change": True,
                })

            # Only safe automatic findings may be queued by one-click accept.
            # Actionable manual findings must go through the reviewed-plan form;
            # informational findings are dismissed/kept separate instead.
            if workflow != "automatic":
                current_app.logger.warning(
                    "accept finding %s rejected: workflow=%s classification=%s",
                    finding_id, workflow, finding.get("classification"),
                )
                return jsonify({
                    "ok": False,
                    "message": "This finding requires a reviewed plan before it can be queued.",
                }), 409

            # Get or create a draft bundle only when there is an executable
            # automatic action to queue.  Review-only findings above therefore
            # do not leave empty draft bundles behind.
            bundle_row = conn.execute(
                "SELECT id FROM quality_bundles WHERE status IN ('draft','validated') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if bundle_row:
                bundle_id = int(bundle_row["id"])
            else:
                bundle_id = create_bundle(conn, str(session.get("admin_username") or "admin"))
            payload = {"strategy": "best_quality"} if finding["action_type"] in {"merge_records", "enrich_record", "clean_record"} else {}
            try:
                current_app.logger.info(
                    "accept automatic finding %s: %s %s action=%s",
                    finding_id, finding["entity_type"], finding["classification"], finding["action_type"],
                )
                add_to_bundle(conn, bundle_id, finding_id, payload)
            except ValueError as exc:
                message = str(exc)
                current_app.logger.warning("accept finding %s failed: %s", finding_id, message)
                return jsonify({"ok": False, "message": message}), 409
            # Get the item_id we just created
            item_row = conn.execute(
                "SELECT id FROM quality_bundle_items WHERE bundle_id=? AND finding_id=? ORDER BY id DESC LIMIT 1",
                (bundle_id, finding_id),
            ).fetchone()
            conn.commit()
            current_app.logger.info("accept finding %s done: bundle_id=%s item_id=%s", finding_id, bundle_id, int(item_row["id"]) if item_row else None)
            return jsonify({"ok": True, "decision": decision, "bundle_id": bundle_id, "item_id": int(item_row["id"]) if item_row else None})
        if decision in {"reject", "defer", "ignore"}:
            conn.execute(
                "UPDATE quality_findings SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("rejected" if decision == "reject" else "deferred", finding_id),
            )
        else:
            conn.execute(
                """INSERT INTO merge_exclusions(entity_type,record_fingerprint,decision,note,created_by)
                   VALUES(?,?,?,?,?) ON CONFLICT(entity_type,record_fingerprint,decision)
                   DO UPDATE SET note=excluded.note,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP""",
                (
                    finding["entity_type"], finding["fingerprint"], decision,
                    str(data.get("note") or "")[:500], str(session.get("admin_username") or "admin"),
                ),
            )
            conn.execute(
                "UPDATE quality_findings SET classification='keep_separate',status='resolved',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (finding_id,),
            )
        conn.commit()
    return jsonify({"ok": True, "decision": decision})


_MAX_BULK_LIMIT = 1000


@bp.post("/data-quality/bulk-decision")
@login_required
def data_quality_bulk_decision():
    data = _payload()
    decision = str(data.get("decision") or "")
    current_app.logger.info("data_quality bulk_decision decision=%s finding_ids=%s filters=%s", decision, data.get("finding_ids"), data.get("filters"))
    if decision not in {"accept", "reject", "ignore", "defer",
                        "keep_separate", "same_series", "false_positive", "ignored_test_data"}:
        return jsonify({"ok": False, "message": "Unsupported decision"}), 400

    finding_ids = []
    if isinstance(data.get("finding_ids"), list):
        finding_ids = [int(v) for v in data["finding_ids"] if isinstance(v, (int, float))]
    filters = data.get("filters", {}) if isinstance(data.get("filters"), dict) else {}
    run_id = int(filters.get("run_id") or data.get("run_id") or 0)
    all_run = bool(data.get("all_run"))

    applied = 0
    failed = 0
    failures = []
    skipped_review = 0
    reviewed_without_change = 0
    review_items = []
    bundle_id = 0

    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        if all_run:
            if not run_id:
                return jsonify({"ok": False, "message": "A scan run is required"}), 400
            entities = []
            offset = 0
            while True:
                batch = list_findings(conn, run_id, limit=500, offset=offset)
                entities.extend(batch)
                if len(batch) < 500:
                    break
                offset += len(batch)
        elif finding_ids:
            placeholders = ",".join("?" for _ in finding_ids)
            rows = conn.execute(
                f"SELECT id, action_type, entity_type, classification, score, contradictions_json, fingerprint, plan_json FROM quality_findings WHERE id IN ({placeholders}) AND status='open'",
                finding_ids,
            ).fetchall()
            entities = [dict(r) for r in rows]
            for item in entities:
                item["contradictions"] = json.loads(item.pop("contradictions_json") or "[]")
                item["plan"] = json.loads(item.pop("plan_json") or "{}")
        else:
            filter_args = {
                "action_type": str(filters.get("action_type") or ""),
                "entity_type": str(filters.get("entity_type") or ""),
                "classification": str(filters.get("classification") or ""),
            }
            total = count_findings(conn, run_id, **filter_args) if run_id else 0
            if total > _MAX_BULK_LIMIT and not bool(data.get("acknowledge_overlimit")):
                return jsonify({
                    "ok": False,
                    "message": f"Match count ({total}) exceeds bulk limit ({_MAX_BULK_LIMIT}). "
                               "Either narrow filters or set acknowledge_overlimit=true.",
                    "total": total,
                    "limit": _MAX_BULK_LIMIT,
                }), 400
            entities = list_findings(conn, run_id, limit=_MAX_BULK_LIMIT, **filter_args)

        if decision == "accept":
            bundle_row = conn.execute(
                "SELECT id FROM quality_bundles WHERE status IN ('draft','validated') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if bundle_row:
                bundle_id = int(bundle_row["id"])
            else:
                bundle_id = create_bundle(conn, str(session.get("admin_username") or "admin"))

            for finding in entities:
                try:
                    action_type = str(finding.get("action_type") or "")
                    workflow = finding.get("workflow") or finding_workflow(finding)
                    if workflow != "automatic":
                        skipped_review += 1
                        current_app.logger.debug(
                            "bulk accept skipped finding=%s workflow=%s classification=%s",
                            finding.get("id"), workflow, finding.get("classification"),
                        )
                        continue
                    payload = {"strategy": "best_quality"} if action_type in {"merge_records", "enrich_record", "clean_record"} else {}
                    add_to_bundle(conn, bundle_id, finding["id"], payload)
                    applied += 1
                except ValueError as exc:
                    failed += 1
                    failures.append({"finding_id": finding["id"], "message": str(exc)})
            current_app.logger.info(
                "bulk_accept done findings=%d applied=%d skipped_manual=%d failed=%d bundle_id=%s",
                len(entities), applied, skipped_review, failed, bundle_id,
            )
            if failures:
                current_app.logger.info("bulk_accept failures: %s", failures[:20])
            conn.commit()
        elif decision in {"reject", "ignore", "defer"}:
            status = "rejected" if decision == "reject" else "deferred"
            for finding in entities:
                try:
                    conn.execute(
                        "UPDATE quality_findings SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (status, finding["id"]),
                    )
                    applied += 1
                except sqlite3.Error as exc:
                    failed += 1
                    failures.append({"finding_id": finding["id"], "message": str(exc)})
            conn.commit()
        else:
            for finding in entities:
                try:
                    conn.execute(
                        """INSERT INTO merge_exclusions(entity_type,record_fingerprint,decision,note,created_by)
                           VALUES(?,?,?,?,?) ON CONFLICT(entity_type,record_fingerprint,decision)
                           DO UPDATE SET note=excluded.note,created_by=excluded.created_by,updated_at=CURRENT_TIMESTAMP""",
                        (finding["entity_type"], finding.get("fingerprint", ""), decision,
                         "", str(session.get("admin_username") or "admin")),
                    )
                    conn.execute(
                        "UPDATE quality_findings SET classification='keep_separate',status='resolved',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (finding["id"],),
                    )
                    applied += 1
                except sqlite3.Error as exc:
                    failed += 1
                    failures.append({"finding_id": finding["id"], "message": str(exc)})
            conn.commit()

        current_app.logger.info("data_quality bulk_decision done decision=%s applied=%s failed=%s bundle_id=%s", decision, applied, failed, bundle_id)
        return jsonify({
            "ok": True,
            "result": {
                "applied": applied,
                "failed": failed,
                "failures": failures,
                "skipped_review": skipped_review,
                "reviewed_without_change": reviewed_without_change,
                "review_items": review_items[:50],
                "bundle_id": bundle_id if bundle_id else None,
            },
        })


@bp.get("/data-quality/bundles/<int:bundle_id>")
@login_required
def data_quality_bundle(bundle_id: int):
    current_app.logger.info("data_quality bundle_detail bundle_id=%s", bundle_id)
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        bundle = bundle_detail(conn, bundle_id)
    if not bundle:
        current_app.logger.warning("data_quality bundle not found bundle_id=%s", bundle_id)
        return jsonify({"ok": False, "message": "Bundle not found"}), 404
    queue_html = render_template("dashboard/data_quality/_queue_summary.html", bundle=bundle)
    return jsonify({"ok": True, "bundle": bundle, "queue_html": queue_html})


@bp.post("/data-quality/bundles")
@login_required
def data_quality_bundle_create():
    current_app.logger.info("data_quality bundle create")
    with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
        bundle_id = create_bundle(conn, str(session.get("admin_username") or "admin"))
    current_app.logger.info("data_quality bundle created bundle_id=%s", bundle_id)
    return jsonify({"ok": True, "bundle_id": bundle_id}), 201


@bp.post("/data-quality/bundles/<int:bundle_id>/items")
@login_required
def data_quality_bundle_add(bundle_id: int):
    data = _payload()
    current_app.logger.info("data_quality bundle_add bundle_id=%s finding_id=%s", bundle_id, data.get("finding_id"))
    try:
        with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
            plan = add_to_bundle(conn, bundle_id, int(data.get("finding_id") or 0), data)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409
    audit_log(
        "data_quality.manual_plan",
        "administrator queued a reviewed Data Quality plan",
        bundle_id=bundle_id,
        finding_id=int(data.get("finding_id") or 0),
    )
    return jsonify({"ok": True, "plan": plan})


@bp.post("/data-quality/bundles/<int:bundle_id>/items/<int:item_id>/remove")
@login_required
def data_quality_bundle_remove(bundle_id: int, item_id: int):
    current_app.logger.info("data_quality bundle_remove bundle_id=%s item_id=%s", bundle_id, item_id)
    try:
        with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
            remove_from_bundle(conn, bundle_id, item_id)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409
    return jsonify({"ok": True})


@bp.post("/data-quality/bundles/<int:bundle_id>/apply")
@login_required
@maintenance_guarded("data quality changes")
def data_quality_bundle_apply(bundle_id: int):
    try:
        report = apply_bundle(Path(current_app.config["DATABASE_PATH"]), bundle_id)
    except ValueError as exc:
        current_app.logger.warning("data_quality apply failed bundle_id=%s error=%s", bundle_id, exc)
        return jsonify({"ok": False, "message": str(exc)}), 409
    except Exception:
        current_app.logger.exception("Data quality bundle application failed", extra={"bundle_id": bundle_id})
        return jsonify({"ok": False, "message": "Bundle rolled back. Check the server log."}), 500
    audit_log("data_quality.bundle_applied", "Data quality bundle applied", bundle_id=bundle_id)
    return jsonify({"ok": True, "report": report})


@bp.delete("/data-quality/bundles/<int:bundle_id>")
@login_required
def data_quality_bundle_delete(bundle_id: int):
    current_app.logger.info("data_quality bundle_delete bundle_id=%s", bundle_id)
    try:
        with connect(Path(current_app.config["DATABASE_PATH"])) as conn:
            delete_draft(conn, bundle_id)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 409
    return jsonify({"ok": True})

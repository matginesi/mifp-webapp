"""Read-only access and exports for preserved invitation-era archives.

No creation, delivery, token exchange, response submission or data cleanup.
"""
from __future__ import annotations

import csv
import fcntl
import io
import json
import logging
import os
import re
import secrets
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLL_ID_RE = re.compile(r"^[0-9a-f]{32}$")

log = logging.getLogger("mifp.polls.storage")


class PollError(ValueError):
    pass


class PollNotFound(PollError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def _polls_root(runtime_config_dir: Path | str) -> Path:
    root = Path(runtime_config_dir) / "polls"
    if root.is_symlink():
        raise PollError("Poll storage cannot be a symbolic link")
    return root


def _poll_dir(runtime_config_dir: Path | str, poll_id: str) -> Path:
    if not POLL_ID_RE.fullmatch(str(poll_id or "")):
        raise PollNotFound("Poll not found")
    root = _polls_root(runtime_config_dir).resolve()
    directory = root / poll_id
    if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != root:
        raise PollNotFound("Poll not found")
    return directory


@contextmanager
def _poll_lock(directory: Path, *, shared: bool = False) -> Iterator[None]:
    # Archived files are immutable. Lock a pre-existing inode without creating
    # lockfiles or changing filesystem permissions during archive reads.
    try:
        descriptor = os.open(directory / "poll.json", os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PollNotFound("Poll metadata unavailable") from exc
    with os.fdopen(descriptor, "rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        yield


def _read_json(path: Path, expected: type) -> Any:
    if path.is_symlink():
        raise PollError("Unsafe archive path")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PollNotFound("Poll not found") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise PollError("Poll metadata is unavailable") from exc
    if not isinstance(payload, expected):
        raise PollError("Poll metadata is malformed")
    return payload


def get_poll(runtime_config_dir: Path | str, poll_id: str) -> dict[str, Any]:
    directory = _poll_dir(runtime_config_dir, poll_id)
    with _poll_lock(directory, shared=True):
        return _read_json(directory / "poll.json", dict)


def _invitations_unlocked(directory: Path) -> list[dict[str, Any]]:
    payload = _read_json(directory / "invitations.json", dict)
    rows = payload.get("invitations")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PollError("Invitation metadata is malformed")
    return rows


def _read_responses_unlocked(directory: Path) -> list[dict[str, Any]]:
    path = directory / "responses.jsonl"
    if path.is_symlink():
        raise PollError("Unsafe archive path")
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    if not data:
        return []
    lines = data.splitlines(keepends=True)
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        complete = line.endswith((b"\n", b"\r"))
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            if index == len(lines) - 1 and not complete:
                log.warning("truncated response tail ignored poll_id=%s", directory.name)
                break
            raise PollError("Response storage contains a malformed record") from exc
        if not isinstance(value, dict):
            raise PollError("Response storage contains a malformed record")
        rows.append(value)
    return rows


def current_responses(runtime_config_dir: Path | str, poll_id: str) -> list[dict[str, Any]]:
    directory = _poll_dir(runtime_config_dir, poll_id)
    with _poll_lock(directory, shared=True):
        rows = _read_responses_unlocked(directory)
    current: dict[str, dict[str, Any]] = {}
    for row in rows:
        invitation_id = str(row.get("invitation_id") or "")
        if not invitation_id:
            continue
        prior = current.get(invitation_id)
        if prior is None or int(row.get("revision") or 0) > int(prior.get("revision") or 0):
            current[invitation_id] = row
    return sorted(current.values(), key=lambda row: str(row.get("submitted_at") or ""), reverse=True)


def list_polls(runtime_config_dir: Path | str) -> list[dict[str, Any]]:
    root = _polls_root(runtime_config_dir)
    # Archive reads must never purge existing personal-invitation data.
    result: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir()) if root.exists() else []:
        if not directory.is_dir() or not POLL_ID_RE.fullmatch(directory.name) or directory.is_symlink():
            continue
        try:
            poll = get_poll(runtime_config_dir, directory.name)
            invitations = invitation_rows(runtime_config_dir, directory.name)
            responses = current_responses(runtime_config_dir, directory.name)
        except PollError:
            continue
        result.append(
            {
                **poll,
                "invitation_count": sum(row.get("status") == "sent" for row in invitations),
                "response_count": len(responses),
            }
        )
    return sorted(result, key=lambda row: str(row.get("updated_at") or ""), reverse=True)


def invitation_rows(runtime_config_dir: Path | str, poll_id: str) -> list[dict[str, Any]]:
    directory = _poll_dir(runtime_config_dir, poll_id)
    with _poll_lock(directory, shared=True):
        return _invitations_unlocked(directory)


def _response_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        invitation_id = str(row.get("invitation_id") or "")
        if invitation_id:
            groups.setdefault(invitation_id, []).append(row)
    for records in groups.values():
        records.sort(key=lambda item: (int(item.get("revision") or 0), str(item.get("submitted_at") or "")))
    return groups


def _response_activity(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"first_submitted_at": "", "last_modified_at": "", "revision_count": 0, "history_retained": 0}
    current = records[-1]
    legacy_compacted = (
        len(records) == 1
        and int(current.get("revision") or 0) > 1
        and not current.get("first_submitted_at")
        and not current.get("revision_timestamps")
    )
    first = "" if legacy_compacted else str(current.get("first_submitted_at") or records[0].get("submitted_at") or "")
    last = str(current.get("last_modified_at") or current.get("submitted_at") or "")
    revision_count = max(int(current.get("revision_count") or 0), int(current.get("revision") or 0), len(records))
    return {
        "first_submitted_at": first,
        "last_modified_at": last,
        "revision_count": revision_count,
        "history_retained": len(records),
    }


def _revision_timestamps(records: list[dict[str, Any]]) -> list[str]:
    if not records:
        return []
    timestamps: list[str] = []
    first_saved = records[0].get("revision_timestamps")
    if isinstance(first_saved, list):
        timestamps.extend(str(value) for value in first_saved if value)
        start = 1
    else:
        start = 0
    for record in records[start:]:
        submitted = str(record.get("submitted_at") or "")
        if submitted:
            timestamps.append(submitted)
    return timestamps


def response_history(runtime_config_dir: Path | str, poll_id: str, response_id: str) -> dict[str, Any]:
    poll = get_poll(runtime_config_dir, poll_id)
    directory = _poll_dir(runtime_config_dir, poll_id)
    with _poll_lock(directory, shared=True):
        rows = _read_responses_unlocked(directory)
        invitations = {row["invitation_id"]: row for row in _invitations_unlocked(directory)}
    target = next((row for row in rows if secrets.compare_digest(str(row.get("response_id") or ""), response_id)), None)
    if target is None:
        raise PollNotFound("Response not found")
    invitation_id = str(target.get("invitation_id") or "")
    records = _response_groups(rows).get(invitation_id, [])
    activity = _response_activity(records)
    revisions: list[dict[str, Any]] = []
    previous_answers: Mapping[str, Any] = {}
    for record in records:
        answers = record.get("answers") if isinstance(record.get("answers"), Mapping) else {}
        changed = [
            question["question"]
            for question in poll["questions"]
            if previous_answers and answers.get(question["id"]) != previous_answers.get(question["id"])
        ]
        revisions.append(
            {
                "revision": int(record.get("revision") or 0),
                "submitted_at": str(record.get("submitted_at") or ""),
                "display_answers": {
                    question["id"]: _answer_label(question, answers.get(question["id"]))
                    for question in poll["questions"]
                },
                "changed_questions": changed,
            }
        )
        previous_answers = answers
    identity: dict[str, str] = {}
    if poll.get("response_mode") == "identified":
        invitation = invitations.get(invitation_id, {})
        identity = {
            "first_name": str(invitation.get("first_name") or ""),
            "last_name": str(invitation.get("last_name") or ""),
            "display_name": str(invitation.get("display_name") or ""),
        }
    return {
        **activity,
        **identity,
        "history_compacted": activity["revision_count"] > activity["history_retained"],
        "revision_timestamps": _revision_timestamps(records),
        "revisions": list(reversed(revisions)),
        "questions": [{"id": q["id"], "question": q["question"]} for q in poll["questions"]],
    }


def response_view(runtime_config_dir: Path | str, poll_id: str) -> list[dict[str, Any]]:
    poll = get_poll(runtime_config_dir, poll_id)
    directory = _poll_dir(runtime_config_dir, poll_id)
    with _poll_lock(directory, shared=True):
        rows = _read_responses_unlocked(directory)
        invitations = {row["invitation_id"]: row for row in _invitations_unlocked(directory)}
    groups = _response_groups(rows)
    result: list[dict[str, Any]] = []
    for invitation_id, records in groups.items():
        row = records[-1]
        presented = {key: value for key, value in row.items() if key != "invitation_id"}
        presented.update(_response_activity(records))
        presented["display_answers"] = {
            question["id"]: _answer_label(question, row.get("answers", {}).get(question["id"]))
            for question in poll["questions"]
        }
        if poll.get("response_mode") == "identified":
            invitation = invitations.get(invitation_id, {})
            presented["first_name"] = invitation.get("first_name", "")
            presented["last_name"] = invitation.get("last_name", "")
            presented["display_name"] = invitation.get("display_name", "")
        result.append(presented)
    return sorted(
        result,
        key=lambda row: str(row.get("last_modified_at") or row.get("submitted_at") or ""),
        reverse=True,
    )


def poll_analysis(runtime_config_dir: Path | str, poll_id: str) -> dict[str, Any]:
    poll = get_poll(runtime_config_dir, poll_id)
    directory = _poll_dir(runtime_config_dir, poll_id)
    with _poll_lock(directory, shared=True):
        all_rows = _read_responses_unlocked(directory)
    groups = _response_groups(all_rows)
    responses = [records[-1] for records in groups.values()]
    invitations = invitation_rows(runtime_config_dir, poll_id)
    total = len(responses)
    output: list[dict[str, Any]] = []
    for question in poll["questions"]:
        qid = question["id"]
        values = [row.get("answers", {}).get(qid) for row in responses]
        answered = [value for value in values if value not in (None, "", [])]
        item: dict[str, Any] = {
            "id": qid,
            "question": question["question"],
            "type": question["type"],
            "answered": len(answered),
            "empty": total - len(answered),
        }
        if question["type"] == "yes_no":
            counts = Counter(answered)
            item["options"] = [
                {
                    "label": question.get("yes_label", "Yes"),
                    "value": "yes",
                    "count": counts["yes"],
                    "percentage": round(counts["yes"] * 100 / total, 1) if total else 0,
                },
                {
                    "label": question.get("no_label", "No"),
                    "value": "no",
                    "count": counts["no"],
                    "percentage": round(counts["no"] * 100 / total, 1) if total else 0,
                },
            ]
        elif question["type"] in {"single_choice", "multiple_choice"}:
            flattened = [choice for value in answered for choice in (value if isinstance(value, list) else [value])]
            counts = Counter(flattened)
            item["options"] = [
                {
                    "label": option["label"],
                    "value": option["id"],
                    "count": counts[option["id"]],
                    "percentage": round(counts[option["id"]] * 100 / total, 1) if total else 0,
                }
                for option in question.get("options", [])
            ]
        elif question["type"] == "date":
            counts = Counter(answered)
            item["options"] = [
                {
                    "label": selected,
                    "value": selected,
                    "count": count,
                    "percentage": round(count * 100 / total, 1) if total else 0,
                }
                for selected, count in sorted(counts.items())
            ]
        elif question["type"] == "text":
            item["responses"] = answered
        output.append(item)
    active_invites = sum(row.get("status") == "sent" for row in invitations)
    activity_by_date: dict[str, dict[str, int]] = {}
    modified_responses = 0
    total_revisions = 0
    last_activity = ""
    for records in groups.values():
        info = _response_activity(records)
        revision_count = int(info["revision_count"])
        total_revisions += revision_count
        if revision_count > 1:
            modified_responses += 1
        if str(info["last_modified_at"]) > last_activity:
            last_activity = str(info["last_modified_at"])
        legacy_compacted = (
            len(records) == 1
            and int(records[0].get("revision") or 0) > 1
            and not records[0].get("revision_timestamps")
        )
        for index, submitted in enumerate(_revision_timestamps(records)):
            day = submitted[:10]
            if not day:
                continue
            bucket = activity_by_date.setdefault(day, {"new": 0, "modified": 0})
            if index == 0 and not legacy_compacted:
                bucket["new"] += 1
            else:
                bucket["modified"] += 1
    return {
        "invitations": active_invites,
        "responses": total,
        "response_rate": round(total * 100 / active_invites, 1) if active_invites else 0,
        "modified_responses": modified_responses,
        "total_revisions": total_revisions,
        "last_response_activity": last_activity,
        "activity": [
            {"date": day, "new": counts["new"], "modified": counts["modified"]}
            for day, counts in sorted(activity_by_date.items())
        ],
        "questions": output,
    }


def _answer_label(question: Mapping[str, Any], value: Any) -> str:
    if value in (None, "", []):
        return ""
    if question["type"] == "yes_no":
        return str(question.get("yes_label" if value == "yes" else "no_label") or value)
    if question["type"] in {"single_choice", "multiple_choice"}:
        labels = {option["id"]: option["label"] for option in question.get("options", [])}
        values = value if isinstance(value, list) else [value]
        return "; ".join(labels.get(item, str(item)) for item in values)
    return str(value)


def _spreadsheet_safe(value: Any) -> str:
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _export_rows(runtime_config_dir: Path | str, poll_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    poll = get_poll(runtime_config_dir, poll_id)
    rows = response_view(runtime_config_dir, poll_id)
    exported: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "response_id": row["response_id"],
            "first_submitted_at": row.get("first_submitted_at") or row["submitted_at"],
            "last_modified_at": row.get("last_modified_at") or row["submitted_at"],
            "revision_count": int(row.get("revision_count") or row.get("revision") or 1),
        }
        if poll["response_mode"] == "identified":
            item["first_name"] = _spreadsheet_safe(row.get("first_name"))
            item["last_name"] = _spreadsheet_safe(row.get("last_name"))
        for index, question in enumerate(poll["questions"], 1):
            item[f"Q{index} {question['question']}"] = _spreadsheet_safe(
                _answer_label(question, row.get("answers", {}).get(question["id"]))
            )
        exported.append(item)
    return poll, exported


def export_csv(runtime_config_dir: Path | str, poll_id: str) -> bytes:
    poll, rows = _export_rows(runtime_config_dir, poll_id)
    headers = (
        list(rows[0])
        if rows
        else ["response_id", "first_submitted_at", "last_modified_at", "revision_count"]
        + [f"Q{index} {question['question']}" for index, question in enumerate(poll["questions"], 1)]
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def export_xlsx(runtime_config_dir: Path | str, poll_id: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    poll, rows = _export_rows(runtime_config_dir, poll_id)
    analysis = poll_analysis(runtime_config_dir, poll_id)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "Overview"
    overview.append(["MIFP Poll report"])
    overview.append(["Title", poll["title"]])
    overview.append(["Status", poll["status"].title()])
    overview.append(["Deadline", poll.get("deadline", "")])
    overview.append(["Invitations", analysis["invitations"]])
    overview.append(["Responses", analysis["responses"]])
    overview.append(["Modified responses", analysis["modified_responses"]])
    overview.append(["Total revisions", analysis["total_revisions"]])
    overview.append(["Last response activity", analysis["last_response_activity"]])
    overview.append(["Response rate", analysis["response_rate"] / 100])
    overview["B10"].number_format = "0.0%"
    responses_sheet = workbook.create_sheet("Responses")
    headers = (
        list(rows[0])
        if rows
        else ["response_id", "first_submitted_at", "last_modified_at", "revision_count"]
        + [f"Q{index} {question['question']}" for index, question in enumerate(poll["questions"], 1)]
    )
    responses_sheet.append(headers)
    for row in rows:
        responses_sheet.append([row.get(header, "") for header in headers])
    responses_sheet.freeze_panes = "A2"
    responses_sheet.auto_filter.ref = responses_sheet.dimensions
    analysis_sheet = workbook.create_sheet("Analysis")
    analysis_sheet.append(["Question", "Option / metric", "Count", "Percentage"])
    for question in analysis["questions"]:
        if question["type"] == "text":
            analysis_sheet.append([question["question"], "Answered", question["answered"], ""])
            analysis_sheet.append([question["question"], "Empty", question["empty"], ""])
        else:
            for option in question.get("options", []):
                analysis_sheet.append(
                    [question["question"], option["label"], option["count"], option["percentage"] / 100]
                )
                analysis_sheet.cell(analysis_sheet.max_row, 4).number_format = "0.0%"
    analysis_sheet.append([])
    activity_header_row = analysis_sheet.max_row + 1
    analysis_sheet.append(["Response activity", "New responses", "Modified responses", ""])
    for activity in analysis["activity"]:
        analysis_sheet.append([activity["date"], activity["new"], activity["modified"], ""])
    for sheet in workbook.worksheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="A72B31")
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(
                52, max(12, *(len(str(cell.value or "")) + 2 for cell in column))
            )
            for cell in column:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in analysis_sheet[activity_header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="A72B31")
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_pdf(runtime_config_dir: Path | str, poll_id: str) -> bytes:
    from xml.sax.saxutils import escape as xml_escape

    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from mifp_app.services.exporters import MIFP_EXPORT_COLORS

    poll = get_poll(runtime_config_dir, poll_id)
    analysis = poll_analysis(runtime_config_dir, poll_id)
    red = colors.HexColor(f"#{MIFP_EXPORT_COLORS['red']}")
    red_dark = colors.HexColor(f"#{MIFP_EXPORT_COLORS['red_dark']}")
    navy = colors.HexColor(f"#{MIFP_EXPORT_COLORS['navy']}")
    gray_100 = colors.HexColor(f"#{MIFP_EXPORT_COLORS['gray_100']}")
    gray_200 = colors.HexColor(f"#{MIFP_EXPORT_COLORS['gray_200']}")
    gray_500 = colors.HexColor(f"#{MIFP_EXPORT_COLORS['gray_500']}")
    body_color = colors.HexColor("#1F2937")
    display_font = "Times-Bold"
    body_font = "Helvetica"
    body_bold = "Helvetica-Bold"
    body_italic = "Helvetica-Oblique"

    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle(
        "MifpPollBrand",
        parent=styles["Heading1"],
        fontName=display_font,
        fontSize=18,
        leading=20,
        textColor=red,
        spaceAfter=2,
        spaceBefore=0,
    )
    title_style = ParagraphStyle(
        "MifpPollTitle",
        parent=styles["Heading2"],
        fontName=display_font,
        fontSize=14,
        leading=17,
        textColor=navy,
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        "MifpPollSubtitle",
        parent=styles["Normal"],
        fontName=body_italic,
        fontSize=8.5,
        leading=11,
        textColor=gray_500,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "MifpPollBody",
        parent=styles["BodyText"],
        fontName=body_font,
        fontSize=8.5,
        leading=12,
        textColor=body_color,
    )
    section_style = ParagraphStyle(
        "MifpPollSection",
        parent=styles["Heading2"],
        fontName=display_font,
        fontSize=12,
        leading=15,
        textColor=navy,
        spaceBefore=3 * mm,
        spaceAfter=2 * mm,
        keepWithNext=True,
    )
    question_meta_style = ParagraphStyle(
        "MifpPollQuestionMeta",
        parent=subtitle_style,
        fontName=body_font,
        spaceAfter=2 * mm,
        keepWithNext=True,
    )
    header_style = ParagraphStyle(
        "MifpPollHeader",
        parent=styles["Normal"],
        fontName=body_bold,
        fontSize=7,
        leading=9,
        textColor=colors.white,
    )
    cell_style = ParagraphStyle(
        "MifpPollCell",
        parent=styles["Normal"],
        fontName=body_font,
        fontSize=7.5,
        leading=10,
        textColor=body_color,
    )
    text_response_style = ParagraphStyle(
        "MifpPollTextResponse",
        parent=body_style,
        backColor=gray_100,
        borderColor=gray_200,
        borderWidth=0.4,
        borderPadding=6,
        spaceAfter=2.5 * mm,
    )

    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title=poll["title"],
        author="Mediterranean Institute of Fundamental Physics",
        subject="Poll analysis report",
    )
    exported_at = utc_now().strftime("%Y-%m-%d %H:%M UTC")
    response_label = "response" if analysis["responses"] == 1 else "responses"
    story: list[Any] = [
        Paragraph("MIFP", brand_style),
        Paragraph(xml_escape(str(poll["title"])), title_style),
        Paragraph(
            f"Poll analysis report  ·  Exported: {exported_at}  ·  "
            f"{analysis['responses']} {response_label}",
            subtitle_style,
        ),
        HRFlowable(width="100%", thickness=1.5, color=red, spaceAfter=8, spaceBefore=2),
    ]
    if poll.get("description"):
        story.extend(
            [
                Paragraph(xml_escape(str(poll["description"])), body_style),
                Spacer(1, 4 * mm),
            ]
        )

    summary_headers = ["Status", "Deadline", "Mode", "Invitations", "Responses", "Response rate"]
    summary_values = [
        str(poll["status"]).title(),
        str(poll.get("deadline") or "—"),
        str(poll.get("response_mode") or "anonymous").title(),
        str(analysis["invitations"]),
        str(analysis["responses"]),
        f"{analysis['response_rate']:.1f}%",
    ]
    story.extend(
        [
            Table(
                [
                    [Paragraph(value, header_style) for value in summary_headers],
                    [Paragraph(xml_escape(value), cell_style) for value in summary_values],
                ],
                colWidths=[23 * mm, 32 * mm, 29 * mm, 29 * mm, 27 * mm, 34 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), red),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), body_bold),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, gray_100]),
                        ("GRID", (0, 0), (-1, -1), 0.4, gray_200),
                        ("LINEBELOW", (0, 0), (-1, 0), 1.5, red_dark),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            ),
            Spacer(1, 3 * mm),
            Paragraph(
                f"Modified responses: <b>{analysis['modified_responses']}</b> &nbsp;&nbsp; "
                f"Total revisions: <b>{analysis['total_revisions']}</b> &nbsp;&nbsp; "
                f"Last activity: <b>{xml_escape(str(analysis['last_response_activity'] or '—'))}</b>",
                body_style,
            ),
            Paragraph("Response activity", section_style),
        ]
    )
    if analysis["activity"]:
        activity_rows = [
            [
                Paragraph("Date (UTC)", header_style),
                Paragraph("New", header_style),
                Paragraph("Modified", header_style),
            ]
        ] + [
            [
                Paragraph(xml_escape(str(item["date"])), cell_style),
                Paragraph(str(item["new"]), cell_style),
                Paragraph(str(item["modified"]), cell_style),
            ]
            for item in analysis["activity"]
        ]
        story.append(
            Table(
                activity_rows,
                repeatRows=1,
                colWidths=[90 * mm, 42 * mm, 42 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), red),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.4, gray_200),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                ),
            )
        )
    else:
        story.append(Paragraph("No response activity yet.", body_style))
    story.append(Paragraph("Response analysis", section_style))

    for index, question in enumerate(analysis["questions"], 1):
        story.append(
            Paragraph(
                f"Q{index} · {xml_escape(str(question['question']))}",
                section_style,
            )
        )
        story.append(
            Paragraph(
                f"Answered: {question['answered']}  ·  Empty: {question['empty']}",
                question_meta_style,
            )
        )
        if question["type"] == "text":
            responses = question.get("responses", [])
            if responses:
                for response in responses:
                    story.append(Paragraph(xml_escape(str(response)), text_response_style))
            else:
                story.append(Paragraph("No text responses.", body_style))
        else:
            table_rows = [
                [
                    Paragraph("Option", header_style),
                    Paragraph("Count", header_style),
                    Paragraph("Percentage", header_style),
                ]
            ] + [
                [
                    Paragraph(xml_escape(str(option["label"])), cell_style),
                    Paragraph(str(option["count"]), cell_style),
                    Paragraph(f"{option['percentage']:.1f}%", cell_style),
                ]
                for option in question.get("options", [])
            ]
            story.append(
                Table(
                    table_rows,
                    repeatRows=1,
                    colWidths=[113 * mm, 25 * mm, 36 * mm],
                    style=TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), red),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTNAME", (0, 0), (-1, 0), body_bold),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, gray_100]),
                            ("GRID", (0, 0), (-1, -1), 0.4, gray_200),
                            ("LINEBELOW", (0, 0), (-1, 0), 1.5, red_dark),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ]
                    ),
                )
            )
            options = question.get("options", [])
            if options:
                drawing = Drawing(174 * mm, min(78 * mm, max(32 * mm, len(options) * 11 * mm)))
                chart = HorizontalBarChart()
                chart.x = 45 * mm
                chart.y = 8 * mm
                chart.width = 120 * mm
                chart.height = drawing.height - 14 * mm
                chart.data = [[option["count"] for option in options]]
                chart.categoryAxis.categoryNames = [str(option["label"])[:45] for option in options]
                chart.categoryAxis.labels.fontName = body_font
                chart.categoryAxis.labels.fontSize = 7
                chart.categoryAxis.labels.fillColor = body_color
                chart.categoryAxis.strokeColor = gray_200
                chart.valueAxis.labels.fontName = body_font
                chart.valueAxis.labels.fontSize = 7
                chart.valueAxis.labels.fillColor = gray_500
                chart.valueAxis.strokeColor = gray_200
                chart.valueAxis.valueMin = 0
                chart.valueAxis.valueMax = max(1, *(option["count"] for option in options))
                chart.valueAxis.valueStep = max(1, int(chart.valueAxis.valueMax / 5))
                chart.bars[0].fillColor = red
                drawing.add(chart)
                story.append(Spacer(1, 3 * mm))
                story.append(drawing)
        story.append(Spacer(1, 4 * mm))

    def add_page_footer(canvas, _document):
        page_width, _page_height = A4
        canvas.saveState()
        canvas.setStrokeColor(red)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 13 * mm, page_width - 18 * mm, 13 * mm)
        canvas.setFont(body_font, 7)
        canvas.setFillColor(gray_500)
        canvas.drawString(18 * mm, 8 * mm, "MIFP · Poll analysis")
        canvas.drawRightString(page_width - 18 * mm, 8 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=add_page_footer, onLaterPages=add_page_footer)
    return output.getvalue()

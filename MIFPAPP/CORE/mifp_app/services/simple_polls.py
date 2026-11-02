"""Minimal JSON/JSONL polling for a shared Aruba mailing-list URL.

No email transport, recipient directory, or respondent identifier in anonymous mode.
The poll and its responses live in one deletable private folder.
"""
from __future__ import annotations

import csv
import fcntl
import io
import shutil
import json
import os
import re
import secrets
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

ID = re.compile(r"^[a-f0-9]{32}$")
KEY = re.compile(r"^[A-Za-z0-9_-]{32}$")
MODES = {"anonymous", "identified"}
TYPES = {"text", "yes_no", "single_choice", "multiple_choice", "date"}
BASES = {"consent", "legitimate_interest", "public_task", "contract"}


class SurveyError(ValueError):
    pass


def _root(config_dir: Path | str) -> Path:
    root = Path(config_dir) / "polls_v2"
    if root.is_symlink():
        raise SurveyError("Unsafe survey storage path")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root.resolve()


def _directory(config_dir: Path | str, poll_id: str, *, create: bool = False) -> Path:
    if not ID.fullmatch(poll_id):
        raise SurveyError("Survey not found")
    root = _root(config_dir)
    directory = root / poll_id
    if directory.is_symlink():
        raise SurveyError("Survey not found")
    if create:
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    if not directory.is_dir() or directory.resolve().parent != root:
        raise SurveyError("Survey not found")
    return directory


@contextmanager
def _locked(directory: Path, exclusive: bool = False, *, deleting: bool = False) -> Iterator[None]:
    # Keep a stable root interlock while a poll is in use. Deletion must not
    # unlink a lock inode while another process is queued on that inode.
    try:
        guard = os.open(directory.parent / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise SurveyError("Survey storage unavailable") from exc
    with os.fdopen(guard, "a+b") as root_lock:
        os.fchmod(root_lock.fileno(), 0o600)
        fcntl.flock(root_lock.fileno(), fcntl.LOCK_EX if deleting else fcntl.LOCK_SH)
        if not directory.is_dir() or directory.is_symlink():
            raise SurveyError("Survey not found")
        for name in ("poll.json", "answers.jsonl", ".lock"):
            if (directory / name).is_symlink():
                raise SurveyError("Unsafe survey storage path")
        with (directory / ".lock").open("a+b") as handle:
            os.chmod(directory / ".lock", 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield


def _atomic(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".poll-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_config(directory: Path) -> dict:
    try:
        data = json.loads((directory / "poll.json").read_text("utf-8"))
        if (not isinstance(data, dict) or not isinstance(data.get("id"), str)
                or not ID.fullmatch(data["id"]) or data["id"] != directory.name
                or not isinstance(data.get("key"), str) or not KEY.fullmatch(data["key"])
                or data.get("response_mode") not in MODES
                or data.get("status") not in {"draft", "open", "closed"}
                or not isinstance(data.get("questions"), list) or not data["questions"]
                or not isinstance(data.get("privacy"), dict)
                or not {"controller", "contact", "purpose", "legal_basis", "recipients", "transfers", "confirmed"} <= data["privacy"].keys()
                or not all(isinstance(data.get(k), str) for k in
                           ("title", "description", "deadline", "retention_until", "created_at"))
                or not all(isinstance(q, dict) and isinstance(q.get("id"), str)
                           and q.get("type") in TYPES and isinstance(q.get("question"), str)
                           and isinstance(q.get("required"), bool) for q in data["questions"])):
            raise ValueError()
        return data
    except (OSError, ValueError) as exc:
        raise SurveyError("Survey metadata unavailable") from exc


def _read_answers(directory: Path) -> list[dict]:
    try:
        raw = (directory / "answers.jsonl").read_bytes()
    except FileNotFoundError:
        return []
    rows = []
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.endswith(b"\n"):
            if index == len(lines) - 1:
                break  # a crash may have interrupted only the final append
            raise SurveyError("Corrupted survey response journal")
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise SurveyError("Corrupted survey response journal") from exc
        if (not isinstance(item, dict)
                or not isinstance(item.get("request_id"), str)
                or not ID.fullmatch(item["request_id"])
                or not isinstance(item.get("answers"), dict)
                or not all(isinstance(k, str) and (isinstance(v, str) or
                    (isinstance(v, list) and all(isinstance(x, str) for x in v)))
                    for k, v in item["answers"].items())
                or not isinstance(item.get("submitted_at"), str)
                or not isinstance(item.get("privacy_version"), str)
                or any(k in item and not isinstance(item[k], str) for k in
                       ("first_name", "last_name", "email", "consent_at"))):
            raise SurveyError("Corrupted survey response journal")
        rows.append(item)
    return rows


def _append(directory: Path, row: dict) -> None:
    path = directory / "answers.jsonl"
    # Repair only a demonstrably incomplete final record before appending.
    with path.open("a+b") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        if end:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0)
                raw = handle.read()
                handle.truncate(raw.rfind(b"\n") + 1)
        handle.seek(0, os.SEEK_END)
        content = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _clean(value: Any, maximum: int) -> str:
    value = str(value or "").strip()
    if len(value) > maximum:
        raise SurveyError("A survey field exceeds its allowed length")
    return value


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError) as exc:
        raise SurveyError("Enter a valid date") from exc


def _normalize_questions(questions: Any) -> list[dict]:
    if not isinstance(questions, list) or not 1 <= len(questions) <= 30:
        raise SurveyError("A survey needs between 1 and 30 questions")
    out = []
    for raw in questions:
        if not isinstance(raw, dict) or raw.get("type") not in TYPES:
            raise SurveyError("Invalid question type")
        title = _clean(raw.get("question"), 300)
        if not title:
            raise SurveyError("Question text is required")
        kind = raw["type"]
        item = {"id": secrets.token_hex(8), "type": kind, "question": title, "required": bool(raw.get("required", False))}
        if kind == "text":
            style = str(raw.get("style") or "single_line")
            if style not in {"single_line", "multi_line"}:
                raise SurveyError("Invalid text response style")
            item["style"] = style
        if kind == "yes_no":
            item["yes_label"] = _clean(raw.get("yes_label") or "Yes", 80)
            item["no_label"] = _clean(raw.get("no_label") or "No", 80)
            if item["yes_label"].casefold() == item["no_label"].casefold():
                raise SurveyError("Yes / No labels must be different")
        if kind in {"single_choice", "multiple_choice"}:
            options = raw.get("options") or []
            if not isinstance(options, list):
                raise SurveyError("Invalid question choices")
            names = [_clean(name, 160) for name in options]
            if len(names) < 2 or len(names) > 30 or not all(names) or len(set(x.casefold() for x in names)) != len(names):
                raise SurveyError("Choice questions require 2–30 distinct options")
            item["options"] = [{"id": secrets.token_hex(8), "label": name} for name in names]
        out.append(item)
    return out


def create(config_dir: Path | str, template: str = "blank") -> dict:
    # Original editor starting points; each is a fully editable draft.
    examples = {
        "blank": ("New survey", "", [{"type": "yes_no", "question": "Will you attend?", "required": True}]),
        "attendance": ("Attendance confirmation", "Please confirm whether you plan to attend.",
                       [{"type": "yes_no", "question": "Are you planning to attend?", "required": True}]),
        "availability": ("Event availability", "Please indicate your availability.",
                         [{"type": "multiple_choice", "question": "Which dates are suitable?", "required": True,
                           "options": ["Date 1", "Date 2", "Date 3"]}]),
        "date": ("Choose a date", "Please indicate your preferred date.",
                 [{"type": "date", "question": "Which date do you prefer?", "required": True}]),
        "location": ("Choose a location", "Please choose your preferred location.",
                     [{"type": "single_choice", "question": "Which location would you prefer?", "required": True,
                       "options": ["Option 1", "Option 2", "Option 3"]}]),
    }
    if template not in examples:
        raise SurveyError("Unknown poll starting point")
    title, description, questions = examples[template]
    poll_id = secrets.token_hex(16)
    directory = _directory(config_dir, poll_id, create=True)
    value = {
        "id": poll_id, "key": secrets.token_urlsafe(24), "title": title, "description": description,
        "status": "draft", "response_mode": "anonymous", "deadline": "", "retention_until": "",
        "questions": _normalize_questions(questions),
        "privacy": {"controller": "", "contact": "", "purpose": "", "legal_basis": "", "recipients": "", "transfers": "", "legitimate_interest": "", "dpo": "", "confirmed": False},
        "created_at": datetime.now(UTC).isoformat(),
    }
    with _locked(directory, exclusive=True):
        _atomic(directory / "poll.json", value)
        (directory / "answers.jsonl").touch(mode=0o600)
    return value


def get(config_dir: Path | str, poll_id: str) -> dict:
    directory = _directory(config_dir, poll_id)
    with _locked(directory):
        return _read_config(directory)


def list_all(config_dir: Path | str) -> list[dict]:
    result = []
    for directory in sorted(_root(config_dir).iterdir()):
        if directory.is_dir() and ID.fullmatch(directory.name) and not directory.is_symlink():
            try:
                with _locked(directory):
                    poll = _read_config(directory)
                    count = len(_read_answers(directory))
                result.append({**poll, "response_count": count})
            except SurveyError:
                # Keep corrupt polls visible to the administrator without
                # exposing their private contents or hiding unaffected polls.
                result.append({"id": directory.name, "title": "Unavailable survey — inspect private storage",
                               "status": "unavailable", "response_mode": "unknown", "deadline": "",
                               "questions": [], "response_count": "?", "created_at": ""})
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def update(config_dir: Path | str, poll_id: str, raw: Mapping[str, Any]) -> dict:
    directory = _directory(config_dir, poll_id)
    with _locked(directory, exclusive=True):
        existing = _read_config(directory)
        answers = _read_answers(directory)
        mode = str(raw.get("response_mode") or existing["response_mode"])
        if mode not in MODES:
            raise SurveyError("Invalid response mode")
        status = str(raw.get("status") or existing["status"])
        if status not in {"draft", "open", "closed"}:
            raise SurveyError("Invalid survey status")
        if existing["status"] != "draft" and status == "draft":
            raise SurveyError("A published survey cannot return to draft")
        if existing["status"] == "closed" and status == "open":
            raise SurveyError("Create a new survey instead of reopening a closed survey")
        privacy_raw = raw.get("privacy") or {}
        if not isinstance(privacy_raw, Mapping):
            raise SurveyError("Invalid privacy configuration")
        privacy = {
            "controller": _clean(privacy_raw.get("controller"), 200),
            "contact": _clean(privacy_raw.get("contact"), 200),
            "purpose": _clean(privacy_raw.get("purpose"), 1000),
            "legal_basis": _clean(privacy_raw.get("legal_basis"), 80),
            "recipients": _clean(privacy_raw.get("recipients"), 500),
            "transfers": _clean(privacy_raw.get("transfers"), 500),
            "legitimate_interest": _clean(privacy_raw.get("legitimate_interest"), 500),
            "dpo": _clean(privacy_raw.get("dpo"), 200),
            "confirmed": bool(privacy_raw.get("confirmed")),
        }
        if privacy["legal_basis"] and privacy["legal_basis"] not in BASES:
            raise SurveyError("Invalid privacy legal basis")
        deadline = _clean(raw.get("deadline"), 10)
        retention = _clean(raw.get("retention_until"), 10)
        if deadline:
            deadline = _date(deadline)
        if retention:
            retention = _date(retention)
        if deadline and retention and retention < deadline:
            raise SurveyError("Retention must be later than the closing date")
        if status == "open":
            if mode == "anonymous" and privacy["legal_basis"] == "consent":
                raise SurveyError("Anonymous surveys cannot use consent in this version: individual withdrawal is not supported")
            if not deadline or deadline < _today():
                raise SurveyError("A survey needs a future deadline")
            if retention and retention < _today():
                raise SurveyError("Retention date must not be in the past")
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", privacy["contact"]):
                raise SurveyError("Enter a valid privacy contact email")
            if not retention or not all((privacy["controller"], privacy["contact"], privacy["purpose"], privacy["legal_basis"], privacy["recipients"], privacy["transfers"], privacy["confirmed"])) or (privacy["legal_basis"] == "legitimate_interest" and not privacy["legitimate_interest"]):
                raise SurveyError("Complete and verify the privacy notice and retention date before opening")
        old_questions = existing["questions"]
        # Compare semantic question content, preserving stable IDs when unchanged.
        candidate = _normalize_questions(raw.get("questions"))
        same_questions = len(candidate) == len(old_questions) and all(
            a["type"] == b["type"] and a["question"] == b["question"] and a["required"] == b["required"]
            and a.get("style", "single_line") == b.get("style", "single_line")
            and a.get("yes_label", "Yes") == b.get("yes_label", "Yes")
            and a.get("no_label", "No") == b.get("no_label", "No")
            and [opt["label"] for opt in a.get("options", [])] == [opt["label"] for opt in b.get("options", [])]
            for a, b in zip(candidate, old_questions)
        )
        if same_questions:
            candidate = old_questions
        # Privacy can be strengthened but not silently rewritten after respondents consented / read it.
        privacy_changed = privacy != existing["privacy"] or retention != existing["retention_until"]
        if (answers or existing.get("collection_started")) and (mode != existing["response_mode"] or not same_questions or privacy_changed):
            raise SurveyError("Mode, questions and privacy are locked after the first response. Create a new survey")
        if existing["status"] != "draft" and (mode != existing["response_mode"] or not same_questions):
            raise SurveyError("Mode and questions are frozen once the survey is published")
        if existing["status"] != "draft" and (
                privacy_changed or _clean(raw.get("title"), 180) != existing["title"]
                or _clean(raw.get("description"), 4000) != existing["description"]):
            raise SurveyError("Published question context and privacy are frozen. Create a new survey")
        value = {**existing, "title": _clean(raw.get("title"), 180), "description": _clean(raw.get("description"), 4000), "status": status,
                 "response_mode": mode, "deadline": deadline, "retention_until": retention, "questions": candidate,
                 "privacy": privacy, "updated_at": datetime.now(UTC).isoformat()}
        value["privacy_version"] = (value["updated_at"] if privacy_changed else
                                    existing.get("privacy_version", existing.get("updated_at", existing["created_at"])))
        if not value["title"]:
            raise SurveyError("Survey title is required")
        _atomic(directory / "poll.json", value)
        return value


def _assert_public(poll: dict, key: str) -> None:
    if not KEY.fullmatch(key) or not secrets.compare_digest(key, poll["key"]):
        raise SurveyError("Survey not available")
    if poll["status"] != "open" or poll["deadline"] < _today():
        raise SurveyError("Survey closed or unavailable")


def public(config_dir: Path | str, poll_id: str, key: str) -> dict:
    poll = get(config_dir, poll_id)
    _assert_public(poll, key)
    return poll


def _validate_answers(poll: dict, raw: Mapping[str, Any]) -> dict:
    result = {}
    expected = {q["id"] for q in poll["questions"]}
    if set(raw) - expected:
        raise SurveyError("Unexpected answer fields")
    for q in poll["questions"]:
        val = raw.get(q["id"], [] if q["type"] == "multiple_choice" else "")
        if q["type"] == "multiple_choice":
            if not isinstance(val, list):
                raise SurveyError("Invalid multiple-choice answer")
            allowed = {opt["id"] for opt in q["options"]}
            if any(not isinstance(v, str) or v not in allowed for v in val) or len(val) != len(set(val)):
                raise SurveyError("Invalid multiple-choice option")
        elif not isinstance(val, str):
            raise SurveyError("Invalid answer")
        elif q["type"] == "text":
            val = _clean(val, 5000)
        elif q["type"] == "yes_no" and val and val not in {"yes", "no"}:
            raise SurveyError("Invalid yes/no answer")
        elif q["type"] == "date" and val:
            val = _date(val)
        elif q["type"] == "single_choice" and val not in {"", *(opt["id"] for opt in q["options"])}:
            raise SurveyError("Invalid choice")
        if q["required"] and not val:
            raise SurveyError("Please answer all required questions")
        result[q["id"]] = val
    return result


def submit(config_dir: Path | str, poll_id: str, key: str, *, request_id: str,
           answers: Mapping[str, Any], identity: Mapping[str, str] | None = None, consent: bool = False) -> bool:
    if not ID.fullmatch(request_id):
        raise SurveyError("Invalid submission ID")
    directory = _directory(config_dir, poll_id)
    with _locked(directory, exclusive=True):
        poll = _read_config(directory)
        _assert_public(poll, key)
        saved = _read_answers(directory)
        if any(row["request_id"] == request_id for row in saved):
            # A previous append may have reached the page cache but failed
            # fsync. Reconfirm durability before acknowledging its retry.
            with (directory / "answers.jsonl").open("rb") as stream:
                os.fsync(stream.fileno())
            return False  # same submission after a lost HTTP acknowledgement
        valid = _validate_answers(poll, answers)
        timestamp = datetime.now(UTC).isoformat() if poll["response_mode"] == "identified" else _today()
        record = {"request_id": request_id, "submitted_at": timestamp, "answers": valid,
                  "privacy_version": poll.get("privacy_version", poll.get("updated_at", poll["created_at"]))}
        if poll["privacy"]["legal_basis"] == "consent":
            if not consent:
                raise SurveyError("Consent is required for this particular processing purpose")
            record["consent_at"] = record["submitted_at"]
        if poll["response_mode"] == "identified":
            identity = identity or {}
            first = _clean(identity.get("first_name"), 120)
            last = _clean(identity.get("last_name"), 120)
            email = _clean(identity.get("email"), 254).lower()
            if not first or not last or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise SurveyError("Name, surname and a valid email are required")
            # Not authentication: reject obvious duplicates but never disclose
            # another person's stored identity or answers to the visitor.
            if any(row.get("email", "").casefold() == email.casefold() for row in saved):
                with (directory / "answers.jsonl").open("rb") as stream:
                    os.fsync(stream.fileno())
                return False
            record.update(first_name=first, last_name=last, email=email)
        # Persist the freeze before acknowledging any response; erasing the
        # last identified answer must never unlock the collected schema/notice.
        if not poll.get("collection_started"):
            poll["collection_started"] = True
            _atomic(directory / "poll.json", poll)
        _append(directory, record)
        return True


def results(config_dir: Path | str, poll_id: str) -> tuple[dict, list[dict]]:
    directory = _directory(config_dir, poll_id)
    with _locked(directory):
        return _read_config(directory), _read_answers(directory)


def summary(config_dir: Path | str, poll_id: str) -> dict:
    poll, rows = results(config_dir, poll_id)
    questions = []
    for q in poll["questions"]:
        counts = Counter()
        for row in rows:
            val = row["answers"].get(q["id"])
            for v in (val if isinstance(val, list) else [val]):
                if v:
                    counts[v] += 1
        questions.append({**q, "counts": dict(counts)})
    return {"responses": len(rows), "questions": questions}


def _csv_cell(value: Any) -> str:
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def export_csv(config_dir: Path | str, poll_id: str) -> bytes:
    poll, rows = results(config_dir, poll_id)
    fields = (["first_name", "last_name", "email"] if poll["response_mode"] == "identified" else []) + [q["question"] for q in poll["questions"]]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([_csv_cell(field) for field in fields])
    for row in rows:
        vals = ([row.get("first_name", ""), row.get("last_name", ""), row.get("email", "")] if poll["response_mode"] == "identified" else [])
        for q in poll["questions"]:
            val = row["answers"].get(q["id"], "")
            label_map = {x["id"]: x["label"] for x in q.get("options", [])}
            if isinstance(val, list):
                val = ", ".join(label_map.get(v, v) for v in val)
            elif q["type"] == "yes_no":
                val = {"yes": q.get("yes_label", "Yes"), "no": q.get("no_label", "No")}.get(val, val)
            elif q["type"] in {"single_choice"}:
                val = label_map.get(val, val)
            # Prevent spreadsheet formula injection on untrusted text.
            text = str(val)
            if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                text = "'" + text
            vals.append(text)
        safe_vals = []
        for v in vals:
            value = str(v)
            safe_vals.append("'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value)
        writer.writerow(safe_vals)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def delete(config_dir: Path | str, poll_id: str, *, expired_only: bool = False) -> bool:
    # Explicit administrator action; no other poll can hold a queued stale lock.
    directory = _directory(config_dir, poll_id)
    with _locked(directory, exclusive=True, deleting=True):
        if expired_only:
            expiry = _read_config(directory).get("retention_until", "")
            if not expiry or expiry >= _today():
                return False
        shutil.rmtree(directory)
        fd = os.open(directory.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return True


def erase_identified_email(config_dir: Path | str, poll_id: str, email: str) -> int:
    """Remove a respondent's data without affecting other respondents.

    Explicit admin-only action; avoids an append-only 'deletion event' because
    that would leave the original personal data in the journal.
    """
    email = _clean(email, 254).casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise SurveyError("Enter a valid email")
    directory = _directory(config_dir, poll_id)
    with _locked(directory, exclusive=True):
        poll = _read_config(directory)
        if poll["response_mode"] != "identified":
            raise SurveyError("Email erasure is available only for identified surveys")
        rows = _read_answers(directory)
        remaining = [row for row in rows if row.get("email", "").casefold() != email]
        deleted = len(rows) - len(remaining)
        if deleted:
            path = directory / "answers.jsonl"
            fd, name = tempfile.mkstemp(dir=directory, prefix=".redact-", suffix=".tmp")
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    for row in remaining:
                        stream.write((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
                dfd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return deleted


def cleanup_expired(config_dir: Path | str) -> int:
    """Delete surveys whose configured retention end date has passed.

    Run daily from the deployment scheduler. Does not erase external backups.
    """
    deleted = 0
    for directory in list(_root(config_dir).iterdir()):
        if directory.is_symlink() or not directory.is_dir() or not ID.fullmatch(directory.name):
            continue
        try:
            with _locked(directory):
                poll = _read_config(directory)
                expiry = poll.get("retention_until", "")
            if expiry and expiry < _today():
                deleted += int(delete(config_dir, directory.name, expired_only=True))
        except SurveyError:
            # An invalid/corrupt survey must be investigated, never silently purged.
            continue
    return deleted


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Remove expired JSON/JSONL surveys")
    parser.add_argument("runtime_config_dir", help="Configured persistent runtime config folder")
    args = parser.parse_args()
    print(f"Deleted {cleanup_expired(args.runtime_config_dir)} expired surveys")

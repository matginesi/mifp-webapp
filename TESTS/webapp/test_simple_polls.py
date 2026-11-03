"""Focused tests for shared-link JSONL polls (no SMTP and no recipients)."""
from __future__ import annotations

import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mifp_app.services import simple_polls as polls
from mifp_app.db.manage import init_database


def survey_config(mode="anonymous", kind="yes_no"):
    question = {"type": kind, "question": "Can you attend?", "required": True}
    if kind == "multiple_choice":
        question["options"] = ["Monday", "Tuesday"]
    return {
        "title": "Availability", "description": "Test event", "status": "open", "response_mode": mode, "notice_reviewed": True,
        "deadline": "2027-03-01", "retention_until": "2027-05-01", "questions": [question],
        "privacy": {"controller": "Example Institute", "contact": "secretary@mifp.eu", "purpose": "Coordinate dates",
                    "legal_basis": "legitimate_interest", "recipients": "Authorised admin only", "transfers": "None", "legitimate_interest": "Event scheduling and capacity planning", "dpo": ""},
    }




def test_anonymous_is_default_and_idempotent(tmp_path):
    poll = polls.create(tmp_path)
    assert poll["response_mode"] == "anonymous"
    poll = polls.update(tmp_path, poll["id"], survey_config())
    qid = poll["questions"][0]["id"]
    assert polls.submit(tmp_path, poll["id"], poll["key"], request_id="a" * 32, answers={qid: "yes"})
    assert not polls.submit(tmp_path, poll["id"], poll["key"], request_id="a" * 32, answers={qid: "yes"})
    _, rows = polls.results(tmp_path, poll["id"])
    assert len(rows) == 1
    assert "email" not in rows[0] and "first_name" not in rows[0]
    assert polls.summary(tmp_path, poll["id"])["responses"] == 1
    assert list((tmp_path / "polls_v2" / poll["id"]).iterdir())


def test_mode_and_questions_lock_after_publishing(tmp_path):
    poll = polls.create(tmp_path)
    opened = polls.update(tmp_path, poll["id"], survey_config())
    changed = survey_config(mode="identified")
    with pytest.raises(polls.SurveyError, match="frozen"):
        polls.update(tmp_path, poll["id"], changed)
    assert polls.get(tmp_path, poll["id"])["response_mode"] == "anonymous"
    changed = survey_config()
    changed["questions"][0]["question"] = "Different question"
    with pytest.raises(polls.SurveyError, match="frozen"):
        polls.update(tmp_path, poll["id"], changed)
    assert polls.get(tmp_path, poll["id"])["questions"] == opened["questions"]


def test_privacy_configuration_required_to_publish(tmp_path):
    poll = polls.create(tmp_path)
    bad = survey_config()
    bad["privacy"]["controller"] = ""
    with pytest.raises(polls.SurveyError, match="privacy"):
        polls.update(tmp_path, poll["id"], bad)
    bad = survey_config()
    bad["privacy"]["legal_basis"] = "invalid"
    with pytest.raises(polls.SurveyError, match="legal basis"):
        polls.update(tmp_path, poll['id'], bad)


def test_identified_is_self_declared_and_cannot_duplicate_email(tmp_path):
    poll = polls.create(tmp_path)
    cfg = survey_config(mode="identified")
    poll = polls.update(tmp_path, poll["id"], cfg)
    qid = poll["questions"][0]["id"]
    identity = {"first_name": "Ada", "last_name": "Lovelace", "email": "Ada@EXAMPLE.org"}
    assert polls.submit(tmp_path, poll["id"], poll["key"], request_id="b"*32, answers={qid:"no"}, identity=identity)
    assert not polls.submit(tmp_path, poll["id"], poll["key"], request_id="c"*32, answers={qid:"no"}, identity=identity)
    _, rows = polls.results(tmp_path, poll["id"])
    assert len(rows) == 1
    assert rows[0]["email"] == "ada@example.org"


def test_consent_requires_explicit_checkbox(tmp_path):
    poll = polls.create(tmp_path)
    cfg = survey_config(mode="identified")
    cfg["privacy"]["legal_basis"] = "consent"
    poll = polls.update(tmp_path, poll["id"], cfg)
    answer = {poll["questions"][0]["id"]: "yes"}
    with pytest.raises(polls.SurveyError, match="Consent"):
        polls.submit(tmp_path, poll["id"], poll["key"], request_id="d"*32, answers=answer, identity={"first_name":"A", "last_name":"B", "email":"a@example.org"})
    assert polls.submit(tmp_path, poll["id"], poll["key"], request_id="d"*32, answers=answer, identity={"first_name":"A", "last_name":"B", "email":"a@example.org"}, consent=True)


def test_concurrent_submits_and_recovery(tmp_path):
    poll = polls.create(tmp_path)
    poll = polls.update(tmp_path, poll["id"], survey_config())
    qid = poll["questions"][0]["id"]
    def send(idx):
        return polls.submit(tmp_path, poll["id"], poll["key"], request_id=f"{idx:032x}", answers={qid:"yes"})
    with ThreadPoolExecutor(max_workers=12) as executor:
        assert sum(executor.map(send, range(1, 101))) == 100
    assert polls.summary(tmp_path, poll["id"])["responses"] == 100
    assert not send(1)
    assert polls.summary(tmp_path, poll["id"])["responses"] == 100


def test_privacy_mode_public_page_and_admin_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mifp.db"))
    monkeypatch.setenv("RUNTIME_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SECRET_KEY", "polls-shared-test-secret-key")
    pytest.importorskip("werkzeug")
    pytest.importorskip("flask")
    from werkzeug.security import generate_password_hash
    from mifp_app import create_app
    init_database(tmp_path / "mifp.db")
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, DATABASE_PATH=tmp_path / "mifp.db",
                      RUNTIME_CONFIG_DIR=tmp_path / "config", ADMIN_PASSWORD_HASH=generate_password_hash("secret123"))
    poll = polls.create(app.config["RUNTIME_CONFIG_DIR"])
    poll = polls.update(app.config["RUNTIME_CONFIG_DIR"], poll["id"], survey_config())
    client = app.test_client()
    url = f"/survey/{poll['id']}/{poll['key']}"
    public_page = client.get(url)
    assert public_page.status_code == 200
    assert b"Privacy information" in public_page.data
    assert b"no name or email is requested" in public_page.data
    assert b"name=\"email\"" not in public_page.data
    assert client.get(f"/survey/{poll['id']}/{'x'*32}").status_code == 404
    assert client.get(f"/dashboard/polls-simple/{poll['id']}").status_code in {302, 401, 403}
    record_id = secrets.token_hex(16)
    qid = poll["questions"][0]["id"]
    sent = client.post(url, data={"submission_id": record_id, f"q_{qid}": "yes"})
    assert sent.status_code == 200
    sent_again = client.post(url, data={"submission_id": record_id, f"q_{qid}": "yes"})
    assert sent_again.status_code == 200
    assert polls.summary(app.config["RUNTIME_CONFIG_DIR"], poll["id"])["responses"] == 1


def test_expired_retention_can_be_cleaned_without_database(tmp_path):
    poll = polls.create(tmp_path)
    cfg = survey_config()
    cfg["status"] = "draft"
    cfg["deadline"] = "2025-01-01"
    cfg["retention_until"] = "2025-01-02"
    polls.update(tmp_path, poll["id"], cfg)
    assert polls.cleanup_expired(tmp_path) == 1
    assert not (tmp_path / "polls_v2" / poll["id"]).exists()


def test_erase_email_does_not_leave_personal_data_in_journal(tmp_path):
    poll = polls.create(tmp_path)
    poll = polls.update(tmp_path, poll["id"], survey_config(mode="identified"))
    qid = poll["questions"][0]["id"]
    identity = {"first_name": "Alice", "last_name": "Example", "email": "alice@example.org"}
    polls.submit(tmp_path, poll["id"], poll["key"], request_id="f"*32, answers={qid:"yes"}, identity=identity)
    assert polls.erase_identified_email(tmp_path, poll["id"], "alice@example.org") == 1
    data = (tmp_path / "polls_v2" / poll["id"] / "answers.jsonl").read_text()
    assert "alice@example.org" not in data and polls.summary(tmp_path, poll["id"])["responses"] == 0

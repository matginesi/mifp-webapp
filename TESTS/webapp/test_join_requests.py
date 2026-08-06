from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path: Path):
    os.environ.update(
        {
            "TESTING": "1",
            "DATABASE_PATH": str(tmp_path / "mifp.db"),
            "ASSETS_DIR": str(tmp_path / "assets"),
            "EXPORT_DIR": str(tmp_path / "exports"),
            "LOG_DIR": str(tmp_path / "logs"),
            "SECRET_KEY": "join-request-test-secret",
            "LOG_ACCESS_ENABLED": "0",
        }
    )
    from mifp_app import create_app

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        DATABASE_PATH=tmp_path / "mifp.db",
        ASSETS_DIR=tmp_path / "assets",
        EXPORT_DIR=tmp_path / "exports",
        LOG_DIR=tmp_path / "logs",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD_HASH=generate_password_hash("secret123"),
        ALLOW_DB_DUMP=True,
    )
    for key in ("ASSETS_DIR", "EXPORT_DIR", "LOG_DIR"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    from mifp_app.db.connection import connect
    from mifp_app.db.migrations import migrate_content_schema

    with connect(app.config["DATABASE_PATH"]) as conn:
        migrate_content_schema(conn)
    yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_logged_in"] = True
        session["admin_username"] = "admin"
        session["_csrf_token"] = "join-request-test-csrf"
    return client


@pytest.fixture
def anon_client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_join_rate_limit():
    from mifp_app.utils.security import reset_rate_limits

    reset_rate_limits("join")
    yield
    reset_rate_limits("join")


def _db(app) -> sqlite3.Connection:
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(app, sql: str, params: tuple = ()):
    with _db(app) as conn:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None


def _insert_join_request(app, suffix: str, status: str = "pending") -> int:
    with _db(app) as conn:
        cursor = conn.execute(
            """
            INSERT INTO join_requests(first_name,last_name,email,affiliation,country,field,motivation,status)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            ("Ada", suffix, f"ada-{suffix.lower()}@example.org", "MIFP", "Italy", "Physics", "Testing", status),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _public_join_payload(email: str = "grace.hopper@example.org", **overrides) -> dict[str, str]:
    payload = {
        "first_name": " Grace ",
        "last_name": " Hopper ",
        "email": email,
        "affiliation": " MIFP Lab ",
        "country": " United States ",
        "field": " Mathematical physics ",
        "position": " Researcher ",
        "motivation": "I would like to contribute to the community.",
        "website": "",
    }
    payload.update(overrides)
    return payload


class TestPublicJoinRequestSubmission:

    def test_public_form_creates_normalized_pending_request(self, app, anon_client):
        response = anon_client.post("/join", data=_public_join_payload("GRACE.HOPPER@EXAMPLE.ORG"))
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Thank you for your interest in MIFP." in body
        with _db(app) as conn:
            row = conn.execute("SELECT * FROM join_requests").fetchone()
            assert row is not None
            assert row["first_name"] == "Grace"
            assert row["last_name"] == "Hopper"
            assert row["email"] == "grace.hopper@example.org"
            assert row["affiliation"] == "MIFP Lab"
            assert row["position"] == "Researcher"
            assert row["status"] == "pending"

    def test_invalid_email_is_rejected_without_creating_request(self, app, anon_client):
        response = anon_client.post("/join", data=_public_join_payload("not-an-email"))
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "valid email" in body.lower()
        assert "Grace" in body
        assert _scalar(app, "SELECT COUNT(*) FROM join_requests") == 0

    def test_duplicate_open_request_is_rejected(self, app, anon_client):
        first = anon_client.post("/join", data=_public_join_payload())
        second = anon_client.post(
            "/join",
            data=_public_join_payload(first_name="Amazing", last_name="Grace"),
        )

        assert first.status_code == 200
        assert "already pending review" in second.get_data(as_text=True)
        assert _scalar(app, "SELECT COUNT(*) FROM join_requests") == 1

    def test_honeypot_submission_looks_successful_but_is_not_persisted(self, app, anon_client):
        response = anon_client.post(
            "/join",
            data=_public_join_payload(website="https://spam.invalid"),
        )

        assert response.status_code == 200
        assert "Thank you for your interest in MIFP." in response.get_data(as_text=True)
        assert _scalar(app, "SELECT COUNT(*) FROM join_requests") == 0

    def test_rate_limit_blocks_excess_submissions(self, app, anon_client):
        app.config["JOIN_MAX_PER_IP_HOUR"] = 2

        for index in range(2):
            response = anon_client.post(
                "/join",
                data=_public_join_payload(f"candidate-{index}@example.org"),
            )
            assert "Thank you for your interest in MIFP." in response.get_data(as_text=True)

        blocked = anon_client.post(
            "/join",
            data=_public_join_payload("candidate-3@example.org"),
        )

        assert blocked.status_code == 200
        assert "too many" in blocked.get_data(as_text=True).lower()
        assert _scalar(app, "SELECT COUNT(*) FROM join_requests") == 2

    def test_public_request_is_visible_and_actionable_in_dashboard(self, app, anon_client, client):
        submitted = anon_client.post(
            "/join",
            data=_public_join_payload("workflow@example.org", first_name="Emmy", last_name="Noether"),
        )
        assert submitted.status_code == 200

        inbox = client.get("/dashboard/join-requests?status=pending")
        assert inbox.status_code == 200
        assert "Emmy Noether" in inbox.get_data(as_text=True)

        request_id = _scalar(app, "SELECT id FROM join_requests WHERE email=?", ("workflow@example.org",))
        approved = client.post(
            f"/dashboard/join-requests/{request_id}/approve",
            data={"create_member": "1"},
        )

        assert approved.status_code == 302
        assert _scalar(app, "SELECT status FROM join_requests WHERE id=?", (request_id,)) == "approved"
        assert _scalar(app, "SELECT COUNT(*) FROM members WHERE email=?", ("workflow@example.org",)) == 1


class TestJoinRequestsList:

    def test_join_requests_list(self, app, client):
        _insert_join_request(app, "One")
        _insert_join_request(app, "Two")

        response = client.get("/dashboard/join-requests")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Ada One" in body
        assert "Ada Two" in body
        assert "No join requests found" not in body

    def test_join_requests_list_empty(self, app, client):
        response = client.get("/dashboard/join-requests")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "No join requests found" in body

    def test_join_requests_filter_by_status(self, app, client):
        _insert_join_request(app, "PendingA", status="pending")
        _insert_join_request(app, "PendingB", status="pending")
        _insert_join_request(app, "ApprovedX", status="approved")
        _insert_join_request(app, "RejectedY", status="rejected")
        _insert_join_request(app, "ArchivedZ", status="archived")

        pending = client.get("/dashboard/join-requests?status=pending")
        assert pending.status_code == 200
        pending_body = pending.get_data(as_text=True)
        assert "Ada PendingA" in pending_body
        assert "Ada PendingB" in pending_body
        assert "Ada ApprovedX" not in pending_body
        assert "Ada RejectedY" not in pending_body

        approved = client.get("/dashboard/join-requests?status=approved")
        assert approved.status_code == 200
        approved_body = approved.get_data(as_text=True)
        assert "Ada ApprovedX" in approved_body
        assert "Ada PendingA" not in approved_body

        rejected = client.get("/dashboard/join-requests?status=rejected")
        assert rejected.status_code == 200
        rejected_body = rejected.get_data(as_text=True)
        assert "Ada RejectedY" in rejected_body
        assert "Ada PendingA" not in rejected_body

        archived = client.get("/dashboard/join-requests?status=archived")
        assert archived.status_code == 200
        archived_body = archived.get_data(as_text=True)
        assert "Ada ArchivedZ" in archived_body
        assert "Ada PendingA" not in archived_body

    def test_join_requests_invalid_status_filter(self, app, client):
        _insert_join_request(app, "Visible")

        response = client.get("/dashboard/join-requests?status=nonexistent")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Ada Visible" in body

    def test_join_requests_search_by_name(self, app, client):
        _insert_join_request(app, "Alice")
        _insert_join_request(app, "Bob")

        response = client.get("/dashboard/join-requests?q=Ali")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Ada Alice" in body
        assert "Ada Bob" not in body

    def test_join_requests_search_by_email(self, app, client):
        _insert_join_request(app, "SearchMe")

        response = client.get("/dashboard/join-requests?q=searchme@")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Ada SearchMe" in body

    def test_join_requests_search_by_affiliation(self, app, client):
        with _db(app) as conn:
            conn.execute(
                """
                INSERT INTO join_requests(first_name,last_name,email,affiliation,country,field,motivation)
                VALUES('Bob','Marley','bob@example.org','Custom Corp','Italy','Maths','Search test')
                """
            )
            conn.commit()

        response = client.get("/dashboard/join-requests?q=Custom")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Bob Marley" in body

    def test_join_requests_search_no_match(self, app, client):
        _insert_join_request(app, "Only")

        response = client.get("/dashboard/join-requests?q=ZZZZNOMATCH")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "No join requests found" in body

    def test_join_requests_search_with_status_filter(self, app, client):
        _insert_join_request(app, "Match", status="approved")
        _insert_join_request(app, "Other", status="pending")

        response = client.get("/dashboard/join-requests?q=Match&status=approved")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Ada Match" in body
        assert "Ada Other" not in body

    def test_join_requests_pagination(self, app, client):
        for i in range(25):
            _insert_join_request(app, f"User{i:02d}")

        page1 = client.get("/dashboard/join-requests")
        assert page1.status_code == 200
        page1_body = page1.get_data(as_text=True)
        assert "Ada User00" in page1_body
        assert "Ada User19" in page1_body
        assert "Ada User20" not in page1_body
        assert "Page 1 of 2" in page1_body

        page2 = client.get("/dashboard/join-requests?page=2")
        assert page2.status_code == 200
        page2_body = page2.get_data(as_text=True)
        assert "Ada User20" in page2_body
        assert "Ada User24" in page2_body
        assert "Ada User00" not in page2_body
        assert "Page 2 of 2" in page2_body

    def test_join_requests_pagination_out_of_range_clamps(self, app, client):
        for i in range(5):
            _insert_join_request(app, f"U{i}")

        page_neg = client.get("/dashboard/join-requests?page=-1")
        assert page_neg.status_code == 200

        page_high = client.get("/dashboard/join-requests?page=999")
        assert page_high.status_code == 200


class TestJoinRequestActions:

    def test_join_request_approve_with_member_creation(self, app, client):
        req_id = _insert_join_request(app, "ApproveWithMember")

        response = client.post(
            f"/dashboard/join-requests/{req_id}/approve",
            data={"create_member": "1"},
        )

        assert response.status_code == 302
        with _db(app) as conn:
            row = conn.execute("SELECT status, member_id FROM join_requests WHERE id=?", (req_id,)).fetchone()
            assert row["status"] == "approved"
            assert row["member_id"] is not None
            member = conn.execute("SELECT * FROM members WHERE id=?", (row["member_id"],)).fetchone()
            assert member is not None
            assert member["first_name"] == "Ada"
            assert member["last_name"] == "ApproveWithMember"
            assert member["email"] == "ada-approvewithmember@example.org"
            assert member["is_active"] == 1

    def test_join_request_approve_without_member(self, app, client):
        req_id = _insert_join_request(app, "ApproveNoMember")

        response = client.post(f"/dashboard/join-requests/{req_id}/approve")

        assert response.status_code == 302
        with _db(app) as conn:
            row = conn.execute("SELECT status, member_id FROM join_requests WHERE id=?", (req_id,)).fetchone()
            assert row["status"] == "approved"
            assert row["member_id"] is None
            assert _scalar(app, "SELECT COUNT(*) FROM members WHERE last_name='ApproveNoMember'") == 0

    def test_join_request_reject_with_note(self, app, client):
        req_id = _insert_join_request(app, "RejectWithNote")

        response = client.post(
            f"/dashboard/join-requests/{req_id}/reject",
            data={"decision_note": "Not enough experience"},
        )

        assert response.status_code == 302
        with _db(app) as conn:
            row = conn.execute("SELECT status, decision_note FROM join_requests WHERE id=?", (req_id,)).fetchone()
            assert row["status"] == "rejected"
            assert row["decision_note"] == "Not enough experience"

    def test_join_request_reject_without_note(self, app, client):
        req_id = _insert_join_request(app, "RejectNoNote")

        response = client.post(f"/dashboard/join-requests/{req_id}/reject")

        assert response.status_code == 302
        with _db(app) as conn:
            row = conn.execute("SELECT status, decision_note FROM join_requests WHERE id=?", (req_id,)).fetchone()
            assert row["status"] == "rejected"
            assert row["decision_note"] is None

    def test_join_request_reject_with_empty_note(self, app, client):
        req_id = _insert_join_request(app, "RejectEmptyNote")

        response = client.post(
            f"/dashboard/join-requests/{req_id}/reject",
            data={"decision_note": ""},
        )

        assert response.status_code == 302
        assert _scalar(app, "SELECT decision_note FROM join_requests WHERE id=?", (req_id,)) is None

    def test_join_request_archive(self, app, client):
        req_id = _insert_join_request(app, "ArchiveMe")

        response = client.post(f"/dashboard/join-requests/{req_id}/archive")

        assert response.status_code == 302
        assert _scalar(app, "SELECT status FROM join_requests WHERE id=?", (req_id,)) == "archived"

    def test_join_request_delete(self, app, client):
        req_id = _insert_join_request(app, "DeleteMe")

        response = client.post(f"/dashboard/join-requests/{req_id}/delete")

        assert response.status_code == 302
        assert _scalar(app, "SELECT COUNT(*) FROM join_requests WHERE id=?", (req_id,)) == 0

    def test_join_request_update_notes_and_status(self, app, client):
        req_id = _insert_join_request(app, "UpdateMe")

        response = client.post(
            f"/dashboard/join-requests/{req_id}/update",
            data={"status": "in_review", "admin_notes": "Checking credentials", "decision_note": "Pending"},
        )

        assert response.status_code == 302
        with _db(app) as conn:
            row = conn.execute("SELECT status, admin_notes, decision_note FROM join_requests WHERE id=?", (req_id,)).fetchone()
            assert row["status"] == "in_review"
            assert row["admin_notes"] == "Checking credentials"
            assert row["decision_note"] == "Pending"

    def test_join_request_update_only_admin_notes(self, app, client):
        req_id = _insert_join_request(app, "NotesOnly")

        response = client.post(
            f"/dashboard/join-requests/{req_id}/update",
            data={"admin_notes": "Some internal note"},
        )

        assert response.status_code == 302
        assert _scalar(app, "SELECT admin_notes FROM join_requests WHERE id=?", (req_id,)) == "Some internal note"

    def test_join_request_invalid_id_returns_404(self, app, client):
        response = client.post("/dashboard/join-requests/not-an-int/update")
        assert response.status_code == 404

        response = client.post("/dashboard/join-requests/not-an-int/approve")
        assert response.status_code == 404

        response = client.post("/dashboard/join-requests/not-an-int/reject")
        assert response.status_code == 404

        response = client.post("/dashboard/join-requests/not-an-int/archive")
        assert response.status_code == 404

        response = client.post("/dashboard/join-requests/not-an-int/delete")
        assert response.status_code == 404

    def test_join_request_nonexistent_id_redirects_gracefully(self, app, client):
        missing = 99999
        assert _scalar(app, "SELECT COUNT(*) FROM join_requests WHERE id=?", (missing,)) == 0

        response = client.post(f"/dashboard/join-requests/{missing}/update", data={"status": "in_review"})
        assert response.status_code == 302

        response = client.post(f"/dashboard/join-requests/{missing}/approve")
        assert response.status_code == 302

        response = client.post(f"/dashboard/join-requests/{missing}/reject", data={"decision_note": "No"})
        assert response.status_code == 302

        response = client.post(f"/dashboard/join-requests/{missing}/archive")
        assert response.status_code == 302

        response = client.post(f"/dashboard/join-requests/{missing}/delete")
        assert response.status_code == 302

    def test_join_request_requires_login(self, app, anon_client):
        req_id = _insert_join_request(app, "NoLogin")

        response = anon_client.get("/dashboard/join-requests")
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")

        for action in ("update", "approve", "reject", "archive", "delete"):
            response = anon_client.post(f"/dashboard/join-requests/{req_id}/{action}")
            assert response.status_code == 302
            assert response.headers["Location"].startswith("/login")

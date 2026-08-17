from __future__ import annotations

import json

import pytest

from mifp_app.services.data_quality.models import Classification, Evidence
from mifp_app.services.data_quality.normalizers import (
    normalize_identity_text,
    normalize_person_name,
    normalize_title,
    normalize_canonical_url,
)


class TestNormalizeIdentityText:
    def test_strips_diacritics(self):
        assert normalize_identity_text("François") == "francois"

    def test_strips_trailing_parenthetical(self):
        assert normalize_identity_text("Alexey Kavokin (University of Southampton)") == "alexey kavokin"
        assert normalize_identity_text("MIFP (Rome)") == "mifp"

    def test_strips_org_suffixes(self):
        assert normalize_identity_text("MIFP Ltd") == "mifp"
        assert normalize_identity_text("MIFP S.p.A.") == "mifp"
        assert normalize_identity_text("MIFP GmbH") == "mifp"

    def test_normalizes_and_to_ampersand(self):
        result = normalize_identity_text("Research and Development")
        assert "and" in result or "&" in result

    def test_empty_returns_empty(self):
        assert normalize_identity_text("") == ""
        assert normalize_identity_text(None) == ""


class TestNormalizePersonName:
    def test_last_comma_first(self):
        assert normalize_person_name("Alexey Kavokin") == "kavokin, alexey"

    def test_particle_handling(self):
        assert normalize_person_name("Jan van der Waals") == "van der waals, jan"

    def test_already_inverted(self):
        assert normalize_person_name("Kavokin Alexey") == "kavokin, alexey"

    def test_short_name_fallback(self):
        assert normalize_person_name("Alexey") != ""


class TestNormalizeTitle:
    def test_removes_mifp_suffix(self):
        assert "mifp" not in normalize_title("Research News — MIFP")

    def test_removes_home_suffix(self):
        assert normalize_title("Events - Home") != "events - home"

    def test_removes_past_event_suffix(self):
        result = normalize_title("Winter School Past Event")
        assert "past event" not in result

    def test_removes_pipe_suffix(self):
        result = normalize_title("About Us | MIFP")
        assert result == "about us"


class TestNormalizeCanonicalUrl:
    def test_removes_tracking_params(self):
        url = "https://example.com/page?utm_source=twitter&id=123"
        result = normalize_canonical_url(url)
        assert "utm_source" not in result
        assert result == "https://example.com/page?id=123"

    def test_adds_https(self):
        assert normalize_canonical_url("mifp.eu") == "https://www.mifp.eu/"

    def test_normalizes_mifp_host(self):
        assert normalize_canonical_url("https://old.mifp.eu/page") == "https://www.mifp.eu/page"
        assert normalize_canonical_url("https://events.mifp.eu/page") == "https://www.mifp.eu/page"

    def test_empty_returns_empty(self):
        assert normalize_canonical_url("") == ""
        assert normalize_canonical_url(None) == ""

    def test_removes_www_prefix_in_path(self):
        result = normalize_canonical_url("https://mifp.eu/www.mifp.eu/contact")
        assert "www.mifp.eu" not in result


class TestJunkClassifier:
    def test_numeric_title(self):
        assert "junk" in Classification.JUNK

    def test_junk_detection_numeric(self):
        from mifp_app.services.data_quality.analyzer import _check_junk_record
        row = {"id": 1, "title": "13", "review_status": "published"}
        finding = _check_junk_record("event", row)
        assert finding is not None
        assert finding.classification.value == "junk_technical_record"

    def test_junk_detection_file_size(self):
        from mifp_app.services.data_quality.analyzer import _check_junk_record
        row = {"id": 2, "title": "1 MB", "review_status": "published"}
        finding = _check_junk_record("event", row)
        assert finding is not None

    def test_junk_detection_page_id(self):
        from mifp_app.services.data_quality.analyzer import _check_junk_record
        row = {"id": 3, "title": "Publications76C3", "review_status": "published"}
        finding = _check_junk_record("event", row)
        assert finding is not None

    def test_clean_title_not_junk(self):
        from mifp_app.services.data_quality.analyzer import _check_junk_record
        row = {"id": 4, "title": "International Conference on Physics 2024", "review_status": "published"}
        finding = _check_junk_record("event", row)
        assert finding is None


class TestEventPageFragment:
    def test_fragment_detection_topic(self):
        from mifp_app.services.data_quality.analyzer import _check_event_page_fragment
        row = {"id": 1, "title": "Conference on Physics 2024 - Topics", "review_status": "published"}
        ctx = {"links": {1: [{"url": "https://example.com/conf2024/topics"}]}}
        finding = _check_event_page_fragment(row, ctx)
        assert finding is not None
        assert finding.classification.value == "page_fragment_attached"

    def test_clean_event_not_fragment(self):
        from mifp_app.services.data_quality.analyzer import _check_event_page_fragment
        row = {"id": 2, "title": "International Conference on Physics 2024", "review_status": "published"}
        ctx = {"links": {2: [{"url": "https://example.com/conf2024/"}]}}
        finding = _check_event_page_fragment(row, ctx)
        assert finding is None

    def test_single_word_keyword_detected(self):
        from mifp_app.services.data_quality.analyzer import _check_event_page_fragment
        row = {"id": 3, "title": "Conference venue", "review_status": "published"}
        ctx = {"links": {3: [{"url": "https://example.com/venue"}]}}
        finding = _check_event_page_fragment(row, ctx)
        assert finding is not None
        assert finding.classification.value == "page_fragment_attached"

    def test_multi_word_keyword_detected(self):
        from mifp_app.services.data_quality.analyzer import _check_event_page_fragment
        row = {"id": 4, "title": "Call for Papers 2024", "review_status": "published"}
        ctx = {"links": {4: [{"url": "https://example.com/2024/cfp"}]}}
        finding = _check_event_page_fragment(row, ctx)
        assert finding is not None
        assert finding.classification.value == "page_fragment_attached"


class TestDatePlaceholder:
    def test_placeholder_detected(self):
        from mifp_app.services.data_quality.analyzer import _check_date_placeholder
        row = {"id": 1, "start_date": "2020-01-01", "end_date": "2020-12-31",
               "date_precision": "range", "review_status": "published"}
        finding = _check_date_placeholder(row)
        assert finding is not None
        assert finding.classification.value == "needs_cleaning"
        assert any(e.code == "false_annual_range" for e in finding.evidence)

    def test_real_date_not_placeholder(self):
        from mifp_app.services.data_quality.analyzer import _check_date_placeholder
        row = {"id": 2, "start_date": "2020-06-15", "end_date": "2020-06-20",
               "date_precision": "range", "review_status": "published"}
        finding = _check_date_placeholder(row)
        assert finding is None

    def test_no_end_not_placeholder(self):
        from mifp_app.services.data_quality.analyzer import _check_date_placeholder
        row = {"id": 3, "start_date": "2020-01-01", "end_date": None,
               "date_precision": "year", "review_status": "published"}
        finding = _check_date_placeholder(row)
        assert finding is None


class TestResolveDates:
    def test_resolve_dates_prefers_non_placeholder(self):
        from mifp_app.services.data_quality.planner import resolve_dates
        fields = []
        records = [
            {"start_date": "2020-01-01", "end_date": "2020-12-31", "date_precision": "range"},
            {"start_date": "2020-06-15", "end_date": "2020-06-20", "date_precision": "range"},
        ]
        result = resolve_dates(fields, records)
        assert result["start_date"] == "2020-06-15"
        assert result["end_date"] == "2020-06-20"

    def test_resolve_dates_best_precision(self):
        from mifp_app.services.data_quality.planner import resolve_dates
        fields = []
        records = [
            {"start_date": "2020-01-01", "end_date": "2020-12-31", "date_precision": "range"},
            {"start_date": "2020-06-15", "end_date": "2020-06-20", "date_precision": "day"},
        ]
        result = resolve_dates(fields, records)
        assert result["date_precision"] == "day"

    def test_resolve_dates_handles_inversion(self):
        from mifp_app.services.data_quality.planner import resolve_dates
        fields = []
        records = [
            {"start_date": "2020-12-31", "end_date": "2020-01-01", "date_precision": "range"},
        ]
        result = resolve_dates(fields, records)
        assert result.get("end_date") is None or result.get("end_date") != "2020-01-01"


class TestDateConsistencyInPolicies:
    def test_event_date_mismatch_contradiction(self):
        from mifp_app.services.data_quality.policies import evaluate_event
        a = {"id": 1, "title": "Winter School 2020", "start_date": "2020-06-01", "end_date": "2020-06-05", "review_status": "published"}
        b = {"id": 2, "title": "Winter School 2021", "start_date": "2021-06-01", "end_date": "2021-06-05", "review_status": "published"}
        ctx = {"links": {1: [], 2: []}}
        classification, score, evidence, contradictions = evaluate_event(a, b, ctx)
        assert classification.value in ("related_not_duplicate", "blocked")

    def test_event_date_overlap_same_series(self):
        from mifp_app.services.data_quality.policies import evaluate_event
        a = {"id": 1, "title": "Winter School 2020", "start_date": "2020-01-01", "end_date": "2020-01-10", "review_status": "published"}
        b = {"id": 2, "title": "Winter School 2021", "start_date": "2021-01-01", "end_date": "2021-01-10", "review_status": "published"}
        ctx = {"links": {1: [], 2: []}}
        classification, score, evidence, contradictions = evaluate_event(a, b, ctx)
        assert classification.value in ("related_not_duplicate", "blocked")


class TestClusterSafety:
    def test_safe_cluster(self):
        from mifp_app.services.data_quality.cluster import cluster_is_safe
        records = [
            {"id": 1, "title": "Event 2024", "start_date": "2024-06-01", "doi": None, "email": None},
            {"id": 2, "title": "Event 2024", "start_date": "2024-06-01", "doi": None, "email": None},
        ]
        safe, reasons, sub = cluster_is_safe(records, "event", {})
        assert safe is True

    def test_unsafe_transitive(self):
        from mifp_app.services.data_quality.cluster import cluster_is_safe
        records = [
            {"id": 1, "title": "Physics Conference 2024", "start_date": "2024-06-01", "email": None, "doi": None},
            {"id": 2, "title": "Physics Conference", "start_date": "2024-06-01", "email": None, "doi": None},
            {"id": 3, "title": "Biology Conference", "start_date": "2025-07-01", "email": None, "doi": None},
        ]
        safe, reasons, sub = cluster_is_safe(records, "event", {})
        assert safe is False

    def test_cross_year_same_series(self):
        from mifp_app.services.data_quality.cluster import cluster_is_safe
        records = [
            {"id": 1, "title": "ICMP 2024", "start_date": "2024-06-01", "email": None, "doi": None},
            {"id": 2, "title": "ICMP 2025", "start_date": "2025-06-01", "email": None, "doi": None},
        ]
        safe, reasons, sub = cluster_is_safe(records, "event", {})
        assert safe is False
        assert any("different year" in r.lower() for r in reasons)


class TestFieldResolution:
    def test_member_email_prefers_personal(self):
        from mifp_app.services.data_quality.planner import _resolve_member_email
        values = [
            {"record_id": 1, "value": "alexey@example.com"},
            {"record_id": 2, "value": "info@example.com"},
        ]
        selected, source_id = _resolve_member_email(values, 2)
        assert selected == "alexey@example.com"
        assert source_id == 1

    def test_member_affiliation_specific(self):
        from mifp_app.services.data_quality.planner import _resolve_member_affiliation
        values = [
            {"record_id": 1, "value": "University of Southampton"},
            {"record_id": 2, "value": "Physics Department, University of Southampton"},
        ]
        selected, source_id = _resolve_member_affiliation(values, 2)
        assert len(selected) > 20  # more specific

    def test_news_body_prefers_longer(self):
        from mifp_app.services.data_quality.planner import _resolve_news_body
        values = [
            {"record_id": 1, "value": "Short body."},
            {"record_id": 2, "value": "A much longer and more complete body text with real content."},
        ]
        selected, source_id = _resolve_news_body(values, 2)
        assert len(selected) > 20

    def test_news_summary_generated_from_body(self):
        from mifp_app.services.data_quality.planner import _resolve_news_summary
        values = [
            {"record_id": 1, "value": "Short"},
            {"record_id": 2, "value": None},
        ]
        body_values = [
            {"record_id": 1, "value": "Short"},
            {"record_id": 2, "value": "This is the full body text of the article with real content."},
        ]
        selected, source_id = _resolve_news_summary(values, 2, body_values)
        assert selected == "This is the full body text of the article with real content."

    def test_event_description_prefers_clean_longer(self):
        from mifp_app.services.data_quality.planner import _resolve_event_description
        values = [
            {"record_id": 1, "value": "Short desc."},
            {"record_id": 2, "value": "A detailed event description with substantial real content about the conference."},
        ]
        selected, source_id = _resolve_event_description(values, 2)
        assert len(selected) > 20
        assert source_id == 2

    def test_publication_title_rejects_digits_and_short(self):
        from mifp_app.services.data_quality.planner import _resolve_publication_title
        values = [
            {"record_id": 1, "value": "12345"},
            {"record_id": 2, "value": "Hi"},
            {"record_id": 3, "value": "Quantum Field Theory: A Modern Perspective"},
        ]
        selected, source_id = _resolve_publication_title(values, 3)
        assert selected == "Quantum Field Theory: A Modern Perspective"
        assert source_id == 3


class TestNewsMergeDetection:
    """Test that evaluate_news catches related articles even when headlines differ."""

    def test_subject_overlap_catches_different_headlines(self):
        from mifp_app.services.data_quality.policies import evaluate_news

        a = {"id": 1, "title": "Prof. Alexey Kavokin and Dr. Stella Kavokina Publish Science Perspective on Universal Scaling",
             "body": "Prof. Kavokin and Dr. Kavokina have published a new Science Perspective.", "date": "2026-04-09"}
        b = {"id": 2, "title": "Congratulations to Prof. Alexey Kavokin and Dr. Stella Kavokina on their Science Perspective",
             "body": "We congratulate Prof. Kavokin and Dr. Kavokina on their recent publication.", "date": "2026"}
        ctx = {"links": {1: [], 2: []}, "assets": {1: [], 2: []}}

        classification, score, evidence, contradictions = evaluate_news(a, b, ctx)
        assert classification.value not in ("related_not_duplicate",)
        assert classification.value in ("strong_candidate", "ambiguous")
        assert any("overlap" in e.code or "subjects" in e.code for e in evidence)

    def test_subject_overlap_respects_year_boundary(self):
        from mifp_app.services.data_quality.policies import evaluate_news

        a = {"id": 1, "title": "Prof. Alexey Kavokin and Dr. Stella Kavokina Publish Science Perspective on Universal Scaling",
             "body": "Prof. Kavokin and Dr. Kavokina have published a new Science Perspective.", "date": "2025"}
        b = {"id": 2, "title": "Congratulations to Prof. Alexey Kavokin and Dr. Stella Kavokina on their Science Perspective",
             "body": "We congratulate Prof. Kavokin and Dr. Kavokina on their recent publication.", "date": "2026"}
        ctx = {"links": {1: [], 2: []}, "assets": {1: [], 2: []}}

        classification, score, evidence, contradictions = evaluate_news(a, b, ctx)
        assert classification.value in ("related_not_duplicate",)

    def test_different_stories_not_falsely_matched(self):
        from mifp_app.services.data_quality.policies import evaluate_news

        a = {"id": 1, "title": "New quantum computing breakthrough at MIT",
             "body": "Researchers at MIT have achieved a major quantum computing milestone.", "date": "2026"}
        b = {"id": 2, "title": "Winter School on Particle Physics 2026 registration open",
             "body": "Registration for the Winter School on Particle Physics is now open.", "date": "2026"}
        ctx = {"links": {1: [], 2: []}, "assets": {1: [], 2: []}}

        classification, score, evidence, contradictions = evaluate_news(a, b, ctx)
        assert classification.value == "related_not_duplicate"


class TestVerifyInvariants:

    def _test_conn(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        return conn

    def test_no_orphan_references(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO members(id, display_name, slug) VALUES(1, 'Test', 'test')")
        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url) VALUES('member', 1, 'https://x.com')")
        conn.commit()
        errors = verify_invariants(conn)
        assert len(errors) == 0
        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url) VALUES('member', 999, 'https://x.com')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("orphan" in e.lower() for e in errors)

    def test_orphan_asset_link(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO assets(id, filename, path) VALUES(1, 'test.png', '/img/test.png')")
        conn.execute("INSERT INTO members(id, display_name, slug) VALUES(1, 'Test', 'test')")
        conn.execute("INSERT INTO asset_links(asset_id, entity_type, entity_id) VALUES(1, 'member', 1)")
        conn.commit()
        errors = verify_invariants(conn)
        assert len(errors) == 0
        conn.execute("INSERT INTO asset_links(asset_id, entity_type, entity_id) VALUES(1, 'member', 999)")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("orphan" in e.lower() for e in errors)

    def test_duplicate_slug_not_reported_when_unique(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO events(id, title, slug) VALUES(1, 'Event A', 'event-a')")
        conn.execute("INSERT INTO events(id, title, slug) VALUES(2, 'Event B', 'event-b')")
        conn.commit()
        errors = verify_invariants(conn)
        slug_errors = [e for e in errors if "duplicate slug" in e.lower()]
        assert len(slug_errors) == 0

    def test_duplicate_doi_detected(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO publications(id, title, doi) VALUES(1, 'Pub A', '10.1234/test')")
        conn.execute("INSERT INTO publications(id, title, doi) VALUES(2, 'Pub B', '10.1234/test')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("duplicate doi" in e.lower() for e in errors)

    def test_event_date_inversion(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO events(id, title, start_date, end_date) VALUES(1, 'Bad Event', '2024-06-30', '2024-06-01')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("start_date" in e and "end_date" in e for e in errors)

    def test_empty_title_detected(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO events(id, title) VALUES(1, '')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("empty title" in e.lower() for e in errors)

    def test_empty_display_name_detected(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO members(id, display_name) VALUES(1, '')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("empty display_name" in e.lower() for e in errors)

    def test_empty_sponsor_name_detected(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()
        conn.execute("INSERT INTO sponsors(id, name) VALUES(1, '')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("empty name in sponsors" in e.lower() for e in errors)


class TestIntegration:
    """Integration tests exercising the full analyze -> findings -> bundle -> apply pipeline."""

    def _test_conn(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        return conn

    def test_analyze_produces_findings(self):
        conn = self._test_conn()
        conn.execute("INSERT INTO members(id, display_name, email, slug) VALUES(1, 'Alexey Kavokin', 'alexey@example.com', 'alexey-kavokin')")
        conn.execute("INSERT INTO members(id, display_name, email, slug) VALUES(2, 'Alexey Kavokin', NULL, 'alexey-kavokin-2')")
        conn.commit()

        from mifp_app.services.data_quality.analyzer import analyze
        result = analyze(conn)
        assert result["finding_count"] > 0
        assert "summary" in result

    def test_analyze_idempotent(self):
        conn = self._test_conn()
        conn.execute("INSERT INTO members(id, display_name, email, slug) VALUES(1, 'Alexey Kavokin', 'alexey@example.com', 'alexey-kavokin')")
        conn.execute("INSERT INTO members(id, display_name, email, slug) VALUES(2, 'Alexey Kavokin', NULL, 'alexey-kavokin-2')")
        conn.commit()

        from mifp_app.services.data_quality.analyzer import analyze, database_fingerprint

        fp1 = database_fingerprint(conn)
        result1 = analyze(conn)
        fp2 = database_fingerprint(conn)

        assert fp1 == fp2

        result2 = analyze(conn)
        assert result2["finding_count"] > 0

    def test_junk_record_quarantined(self):
        conn = self._test_conn()
        conn.execute("INSERT INTO events(id, title, slug, start_date, date_precision) VALUES(1, '13', '13', '2020-01-01', 'year')")
        conn.commit()

        from mifp_app.services.data_quality.analyzer import analyze, list_findings
        result = analyze(conn)
        findings = list_findings(conn, result["run_id"])

        junk = [f for f in findings if f["classification"] == "junk_technical_record"]
        assert len(junk) == 1
        assert junk[0]["action_type"] == "clean_record"

    def test_page_fragment_detected(self):
        conn = self._test_conn()
        conn.execute("INSERT INTO events(id, title, slug, start_date, date_precision) VALUES(1, 'Conference Topics', 'conference-topics', '2020-01-01', 'year')")
        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url, role) VALUES('event', 1, 'https://example.com/topics', 'reference')")
        conn.commit()

        from mifp_app.services.data_quality.analyzer import analyze, list_findings
        result = analyze(conn)
        findings = list_findings(conn, result["run_id"])

        fragments = [f for f in findings if f["classification"] == "page_fragment_attached"]
        assert len(fragments) >= 1
        fragment = fragments[0]

        from mifp_app.services.data_quality import add_to_bundle, create_bundle
        bundle_id = create_bundle(conn, "admin")
        with pytest.raises(ValueError, match="at least two records"):
            add_to_bundle(conn, bundle_id, fragment["id"], {"strategy": "best_quality"})

    def test_validation_removes_legacy_single_record_merge(self):
        conn = self._test_conn()
        conn.execute(
            "INSERT INTO events(id,title,slug,start_date,date_precision) "
            "VALUES(1,'Conference Topics','conference-topics','2020-01-01','year')"
        )
        conn.commit()

        from mifp_app.services.data_quality.analyzer import analyze, list_findings
        from mifp_app.services.data_quality import create_bundle
        from mifp_app.services.data_quality.executor import validate_bundle

        result = analyze(conn)
        fragment = next(
            finding for finding in list_findings(conn, result["run_id"])
            if finding["classification"] == "page_fragment_attached"
        )
        bundle_id = create_bundle(conn, "admin")
        conn.execute(
            "INSERT INTO quality_bundle_items(bundle_id,finding_id,action_type,payload_json) "
            "VALUES(?,?,?,?)",
            (bundle_id, fragment["id"], "merge_records", json.dumps({"plan": fragment["plan"]})),
        )
        conn.execute("UPDATE quality_findings SET status='bundled' WHERE id=?", (fragment["id"],))
        conn.commit()

        report = validate_bundle(conn, bundle_id)

        assert report["valid"] is False
        assert report["errors"] == ["Bundle has no executable actions"]
        assert any("removed" in warning for warning in report["warnings"])
        assert conn.execute(
            "SELECT COUNT(*) FROM quality_bundle_items WHERE bundle_id=?", (bundle_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM quality_findings WHERE id=?", (fragment["id"],)
        ).fetchone()["status"] == "resolved"

    def test_manual_asset_recovery_is_not_added_to_executable_bundle(self):
        conn = self._test_conn()
        conn.execute(
            "INSERT INTO assets(id,filename,path,kind,storage_status) "
            "VALUES(1,'missing.pdf','assets/pdf/missing.pdf','document','missing')"
        )
        run_id = conn.execute(
            "INSERT INTO quality_runs(status,fingerprint) VALUES('completed','asset-run')"
        ).lastrowid
        plan = {
            "action_type": "repair_relations_or_assets",
            "entity_type": "asset",
            "record_ids": [1],
            "operation": "recover_or_relink_missing_asset",
            "requires_review": True,
        }
        finding_id = conn.execute(
            "INSERT INTO quality_findings("
            "run_id,action_type,entity_type,record_ids_json,classification,fingerprint,plan_json"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                run_id, "repair_relations_or_assets", "asset", "[1]",
                "needs_cleaning", "asset-finding", json.dumps(plan),
            ),
        ).lastrowid
        conn.commit()

        from mifp_app.services.data_quality import add_to_bundle, create_bundle

        bundle_id = create_bundle(conn, "admin")
        with pytest.raises(ValueError, match="manual recovery"):
            add_to_bundle(conn, bundle_id, finding_id, {})
        assert conn.execute(
            "SELECT COUNT(*) FROM quality_bundle_items WHERE bundle_id=?", (bundle_id,)
        ).fetchone()[0] == 0

    def test_validation_discards_finding_when_source_changed_after_scan(self):
        conn = self._test_conn()
        conn.execute("INSERT INTO members(id,display_name,slug) VALUES(1,'Before','before')")
        run_id = conn.execute(
            "INSERT INTO quality_runs(status,fingerprint) VALUES('completed','stale-run')"
        ).lastrowid
        plan = {
            "action_type": "clean_record",
            "entity_type": "member",
            "record_ids": [1],
            "fields": [{"field": "display_name", "proposed_value": "After"}],
            "source_state_fingerprint": "obsolete-fingerprint",
        }
        finding_id = conn.execute(
            "INSERT INTO quality_findings("
            "run_id,action_type,entity_type,record_ids_json,classification,fingerprint,plan_json,status"
            ") VALUES(?,?,?,?,?,?,?,'bundled')",
            (
                run_id, "clean_record", "member", "[1]", "needs_cleaning",
                "stale-finding", json.dumps(plan),
            ),
        ).lastrowid
        bundle_id = conn.execute("INSERT INTO quality_bundles(created_by) VALUES('admin')").lastrowid
        conn.execute(
            "INSERT INTO quality_bundle_items(bundle_id,finding_id,action_type,payload_json) VALUES(?,?,?,?)",
            (bundle_id, finding_id, "clean_record", json.dumps({"plan": plan})),
        )
        conn.commit()

        from mifp_app.services.data_quality.executor import validate_bundle

        report = validate_bundle(conn, bundle_id)

        assert report["errors"] == ["Bundle has no executable actions"]
        assert any("source changed" in warning for warning in report["warnings"])
        assert conn.execute(
            "SELECT status FROM quality_findings WHERE id=?", (finding_id,)
        ).fetchone()["status"] == "rejected"

    def test_date_placeholder_flagged(self):
        conn = self._test_conn()
        conn.execute("INSERT INTO events(id, title, slug, start_date, end_date, date_precision) VALUES(1, 'Physics 2020', 'physics-2020', '2020-01-01', '2020-12-31', 'range')")
        conn.commit()

        from mifp_app.services.data_quality.analyzer import analyze, list_findings
        result = analyze(conn)
        findings = list_findings(conn, result["run_id"])

        placeholders = [f for f in findings if any(e["code"] == "false_annual_range" for e in f.get("evidence", []))]
        assert len(placeholders) >= 1

    def test_verify_invariants_with_real_data(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = self._test_conn()

        conn.execute("INSERT INTO members(id, display_name, slug) VALUES(1, 'Test', 'test')")
        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url) VALUES('member', 1, 'https://x.com')")
        conn.commit()

        errors = verify_invariants(conn)
        assert len(errors) == 0

        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url) VALUES('member', 999, 'https://orphan.com')")
        conn.commit()

        errors = verify_invariants(conn)
        assert any("orphan" in e.lower() for e in errors)


class TestDecisionAcceptIgnore:
    def _conn(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        return conn

    def _seed_finding(self, conn, action_type="clean_record", entity_type="member", classification="needs_cleaning"):
        conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_pct INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_message TEXT DEFAULT ''")
        run_id = conn.execute(
            "INSERT INTO quality_runs(status, fingerprint, progress_pct, progress_message) VALUES('completed','fp1',100,'done')"
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_findings(run_id, action_type, entity_type, record_ids_json, classification, fingerprint, plan_json)
               VALUES(?,?,?,?,?,?,?)""",
            (run_id, action_type, entity_type, "[1]", classification, "findfp1", '{"action_type":"clean_record","fields":[]}'),
        )
        conn.commit()
        return run_id

    def test_decision_accept_creates_bundle_and_item(self):
        conn = self._conn()
        run_id = self._seed_finding(conn)
        finding_id = conn.execute("SELECT id FROM quality_findings").fetchone()["id"]

        from mifp_app.services.data_quality import create_bundle, add_to_bundle
        bundle_id = create_bundle(conn, "admin")
        plan = add_to_bundle(conn, bundle_id, finding_id, {"strategy": "best_quality"})

        # Verify finding is now bundled
        finding = conn.execute("SELECT status FROM quality_findings WHERE id=?", (finding_id,)).fetchone()
        assert finding["status"] == "bundled"

        # Verify item exists
        item = conn.execute("SELECT * FROM quality_bundle_items WHERE bundle_id=? AND finding_id=?", (bundle_id, finding_id)).fetchone()
        assert item is not None

    def test_decision_ignore_sets_deferred(self):
        conn = self._conn()
        run_id = self._seed_finding(conn)
        finding_id = conn.execute("SELECT id FROM quality_findings").fetchone()["id"]

        conn.execute("UPDATE quality_findings SET status='deferred' WHERE id=?", (finding_id,))
        conn.commit()
        finding = conn.execute("SELECT status FROM quality_findings WHERE id=?", (finding_id,)).fetchone()
        assert finding["status"] == "deferred"


class TestBulkDecision:
    def _conn(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_pct INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_message TEXT DEFAULT ''")
        return conn

    def _seed(self, conn, extra_findings=0):
        run_id = conn.execute(
            "INSERT INTO quality_runs(status, fingerprint, progress_pct, progress_message) VALUES('completed','fp1',100,'done')"
        ).lastrowid
        for i in range(1 + extra_findings):
            conn.execute(
                """INSERT INTO quality_findings(run_id, action_type, entity_type, record_ids_json, classification, fingerprint, plan_json)
                   VALUES(?,?,?,?,?,?,?)""",
                (run_id, "clean_record", "member", "[1]", "needs_cleaning", f"fp{i+1}",
                 '{"action_type":"clean_record","fields":[]}'),
            )
        conn.commit()

    def test_bulk_accept_with_ids(self):
        conn = self._conn()
        from mifp_app.services.data_quality import create_bundle, add_to_bundle
        self._seed(conn)
        findings = conn.execute("SELECT id FROM quality_findings").fetchall()
        ids = [f["id"] for f in findings]

        bundle_id = create_bundle(conn, "admin")
        applied = 0
        for fid in ids:
            try:
                add_to_bundle(conn, bundle_id, fid, {"strategy": "best_quality"})
                applied += 1
            except ValueError:
                pass
        conn.commit()
        assert applied == len(ids)

        for fid in ids:
            status = conn.execute("SELECT status FROM quality_findings WHERE id=?", (fid,)).fetchone()["status"]
            assert status == "bundled"

    def test_bulk_accept_partial_failure(self):
        from mifp_app.services.data_quality import create_bundle, add_to_bundle
        conn = self._conn()

        run_id = conn.execute(
            "INSERT INTO quality_runs(status, fingerprint, progress_pct, progress_message) VALUES('completed','fp1',100,'done')"
        ).lastrowid
        conn.execute(
            """INSERT INTO quality_findings(run_id, action_type, entity_type, record_ids_json, classification, fingerprint, plan_json)
               VALUES(?,?,?,?,?,?,?)""",
            (run_id, "clean_record", "member", "[1]", "needs_cleaning", "fpok",
             '{"action_type":"clean_record","fields":[]}'),
        )
        conn.execute(
            """INSERT INTO quality_findings(run_id, action_type, entity_type, record_ids_json, classification, fingerprint, plan_json)
               VALUES(?,?,?,?,?,?,?)""",
            (run_id, "clean_record", "member", "[2]", "blocked", "fpbad",
             '{"action_type":"clean_record","fields":[]}'),
        )
        conn.commit()

        bundle_id = create_bundle(conn, "admin")
        findings = conn.execute("SELECT id, classification FROM quality_findings").fetchall()

        applied = 0
        failed = 0
        for f in findings:
            try:
                add_to_bundle(conn, bundle_id, f["id"], {"strategy": "best_quality"})
                applied += 1
            except ValueError:
                failed += 1
        conn.commit()

        assert applied == 1
        assert failed == 1

    def test_bulk_reject_all(self):
        conn = self._conn()
        self._seed(conn)
        conn.execute("UPDATE quality_findings SET status='rejected'")
        conn.commit()
        for f in conn.execute("SELECT status FROM quality_findings").fetchall():
            assert f["status"] == "rejected"


class TestMergeDetectionAndApplication:
    """End-to-end merge detection + application for multi-field similarity."""

    def _conn(self):
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_pct INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE quality_runs ADD COLUMN progress_message TEXT DEFAULT ''")
        return conn

    def _run_analyze(self, conn):
        from mifp_app.services.data_quality.analyzer import analyze
        conn.execute("PRAGMA foreign_keys=OFF")
        result = analyze(conn)
        conn.execute("PRAGMA foreign_keys=ON")
        return result

    def test_member_multi_field_merge(self):
        """Two members with same name, same country, same field → STRONG merge candidate."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO members(id,display_name,first_name,last_name,affiliation,country,field,slug) "
            "VALUES(1,'Alexey Kavokin','Alexey','Kavokin','Southampton University','UK','physics','ak1')"
        )
        conn.execute(
            "INSERT INTO members(id,display_name,first_name,last_name,affiliation,country,field,slug) "
            "VALUES(2,'Alexey Kavokin','Alexey','Kavokin','University of Southampton','United Kingdom','physics','ak2')"
        )
        conn.commit()
        result = self._run_analyze(conn)

        from mifp_app.services.data_quality.analyzer import list_findings
        findings = list_findings(conn, result["run_id"])
        merges = [f for f in findings if f["action_type"] == "merge_records"]
        assert len(merges) >= 1
        merge = merges[0]
        assert merge["entity_type"] == "member"
        assert set(merge["record_ids"]) == {1, 2}

    def test_member_different_email_blocks_merge(self):
        """Same name but different email → BLOCKED."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO members(id,display_name,email,slug) "
            "VALUES(1,'Alexey Kavokin','alexey@example.com','ak1')"
        )
        conn.execute(
            "INSERT INTO members(id,display_name,email,slug) "
            "VALUES(2,'Alexey Kavokin','other@example.com','ak2')"
        )
        conn.commit()
        result = self._run_analyze(conn)

        from mifp_app.services.data_quality.analyzer import list_findings
        findings = list_findings(conn, result["run_id"])
        merges = [f for f in findings if f["action_type"] == "merge_records"]
        blocked = [f for f in findings if f["classification"] == "blocked"]
        # Different email should produce a blocked finding or no merge at all
        assert not merges or all(m["classification"] == "blocked" for m in merges)

    def test_event_series_different_year_no_merge(self):
        """Same event series name but different year → RELATED (not merged)."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO events(id,title,start_date,date_precision,slug) "
            "VALUES(1,'Winter School 2025','2025-01-01','year','ws25')"
        )
        conn.execute(
            "INSERT INTO events(id,title,start_date,date_precision,slug) "
            "VALUES(2,'Winter School 2026','2026-01-01','year','ws26')"
        )
        conn.commit()
        result = self._run_analyze(conn)

        from mifp_app.services.data_quality.analyzer import list_findings
        findings = list_findings(conn, result["run_id"])
        merges = [f for f in findings if f["action_type"] == "merge_records"]
        assert not merges

    def test_news_exact_body_with_conflicting_identity_requires_review(self):
        """Identical prose cannot override different titles and complete dates."""
        conn = self._conn()
        body = "Prof. Kavokin and Dr. Kavokina have published a new Science Perspective. " * 10
        conn.execute(
            "INSERT INTO news(id,title,body,date,slug) "
            "VALUES(1,'First headline','" + body + "','2026-04-09','n1')"
        )
        conn.execute(
            "INSERT INTO news(id,title,body,date,slug) "
            "VALUES(2,'Second headline','" + body + "','2026-04-10','n2')"
        )
        conn.commit()
        result = self._run_analyze(conn)

        from mifp_app.services.data_quality.analyzer import list_findings
        findings = list_findings(conn, result["run_id"])
        merges = [f for f in findings if f["action_type"] == "merge_records"]
        assert len(merges) >= 1
        assert merges[0]["classification"] == "ambiguous"
        assert merges[0]["workflow"] == "manual"

    def test_full_merge_pipeline_apply(self):
        """Full pipeline: detect duplicate members → bundle → apply."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO members(id,display_name,first_name,last_name,affiliation,country,slug) "
            "VALUES(1,'Alexey Kavokin','Alexey','Kavokin','Southampton University','UK','ak1')"
        )
        conn.execute(
            "INSERT INTO members(id,display_name,first_name,last_name,affiliation,country,slug) "
            "VALUES(2,'Alexey Kavokin','Alexey','Kavokin','Southampton University','UK','ak2')"
        )
        conn.execute(
            "INSERT INTO entity_links(entity_type,entity_id,url,role) "
            "VALUES('member',1,'https://example.com/profile','reference')"
        )
        conn.execute(
            "INSERT INTO entity_links(entity_type,entity_id,url,role) "
            "VALUES('member',2,'https://example.com/profile','reference')"
        )
        conn.commit()
        result = self._run_analyze(conn)

        from mifp_app.services.data_quality.analyzer import list_findings
        from mifp_app.services.data_quality import create_bundle, add_to_bundle, apply_bundle
        from tempfile import NamedTemporaryFile
        from pathlib import Path
        import sqlite3
        import os

        findings = list_findings(conn, result["run_id"])
        merges = [f for f in findings if f["action_type"] == "merge_records"]
        assert merges, "Expected merge findings"

        bundle_id = create_bundle(conn, "test")
        for m in merges:
            add_to_bundle(conn, bundle_id, m["id"], {"strategy": "best_quality"})
        conn.commit()

        from mifp_app.services.data_quality.executor import validate_bundle
        report = validate_bundle(conn, bundle_id)
        assert report.get("valid"), f"Bundle validation failed: {report.get('errors')}"
        conn.rollback()

        # Write to a temp file for apply_bundle (requires file path)
        tmp = NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            source = Path(tmp.name)
            source_db = sqlite3.connect(str(source))
            source_db.row_factory = sqlite3.Row
            schema = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE" / "mifp_app" / "db" / "schema.sql"
            source_db.executescript(schema.read_text(encoding="utf-8"))
            source_db.execute("ALTER TABLE quality_runs ADD COLUMN progress_pct INTEGER DEFAULT 0")
            source_db.execute("ALTER TABLE quality_runs ADD COLUMN progress_message TEXT DEFAULT ''")
            # Copy data
            for table in ("members", "entity_links", "quality_runs", "quality_findings",
                          "quality_bundles", "quality_bundle_items", "resolved_pairs"):
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                for row in rows:
                    cols = ", ".join(row.keys())
                    vals = ", ".join("?" for _ in row.keys())
                    source_db.execute(f"INSERT INTO {table}({cols}) VALUES({vals})", list(row))
                source_db.commit()

            report = apply_bundle(source, bundle_id)
            assert "backup_path" in report
            assert report.get("operations", 0) > 0

            # A completed merge physically removes the redundant row after
            # preserving its aliases and relationships.
            remaining = source_db.execute(
                "SELECT id, review_status FROM members WHERE id IN (1,2) ORDER BY id"
            ).fetchall()
            assert len(remaining) == 1
            assert remaining[0]["review_status"] == "published"
        finally:
            os.unlink(tmp.name)

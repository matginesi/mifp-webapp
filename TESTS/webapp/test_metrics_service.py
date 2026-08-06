from __future__ import annotations


def test_normalize_metric_path_removes_query_and_sensitive_segments():
    from mifp_app.services.metrics_service import normalize_metric_path

    assert normalize_metric_path("/events?x=1") == "/events"
    assert normalize_metric_path("/join?email=test@example.com") == "/join"
    assert normalize_metric_path("/reset-password/abcdef1234567890") == "/reset-password/[token]"
    assert normalize_metric_path("/events/PLMCN-2026/program/index.html") == "/events/PLMCN-2026/program/index.html"
    assert normalize_metric_path("/media/uploads/very-private-file-name-with-email@example.com.pdf") == "/media/pdf"
    assert normalize_metric_path("/dashboard/server") == "/dashboard/[admin]"


def test_sanitize_unknown_question_removes_personal_identifiers():
    from mifp_app.services.metrics_service import sanitize_unknown_question

    sanitized = sanitize_unknown_question(
        "Email Me@Test.Example or call +39 333 123 4567 about https://example.test/token/abcdef1234567890"
    )

    assert sanitized == "email [email] or call [phone] about [url]"
    assert "Me@Test" not in sanitized
    assert "333" not in sanitized
    assert "abcdef1234567890" not in sanitized

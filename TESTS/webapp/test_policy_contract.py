"""The published policies must not drift away from the implementation.

These are deliberately *targeted* checks for the specific claims that were wrong
before this cleanup, not full-text policy snapshots. A policy may be rewritten
freely as long as it does not reintroduce a claim the code contradicts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "MIFPAPP" / "CORE"
PRIVACY = CORE / "Privacy.md"
COOKIE_POLICY = CORE / "cookie-policy.md"
COOKIE_FALLBACK = CORE / "mifp_app" / "templates" / "public" / "cookie_policy.html"
JOIN_FORM = CORE / "mifp_app" / "templates" / "public" / "join.html"
DECISIONS = Path(__file__).resolve().parents[2] / "docs" / "PRIVACY_DECISIONS_REQUIRED.md"

POLICY_FILES = (PRIVACY, COOKIE_POLICY, COOKIE_FALLBACK)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """Policy text with whitespace collapsed, for multi-word phrase checks."""
    return re.sub(r"\s+", " ", _read(path)).strip()


# ---------------------------------------------------------------------------
# Technical claims
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", POLICY_FILES)
def test_policies_do_not_claim_bcrypt(path):
    """The real implementation is PBKDF2-HMAC-SHA256 via manage.py/Werkzeug."""
    assert "bcrypt" not in _read(path).lower(), path


def test_privacy_policy_names_the_real_password_hash():
    text = _read(PRIVACY)
    assert "PBKDF2-HMAC-SHA256" in text
    assert "600,000" in text


@pytest.mark.parametrize("path", POLICY_FILES)
def test_policies_use_the_real_cookie_names(path):
    """`session` was the Flask default; the deployment renames it."""
    text = _read(path)
    assert "mifp_admin_session" in text, path
    assert "mifp_csrf" in text, path
    assert not re.search(r"\|\s*`?session`?\s*\|", text), f"stale 'session' cookie name in {path}"


def test_cookie_policy_describes_the_csrf_cookie_and_its_lifetime():
    text = _read(COOKIE_POLICY)
    assert "strictly necessary" in text.lower()
    assert "2 hours" in text


@pytest.mark.parametrize("path", (COOKIE_POLICY, COOKIE_FALLBACK))
def test_policies_describe_the_notice_as_informational(path):
    text = re.sub(r"\s+", " ", _read(path)).lower()
    assert "informational only" in text, path
    assert "does not ask for consent" in text, path


@pytest.mark.parametrize("path", POLICY_FILES)
def test_policies_do_not_claim_anonymous_visitors_never_get_cookies(path):
    """`/join` and `/login` legitimately need the anonymous CSRF binding."""
    text = _read(path).lower()
    for absolute in (
        "no cookies are set at all",
        "anonymous visitors to the public website do not receive any cookies",
        "anonymous visitors browsing public pages receive no cookies",
    ):
        assert absolute not in text, (path, absolute)


def test_cookie_policy_describes_revision_acknowledgement_without_fake_consent():
    """Only the acknowledged notice revision is stored locally."""
    text = _flat(COOKIE_POLICY).lower()
    assert "informational only" in text
    assert "no server-side record" in text
    assert "mifp-cookie-notice-revision" in text
    assert "not used to identify or track" in text
    assert "new notice revision" in text


@pytest.mark.parametrize("path", POLICY_FILES)
def test_policies_do_not_promise_features_that_do_not_exist(path):
    lowered = _read(path).lower()
    assert "contact form" not in lowered, path
    assert "event registration" not in lowered, path


def test_privacy_policy_does_not_use_an_absolute_sensitive_data_claim():
    text = _read(PRIVACY).lower()
    assert "we do not collect or process any sensitive data" not in text
    assert "special-category" in text or "special category" in text


def test_privacy_policy_does_not_claim_an_article_9_legal_basis():
    """It may say it does *not* rely on Article 9; it must not claim a 9(2) basis."""
    text = _read(PRIVACY)
    assert "9(2)" not in text
    assert "does not rely on Article 9" in text


def test_privacy_policy_distinguishes_data_sources():
    text = _read(PRIVACY).lower()
    assert "historical" in text
    assert "imported" in text


def test_privacy_policy_states_logs_are_pseudonymous_not_anonymous():
    text = _read(PRIVACY)
    assert "pseudonymous" in text.lower()
    assert "IP addresses are still processed" in text


def test_privacy_policy_does_not_claim_log_retention_is_automatic():
    text = _read(PRIVACY)
    assert "protected maintenance cleanup" in text
    assert "not a continuously running scheduled job" in text


def test_privacy_policy_does_not_assert_dat_processing_agreements_exist():
    lowered = _read(PRIVACY).lower()
    assert "under strict data processing agreements" not in lowered
    assert "cannot be established from this repository" in lowered


def test_cookie_policy_does_not_offer_a_consent_gate():
    lowered = _read(COOKIE_POLICY).lower()
    for fake in ("accept all", "reject all", "manage cookies"):
        assert fake not in lowered, fake
    # The notice is documented as informational, and no consent is collected.
    flat = _flat(COOKIE_POLICY).lower()
    assert "does not collect consent" in flat
    assert "not a consent mechanism" in flat
    assert "nothing optional to consent to" in flat


# ---------------------------------------------------------------------------
# Published pages
# ---------------------------------------------------------------------------

def test_cookie_fallback_matches_the_markdown_policy():
    """The HTML fallback must not contradict the markdown the PDF is built from."""
    fallback = _read(COOKIE_FALLBACK)
    assert "mifp_csrf" in fallback
    assert "mifp_admin_session" in fallback
    assert ">session<" not in fallback
    assert "localStorage analytics" not in fallback or "does not use" in fallback


def test_join_form_notice_is_factual():
    form = _read(JOIN_FORM)
    assert "you consent" not in form.lower()
    assert "will not be shared with third parties without your explicit consent" not in form
    assert "you ask MIFP to process the information provided" in form
    assert "special-category" in form
    # No mandatory consent checkbox without an independent consent purpose.
    assert 'name="consent"' not in form
    assert 'type="checkbox"' not in form


# ---------------------------------------------------------------------------
# Internal follow-up file
# ---------------------------------------------------------------------------

def test_privacy_decisions_file_exists_and_is_actionable():
    assert DECISIONS.is_file()
    text = _read(DECISIONS)
    assert "OPEN" in text
    for topic in ("legal basis", "SMTP", "backup", "provenance", "Conference Editor"):
        assert topic in text, topic
    # It must record the verified facts too, so they are not re-litigated.
    assert "PBKDF2-HMAC-SHA256" in text
    assert "Not bcrypt" in text

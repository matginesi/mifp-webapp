# DQ Targeted Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the existing 3-phase data quality system with targeted improvements across 8 areas (normalization, junk classifcation, page fragment detection, date handling, cluster safety, field resolution, post-apply verification, tests).

**Architecture:** Incremental module-by-module evolution — no rewrite. Every change adds new functions alongside existing ones. Existing analyze → bundle → apply flow remains untouched. Each area is independently testable.

**Tech Stack:** Python 3.12+, sqlite3, pytest, Flask

## Global Constraints

- All new code must pass `python -m pytest TESTS/webapp -q`
- No new dependencies beyond stdlib + Flask + sqlite3
- All DB mutations go through parameterized queries
- No sensitive data in logs
- Every public function has a test

---

## File Structure

### Modified files:
- `MIFPAPP/CORE/mifp_app/services/data_quality/normalizers.py` — new normalization functions
- `MIFPAPP/CORE/mifp_app/services/data_quality/models.py` — new Classification values
- `MIFPAPP/CORE/mifp_app/services/data_quality/policies.py` — enhanced per-type evaluation
- `MIFPAPP/CORE/mifp_app/services/data_quality/analyzer.py` — junk checks, fragment detection, date checks
- `MIFPAPP/CORE/mifp_app/services/data_quality/planner.py` — per-type field resolution, date resolution
- `MIFPAPP/CORE/mifp_app/services/data_quality/executor.py` — post-apply verification
- `MIFPAPP/CORE/mifp_app/services/data_quality/__init__.py` — exports for cluster module

### Created files:
- `MIFPAPP/CORE/mifp_app/services/data_quality/cluster.py` — cluster safety logic
- `TESTS/webapp/test_data_quality_unit.py` — all unit tests

---

### Task 1: Enhanced Normalization Functions

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/normalizers.py` (lines 1-158)
- Test: `TESTS/webapp/test_data_quality_unit.py`

**Interfaces:**
- Produces: `normalize_identity_text(value) -> str`, `normalize_person_name(value) -> str`, `normalize_title(value) -> str`, `normalize_canonical_url(value) -> str`

- [ ] **Step 1: Write the failing tests**

Add these tests to a new file `TESTS/webapp/test_data_quality_unit.py`:

```python
from __future__ import annotations

import pytest

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest TESTS/webapp/test_data_quality_unit.py::TestNormalizeIdentityText -v`
Expected: FAIL with ImportError (function not defined yet)

- [ ] **Step 3: Write minimal implementation**

Add to `normalizers.py`:

```python
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_ORG_SUFFIX = re.compile(r"\s+(?:Ltd|Inc|Corp|S\.p\.A\.|GmbH|S\.?A\.?R\.?L\.?|LLC|PLC|Co\..*)$", re.I)
_DIACRITICS = re.compile(r"[^a-zA-Z0-9\s&]+")
_AMP = re.compile(r"\band\b", re.I)
_PARTICLES = frozenset({"di", "de", "del", "della", "van", "von", "da", "der", "den", "ter", "vom", "zum"})


def normalize_identity_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.replace("&", " and ")
    text = _AMP.sub("&", text)
    text = _ORG_SUFFIX.sub("", text)
    text = _TRAILING_PAREN.sub("", text)
    text = _DIACRITICS.sub(" ", text)
    return _SPACE.sub(" ", text).strip().lower()


def normalize_person_name(value: object) -> str:
    """Return stable 'last, first' canonical form."""
    p = person_name(value)
    if len(p.normal) < 2:
        return str(value or "").strip().lower()
    return ", ".join(p.inverted)


def normalize_title(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*[—–|]\s*MIFP.*$", "", text)
    text = re.sub(r"\s*[—–|]\s*Mediterranean Institute.*$", "", text)
    text = re.sub(r"\s*-\s*Home$", "", text, flags=re.I)
    text = re.sub(r"\s*\|.*$", "", text)
    text = re.sub(r"\s*Past Event\s*$", "", text, flags=re.I)
    return comparison_text(text)


def normalize_canonical_url(value: object) -> str:
    raw = unquote(str(value or "").strip())
    if not raw:
        return ""
    raw = re.sub(r"[?&](?:utm_[^&=]+|fbclid|gclid|ref|source)=[^&]+", "", raw)
    raw = raw.rstrip("?&")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold().replace("old.mifp.eu", "www.mifp.eu").replace("events.mifp.eu", "www.mifp.eu")
    path = re.sub(r"/+", "/", parts.path or "/")
    path = re.sub(r"/media/[^/]+/v1/", "/media/", path)
    path = path.replace("/www.mifp.eu/", "/")
    return urlunsplit(("https", host, path.rstrip("/") or "/", "", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest TESTS/webapp/test_data_quality_unit.py::TestNormalizeIdentityText TESTS/webapp/test_data_quality_unit.py::TestNormalizePersonName -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dq): add normalize_identity_text, normalize_person_name, normalize_title, normalize_canonical_url"
```

---

### Task 2: Junk/Technical Record Classification

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/models.py` — add JUNK classification
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/analyzer.py` — add junk checks
- Test: `TESTS/webapp/test_data_quality_unit.py`

**Interfaces:**
- Consumes: `Classification.JUNK = "junk_technical_record"`, `Classification.FRAGMENT = "page_fragment_attached"`
- Consumes: `_check_junk_record(row) -> Finding | None` in analyzer.py

- [ ] **Step 1: Write the failing tests**

```python
from mifp_app.services.data_quality.models import Classification

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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest TESTS/webapp/test_data_quality_unit.py::TestJunkClassifier -v`
Expected: FAIL with ImportError / attribute errors

- [ ] **Step 3: Implement JUNK classification in models.py**

Add to `Classification` enum:
```python
JUNK = "junk_technical_record"
FRAGMENT = "page_fragment_attached"
```

- [ ] **Step 4: Implement junk checks in analyzer.py**

Add to `analyzer.py` imports:
```python
from .models import ActionType, Classification, Evidence, Finding
```

Add after the existing function, before `_quality_findings`:

```python
_JUNK_TITLE_PATTERNS = [
    re.compile(r"^\d{1,3}$"),  # pure short numbers
    re.compile(r"^\d+\s*(?:MB|KB|GB|bytes?)$", re.I),  # file sizes
    re.compile(r"^(?:page|file|document|download)\s*\d*$", re.I),
    re.compile(r"^[a-z]+[\da-f]{4,}$", re.I),  # hex-ish page IDs
    re.compile(r"^[a-z]*\d+[a-z]+$", re.I),  # mixed letter-number junk
    re.compile(r"^(?:publications?|archive|news)\s*\d*\s*$", re.I),
    re.compile(r"^\s*$"),
]


def _check_junk_record(entity_type: str, row: dict) -> Finding | None:
    label_field = LABELS.get(entity_type, "title")
    if entity_type not in TABLES:
        return None
    label = str(row.get(label_field) or "")
    for pattern in _JUNK_TITLE_PATTERNS:
        if pattern.match(label.strip()):
            evidence = [Evidence(
                "junk_technical_title", "deterministic",
                f"Title '{label}' appears to be a technical identifier, not a real entity name",
                [label],
            )]
            plan = {
                "action_type": "clean_record",
                "entity_type": entity_type,
                "record_ids": [row["id"]],
                "operation": "quarantine",
                "requires_review": True,
                "source_fingerprint": stable_fingerprint(entity_type, [row], action="junk_record"),
                "source_state_fingerprint": stable_fingerprint(entity_type, [row]),
            }
            return Finding(
                ActionType.CLEAN, entity_type, [row["id"]],
                Classification.JUNK, evidence, [], plan,
                plan["source_fingerprint"], 1,
            )
    return None
```

Then in `_quality_findings()`, add at the start:
```python
junk = _check_junk_record(entity_type, row)
if junk:
    output.append(junk)
    continue
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest TESTS/webapp/test_data_quality_unit.py::TestJunkClassifier -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(dq): add junk_technical_record classification and detection"
```

---

### Task 3: Page Fragment Detection (Events)

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/analyzer.py`
- Test: `TESTS/webapp/test_data_quality_unit.py`

**Interfaces:**
- Produces: `_check_event_page_fragment(row, context) -> Finding | None`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to see failure**

Run: `python -m pytest TESTS/webapp/test_data_quality_unit.py::TestEventPageFragment -v`

- [ ] **Step 3: Implement `_check_event_page_fragment` in analyzer.py**

```python
_FRAGMENT_KEYWORDS = frozenset({
    "topics", "fees", "program", "gallery", "registration",
    "committees", "speakers", "call for papers", "venue",
    "accommodation", "sponsors", "support", "proceedings",
    "template", "downloads", "important dates", "scope",
    "invited speakers", "commitee", "programme",
    "photo gallery", "travel", "visa", "submission",
})


def _check_event_page_fragment(row: dict, context: dict) -> Finding | None:
    title = str(row.get("title") or "")
    title_lower = comparison_text(title)
    title_words = set(title_lower.split())
    matches = title_words & _FRAGMENT_KEYWORDS
    if not matches:
        return None
    evidence_words = sorted(matches)
    evidence = [Evidence(
        "event_page_fragment", "strong",
        f"Title contains fragment keywords ({', '.join(evidence_words)}), "
        f"suggesting this is a subpage of a larger event, not a standalone event",
        [title],
    )]
    plan = {
        "action_type": "merge_records",
        "entity_type": "event",
        "record_ids": [row["id"]],
        "operation": "absorb_fragment",
        "requires_review": True,
        "proposed_parent_hint": None,
        "fragment_keywords": evidence_words,
        "source_fingerprint": stable_fingerprint("event", [row], action="page_fragment"),
        "source_state_fingerprint": stable_fingerprint("event", [row]),
    }
    return Finding(
        ActionType.MERGE, "event", [row["id"]],
        Classification.FRAGMENT, evidence, [], plan,
        plan["source_fingerprint"], 0.88,
    )
```

Integrate in `_quality_findings()` for events:
```python
if entity_type == "event":
    frag = _check_event_page_fragment(row, context or {})
    if frag:
        output.append(frag)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest TESTS/webapp/test_data_quality_unit.py::TestEventPageFragment -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dq): add page fragment detection for events"
```

---

### Task 4: Date Handling Improvements

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/analyzer.py` — date placeholder check
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/planner.py` — date resolution
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/policies.py` — date consistency in evaluation
- Test: `TESTS/webapp/test_data_quality_unit.py`

**Interfaces:**
- Produces: `_check_date_placeholder(row) -> Finding | None` in analyzer.py
- Produces: `resolve_dates(fields: list[dict], records: list[dict]) -> dict` in planner.py

- [ ] **Step 1: Write failing tests**

```python
class TestDateHandling:
    def test_date_placeholder_detected(self):
        from mifp_app.services.data_quality.analyzer import _check_date_placeholder
        row = {"id": 1, "start_date": "2020-01-01", "end_date": "2020-12-31",
               "date_precision": "range", "review_status": "published"}
        finding = _check_date_placeholder(row)
        assert finding is not None

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
        # Year precision with Jan 1 is expected
        assert finding is None  # year precision = intentional
```

- [ ] **Step 2: Run to fail**

- [ ] **Step 3: Implement `_check_date_placeholder` in analyzer.py**

```python
def _check_date_placeholder(row: dict) -> Finding | None:
    start = str(row.get("start_date") or "")
    end = str(row.get("end_date") or "")
    precision = str(row.get("date_precision") or "")
    if precision == "range" and start.endswith("-01-01") and end.endswith("-12-31") and start[:4] == end[:4]:
        year = start[:4]
        evidence = [Evidence(
            "false_annual_range", "strong",
            f"Date range {start} to {end} encodes a year ({year}), not a proven range",
            [start, end],
        )]
        plan = {
            "action_type": "clean_record",
            "entity_type": "event",
            "record_ids": [row["id"]],
            "fields": [
                {"field": "end_date", "proposed_value": None,
                 "action": "replace_with_cleaned", "requires_review": True,
                 "reason": "Year-only date is not a full-year event."},
                {"field": "date_precision", "proposed_value": "year",
                 "action": "replace_with_cleaned", "requires_review": True,
                 "reason": "Only the year is known."},
                {"field": "date_text", "proposed_value": year,
                 "action": "replace_with_cleaned", "requires_review": True,
                 "reason": "Preserve the known year."},
            ],
            "source_fingerprint": stable_fingerprint("event", [row], action="date_placeholder"),
            "source_state_fingerprint": stable_fingerprint("event", [row]),
        }
        return Finding(
            ActionType.CLEAN, "event", [row["id"]],
            Classification.CLEANING, evidence, [], plan,
            plan["source_fingerprint"], 0.95,
        )
    return None
```

- [ ] **Step 4: Implement `resolve_dates` in planner.py**

```python
_DATE_PLACEHOLDER_START = re.compile(r"^\d{4}-01-01$")
_DATE_PLACEHOLDER_END = re.compile(r"^\d{4}-12-31$")


def resolve_dates(fields: list[dict], records: list[dict]) -> dict:
    merged = {}
    for field in ("start_date", "end_date", "date_precision", "date_text", "date_is_inferred"):
        values = [r.get(field) for r in records if r.get(field) not in (None, "")]
        if not values:
            continue
        if field == "date_precision":
            precisions = {"day": 4, "month": 3, "year": 2, "range": 1, "unknown": 0}
            best = max(values, key=lambda v: precisions.get(str(v), 0))
            merged[field] = best
        elif field == "date_is_inferred":
            merged[field] = min(int(v) for v in values)
        elif field in ("start_date", "end_date"):
            non_placeholder = [v for v in values
                               if not _DATE_PLACEHOLDER_START.match(str(v))
                               and not _DATE_PLACEHOLDER_END.match(str(v))]
            if non_placeholder:
                merged[field] = max(non_placeholder, key=lambda v: len(str(v)))
            else:
                merged[field] = max(values, key=lambda v: len(str(v)))
        else:
            merged[field] = max(values, key=lambda v: len(str(v)))
    if "start_date" in merged and "end_date" in merged:
        if merged["start_date"] > merged["end_date"]:
            merged["end_date"] = None
            merged["date_precision"] = merged.get("date_precision", "unknown")
    return merged
```

- [ ] **Step 5: Write tests for date consistency in policies.py**

- [ ] **Step 6: Run all date tests**

- [ ] **Step 7: Commit**

---

### Task 5: Cluster Safety

**Files:**
- Create: `MIFPAPP/CORE/mifp_app/services/data_quality/cluster.py`
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/__init__.py`
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/analyzer.py` — integrate cluster checks
- Test: `TESTS/webapp/test_data_quality_unit.py`

**Interfaces:**
- Produces: `cluster_is_safe(cluster: list[dict], entity_type: str, context: dict) -> tuple[bool, list[str], list[list[dict]]]`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Create `cluster.py`**

```python
from __future__ import annotations

from itertools import combinations
from typing import Any

from .normalizers import tokens, years
from .policies import similarity


def _event_series_key(title: str) -> str:
    """Extract event series name ignoring year and keywords."""
    year_tokens = years(title)
    skip = {"conference", "meeting", "school", "workshop", "symposium", "congress",
            "the", "and", "of", "on", "in", "for", str(y) for y in year_tokens}
    meaningful = [t for t in tokens(title) if t not in skip]
    return " ".join(meaningful[:6])


def cluster_is_safe(
    records: list[dict],
    entity_type: str,
    context: dict[str, Any] | None = None,
) -> tuple[bool, list[str], list[list[dict]]]:
    reasons: list[str] = []
    if len(records) < 2:
        return True, [], [records]
    
    ids_seen: set[int] = set()
    for r in records:
        rid = int(r.get("id", 0))
        if rid in ids_seen:
            reasons.append(f"Duplicate record id {rid}")
        ids_seen.add(rid)
    
    for a, b in combinations(records, 2):
        doi_a = str(a.get("doi") or "").strip().lower()
        doi_b = str(b.get("doi") or "").strip().lower()
        if doi_a and doi_b and doi_a != doi_b:
            reasons.append(f"Different DOIs: {doi_a} vs {doi_b}")
        email_a = str(a.get("email") or "").strip().lower()
        email_b = str(b.get("email") or "").strip().lower()
        if email_a and email_b and email_a != email_b and "@" in email_a and "@" in email_b:
            reasons.append(f"Different personal emails: {email_a} vs {email_b}")
    
    if entity_type == "event":
        years_seen: set[int] = set()
        for r in records:
            for y in years(r.get("title", "")) | years(r.get("start_date", "")):
                years_seen.add(y)
        series_keys = {_event_series_key(r.get("title", "")) for r in records if r.get("title")}
        if len(series_keys) > 1 and len(years_seen) > 1:
            reasons.append(f"Different event series ({series_keys}) and years ({years_seen})")
        elif len(series_keys) == 1 and len(years_seen) > 1:
            reasons.append(f"Same series but different years: {sorted(years_seen)}")

    start_dates = [r.get("start_date") for r in records if r.get("start_date")]
    if len(start_dates) >= 2:
        unique_dates = set(start_dates)
        if len(unique_dates) > 1 and entity_type != "news":
            reasons.append(f"Multiple different start dates: {sorted(unique_dates)}")
    
    if reasons:
        return False, reasons, [records]
    return True, [], [records]
```

- [ ] **Step 3: Integrate in analyzer.py `_consolidate_exact_groups()`**

```python
from .cluster import cluster_is_safe

# After building the group, before creating Finding:
safe, reasons, subclusters = cluster_is_safe(records, entity_type, context or {})
if not safe:
    # Downgrade classification to AMBIGUOUS with reasons
    for reason in reasons:
        contradictions.append(Evidence("cluster_unsafe", "blocking", reason, []))
    classification = Classification.AMBIGUOUS
```

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

---

### Task 6: Per-Type Field Resolution

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/planner.py`
- Test: `TESTS/webapp/test_data_quality_unit.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Implement per-type resolvers in planner.py**

```python
_GENERIC_EMAILS = frozenset({"info", "contact", "admin", "webmaster", "support", "noreply", "no-reply"})


def _is_generic_email(email: str) -> bool:
    local = email.split("@")[0].strip().lower() if "@" in email else email
    return local in _GENERIC_EMAILS


def _resolve_member_email(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    personal = [v for v in candidates if not _is_generic_email(str(v["value"]))]
    if personal:
        best = max(personal, key=lambda v: len(str(v["value"])))
        return str(best["value"]), int(best["record_id"])
    best = max(candidates, key=lambda v: len(str(v["value"])))
    return str(best["value"]), int(best["record_id"])


def _resolve_member_affiliation(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    best = max(candidates, key=lambda v: (len(str(v["value"]).split(",")), len(str(v["value"]))))
    return str(best["value"]), int(best["record_id"])


def _resolve_news_body(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    # prefer longer after boilerplate removal
    def body_score(v):
        text = str(v["value"])
        cleaned, _ = clean_boilerplate(text)
        return len(cleaned)
    best = max(candidates, key=body_score)
    cleaned, _ = clean_boilerplate(str(best["value"]))
    return cleaned, int(best["record_id"])


def _resolve_news_summary(values: list[dict], canonical_id: int, body_values: list[dict]) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if candidates:
        best = max(candidates, key=lambda v: len(str(v["value"])))
        summary = str(best["value"])
        if len(summary.split()) >= 10:
            return summary, int(best["record_id"])
    body_candidates = [v for v in body_values if v.get("value") not in (None, "")]
    if body_candidates:
        best_body = max(body_candidates, key=lambda v: len(str(v["value"])))
        body = str(best_body["value"])
        sentences = body.replace("\n", " ").split(". ")
        if sentences:
            first = sentences[0].strip() + "."
            if 10 <= len(first.split()) <= 40:
                return first, int(best_body["record_id"])
    return None, canonical_id


def _resolve_event_description(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    def desc_score(v):
        text = str(v["value"])
        cleaned, removed = clean_boilerplate(text)
        return len(cleaned), -len(removed), -len(text)
    best = max(candidates, key=desc_score)
    cleaned, _ = clean_boilerplate(str(best["value"]))
    return cleaned, int(best["record_id"])


def _resolve_publication_title(values: list[dict], canonical_id: int) -> tuple[str | None, int]:
    candidates = [v for v in values if v.get("value") not in (None, "")]
    if not candidates:
        return None, canonical_id
    def title_score(v):
        text = str(v["value"]).strip()
        if text.isdigit():
            return 0
        if len(text) < 10:
            return 1
        return 10 + len(text)
    best = max(candidates, key=title_score)
    return str(best["value"]), int(best["record_id"])
```

Then integrate these into `build_merge_plan()` or as a separate `resolve_fields_for_type()` function called during best quality selection.

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 7: Post-Apply Verification

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/executor.py`
- Test: `TESTS/webapp/test_data_quality_unit.py`

**Interfaces:**
- Produces: `verify_invariants(conn, entity_type) -> list[str]`

- [ ] **Step 1: Write failing tests**

```python
class TestVerifyInvariants:
    def test_no_orphan_references(self):
        from mifp_app.services.data_quality.executor import verify_invariants
        conn = _build_test_db()
        conn.execute("INSERT INTO members(id, display_name, slug) VALUES(1, 'Test', 'test')")
        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url) VALUES('member', 1, 'https://x.com')")
        conn.commit()
        errors = verify_invariants(conn)
        assert len(errors) == 0
        conn.execute("INSERT INTO entity_links(entity_type, entity_id, url) VALUES('member', 999, 'https://x.com')")
        conn.commit()
        errors = verify_invariants(conn)
        assert any("orphan" in e.lower() for e in errors)
```

- [ ] **Step 2: Implement `verify_invariants` in executor.py**

```python
def verify_invariants(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    tables_map = {"member": "members", "event": "events", "news": "news",
                  "publication": "publications", "sponsor": "sponsors",
                  "research_area": "research_areas", "page": "pages"}
    
    # Orphaned entity_links
    for row in conn.execute("SELECT DISTINCT entity_type, entity_id FROM entity_links"):
        et, eid = str(row["entity_type"]), int(row["entity_id"])
        table = tables_map.get(et)
        if table and not conn.execute(f'SELECT 1 FROM "{table}" WHERE id=?', (eid,)).fetchone():
            errors.append(f"orphan entity_link: {et} id={eid} references non-existent {table} row")
    
    # Orphaned asset_links
    for row in conn.execute("SELECT DISTINCT entity_type, entity_id FROM asset_links"):
        et, eid = str(row["entity_type"]), int(row["entity_id"])
        table = tables_map.get(et)
        if table and not conn.execute(f'SELECT 1 FROM "{table}" WHERE id=?', (eid,)).fetchone():
            errors.append(f"orphan asset_link: {et} id={eid} references non-existent {table} row")
    
    # Duplicate slugs
    for entity_type, table in tables_map.items():
        slugs = conn.execute(f'SELECT slug, COUNT(*) as cnt FROM "{table}" WHERE slug IS NOT NULL AND slug!=\'\' GROUP BY slug HAVING cnt>1').fetchall()
        for slug_row in slugs:
            errors.append(f"duplicate slug {slug_row['slug']} in {table} (count={slug_row['cnt']})")
    
    # Duplicate DOIs
    dois = conn.execute("SELECT doi, COUNT(*) as cnt FROM publications WHERE doi IS NOT NULL AND doi!='' GROUP BY doi HAVING cnt>1").fetchall()
    for doi_row in dois:
        errors.append(f"duplicate doi {doi_row['doi']} (count={doi_row['cnt']})")
    
    # Events start <= end
    bad_dates = conn.execute("SELECT id, start_date, end_date FROM events WHERE start_date IS NOT NULL AND end_date IS NOT NULL AND start_date > end_date").fetchall()
    for bad in bad_dates:
        errors.append(f"event id={bad['id']}: start_date {bad['start_date']} > end_date {bad['end_date']}")
    
    # Empty titles
    for table, label in [("events", "title"), ("news", "title"), ("publications", "title"),
                         ("members", "display_name"), ("sponsors", "name")]:
        empties = conn.execute(f'SELECT id FROM "{table}" WHERE COALESCE({label},\'\')=\'\'').fetchall()
        for empty in empties:
            errors.append(f"empty {label} in {table} id={empty['id']}")
    
    return errors
```

- [ ] **Step 3: Integrate in `apply_bundle()`**

After the foreign_key_check and before final commit:
```python
invariant_errors = verify_invariants(conn)
if invariant_errors:
    raise RuntimeError(f"Invariant verification failed: {'; '.join(invariant_errors[:10])}")
```

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

---

### Task 8: Integration Tests

**Files:**
- Modify: `TESTS/webapp/test_data_quality_unit.py`

- [ ] **Step 1: Write integration tests**

```python
class TestIntegration:
    def test_analyze_idempotent(self):
        """Running analyze twice on the same DB produces same results."""
        pass  # full integration with in-memory SQLite
    
    def test_apply_then_verify(self):
        """After apply, invariants hold."""
        pass
```

- [ ] **Step 2: Run all tests**

```bash
python -m pytest TESTS/webapp/test_data_quality_unit.py -v
```

- [ ] **Step 3: Run full webapp test suite**

```bash
python -m pytest TESTS/webapp -q
```

- [ ] **Step 4: Commit final**

```bash
git add -A && git commit -m "test(dq): add integration tests for analyze idempotency and post-apply verification"
```

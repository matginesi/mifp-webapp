# DQ Simplify Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2-phase DQ interface + permanent resolution tracking (whack-a-mole fix)

**Architecture:** `resolved_pairs` table tracks content-fingerprints of resolved pairs; analyzer skips them; executor populates them after every action. Interface goes from 3-phase to 2-phase.

**Tech Stack:** Python 3.12+, Flask 3.x, SQLite, Tailwind CSS, vanilla JS

## Global Constraints

- All SQLite schema changes via `CREATE TABLE IF NOT EXISTS` — no destructive migrations
- Test coverage for new functions; existing tests must pass
- No new npm/pip dependencies
- Frontend: vanilla JS + existing Bootstrap/MIFP UI patterns

---

### Task 1: `content_fingerprint` + `resolved_pairs` table

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/normalizers.py`
- Modify: `MIFPAPP/CORE/mifp_app/db/schema.sql`
- Test: `TESTS/webapp/test_data_quality.py`

**Interfaces:**
- Produces: `content_fingerprint(record: dict) -> str`
- Produces: Table `resolved_pairs(entity_type, left_fp, right_fp, action, finding_id, bundle_id, applied_at)`

- [ ] **Add `content_fingerprint` to `normalizers.py`**

```python
def content_fingerprint(record: dict) -> str:
    exclude = {"id", "slug", "sort_order", "source_order",
               "display_order", "created_at", "updated_at"}
    clean = {k: v for k, v in record.items() if k not in exclude}
    raw = json.dumps(clean, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()
```

- [ ] **Add `resolved_pairs` to `schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS resolved_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    left_fingerprint TEXT NOT NULL,
    right_fingerprint TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('merged','rejected','cleaned','enriched','split')),
    finding_id INTEGER,
    bundle_id INTEGER,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, left_fingerprint, right_fingerprint)
);
```

- [ ] **Write tests**

```python
def test_content_fingerprint_ignores_id_and_slug(database: Path):
    a = {"id": 1, "slug": "old", "title": "Same content", "body": "Hello"}
    b = {"id": 2, "slug": "new", "title": "Same content", "body": "Hello"}
    assert content_fingerprint(a) == content_fingerprint(b)

def test_content_fingerprint_different_content_differs(database: Path):
    a = {"id": 1, "title": "Alpha", "body": "X"}
    b = {"id": 2, "title": "Beta", "body": "Y"}
    assert content_fingerprint(a) != content_fingerprint(b)
```

- [ ] **Run tests to verify they fail** (function not found)
- [ ] **Run tests to verify they pass** after adding the code

---

### Task 2: Analyzer skips resolved pairs + duplicate records

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/analyzer.py`
- Test: `TESTS/webapp/test_data_quality.py`

**Interfaces:**
- Consumes: `content_fingerprint()` from normalizers
- Consumes: Table `resolved_pairs`

- [ ] **In `_pairwise_findings()`, add resolved_pairs check**

```python
# After fetching record_a and record_b, before computing classification:
fp_a = content_fingerprint(record_a)
fp_b = content_fingerprint(record_b)
pair_key = tuple(sorted([fp_a, fp_b]))
resolved = conn.execute(
    "SELECT 1 FROM resolved_pairs WHERE entity_type=? AND left_fingerprint=? AND right_fingerprint=?",
    (entity_type, pair_key[0], pair_key[1]),
).fetchone()
if resolved:
    continue
```

- [ ] **Add skip for records already `duplicate`**

```python
# Before the pairwise loop, check each record:
if str(row.get("review_status") or "") in {"quarantined", "archived", "duplicate"}:
    continue  # already handled in a previous merge
```

- [ ] **Write tests**

```python
def test_resolved_pair_not_reported(database: Path):
    # Insert two news records + resolved_pairs entry for their content fingerprint
    # Run analyze → expect no findings for that pair

def test_duplicate_record_skipped(database: Path):
    # Insert a news record with review_status='duplicate'
    # Run analyze → expect no findings involving it
```

- [ ] **Run full test suite** — `python -m pytest TESTS/webapp -q`

---

### Task 3: Executor writes resolved_pairs after apply

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/data_quality/executor.py`

**Interfaces:**
- Consumes: `resolved_pairs` table
- Consumes: `content_fingerprint()` from normalizers

- [ ] **Add `_write_resolved_pair()` helper**

```python
def _write_resolved_pair(conn, entity_type, record, action, finding_id=None, bundle_id=None):
    fp = content_fingerprint(record)
    conn.execute(
        """INSERT OR IGNORE INTO resolved_pairs(entity_type,left_fingerprint,right_fingerprint,
           action,finding_id,bundle_id,applied_at)
           VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (entity_type, fp, fp, action, finding_id, bundle_id),
    )
```

- [ ] **Call it in `_apply_merge`** (after processing each non-canonical record):

```python
_write_resolved_pair(conn, entity_type, old, "merged", plan.get("finding_id"), bundle_id)
_write_resolved_pair(conn, entity_type, canonical, "merged", plan.get("finding_id"), bundle_id)
```

- [ ] **Call it in `_apply_clean`**:

```python
_write_resolved_pair(conn, entity_type, {id: record_id, ...}, "cleaned", ...)
```

- [ ] **Call it on reject/decision** (in `batch_reject_findings` + decision route):

```python
_write_resolved_pair(conn, entity_type, record, "rejected", finding_id)
```

- [ ] **Write tests**

```python
def test_apply_merge_writes_resolved_pair(database: Path):
    # Apply merge → check resolved_pairs has the entries

def test_reject_writes_resolved_pair(database: Path):
    # Reject finding → check resolved_pairs has the entry
```

- [ ] **Run full test suite**

---

### Task 4: Simplify interface to 2 phases

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/templates/dashboard/data_quality.html`
- Modify: `MIFPAPP/CORE/mifp_app/static/js/dashboard/data-quality.js`
- Modify: `MIFPAPP/CORE/mifp_app/static/css/dashboard.css`

- [ ] **Rewrite template to 2 phases**

Phase 1: Analysis — summary cards + progress
Phase 2: Apply — [Apply All] button + list of ambiguous findings only

- [ ] **Simplify JS**

Remove: acceptAll, forceBest, rejectAll, finding-level accept/reject buttons
Keep: analyze, progress poll, load findings, apply all
Add: "Ignore forever" button on ambiguous cards → POST to decision endpoint

- [ ] **Run full test suite** (including browser smoke test if available)

---

### Task 5: Run full test suite + verify

- [ ] `python -m pytest TESTS/webapp -q` — all pass
- [ ] `python -m pytest TESTS/scraper -q` — all pass (if scraper tests exist)

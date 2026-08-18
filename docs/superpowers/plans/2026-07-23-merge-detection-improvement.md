# Merge Detection Improvement — Implementation Plan

> **For agentic workers:** Implement all tasks in order.

**Goal:** Replace word-overlap Jaccard with multi-signal composite similarity in `find_merge_candidates`.

**Architecture:** 5 signal functions + 1 orchestrator, all in `services/importers.py`. The word-overlap block (lines 295–324) is replaced with a composite score call.

**Tech Stack:** Python stdlib only (difflib.SequenceMatcher, re, datetime, unicodedata).

## Global Constraints

- No new dependencies.
- No DB schema changes.
- No template/JS changes.
- Signal weights must sum to 1.0.

---

### Task 1: Add signal helper functions

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/importers.py` (insert before `find_merge_candidates`)

- [ ] Add `_title_ratio(title_a, title_b) → float`
- [ ] Add `_bigram_jaccard(title_a, title_b) → float`
- [ ] Add `_date_proximity(typ, row_a, row_b) → float`
- [ ] Add `_url_jaccard(conn, typ, id_a, id_b) → float`
- [ ] Add `_content_overlap(typ, row_a, row_b) → float`
- [ ] Add `_composite_similarity(conn, typ, row_a, row_b) → float`

### Task 2: Replace word-overlap block in `find_merge_candidates`

**Files:**
- Modify: `MIFPAPP/CORE/mifp_app/services/importers.py:295-324`

- [ ] Remove the word-overlap block, call `_composite_similarity` instead
- [ ] Add `confidences` dict parallel to `reasons`
- [ ] Set `"confidence": confidences.get(pair_key, "identity")` in pair output
- [ ] Test with COMPLETE_00 data

### Task 3: Verify with real import + merge test

- [ ] Run pytest suite
- [ ] Run manual test: import news twice with different slugs, verify merge candidates found

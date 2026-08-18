# Merge Detection Improvement — Design Doc

## Problem

`find_merge_candidates()` uses two passes: (1) exact identity keys (normalized title, email, DOI,
URL, series+year), and (2) a word-set Jaccard overlap ratio on titles. The word-overlap pass is
too primitive — it ignores word ordering, character-level variation, date proximity, URL overlap,
and content similarity — causing many true-similar records to be missed while letting through
false positives.

## Approach

**Multi-signal composite similarity.** For each pair of records not already clustered by identity
keys, compute a weighted combination of five similarity signals. If the composite score ≥ 0.55,
merge the clusters. A name-penalty is applied when the first 15 characters of the normalized
titles differ significantly (SequenceMatcher < 0.40), reducing false positives from structurally
similar titles about different subjects.

### Signals

| Signal | Weight | Method | Applicable to |
|---|---|---|---|
| Title ratio | 0.45 | `difflib.SequenceMatcher` on normalized title | all |
| Bigram Jaccard | 0.25 | character 2-gram Jaccard on normalized title | all |
| Date proximity | 0.15 | `1 - min(|days_diff|, 90) / 90` — same day=1, >90d=0 | news, events |
| URL overlap | 0.10 | Jaccard on source URLs from `entity_links` | all |
| Content overlap | 0.05 | `SequenceMatcher` on summary/body/abstract | news, events, members, publications |

When a signal is not available (e.g. date for sponsors), its weight is redistributed proportionally
to title ratio and bigram Jaccard.

### Name-penalty

Titles about different subjects but sharing a long structural suffix (e.g. "Prof. X Honorary
Professorship at State University of Vladimir" vs "Dr. Y Honorary Professorship at State
University of Vladimir") get a high SequenceMatcher ratio despite being unrelated. To counter
this, if the first 15 characters of the normalized titles have SequenceMatcher < 0.25, a -0.25
penalty is applied; if < 0.40, a -0.12 penalty.

### Threshold

Composite score ≥ 0.55 produces a cluster merge.

### Confidence

- Identity-key matches → `confidence: "identity"`
- Multi-signal matches → `confidence: "similar"`
- `reason` format: `"multi-signal (0.73)"` or existing identity key reasons

### No external dependencies

All signals use stdlib (`difflib`, `unicodedata`, `re`, `datetime`). The `SequenceMatcher` ratio
is already available — no pip install needed.

## Changes

All within `services/importers.py`:

1. Add `_composite_similarity(conn, typ, row_a, row_b) → float` — orchestrator
2. Add `_title_ratio(a, b) → float` — `SequenceMatcher` on `_identity_text`
3. Add `_bigram_jaccard(a, b) → float` — character 2-gram
4. Add `_date_proximity(typ, row_a, row_b) → float` — days between dates
5. Add `_url_jaccard(conn, typ, id_a, id_b) → float` — shared source URLs
6. Add `_content_overlap(typ, row_a, row_b) → float` — summary/body/abstract
7. Add `_composite_similarity(conn, typ, row_a, row_b, url_sets) → float` — orchestrator
8. Replace the word-overlap block in `find_merge_candidates` with composite call
9. Pre-fetch all entity_links URLs per table before the O(n²) loop for efficiency
10. Track `confidences` dict parallel to `reasons` for confidence: "identity" vs "similar"
11. No changes to database schema, templates, JS, or API contracts

## Testing

- `COMPLETE_00/` news data already has 3 known similar pairs at 55%+ word overlap
- Insert same records twice with different slugs, call `find_merge_candidates`, verify new pairs found
- Verify identity-key pairs still present with `confidence: "identity"`
- Verify `merge_records` works on multi-signal pairs (same function, no changes needed)

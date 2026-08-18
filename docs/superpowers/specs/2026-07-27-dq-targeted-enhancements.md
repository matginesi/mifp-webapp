# DQ Targeted Enhancements

## Scope

Evolve the existing 3-phase data quality system (analyze → bundle → apply) with targeted
improvements across 8 areas. No architecture rewrite — every change is incremental,
non-destructive, and tested.

## 1. Normalization

### New functions in `normalizers.py`

- `normalize_identity_text()` — most aggressive: strips diacritics, punctuation, articles,
  trailing parentheticals, org suffixes. For comparing identity-bearing fields where
  "Alexey Kavokin (University of Southampton)" should match "Alexey Kavokin".
- `normalize_person_name()` — same as existing `person_name()` but returns a stable
  canonical string `"last, first"` for direct equality checks.
- `normalize_title()` — keeps meaningful words, drops `| MIFP`, `— MIFP` suffixes,
  normalises quotes, removes boilerplate `"Home"`, `"Past event"`.
- `normalize_canonical_url()` — enhanced URL normalisation:
  - Aruba `media/.../v1/...` → stable path
  - `old.mifp.eu/...` → `www.mifp.eu/...`
  - `events.mifp.eu/...` → stable path
  - Remove utm\_\*, fbclid, gclid
  - Percent-decoding with safe roundtrip
  - HTTP/HTTPS considered equal on same host/path

### Improved existing

- `person_name()` — handle `"Cognome Nome, Nome Cognome"` comma-separated double name.
  Handle `"van der Waals"` style particles properly.
- `comparison_text()` — convert `&` ↔ `and` for comparison.

## 2. Junk/Technical Record Classification

### New checks in `_invalid_findings()` / `_quality_findings()`

Detect and classify as `junk_technical_record`:

- Titles that are only numbers, file sizes, page identifiers
- `publications/` path segments used as event titles
- `1 MB`, `04`, `13`, `Publications76C3` as titles
- Generic button/download text promoted to title
- Empty/numeric descriptions
- Suspiciously short names (single char, pure date strings)

These are routed to quarantine, not to merge candidates.

### New classification

Add `Classification.JUNK = "junk_technical_record"` and `Classification.FRAGMENT = "page_fragment_attached"`.

## 3. Page Fragment Detection (Events)

### New `_check_event_page_fragment()` in `analyzer.py`

Detect events whose title/URL suggests they are secondary pages of another event:

- `title` matches patterns: `"Topics"`, `"Fees"`, `"Program"`, `"Gallery"`,
  `"Registration"`, `"Committees"`, `"Speakers"`, `"Call for papers"`, `"Venue"`,
  `"Accommodation"`, `"Sponsors"`, `"Support"`, `"Proceedings"`, `"Template"`,
  `"Downloads"`, `"Important dates"`, `"Scope"`.
- URL path contains the same conference-series root as another event's URL.

Produces a `FRAGMENT` finding that proposes merging the fragment INTO the parent event
(absorb links, description, assets; then quarantine the fragment).

Uses URL normalisation to find the parent: if two events share a host/path prefix and
one has a fragment title, the fragment is absorbed.

## 4. Date Handling

### New `resolve_dates()` in `planner.py`

Rules for selecting the best date across records:

1. `YYYY-01-01` only kept if `date_precision == 'year'` and no better date exists.
2. Prefer `day > month > year` precision.
3. If `date_precision == 'range'` with `start=YYYY-01-01` and `end=YYYY-12-31`, demote
   to `precision=year` and clear end\_date. This is a year placeholder, not a range.
4. `end >= start` invariant enforced.
5. Date must be consistent with year mentioned in title/content — if title says
   "2018" and date is 2016, flag as conflict.
6. Do not infer dates from text; use `date_is_inferred` flag if needed.
7. Prefer real dates from the same source record (don't mix start from A and end from B
   unless they're from the same original record or explicitly compatible).

### New `_check_date_placeholder()` in `analyzer.py`

Specific check for `YYYY-01-01` / `YYYY-12-31` placeholder dates that aren't real.

## 5. Cluster Safety

### New module `cluster.py`

```python
def cluster_is_safe(cluster: list[tuple[int, dict]], entity_type: str, context: dict) -> ClusterSafety:
    """Check if every pair in a cluster is safe to merge transitively.
    
    Returns ClusterSafety(safe=True/False, reasons=[...], subclusters=[...]).
    """
```

Safety checks:
- No hard conflicts between ANY pair in the cluster
- Year/date consistency across all members
- Strong identifier consistency (DOI, email)
- Maximum similarity diameter (no record is an outlier)
- No bridge records (a generic record that connects two unrelated specific records)
- No cross-edition merging (same series, different years)
- No cross-type merging

If unsafe: attempt to split into safe subclusters using connected-components breakdown
with conflict edges removed.

### Integration in `_consolidate_exact_groups()` and `analyze()`

After building exact groups via union-find, run cluster safety check. Unsafe clusters
are downgraded to `manual_review_required` with documented reasons.

## 6. Field Resolution per Type

### Enhanced `_best_field_value()` in `planner.py`

Entity-specific field resolution rules:

**Member:**
- `email`: prefer personal emails over generic (`info@`, `contact@`, `admin@`)
- `affiliation`: prefer more specific over longer (score by known-institution match)
- `bio`: prefer longer + cleaner (boilerplate-removed)
- `first_name`/`last_name`: prefer `first_name + last_name` over display\_name
- `country`: must be consistent; if conflict, flag review

**News:**
- `title`: prefer descriptive title (>3 words, not just a name)
- `summary`: generate from body only if empty, truncated (<10 words), or identical to body
- `body`: prefer more complete (longer after boilerplate removal)
- `date`: prefer non-inferred, day-precision
- Remove `read more`, `continue reading` boilerplate from body

**Event:**
- `description`: prefer clean (no menu/cookie/nav); prefer shorter meaningful over long boilerplate
- `start_date`/`end_date`: from same source; prefer precision day > month > year
- `location`: prefer specific (city, country) over generic ("Excursion", "TBD")
- `remote_url`: prefer the root URL of the minisite, not a subpage

**Publication:**
- `title`: prefer full title from bibliographic metadata record
- `doi`: exact match is identity
- `authors`: prefer complete author list
- `abstract`: prefer longer + cleaner

**Sponsor:**
- `name`: prefer official name (with correct punctuation)
- `url`: prefer domain root

### Integration in `build_merge_plan()`

Replace generic field resolution with type-specific `_resolve_fields_<type>()` functions
called from `build_merge_plan()`.

## 7. Post-Apply Verification

### New `verify_invariants()` in `executor.py`

Called after every apply. Checks:

1. No orphaned references — all `entity_links`, `asset_links`, `entity_relations` point
   to existing entities.
2. No duplicate slugs per entity type (excluding `content_aliases`).
3. No duplicate DOIs in publications.
4. All events have `start_date <= end_date`.
5. No entity with empty title/name.
6. No asset linked more than once to the same entity with same role.
7. `source_count = canonical + merged + rejected` invariant.
8. `quality_bundles.status == 'applied'`.
9. Re-analysis of unmodified DB yields zero new findings.

If any invariant fails: auto-rollback the transaction and log the failure.
Log: `"verify_invariants: entity_type=X id=Y field=Z failed: reason"`.

### Integration in `apply_bundle()`

Call `verify_invariants()` immediately before the final `conn.commit()`.

## 8. Tests

### New test file `TESTS/webapp/test_data_quality_unit.py`

Fixtures (inline SQLite + small JSON fixtures):

- 3 copies of same member (exact duplicate)
- 2 members with inverted names
- 1 member with generic email
- News with short/generic title + descriptive title variant
- 2 agreements same orgs, different years
- Event canonical + topics/fees/gallery subpages
- 2 editions same conference (different years)
- Publication full + fragment "13"
- PDF present in only 1 copy
- Description (huge menu boilerplate + clean short desc)
- Date placeholder YYYY-01-01 and precise dates
- HTTP/HTTPS/slash-final URL variants

Test cases:

- `test_normalize_identity_text`
- `test_normalize_person_name`
- `test_person_name_inversion`
- `test_person_names_equivalent`
- `test_normalize_canonical_url` (Aruba, wrapper, tracking params)
- `test_normalized_doi`
- `test_junk_classifier` (numeric title, file size, page ID)
- `test_event_page_fragment_detection`
- `test_date_placeholder_detection`
- `test_date_resolution`
- `test_cluster_is_safe` (safe cluster, unsafe transitive, unsafe cross-year)
- `test_cluster_is_safe_splits`
- `test_evaluate_member` (exact, same-name-conflict-email, same-name-no-email)
- `test_evaluate_event` (same series different year, same event different URL, fragment)
- `test_evaluate_news` (same body, same subject different action, generic title blocked)
- `test_evaluate_publication` (same DOI, different DOI, same title + authors)
- `test_field_resolution_member`
- `test_field_resolution_news`
- `test_field_resolution_event`
- `test_field_resolution_publication`
- `test_verify_invariants`
- `test_invariants_fails_orphan`
- `test_invariants_fails_duplicate_slug`
- `test_analyze_idempotent`

### Test commands

```bash
python -m pytest TESTS/webapp/test_data_quality_unit.py -v
```

## Implementation Order

1. Normalization (`normalizers.py`) — foundation, no dependencies
2. Junk classification (`analyzer.py`) — depends on normalization
3. Page fragment detection (`analyzer.py`, `policies.py`) — depends on normalization
4. Date handling (`analyzer.py`, `planner.py`, `policies.py`)
5. Cluster safety (`cluster.py`) — depends on policies
6. Field resolution (`planner.py`) — depends on normalization
7. Post-apply verification (`executor.py`) — depends on everything
8. Tests — after every step, write tests for that step

Each step is independently testable and mergable.

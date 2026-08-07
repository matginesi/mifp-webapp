# MIFP Data Quality fix — 2026-08-07

This patch fixes the Data Quality false-positive flood reproduced from the supplied post-analysis export.

## Exact root cause of the 279 manual findings

Using the same analyzer version as the current MIFP backend:

- 56 were non-asset content findings;
- 223 were false missing-asset findings;
- 56 + 223 = 279.

The 223 asset findings came from two issues:

1. 182 canonical DB asset paths already start with `assets/...`, while `ASSETS_DIR` already points at the assets directory. The old analyzer therefore checked `.../assets/assets/...`.
2. 41 assets are intentionally `external` or `missing`; they must not be treated as local files requiring a human Data Quality decision.

## Code fixes

1. Resolve DB-tracked asset paths through the existing `resolve_db_asset_path()` helper.
2. Test on-disk existence only for assets with `storage_status=local` and `is_external=0`.
3. Do not flag legitimate names such as `Andrea D'Andrea` as first/last-name inversions merely because one token is a substring of the other.
4. Treat event suffixes such as `| ICP2DC5` as acronym/title separators instead of automatically classifying the event as an aggregated record.
5. Normalize filler terms (`MIFP`, `of`, `international`, etc.) when comparing event series so different yearly editions are kept separate.
6. Remove the unsafe news fallback that created manual candidates from same scraper + fuzzy-similar dates alone.
7. Tighten fuzzy-news fallback so title similarity alone is not sufficient.
8. Add regression tests covering these cases.

## Reproduction

Against the supplied 447-record post-import export, with the 205 locally-managed asset paths represented on disk:

- old analyzer: 279 manual decisions in the user's real run;
- patched analyzer before editorial event cleanup: 26 manual content findings, 0 manual asset findings;
- patched analyzer + `MIFP_EXPORT_CLEAN_V2_IMPORT_READY_2026-08-07.zip`: 411 records, 3 total findings:
  - 1 manual;
  - 2 informational;
  - 0 manual asset findings.

The one remaining manual item is a pair of distinct Megagrant news articles about different recipients. It should be kept separate rather than auto-merged.

## Apply

From the repository root:

```bash
patch -p1 < data-quality-fix.patch
```

or copy the included files over the same paths.

Then restart the app and run a new Data Quality analysis. Historical runs may remain visible, but the newest run uses the corrected analyzer.

## Validation note

The analyzer was exercised standalone against the reconstructed real dataset and schema. The full Flask pytest suite was not run in the analysis environment because Flask is not installed there; do not treat this package as claiming a full-suite pass. The modified Python files compile successfully and the standalone Data Quality regression produced the counts above.

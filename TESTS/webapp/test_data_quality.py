from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import mifp_app.services.data_quality.executor as quality_executor
from mifp_app.db.connection import connect
from mifp_app.db.migrations import migrate_content_schema
from mifp_app.services.data_quality import (
    add_to_bundle,
    analyze,
    apply_bundle,
    batch_add_to_bundle,
    batch_reject_findings,
    count_workflows,
    create_bundle,
    delete_draft,
    list_findings,
    validate_bundle,
)
from mifp_app.services.data_quality.normalizers import (
    aggregate_markers,
    clean_boilerplate,
    content_fingerprint,
    person_names_equivalent,
)
from mifp_app.services.data_quality.policies import (
    evaluate_event,
    evaluate_member,
    evaluate_news,
    evaluate_publication,
)
from mifp_app.services.data_quality.planner import build_merge_plan, records_for


def test_data_quality_public_api_exports_count_workflows():
    # dashboard_data_quality imports this from the package root; keep that
    # contract covered so application startup cannot regress silently.
    assert callable(count_workflows)


def test_blocked_finding_that_requires_review_is_manual_workflow():
    from mifp_app.services.data_quality.analyzer import finding_workflow

    finding = {
        "classification": "blocked",
        "action_type": "repair_relations_or_assets",
        "plan": {
            "operation": "recover_or_relink_missing_asset",
            "requires_review": True,
        },
    }
    assert finding_workflow(finding) == "manual"

    finding["plan"]["requires_review"] = False
    assert finding_workflow(finding) == "informational"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "quality.db"
    with connect(path) as conn:
        migrate_content_schema(conn)
    return path


def context() -> dict:
    return {"links": {}, "assets": {}}


def test_force_reimported_publication_is_detected_and_physically_merged(
    database: Path, tmp_path: Path
):
    from mifp_app.services.importers import import_jsonl

    source = tmp_path / "publication.jsonl"
    source.write_text(
        json.dumps({
            "type": "publication",
            "data": {
                "title": "A deterministic publication clone",
                "slug": "deterministic-publication-clone",
                "authors": "Ada Lovelace; Emmy Noether",
                "journal": "MIFP Journal",
                "year": 2026,
                "review_status": "published",
            },
            "links": [{
                "url": "https://example.test/papers/deterministic-clone",
                "role": "primary",
                "is_primary": True,
            }],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )

    with connect(database) as conn:
        first = import_jsonl(conn, source)
        forced = import_jsonl(conn, source, force_import=True)
        assert first["inserted"]["publication"] == 1
        assert forced["inserted"]["publication"] == 1
        assert conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 2

        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "publication"
            and item["classification"] == "exact_duplicate"
        )
        assert any(
            item["code"] == "forced_reimport_clone"
            for item in finding["evidence"]
        )
        bundle = create_bundle(conn, "tester")
        queued = batch_add_to_bundle(
            conn, bundle, run["run_id"], entity_type="publication"
        )
        assert queued["added"] == 1
        assert validate_bundle(conn, bundle)["valid"]

    report = apply_bundle(database, bundle)
    assert report["records_removed"] == 1

    with connect(database) as conn:
        publications = conn.execute(
            "SELECT id,slug,review_status FROM publications"
        ).fetchall()
        assert len(publications) == 1
        canonical_id = int(publications[0]["id"])
        assert publications[0]["slug"] == "deterministic-publication-clone"
        assert publications[0]["review_status"] == "published"
        assert conn.execute(
            "SELECT COUNT(*) FROM content_aliases "
            "WHERE entity_type='publication' "
            "AND old_slug='deterministic-publication-clone-2'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM import_records "
            "WHERE entity_type='publication' AND entity_id=?",
            (canonical_id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM entity_links "
            "WHERE entity_type='publication' AND entity_id=?",
            (canonical_id,),
        ).fetchone()[0] == 1


def test_legacy_publication_marked_duplicate_is_offered_for_safe_removal(
    database: Path,
):
    with connect(database) as conn:
        canonical_id = conn.execute(
            "INSERT INTO publications(title,slug,review_status) "
            "VALUES('Legacy publication','legacy-publication','published')"
        ).lastrowid
        duplicate_id = conn.execute(
            "INSERT INTO publications(title,slug,review_status) "
            "VALUES('Legacy publication','legacy-publication-2','duplicate')"
        ).lastrowid
        conn.execute(
            """
            INSERT INTO content_aliases(
                entity_type,old_slug,canonical_entity_id,canonical_slug
            ) VALUES('publication','legacy-publication-2',?,'legacy-publication')
            """,
            (canonical_id,),
        )
        conn.commit()
        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["record_ids"] == [duplicate_id]
            and item["plan"].get("operation") == "remove_merged_duplicate"
        )
        assert finding["classification"] == "needs_cleaning"
        bundle = create_bundle(conn, "tester")
        add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
        assert validate_bundle(conn, bundle)["valid"]

    apply_bundle(database, bundle)
    with connect(database) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM publications"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM publications WHERE id=?", (canonical_id,)
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("entity_type", "table", "data"),
    [
        ("member", "members", {
            "display_name": "Grace Hopper", "slug": "grace-hopper",
            "first_name": "Grace", "last_name": "Hopper",
        }),
        ("event", "events", {
            "title": "Deterministic Import Event", "slug": "deterministic-import-event",
            "start_date": "2026-09-10", "date_precision": "day",
        }),
        ("news", "news", {
            "title": "Deterministic import announcement",
            "slug": "deterministic-import-announcement",
            "body": "A complete announcement imported twice for verification.",
        }),
        ("publication", "publications", {
            "title": "Deterministic imported paper",
            "slug": "deterministic-imported-paper", "year": 2026,
        }),
        ("research_area", "research_areas", {
            "title": "Deterministic Research Area",
            "slug": "deterministic-research-area",
            "description": "A complete research area description.",
        }),
        ("page", "pages", {
            "title": "Deterministic Documentation Page",
            "slug": "deterministic-documentation-page",
            "type": "documentation", "body": "Complete documentation content.",
        }),
        ("sponsor", "sponsors", {
            "name": "Deterministic Sponsor", "slug": "deterministic-sponsor",
            "description": "A sponsor imported twice for verification.",
        }),
    ],
)
def test_force_reimport_clone_is_removed_for_every_importable_entity(
    database: Path,
    tmp_path: Path,
    entity_type: str,
    table: str,
    data: dict,
):
    from mifp_app.services.importers import import_jsonl

    source = tmp_path / f"{entity_type}.jsonl"
    source.write_text(
        json.dumps({
            "type": entity_type,
            "data": data,
            "links": [],
            "assets": [],
            "meta": {},
        }) + "\n",
        encoding="utf-8",
    )
    with connect(database) as conn:
        import_jsonl(conn, source)
        import_jsonl(conn, source, force_import=True)
        assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 2
        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == entity_type
            and item["classification"] == "exact_duplicate"
            and any(
                evidence["code"] == "forced_reimport_clone"
                for evidence in item["evidence"]
            )
        )
        bundle = create_bundle(conn, "tester")
        add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
        assert validate_bundle(conn, bundle)["valid"]

    report = apply_bundle(database, bundle)
    assert report["records_removed"] == 1
    with connect(database) as conn:
        rows = conn.execute(f'SELECT slug FROM "{table}"').fetchall()
        assert [row["slug"] for row in rows] == [data["slug"]]


@pytest.mark.parametrize("left,right", [
    ("Aldo Di Carlo", "Di Carlo Aldo"),
    ("Alexey Kavokin", "Kavokin Alexey"),
    ("Altshuler Boris", "Boris Altshuler"),
    ("Igor Lukyanchuk", "Lukyanchuk Igor"),
    ("Rinaldo Santonico", "Santonico Rinaldo"),
])
def test_member_name_inversion(left, right):
    assert person_names_equivalent(left, right)
    result = evaluate_member({"display_name": left}, {"display_name": right}, context())
    assert result[0] == "strong_candidate"


@pytest.mark.parametrize("left,right", [
    ("Aleiner Igor", "Lerner Igor"),
    ("Karabchevsky Alina", "Karabchevsky Serge"),
])
def test_shared_name_token_is_not_duplicate(left, right):
    assert not person_names_equivalent(left, right)
    assert evaluate_member({"display_name": left}, {"display_name": right}, context())[0] == "related_not_duplicate"


def test_event_series_different_year_is_never_merge():
    result = evaluate_event(
        {"id": 1, "title": "March Meeting 2018", "start_date": "2018-03-01"},
        {"id": 2, "title": "March Meeting 2019", "start_date": "2019-03-01"},
        context(),
    )
    assert result[0] == "related_not_duplicate"
    assert any(item.code == "different_event_year" for item in result[3])


def test_mifp_event_series_prefix_does_not_hide_different_editions():
    result = evaluate_event(
        {"id": 1, "title": "March Meeting 2018", "start_date": "2018-03-01"},
        {"id": 2, "title": "MIFP March Meeting 2024", "start_date": "2024-02-27"},
        context(),
    )
    assert result[0] == "related_not_duplicate"
    assert any(item.code == "different_event_year" for item in result[3])


def test_same_scraper_and_similar_date_do_not_make_unrelated_news_ambiguous():
    result = evaluate_news(
        {"id": 1, "title": "Quantum dots in photonics", "date": "2013-05-02", "source_kind": "legacy_scraper"},
        {"id": 2, "title": "University cooperation agreement", "date": "2013-07-02", "source_kind": "legacy_scraper"},
        context(),
    )
    assert result[0] == "related_not_duplicate"


def test_similar_award_headlines_with_different_people_are_not_duplicate_candidates():
    result = evaluate_news(
        {"id": 1, "title": "Aldo Di Carlo Wins Megagrant of the Russian Federation"},
        {"id": 2, "title": "Bernard Gil and Anvar Zakhidov Win Megagrants of the Russian Federation"},
        context(),
    )
    assert result[0] == "related_not_duplicate"


def test_member_substring_surname_is_not_reported_as_name_inversion():
    from mifp_app.services.data_quality.analyzer import _check_name_inversion

    assert _check_name_inversion({
        "id": 1,
        "first_name": "Andrea",
        "last_name": "D'Andrea",
        "display_name": "Andrea D'Andrea",
    }) is None


def test_pipe_before_event_acronym_is_not_aggregated_record():
    from mifp_app.services.data_quality.analyzer import _check_aggregated_event

    assert _check_aggregated_event({
        "id": 1,
        "title": "International Conference on Physics of 2D Crystals 2020 | ICP2DC5",
    }) is None


def test_generic_news_titles_have_insufficient_identity():
    result = evaluate_news({"id": 1, "title": "News"}, {"id": 2, "title": "News"}, context())
    assert result[0] == "blocked"
    assert result[3][0].code == "insufficient_identity"


def test_publication_archive_boilerplate_is_aggregated_not_merge_identity():
    text = """First paper abstract.
Start Prev Next Files
Uploaded: 2020-01-01 File Size: 2 MB Download
Second paper title and abstract with enough scientific text.
Page 2 of 4 Results 11 - 20 of 34
Third paper title and abstract with more scientific text."""
    assert aggregate_markers(text)
    cleaned, removed = clean_boilerplate(text)
    assert removed
    result = evaluate_publication(
        {"id": 1, "title": "First paper", "abstract": text},
        {"id": 2, "title": "Second paper", "abstract": "Scientific abstract"},
        context(),
    )
    assert result[0] == "blocked"


def test_analysis_creates_distinct_action_types_without_mutating_content(database: Path):
    with connect(database) as conn:
        conn.execute("INSERT INTO members(display_name,slug,affiliation) VALUES('Aldo Di Carlo','aldo-di-carlo','University of Rome II')")
        conn.execute("INSERT INTO members(display_name,slug,affiliation) VALUES('Di Carlo Aldo','di-carlo-aldo','Engineering Department, University of Rome II, Italy')")
        conn.execute("INSERT INTO events(title,slug,start_date,end_date,date_precision) VALUES('March Meeting 2018','march-2018','2018-01-01','2018-12-31','range')")
        conn.execute("INSERT INTO events(title,slug) VALUES('404 View not found','404-view-not-found')")
        conn.execute("INSERT INTO news(title,slug) VALUES('News','news-placeholder')")
        conn.execute(
            "INSERT INTO publications(title,slug,abstract) VALUES(?,?,?)",
            ("Container", "container", "Start Prev Next Files\nFirst long scientific entry. Uploaded: 2020 File Size: 1MB Download\nSecond long scientific entry with authors and abstract. Page 2 of 4 Results 11 - 20 of 34"),
        )
        conn.commit()
        before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("members", "events", "news", "publications")}
        result = analyze(conn)
        findings = list_findings(conn, result["run_id"])
        after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
    assert before == after
    assert any(item["action_type"] == "merge_records" and item["entity_type"] == "member" for item in findings)
    assert any(item["classification"] == "invalid_record" for item in findings)
    assert any(item["action_type"] == "clean_record" for item in findings)
    assert any(item["action_type"] == "split_aggregated_record" for item in findings)


def test_analysis_reports_missing_asset_files_without_offering_unsafe_repair(database: Path, tmp_path: Path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    with connect(database) as conn:
        asset_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,source_url) VALUES('missing.jpg','missing.jpg','image','https://example.test/missing.jpg')"
        ).lastrowid
        conn.commit()
        result = analyze(conn, assets_dir=assets_dir)
        finding = next(
            item for item in list_findings(conn, result["run_id"])
            if item["entity_type"] == "asset" and item["record_ids"] == [asset_id]
        )
    assert finding["action_type"] == "repair_relations_or_assets"
    assert finding["classification"] == "blocked"
    assert finding["evidence"][0]["code"] == "missing_asset_file"


def test_analysis_accepts_canonical_assets_prefix(database: Path, tmp_path: Path):
    assets_dir = tmp_path / "assets"
    image = assets_dir / "image" / "present.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    with connect(database) as conn:
        asset_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,storage_status,is_external) "
            "VALUES('present.jpg','assets/image/present.jpg','image','local',0)"
        ).lastrowid
        conn.commit()
        result = analyze(conn, assets_dir=assets_dir)
        findings = list_findings(conn, result["run_id"], entity_type="asset")
    assert not any(item["record_ids"] == [asset_id] for item in findings)


def test_analysis_does_not_require_external_or_missing_assets_on_disk(database: Path, tmp_path: Path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    with connect(database) as conn:
        external_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,storage_status,is_external,source_url) "
            "VALUES('external.pdf','external/external.pdf','pdf','external',1,'https://example.test/external.pdf')"
        ).lastrowid
        missing_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,storage_status,is_external,source_url) "
            "VALUES('missing.pdf','assets/pdf/missing.pdf','pdf','missing',0,'https://example.test/missing.pdf')"
        ).lastrowid
        conn.commit()
        result = analyze(conn, assets_dir=assets_dir)
        findings = list_findings(conn, result["run_id"], entity_type="asset")
    finding_ids = {rid for item in findings for rid in item["record_ids"]}
    assert external_id not in finding_ids
    assert missing_id not in finding_ids


def test_new_schema_contains_only_quality_system(database: Path):
    with connect(database) as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"quality_runs", "quality_findings", "merge_exclusions", "quality_bundles", "quality_bundle_items"} <= tables
    assert not {"merge_analyses", "merge_suggestions", "merge_decisions", "merge_bundles", "merge_bundle_items"} & tables


def test_dry_run_detects_record_changed_after_analysis(database: Path):
    with connect(database) as conn:
        conn.execute("INSERT INTO members(display_name,slug) VALUES('Alexey Kavokin','alexey-kavokin')")
        conn.execute("INSERT INTO members(display_name,slug) VALUES('Kavokin Alexey','kavokin-alexey')")
        conn.commit()
        result = analyze(conn)
        finding = next(item for item in list_findings(conn, result["run_id"]) if item["action_type"] == "merge_records")
        bundle = create_bundle(conn, "tester")
        add_to_bundle(conn, bundle, finding["id"])
        assert validate_bundle(conn, bundle)["valid"]
        conn.execute("UPDATE members SET affiliation='Changed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (finding["record_ids"][0],))
        conn.commit()
        report = validate_bundle(conn, bundle)
    assert not report["valid"]
    assert report["errors"] == ["Bundle has no executable actions"]
    assert any("source changed after analysis" in warning for warning in report["warnings"])


def test_best_quality_strategy_resolves_longest_clean_information(database: Path):
    with connect(database) as conn:
        conn.execute("INSERT INTO members(display_name,slug,bio) VALUES('Alexey Kavokin','alexey-kavokin','Short bio')")
        conn.execute(
            "INSERT INTO members(display_name,slug,bio) VALUES('Kavokin Alexey','kavokin-alexey',?)",
            ("A substantially more complete scientific biography with affiliations, research interests and awards.",),
        )
        conn.commit()
        result = analyze(conn)
        finding = next(item for item in list_findings(conn, result["run_id"]) if item["action_type"] == "merge_records")
        bundle = create_bundle(conn, "tester")
        plan = add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
    bio = next(field for field in plan["fields"] if field["field"] == "bio")
    assert plan["selection_strategy"] == "best_quality"
    assert "substantially more complete" in bio["proposed_value"]
    assert bio["action"] == "best_quality_choice"
    assert not bio["requires_review"]


def test_merge_bundle_creates_backup_alias_and_preserves_asset(database: Path, tmp_path: Path):
    asset_path = tmp_path / "photo.jpg"
    asset_path.write_bytes(b"image")
    with connect(database) as conn:
        first = conn.execute("INSERT INTO members(display_name,slug) VALUES('Igor Lukyanchuk','igor-lukyanchuk')").lastrowid
        second = conn.execute("INSERT INTO members(display_name,slug) VALUES('Lukyanchuk Igor','lukyanchuk-igor')").lastrowid
        asset = conn.execute(
            "INSERT INTO assets(filename,path,kind,checksum,width,height,size) VALUES('photo.jpg',?,'image','fixture-checksum',320,240,1000)",
            (str(asset_path),),
        ).lastrowid
        conn.execute("INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary) VALUES(?,'member',?,'profile',1)", (asset, second))
        best_path = tmp_path / "photo-large.jpg"
        best_path.write_bytes(b"larger-image")
        best_asset = conn.execute(
            "INSERT INTO assets(filename,path,kind,checksum,width,height,size) VALUES('photo-large.jpg',?,'image','fixture-checksum-large',1920,1080,5000)",
            (str(best_path),),
        ).lastrowid
        conn.execute("INSERT INTO asset_links(asset_id,entity_type,entity_id,role,is_primary) VALUES(?,'member',?,'profile',0)", (best_asset, first))
        conn.commit()
        result = analyze(conn)
        finding = next(item for item in list_findings(conn, result["run_id"]) if set(item["record_ids"]) == {first, second})
        bundle = create_bundle(conn, "tester")
        add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
        assert validate_bundle(conn, bundle)["valid"]
    report = apply_bundle(database, bundle)
    assert Path(report["backup_path"]).is_file()
    with connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM members WHERE id IN (?,?) AND review_status='published'", (first, second)).fetchone()[0] == 1
        alias = conn.execute("SELECT canonical_entity_id FROM content_aliases WHERE old_slug IN ('igor-lukyanchuk','lukyanchuk-igor')").fetchone()
        assert alias
        assert conn.execute("SELECT COUNT(*) FROM asset_links WHERE asset_id=?", (asset,)).fetchone()[0] == 1
        assert conn.execute("SELECT is_primary FROM asset_links WHERE asset_id=?", (best_asset,)).fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
    assert asset_path.is_file()


def test_event_merge_remaps_hierarchy_without_foreign_key_errors(database: Path):
    with connect(database) as conn:
        old_id = conn.execute(
            "INSERT INTO events(title,slug,review_status) VALUES(?,?,?)",
            ("Duplicate parent", "duplicate-parent", "review"),
        ).lastrowid
        canonical_id = conn.execute(
            "INSERT INTO events(title,slug,parent_event_id,review_status) VALUES(?,?,?,?)",
            ("Canonical event", "canonical-event", old_id, "review"),
        ).lastrowid
        child_id = conn.execute(
            "INSERT INTO events(title,slug,parent_event_id,review_status) VALUES(?,?,?,?)",
            ("Child session", "child-session", old_id, "review"),
        ).lastrowid
        bundle_id = create_bundle(conn, "tester")
        plan = build_merge_plan(
            conn,
            "event",
            records_for(conn, "event", [old_id, canonical_id]),
        )
        plan["canonical_id"] = canonical_id
        # Exercise compatibility with plans queued before FK-aware planning.
        parent_field = next(
            field for field in plan["fields"] if field["field"] == "parent_event_id"
        )
        assert parent_field["action"] == "preserve_relationship"
        parent_field["proposed_value"] = old_id

        quality_executor._apply_merge(conn, plan, bundle_id)
        conn.commit()

        canonical = conn.execute(
            "SELECT parent_event_id FROM events WHERE id=?", (canonical_id,)
        ).fetchone()
        child = conn.execute(
            "SELECT parent_event_id FROM events WHERE id=?", (child_id,)
        ).fetchone()
        assert canonical["parent_event_id"] is None
        assert child["parent_event_id"] == canonical_id
        assert conn.execute(
            "SELECT 1 FROM events WHERE id=?", (old_id,)
        ).fetchone() is None
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None


def test_news_with_different_titles_and_same_content_is_proposed(database: Path):
    body = "The institute announces a quantum photonics programme with international research partners."
    with connect(database) as conn:
        conn.execute(
            "INSERT INTO news(title,slug,body,date) VALUES(?,?,?,?)",
            ("New quantum programme announced", "quantum-programme", body, "2026-04-12"),
        )
        conn.execute(
            "INSERT INTO news(title,slug,body,date) VALUES(?,?,?,?)",
            ("International partners join photonics initiative", "partners-photonics", body, "2026-04-13"),
        )
        conn.commit()
        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "news" and item["action_type"] == "merge_records"
        )
    assert finding["classification"] == "exact_duplicate"
    date_field = next(field for field in finding["plan"]["fields"] if field["field"] == "date")
    assert date_field["requires_review"]


def test_generic_news_content_is_automatically_titled_and_duplicate_text_matches(database: Path):
    body = "International researchers launch a new photonics collaboration programme."
    with connect(database) as conn:
        conn.execute("INSERT INTO news(title,slug,body) VALUES('News','generic-one',?)", (body,))
        conn.execute("INSERT INTO news(title,slug,body) VALUES('XHR News','generic-two',?)", (body,))
        conn.execute("INSERT INTO news(title,slug) VALUES('News','generic-empty')")
        conn.commit()
        run = analyze(conn)
        findings = list_findings(conn, run["run_id"])
    automatic_titles = [
        item for item in findings
        if item["action_type"] == "clean_record"
        and item["classification"] == "needs_cleaning"
        and item["entity_type"] == "news"
    ]
    assert len(automatic_titles) == 2
    assert all(item["plan"]["fields"][0]["proposed_value"] == body for item in automatic_titles)
    assert any(
        item["action_type"] == "merge_records"
        and item["classification"] == "exact_duplicate"
        for item in findings
    )
    assert not any(
        item["action_type"] == "merge_records"
        and item["classification"] == "blocked"
        and any(evidence["code"] == "insufficient_identity" for evidence in item["contradictions"])
        for item in findings
    )


def test_generic_news_without_content_is_quarantined_without_nulling_title(database: Path):
    with connect(database) as conn:
        news_id = conn.execute(
            "INSERT INTO news(title,slug) VALUES('News','empty-generic-news')"
        ).lastrowid
        conn.commit()
        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "news" and item["record_ids"] == [news_id]
        )
        bundle = create_bundle(conn, "tester")
        add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
        assert validate_bundle(conn, bundle)["valid"]
    apply_bundle(database, bundle)
    with connect(database) as conn:
        row = conn.execute("SELECT title,review_status FROM news WHERE id=?", (news_id,)).fetchone()
    assert row["title"] == "News"
    assert row["review_status"] == "quarantined"
    with connect(database) as conn:
        rerun = analyze(conn)
        assert not any(
            item["record_ids"] == [news_id]
            for item in list_findings(conn, rerun["run_id"])
        )


def test_shared_asset_alone_not_exact_duplicate(database: Path):
    """Sharing the same binary asset is not sufficient to classify news as duplicates."""
    with connect(database) as conn:
        news_ids = [
            conn.execute(
                "INSERT INTO news(title,slug,body) VALUES(?,?,?)",
                (title, slug, body),
            ).lastrowid
            for title, slug, body in (
                ("Research result announced", "result-announced", "A short announcement."),
                ("Congratulations to the research team", "team-congratulations", "Award details."),
                ("Download the scientific paper", "scientific-paper", "Publication information."),
            )
        ]
        asset_id = conn.execute(
            "INSERT INTO assets(filename,path,kind,checksum) VALUES('paper.pdf','paper.pdf','pdf','same-pdf-checksum')"
        ).lastrowid
        for news_id in news_ids:
            conn.execute(
                "INSERT INTO asset_links(asset_id,entity_type,entity_id,role) VALUES(?,'news',?,'document')",
                (asset_id, news_id),
            )
        conn.commit()
        run = analyze(conn)
        exact = [
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "news"
            and item["classification"] == "exact_duplicate"
        ]
    assert len(exact) == 0, "Shared asset alone should not produce exact_duplicate"


def test_force_mode_queues_high_confidence_ambiguous_news(database: Path):
    with connect(database) as conn:
        conn.execute(
            "INSERT INTO news(title,slug,body) VALUES('Photonics research programme announced by MIFP today','quantum-programme-a','This is the first complete version of a detailed article')"
        )
        conn.execute(
            "INSERT INTO news(title,slug,body) VALUES('Photonics research programme announced at conference','quantum-programme-b','A short update notice')"
        )
        conn.commit()
        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "news" and item["classification"] == "ambiguous"
        )
        assert finding["score"] >= .65
        safe_bundle = create_bundle(conn, "tester")
        safe = batch_add_to_bundle(conn, safe_bundle, run["run_id"], classification="reviewable")
        assert safe["added"] == 0
        delete_draft(conn, safe_bundle)
        forced_bundle = create_bundle(conn, "tester")
        forced = batch_add_to_bundle(conn, forced_bundle, run["run_id"], classification="reviewable", force=True)
        assert forced["added"] == 1
        assert validate_bundle(conn, forced_bundle)["valid"]


def test_client_cannot_change_plan_record_ids(database: Path):
    with connect(database) as conn:
        first = conn.execute("INSERT INTO members(display_name,slug) VALUES('Alexey Kavokin','alexey-kavokin')").lastrowid
        second = conn.execute("INSERT INTO members(display_name,slug) VALUES('Kavokin Alexey','kavokin-alexey')").lastrowid
        unrelated = conn.execute("INSERT INTO members(display_name,slug) VALUES('Other Person','other-person')").lastrowid
        conn.commit()
        run = analyze(conn)
        finding = next(item for item in list_findings(conn, run["run_id"]) if set(item["record_ids"]) == {first, second})
        bundle = create_bundle(conn, "tester")
        forged = dict(finding["plan"])
        forged["record_ids"] = [first, unrelated]
        with pytest.raises(ValueError, match="record_ids cannot be changed"):
            add_to_bundle(conn, bundle, finding["id"], {"plan": forged})


def test_manual_field_choice_is_preserved_and_auditable(database: Path):
    with connect(database) as conn:
        conn.execute("INSERT INTO members(display_name,slug,bio) VALUES('Igor Lukyanchuk','igor-lukyanchuk','First biography')")
        conn.execute("INSERT INTO members(display_name,slug,bio) VALUES('Lukyanchuk Igor','lukyanchuk-igor','Second biography')")
        conn.commit()
        run = analyze(conn)
        finding = next(item for item in list_findings(conn, run["run_id"]) if item["action_type"] == "merge_records")
        reviewed = dict(finding["plan"])
        reviewed["fields"] = [
            {**field, "proposed_value": "Administrator consolidated biography"}
            if field["field"] == "bio" else field
            for field in reviewed["fields"]
        ]
        bundle = create_bundle(conn, "tester")
        plan = add_to_bundle(conn, bundle, finding["id"], {"plan": reviewed})
    bio = next(field for field in plan["fields"] if field["field"] == "bio")
    assert bio["proposed_value"] == "Administrator consolidated biography"
    assert bio["action"] == "administrator_choice"


def test_bundle_failure_rolls_back_every_content_change(database: Path, monkeypatch: pytest.MonkeyPatch):
    with connect(database) as conn:
        for name, slug in (
            ("Alexey Kavokin", "alexey-kavokin"),
            ("Kavokin Alexey", "kavokin-alexey"),
            ("Igor Lukyanchuk", "igor-lukyanchuk"),
            ("Lukyanchuk Igor", "lukyanchuk-igor"),
        ):
            conn.execute("INSERT INTO members(display_name,slug) VALUES(?,?)", (name, slug))
        conn.commit()
        run = analyze(conn)
        findings = [
            item for item in list_findings(conn, run["run_id"])
            if item["action_type"] == "merge_records"
        ]
        bundle = create_bundle(conn, "tester")
        for finding in findings:
            add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
        assert validate_bundle(conn, bundle)["valid"]
        before = [tuple(row) for row in conn.execute("SELECT id,display_name,slug FROM members ORDER BY id")]

    original_apply_merge = quality_executor._apply_merge
    calls = 0

    def fail_on_second(conn, plan, bundle_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated bundle failure")
        original_apply_merge(conn, plan, bundle_id)

    monkeypatch.setattr(quality_executor, "_apply_merge", fail_on_second)
    with pytest.raises(RuntimeError, match="simulated bundle failure"):
        apply_bundle(database, bundle)

    with connect(database) as conn:
        after = [tuple(row) for row in conn.execute("SELECT id,display_name,slug FROM members ORDER BY id")]
        status = conn.execute("SELECT status FROM quality_bundles WHERE id=?", (bundle,)).fetchone()["status"]
    assert after == before
    assert status == "failed"


def test_batch_accept_skips_manual_splits_and_overlapping_actions(database: Path):
    with connect(database) as conn:
        first = conn.execute(
            "INSERT INTO members(display_name,slug,bio) VALUES('Aldo Di Carlo','aldo-di-carlo','Navigation Home Profile')"
        ).lastrowid
        conn.execute(
            "INSERT INTO members(display_name,slug) VALUES('Di Carlo Aldo','di-carlo-aldo')"
        )
        conn.execute(
            "INSERT INTO members(display_name,slug) VALUES('Aldo Di Carlo','aldo-di-carlo-2')"
        )
        conn.execute(
            "INSERT INTO publications(title,slug,abstract) VALUES(?,?,?)",
            (
                "Container",
                "container",
                "Start Prev Next Files\nFirst scientific paper with a sufficiently descriptive title. Uploaded: 2020 File Size: 1MB Download\nSecond scientific paper with another sufficiently descriptive title. Page 2 of 4 Results 11 - 20 of 34",
            ),
        )
        conn.execute("INSERT INTO news(title,slug) VALUES('News','generic-news-1')")
        conn.execute("INSERT INTO news(title,slug) VALUES('News','generic-news-2')")
        conn.commit()
        run = analyze(conn)
        findings = list_findings(conn, run["run_id"])
        assert any(item["action_type"] == "split_aggregated_record" for item in findings)
        assert sum(first in item["record_ids"] for item in findings) >= 2
        bundle = create_bundle(conn, "tester")
        result = batch_add_to_bundle(conn, bundle, run["run_id"])
        report = validate_bundle(conn, bundle)
        assert result["errors"] == 0
        assert report["valid"]



def test_validate_bundle_recovers_legacy_split_titles(database: Path):
    """Legacy split bundles with title_hint/segment must not fail the whole apply preflight."""
    with connect(database) as conn:
        conn.execute(
            "INSERT INTO publications(title,slug,abstract) VALUES(?,?,?)",
            (
                "Container",
                "legacy-split-container",
                "First scientific paper with a sufficiently descriptive title. Uploaded: 2020 File Size: 1MB Download\n"
                "Second scientific paper with another sufficiently descriptive title. Page 2 of 4 Results 11 - 20 of 34",
            ),
        )
        conn.commit()
        run = analyze(conn)
        finding = next(
            item for item in list_findings(conn, run["run_id"])
            if item["action_type"] == "split_aggregated_record"
        )
        legacy_plan = json.loads(json.dumps(finding["plan"]))
        assert len(legacy_plan.get("proposed_records") or []) >= 2
        for proposed in legacy_plan["proposed_records"]:
            proposed.pop("title", None)
            assert proposed.get("title_hint") or proposed.get("segment")

        bundle = create_bundle(conn, "tester")
        conn.execute(
            "INSERT INTO quality_bundle_items(bundle_id,finding_id,action_type,payload_json) VALUES(?,?,?,?)",
            (bundle, finding["id"], "split_aggregated_record", json.dumps({"plan": legacy_plan})),
        )
        conn.execute("UPDATE quality_findings SET status='bundled' WHERE id=?", (finding["id"],))
        conn.commit()

        report = validate_bundle(conn, bundle)
        assert report["valid"], report
        assert any("recovered legacy split titles" in warning for warning in report["warnings"])
        stored = conn.execute(
            "SELECT payload_json FROM quality_bundle_items WHERE bundle_id=?", (bundle,)
        ).fetchone()["payload_json"]
        stored_plan = json.loads(stored)["plan"]
        assert all(str(item.get("title") or "").strip() for item in stored_plan["proposed_records"])

def test_content_fingerprint_ignores_id_and_slug(database: Path):
    a = {"id": 1, "slug": "old", "title": "Same content", "body": "Hello"}
    b = {"id": 2, "slug": "new", "title": "Same content", "body": "Hello"}
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_different_content_differs(database: Path):
    a = {"id": 1, "title": "Alpha", "body": "X"}
    b = {"id": 2, "title": "Beta", "body": "Y"}
    assert content_fingerprint(a) != content_fingerprint(b)


def test_resolved_pair_not_reported(database: Path):
    from mifp_app.services.data_quality.normalizers import content_fingerprint
    with connect(database) as conn:
        id_a = conn.execute("INSERT INTO news(title,slug,body) VALUES('News','news-a','Same body for testing')").lastrowid
        id_b = conn.execute("INSERT INTO news(title,slug,body) VALUES('News','news-b','Same body for testing')").lastrowid
        conn.commit()
        fp_a = content_fingerprint(dict(conn.execute("SELECT * FROM news WHERE id=?", (id_a,)).fetchone()))
        fp_b = content_fingerprint(dict(conn.execute("SELECT * FROM news WHERE id=?", (id_b,)).fetchone()))
        conn.execute(
            "INSERT INTO resolved_pairs(entity_type,left_fingerprint,right_fingerprint,action) VALUES(?,?,?,?)",
            ("news", fp_a, fp_b, "merged"),
        )
        conn.commit()
        run = analyze(conn)
        findings = [
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "news" and item["action_type"] == "merge_records"
        ]
    assert len(findings) == 0, "Resolved pair should not be reported"


def test_duplicate_record_skipped(database: Path):
    with connect(database) as conn:
        conn.execute("INSERT INTO news(title,slug,body,review_status) VALUES('News','news-dup','Some body','duplicate')")
        conn.execute("INSERT INTO news(title,slug,body) VALUES('News','news-pub','Some body')")
        conn.commit()
        run = analyze(conn)
        findings = [
            item for item in list_findings(conn, run["run_id"])
            if item["entity_type"] == "news" and item["action_type"] == "merge_records"
        ]
    assert len(findings) == 0, "Duplicate records should not generate merge findings"


def test_apply_merge_writes_resolved_pair(database: Path):
    with connect(database) as conn:
        first = conn.execute("INSERT INTO members(display_name,slug) VALUES('Igor Lukyanchuk','igor-lukyanchuk')").lastrowid
        second = conn.execute("INSERT INTO members(display_name,slug) VALUES('Lukyanchuk Igor','lukyanchuk-igor')").lastrowid
        conn.commit()
        result = analyze(conn)
        finding = next(item for item in list_findings(conn, result["run_id"]) if item["action_type"] == "merge_records")
        bundle = create_bundle(conn, "tester")
        add_to_bundle(conn, bundle, finding["id"], {"strategy": "best_quality"})
        assert validate_bundle(conn, bundle)["valid"]
    apply_bundle(database, bundle)
    with connect(database) as conn:
        rows = conn.execute(
            "SELECT entity_type, action, left_fingerprint, right_fingerprint FROM resolved_pairs WHERE entity_type='member' AND action='merged'"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["left_fingerprint"] == rows[0]["right_fingerprint"]
        assert rows[1]["left_fingerprint"] == rows[1]["right_fingerprint"]


def test_reject_writes_resolved_pair(database: Path):
    with connect(database) as conn:
        first = conn.execute("INSERT INTO members(display_name,slug) VALUES('Aldo Di Carlo','aldo-di-carlo')").lastrowid
        second = conn.execute("INSERT INTO members(display_name,slug) VALUES('Di Carlo Aldo','di-carlo-aldo')").lastrowid
        conn.commit()
        result = analyze(conn)
        assert any(item["action_type"] == "merge_records" for item in list_findings(conn, result["run_id"]))
        report = batch_reject_findings(conn, result["run_id"])
        assert report["rejected"] > 0
        rows = conn.execute(
            "SELECT entity_type, action, left_fingerprint, right_fingerprint FROM resolved_pairs WHERE entity_type='member' AND action='rejected'"
        ).fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row["left_fingerprint"] == row["right_fingerprint"]


def test_data_quality_deterministic_cleanup_is_automatic_even_when_large():
    from mifp_app.services.data_quality.analyzer import finding_workflow

    finding = {
        "classification": "needs_cleaning",
        "action_type": "clean_record",
        "score": 0.9,
        "evidence": [{"code": "scraper_boilerplate", "strength": "strong"}],
        "contradictions": [],
        "plan": {
            "fields": [{
                "field": "body",
                "proposed_value": "Useful scientific content remains.",
                "action": "replace_with_cleaned",
                # Old policy made the whole finding manual merely because the
                # cleaned text was much shorter than scraper garbage.
                "requires_review": True,
            }]
        },
    }
    assert finding_workflow(finding) == "automatic"


def test_data_quality_exact_duplicate_stays_automatic_with_conflicting_descriptive_fields():
    from mifp_app.services.data_quality.analyzer import finding_workflow

    finding = {
        "classification": "exact_duplicate",
        "action_type": "merge_records",
        "score": 1.0,
        "evidence": [{"code": "same_doi", "strength": "deterministic"}],
        "contradictions": [],
        "plan": {
            "record_ids": [1, 2],
            "fields": [{
                "field": "abstract",
                "proposed_value": "Longer abstract",
                "action": "manual_edit_required",
                "requires_review": True,
            }],
        },
    }
    assert finding_workflow(finding) == "automatic"


def test_data_quality_only_very_high_confidence_strong_merge_is_automatic():
    from mifp_app.services.data_quality.analyzer import finding_workflow

    base = {
        "classification": "strong_candidate",
        "action_type": "merge_records",
        "evidence": [{"code": "same_headline_compatible_date", "strength": "strong"}],
        "contradictions": [],
        "plan": {"record_ids": [1, 2], "fields": []},
    }
    assert finding_workflow({**base, "score": 0.98}) == "automatic"
    assert finding_workflow({**base, "score": 0.90}) == "manual"


def test_data_quality_technical_junk_is_automatic_reversible_quarantine():
    from mifp_app.services.data_quality.analyzer import finding_workflow

    finding = {
        "classification": "junk_technical_record",
        "action_type": "clean_record",
        "score": 1.0,
        "evidence": [{"code": "junk_technical_title", "strength": "deterministic"}],
        "contradictions": [],
        "plan": {"operation": "quarantine", "requires_review": False},
    }
    assert finding_workflow(finding) == "automatic"

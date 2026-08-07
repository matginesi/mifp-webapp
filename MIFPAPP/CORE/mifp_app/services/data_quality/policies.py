from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .models import Classification, Evidence
from .normalizers import (
    aggregate_markers,
    classify_url,
    comparison_text,
    normalize_url,
    normalized_doi,
    person_names_equivalent,
    tokens,
    years,
)

_NEWS_STOP = frozenset({"mifp", "professor", "dr", "the", "and", "prof",
                         "on", "to", "for", "in", "its", "their", "her",
                         "his", "our", "with", "from", "after", "before"})


def _e(code: str, strength: str, explanation: str, values: list[Any] | None = None) -> Evidence:
    return Evidence(code, strength, explanation, values or [])


def similarity(left: object, right: object) -> float:
    a, b = comparison_text(left), comparison_text(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _shared_urls(urls_a: set[str], urls_b: set[str], kind: str) -> set[str]:
    return {u for u in (urls_a & urls_b) if classify_url(u) == kind}


def _display_name(row: dict) -> str:
    return str(row.get("display_name") or f"{row.get('first_name') or ''} {row.get('last_name') or ''}").strip()


def evaluate_member(a: dict, b: dict, context: dict) -> tuple[Classification, float, list[Evidence], list[Evidence]]:
    evidence: list[Evidence] = []
    contradictions: list[Evidence] = []

    same_name = person_names_equivalent(_display_name(a), _display_name(b))
    if not same_name:
        return Classification.RELATED, similarity(_display_name(a), _display_name(b)), evidence, contradictions

    evidence.append(_e("same_normalized_person_name", "strong",
                       "Same full name after controlled first/last-name inversion",
                       [_display_name(a), _display_name(b)]))

    email_a, email_b = comparison_text(a.get("email")), comparison_text(b.get("email"))
    if email_a and email_b:
        if email_a != email_b:
            contradictions.append(_e("different_email", "blocking",
                                     "Different email addresses identify different people", [email_a, email_b]))
            return Classification.BLOCKED, 0, evidence, contradictions
        evidence.append(_e("same_email", "deterministic", "Email addresses match", [email_a]))
        return Classification.EXACT, 1, evidence, contradictions

    urls_a = {normalize_url(row["url"]) for row in context.get("links", {}).get(a.get("id"), [])}
    urls_b = {normalize_url(row["url"]) for row in context.get("links", {}).get(b.get("id"), [])}
    shared_detail = _shared_urls(urls_a, urls_b, "entity_detail")
    if shared_detail:
        evidence.append(_e("same_profile_url", "deterministic", "Same profile page URL", sorted(shared_detail)))
        return Classification.EXACT, 1, evidence, contradictions

    score = .85
    aff = similarity(a.get("affiliation"), b.get("affiliation"))
    if a.get("affiliation") and b.get("affiliation"):
        if aff < .30:
            contradictions.append(_e("incompatible_affiliation", "review",
                                     "Affiliations do not clearly match",
                                     [a.get("affiliation"), b.get("affiliation")]))
            return Classification.AMBIGUOUS, .7, evidence, contradictions
        evidence.append(_e("compatible_affiliation", "supporting",
                           "Affiliations are compatible",
                           [a.get("affiliation"), b.get("affiliation")]))

    country_a, country_b = comparison_text(a.get("country")), comparison_text(b.get("country"))
    if country_a and country_b and country_a == country_b:
        evidence.append(_e("same_country", "supporting", "Same country of affiliation"))
        score = .92

    field_a, field_b = comparison_text(a.get("field")), comparison_text(b.get("field"))
    if field_a and field_b and field_a == field_b:
        evidence.append(_e("same_research_field", "supporting", "Same research field", [a.get("field")]))
        score = min(score + .05, .97)

    bio_score = similarity(a.get("bio"), b.get("bio"))
    if bio_score >= .7:
        evidence.append(_e("similar_bio", "strong", "Biographies are substantially similar"))
        score = max(score, bio_score)

    if not evidence:
        evidence.append(_e("compatible_identity", "supporting",
                           "Same name with no conflicting identity signals"))

    return Classification.STRONG, min(score, .97), evidence, contradictions


def _event_series(title: object) -> str:
    return " ".join(token for token in tokens(title)
                    if not token.isdigit()
                    and token not in {"conference", "meeting", "school", "workshop",
                                      "the", "home", "registration", "call", "open"})


def evaluate_event(a: dict, b: dict, context: dict) -> tuple[Classification, float, list[Evidence], list[Evidence]]:
    evidence: list[Evidence] = []
    contradictions: list[Evidence] = []

    years_a = years(a.get("title")) | years(a.get("start_date"))
    years_b = years(b.get("title")) | years(b.get("start_date"))
    series_same = bool(_event_series(a.get("title"))
                       and _event_series(a.get("title")) == _event_series(b.get("title")))

    if series_same and years_a and years_b and years_a != years_b:
        contradictions.append(_e("different_event_year", "blocking",
                                 "Different editions of the same event series",
                                 [sorted(years_a), sorted(years_b)]))
        return Classification.RELATED, .95, [_e("same_event_series", "strong",
                                                "Event series names match")], contradictions

    remote_a, remote_b = normalize_url(a.get("remote_url")), normalize_url(b.get("remote_url"))
    if remote_a and remote_b and remote_a == remote_b:
        evidence.append(_e("same_remote_url", "deterministic", "Same external event page URL", [remote_a]))
        return Classification.EXACT, 1, evidence, contradictions

    urls_a = {normalize_url(row["url"]) for row in context.get("links", {}).get(a.get("id"), [])}
    urls_b = {normalize_url(row["url"]) for row in context.get("links", {}).get(b.get("id"), [])}
    shared_detail = _shared_urls(urls_a, urls_b, "entity_detail")
    if shared_detail:
        evidence.append(_e("same_event_detail_url", "deterministic",
                           "Same event-specific page", sorted(shared_detail)))
        return Classification.EXACT, 1, evidence, contradictions

    title_score = similarity(a.get("title"), b.get("title"))

    if title_score >= .82:
        if not years_a or not years_b or years_a == years_b:
            evidence.append(_e("same_event_identity", "strong",
                               "Titles and edition are compatible"))
            loc_score = similarity(a.get("location"), b.get("location"))
            if loc_score >= .7:
                evidence.append(_e("same_location", "supporting", "Same event location"))
            return Classification.STRONG, title_score, evidence, contradictions

    loc_score = similarity(a.get("location"), b.get("location"))
    if loc_score >= .7 and years_a == years_b:
        desc_score = similarity(a.get("description"), b.get("description"))
        if desc_score >= .6:
            evidence.append(_e("same_location_and_description", "strong",
                               "Same location and similar description"))
            return Classification.STRONG, max(desc_score, loc_score), evidence, contradictions

    if a.get("parent_event_id") and b.get("parent_event_id") and a["parent_event_id"] == b["parent_event_id"]:
        evidence.append(_e("same_parent_event", "strong",
                           "Both events belong to the same parent event"))
        return Classification.STRONG, .85, evidence, contradictions

    if title_score >= .72:
        return Classification.AMBIGUOUS, title_score, evidence, contradictions

    return Classification.RELATED, title_score, evidence, contradictions


def _news_dates_compatible(left: object, right: object) -> bool:
    a, b = str(left or "").strip(), str(right or "").strip()
    if not a or not b:
        return True
    # Missing/partial dates from old scrapers are compatible only when their
    # known prefix agrees. Two different complete dates are not auto-merged.
    if len(a) < 10 or len(b) < 10:
        return a.startswith(b) or b.startswith(a) or (a[:4] and a[:4] == b[:4])
    return a[:10] == b[:10]


def evaluate_news(a: dict, b: dict, context: dict) -> tuple[Classification, float, list[Evidence], list[Evidence]]:
    generic = {"news", "xhr news", "update"}
    title_a, title_b = comparison_text(a.get("title")), comparison_text(b.get("title"))
    body_score = max(similarity(a.get("body"), b.get("body")),
                     similarity(a.get("summary"), b.get("summary")))

    checksums_a = {str(row.get("checksum"))
                   for row in context.get("assets", {}).get(a["id"], []) if row.get("checksum")}
    checksums_b = {str(row.get("checksum"))
                   for row in context.get("assets", {}).get(b["id"], []) if row.get("checksum")}
    shared_assets = sorted(checksums_a & checksums_b) if checksums_a & checksums_b else []

    if body_score >= .99:
        ev = [_e("equivalent_article_text", "deterministic",
                 "Article content is essentially the same body text")]
        if shared_assets:
            ev.append(_e("same_asset_checksum", "deterministic",
                         "The news records reference the same binary asset", shared_assets))
        return Classification.EXACT, body_score, ev, []

    if body_score >= .88:
        ev = [_e("equivalent_article_text", "strong",
                 "Article content matches even though headlines may differ")]
        if shared_assets:
            ev.append(_e("same_asset_checksum", "deterministic",
                         "The news records reference the same binary asset", shared_assets))
        return Classification.STRONG, body_score, ev, []

    if title_a in generic or title_b in generic:
        return Classification.BLOCKED, 0, [], [_e("insufficient_identity", "blocking",
                                                  "A generic headline cannot identify an article",
                                                  [a.get("title"), b.get("title")])]

    urls_a = {normalize_url(row["url"]) for row in context.get("links", {}).get(a.get("id"), [])}
    urls_b = {normalize_url(row["url"]) for row in context.get("links", {}).get(b.get("id"), [])}
    shared_detail = _shared_urls(urls_a, urls_b, "entity_detail")
    if shared_detail:
        ev = [_e("same_article_detail_url", "deterministic",
                 "Same article-specific URL", sorted(shared_detail))]
        if shared_assets:
            ev.append(_e("same_asset_checksum", "deterministic", "Same binary asset", shared_assets))
        return Classification.EXACT, 1, ev, []

    title_score = similarity(title_a, title_b)
    if title_a and title_a == title_b and _news_dates_compatible(a.get("date"), b.get("date")):
        # Exact headline plus a missing/partial matching date is the common
        # legacy scraper duplicate: keep the richer canonical record instead
        # of asking an administrator to resolve the pair manually.
        return Classification.STRONG, .98, [
            _e("same_headline_compatible_date", "strong",
               "Same headline with compatible complete/partial publication date",
               [a.get("date"), b.get("date")])
        ], []
    subjects_a = set(tokens(a.get("title"))) - _NEWS_STOP
    subjects_b = set(tokens(b.get("title"))) - _NEWS_STOP

    years_a = years(" ".join(str(a.get(k) or "") for k in ("title", "body", "date")))
    years_b = years(" ".join(str(b.get(k) or "") for k in ("title", "body", "date")))
    different_years = bool(years_a and years_b and years_a != years_b)

    if different_years and title_score < .92:
        ct = _e("different_news_year", "blocking", "Different years indicate different facts",
                [sorted(years_a), sorted(years_b)])
        if max(title_score, body_score) >= .75:
            return Classification.BLOCKED, max(title_score, body_score), [], [ct]
        return Classification.RELATED, max(title_score, body_score), [], []

    evidence: list[Evidence] = []
    if shared_assets:
        evidence.append(_e("same_asset_checksum", "deterministic",
                           "The news records reference the same binary asset", shared_assets))

    overlap = len(subjects_a & subjects_b) / max(1, min(len(subjects_a), len(subjects_b)))

    if body_score >= .9 and title_score >= .55 and overlap >= .5:
        evidence.extend([
            _e("equivalent_article_text", "strong", "Informative article text matches"),
            _e("same_named_subjects", "strong", "Headline subjects match"),
        ])
        return Classification.STRONG, max(body_score, title_score), evidence, []

    if title_score >= .78 and body_score >= .60:
        evidence.append(_e("compatible_headline_and_body", "strong",
                           "Headline and article text are compatible"))
        date_sim = similarity(a.get("date"), b.get("date"))
        if date_sim >= .85:
            evidence.append(_e("same_publication_date", "supporting", "Same publication date"))
        nt_a, nt_b = a.get("news_type"), b.get("news_type")
        if nt_a and nt_b and nt_a == nt_b:
            evidence.append(_e("same_news_type", "supporting", "Same news category", [nt_a]))
        return Classification.STRONG, max(title_score, body_score), evidence, []

    if overlap >= .6 and title_score >= .55:
        if different_years:
            return Classification.RELATED, max(title_score, body_score), evidence, []
        evidence.append(_e("same_news_subjects", "strong",
                           "Headlines share named subjects even though wording differs"))
        return Classification.AMBIGUOUS, max(title_score, overlap, body_score), evidence, []

    src_a, src_b = a.get("source_kind"), b.get("source_kind")
    if src_a and src_b and src_a == src_b and src_a not in ("manual",):
        date_sim = similarity(a.get("date"), b.get("date"))
        if date_sim >= .85:
            evidence.append(_e("same_source_same_date", "supporting",
                               "Same source and publication date"))
            return Classification.AMBIGUOUS, max(.7, title_score, body_score), evidence, []

    return (Classification.AMBIGUOUS if max(title_score, body_score) >= .75
            else Classification.RELATED), max(title_score, body_score), evidence, []


def evaluate_publication(a: dict, b: dict, context: dict) -> tuple[Classification, float, list[Evidence], list[Evidence]]:
    if aggregate_markers(a.get("abstract")) or aggregate_markers(b.get("abstract")):
        return Classification.BLOCKED, 0, [], [_e("aggregated_source_record", "blocking",
                                                  "Container text cannot establish publication identity")]

    doi_a, doi_b = normalized_doi(a.get("doi")), normalized_doi(b.get("doi"))
    if doi_a and doi_b and doi_a != doi_b:
        return Classification.BLOCKED, 0, [], [_e("different_doi", "blocking",
                                                  "Different DOI values identify different publications",
                                                  [doi_a, doi_b])]
    if doi_a and doi_a == doi_b:
        return Classification.EXACT, 1, [_e("same_doi", "deterministic",
                                            "Normalized DOI values match", [doi_a])], []

    title_a = comparison_text(a.get("title"))
    title_b = comparison_text(b.get("title"))
    slug_a = str(a.get("slug") or "").strip().casefold()
    slug_b = str(b.get("slug") or "").strip().casefold()

    def forced_slug_base(value: str) -> str:
        match = re.fullmatch(r"(.+)-([2-9]\d*)", value)
        return match.group(1) if match else value

    force_copy_slugs = (
        slug_a
        and slug_b
        and slug_a != slug_b
        and forced_slug_base(slug_a) == forced_slug_base(slug_b)
        and (forced_slug_base(slug_a) != slug_a or forced_slug_base(slug_b) != slug_b)
    )
    clone_fields = ("year", "authors", "journal", "doi", "abstract", "date_text", "date_precision")
    same_payload = all(
        comparison_text(a.get(field)) == comparison_text(b.get(field))
        for field in clone_fields
    )
    if title_a and title_a == title_b and force_copy_slugs and same_payload:
        return Classification.EXACT, 1, [_e(
            "forced_reimport_clone",
            "deterministic",
            "Same publication payload with a Force reimport slug suffix",
            [slug_a, slug_b],
        )], []

    docs_a = {normalize_url(row["url"]) for row in context.get("links", {}).get(a["id"], [])
              if classify_url(row["url"]) == "document"}
    docs_b = {normalize_url(row["url"]) for row in context.get("links", {}).get(b["id"], [])
              if classify_url(row["url"]) == "document"}
    if docs_a & docs_b:
        return Classification.EXACT, 1, [_e("same_document_url", "deterministic",
                                            "Direct document URLs match", sorted(docs_a & docs_b))], []

    title_score = similarity(a.get("title"), b.get("title"))
    author_score = similarity(a.get("authors"), b.get("authors"))
    journal_score = similarity(a.get("journal"), b.get("journal"))
    abstract_score = similarity(a.get("abstract"), b.get("abstract"))

    year_a, year_b = a.get("year"), b.get("year")
    year_compatible = (not year_a or not year_b
                       or abs(int(year_a) - int(year_b)) <= 1)

    evidence: list[Evidence] = []

    if title_score >= .88 and author_score >= .65 and year_compatible:
        evidence.append(_e("same_title_authors", "strong", "Title and authors match"))
        if journal_score >= .6:
            evidence.append(_e("same_journal", "supporting", "Same journal", [a.get("journal")]))
            return Classification.STRONG, min(.98, (title_score + author_score + journal_score) / 3), evidence, []
        return Classification.STRONG, (title_score + author_score) / 2, evidence, []

    if abstract_score >= .85 and title_score >= .7:
        evidence.append(_e("similar_abstract", "strong", "Abstracts are substantially similar"))
        if author_score >= .5:
            evidence.append(_e("compatible_authors", "supporting", "Authors are compatible"))
            return Classification.STRONG, (abstract_score + title_score) / 2, evidence, []
        return Classification.AMBIGUOUS, abstract_score, evidence, []

    if title_score >= .82 and author_score >= .65:
        return Classification.AMBIGUOUS, (title_score + author_score) / 2, evidence, []

    if journal_score >= .85 and title_score >= .6 and year_compatible:
        evidence.append(_e("same_journal_similar_title", "supporting",
                           "Same journal with compatible title"))
        return Classification.AMBIGUOUS, (journal_score + title_score) / 2, evidence, []

    return (Classification.AMBIGUOUS if title_score >= .78
            else Classification.RELATED), title_score, evidence, []


def evaluate_sponsor(a: dict, b: dict, context: dict) -> tuple[Classification, float, list[Evidence], list[Evidence]]:
    evidence: list[Evidence] = []

    docs_a = {normalize_url(row["url"]) for row in context.get("links", {}).get(a["id"], [])}
    docs_b = {normalize_url(row["url"]) for row in context.get("links", {}).get(b["id"], [])}
    if docs_a & docs_b:
        evidence.append(_e("same_sponsor_url", "deterministic",
                           "Same linked website or page", sorted(docs_a & docs_b)))
        return Classification.EXACT, 1, evidence, []

    name_score = similarity(a.get("name"), b.get("name"))
    if name_score >= .88:
        desc_score = similarity(a.get("description"), b.get("description"))
        if desc_score >= .7:
            evidence.append(_e("similar_description", "supporting", "Descriptions match"))
            ta, tb = a.get("tier"), b.get("tier")
            if ta and tb and ta == tb:
                evidence.append(_e("same_sponsor_tier", "supporting", "Same sponsorship tier", [ta]))
            return Classification.STRONG, min(.98, (name_score + desc_score) / 2), evidence, []
        return Classification.STRONG, name_score, evidence, []

    if name_score >= .75:
        return Classification.AMBIGUOUS, name_score, evidence, []

    return Classification.RELATED, name_score, evidence, []


def evaluate_structured_content(a: dict, b: dict, context: dict) -> tuple[Classification, float, list[Evidence], list[Evidence]]:
    """Conservative policy for pages and research areas.

    Deterministic Force reimport clones are handled by the analyzer before
    this policy. Ordinary similar editorial pages must remain separate unless
    an administrator reviews them.
    """
    title_score = similarity(a.get("title"), b.get("title"))
    content_a = a.get("body") or a.get("description") or a.get("summary")
    content_b = b.get("body") or b.get("description") or b.get("summary")
    content_score = similarity(content_a, content_b)
    if title_score >= .9 and content_a and content_b and content_score >= .9:
        return Classification.STRONG, min(.98, (title_score + content_score) / 2), [
            _e("same_editorial_content", "strong", "Title and editorial content match")
        ], []
    if title_score >= .8:
        return Classification.AMBIGUOUS, title_score, [], []
    return Classification.RELATED, title_score, [], []


POLICIES = {
    "member": evaluate_member,
    "event": evaluate_event,
    "news": evaluate_news,
    "publication": evaluate_publication,
    "research_area": evaluate_structured_content,
    "page": evaluate_structured_content,
    "sponsor": evaluate_sponsor,
}

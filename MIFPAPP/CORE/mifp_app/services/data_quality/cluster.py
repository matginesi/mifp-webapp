from __future__ import annotations

from itertools import combinations
from typing import Any

from .normalizers import tokens, years


def _event_series_key(title: str) -> str:
    year_tokens = years(title)
    skip = {"conference", "meeting", "school", "workshop", "symposium", "congress",
            "the", "and", "of", "on", "in", "for"} | {str(y) for y in year_tokens}
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

    start_dates: list[Any] = [r.get("start_date") for r in records if r.get("start_date")]
    if len(start_dates) >= 2:
        unique_dates = set(start_dates)
        if len(unique_dates) > 1 and entity_type != "news":
            reasons.append(f"Multiple different start dates: {sorted(unique_dates)}")

    if reasons:
        return False, reasons, [records]
    return True, [], [records]

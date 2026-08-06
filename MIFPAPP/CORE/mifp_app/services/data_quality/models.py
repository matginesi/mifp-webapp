from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ActionType(StrEnum):
    CLEAN = "clean_record"
    ENRICH = "enrich_record"
    MERGE = "merge_records"
    SPLIT = "split_aggregated_record"
    REPAIR = "repair_relations_or_assets"


class Classification(StrEnum):
    EXACT = "exact_duplicate"
    STRONG = "strong_candidate"
    AMBIGUOUS = "ambiguous"
    RELATED = "related_not_duplicate"
    BLOCKED = "blocked"
    INVALID = "invalid_record"
    CLEANING = "needs_cleaning"
    AGGREGATED = "aggregated_record"
    KEEP_SEPARATE = "keep_separate"
    JUNK = "junk_technical_record"
    FRAGMENT = "page_fragment_attached"


@dataclass(frozen=True)
class Evidence:
    code: str
    strength: str
    explanation: str
    values: list[Any] = field(default_factory=list)


@dataclass
class Finding:
    action_type: ActionType
    entity_type: str
    record_ids: list[int]
    classification: Classification
    evidence: list[Evidence]
    contradictions: list[Evidence]
    plan: dict[str, Any]
    fingerprint: str
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def require_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("action payload must be an object")
    if len(value) > 200:
        raise ValueError("action payload is too large")
    return value

"""Version 1 reusable poll templates. Never deserialize runtime poll state."""
from __future__ import annotations

from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from . import simple_polls as store

MAX_YAML_BYTES = 1024 * 1024
MAX_YAML_EVENTS = 6000
MAX_YAML_DEPTH = 8
PRIVACY_LIMITS = {
    "controller": 200, "contact": 200, "purpose": 1000, "legal_basis": 80,
    "recipients": 500, "transfers": 500, "legitimate_interest": 500, "dpo": 200,
}
QUESTION_FIELDS = {
    "text": {"style"}, "yes_no": {"yes_label", "no_label"},
    "single_choice": {"options"}, "multiple_choice": {"options"},
    "date": set(), "date_availability": {"dates"},
}


def _fields(value: Any, allowed: set[str], context: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise store.SurveyError(f"{context} must be a mapping with text field names")
    if set(value) - allowed:
        raise store.SurveyError(f"Unsupported fields in {context}")
    return value


def _text(value: Any, maximum: int, context: str) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise store.SurveyError(f"Invalid or overlong {context}")
    return value.strip()


def _question_template(question: dict) -> dict:
    fields = {"type", "question", "required"} | QUESTION_FIELDS[question["type"]]
    result = {key: value for key, value in question.items() if key in fields}
    if "options" in result:
        result["options"] = [item["label"] for item in question["options"]]
    return result


def validate_template(raw: Any) -> dict:
    raw = _fields(raw, {"schema_version", "poll"}, "template")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise store.SurveyError("Unsupported YAML schema_version; expected 1")
    raw = _fields(raw.get("poll"), {"title", "description", "response_mode", "questions", "privacy"}, "poll")
    title = _text(raw.get("title"), 180, "poll title")
    if not title:
        raise store.SurveyError("Poll title is required")
    description = _text(raw.get("description", ""), 4000, "description")
    mode = raw.get("response_mode", "anonymous")
    if not isinstance(mode, str) or mode not in store.MODES:
        raise store.SurveyError("Invalid response mode")
    questions = raw.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 30:
        raise store.SurveyError("A template needs between 1 and 30 questions")
    for q in questions:
        q = _fields(q, {"type", "question", "required", "style", "yes_label", "no_label", "options", "dates"}, "question")
        kind = q.get("type")
        if not isinstance(kind, str) or kind not in QUESTION_FIELDS:
            raise store.SurveyError("Invalid question type")
        _fields(q, {"type", "question", "required"} | QUESTION_FIELDS[kind], "question")
        if type(q.get("required", False)) is not bool:
            raise store.SurveyError("Question required must be true or false")
        _text(q.get("question"), 300, "question text")
        for field in ("style", "yes_label", "no_label"):
            if field in q:
                _text(q[field], 80, field)
        if "options" in q and (not isinstance(q["options"], list) or any(not isinstance(x, str) for x in q["options"])):
            raise store.SurveyError("Choices must be a list of text labels")
    normalized = store._normalize_questions(questions)
    value = {"title": title, "description": description, "response_mode": mode,
             "questions": [_question_template(q) for q in normalized]}
    if "privacy" in raw:
        privacy = _fields(raw["privacy"], set(PRIVACY_LIMITS) | {"confirmed"}, "privacy")
        if "confirmed" in privacy and type(privacy["confirmed"]) is not bool:
            raise store.SurveyError("Privacy confirmed must be true or false")
        privacy = {key: _text(privacy.get(key, ""), maximum, key) for key, maximum in PRIVACY_LIMITS.items()}
        if privacy["legal_basis"] and privacy["legal_basis"] not in store.BASES:
            raise store.SurveyError("Invalid privacy legal basis")
        value["privacy"] = {**privacy, "confirmed": False}
    return {"schema_version": 1, "poll": value}


def _check_mapping_keys(node) -> None:
    if isinstance(node, MappingNode):
        seen = set()
        for key, value in node.value:
            if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in seen:
                raise store.SurveyError("YAML field names must be unique text keys")
            seen.add(key.value)
            _check_mapping_keys(value)
    elif isinstance(node, SequenceNode):
        for item in node.value:
            _check_mapping_keys(item)
    elif isinstance(node, ScalarNode):
        node.value.encode("utf-8")  # reject escaped unpaired surrogates before rendering


def load_template(payload: bytes) -> dict:
    if not payload or len(payload) > MAX_YAML_BYTES:
        raise store.SurveyError("Upload a non-empty YAML file no larger than 1 MiB")
    try:
        text = payload.decode("utf-8-sig")
        depth = 0
        for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader), 1):
            if count > MAX_YAML_EVENTS:
                raise store.SurveyError("YAML template is too complex")
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) or getattr(event, "tag", None):
                raise store.SurveyError("YAML anchors, aliases and explicit tags are not supported")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise store.SurveyError("YAML nesting is too deep")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        # Compose after the bounded event pass to reject duplicate keys before
        # safe_load would collapse them. Aliases/merge keys cannot bypass this.
        _check_mapping_keys(yaml.compose(text, Loader=yaml.SafeLoader))
        return validate_template(yaml.safe_load(text))
    except store.SurveyError:
        raise
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError) as exc:
        raise store.SurveyError("Invalid YAML template; upload one UTF-8 YAML document") from exc


def export_template(config_dir, poll_id: str) -> bytes:
    poll = store.get(config_dir, poll_id)
    template = {"schema_version": 1, "poll": {"title": poll["title"], "description": poll["description"],
                "response_mode": poll["response_mode"],
                "questions": [_question_template(q) for q in poll["questions"]]}}
    return yaml.safe_dump(template, allow_unicode=True, sort_keys=False).encode("utf-8")


def create_draft(config_dir, template: dict) -> dict:
    # Validate again at the confirmation boundary; never accept runtime IDs,
    # bearer keys, status, responses or identity fields from a template.
    value = validate_template(template)["poll"]
    poll = store.create(config_dir)
    return store.update(config_dir, poll["id"], {**value, "status": "draft",
                       "privacy": {**poll["privacy"], **value.get("privacy", {}), "confirmed": False}})

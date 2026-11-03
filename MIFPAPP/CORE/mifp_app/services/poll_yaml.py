"""Version 1 reusable poll templates. Never deserialize runtime poll state."""
from __future__ import annotations

from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from . import simple_polls as store

MAX_YAML_BYTES = 1024 * 1024
MAX_YAML_EVENTS = 6000
MAX_YAML_DEPTH = 8
PRIVACY_LIMITS = store.PRIVACY_LIMITS
QUESTION_FIELDS = {
    "text": {"style"}, "yes_no": {"yes_label", "no_label"},
    "single_choice": {"options"}, "multiple_choice": {"options"},
    "date": set(), "date_availability": {"dates"},
}


def _fields(value: Any, allowed: set[str], context: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise store.SurveyError(f"{context} must be a mapping with text field names")
    if set(value) - allowed:
        names = ", ".join(f"{context}.{name}" for name in sorted(set(value) - allowed))
        raise store.SurveyError(f"Unsupported fields in {context}: {names}")
    return value


def _text(value: Any, maximum: int, context: str) -> str:
    if not isinstance(value, str):
        raise store.SurveyError(f"{context}: expected text")
    if len(value) > maximum:
        raise store.SurveyError(f"{context}: maximum {maximum} characters")
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
        raise store.SurveyError("Unsupported schema_version: expected integer 1")
    raw = _fields(raw.get("poll"), {"title", "description", "response_mode", "questions", "privacy", "privacy_source", "aggregate_only", *store.SCHEDULE_FIELDS}, "poll")
    title = _text(raw.get("title"), 180, "poll.title")
    if not title:
        raise store.SurveyError("poll.title: non-empty text is required")
    description = _text(raw.get("description", ""), 4000, "poll.description")
    mode = raw.get("response_mode", "anonymous")
    if not isinstance(mode, str) or mode not in store.MODES:
        raise store.SurveyError("poll.response_mode: expected anonymous or identified")
    aggregate_only = raw.get("aggregate_only", False)
    if type(aggregate_only) is not bool:
        raise store.SurveyError("poll.aggregate_only: expected true or false")
    if aggregate_only and mode != "anonymous":
        raise store.SurveyError("poll.aggregate_only: requires anonymous response mode")
    questions = raw.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 30:
        raise store.SurveyError("poll.questions: expected a list of 1–30 questions")
    normalized = []
    for index, q in enumerate(questions):
        path = f"poll.questions[{index}]"
        q = _fields(q, {"type", "question", "required", "style", "yes_label", "no_label", "options", "dates"}, path)
        kind = q.get("type")
        if not isinstance(kind, str) or kind not in QUESTION_FIELDS:
            raise store.SurveyError(f"{path}.type: supported types are {', '.join(sorted(QUESTION_FIELDS))}")
        _fields(q, {"type", "question", "required"} | QUESTION_FIELDS[kind], path)
        if type(q.get("required", False)) is not bool:
            raise store.SurveyError(f"{path}.required: expected true or false")
        if not _text(q.get("question"), 300, f"{path}.question"):
            raise store.SurveyError(f"{path}.question: non-empty text is required")
        for field in ("style", "yes_label", "no_label"):
            if field in q:
                _text(q[field], 80, f"{path}.{field}")
        if "options" in q and (not isinstance(q["options"], list) or any(not isinstance(x, str) for x in q["options"])):
            raise store.SurveyError(f"{path}.options: expected a list of text labels")
        setting = {"text": "style", "yes_no": "yes_label/no_label", "single_choice": "options",
                   "multiple_choice": "options", "date_availability": "dates", "date": "question"}[kind]
        try:
            normalized.extend(store._normalize_questions([q]))
        except store.SurveyError as exc:
            raise store.SurveyError(f"{path}.{setting}: {exc}") from exc
    if aggregate_only and not store._aggregate_compatible(normalized):
        raise store.SurveyError("poll.aggregate_only: free-text questions cannot be stored as aggregate counts")
    value = {"title": title, "description": description, "response_mode": mode,
             "aggregate_only": aggregate_only, "questions": [_question_template(q) for q in normalized]}
    for field in store.SCHEDULE_FIELDS:
        day = raw.get(field, "")
        if day is None or day == "":
            value[field] = ""
        else:
            try:
                value[field] = store._calendar_dates([day])[0]
            except store.SurveyError as exc:
                raise store.SurveyError(f"poll.{field}: {exc}") from exc
    privacy = _fields(raw.get("privacy", {}), set(PRIVACY_LIMITS) | {"confirmed"}, "poll.privacy")
    if "confirmed" in privacy and type(privacy["confirmed"]) is not bool:
        raise store.SurveyError("poll.privacy.confirmed: expected true or false")
    privacy = {key: _text(privacy.get(key, ""), maximum, f"poll.privacy.{key}") for key, maximum in PRIVACY_LIMITS.items()}
    if privacy["legal_basis"] and privacy["legal_basis"] not in store.BASES:
        raise store.SurveyError("poll.privacy.legal_basis: expected consent, legitimate_interest, public_task or contract (or blank in Draft)")
    # Historic source metadata has no configuration effect. There is only the
    # poll's canonical object, never a lookup or profile-selection workflow.
    old_source = raw.get("privacy_source")
    if old_source is not None and old_source not in ("organization", "custom"):
        raise store.SurveyError("poll.privacy_source: unsupported historic value; omit this field")
    value["privacy"] = store.normalize_privacy(privacy)
    return {"schema_version": 1, "poll": value}


def _check_mapping_keys(node, path: str = "") -> None:
    if isinstance(node, MappingNode):
        seen = set()
        for key, value in node.value:
            location = f"line {key.start_mark.line + 1}, column {key.start_mark.column + 1}"
            if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                raise store.SurveyError(f"{path or 'template'}: YAML field names must be text ({location})")
            field = f"{path}.{key.value}" if path else key.value
            if key.value in seen:
                raise store.SurveyError(f"{field}: duplicate YAML field ({location})")
            seen.add(key.value)
            _check_mapping_keys(value, field)
    elif isinstance(node, SequenceNode):
        for index, item in enumerate(node.value):
            _check_mapping_keys(item, f"{path}[{index}]")
    elif isinstance(node, ScalarNode):
        try:
            node.value.encode("utf-8")  # reject escaped unpaired surrogates
            if node.tag == "tag:yaml.org,2002:timestamp" and len(node.value) == 10:
                store._calendar_dates([node.value])
        except (UnicodeError, store.SurveyError) as exc:
            raise store.SurveyError(f"{path}: invalid Unicode or calendar date at line {node.start_mark.line + 1}") from exc


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
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise store.SurveyError(f"Invalid YAML{location}: {exc.problem or 'malformed document'}") from exc
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError) as exc:
        raise store.SurveyError("Invalid YAML template; upload one UTF-8 YAML document") from exc


def export_template(config_dir, poll_id: str) -> bytes:
    poll = store.get(config_dir, poll_id)
    template = {"schema_version": 1, "poll": {"title": poll["title"], "description": poll["description"],
                "response_mode": poll["response_mode"], "aggregate_only": poll.get("aggregate_only", False),
                **{field: poll.get(field, "") for field in store.SCHEDULE_FIELDS},
                "privacy": {field: poll["privacy"].get(field, "") for field in PRIVACY_LIMITS},
                "questions": [_question_template(q) for q in poll["questions"]]}}
    return yaml.safe_dump(template, allow_unicode=True, sort_keys=False).encode("utf-8")


def create_draft(config_dir, template: dict) -> dict:
    # Validate again at the confirmation boundary; never accept runtime IDs,
    # bearer keys, status, responses or identity fields from a template.
    value = validate_template(template)["poll"]
    try:
        return store.create(config_dir, definition=value)
    except OSError as exc:
        raise store.SurveyError("Unable to save the new draft. No poll was imported; check private storage permissions and free space") from exc

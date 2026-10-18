"""Current questionnaire loading, branching, sanitization, and validation."""

from __future__ import annotations

import json
import math
import re
import uuid
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from retirement_readiness.core.constants import SURVEY_ID
from retirement_readiness.core.numeric_answers import UNKNOWN, numeric_answer

_DATA_PATH = Path(__file__).with_name("data") / "questionnaire.json"
_DIVISION_DATA_PATH = Path(__file__).with_name("data") / "administrative-divisions.json"
_INLINE_FIELDS_PATH = Path(__file__).with_name("data") / "inline-fields.json"
_BLANK_PATTERN = re.compile(r"（填空）|_{3,}")
_MULTIPLE_CHOICE_CONSTRAINTS = {
    "exclusiveOptionCodes": [
        "never_happened",
        "none",
        "not_applicable",
        "not_sure",
    ],
    "mutuallyExclusiveGroups": [],
}
_EXCLUSIVE_OPTIONS = frozenset(_MULTIPLE_CHOICE_CONSTRAINTS["exclusiveOptionCodes"])
_MUTUALLY_EXCLUSIVE_GROUPS = tuple(
    (
        frozenset(group["optionCodes"]),
        str(group["validationError"]),
    )
    for group in _MULTIPLE_CHOICE_CONSTRAINTS["mutuallyExclusiveGroups"]
)

_INLINE_CONSTRAINTS: dict[str, tuple[str, float | None, float | None, bool]] = {}


def _set_inline(
    owner_id: str, constraints: list[tuple[str, float | None, float | None, bool]]
) -> None:
    for index, constraint in enumerate(constraints, start=1):
        _INLINE_CONSTRAINTS[f"{owner_id}__blank_{index}"] = constraint


_NUMBER = ("number", 0, None, False)
_INTEGER = ("integer", 0, None, False)
_set_inline("Q5", [("integer", 0, 20, False), ("number_list", 0, 120, False)])
_set_inline("Q6__parents_alive_text", [("integer", 0, 120, False)] * 2)
_set_inline("Q6__father_alive_text", [("integer", 0, 120, False)])
_set_inline("Q6__mother_alive_text", [("integer", 0, 120, False)])
for _owner in ("Q11_2", "P9_4_2"):
    _set_inline(_owner, [_NUMBER])
for _owner in (
    "Q13__clear_text",
    "Q13__very_clear_text",
    "Q14__rough_text",
    "Q14__detailed_text",
):
    _set_inline(_owner, [_NUMBER])
_set_inline(
    "Q27",
    [("integer" if index in {6, 8} else "number", 0, None, True) for index in range(10)],
)
_set_inline("Q28", [("number", 0, None, True)] * 6)
_set_inline("Q29_1", [_NUMBER] * 3)
_set_inline("Q30", [("number", 0, None, True)] * 10)


@lru_cache(maxsize=1)
def questionnaire() -> dict[str, Any]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def administrative_divisions() -> dict[str, Any]:
    return json.loads(_DIVISION_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def inline_field_definitions() -> dict[str, list[dict[str, str]]]:
    return json.loads(_INLINE_FIELDS_PATH.read_text(encoding="utf-8"))


def questionnaire_payload() -> dict[str, Any]:
    payload = questionnaire()
    payload_questions = deepcopy(payload.get("questions") or [])
    _attach_inline_fields(payload_questions)
    return {
        "survey_id": SURVEY_ID,
        "sections": deepcopy(payload.get("sections") or []),
        "questions": payload_questions,
        "resources": {
            "administrative_divisions": deepcopy(administrative_divisions()),
            "multiple_choice_constraints": deepcopy(_MULTIPLE_CHOICE_CONSTRAINTS),
        },
    }


def _attach_inline_fields(payload_questions: list[dict[str, Any]]) -> None:
    definitions = inline_field_definitions()
    for question in payload_questions:
        question_id = str(question["id"])
        if question_id in definitions:
            question["inlineFields"] = _inline_fields(question_id, definitions[question_id])
        for option in question.get("options") or []:
            owner_id = f"{question_id}__{option['code']}_text"
            if owner_id in definitions:
                option["inlineFields"] = _inline_fields(owner_id, definitions[owner_id])


def _inline_fields(owner_id: str, definitions: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"key": f"{owner_id}__blank_{index}", **definition}
        for index, definition in enumerate(definitions, start=1)
    ]


def questions() -> list[dict[str, Any]]:
    return list(questionnaire().get("questions") or [])


def get_question(question_id: str) -> dict[str, Any] | None:
    return _question_index().get(question_id)


@lru_cache(maxsize=1)
def _question_index() -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in questions()}


@lru_cache(maxsize=1)
def allowed_answer_keys() -> frozenset[str]:
    keys: set[str] = set()
    for question in questions():
        question_id = str(question["id"])
        keys.add(question_id)
        keys.update(_blank_keys(question_id, str(question.get("text") or "")))
        for option in question.get("options") or []:
            owner_id = f"{question_id}__{option['code']}_text"
            option_keys = _blank_keys(owner_id, str(option.get("label") or ""))
            if option_keys:
                keys.update(option_keys)
            elif option.get("requiresText"):
                keys.add(owner_id)
    return frozenset(keys)


def validate_answer_changes(changes: dict[str, Any]) -> None:
    unknown = sorted(set(changes).difference(allowed_answer_keys()))
    if unknown:
        raise ValueError(f"未知问卷字段：{', '.join(unknown[:5])}")
    encoded = json.dumps(changes, ensure_ascii=False, default=str)
    if len(encoded) > 250_000:
        raise ValueError("单次问卷保存内容过大")


def has_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(has_value(item) for item in value)
    if isinstance(value, dict):
        return any(has_value(item) for item in value.values())
    return True


def _condition_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values: list[Any] = []
        for item in value.values():
            values.extend(item if isinstance(item, list) else [item])
        return values
    return value if isinstance(value, list) else [value]


def condition_matches(condition: dict[str, Any] | None, answers: dict[str, Any]) -> bool:
    if not condition:
        return True
    value = answers.get(str(condition.get("questionId") or ""))
    if condition.get("answered") and not has_value(value):
        return False
    values = [str(item) for item in _condition_values(value)]
    includes = [str(item) for item in condition.get("containsAny") or []]
    excludes = [str(item) for item in condition.get("notContainsAny") or []]
    if includes and not any(item in includes for item in values):
        return False
    if excludes and any(item in excludes for item in values):
        return False
    return True


def should_show(question: dict[str, Any], answers: dict[str, Any]) -> bool:
    if not condition_matches(question.get("showIf"), answers):
        return False
    if question.get("showIfAll") and not all(
        condition_matches(item, answers) for item in question["showIfAll"]
    ):
        return False
    if question.get("showIfAny") and not any(
        condition_matches(item, answers) for item in question["showIfAny"]
    ):
        return False
    if question.get("showIfAnyAll") and not any(
        all(condition_matches(item, answers) for item in group)
        for group in question["showIfAnyAll"]
    ):
        return False
    return True


def active_questions(answers: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in questions() if should_show(item, answers)]


def _blank_keys(owner_id: str, text: str | None) -> list[str]:
    source = text or ""
    if "（填空）" not in source and "__" in owner_id:
        return []
    count = max(0, len(_BLANK_PATTERN.split(source)) - 1)
    return [f"{owner_id}__blank_{index}" for index in range(1, count + 1)]


def _selected(question: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    value = answers.get(str(question.get("id")))
    if question.get("type") == "multiple":
        return [str(item) for item in value] if isinstance(value, list) else []
    return [str(value)] if has_value(value) else []


def _required_supplemental(question: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    selected = set(_selected(question, answers))
    required: list[str] = []
    for option in question.get("options") or []:
        if str(option.get("code")) not in selected:
            continue
        owner_id = f"{question['id']}__{option['code']}_text"
        blank_keys = _blank_keys(owner_id, str(option.get("label") or ""))
        if blank_keys:
            required.extend(blank_keys)
        elif option.get("requiresText"):
            required.append(owner_id)
    return required


def _numeric_issue(key: str, value: Any, answers: dict[str, Any]) -> str:
    constraint = _INLINE_CONSTRAINTS.get(key)
    if not constraint:
        return ""
    kind, minimum, maximum, allow_slash = constraint
    if key.split("__")[0] in {"Q27", "Q28", "Q29_1", "Q30"}:
        status, lower, upper = numeric_answer(value)
        if status == "unknown":
            return ""
        if status in {"known", "range"}:
            bounds = [item for item in (lower, upper) if item is not None]
            if any(item < 0 for item in bounds):
                return "min_0"
            return (
                "integer_required"
                if kind == "integer" and any(not item.is_integer() for item in bounds)
                else ""
            )
        return "numeric_value_required"
    text = str(value if value is not None else "").strip()
    if key == "Q5__blank_2":
        child_count = _number(answers.get("Q5__blank_1"))
        if child_count == 0 and text in {"0", "无", "没有", "/"}:
            return ""
    if allow_slash and text == "/":
        return ""
    parts = re.split(r"[、,，;；\s]+", text) if kind == "number_list" else [text]
    values = [_number(item) for item in parts if item != ""]
    if not values or any(item is None for item in values):
        return "numeric_value_required"
    numeric = [float(item) for item in values if item is not None]
    if kind == "integer" and any(not item.is_integer() for item in numeric):
        return "integer_required"
    if minimum is not None and any(item < minimum for item in numeric):
        return f"min_{minimum:g}"
    if maximum is not None and any(item > maximum for item in numeric):
        return f"max_{maximum:g}"
    if key == "Q5__blank_2":
        child_count = _number(answers.get("Q5__blank_1"))
        if child_count is not None and child_count > 0 and len(numeric) != int(child_count):
            return "child_age_count_mismatch"
    return ""


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _question_issues(
    question: dict[str, Any], answers: dict[str, Any]
) -> tuple[list[str], list[str]]:
    question_id = str(question.get("id") or "")
    question_type = str(question.get("type") or "")
    value = answers.get(question_id)
    missing: list[str] = []
    invalid: list[str] = []

    if question_type in {"single", "multiple"}:
        selected = _selected(question, answers)
        if not selected:
            missing.append(question_id)
        allowed = {str(item.get("code")) for item in question.get("options") or []}
        invalid.extend(f"{question_id}:{code}" for code in selected if code not in allowed)
        if question_type == "multiple" and len(selected) > 1:
            if any(code in _EXCLUSIVE_OPTIONS for code in selected):
                invalid.append(f"{question_id}:exclusive_option_combined")
            for group, error_code in _MUTUALLY_EXCLUSIVE_GROUPS:
                if len(group.intersection(selected)) > 1:
                    invalid.append(f"{question_id}:{error_code}")
        for key in _required_supplemental(question, answers):
            if not has_value(answers.get(key)):
                missing.append(key)
            else:
                issue = _numeric_issue(key, answers.get(key), answers)
                if issue:
                    invalid.append(f"{key}:{issue}")
        return missing, invalid

    if question_type == "number":
        if not has_value(value):
            missing.append(question_id)
        else:
            numeric = _number(value)
            if numeric is None:
                invalid.append(f"{question_id}:finite_number_required")
            else:
                issue = numeric_constraint_issue(value, question.get("numericConstraint") or {})
                if issue:
                    invalid.append(f"{question_id}:{issue}")
        return missing, invalid

    if question_type in {"textarea", "text"}:
        keys = _blank_keys(question_id, str(question.get("text") or ""))
        if keys:
            for key in keys:
                if key == "Q5__blank_2" and _number(answers.get("Q5__blank_1")) == 0:
                    continue
                if not has_value(answers.get(key)):
                    if not question.get("blankAsZero"):
                        missing.append(key)
                else:
                    issue = _numeric_issue(key, answers.get(key), answers)
                    if issue:
                        invalid.append(f"{key}:{issue}")
        elif not has_value(value):
            missing.append(question_id)
        return missing, invalid

    if question_type in {"composite", "dynamicTable"}:
        rows = value if isinstance(value, list) else []
        if question_type == "dynamicTable":
            count = _number(answers.get(question.get("repeatCountFrom", "")))
            if not isinstance(rows, list) or len(rows) != count:
                invalid.append(f"{question_id}:segment_count_mismatch")
                return missing, invalid
            ids = [row.get("segment_id") for row in rows if isinstance(row, dict)]
            if len(ids) != len(rows) or any(not item for item in ids) or len(set(ids)) != len(ids):
                invalid.append(f"{question_id}:segment_id_missing_or_duplicate")
        else:
            rows = [value if isinstance(value, dict) else {}]
        for index, record in enumerate(rows):
            if not isinstance(record, dict):
                invalid.append(f"{question_id}.rows.{index}:invalid_record")
                continue
            prefix = (
                f"{question_id}.rows.{index}" if question_type == "dynamicTable" else question_id
            )
            for field in question.get("fields") or []:
                if not should_show(field, {**answers, **record}):
                    continue
                key = field["key"]
                if not has_value(record.get(key)):
                    missing.append(f"{prefix}.{key}")
                elif field.get("kind") == "select":
                    if str(record[key]) not in {
                        str(option["code"]) for option in _field_options(question, field, record)
                    }:
                        invalid.append(f"{prefix}.{key}:invalid_option")
                elif field.get("kind") == "number":
                    issue = numeric_constraint_issue(record[key], field)
                    if issue:
                        invalid.append(f"{prefix}.{key}:{issue}")
        return missing, invalid

    if not has_value(value):
        missing.append(question_id)
    return missing, invalid


def _owner_for_key(key: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in questions()
            if key == item.get("id") or key.startswith(f"{item.get('id')}__")
        ),
        None,
    )


def numeric_constraint_issue(value: Any, constraint: dict[str, Any]) -> str:
    if constraint.get("allowUnknown") and str(value).strip() in UNKNOWN:
        return ""
    numeric = _number(value)
    if numeric is None:
        return "finite_number_required"
    if constraint.get("integer") and not numeric.is_integer():
        return "integer_required"
    for key, comparison in (
        ("min", numeric < constraint.get("min", -math.inf)),
        ("max", numeric > constraint.get("max", math.inf)),
    ):
        if comparison:
            return f"{key}_{constraint[key]}"
    return ""


def _field_options(
    question: dict[str, Any], field: dict[str, Any], record: dict[str, Any]
) -> list[dict[str, Any]]:
    key = field["key"]
    divisions = administrative_divisions()
    if key.endswith("province_code"):
        return divisions["provinces"]
    if key.endswith("city_code"):
        return divisions["citiesByProvince"].get(
            str(record.get(key.replace("city_code", "province_code"), "")), []
        )
    return field.get("options") or []


def _sanitize_record(
    question: dict[str, Any], record: dict[str, Any], answers: dict[str, Any]
) -> dict[str, Any]:
    fields = question.get("fields") or []
    result = {
        key: value
        for key, value in record.items()
        if key == "segment_id" or any(field["key"] == key for field in fields)
    }
    for field in fields:
        key = field["key"]
        if not should_show(field, {**answers, **result}):
            result.pop(key, None)
        elif key.endswith(("province_code", "city_code")) and has_value(result.get(key)):
            if str(result[key]) not in {
                str(item["code"]) for item in _field_options(question, field, result)
            }:
                result.pop(key, None)
    return result


def sanitize_answers(raw_answers: dict[str, Any]) -> dict[str, Any]:
    answers = {
        key: deepcopy(value)
        for key, value in (raw_answers or {}).items()
        if key in allowed_answer_keys()
    }
    if raw_answers.get("_survey_id") == SURVEY_ID:
        answers["_survey_id"] = SURVEY_ID
    for _ in range(len(questions())):
        active_ids = {item["id"] for item in active_questions(answers)}
        changed = False
        for key in list(answers):
            owner = _owner_for_key(key)
            if owner and owner["id"] not in active_ids:
                answers.pop(key, None)
                changed = True
        if not changed:
            break
    for question in active_questions(answers):
        if question.get("type") in {"single", "multiple"}:
            selected = set(_selected(question, answers))
            for option in question.get("options") or []:
                if str(option.get("code")) in selected:
                    continue
                prefix = f"{question['id']}__{option['code']}_text"
                for key in list(answers):
                    if key == prefix or key.startswith(f"{prefix}__"):
                        answers.pop(key, None)
        question_id = str(question["id"])
        value = answers.get(question_id)
        if question.get("type") == "composite" and isinstance(value, dict):
            answers[question_id] = _sanitize_record(question, value, answers)
        elif question.get("type") == "dynamicTable":
            rows = value if isinstance(value, list) else []
            count = _number(answers.get(question.get("repeatCountFrom", "")))
            if count is not None and count.is_integer() and 0 <= count <= 1200:
                rows = (rows + [{} for _ in range(max(0, int(count) - len(rows)))])[: int(count)]
            answers[question_id] = [
                {
                    **_sanitize_record(question, row, answers),
                    "segment_id": row.get("segment_id") or str(uuid.uuid4()),
                }
                for row in rows
                if isinstance(row, dict)
            ]
    return answers


def validate_answers(raw_answers: dict[str, Any]) -> dict[str, Any]:
    answers = sanitize_answers(raw_answers)
    active = active_questions(answers)
    missing: list[str] = []
    invalid: list[str] = []
    answered: list[str] = []
    for question in active:
        question_missing, question_invalid = _question_issues(question, answers)
        missing.extend(question_missing)
        invalid.extend(question_invalid)
        if not question_missing and not question_invalid:
            answered.append(str(question["id"]))
    return {
        "complete": not missing and not invalid,
        "active_question_ids": [str(item["id"]) for item in active],
        "answered_question_ids": answered,
        "missing_fields": sorted(set(missing)),
        "invalid_fields": sorted(set(invalid)),
    }


def is_question_complete(question_id: str, answers: dict[str, Any]) -> bool:
    question = get_question(question_id)
    if not question or not should_show(question, answers):
        return False
    missing, invalid = _question_issues(question, answers)
    return not missing and not invalid

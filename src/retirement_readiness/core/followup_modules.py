"""Module definitions and answer views for questionnaire follow-ups."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from retirement_readiness.core.questionnaire import questions, should_show

_PROMPT_PATH = Path(__file__).with_name("data") / "followup-prompt.json"
_FRAMEWORK_PATH = Path(__file__).with_name("data") / "followup-framework-fields.json"
_MODULE_IDS = ("basic", "family_pension", "finance", "health", "activity", "care")
_HOUSEHOLD_FIELDS = {
    "asset_liquidity_for_retirement",
    "debt_repayment_schedule",
    "care_budget_capacity",
    "care_resource_readiness",
}


@lru_cache(maxsize=1)
def followup_prompt() -> dict[str, Any]:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def framework_fields() -> list[dict[str, str]]:
    return json.loads(_FRAMEWORK_PATH.read_text(encoding="utf-8"))


def followup_modules() -> list[dict[str, Any]]:
    configs = followup_prompt()["modules"]
    return [{"id": module_id, **configs[module_id]} for module_id in _MODULE_IDS]


def get_followup_module(module_id: str) -> dict[str, Any] | None:
    return next(
        (module for module in followup_modules() if module["id"] == module_id),
        None,
    )


def active_module_questions(module_id: str, answers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        question
        for question in questions()
        if question.get("sectionId") == module_id and should_show(question, answers)
    ]


def active_followup_modules(answers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        module
        for module in followup_modules()
        if active_module_questions(str(module["id"]), answers)
    ]


def answer_label(question: dict[str, Any], value: Any) -> Any:
    if value is None or value == "":
        return value
    options = {
        str(option.get("code")): str(option.get("label") or option.get("code"))
        for option in question.get("options") or []
    }
    if isinstance(value, list):
        return [options.get(str(item), item) for item in value]
    return options.get(str(value), value)


def readable_module_answers(module_id: str, answers: dict[str, Any]) -> list[dict[str, Any]]:
    readable = []
    for question in active_module_questions(module_id, answers):
        question_id = str(question["id"])
        readable.append(
            {
                "question_id": question_id,
                "question_text": question.get("text") or "",
                "answer": answer_label(question, answers.get(question_id)),
                "supplemental_answers": {
                    key: value
                    for key, value in answers.items()
                    if key.startswith(f"{question_id}__")
                },
            }
        )
    return readable


def followup_subject_scope(
    module_id: str, fields: list[str], explicit_scope: str | None = None
) -> str:
    if module_id == "family_pension" and explicit_scope in {
        "respondent",
        "spouse",
        "household",
    }:
        return explicit_scope
    if module_id == "spouse":
        return "spouse"
    if _HOUSEHOLD_FIELDS.intersection(fields):
        return "household"
    module = get_followup_module(module_id)
    return str(module.get("defaultSubjectScope") if module else "respondent")

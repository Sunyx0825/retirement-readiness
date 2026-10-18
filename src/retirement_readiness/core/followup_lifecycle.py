"""Persistence-neutral lifecycle for module-level retirement follow-ups."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from retirement_readiness.core.followup_answers import normalize_answer
from retirement_readiness.core.followup_modules import (
    active_followup_modules,
    active_module_questions,
    get_followup_module,
)

_LIFECYCLE_STATUSES = {"issued", "answered", "skipped", "no_question"}


def module_answer_fingerprint(answers: dict[str, Any], module_id: str) -> str:
    question_ids = {str(question["id"]) for question in active_module_questions(module_id, answers)}
    module = get_followup_module(module_id) or {}
    question_ids.update(
        dependency_fields(module_id, module.get("allowedFrameworkFields", []), "household")
    )
    fields = sorted(
        (key, value)
        for key, value in answers.items()
        if any(
            key == question_id or key.startswith(f"{question_id}__") for question_id in question_ids
        )
    )
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def followup_budget(
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    max_modules: int,
    max_questions: int,
) -> dict[str, Any]:
    lifecycle = [item for item in events if item.get("status") in _LIFECYCLE_STATUSES]
    modules = {
        str(item.get("module_id")) for item in [*followups, *lifecycle] if item.get("module_id")
    }
    event_ids = {
        str(question_id) for item in lifecycle for question_id in item.get("question_ids") or []
    }
    used_questions = len(event_ids) + sum(
        1 for item in followups if str(item.get("followup_id") or "") not in event_ids
    )
    return {
        "max_modules": max_modules,
        "max_questions": max_questions,
        "used_modules": len(modules),
        "used_questions": used_questions,
        "can_ask": len(modules) < max_modules and used_questions < max_questions,
    }


def module_evaluated(
    answers: dict[str, Any],
    module_id: str,
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> bool:
    fingerprint = module_answer_fingerprint(answers, module_id)
    return any(
        item.get("module_id") == module_id
        and item.get("status") in _LIFECYCLE_STATUSES
        and not item.get("requires_reevaluation")
        and item.get("module_answer_fingerprint") == fingerprint
        for item in events
    )


def missing_followup_module_evaluations(
    answers: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    return [
        str(module["id"])
        for module in active_followup_modules(answers)
        if not module_evaluated(answers, str(module["id"]), [], events)
    ]


def reconcile_followups(
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid_followups = effective_followups(answers, followups)
    valid_events = []
    for source in events:
        event = dict(source)
        module_id = str(event.get("module_id") or "")
        if not get_followup_module(module_id) or not active_module_questions(module_id, answers):
            continue
        fingerprint = module_answer_fingerprint(answers, module_id)
        if event.get("status") == "answered":
            ids = {
                item["followup_id"]
                for item in valid_followups
                if item.get("module_id") == module_id
            }
            retained = [
                item
                for item in (event.get("pending_bundle") or {}).get("questions", [])
                if item["followup_id"] in ids
            ]
            if retained:
                event["requires_reevaluation"] = bool(
                    event.get("requires_reevaluation")
                    or len(retained) != len(event.get("question_ids") or [])
                )
                event["module_answer_fingerprint"] = fingerprint
                event["pending_bundle"] = {
                    **event["pending_bundle"],
                    "questions": retained,
                }
                event["question_ids"] = [item["followup_id"] for item in retained]
                event["question_texts"] = [item["question_text"] for item in retained]
                event["framework_fields"] = sorted(
                    {field for item in retained for field in item["framework_fields"]}
                )
                valid_events.append(event)
        elif event.get("module_answer_fingerprint") == fingerprint:
            valid_events.append(event)
    return valid_followups, valid_events


def build_no_followup_event(bundle: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    return _base_event(bundle, answers, "no_question")


def build_followup_event(bundle: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    return _base_event(bundle, answers, "issued")


def _base_event(bundle: dict[str, Any], answers: dict[str, Any], status: str) -> dict[str, Any]:
    now = datetime.now().isoformat()
    module_id = str(bundle["module_id"])
    fingerprint = module_answer_fingerprint(answers, module_id)
    event = {
        "pending_followup_id": f"PF-{uuid.uuid4()}",
        "status": status,
        "parent_question_id": bundle["parent_question_id"],
        "module_id": module_id,
        "module_title": bundle["module_title"],
        "module_prompt_version": bundle["module_prompt_version"],
        "module_question_ids": list(bundle.get("module_question_ids") or []),
        "question_ids": [item["followup_id"] for item in bundle["questions"]],
        "question_texts": [item["question_text"] for item in bundle["questions"]],
        "framework_fields": sorted(
            {field for item in bundle["questions"] for field in item["framework_fields"]}
        ),
        "provider": bundle["provider"],
        "priority": bundle["priority"],
        "issued_at": now,
        "answer_fingerprint": fingerprint,
        "module_answer_fingerprint": fingerprint,
        "pending_bundle": bundle,
    }
    if status != "issued":
        event["resolved_at"] = now
    return event


def answer_pending_event(
    event: dict[str, Any],
    answers: dict[str, Any],
    submitted_answers: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _assert_pending_current(event, answers)
    return _resolved_event(event, "answered"), _answer_records(event, submitted_answers, answers)


def update_answered_event(
    event: dict[str, Any],
    answers: dict[str, Any],
    submitted_answers: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if event.get("status") != "answered":
        raise ValueError("追问答案尚未保存")
    _assert_current_module_answers(event, answers)
    return _resolved_event(event, "answered"), _answer_records(event, submitted_answers, answers)


def _answer_records(
    event: dict[str, Any], submitted_answers: dict[str, Any], answers: dict[str, Any]
) -> list[dict[str, Any]]:
    bundle = event.get("pending_bundle") or {}
    records = []
    for question in bundle.get("questions") or []:
        followup_id = str(question.get("followup_id") or "")
        value = submitted_answers.get(followup_id)
        codes, label, other_text = normalize_answer(question, value)
        selected = codes if isinstance(codes, list) else [codes]
        options = {str(item.get("code")): item for item in question.get("options") or []}
        fields = list(question.get("framework_fields") or [])
        tags = sorted(
            {tag for code in selected for tag in options.get(str(code), {}).get("tags") or []}
        )
        records.append(
            {
                "parent_question_id": event["parent_question_id"],
                "module_id": event["module_id"],
                "module_title": event["module_title"],
                "module_answer_fingerprint": event["module_answer_fingerprint"],
                "subject_scope": question.get("subject_scope"),
                "followup_id": followup_id,
                "trigger_reason": bundle.get("trigger_reason") or "",
                "priority": bundle.get("priority") or "medium",
                "question_text": question.get("question_text") or "",
                "question_type": question.get("question_type") or "single_choice",
                "question_options": question.get("options") or [],
                "answer_raw": value,
                "answer_code": codes,
                "answer_label": label,
                "other_text": other_text,
                "target_framework_fields": fields,
                "dependency_fields": dependency_fields(
                    event["module_id"], fields, question.get("subject_scope")
                ),
                "dependency_fingerprint": dependency_fingerprint(
                    answers,
                    dependency_fields(event["module_id"], fields, question.get("subject_scope")),
                ),
                "structured_tags": sorted({*tags, *(f"framework_{field}" for field in fields)}),
                "data_use": question.get("data_use") or [],
                "prompt_version": event["module_prompt_version"],
                "confidence": 0.88 if bundle.get("provider") == "api" else 0.78,
                "needs_manual_review": any(
                    re.search(r"overseas|unclear|not_sure", str(item))
                    for item in [*tags, *selected]
                ),
                "status": "answered",
                "source": "ai_followup",
            }
        )
    return records


def skip_pending_event(event: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    _assert_pending_current(event, answers)
    return _resolved_event(event, "skipped")


def _assert_pending_current(event: dict[str, Any], answers: dict[str, Any]) -> None:
    if event.get("status") != "issued":
        raise ValueError("追问已经处理")
    _assert_current_module_answers(event, answers)


def _assert_current_module_answers(event: dict[str, Any], answers: dict[str, Any]) -> None:
    module_id = str(event.get("module_id") or "")
    if event.get("module_answer_fingerprint") != module_answer_fingerprint(answers, module_id):
        raise ValueError("当前模块答案已变化，请重新检测追问")


def _resolved_event(event: dict[str, Any], status: str) -> dict[str, Any]:
    updated = dict(event)
    updated["status"] = status
    updated["resolved_at"] = datetime.now().isoformat()
    return updated


_DEPENDENCIES = {
    "occupation_income_volatility": ["Q8", "A8_1", "A8_2", "B8_1", "Q28"],
    "pension_transfer_status": ["Q9", "PENSION_BENEFIT_CITY", "PENSION_HISTORY"],
    "benefit_city_eligibility": ["Q7", "Q9", "PENSION_BENEFIT_CITY", "PENSION_HISTORY"],
    "pension_record_confidence": ["Q9", "PENSION_HISTORY"],
    "personal_pension_barrier": ["Q9", "Q10", "Q10_1"],
    "overseas_retirement_account": ["Q7", "Q9"],
    "asset_liquidity_for_retirement": ["Q27", "Q11", "Q11_1", "Q11_2"],
    "debt_repayment_schedule": ["Q29", "Q29_1", "Q30"],
    "medical_reimbursement_certainty": ["Q31", "Q32", "Q33"],
    "chronic_disease_control": ["Q35", "Q36"],
    "health_behavior_barrier": ["Q35", "Q36", "Q37", "Q38", "Q39", "Q40", "Q41", "Q42"],
    "activity_execution_barrier": ["Q43", "Q43_1", "Q8", "Q35"],
    "social_support_gap": ["KIN_SUPPORT", "Q45"],
    "care_budget_capacity": ["Q46", "Q28", "Q30"],
    "care_resource_readiness": ["Q46", "Q46_1", "Q46_2", "Q46_3", "Q46_4", "Q34"],
}


def dependency_fields(module: str, fields: list[str], scope: str | None) -> list[str]:
    ids = {key for field in fields for key in _DEPENDENCIES.get(field, [])}
    if module == "family_pension":
        spouse_ids = {
            "SP_" + key
            if key.startswith("PENSION_")
            else {"Q9": "P9", "Q31": "P9_5", "Q32": "P9_6"}.get(key, key)
            for key in ids
        }
        ids = spouse_ids if scope == "spouse" else ids | spouse_ids if scope == "household" else ids
        ids.add("Q4")
    return sorted(ids | {"_survey_id"})


def dependency_fingerprint(answers: dict[str, Any], fields: list[str]) -> str:
    values = {
        key: value
        for key, value in answers.items()
        if any(key == field or key.startswith(field + "__") for field in fields)
    }
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def effective_followups(
    answers: dict[str, Any], followups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    def current(item: dict[str, Any]) -> bool:
        fields = item.get("dependency_fields")
        return (
            item.get("dependency_fingerprint") == dependency_fingerprint(answers, fields)
            if fields
            else item.get("module_answer_fingerprint")
            == module_answer_fingerprint(answers, str(item.get("module_id") or ""))
        )

    return [
        item
        for item in followups
        if item.get("status") == "answered"
        and get_followup_module(str(item.get("module_id") or ""))
        and current(item)
    ]


def debt_cleared_before_retirement(
    answers: dict[str, Any], followups: list[dict[str, Any]]
) -> bool | None:
    states = set()
    for item in effective_followups(answers, followups):
        if "debt_repayment_schedule" not in (item.get("target_framework_fields") or []):
            continue
        selected = item.get("answer_code", item.get("answer_raw"))
        values = selected if isinstance(selected, list) else [selected]
        for value in values:
            states.add(
                True
                if value
                in {
                    "before_retirement",
                    "paid_before_retirement",
                    "yes_before_retirement",
                }
                else False
                if value in {"after_retirement", "partly_after_retirement"}
                else None
            )
    return next(iter(states)) if len(states) == 1 else None

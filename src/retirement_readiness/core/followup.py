"""Generate one bounded AI follow-up bundle after a questionnaire module."""

from __future__ import annotations

import json
import re
from logging import getLogger as get_logger
from typing import Any

from retirement_readiness.core.followup_answers import requires_text
from retirement_readiness.core.followup_lifecycle import (
    followup_budget,
    module_evaluated,
)
from retirement_readiness.core.followup_modules import (
    active_module_questions,
    answer_label,
    followup_modules,
    followup_prompt,
    followup_subject_scope,
    framework_fields,
    get_followup_module,
    readable_module_answers,
)
from retirement_readiness.core.followup_rules import (
    followup_needs,
    module_fallback_candidates,
)
from retirement_readiness.core.questionnaire import questions
from retirement_readiness.runtime import openai_client, settings

logger = get_logger(__name__)
_PUNCTUATION = re.compile(r"[，。？！；：、,.?!;:\"“”'‘’（）()【】\[\]《》<>\s]+")
_FRAMEWORK_FIELDS = framework_fields()
_ALLOWED_FIELD_CODES = {item["code"] for item in _FRAMEWORK_FIELDS}
_CORE_COVERED_FIELDS = {"family_care_consensus"}


async def generate_followup_bundle(
    *,
    module_id: str,
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    module = get_followup_module(module_id)
    module_questions = active_module_questions(module_id, answers)
    if not module or not module_questions:
        raise ValueError("追问模块不存在或当前不适用")

    release = followup_prompt()
    anchor_id = str(module_questions[-1]["id"])
    budget = followup_budget(
        followups,
        events,
        max_modules=int(release["maxModules"]),
        max_questions=int(release["maxQuestions"]),
    )
    module_question_ids = {
        str(question_id)
        for event in events
        if event.get("module_id") == module_id
        for question_id in event.get("question_ids") or []
    } | {str(item["followup_id"]) for item in followups if item.get("module_id") == module_id}
    budget["module_remaining"] = max(
        0, int(release["maxQuestionsPerModule"]) - len(module_question_ids)
    )
    module_used = any(item.get("module_id") == module_id for item in [*events, *followups])
    budget["can_ask"] = (
        (module_used or budget["used_modules"] < budget["max_modules"])
        and budget["used_questions"] < budget["max_questions"]
        and budget["module_remaining"] > 0
    )
    if not budget["can_ask"] or module_evaluated(answers, module_id, followups, events):
        return _no_ask(module, anchor_id, answers, "fallback")

    payload = _prompt_payload(module, anchor_id, answers, followups, events, budget)
    try:
        response = await openai_client.chat_completion(
            messages=[
                {"role": "system", "content": release["sharedSystemPrompt"]},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            model=getattr(settings, "FAST_MODEL", None),
            temperature=0.0,
            max_tokens=1800,
            stream=False,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        normalized = _normalize_bundle(raw, module, anchor_id, answers, followups, events, budget)
        if normalized is None:
            raise ValueError("AI module follow-up response failed schema validation")
        return normalized
    except Exception as error:
        logger.warning(
            "retirement module follow-up model failed; using deterministic fallback: %s",
            error,
        )
        return _deterministic_module_followup(module, anchor_id, answers, followups, events, budget)


def _prompt_payload(
    module: dict[str, Any],
    anchor_id: str,
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
    budget: dict[str, Any],
) -> dict[str, Any]:
    release = followup_prompt()
    module_id = str(module["id"])
    module_order = [item["id"] for item in followup_modules()]
    current_order = module_order.index(module_id)
    previous_answers = []
    for question in questions():
        section_id = str(question.get("sectionId") or "")
        if section_id not in module_order or module_order.index(section_id) >= current_order:
            continue
        question_id = str(question["id"])
        supplements = {
            key: value for key, value in answers.items() if key.startswith(f"{question_id}__")
        }
        value = answer_label(question, answers.get(question_id))
        if value not in (None, "", []) or supplements:
            previous_answers.append(
                {
                    "question_id": question_id,
                    "section_id": section_id,
                    "question_text": question.get("text") or "",
                    "answer": value,
                    "supplemental_answers": supplements,
                }
            )

    allowed = set(module["allowedFrameworkFields"])
    return {
        "module": {
            "id": module_id,
            "title": module["title"],
            "subject_scope": module["defaultSubjectScope"],
            "priority_focus": module["priorityFocus"],
            "must_not_ask": module["mustNotAsk"],
        },
        "current_module_answers": readable_module_answers(module_id, answers),
        "previous_important_answers": previous_answers[-36:],
        "static_questionnaire_coverage": release["globalStaticCoverage"],
        "retirement_memory": {
            "followup_needs": followup_needs(answers, followups),
            "consistency_issues": [],
        },
        "already_asked_followups": [
            {
                "module_id": item.get("module_id"),
                "subject_scope": item.get("subject_scope"),
                "question_text": item.get("question_text"),
                "framework_fields": item.get("target_framework_fields"),
                "answer_label": item.get("answer_label"),
            }
            for item in followups
        ],
        "issued_or_skipped_followups": [
            {
                "module_id": item.get("module_id"),
                "status": item.get("status"),
                "question_texts": item.get("question_texts"),
                "framework_fields": item.get("framework_fields"),
            }
            for item in events
        ],
        "allowed_framework_fields": [
            field for field in _FRAMEWORK_FIELDS if field["code"] in allowed
        ],
        "budget": {
            "remaining_modules": max(0, int(release["maxModules"]) - int(budget["used_modules"])),
            "remaining_questions": max(
                0, int(release["maxQuestions"]) - int(budget["used_questions"])
            ),
            "max_questions_this_module": budget["module_remaining"],
        },
        "output_contract": {
            "parent_question_id": anchor_id,
            "should_ask": "boolean",
            "trigger_reason": "string; 仅供后台审计",
            "priority": "high | medium | low",
            "questions": [
                {
                    "question_text": "string; 只写给受访者看的单一问题",
                    "question_type": "single_choice | multiple_choice | short_text_with_tags",
                    "subject_scope": "respondent | spouse | household；明确本题对象，不得向无配偶者询问配偶",
                    "framework_fields": ["必须来自allowed_framework_fields"],
                    "options": [{"code": "string", "label": "string", "tags": ["string"]}],
                    "data_use": [
                        "report_personalization",
                        "research_explanation_variable",
                    ],
                }
            ],
        },
        "final_checks": [
            "没有关键缺口时should_ask=false且questions为空数组。",
            "不得询问static_questionnaire_coverage已经覆盖的同义信息。",
            "不得重复already_asked_followups或issued_or_skipped_followups中的字段和同义问题。",
            "选择题必须有2至5个可编码选项；短文本题可以不提供options。",
        ],
    }


def _normalize_bundle(
    raw: Any,
    module: dict[str, Any],
    anchor_id: str,
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
    budget: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    candidate = next(
        (
            item
            for item in (
                raw,
                raw.get("followup"),
                raw.get("result"),
                raw.get("data"),
                raw.get("output"),
            )
            if isinstance(item, dict)
        ),
        None,
    )
    if candidate is None:
        return None
    raw_questions = candidate.get(
        "questions",
        candidate.get("followup_questions", candidate.get("items", [])),
    )
    if not isinstance(raw_questions, list):
        return None
    should_ask = candidate.get("should_ask") in {True, "true"} or (
        "should_ask" not in candidate and bool(raw_questions)
    )
    should_not_ask = candidate.get("should_ask") in {False, "false"}
    if not should_ask and not should_not_ask:
        return None

    release = followup_prompt()
    remaining = min(
        int(budget["module_remaining"]),
        int(release["maxQuestions"]) - int(budget["used_questions"]),
    )
    normalized = [
        _normalize_question(item, module, anchor_id, index)
        for index, item in enumerate(raw_questions, start=1)
    ]
    selected = (
        _dedupe_questions(
            [item for item in normalized if item],
            followups,
            events,
            answers,
            remaining,
        )
        if should_ask
        else []
    )
    return _bundle(
        module,
        anchor_id,
        answers,
        selected,
        candidate.get("priority"),
        "api",
    )


def _normalize_question(
    raw: Any, module: dict[str, Any], anchor_id: str, index: int
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = _clean_question_text(
        str(
            raw.get("question_text")
            or raw.get("question")
            or raw.get("text")
            or raw.get("title")
            or ""
        )
    )
    raw_fields = raw.get(
        "framework_fields",
        raw.get("target_framework_fields", raw.get("frameworkFields", [])),
    )
    if isinstance(raw_fields, str):
        raw_fields = [raw_fields]
    fields = list(
        dict.fromkeys(
            str(item)
            for item in raw_fields or []
            if str(item) in _ALLOWED_FIELD_CODES and str(item) in module["allowedFrameworkFields"]
        )
    )
    if not fields or _CORE_COVERED_FIELDS.intersection(fields):
        return None
    raw_type = str(raw.get("question_type") or raw.get("type") or "single_choice")
    question_type = {
        "multiple": "multiple_choice",
        "text": "short_text_with_tags",
        "short_text": "short_text_with_tags",
    }.get(raw_type, raw_type)
    if question_type not in {
        "single_choice",
        "multiple_choice",
        "short_text_with_tags",
    }:
        return None
    options = []
    for option in raw.get("options") or []:
        if not isinstance(option, dict):
            continue
        code = option.get("code", option.get("value", option.get("id")))
        label = option.get("label", option.get("text", option.get("name", code)))
        if code and label:
            tags = option.get("tags") or []
            options.append(
                {
                    "code": str(code),
                    "label": str(label),
                    "requires_text": requires_text({**option, "code": code, "label": label}),
                    "tags": list(
                        dict.fromkeys(
                            str(tag) for tag in ([tags] if isinstance(tags, str) else tags)
                        )
                    ),
                }
            )
    data_use = raw.get("data_use", raw.get("dataUse", []))
    data_use = [data_use] if isinstance(data_use, str) else data_use
    stable_field = re.sub(r"[^a-zA-Z0-9_-]", "_", fields[0])
    return {
        "followup_id": f"FQ-{anchor_id}-{stable_field}-{index}",
        "question_text": text,
        "question_type": question_type,
        "framework_fields": fields,
        "options": options,
        "data_use": list(dict.fromkeys(str(item) for item in data_use or []))
        or [
            "report_personalization",
            "research_explanation_variable",
            *(f"framework:{field}" for field in fields),
        ],
        "subject_scope": followup_subject_scope(
            str(module["id"]), fields, str(raw.get("subject_scope") or "")
        ),
    }


def _dedupe_questions(
    candidates: list[dict[str, Any]],
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
    answers: dict[str, Any],
    remaining: int,
) -> list[dict[str, Any]]:
    def scoped(scope: Any, field: Any) -> str:
        return f"{scope or 'respondent'}:{field}"

    used_fields = {
        scoped(item.get("subject_scope"), field)
        for item in followups
        for field in item.get("target_framework_fields") or []
    }
    used_texts = {_normalize_text(str(item.get("question_text") or "")) for item in followups}
    for event in events:
        event_module = str(event.get("module_id") or "")
        for field in event.get("framework_fields") or []:
            used_fields.add(scoped(followup_subject_scope(event_module, [str(field)]), field))
        used_texts.update(_normalize_text(str(text)) for text in event.get("question_texts") or [])
    core_texts = {
        _normalize_text(str(text))
        for question in questions()
        for text in (question.get("text"), question.get("original_text"))
        if text
    }
    selected = []
    for candidate in candidates:
        if len(selected) >= max(0, remaining):
            break
        if _is_low_quality(candidate, answers):
            continue
        fingerprint = _normalize_text(candidate["question_text"])
        fields = candidate["framework_fields"]
        scope = candidate.get("subject_scope")
        if (
            not fingerprint
            or fingerprint in used_texts
            or fingerprint in core_texts
            or _has_similar_text(fingerprint, used_texts)
            or _has_similar_text(fingerprint, core_texts)
            or any(scoped(scope, field) in used_fields for field in fields)
        ):
            continue
        selected.append(candidate)
        used_texts.add(fingerprint)
        used_fields.update(scoped(scope, field) for field in fields)
    return selected


def _deterministic_module_followup(
    module: dict[str, Any],
    anchor_id: str,
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    events: list[dict[str, Any]],
    budget: dict[str, Any],
) -> dict[str, Any]:
    candidates, trigger, priority = module_fallback_candidates(str(module["id"]), answers)
    normalized = [
        _normalize_question(item, module, anchor_id, index)
        for index, item in enumerate(candidates, start=1)
    ]
    release = followup_prompt()
    selected = _dedupe_questions(
        [item for item in normalized if item],
        followups,
        events,
        answers,
        min(
            int(budget["module_remaining"]),
            int(release["maxQuestions"]) - int(budget["used_questions"]),
        ),
    )
    bundle = _bundle(module, anchor_id, answers, selected, priority, "fallback")
    if selected:
        bundle["trigger_reason"] = trigger
    return bundle


def _bundle(
    module: dict[str, Any],
    anchor_id: str,
    answers: dict[str, Any],
    selected: list[dict[str, Any]],
    priority: Any,
    provider: str,
) -> dict[str, Any]:
    has_questions = bool(selected)
    return {
        "parent_question_id": anchor_id,
        "module_id": module["id"],
        "module_title": module["title"],
        "module_prompt_version": followup_prompt()["version"],
        "module_question_ids": [
            question["id"] for question in active_module_questions(str(module["id"]), answers)
        ],
        "should_ask": has_questions,
        "trigger_reason": (
            "当前答案提示存在评估框架关键字段缺口，已触发补充问题。"
            if has_questions
            else "当前答案未暴露必须追问的关键字段缺口。"
        ),
        "priority": (
            priority if has_questions and priority in {"high", "medium", "low"} else "low"
        ),
        "questions": selected,
        "provider": provider,
    }


def _no_ask(
    module: dict[str, Any],
    anchor_id: str,
    answers: dict[str, Any],
    provider: str,
) -> dict[str, Any]:
    return _bundle(module, anchor_id, answers, [], "low", provider)


def _contradicts_known_facts(text: str, answers: dict[str, Any]) -> bool:
    chronic = _codes(answers.get("Q35"))
    hospitalization = str(answers.get("Q36") or "")
    disease_rules = {
        "cancer": r"癌症|恶性肿瘤|肿瘤|化疗|放疗|靶向|免疫治疗",
        "hypertension": r"高血压|血压",
        "diabetes": r"糖尿病|血糖",
        "hyperlipidemia": r"高血脂|血脂",
        "coronary_heart": r"冠心病|心脏病|心梗|心绞痛",
        "chronic_lung": r"慢性肺|哮喘|慢阻肺|肺功能",
        "arthritis": r"关节炎|类风湿|关节疼痛",
        "stomach_liver_kidney": r"胃病|肝病|肾病|肾功能|肝功能",
    }
    if any(
        code not in chronic and re.search(pattern, text) for code, pattern in disease_rules.items()
    ):
        return True
    has_chronic = bool(chronic) and "none" not in chronic
    if not has_chronic and re.search(
        r"慢性病|慢病|所患|病情|治疗方案|规范用药|定期复诊|复诊用药|控制情况|复查安排",
        text,
    ):
        return True
    return hospitalization in {"0", "none"} and bool(
        re.search(r"住院后|出院后|住院治疗后|本次住院|住院恢复", text)
    )


def _codes(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    return set() if value in {None, ""} else {str(value)}


def _is_low_quality(question: dict[str, Any], answers: dict[str, Any]) -> bool:
    text = question["question_text"]
    fields = question["framework_fields"]
    return bool(
        not 8 <= len(text) <= 96
        or _contradicts_known_facts(text, answers)
        or re.search(
            r"[①②③④⑤]|请分别|包括但不限于|请详细说明|为什么需要|需要补充|发现.*需要",
            text,
        )
        or len(re.findall(r"[？?]", text)) > 1
        or (
            question["question_type"] != "short_text_with_tags"
            and not 2 <= len(question["options"]) <= 5
        )
        or _CORE_COVERED_FIELDS.intersection(fields)
    )


def _has_similar_text(candidate: str, existing: set[str], threshold: float = 0.68) -> bool:
    pairs = _bigrams(candidate)
    return (
        any(
            other and len(pairs.intersection(other)) / min(len(pairs), len(other)) >= threshold
            for other in (_bigrams(text) for text in existing)
        )
        if pairs
        else False
    )


def _bigrams(value: str) -> set[str]:
    normalized = _normalize_text(value)
    return {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))}


def _clean_question_text(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    candidate = next(
        (line for line in reversed(lines) if line.endswith(("？", "?"))),
        lines[-1] if lines else "",
    )
    candidate = re.sub(
        r"^(追问问题|问题|题目|AI追问|Q\d+|FQ[-\w]*)[：:.\s-]*",
        "",
        candidate,
        flags=re.I,
    )
    return re.sub(r"^(基于|由于|发现|考虑到|为了).+?[，,；;]\s*", "", candidate).strip()


def _normalize_text(value: str) -> str:
    return _PUNCTUATION.sub("", value).lower()

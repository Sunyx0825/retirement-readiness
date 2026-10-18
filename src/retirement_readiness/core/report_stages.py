"""Independent AI stages used by retirement report orchestration."""

from __future__ import annotations

import asyncio
import json
import re
from logging import getLogger as get_logger
from typing import Any

from retirement_readiness.core.report_assessment_context import visible_evidence_text
from retirement_readiness.core.report_labels import REPORT_RESULT_FACT_IDS
from retirement_readiness.core.report_prompts import (
    REPORT_CALL_A_PROMPT,
    REPORT_OPEN_INSIGHT_PROMPT,
    REPORT_OVERALL_ASSESSMENT_PROMPT,
    REPORT_SCORE_CAPTION_PROMPT,
    REPORT_SECTION2_FINANCIAL_OVERVIEW_PROMPT,
    REPORT_SECTION2_OVERVIEW_PROMPT,
    section2_prompt,
)
from retirement_readiness.core.rule_engine import validate_recommendation_choices
from retirement_readiness.runtime import openai_client, settings

logger = get_logger(__name__)
_REPORT_OBJECT_CODES = (
    "basic_living",
    "routine_medical",
    "major_medical",
    "long_term_care",
    "financial_cognition",
    "health_capital",
    "activity_social",
    "care_planning",
)
_SCORE_OBJECT_CODES = (
    "financial_cognition",
    "health_capital",
    "activity_social",
    "care_planning",
)
_OBJECT_GUIDANCE = {
    "basic_living": "解释家庭纯养老金对家庭基本生活支出的覆盖。资产不在该指数分子中；个人与配偶养老金可以说明构成，但不要把家庭结果误写成个人结果。",
    "routine_medical": "解释家庭纯养老金对家庭基本生活和常规医疗模型支出的覆盖。配偶未采集慢病与住院信息，不得据此推断配偶健康或费用。",
    "major_medical": "解释家庭养老金、资产衍生收入和风险折扣后金融资产本金对单次家庭重疾情景的覆盖。本金只计一次，房产展示估值不进入该指数。",
    "long_term_care": "解释家庭养老金、资产衍生收入和风险折扣后金融资产本金对完整退休规划期24小时照护情景的覆盖，并区分资金测算与受访者本人的照护偏好。",
    "financial_cognition": "比较本人预期与测算时，对象自身得分无需占用calculationFactIds；正文引用哪些额外指数，就逐项返回对应ID，不判定谁对谁错。",
    "health_capital": "只根据已确认的健康行为和管理条件解释得分；不得把这些行为写成会影响疾病控制、病情、费用、体能或照护依赖。",
    "activity_social": "结合活动构想、参与条件和支持联系解释得分；不把意向写成已经落地，不用“孤立”定性，也不从联系数量推断活动依赖、突发时能否获助或替代家庭照护。阶段适配v2：有已确认现实参与困难时优先评价其对已有活动的影响，不将当前障碍淡化为退休前期未细化；否则结合距本人退休时间和现实参与条件，说明现阶段已有基础、提前接触的价值及以后细化的空间。距退休较远时，已有活动与渠道同伴可构成前期基础，尚无详细退休日程不等于当前生活缺口；临近退休再强调衔接具体安排。只评估准备尺度与条件的意义，不把将来再排日程或核验资源写成行动要求，不写无依据的紧迫结论；联系人较多本身不证明能弥补或替代另一类支持。",
    "care_planning": "结合照护偏好、资源核验和家庭沟通解释得分；不得替家人分配责任，也不得假定承诺或服务可得。阶段适配v2：已确认当前照护困难优先于退休远近，直接评价眼前需要与已有准备的衔接；否则明确区分前期了解、临近退休的偏好衔接及已存在的当前照护需求。距退休较远且无已确认现实需要时，说明了解选项为何有价值，具体服务尚未落实属于以后细化空间；低分反映准备状态，不等于急迫风险。已有书面安排与共识不能说成缺失；有当前困难则按现实需要评价，慢病本身不证明失能。收束时，有现实困难就说明当前协助衔接是眼前重点，不以距退休远、未来了解或无需立即服务淡化它；没有已确认现实需要才写未来细化空间。未报告或未询问当前需求只能写尚未确认，不得说本人没有需求或无需照护。不写行动步骤；时间不明不编数字。",
}


async def run_call_a(
    *,
    evidence: dict[str, Any],
    problems: list[dict[str, Any]],
    recommendation_candidates: list[dict[str, Any]],
    baseline_narrative: dict[str, list[str]],
    relationships: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    eligible_recommendation_ids = {item["rule"]["ruleId"] for item in recommendation_candidates}
    eligible_relationships = tuple(
        relation
        for relation in relationships
        if relation["recommendationId"] in eligible_recommendation_ids
    )
    problem_inputs = [_rule_input(item, eligible_relationships) for item in problems]
    recommendation_inputs = [
        _rule_input(item, eligible_relationships) for item in recommendation_candidates
    ]
    request = {
        "verifiedFacts": _verified_rule_facts([*problems, *recommendation_candidates]),
        "deterministicSummary": {
            "financialIndices": (evidence.get("calculation_facts") or {}).get("financial_indices")
            or {},
            "scores": (evidence.get("calculation_facts") or {}).get("scores") or {},
        },
        "baselineNarrative": baseline_narrative,
        "problemCandidates": _group_rule_inputs(problem_inputs),
        "recommendationCandidates": _group_rule_inputs(recommendation_inputs),
        "requiredObjectDecisions": [
            {
                "objectCode": code,
                "problemSelections": [
                    {
                        "ruleId": item["ruleId"],
                        "evidenceFactIds": item["requiredEvidenceFactIds"],
                    }
                    for item in problem_inputs
                    if item["objectCode"] == code
                ],
                "recommendationSelections": (
                    "从该对象的 recommendationCandidates 中选择0至3条；有问题时必须用 primary_fix 覆盖每个问题"
                    if any(item["objectCode"] == code for item in recommendation_inputs)
                    else []
                ),
            }
            for code in _REPORT_OBJECT_CODES
        ],
        "outputContract": {
            "v2_report": {"conclusion": ["string"]},
            "objectDecisions": "copy every object and every problem selection; choose zero to three allowed recommendation selections per object and satisfy primary_fix coverage",
        },
    }
    last_error: Exception | None = None
    for attempt in range(2):
        payload = await _call_json_stage("rule_selection", request, REPORT_CALL_A_PROMPT)
        try:
            if not isinstance(payload, dict):
                raise ValueError("REPORT_CALL_A_API_REQUIRED")
            recommendation_ids = _call_a_recommendation_ids(
                payload.get("objectDecisions"), problem_inputs
            )
            selected = validate_recommendation_choices(
                recommendation_ids,
                problems=problems,
                candidates=recommendation_candidates,
                relationships=relationships,
            )
            conclusion = _validated_conclusion((payload.get("v2_report") or {}).get("conclusion"))
            return {
                "recommendations": selected,
                "conclusion": conclusion,
                "status": "model",
            }
        except (AttributeError, TypeError, ValueError) as error:
            last_error = error
            logger.warning(
                "retirement report stage rule_selection structure validation failed "
                "(attempt %s/2): %s",
                attempt + 1,
                error,
            )
    if last_error is None:
        raise ValueError("REPORT_CALL_A_API_REQUIRED")
    raise last_error


async def run_overall_assessment(
    evidence: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    summary = await _call_validated_json_stage(
        "overall_assessment",
        {
            "reportAssessmentContext": _report_assessment_context_input(evidence),
            "deterministicObjects": [_deterministic_object_input(item) for item in sections],
            "outputContract": {
                "overallResult": "4至5句话、160至300字的一个自然段",
                "contextIds": ["正文实际使用的contextId"],
                "calculationFactIds": ["正文实际使用的calculationFactId，最多4个"],
                "assumptionIds": ["正文实际使用的assumptionId，最多3个"],
            },
        },
        REPORT_OVERALL_ASSESSMENT_PROMPT,
        lambda payload: _validated_overall_assessment(payload, evidence),
    )
    return {
        "overall_summary": summary,
        "priority_order": [],
        "status": "model",
    }


async def run_section_personalization(
    evidence: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    overview, problems, recommendations = await asyncio.gather(
        run_section_overviews(evidence, sections),
        run_problem_explanations(evidence, sections),
        run_recommendation_explanations(evidence, sections),
    )
    return {
        "overviews": overview["items"],
        "problems": problems["items"],
        "recommendations": recommendations["items"],
        "status": {
            "overview": overview["status"],
            "problem": problems["status"],
            "recommendation": recommendations["status"],
        },
    }


async def run_section_overviews(
    evidence: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    financial_codes = {
        "basic_living",
        "routine_medical",
        "major_medical",
        "long_term_care",
    }
    financial = [item for item in sections if item["code"] in financial_codes]
    dimensions = [item for item in sections if item["code"] not in financial_codes]
    results = await asyncio.gather(
        *(
            _run_section_overview_group(
                evidence,
                group,
                REPORT_SECTION2_FINANCIAL_OVERVIEW_PROMPT
                if group is financial
                else REPORT_SECTION2_OVERVIEW_PROMPT,
            )
            for group in (financial, dimensions)
            if group
        )
    )
    return {
        "items": {key: value for result in results for key, value in result["items"].items()},
        "status": "model",
    }


async def _run_section_overview_group(
    evidence: dict[str, Any],
    sections: list[dict[str, Any]],
    prompt: str,
) -> dict[str, Any]:
    items = await _call_validated_json_stage(
        "section_overviews",
        {
            "reportAssessmentContext": _report_assessment_context_input(evidence),
            "reportObjects": [_report_object_input(item) for item in sections],
            "objectTasks": [
                {
                    "objectRef": item["code"],
                    "objectGuidance": _OBJECT_GUIDANCE[item["code"]],
                    "selectedProblemRefs": [rule["rule_id"] for rule in item.get("problems") or []],
                    "selectedRecommendationRefs": [
                        rule["rule_id"] for rule in item.get("recommendations") or []
                    ],
                }
                for item in sections
            ],
            "outputContract": {
                "items": [
                    {
                        "objectCode": item["code"],
                        "overview": "2至3句话、70至180字",
                        "contextIds": ["正文实际使用的contextId，最多8个"],
                        "calculationFactIds": ["正文实际使用的calculationFactId，最多2个"],
                        "assumptionIds": ["正文实际使用的assumptionId，最多2个"],
                    }
                    for item in sections
                ]
            },
        },
        prompt,
        lambda payload: _validated_overviews(payload.get("items"), sections, evidence),
    )
    return {
        "items": items,
        "status": "model",
    }


async def run_problem_explanations(
    evidence: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    results = await asyncio.gather(
        *(_run_problem_object(evidence, section) for section in sections if section.get("problems"))
    )
    return {
        "items": {key: value for result in results for key, value in result["items"].items()},
        "status": "model",
    }


async def _run_problem_object(evidence: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    selections = section.get("problems") or []
    items = await _call_validated_json_stage(
        "problem_explanations",
        {
            "reportAssessmentContext": _report_assessment_context_input(evidence),
            "reportObjects": [_report_object_input(section)],
            "platformObjectTask": {
                "objectRef": section["code"],
                "problemFrames": [
                    {"ruleId": item["rule_id"], "selectedProblemRef": item["rule_id"]}
                    for item in selections
                ],
                "expectedItemCount": len(selections),
                "expectedRuleIdsInOrder": [item["rule_id"] for item in selections],
                "outputReminder": "每个输入规则恰好返回一项，数量和顺序必须完全一致；内部ID只放在对应字段，正文只写自然中文。",
            },
        },
        section2_prompt(section["code"], "problem"),
        lambda payload: _require_items(
            _validated_problem_explanations(payload.get("items"), selections, evidence),
            f"SECTION2_PROBLEM_INVALID:{section['code']}",
        ),
    )
    return {"items": items, "status": "model"}


async def run_recommendation_explanations(
    evidence: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    results = await asyncio.gather(
        *(
            _run_recommendation_object(evidence, section)
            for section in sections
            if section.get("recommendations")
        )
    )
    return {
        "items": {key: value for result in results for key, value in result["items"].items()},
        "status": "model",
    }


async def _run_recommendation_object(
    evidence: dict[str, Any], section: dict[str, Any]
) -> dict[str, Any]:
    selections = section.get("recommendations") or []
    items = await _call_validated_json_stage(
        "recommendation_explanations",
        {
            "reportAssessmentContext": _report_assessment_context_input(evidence),
            "reportObjects": [_report_object_input(section)],
            "platformObjectTask": {
                "objectRef": section["code"],
                "recommendationFrames": [
                    {
                        "ruleId": item["rule_id"],
                        "selectedRecommendationRef": item["rule_id"],
                        "supportedProblemIds": [
                            problem_id
                            for problem_id in item.get("supported_problem_ids") or []
                            if problem_id
                            in {problem["rule_id"] for problem in section.get("problems") or []}
                        ],
                    }
                    for item in selections
                ],
                "expectedItemCount": len(selections),
                "expectedRuleIdsInOrder": [item["rule_id"] for item in selections],
                "outputReminder": "每个输入规则恰好返回一项，数量和顺序必须完全一致；内部ID只放在对应字段，正文只写自然中文。",
            },
        },
        section2_prompt(section["code"], "recommendation"),
        lambda payload: _require_items(
            _validated_recommendation_explanations(payload.get("items"), selections, evidence),
            f"SECTION2_RECOMMENDATION_INVALID:{section['code']}",
        ),
    )
    return {"items": items, "status": "model"}


async def run_score_captions(
    evidence: dict[str, Any], sections: list[dict[str, Any]]
) -> dict[str, Any]:
    items = await _call_validated_json_stage(
        "score_captions",
        {
            "reportAssessmentContext": _report_assessment_context_input(evidence),
            "scoreObjects": [
                {
                    "objectCode": item["code"],
                    "title": item["title"],
                    "score": _object_score(item),
                }
                for item in sections
                if item["code"]
                in {
                    "financial_cognition",
                    "health_capital",
                    "activity_social",
                    "care_planning",
                }
            ],
            "outputContract": {
                "items": [
                    {
                        "objectCode": item["code"],
                        "caption": "一句自然短说明",
                        "contextIds": ["实际使用的contextId"],
                        "calculationFactIds": ["实际使用的calculationFactId，最多1项"],
                    }
                    for item in sections
                    if item["code"]
                    in {
                        "financial_cognition",
                        "health_capital",
                        "activity_social",
                        "care_planning",
                    }
                ]
            },
        },
        REPORT_SCORE_CAPTION_PROMPT,
        lambda payload: _validated_score_captions(
            payload.get("items"), _SCORE_OBJECT_CODES, evidence
        ),
    )
    return {
        "items": items,
        "status": "model",
    }


async def run_open_insights(evidence: dict[str, Any]) -> dict[str, Any]:
    items = await _call_validated_json_stage(
        "open_insights",
        {
            "reportAssessmentContext": _report_assessment_context_input(evidence),
            "outputContract": {
                "insights": [
                    {
                        "title": "普通人容易理解的自然标题",
                        "situationAnalysis": "处境分析",
                        "whyItMatters": "重要性",
                        "sourceRecommendationIds": ["实际采用的已选建议ruleId"],
                        "contextIds": ["实际使用的contextId"],
                        "calculationFactIds": ["实际使用的calculationFactId"],
                        "assumptionIds": ["实际使用的assumptionId"],
                        "visibleEvidenceIds": ["用于用户可见现实依据的1至5个最直接ID"],
                        "recommendationDirection": "建议方向",
                        "actionSteps": ["行动1", "行动2"],
                        "confidence": "high | medium | low",
                    }
                ]
            },
        },
        REPORT_OPEN_INSIGHT_PROMPT,
        lambda payload: _validated_open_insights(payload, evidence),
    )
    return {
        "items": items,
        "status": "model",
    }


def _validated_open_insight(raw: Any, evidence: dict[str, Any], index: int) -> dict[str, Any]:
    item_number = index + 1
    if not isinstance(raw, dict):
        raise ValueError(f"OPEN_INSIGHT_ITEM_{item_number}_TYPE")
    recommendation_ids = _validated_open_insight_ids(
        raw.get("sourceRecommendationIds"),
        _recommendation_ids(evidence),
        item_number=item_number,
        field="SOURCE_RECOMMENDATION_IDS",
        minimum=1,
        maximum=3,
    )
    context_ids = _validated_open_insight_ids(
        raw.get("contextIds"),
        _context_ids(evidence),
        item_number=item_number,
        field="CONTEXT_IDS",
        minimum=1,
        maximum=8,
    )
    calculation_ids = _validated_open_insight_ids(
        raw.get("calculationFactIds"),
        _calculation_fact_ids(evidence),
        item_number=item_number,
        field="CALCULATION_FACT_IDS",
        minimum=0,
        maximum=3,
    )
    assumption_ids = _validated_open_insight_ids(
        raw.get("assumptionIds"),
        _assumption_ids(evidence),
        item_number=item_number,
        field="ASSUMPTION_IDS",
        minimum=0,
        maximum=2,
    )
    referenced_ids = set(context_ids + calculation_ids + assumption_ids)
    visible_ids = _validated_open_insight_ids(
        raw.get("visibleEvidenceIds"),
        referenced_ids,
        item_number=item_number,
        field="VISIBLE_EVIDENCE_IDS",
        minimum=1,
        maximum=5,
    )
    confidence = str(raw.get("confidence") or "")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError(f"OPEN_INSIGHT_ITEM_{item_number}_CONFIDENCE")

    title = _validated_open_insight_text(raw.get("title"), item_number, "TITLE", 8, 54)
    situation = _validated_open_insight_text(
        raw.get("situationAnalysis"), item_number, "SITUATION_ANALYSIS", 30, 260
    )
    why = _validated_open_insight_text(
        raw.get("whyItMatters"), item_number, "WHY_IT_MATTERS", 25, 200
    )
    direction = _validated_open_insight_text(
        raw.get("recommendationDirection"),
        item_number,
        "RECOMMENDATION_DIRECTION",
        10,
        120,
    )
    raw_steps = raw.get("actionSteps")
    if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 4:
        raise ValueError(f"OPEN_INSIGHT_ITEM_{item_number}_ACTION_STEPS_COUNT")
    steps = [
        _validated_open_insight_text(
            value,
            item_number,
            f"ACTION_STEP_{step_index + 1}",
            8,
            100,
        )
        for step_index, value in enumerate(raw_steps)
    ]
    return {
        "insight_id": f"OPEN-INSIGHT-{item_number}",
        "title": title,
        "situation": situation,
        "why_it_matters": why,
        "source_recommendation_ids": recommendation_ids,
        "context_ids": context_ids,
        "calculation_fact_ids": calculation_ids,
        "assumption_ids": assumption_ids,
        "visible_evidence_ids": visible_ids,
        "evidence": _visible_evidence_text(evidence, visible_ids),
        "recommendation_direction": direction,
        "action_steps": steps,
        "confidence": confidence,
    }


def _validated_open_insights(
    payload: dict[str, Any], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    raw_items = payload.get("insights")
    if not isinstance(raw_items, list) or len(raw_items) > 3:
        raise ValueError("OPEN_INSIGHT_COUNT")
    items = [_validated_open_insight(raw, evidence, index) for index, raw in enumerate(raw_items)]
    return items


async def _call_validated_json_stage(
    stage: str,
    payload: dict[str, Any],
    system_prompt: str,
    validator: Any,
) -> Any:
    last_error: Exception | None = None
    previous_response: dict[str, Any] | None = None
    for attempt in range(2):
        if attempt == 0:
            data = await _call_json_stage(stage, payload, system_prompt)
        else:
            data = await _call_json_stage(
                stage,
                payload,
                system_prompt,
                validation_error=str(last_error),
                previous_response=previous_response,
            )
        try:
            return validator(data)
        except (AttributeError, TypeError, ValueError) as error:
            last_error = error
            previous_response = data
            logger.warning(
                "retirement report stage %s structure validation failed (attempt %s/2): %s",
                stage,
                attempt + 1,
                error,
            )
    raise ValueError(f"RETIREMENT_REPORT_STAGE_VALIDATION_FAILED:{stage}") from last_error


async def _call_json_stage(
    stage: str,
    payload: dict[str, Any],
    system_prompt: str,
    *,
    validation_error: str | None = None,
    previous_response: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        ]
        if validation_error:
            compact_error = " ".join(validation_error.split())[:1000]
            if previous_response is not None:
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            previous_response,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "上一次完整JSON未通过结构校验，错误代码："
                        f"{compact_error}。保留所有已符合输出合同的字段和值，"
                        "只修复错误代码指向的字段；仍须返回完整合法JSON，不要输出解释。"
                    ),
                }
            )
        response = await openai_client.chat_completion(
            stage=stage,
            messages=messages,
            model=getattr(settings, "FAST_MODEL", None),
            temperature=0.0,
            max_tokens=2200,
            stream=False,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content or "{}")
        return data if isinstance(data, dict) else None
    except Exception as error:
        logger.exception("retirement report stage %s failed", stage)
        raise RuntimeError(f"RETIREMENT_REPORT_STAGE_FAILED:{stage}") from error


def _rule_input(item: dict[str, Any], relationships: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    rule = item["rule"]
    evidence = item.get("evidence") or []
    evidence_ids = [str(fact["factId"]) for fact in evidence if fact.get("factId")]
    return {
        "ruleId": rule["ruleId"],
        "objectCode": rule["objectCode"],
        "title": rule["title"],
        "meaning": rule.get("content") or "",
        **(
            {
                "coreAction": rule.get("action") or "",
                "completionMarker": rule.get("completionMarker") or "",
            }
            if rule.get("kind") == "recommendation"
            else {}
        ),
        "eligibleEvidence": [
            {"factId": fact["factId"], "statement": fact["statement"]}
            for fact in evidence
            if fact.get("factId")
        ],
        "requiredEvidenceFactIds": evidence_ids,
        "requiredEvidenceAll": list(rule["evidenceWhen"]["all"]),
        "requiredEvidenceAny": list(rule["evidenceWhen"]["any"]),
        "priorityScore": item.get("applies_score") or 0,
        "priorityConditionSatisfied": bool(item.get("applies_score")),
        "platformQualified": True,
        **(
            {
                "relatedProblems": [
                    {
                        "problemId": relation["problemId"],
                        "relationType": relation["relationType"],
                        "relationPriority": relation["priority"],
                    }
                    for relation in relationships
                    if relation["recommendationId"] == rule["ruleId"]
                ]
            }
            if rule.get("kind") == "recommendation"
            else {
                "relatedRecommendations": [
                    {
                        "recommendationId": relation["recommendationId"],
                        "relationType": relation["relationType"],
                        "relationPriority": relation["priority"],
                    }
                    for relation in relationships
                    if relation["problemId"] == rule["ruleId"]
                ]
            }
        ),
    }


def _group_rule_inputs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "objectCode": code,
            "candidates": [item for item in items if item["objectCode"] == code],
        }
        for code in _REPORT_OBJECT_CODES
    ]


def _verified_rule_facts(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    facts: dict[str, str] = {}
    for item in items:
        for fact in item.get("evidence") or []:
            if fact.get("factId") and fact.get("statement"):
                facts[str(fact["factId"])] = str(fact["statement"])
    return [{"factId": fact_id, "statement": statement} for fact_id, statement in facts.items()]


def _call_a_recommendation_ids(value: Any, problem_inputs: list[dict[str, Any]]) -> list[str]:
    if not isinstance(value, list) or len(value) != len(_REPORT_OBJECT_CODES):
        raise ValueError("CALL_A_OBJECT_DECISION_COUNT")
    by_code: dict[str, dict[str, Any]] = {}
    for decision in value:
        if not isinstance(decision, dict):
            raise ValueError("CALL_A_OBJECT_DECISION_INVALID")
        code = str(decision.get("objectCode") or "")
        if code not in _REPORT_OBJECT_CODES or code in by_code:
            raise ValueError(f"CALL_A_INVALID_OR_DUPLICATE_OBJECT:{code}")
        if not isinstance(decision.get("problemSelections"), list) or not isinstance(
            decision.get("recommendationSelections"), list
        ):
            raise ValueError(f"CALL_A_OBJECT_SELECTION_NOT_ARRAY:{code}")
        by_code[code] = decision
    result: list[str] = []
    for expected_code in _REPORT_OBJECT_CODES:
        decision = by_code[expected_code]
        expected_problems = [
            {
                "ruleId": item["ruleId"],
                "evidenceFactIds": item["requiredEvidenceFactIds"],
            }
            for item in problem_inputs
            if item["objectCode"] == expected_code
        ]
        if decision["problemSelections"] != expected_problems:
            raise ValueError(f"CALL_A_PROBLEM_SELECTION_MISMATCH:{expected_code}")
        selections = decision.get("recommendationSelections")
        for selection in selections:
            if not isinstance(selection, dict) or not selection.get("ruleId"):
                raise ValueError(f"CALL_A_RECOMMENDATION_SELECTION_INVALID:{expected_code}")
            result.append(str(selection["ruleId"]))
    return result


def _deterministic_object_input(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "objectCode": item["code"],
        "code": item["code"],
        "title": item["title"],
        "kind": (
            "financial_coverage"
            if item["code"]
            in {"basic_living", "routine_medical", "major_medical", "long_term_care"}
            else "readiness_dimension"
        ),
        "score": _object_score(item),
    }


def _report_object_input(item: dict[str, Any]) -> dict[str, Any]:
    deterministic = _deterministic_object_input(item)
    code = item["code"]
    return {
        "objectCode": code,
        "title": item["title"],
        "kind": deterministic["kind"],
        "resultFactId": REPORT_RESULT_FACT_IDS[code],
    }


def _object_score(item: dict[str, Any]) -> str:
    metric = item.get("metric") or {}
    display = str(metric.get("display") or "暂不计算")
    return (
        f"准备充足程度 {display}"
        if metric.get("kind") == "coverage_ratio"
        else f"{float(metric.get('value') or 0) / 10:.1f}/10"
        if metric.get("kind") == "score"
        else display
    )


def _validated_conclusion(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("CALL_A_CONCLUSION_NOT_ARRAY")
    result = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
    if not 1 <= len(result) <= 3:
        raise ValueError("CALL_A_CONCLUSION_COUNT")
    return result


def _validated_overall_assessment(payload: dict[str, Any], evidence: dict[str, Any]) -> str:
    fact_ids = _validated_id_list(
        payload.get("calculationFactIds"),
        _calculation_fact_ids(evidence),
        minimum=0,
        maximum=4,
    )
    context_ids = _validated_id_list(
        payload.get("contextIds"),
        _context_ids(evidence),
        minimum=0,
        maximum=8,
    )
    assumption_ids = _validated_id_list(
        payload.get("assumptionIds"),
        _assumption_ids(evidence),
        minimum=0,
        maximum=3,
    )
    if context_ids is None or fact_ids is None or assumption_ids is None:
        raise ValueError("OVERALL_ASSESSMENT_REFERENCE_IDS_INVALID")
    summary = str(payload.get("overallResult") or "").strip()
    if not 80 <= len(summary) <= 360:
        raise ValueError(
            f"OVERALL_ASSESSMENT_LENGTH: overallResult当前{len(summary)}字，超过或不足校验范围80至360字；请改写为原提示要求的160至300字"
        )
    return summary


def _require_items(value: dict[str, str] | None, error: str) -> dict[str, str]:
    if value is None:
        raise ValueError(error)
    return value


def _validated_overviews(
    value: Any,
    sections: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != len(sections):
        raise ValueError("SECTION2_OVERVIEW_ITEM_COUNT")
    result: dict[str, str] = {}
    errors = []
    financial_codes = {
        "basic_living",
        "routine_medical",
        "major_medical",
        "long_term_care",
    }
    for raw, section in zip(value, sections, strict=True):
        code = section["code"]
        if not isinstance(raw, dict) or raw.get("objectCode") != code:
            errors.append(f"{code}: objectCode或顺序错误")
            continue
        overview = str(raw.get("overview") or "").strip()
        if not 40 <= len(overview) <= 220:
            errors.append(
                f"{code}.overview为{len(overview)}字；按原提示改为70至180字，校验硬上限220字"
            )
        required = REPORT_RESULT_FACT_IDS.get(code) if code in financial_codes else None
        for key, allowed, minimum, maximum in (
            ("contextIds", _context_ids(evidence), 1, 8),
            (
                "calculationFactIds",
                _calculation_fact_ids(evidence),
                1 if required else 0,
                2,
            ),
            ("assumptionIds", _assumption_ids(evidence), 0, 2),
        ):
            ids = _validated_id_list(raw.get(key), allowed, minimum=minimum, maximum=maximum)
            if ids is None:
                errors.append(f"{code}.{key}须为{minimum}至{maximum}个有效ID")
            elif required and key == "calculationFactIds" and required not in ids:
                errors.append(f"{code}.{key}必须包含{required}")
        result[code] = overview
    if errors:
        raise ValueError("SECTION2_OVERVIEW_INVALID: " + "; ".join(errors))
    return result


def _validated_score_captions(
    value: Any,
    object_codes: tuple[str, ...],
    evidence: dict[str, Any],
) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != len(object_codes):
        raise ValueError("REPORT_SCORE_CAPTION_ITEM_COUNT")
    result: dict[str, str] = {}
    for raw, code in zip(value, object_codes, strict=True):
        if not isinstance(raw, dict) or raw.get("objectCode") != code:
            raise ValueError(f"REPORT_SCORE_CAPTION_OBJECT_ORDER:{code}")
        context_ids = _validated_id_list(
            raw.get("contextIds"), _context_ids(evidence), minimum=0, maximum=8
        )
        calculation_ids = _validated_id_list(
            raw.get("calculationFactIds"),
            _calculation_fact_ids(evidence),
            minimum=0,
            maximum=1,
        )
        if context_ids is None or calculation_ids is None:
            raise ValueError(f"REPORT_SCORE_CAPTION_REFERENCE_IDS:{code}")
        caption = _safe_text(raw.get("caption"), 90)
        if len(caption) < 15:
            raise ValueError(f"REPORT_SCORE_CAPTION_TEXT_INVALID:{code}")
        result[code] = caption
    return result


def _validated_problem_explanations(
    value: Any,
    selections: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, str] | None:
    if not isinstance(value, list) or len(value) != len(selections):
        return None
    result: dict[str, str] = {}
    for raw, selection in zip(value, selections, strict=True):
        if (
            not isinstance(raw, dict)
            or raw.get("ruleId") != selection["rule_id"]
            or not _explanation_references_valid(raw, evidence)
            or _validated_id_list(
                raw.get("contextIds"),
                _context_ids(evidence),
                minimum=0,
                maximum=2,
            )
            is None
        ):
            return None
        situation = _safe_text(raw.get("situationAnalysis"), 160)
        impact = _safe_text(raw.get("retirementImpact"), 160)
        explanation = _join_sentences(situation, impact)
        if not situation or not impact or not 15 <= len(explanation) <= 200:
            return None
        result[selection["rule_id"]] = explanation
    return result


def _validated_recommendation_explanations(
    value: Any,
    selections: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, str] | None:
    if not isinstance(value, list) or len(value) != len(selections):
        return None
    result: dict[str, str] = {}
    for raw, selection in zip(value, selections, strict=True):
        if (
            not isinstance(raw, dict)
            or raw.get("ruleId") != selection["rule_id"]
            or not _explanation_references_valid(raw, evidence)
            or _validated_id_list(
                raw.get("contextIds"),
                _context_ids(evidence),
                minimum=0,
                maximum=6,
            )
            is None
        ):
            return None
        fit = _safe_text(raw.get("fitAnalysis"), 90)
        next_step = _safe_text(raw.get("personalizedNextStep"), 130)
        explanation = _join_sentences(fit, next_step)
        if not fit or not next_step or not 50 <= len(explanation) <= 200:
            return None
        result[selection["rule_id"]] = explanation
    return result


def _join_sentences(*values: str) -> str:
    return "".join(f"{value.rstrip('。；;')}。" for value in values if value)


def _safe_text(
    value: Any,
    limit: int,
) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if text and len(text) <= limit else ""


def _bounded_text(
    value: Any,
    minimum: int,
    maximum: int,
) -> str:
    text = _safe_text(
        value,
        maximum,
    )
    return text if len(text) >= minimum else ""


def _bounded_text_list(
    value: Any,
    minimum_count: int,
    maximum_count: int,
    minimum_length: int,
    maximum_length: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum_count <= len(value) <= maximum_count:
        return []
    result = [_bounded_text(item, minimum_length, maximum_length) for item in value]
    return result if all(result) else []


def _validated_id_list(
    value: Any,
    allowed_ids: set[str],
    *,
    minimum: int,
    maximum: int,
) -> list[str] | None:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        return None
    result = [str(item or "") for item in value]
    if len(set(result)) != len(result) or any(item not in allowed_ids for item in result):
        return None
    return result


def _validated_open_insight_ids(
    value: Any,
    allowed_ids: set[str],
    *,
    item_number: int,
    field: str,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"OPEN_INSIGHT_ITEM_{item_number}_{field}_COUNT")
    result = [str(item or "") for item in value]
    if len(set(result)) != len(result) or any(item not in allowed_ids for item in result):
        raise ValueError(f"OPEN_INSIGHT_ITEM_{item_number}_{field}_INVALID")
    return result


def _validated_open_insight_text(
    value: Any,
    item_number: int,
    field: str,
    minimum: int,
    maximum: int,
) -> str:
    text = _bounded_text(value, minimum, maximum)
    if not text:
        raise ValueError(f"OPEN_INSIGHT_ITEM_{item_number}_{field}_LENGTH")
    return text


def _recommendation_ids(evidence: dict[str, Any]) -> set[str]:
    return {
        str(item.get("rule_id"))
        for selections in (evidence.get("recommendations") or {}).values()
        for item in selections
        if isinstance(item, dict) and item.get("rule_id")
    }


def _report_assessment_context_input(evidence: dict[str, Any]) -> dict[str, Any]:
    context = evidence.get("report_assessment_context")
    if not isinstance(context, dict):
        raise ValueError("RETIREMENT_REPORT_ASSESSMENT_CONTEXT_MISSING")
    return context


def _context_ids(evidence: dict[str, Any]) -> set[str]:
    context = _report_assessment_context_input(evidence)
    return {
        str(item.get("contextId"))
        for item in (context.get("factCatalog") or {}).get("questionnaire") or []
        if item.get("contextId") and item.get("referenceable") is not False
    }


def _calculation_fact_ids(evidence: dict[str, Any]) -> set[str]:
    context = _report_assessment_context_input(evidence)
    return {
        str(item.get("calculationFactId"))
        for item in (context.get("factCatalog") or {}).get("calculations") or []
        if isinstance(item, dict) and item.get("calculationFactId")
    }


def _assumption_ids(evidence: dict[str, Any]) -> set[str]:
    context = _report_assessment_context_input(evidence)
    return {
        str(item.get("assumptionId"))
        for item in (context.get("factCatalog") or {}).get("assumptions") or []
        if isinstance(item, dict) and item.get("assumptionId")
    }


def _visible_evidence_text(evidence: dict[str, Any], ids: list[str]) -> str:
    return visible_evidence_text(_report_assessment_context_input(evidence), ids)


def _explanation_references_valid(raw: dict[str, Any], evidence: dict[str, Any]) -> bool:
    return all(
        _validated_id_list(raw.get(key), allowed, minimum=0, maximum=2) is not None
        for key, allowed in (
            ("calculationFactIds", _calculation_fact_ids(evidence)),
            ("assumptionIds", _assumption_ids(evidence)),
        )
    )

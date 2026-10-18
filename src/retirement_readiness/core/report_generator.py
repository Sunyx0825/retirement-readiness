"""Generate a structured retirement report with preserved business stages."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from retirement_readiness.core.constants import CALCULATION_ID, SURVEY_ID
from retirement_readiness.core.followup_lifecycle import effective_followups
from retirement_readiness.core.report_assessment_context import (
    build_report_assessment_context,
)
from retirement_readiness.core.report_labels import REPORT_OBJECT_TITLES
from retirement_readiness.core.report_stages import (
    run_call_a,
    run_open_insights,
    run_overall_assessment,
    run_score_captions,
    run_section_personalization,
)
from retirement_readiness.core.rule_catalog import active_rule_catalog
from retirement_readiness.core.rule_engine import report_rule_candidates
from retirement_readiness.core.rule_facts import build_rule_facts

_OBJECT_ORDER = (
    "basic_living",
    "routine_medical",
    "major_medical",
    "long_term_care",
    "financial_cognition",
    "health_capital",
    "activity_social",
    "care_planning",
)
_OBJECT_TITLES = REPORT_OBJECT_TITLES
_REPORT_GREETING = "尊敬的受访者：感谢您参与本次居民退休准备状况调研。基于您提供的问卷信息和补充回答，我们为您生成家庭退休准备评估报告。其中，财务测算以家庭为单位，健康、活动社交和照护规划仍评价受访者本人。本报告用于帮助您整理当前准备状况、识别需要关注的信息，并形成下一步行动建议；报告中的评估建议仅供参考，不构成投资、保险、医疗、法律或税务建议。"
_REPORT_DEFAULT_CONCLUSION = "本次评估结果反映的是您当前填写信息下的退休准备状态，不代表最终退休状态。养老规划是一项长期、动态的过程，随着收入、资产、家庭、健康和政策口径变化，结果也需要持续更新。"


async def generate_retirement_report(
    *,
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    calculations: dict[str, Any],
) -> dict[str, Any]:
    followups = effective_followups(answers, followups)
    calculations["survey_id"] = SURVEY_ID
    calculations["calculation_id"] = CALCULATION_ID
    catalog = active_rule_catalog()
    facts = build_rule_facts(answers, followups, calculations, catalog)
    candidates = report_rule_candidates(catalog, facts)
    call_a_context = {
        "problems": candidates["problems"],
        "recommendations": [],
    }
    call_a = await run_call_a(
        evidence=_model_evidence(
            answers, followups, calculations, call_a_context, catalog.relationships
        ),
        problems=candidates["problems"],
        recommendation_candidates=candidates["recommendation_candidates"],
        baseline_narrative={
            "greeting": [_REPORT_GREETING],
            "conclusion": [_REPORT_DEFAULT_CONCLUSION],
        },
        relationships=catalog.relationships,
    )
    selected = {
        "problems": candidates["problems"],
        "recommendations": call_a["recommendations"],
    }
    baseline = _baseline_report(calculations, selected, catalog.rule_pack_id, catalog.relationships)
    evidence = _model_evidence(answers, followups, calculations, selected, catalog.relationships)
    overall, personalization, score_captions, open_insights = await asyncio.gather(
        run_overall_assessment(evidence, baseline["sections"]),
        run_section_personalization(evidence, baseline["sections"]),
        run_score_captions(evidence, baseline["sections"]),
        run_open_insights(evidence),
    )
    _apply_model_stages(
        baseline,
        call_a,
        overall,
        personalization,
        score_captions,
        open_insights,
    )
    baseline["v2_report"] = _v2_report(baseline, calculations)
    baseline["visuals"] = _report_visuals(calculations, baseline["sections"])
    baseline["markdown"] = report_markdown(baseline)
    return baseline


def _baseline_report(
    calculations: dict[str, Any],
    selected: dict[str, list[dict[str, Any]]],
    rule_pack_id: str,
    relationships: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    indices = calculations["financial_indices"]
    scores = calculations["scores"]
    problem_by_object = _group_selections(selected["problems"], ())
    recommendation_by_object = _group_selections(selected["recommendations"], relationships)
    sections = []
    for code in _OBJECT_ORDER:
        metric = _metric(code, indices, scores)
        problems = problem_by_object.get(code, [])
        recommendations = recommendation_by_object.get(code, [])
        if metric is None and not problems and not recommendations:
            continue
        sections.append(
            {
                "code": code,
                "title": _OBJECT_TITLES[code],
                "metric": metric,
                "assessment": _section_assessment(code, metric),
                "problems": problems,
                "recommendations": recommendations,
                "personalized_note": "",
            }
        )
    lowest = sorted(
        (
            (item["metric"]["normalized"], item["title"])
            for item in sections
            if item["metric"] is not None
        ),
        key=lambda item: item[0],
    )[:3]
    return {
        "schema_version": "retirement-readiness-report/1.0.0",
        "rule_pack_id": rule_pack_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "title": "家庭退休准备评估报告",
        "overall_summary": _deterministic_overall_summary(calculations),
        "opening_insight": "先处理影响基本生活、医疗和照护安全的事项，再逐步完善长期积累与生活安排。",
        "priority_order": [title for _, title in lowest],
        "action_focus": [
            item["rule"]["action"]
            for item in selected["recommendations"][:3]
            if item["rule"].get("action")
        ],
        "headline_metrics": {
            "monthly_total_income": calculations["monthly_total_income"],
            "monthly_total_expense": calculations["monthly_total_expense"],
            "monthly_gap": calculations["monthly_gap"],
            "coverage_indices": {
                "basic_living": indices["basic_living"],
                "routine_medical": indices["basic_living_plus_routine_medical"],
                "major_medical": indices["basic_living_plus_major_medical"],
                "long_term_care": indices["basic_living_plus_care"],
            },
            "scores": scores,
        },
        "sections": sections,
        "warnings": calculations.get("warnings") or [],
        "assumptions": calculations.get("default_parameters") or [],
        "calculation_appendix": {
            "assumptions": calculations["assumptions"],
            "income_breakdown": calculations["income_breakdown"],
            "expense_breakdown": calculations["expense_breakdown"],
            "asset_breakdown": calculations["asset_breakdown"],
            "financial_indices": calculations["financial_indices"],
            "scores": calculations["scores"],
            "low_confidence_fields": calculations.get("low_confidence_fields") or [],
        },
        "model_enhancement": {
            "stages": {
                "rule_selection": "pending",
                "overall_assessment": "pending",
                "section_overview": "pending",
                "problem_explanation": "pending",
                "recommendation_explanation": "pending",
                "score_captions": "pending",
                "open_insights": "pending",
            },
            "call_order": [
                "RULE_SELECTION",
                "OVERALL_ASSESSMENT",
                "SECTION_OVERVIEW",
                "PROBLEM_EXPLANATION",
                "RECOMMENDATION_EXPLANATION",
                "SCORE_CAPTIONS",
                "OPEN_INSIGHTS",
            ],
        },
    }


def _group_selections(
    items: list[dict[str, Any]], relationships: tuple[dict[str, Any], ...]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        rule = item["rule"]
        grouped.setdefault(rule["objectCode"], []).append(
            {
                "rule_id": rule["ruleId"],
                "title": rule["title"],
                "content": rule["content"],
                **({"action": rule["action"]} if rule.get("action") else {}),
                **(
                    {"completion_marker": rule["completionMarker"]}
                    if rule.get("completionMarker")
                    else {}
                ),
                "evidence": [fact["statement"] for fact in item["evidence"]],
                "evidence_fact_ids": [fact["factId"] for fact in item["evidence"]],
                "evidence_source_field_ids": [
                    list(fact.get("sourceFieldIds") or []) for fact in item["evidence"]
                ],
                **(
                    {
                        "supported_problem_ids": [
                            relation["problemId"]
                            for relation in relationships
                            if relation["recommendationId"] == rule["ruleId"]
                        ]
                    }
                    if rule.get("kind") == "recommendation"
                    else {}
                ),
            }
        )
    return grouped


def _metric(code: str, indices: dict[str, Any], scores: dict[str, Any]) -> dict[str, Any] | None:
    ratio_keys = {
        "basic_living": "basic_living",
        "routine_medical": "basic_living_plus_routine_medical",
        "major_medical": "basic_living_plus_major_medical",
        "long_term_care": "basic_living_plus_care",
    }
    score_keys = {
        "financial_cognition": "financial_cognition",
        "health_capital": "health_capital",
        "activity_social": "activity_social",
        "care_planning": "care_planning",
    }
    if code in ratio_keys:
        value = number_value(indices.get(ratio_keys[code]))
        return {
            "kind": "coverage_ratio",
            "value": value,
            "display": f"{round(value * 100)}%",
            "normalized": value * 100,
        }
    if code in score_keys:
        value = number_value(scores.get(score_keys[code]))
        return {
            "kind": "score",
            "value": round(value),
            "display": f"{round(value)}分",
            "normalized": value,
        }
    return None


def _section_assessment(code: str, metric: dict[str, Any] | None) -> str:
    if not metric:
        return "当前信息用于形成定性判断。"
    normalized = metric["normalized"]
    if metric["kind"] == "coverage_ratio":
        state = (
            "准备余地比较充足"
            if normalized >= 120
            else "当前准备可以支撑该情景"
            if normalized >= 100
            else "接近充足但仍需要留意缺口"
            if normalized >= 85
            else "存在较明显缺口"
        )
        ratio = round(normalized)
        descriptions = {
            "basic_living": (
                "基本生活充足指数看退休后纯养老金收入能否覆盖衣、食、住、行等基础支出。"
                f"当前为准备充足程度 {ratio}%，{state}，达到100%表示该情景下养老金刚好覆盖支出，"
                "下一步重点是养老金收入、基本生活支出边界和基础缴费安排。"
            ),
            "routine_medical": (
                "慢病风险充足指数看退休后纯养老金收入能否同时覆盖基本生活和门诊、购药、慢病管理等常规医疗支出。"
                f"当前为准备充足程度 {ratio}%，{state}，达到100%表示该情景下养老金刚好覆盖支出，"
                "需要同时看长期医疗习惯、医保连续性和就医路径。"
            ),
            "major_medical": (
                "重疾风险充足指数看退休后纯养老金、资产衍生收入和可变现金融资产本金能否覆盖基本生活及较大住院或治疗支出压力。"
                f"当前为准备充足程度 {ratio}%，{state}，达到100%表示该情景下资源刚好覆盖压力测试需求，"
                "重点是住院垫付、保障责任和应急安排。"
            ),
            "long_term_care": (
                "失能风险充足指数看退休后纯养老金、资产衍生收入和可变现金融资产本金能否覆盖基本生活及较长期照护费用压力。"
                f"当前为准备充足程度 {ratio}%，{state}，达到100%表示该情景下资源刚好覆盖压力测试需求，"
                "重点是费用上限、服务来源和家庭责任。"
            ),
        }
        return descriptions.get(code, f"当前为准备充足程度 {ratio}%，{state}。")

    score = round(normalized)
    descriptions = {
        "financial_cognition": (
            "财务准备认知看养老金、社保医保路径、退休收支预期和资产动用规则能否合成一套行动判断。",
            "重点短板在关键金额、政策路径和资产用途还没有形成清晰规则，需要先把生活资金、医疗保障和资产动用顺序落到可执行安排。",
            "已经有一定判断基础，但养老金记录、医保路径、退休预算或资产使用规则仍需要继续落实。",
            "基础较稳，下一步是把现有判断固化为年度复核、家庭沟通和资金用途规则。",
            "基础较稳，后续重点是保持官方记录、退休预算和资产用途每年更新。",
        ),
        "health_capital": (
            "健康资本积累看健康监测、生活方式、慢病管理、运动体检、指标记录和居家安全能否降低未来医疗与照护压力。",
            "重点短板在运动、体检、自测、慢病管理或居家安全还没有形成稳定节奏，需要先把健康行动固定成日常安排。",
            "已有基础，但健康管理仍需要从临时行动转成长期节奏。",
            "基础较稳，重点是防止退休节奏变化后健康管理松下来。",
            "当前健康管理基础较稳，后续以持续复查、运动、指标记录和居家安全巩固优势。",
        ),
        "activity_social": (
            "活动社交规划看退休后的日常结构、兴趣活动、参与渠道和现实求助网络是否稳定。",
            "重点短板在固定活动、参与渠道、同伴安排或可求助联系人还不够明确，需要先把退休生活节奏具体化。",
            "已有基础，但活动频率、地点、同伴、交通方式和支持网络仍要继续具体化，才能稳定接上退休后的日常节奏。",
            "基础较稳，重点是把活动、联系人、参与渠道和低成本交通方式固定成可持续安排。",
            "当前活动和支持基础较稳，后续以稳定频率、固定联系人、低成本渠道和备用参与方式巩固。",
        ),
        "care_planning": (
            "失能照护规划看照护方式、责任分工、候选资源和预算边界能否真正执行。",
            "重点短板在照护方式、责任人、预算上限或候选资源尚未落地，需要优先形成首选方案和备用方案。",
            "已有基础，但首选方案、备用方案、家庭责任、费用上限和可联系服务渠道仍要写清楚，才能在需要照护时直接执行。",
            "基础较稳，重点是把偏好和家庭共识转化为可联系资源、费用上限和备用联系人。",
            "当前照护规划基础较稳，后续以备用联系人、服务候选、费用上限和家庭定期沟通巩固。",
        ),
    }
    wording = descriptions.get(code)
    if not wording:
        return "当前维度的退休准备还有提升空间。"
    detail = (
        wording[4]
        if score >= 95
        else wording[3]
        if score >= 90
        else wording[2]
        if score >= 60
        else wording[1]
    )
    return f"{wording[0]}{detail}"


def _deterministic_overall_summary(calculations: dict[str, Any]) -> str:
    indices = calculations["financial_indices"]
    scores = calculations["scores"]
    gap = number_value(calculations.get("monthly_gap"))
    if number_value(indices.get("basic_living")) < 1:
        finance = (
            "财务准备方面，退休后的家庭确定性收入尚不能覆盖基本生活，基础现金流仍是当前主要制约。"
        )
    elif (
        min(
            number_value(indices.get("basic_living_plus_major_medical")),
            number_value(indices.get("basic_living_plus_care")),
        )
        < 1
    ):
        finance = "财务准备方面，基本生活资金已有基础，但重疾或失能照护压力仍可能改变退休判断。"
    else:
        finance = (
            "财务准备方面，基本生活、医疗和照护资金已有较好基础，重点是保持资金用途和取用顺序清楚。"
        )
    cashflow = (
        "家庭退休后每月确定性收入高于基本生活支出，当前测算存在结余。"
        if gap >= 0
        else "家庭退休后每月确定性收入低于基本生活支出，当前测算存在缺口。"
    )
    dimension_wording = {
        "health_capital": "健康资本积累",
        "activity_social": "活动社交规划",
        "care_planning": "失能照护规划",
    }
    summaries = []
    for code, title in dimension_wording.items():
        score = number_value(scores.get(code))
        state = "基础较稳" if score >= 90 else "已有一定基础" if score >= 60 else "仍需优先补强"
        summaries.append(f"{title}{state}")
    weakest = sorted(
        (
            (number_value(scores.get(code)), _OBJECT_TITLES[code])
            for code in (
                "financial_cognition",
                "health_capital",
                "activity_social",
                "care_planning",
            )
        ),
        key=lambda item: item[0],
    )[:2]
    priorities = "、".join(title for _, title in weakest)
    return f"{finance}{cashflow}{'，'.join(summaries)}。当前最值得关注的是{priorities}。"


def _model_evidence(
    answers,
    followups,
    calculations,
    selected,
    relationships: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    problem_groups = _group_selections(selected["problems"], ())
    recommendation_groups = _group_selections(selected["recommendations"], relationships)
    return {
        "report_assessment_context": build_report_assessment_context(
            answers=dict(answers),
            followups=list(followups),
            calculations=calculations,
            problems=problem_groups,
            recommendations=recommendation_groups,
        ),
        "calculation_facts": {
            "monthly_total_income": calculations["monthly_total_income"],
            "monthly_total_expense": calculations["monthly_total_expense"],
            "monthly_gap": calculations["monthly_gap"],
            "income_breakdown": calculations["income_breakdown"],
            "expense_breakdown": calculations["expense_breakdown"],
            "asset_breakdown": calculations["asset_breakdown"],
            "financial_indices": calculations["financial_indices"],
            "scores": calculations["scores"],
        },
        "warnings": calculations.get("warnings") or [],
        "problems": problem_groups,
        "recommendations": recommendation_groups,
    }


def _apply_model_stages(
    report: dict[str, Any],
    call_a: dict[str, Any],
    overall: dict[str, Any],
    personalization: dict[str, Any],
    score_captions: dict[str, Any],
    open_insights: dict[str, Any],
) -> None:
    stages = report["model_enhancement"]["stages"]
    stages["rule_selection"] = call_a["status"]
    report["conclusion"] = call_a["conclusion"]
    if overall["overall_summary"]:
        report["overall_summary"] = overall["overall_summary"]
    if overall["priority_order"]:
        titles = {item["code"]: item["title"] for item in report["sections"]}
        report["priority_order"] = [
            titles[code] for code in overall["priority_order"] if code in titles
        ]
    stages["overall_assessment"] = overall["status"]

    problem_explanations = personalization["problems"]
    recommendation_explanations = personalization["recommendations"]
    captions = score_captions["items"]
    for section in report["sections"]:
        code = section["code"]
        if personalization["overviews"].get(code):
            section["personalized_note"] = personalization["overviews"][code]
        if section.get("metric") and captions.get(code):
            section["metric"]["caption"] = captions[code]
        for problem in section["problems"]:
            explanation = problem_explanations.get(problem["rule_id"])
            if explanation:
                problem["personalized_explanation"] = explanation
        for recommendation in section["recommendations"]:
            explanation = recommendation_explanations.get(recommendation["rule_id"])
            if explanation:
                recommendation["personalized_explanation"] = explanation

    stages["section_overview"] = personalization["status"]["overview"]
    stages["problem_explanation"] = personalization["status"]["problem"]
    stages["recommendation_explanation"] = personalization["status"]["recommendation"]
    stages["score_captions"] = score_captions["status"]
    stages["open_insights"] = open_insights["status"]
    report["open_insights"] = open_insights["items"]


def _v2_report(report: dict[str, Any], calculations: dict[str, Any]) -> dict[str, Any]:
    sections = {item["code"]: item for item in report["sections"]}
    financial_codes = (
        "basic_living",
        "routine_medical",
        "major_medical",
        "long_term_care",
    )
    dimension_codes = (
        "financial_cognition",
        "health_capital",
        "activity_social",
        "care_planning",
    )
    return {
        "title": report["title"],
        "greeting": [_REPORT_GREETING],
        "overall_result": [report["overall_summary"]],
        "financial_preparation": {
            "cognition": _v2_dimension(sections["financial_cognition"], 1),
            "behavior_scenarios": [_v2_scenario(sections[code]) for code in financial_codes],
        },
        "non_financial_preparation": {
            "health_capital": _v2_dimension(sections["health_capital"], 1),
            "activity_social": _v2_dimension(sections["activity_social"], 2),
            "care_planning": _v2_dimension(sections["care_planning"], 3),
        },
        "detailed_results": {
            "financial_coverage": [
                _v2_detailed_result(sections[code], "financial_coverage")
                for code in financial_codes
            ],
            "readiness_dimensions": [
                _v2_detailed_result(sections[code], "readiness_dimension")
                for code in dimension_codes
            ],
        },
        "calculation_appendix": _v2_calculation_appendix(calculations),
        "conclusion": report.get("conclusion") or [_REPORT_DEFAULT_CONCLUSION],
    }


def _v2_dimension(section: dict[str, Any], number: int) -> dict[str, Any]:
    title = {
        "financial_cognition": "财务准备认知维度得分",
        "health_capital": "健康资本积累维度得分",
        "activity_social": "活动社交规划维度得分",
        "care_planning": "失能照护规划维度得分",
    }[section["code"]]
    return {
        "title": f"{number}. {title}",
        "score": _v2_score(section),
        "result_interpretation": [section.get("personalized_note") or section["assessment"]],
        "improvement_actions": _section_actions(section),
        "evidence": _section_evidence(section),
    }


def _v2_scenario(section: dict[str, Any]) -> dict[str, Any]:
    titles = {
        "basic_living": "基本生活充足指数",
        "routine_medical": "慢病风险充足指数",
        "major_medical": "重疾风险充足指数",
        "long_term_care": "失能风险充足指数",
    }
    return {
        "title": titles[section["code"]],
        "score": _v2_score(section),
        "result_interpretation": [section.get("personalized_note") or section["assessment"]],
        "improvement_actions": _section_actions(section),
        "caveats": [],
    }


def _v2_detailed_result(section: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "code": section["code"],
        "title": section["title"],
        "kind": kind,
        "score": _v2_score(section),
        "overview": section.get("personalized_note") or section["assessment"],
        "issues": [
            {
                "title": item["title"],
                "description": item.get("personalized_explanation") or item["content"],
                "evidence": list(item.get("evidence") or []),
                "rule_id": item["rule_id"],
                "evidence_fact_ids": list(item.get("evidence_fact_ids") or []),
            }
            for item in section.get("problems") or []
        ],
        "recommendations": [
            {
                "title": item["title"],
                "action": item["action"],
                **(
                    {"why": item["personalized_explanation"]}
                    if item.get("personalized_explanation")
                    else {}
                ),
                "source": "library",
                "library_id": item["rule_id"],
            }
            for item in section.get("recommendations") or []
        ],
    }


def _v2_score(section: dict[str, Any]) -> str:
    metric = section.get("metric") or {}
    if metric.get("kind") == "score":
        return f"{number_value(metric.get('value')) / 10:.1f}/10"
    if metric.get("kind") == "coverage_ratio":
        return f"准备充足程度 {round(number_value(metric.get('value')) * 100)}%"
    return "暂不计算"


def _section_actions(section: dict[str, Any]) -> list[str]:
    return [item["action"] for item in section.get("recommendations") or [] if item.get("action")]


def _section_evidence(section: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            evidence
            for item in section.get("problems") or []
            for evidence in item.get("evidence") or []
        )
    )


def _v2_calculation_appendix(calculations: dict[str, Any]) -> list[dict[str, str]]:
    indices = calculations["financial_indices"]
    resources = indices.get("scenario_resources") or {}
    denominators = indices.get("index_denominators") or {}
    return [
        {
            "title": _OBJECT_TITLES[code],
            "source": "平台确定性测算",
            "formula": "可用资源 ÷ 情景所需",
            "calculation": (
                f"{_yuan(resources.get(index_key))} ÷ "
                f"{_yuan(denominators.get(index_key))} = "
                f"{_percentage(indices.get(index_key))}"
            ),
            "caveat": "结果依赖本次问卷输入及报告所列默认参数。",
        }
        for code, index_key in (
            ("basic_living", "basic_living"),
            ("routine_medical", "basic_living_plus_routine_medical"),
            ("major_medical", "basic_living_plus_major_medical"),
            ("long_term_care", "basic_living_plus_care"),
        )
    ]


def _report_visuals(
    calculations: dict[str, Any], sections: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    income = calculations["income_breakdown"]
    expense = calculations["expense_breakdown"]
    assets = calculations["asset_breakdown"]
    indices = calculations["financial_indices"]
    scores = calculations["scores"]
    pension_income = number_value(income.get("monthly_pension_income"))
    basic_expense = number_value(expense.get("monthly_retirement_basic_living"))
    asset_input_complete = income.get("asset_input_status") != "partial_or_missing"
    household_pension = number_value(income.get("monthly_household_basic_pension"))
    respondent_pension = number_value(income.get("monthly_respondent_basic_pension"))
    realisable_assets = (
        assets.get("household_total_realisable_assets_estimate") if asset_input_complete else None
    )
    monthly_balance = round(pension_income - basic_expense)
    section_by_code = {item["code"]: item for item in sections or []}
    cashflow = (
        (
            "total_income",
            "退休后家庭确定性收入",
            "income_breakdown.monthly_pension_income",
            "total",
            pension_income,
            "基本养老金、二三支柱和商业养老年金",
            "good" if pension_income >= basic_expense else "watch",
        ),
        (
            "household_pension",
            "退休后家庭基本养老金收入",
            "income_breakdown.monthly_household_basic_pension",
            "component",
            household_pension,
            "社保发放给家庭成员的基本养老金合计",
            "good" if household_pension >= basic_expense else "watch",
        ),
        (
            "respondent_pension",
            "退休后个人基本养老金收入",
            "income_breakdown.monthly_respondent_basic_pension",
            "component",
            respondent_pension,
            "社保发放给受访者本人的基本养老金",
            "watch",
        ),
        (
            "realisable_assets",
            "退休时家庭全部可变现资产",
            "asset_breakdown.household_total_realisable_assets_estimate",
            "component",
            realisable_assets,
            "包含家庭金融资产和非自住房产（估算）",
            "watch",
        ),
        (
            "basic_expense",
            "退休后家庭基本生活支出",
            "expense_breakdown.monthly_basic_living_at_retirement",
            "expense",
            basic_expense,
            "家庭衣食住行等基础生活支出",
            "watch",
        ),
        (
            "monthly_gap",
            "退休后家庭每月结余资金",
            "income_breakdown.monthly_pension_income - expense_breakdown.monthly_basic_living_at_retirement",
            "result",
            monthly_balance,
            "退休后家庭确定性收入减去家庭基本生活支出",
            "good" if monthly_balance >= 0 else "risk",
        ),
    )
    return {
        "cashflow": [
            {
                "code": code,
                "label": label,
                "source_field": source_field,
                "role": role,
                "value": value,
                "display": "暂不计算"
                if value is None
                else f"{round(number_value(value)):,} 元"
                + ("/月" if code != "realisable_assets" else ""),
                "caption": caption,
                "status": status,
            }
            for code, label, source_field, role, value, caption, status in cashflow
        ],
        "financial_indices": [
            {
                "code": code,
                "label": label,
                "value": indices[key],
                "threshold": 1,
                "display": f"准备充足程度 {round(indices[key] * 100)}%",
                "status": _ratio_status(indices[key]),
                "caption": caption,
            }
            for code, label, key, caption in (
                (
                    "financial_basic_living",
                    "基本生活充足指数",
                    "basic_living",
                    "退休后每月确定性收入能否负担基本生活支出",
                ),
                (
                    "financial_routine_medical",
                    "慢病风险充足指数",
                    "basic_living_plus_routine_medical",
                    "退休后每月确定性收入能否负担基本生活和常规医疗支出",
                ),
                (
                    "financial_major_medical",
                    "重疾风险充足指数",
                    "basic_living_plus_major_medical",
                    "退休后每月确定性收入与可变现资产的总和能否负担基本生活和大额医疗支出",
                ),
                (
                    "financial_care",
                    "失能风险充足指数",
                    "basic_living_plus_care",
                    "退休后每月确定性收入与可变现资产的总和能否负担基本生活和长期照护支出",
                ),
            )
        ],
        "dimension_scores": [
            {
                "code": code,
                "label": label,
                "score": scores[code] / 10,
                "max": 10,
                "display": f"{scores[code] / 10:.1f}/10",
                "status": _score_status(scores[code]),
                "caption": (
                    ((section_by_code.get(code) or {}).get("metric") or {}).get("caption")
                    or caption
                ),
            }
            for code, label, caption in (
                (
                    "financial_cognition",
                    "财务准备认知",
                    "说明本人对退休收入、支出和准备计划的理解与把握",
                ),
                ("health_capital", "健康资本积累", "说明当前健康习惯和日常管理是否形成稳定安排"),
                (
                    "activity_social",
                    "活动社交规划",
                    "说明退休活动、参与条件和现实支持是否已经想清楚",
                ),
                ("care_planning", "失能照护规划", "说明照护方式、资源核验和家庭沟通是否已有准备"),
            )
        ],
    }


def _ratio_status(value: Any) -> str:
    ratio = number_value(value)
    return "good" if ratio >= 1.1 else "watch" if ratio >= 0.9 else "risk"


def _score_status(value: Any) -> str:
    score = number_value(value)
    return "good" if score >= 75 else "watch" if score >= 60 else "risk"


def report_markdown(report: dict[str, Any]) -> str:
    v2 = report["v2_report"]
    visuals = report["visuals"]
    lines = [
        f"# {report['title']}",
        "",
        *v2["greeting"],
        "",
        "## 测算结果速览",
        "",
    ]
    lines.extend(
        f"- {item['label']}：{item['display']}。{item['caption']}" for item in visuals["cashflow"]
    )
    lines.extend(["", "### 财务准备行为", ""])
    lines.extend(f"- {item['label']}：{item['display']}" for item in visuals["financial_indices"])
    lines.extend(["", "### 退休准备评分", ""])
    lines.extend(f"- {item['label']}：{item['display']}" for item in visuals["dimension_scores"])
    lines.extend(["", "## 一、退休准备总体结果", "", *v2["overall_result"]])
    lines.extend(["", "## 二、退休准备详细测算结果", "", "### A. 财务准备行为"])
    for item in v2["detailed_results"]["financial_coverage"]:
        _append_v2_detailed_result(lines, item)
    lines.extend(["", "### B. 退休准备评分"])
    for item in v2["detailed_results"]["readiness_dimensions"]:
        _append_v2_detailed_result(lines, item)
    lines.extend(["", "## 三、你可能忽视的重要问题和解决方案", ""])
    if report.get("open_insights"):
        for item in report["open_insights"]:
            lines.extend(
                [
                    f"### {item['title']}",
                    "",
                    item["situation"],
                    "",
                    f"为什么值得现在处理：{item['why_it_matters']}",
                    "",
                    f"现实依据：{item['evidence']}",
                    "",
                    "建议行动：",
                ]
            )
            lines.extend(f"- {step}" for step in item.get("action_steps") or [])
    else:
        lines.append("根据本次有效信息，暂未形成同时满足事实门槛和去重要求的隐藏洞察。")
    return "\n".join(lines)


def _append_v2_detailed_result(lines: list[str], item: dict[str, Any]) -> None:
    lines.extend(
        [
            "",
            f"#### {item['title']}　{item['score']}",
            "",
            "**总览**",
            "",
            item["overview"],
            "",
            "**存在的问题**",
            "",
        ]
    )
    if item["issues"]:
        for issue in item["issues"]:
            lines.extend([f"- {issue['title']}：{issue['description']}"])
            lines.extend(f"  - 判断依据：{evidence}" for evidence in issue["evidence"])
    else:
        lines.append("根据本次有效信息，暂未识别出需要优先提示的问题。")
    lines.extend(["", "**改进建议**", ""])
    if item["recommendations"]:
        lines.extend(
            f"- {recommendation['title']}：{recommendation['action']}"
            + (f" {recommendation['why']}" if recommendation.get("why") else "")
            for recommendation in item["recommendations"]
        )
    else:
        lines.append("根据本次有效信息，暂没有满足事实和边界要求的改进建议。")


def _plain(value: Any) -> str:
    number = number_value(value)
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}".rstrip("0").rstrip(".")


def _yuan(value: Any) -> str:
    return f"{_plain(value)}元"


def _percentage(value: Any) -> str:
    return f"{number_value(value) * 100:.0f}%"


def number_value(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except (TypeError, ValueError):
        return 0.0

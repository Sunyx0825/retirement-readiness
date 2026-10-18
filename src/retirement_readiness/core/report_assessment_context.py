"""Source-equivalent fact contract between retirement data and report LLM stages."""

from __future__ import annotations

import math
import re
from typing import Any

from retirement_readiness.core.followup_lifecycle import effective_followups
from retirement_readiness.core.parameters import region_from_composite
from retirement_readiness.core.questionnaire import (
    active_questions,
    administrative_divisions,
    has_value,
    inline_field_definitions,
    validate_answers,
)
from retirement_readiness.core.report_labels import REPORT_RESULT_FACT_IDS

REPORT_ASSESSMENT_CONTEXT_VERSION = "report_assessment_context_v1.4.0"

_FINANCIAL_OBJECTS = [
    "basic_living",
    "routine_medical",
    "major_medical",
    "long_term_care",
    "financial_cognition",
]
_ALL_OBJECTS = [
    *_FINANCIAL_OBJECTS,
    "health_capital",
    "activity_social",
    "care_planning",
]
_SCORE_FACT_IDS = {
    "CALC-SCORE-FINANCIAL-COGNITION",
    "CALC-SCORE-HEALTH-CAPITAL",
    "CALC-SCORE-ACTIVITY-SOCIAL",
    "CALC-SCORE-CARE-PLANNING",
}
_HOUSEHOLD_QUESTION_IDS = {"Q27", "Q28", "Q29", "Q29_1", "Q30", "Q47"}
_SCOPE_BOUNDARIES = {
    "respondent": "仅描述受访者本人，不得扩张为配偶或整个家庭的事实。",
    "spouse": "仅描述配偶，不得扩张为受访者本人或整个家庭的事实。",
    "household": "描述家庭共同财务或家庭共同安排，不得改写为受访者本人单独承担或拥有。",
}
_INLINE_FIELDS = inline_field_definitions()


def build_report_assessment_context(
    *,
    answers: dict[str, Any],
    followups: list[dict[str, Any]],
    calculations: dict[str, Any],
    problems: dict[str, list[dict[str, Any]]],
    recommendations: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build the current semantic contract for independently generated report stages."""
    questionnaire = _questionnaire_facts(answers)
    questionnaire.extend(_followup_facts(answers, followups))
    validation = validate_answers(answers)
    unresolved = sorted(
        {
            *validation.get("missing_fields", []),
            *validation.get("invalid_fields", []),
        }
    )
    return {
        "version": REPORT_ASSESSMENT_CONTEXT_VERSION,
        "subjectScopeDefinitions": dict(_SCOPE_BOUNDARIES),
        "factCatalog": {
            "questionnaire": questionnaire,
            "calculations": _calculation_facts(calculations),
            "assumptions": _assessment_assumptions(calculations),
            "ruleEvidence": _rule_evidence_catalog(problems, recommendations),
        },
        "dataQuality": {
            "completeForAiNarrative": bool(questionnaire) and bool(validation.get("complete")),
            "unresolvedSourceFieldIds": unresolved,
            "warnings": list(calculations.get("warnings") or []),
            "lowConfidenceFields": list(calculations.get("low_confidence_fields") or []),
        },
        "selectedProblems": _selected_rule_context(problems, recommendation=False),
        "selectedRecommendations": _selected_rule_context(recommendations, recommendation=True),
    }


def visible_evidence_text(context: dict[str, Any], ids: list[str]) -> str:
    """Render only normalized, referenceable catalog entries selected by the model."""
    catalog = context.get("factCatalog") or {}
    questionnaire = {
        str(item.get("contextId")): item
        for item in catalog.get("questionnaire") or []
        if item.get("contextId") and item.get("referenceable") is not False
    }
    calculations = {
        str(item.get("calculationFactId")): item
        for item in catalog.get("calculations") or []
        if item.get("calculationFactId")
    }
    assumptions = {
        str(item.get("assumptionId")): item
        for item in catalog.get("assumptions") or []
        if item.get("assumptionId")
    }
    parts: list[str] = []
    for item_id in ids:
        if item_id in questionnaire:
            item = questionnaire[item_id]
            parts.append(f"{item['questionName']}：{item['answerText']}")
        elif item_id in calculations:
            item = calculations[item_id]
            parts.append(f"{item['label']}：{item['displayValue']}")
        elif item_id in assumptions:
            item = assumptions[item_id]
            parts.append(f"{item['label']}：{item['displayValue']}")
    return "；".join(part for part in parts if part.strip())


def _questionnaire_facts(answers: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for question in active_questions(answers):
        answer = _answer_text(question, answers)
        if not answer:
            continue
        scope = _subject_scope(str(question["id"]))
        facts.append(
            {
                "contextId": _question_context_id(str(question["id"])),
                "domain": _question_domain(question),
                "questionName": _clean_question_name(str(question.get("text") or "")),
                "answerText": answer,
                "status": "direct",
                "subjectScope": scope,
                "source": "questionnaire",
                "referenceable": True,
                "scopeBoundaryId": _scope_boundary_id(scope),
                "sourceFieldIds": _source_field_ids(question, answers),
                "dataBoundary": "untrusted_user_data",
            }
        )
    return facts


def _followup_facts(
    answers: dict[str, Any], followups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    active_ids = {str(item["id"]) for item in active_questions(answers)}
    for item in effective_followups(answers, followups):
        module_id = str(item.get("module_id") or "")
        followup_id = str(item.get("followup_id") or "")
        parent_id = str(item.get("parent_question_id") or "")
        if (
            not module_id
            or not followup_id
            or parent_id not in active_ids
            or item.get("status") != "answered"
            or not has_value(item.get("answer_label") or item.get("answer_raw"))
        ):
            continue
        grouped.setdefault((parent_id, followup_id), []).append(item)

    facts = []
    for (parent_id, followup_id), items in grouped.items():
        latest = items[-1]
        answers_text = {
            _sanitize_text(item.get("answer_label") or item.get("answer_raw")) for item in items
        }
        status = (
            "conflict" if latest.get("status") == "conflict" or len(answers_text) > 1 else "direct"
        )
        scope = str(latest.get("subject_scope") or _subject_scope(parent_id))
        facts.append(
            {
                "contextId": _followup_context_id(parent_id, followup_id),
                "domain": _followup_domain(latest),
                "questionName": _clean_question_name(
                    str(latest.get("question_text") or "补充回答")
                ),
                "answerText": (
                    "该项信息存在冲突，未进入报告事实"
                    if status == "conflict"
                    else next(iter(answers_text))
                ),
                "status": status,
                "subjectScope": scope,
                "source": "ai_followup",
                "parentQuestionId": parent_id,
                "referenceable": status != "conflict",
                "scopeBoundaryId": _scope_boundary_id(scope),
                "sourceFieldIds": [f"followups.{parent_id}.{followup_id}"],
                "dataBoundary": "untrusted_user_data",
            }
        )
    return facts


def _answer_text(question: dict[str, Any], answers: dict[str, Any]) -> str:
    question_id = str(question["id"])
    value = answers.get(question_id)
    question_type = str(question.get("type") or "")
    if question_type == "single":
        base = (
            _format_object(question, value)
            if isinstance(value, dict)
            else _option_label(question, value)
        )
    elif question_type == "multiple":
        selected = value if isinstance(value, list) else [value] if has_value(value) else []
        base = "；".join(filter(None, (_option_label(question, item) for item in selected)))
    elif isinstance(value, dict):
        base = _format_object(question, value)
    elif question_type == "dynamicTable" and isinstance(value, list):
        base = "；".join(
            f"第{i}段：{_format_object(question, row)}" for i, row in enumerate(value, 1)
        )
    elif isinstance(value, list):
        base = "；".join(_display_scalar(item) for item in value if has_value(item))
    else:
        base = _display_scalar(value) if has_value(value) or value == 0 else ""
    supplemental = _supplemental_answer(question_id, answers)
    if supplemental and _INLINE_FIELDS.get(question_id):
        return supplemental
    if not base:
        return supplemental
    if not supplemental or isinstance(value, dict):
        return base
    return f"{re.sub(r'（填空）[^；，]*', '', base)}；{supplemental}"


def _format_object(question: dict[str, Any], value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if question.get("type") != "dynamicTable" and (
        "province_code" in value or "city_code" in value
    ):
        return _administrative_division_text(value)
    choice = _option_label(question, value.get("choice"))
    if choice:
        details = [
            f"{_display_scalar(value['amount_yuan'])}元"
            if value.get("amount_yuan") is not None
            else "",
            _display_scalar(value.get("city")),
            _display_scalar(value.get("text")),
        ]
        return " ".join(item for item in [choice.replace("（填空）", ""), *details] if item)
    fields = question.get("fields") or question.get("rows") or []
    parts = []
    for field in fields:
        raw = value.get(str(field.get("key") or ""))
        if not has_value(raw) and raw != 0:
            continue
        label = next(
            (
                str(option.get("label") or "")
                for option in field.get("options") or []
                if str(option.get("code")) == str(raw)
            ),
            _display_scalar(raw),
        )
        key = str(field.get("key") or "")
        if key.endswith("province_code"):
            label = region_from_composite({"province_code": raw})
        elif key.endswith("city_code"):
            label = region_from_composite({"city_code": raw})
        parts.append(f"{field.get('label')}：{label}{field.get('unit') or ''}")
    return (
        "；".join(parts)
        if parts
        else "；".join(
            _display_scalar(item) for item in value.values() if has_value(item) or item == 0
        )
    )


def _supplemental_answer(question_id: str, answers: dict[str, Any]) -> str:
    definitions = _INLINE_FIELDS.get(question_id) or []
    items = sorted(
        (
            (key, value)
            for key, value in answers.items()
            if key.startswith(f"{question_id}__blank_") and (has_value(value) or value == 0)
        ),
        key=lambda item: _numeric_suffix(item[0]),
    )
    parts = []
    for index, (_, raw) in enumerate(items):
        field = definitions[index] if index < len(definitions) else {}
        label = str(field.get("label") or "补充信息")
        parts.append(f"{label}：{_display_scalar(raw)}{field.get('unit') or ''}")
    return "；".join(parts)


def _calculation_facts(calculations: dict[str, Any]) -> list[dict[str, Any]]:
    income = calculations.get("income_breakdown") or {}
    expense = calculations.get("expense_breakdown") or {}
    assets = calculations.get("asset_breakdown") or {}
    indices = calculations.get("financial_indices") or {}
    scores = calculations.get("scores") or {}
    cashflow = calculations.get("cashflow") or {}
    pension = _number(income.get("monthly_pension_income") or income.get("monthly_basic_pension"))
    asset_support = _number(income.get("monthly_asset_support"))
    available = _number(
        income.get("monthly_consumption_smoothing_support")
        if income.get("monthly_consumption_smoothing_support") is not None
        else pension + asset_support
    )
    support_gap = income.get("monthly_support_gap_with_assets")
    if support_gap is None:
        support_gap = cashflow.get("monthly_support_gap_with_assets")
    basic_expense = _number(
        expense.get("monthly_basic_living_at_retirement")
        if expense.get("monthly_basic_living_at_retirement") is not None
        else expense.get("monthly_retirement_basic_living")
    )
    confidence = _finance_confidence(calculations)
    facts = [
        _fact(
            "CALC-MONTHLY-PENSION-INCOME",
            "退休后家庭确定性收入",
            pension,
            "yuan_month",
            _FINANCIAL_OBJECTS,
            _pension_confidence(calculations),
            "夫妻双方基本养老金、企业或职业年金、个人养老金和商业养老年金的月度合计。",
            "这是按当前参保记录和模型参数得到的估算，不包含金融资产收益、房租或资产本金。",
            components=_pension_components(income),
        ),
        _fact(
            "CALC-MONTHLY-ASSET-SUPPORT",
            "已有资产对养老的月补充金额",
            asset_support,
            "yuan_month",
            _FINANCIAL_OBJECTS,
            confidence,
            "金融资产估算收益、房租收入和风险折扣后可动用金融资产本金的月度平滑额。",
            "基本生活和慢病指数不使用这项资产补充；重疾和失能指数按各自公式计入资产收益和本金。",
        ),
        _fact(
            "CALC-MONTHLY-AVAILABLE-FUNDS",
            "含资产本金平滑动用的退休后每月可支撑资金",
            available,
            "yuan_month",
            _FINANCIAL_OBJECTS,
            confidence,
            "退休后家庭确定性收入与已有资产月补充金额的合计，其中资产月补充包含风险折扣后金融资产本金按退休年限平滑使用的金额。",
            "这是为展示长期消费平滑形成的月度口径，不代表每月都能获得同额的经常性现金收入。",
        ),
        _fact(
            "CALC-HOUSEHOLD-MONTHLY-BASIC-BALANCE",
            "退休后家庭每月结余资金",
            pension - basic_expense,
            "yuan_month",
            _FINANCIAL_OBJECTS,
            confidence,
            "退休后家庭确定性收入减去家庭基本生活月支出。",
            "该差额不计金融资产收益、房租或资产本金，也不包含常规医疗、重疾和照护压力。",
        ),
        _fact(
            "CALC-MONTHLY-BASIC-LIVING-EXPENSE",
            "退休后家庭基本生活月支出",
            basic_expense,
            "yuan_month",
            _FINANCIAL_OBJECTS,
            "medium",
            "按当前家庭支出、退休地点和通胀假设推算的退休时点家庭基本生活支出。",
            "这是模型估算，不等于用户已经承诺的退休预算。",
        ),
        _fact(
            "CALC-MONTHLY-ROUTINE-MEDICAL-EXPENSE",
            "常规医疗月支出",
            _number(expense.get("monthly_routine_medical")),
            "yuan_month",
            ["routine_medical", "financial_cognition"],
            "medium",
            "门诊、购药和慢病管理等常规医疗支出的模型估算。",
            "不代表具体疾病、治疗方案或医保报销后的实际金额。",
        ),
        _fact(
            "CALC-MAJOR-MEDICAL-OOP",
            "大额医疗自付压力",
            _number(expense.get("major_medical_oop_before_assets")),
            "yuan",
            ["major_medical", "financial_cognition"],
            "medium",
            "当前模型设定的大额医疗情景自付压力。",
            "这是单次家庭压力测试参数，不是对任何家庭成员未来疾病或实际医疗账单的预测。",
        ),
        _fact(
            "CALC-ANNUAL-24H-CARE-COST",
            "24小时照护年费用",
            _number(expense.get("annual_24h_care_cost")),
            "yuan_year",
            ["long_term_care", "care_planning"],
            "medium",
            "按照护地区参数估算的24小时照护年度费用。",
            "这是模型场景成本，不代表当地某家机构或某位护工的报价。",
        ),
        _fact(
            "CALC-RISK-ADJUSTED-LIQUID-ASSETS",
            "风险折扣后的可变现金融资产",
            _number(assets.get("risk_adjusted_liquid_assets")),
            "yuan",
            ["major_medical", "long_term_care", "financial_cognition"],
            confidence,
            "按不同资产的流动性系数折算后，可用于重疾或长期照护压力测试的金融资产本金。",
            "同一情景中本金只计入一次；该数值不构成资产处置建议。",
        ),
    ]
    if assets.get("household_total_realisable_assets_estimate") is not None:
        facts.insert(
            4,
            _fact(
                "CALC-HOUSEHOLD-TOTAL-REALISABLE-ASSETS",
                "退休时家庭全部可变现资产",
                _number(assets["household_total_realisable_assets_estimate"]),
                "yuan",
                _FINANCIAL_OBJECTS,
                confidence,
                "当前家庭金融资产按收益率及完整收支形成的月度盈余或亏损滚动至家庭首位退休时点后，再加非自住房产等值估算。",
                "非自住房产按每套等值估算；该展示值不进入四项充足指数，也不构成出售房产建议。",
            ),
        )
    if support_gap is not None:
        gap = _number(support_gap)
        facts.insert(
            3,
            _fact(
                "CALC-MONTHLY-SUPPORT-GAP",
                "每月预计结余" if gap >= 0 else "每月预计缺口",
                gap,
                "yuan_month",
                _FINANCIAL_OBJECTS,
                confidence,
                "含资产本金平滑动用的每月可支撑资金与退休时点基本生活月支出的差额。",
                "该差额使用资产本金月度平滑口径，不等同于经常性现金流结余。",
                display_value=_display_amount(abs(gap), "yuan_month"),
            ),
        )
    for code, index_key, scope, boundary in (
        (
            "basic_living",
            "basic_living",
            "纯养老金在整个退休期内对基本生活支出的覆盖比例。",
            "该指数不计金融资产收益、房租或资产本金。",
        ),
        (
            "routine_medical",
            "basic_living_plus_routine_medical",
            "纯养老金在整个退休期内对基本生活与常规医疗支出的覆盖比例。",
            "该指数不计金融资产收益、房租或资产本金。",
        ),
        (
            "major_medical",
            "basic_living_plus_major_medical",
            "养老金、资产衍生收入和风险折扣后的金融资产本金对基本生活及大额医疗自付压力的覆盖比例。",
            "本金只计入一次；该指数是压力测试，不预测实际患病或支出。",
        ),
        (
            "long_term_care",
            "basic_living_plus_care",
            "养老金、资产衍生收入和风险折扣后的金融资产本金对基本生活及完整退休规划期24小时照护压力的覆盖比例。",
            "本金只计入一次；该指数不预测本人一定发生失能。",
        ),
    ):
        facts.append(
            _fact(
                REPORT_RESULT_FACT_IDS[code],
                {
                    "basic_living": "基本生活充足指数",
                    "routine_medical": "慢病风险充足指数",
                    "major_medical": "重疾风险充足指数",
                    "long_term_care": "失能风险充足指数",
                }[code],
                _number(indices.get(index_key)),
                "ratio",
                [code, "financial_cognition"]
                if code != "long_term_care"
                else [code, "care_planning", "financial_cognition"],
                confidence,
                scope,
                boundary,
            )
        )
    for code, label, scope, boundary in (
        (
            "financial_cognition",
            "财务准备认知得分",
            "依据问卷中财务知识、退休预期、计划和执行信心形成的标准化得分。",
            "得分用于描述当前准备认知，不等同于人格、能力或最终退休结果。",
        ),
        (
            "health_capital",
            "健康资本得分",
            "依据问卷中的健康行为、健康管理和监测情况形成的标准化得分。",
            "得分不是医学诊断，也不预测具体疾病或失能。",
        ),
        (
            "activity_social",
            "活动与社交得分",
            "依据退休活动构想、参与条件和支持联系情况形成的标准化得分。",
            "得分只描述当前准备，不代表未来活动一定能够或不能落地。",
        ),
        (
            "care_planning",
            "照护规划得分",
            "依据照护偏好、资源核验、执行条件和家庭沟通情况形成的标准化得分。",
            "得分不代表家人已经承诺照护，也不确认服务一定可得。",
        ),
    ):
        facts.append(
            _fact(
                REPORT_RESULT_FACT_IDS[code],
                label,
                _number(scores.get(code)),
                "score_10",
                [code],
                "high",
                scope,
                boundary,
            )
        )
    for fact in facts:
        fact["subjectScope"] = (
            "respondent" if fact["calculationFactId"] in _SCORE_FACT_IDS else "household"
        )
    return facts


def _fact(
    fact_id: str,
    label: str,
    value: float,
    unit: str,
    object_codes: list[str],
    confidence: str,
    semantic_scope: str,
    boundary: str,
    *,
    components: list[dict[str, str]] | None = None,
    display_value: str | None = None,
) -> dict[str, Any]:
    fact = {
        "calculationFactId": fact_id,
        "label": label,
        "displayValue": display_value or _display_amount(value, unit),
        "numericValue": value,
        "unit": unit,
        "objectCodes": list(object_codes),
        "confidence": confidence,
        "semanticScope": semantic_scope,
        "interpretationBoundary": boundary,
        "source": "deterministic_calculation",
    }
    if components is not None:
        fact["components"] = components
    return fact


def _assessment_assumptions(calculations: dict[str, Any]) -> list[dict[str, Any]]:
    assumptions = calculations.get("assumptions") or {}
    income = calculations.get("income_breakdown") or {}
    retirement_display = (
        "以家庭首位退休者为共同价格及资产起点，各成员按本人退休节点领取养老金；"
        f"按{_compact(assumptions.get('life_expectancy'))}岁规划，"
        f"家庭费用采用较长退休规划期{_compact(assumptions.get('retirement_years'))}年，"
        "有限养老账户领完后停止计入收入。"
    )
    region = assumptions.get("city_for_estimate") or _region_label(
        (assumptions.get("regions") or {}).get("pension") or {}
    )
    return [
        _assumption(
            "ASSUMP-RETIREMENT-HORIZON",
            "退休期",
            retirement_display,
            _FINANCIAL_OBJECTS,
            "medium",
            "退休年龄或预期寿命变化会改变长期覆盖结果。",
            "derived",
        ),
        _assumption(
            "ASSUMP-INFLATION",
            "通胀率",
            f"支出按每年{_number(assumptions.get('inflation_rate')) * 100:.1f}%的通胀率推算至退休时点",
            _FINANCIAL_OBJECTS,
            "medium",
            "这是长期模型参数，不是对每一年实际物价变化的预测。",
            "system_parameter",
        ),
        _assumption(
            "ASSUMP-ASSET-LIQUIDITY-HAIRCUT",
            "资产流动性折扣",
            "可变现金融资产按各资产类别的流动性系数折算",
            ["major_medical", "long_term_care", "financial_cognition"],
            "medium",
            "折算值只用于压力测试，不代表建议立即变现或按该价值成交。",
            "system_parameter",
        ),
        _assumption(
            "ASSUMP-LONG-TERM-CARE-HORIZON",
            "长期照护压力期",
            "长期照护按24小时照护年费用覆盖完整家庭退休规划期",
            ["long_term_care", "care_planning"],
            "medium",
            "这是统一的单次家庭压力测试口径，不预测任何家庭成员实际照护年限。",
            "system_parameter",
        ),
        _assumption(
            "ASSUMP-PENSION-ESTIMATION",
            "养老金估算方法",
            str(
                income.get("pension_estimation_method")
                or "按本人及配偶当前参保记录分别估算后汇总为家庭确定性收入。"
            ),
            _FINANCIAL_OBJECTS,
            _pension_confidence(calculations),
            "估算受缴费记录、计发参数和未来政策变化影响，不等于最终核定待遇。",
            "derived",
        ),
        _assumption(
            "ASSUMP-SUPPLEMENTAL-PENSION-TERMINAL-VALUE",
            "二、三支柱终值估算",
            "企业及职业年金合并按8%缴费率、3%账户收益率从2015年估算；个人养老金按已填年缴存档位和4%账户收益率估算。工资或缴存额增长率为3%，个人养老金参保地命中试点地区时从2023年起算，否则从2025年起算；退休终值除以本人或配偶第一支柱对应计发月数形成月收入。",
            _FINANCIAL_OBJECTS,
            _pension_confidence(calculations),
            "这是统一模型估算，不等于账户实时余额、投资收益承诺或最终核定领取额；未知信息采用披露的规划假设，不改写原始回答。",
            "system_parameter",
        ),
        _assumption(
            "ASSUMP-PARAMETER-REGION",
            "测算地区",
            f"生活、医疗和照护参数按{region or '全国默认'}口径估算",
            _ALL_OBJECTS,
            "medium",
            "退休地点或实际服务地区变化时，相关费用参数需要重新核对。",
            "derived",
        ),
    ]


def _assumption(
    assumption_id: str,
    label: str,
    display: str,
    objects: list[str],
    confidence: str,
    boundary: str,
    source: str,
) -> dict[str, Any]:
    return {
        "assumptionId": assumption_id,
        "label": label,
        "displayValue": display,
        "objectCodes": list(objects),
        "confidence": confidence,
        "interpretationBoundary": boundary,
        "source": source,
    }


def _rule_evidence_catalog(
    problems: dict[str, list[dict[str, Any]]],
    recommendations: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for groups in (problems, recommendations):
        for items in groups.values():
            for item in items:
                source_lists = item.get("evidence_source_field_ids") or []
                for index, (fact_id, statement) in enumerate(
                    zip(
                        item.get("evidence_fact_ids") or [],
                        item.get("evidence") or [],
                        strict=False,
                    )
                ):
                    normalized = {
                        "factId": str(fact_id),
                        "statement": str(statement),
                        "sourceFieldIds": sorted(
                            str(value)
                            for value in (source_lists[index] if index < len(source_lists) else [])
                        ),
                    }
                    existing = catalog.get(str(fact_id))
                    if existing is not None and existing != normalized:
                        raise ValueError(f"REPORT_ASSESSMENT_RULE_EVIDENCE_CONFLICT:{fact_id}")
                    catalog[str(fact_id)] = normalized
    return [catalog[key] for key in sorted(catalog)]


def _selected_rule_context(
    groups: dict[str, list[dict[str, Any]]], recommendation: bool
) -> list[dict[str, Any]]:
    return [
        {
            "ruleId": item["rule_id"],
            "objectCode": object_code,
            "title": item["title"],
            "meaning": item.get("content") or "",
            **(
                {
                    "actionBoundary": item.get("action") or "",
                    "completionBoundary": item.get("completion_marker") or "",
                }
                if recommendation
                else {}
            ),
            "evidenceFactIds": list(item.get("evidence_fact_ids") or []),
        }
        for object_code, items in groups.items()
        for item in items
    ]


def _pension_components(income: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "label": label,
            "displayValue": _display_amount(_number(income.get(key)), "yuan_month"),
        }
        for label, key in (
            ("本人基本养老金", "monthly_respondent_basic_pension"),
            ("配偶基本养老金", "monthly_spouse_basic_pension"),
            ("本人企业/职业年金", "monthly_respondent_second_pillar_annuity"),
            ("本人个人养老金", "monthly_respondent_personal_pension"),
            ("本人商业养老年金", "monthly_respondent_commercial_annuity"),
            ("配偶企业/职业年金", "monthly_spouse_second_pillar_annuity"),
            ("配偶个人养老金", "monthly_spouse_personal_pension"),
            ("配偶商业养老年金", "monthly_spouse_commercial_annuity"),
        )
    ]


def _finance_confidence(calculations: dict[str, Any]) -> str:
    income = calculations.get("income_breakdown") or {}
    assets = calculations.get("asset_breakdown") or {}
    if calculations.get("finance_profile_status") == "missing":
        return "low"
    if income.get("income_input_status") == "partial_or_missing":
        return "low"
    if income.get("asset_input_status") == "partial_or_missing":
        return "low"
    if assets.get("input_status") == "partial_or_missing":
        return "low"
    return "medium"


def _pension_confidence(calculations: dict[str, Any]) -> str:
    income = calculations.get("income_breakdown") or {}
    statuses = [
        item.get("status")
        for item in (income.get("supplemental_pension_details") or {}).values()
        if isinstance(item, dict) and item.get("status") != "not_participating"
    ]
    if any(status != "estimated" for status in statuses):
        return "low"
    confidences = [
        item.get("employee_index_confidence")
        for item in (
            income.get("pension_details") or {},
            income.get("spouse_pension_details") or {},
        )
        if isinstance(item, dict) and item.get("employee_index_confidence")
    ]
    if "low" in confidences:
        return "low"
    return "high" if confidences and all(value == "high" for value in confidences) else "medium"


def _question_domain(question: dict[str, Any]) -> str:
    question_id = str(question["id"])
    if question_id == "Q8" or question_id.startswith("B8") or question_id == "P8":
        return "work"
    section = str(question.get("sectionId") or "")
    if section in {"health", "activity", "care"}:
        return section
    if section in {"family_pension", "finance"}:
        return "financial"
    return "family"


def _followup_domain(item: dict[str, Any]) -> str:
    fields = " ".join(str(field) for field in item.get("target_framework_fields") or [])
    for pattern, domain in (
        (r"health|medical", "health"),
        (r"activity|social", "activity"),
        (r"care", "care"),
        (r"work|employment", "work"),
    ):
        if re.search(pattern, fields, re.I):
            return domain
    return "financial"


def _subject_scope(question_id: str) -> str:
    if re.match(r"^(?:SP_|P\d|PA\d|PB\d)", question_id):
        return "spouse"
    return "household" if question_id in _HOUSEHOLD_QUESTION_IDS else "respondent"


def _scope_boundary_id(scope: str) -> str:
    return {
        "spouse": "SCOPE-SPOUSE",
        "household": "SCOPE-HOUSEHOLD",
    }.get(scope, "SCOPE-RESPONDENT")


def _source_field_ids(question: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    question_id = str(question["id"])
    fields = sorted(
        (key for key in answers if key == question_id or key.startswith(f"{question_id}__")),
        key=lambda key: (_numeric_suffix(key), key),
    )
    return fields or [question_id]


def _administrative_division_text(value: dict[str, Any]) -> str:
    divisions = administrative_divisions()
    province_code = str(value.get("province_code") or "")
    city_code = str(value.get("city_code") or "")
    province = next(
        (
            str(item.get("label") or "")
            for item in divisions.get("provinces") or []
            if str(item.get("code")) == province_code
        ),
        "",
    )
    city = next(
        (
            str(item.get("label") or "")
            for item in (divisions.get("citiesByProvince") or {}).get(province_code, [])
            if str(item.get("code")) == city_code
        ),
        "",
    )
    return " ".join(filter(None, (province, city)))


def _option_label(question: dict[str, Any], code: Any) -> str:
    return next(
        (
            _sanitize_text(option.get("label"))
            for option in question.get("options") or []
            if str(option.get("code")) == str(code)
        ),
        "",
    )


def _display_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f"{value:,.10g}"
    text = _sanitize_text(value)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        try:
            number = float(text)
            return f"{number:,.10g}"
        except ValueError:
            pass
    return text


def _display_amount(value: float, unit: str) -> str:
    rounded = f"{round(value):,}"
    if unit == "yuan_month":
        return f"{rounded}元/月"
    if unit == "yuan_year":
        return f"{rounded}元/年"
    if unit == "yuan":
        return f"{rounded}元"
    if unit == "ratio":
        return f"{max(0, round(value * 100))}%"
    if unit == "score_10":
        return f"{value / 10:.1f}/10"
    return str(value)


def _sanitize_text(value: Any) -> str:
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text).replace("```", "｀｀｀")
    text = text.replace("<", "＜").replace(">", "＞")
    return re.sub(r"\s+", " ", text).strip()[:800]


def _clean_question_name(value: str) -> str:
    text = re.sub(
        r"^(?:\d+(?:-\d+)*|[ABP]\d+(?:-\d+)*)[.、]\s*",
        "",
        _sanitize_text(value),
    )
    return text.replace("（填空）", "待填写位置")[:260]


def _question_context_id(question_id: str) -> str:
    return f"CTX-{question_id.replace('_', '-')}"


def _followup_context_id(parent_id: str, followup_id: str) -> str:
    clean_parent = re.sub(r"[^A-Za-z0-9-]", "-", parent_id)
    clean_followup = re.sub(r"[^A-Za-z0-9-]", "-", followup_id)
    return f"CTX-FOLLOWUP-{clean_parent}-{clean_followup}"


def _numeric_suffix(value: str) -> int:
    match = re.search(r"(\d+)$", value)
    return int(match.group(1)) if match else 0


def _number(value: Any) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _compact(value: Any) -> str:
    number = _number(value)
    return str(int(number)) if number.is_integer() else str(number)


def _region_label(region: dict[str, Any]) -> str:
    return str(region.get("city") or region.get("province") or region.get("raw") or "")

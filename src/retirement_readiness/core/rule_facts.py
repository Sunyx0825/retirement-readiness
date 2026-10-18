"""Build active report-rule facts from the current questionnaire only."""

from __future__ import annotations

import re
from typing import Any

from retirement_readiness.core.calculation_inputs import (
    calculation_answers,
    has_value,
    number,
)
from retirement_readiness.core.followup_lifecycle import debt_cleared_before_retirement
from retirement_readiness.core.numeric_answers import UNKNOWN, numeric_answer
from retirement_readiness.core.rule_catalog import RuleCatalog


def build_rule_facts(
    raw: dict[str, Any],
    followups: list[dict[str, Any]],
    calculations: dict[str, Any],
    catalog: RuleCatalog,
) -> dict[str, dict[str, Any]]:
    answers = calculation_answers(raw)
    facts = {
        fact_id: _fact(fact_id, "unknown", "本次信息不足，尚不能形成可靠判断。")
        for fact_id in catalog.fact_ids
    }

    def put(
        fact_id: str,
        state: bool | str,
        statement: str,
        fields: tuple[str, ...] = (),
        *,
        source: str = "questionnaire",
        display: str = "",
    ) -> None:
        if fact_id not in facts:
            return
        normalized = state if isinstance(state, str) else "true" if state else "false"
        facts[fact_id] = _fact(
            fact_id,
            normalized,
            statement,
            source=source,
            fields=fields,
            display=display,
        )

    def pair(
        positive_id: str,
        negative_id: str,
        known: bool,
        positive: bool,
        positive_statement: str,
        negative_statement: str,
        *fields: str,
    ) -> None:
        if not known:
            return
        put(positive_id, positive, positive_statement, tuple(fields))
        put(negative_id, not positive, negative_statement, tuple(fields))

    finance_known = calculations.get("finance_profile_status") == "available"
    pair(
        "finance_profile_known",
        "finance_profile_missing",
        True,
        finance_known,
        "资产、收入、债务和支出信息足以支持完整财务判断。",
        "资产、收入、债务或支出信息尚不完整。",
        "Q27",
        "Q28",
        "Q29",
        "Q30",
    )
    for gap_id, adequate_id, key, label, resource in (
        (
            "basic_gap",
            "basic_adequate",
            "basic_living",
            "基本生活充足程度",
            "纯养老金收入",
        ),
        (
            "routine_gap",
            "routine_adequate",
            "basic_living_plus_routine_medical",
            "慢病风险充足程度",
            "纯养老金收入",
        ),
        (
            "major_gap",
            "major_adequate",
            "basic_living_plus_major_medical",
            "重疾风险充足程度",
            "养老金和可动用资产",
        ),
        (
            "care_funding_gap",
            "care_funding_adequate",
            "basic_living_plus_care",
            "失能风险充足程度",
            "养老金和可动用资产",
        ),
    ):
        ratio = number(calculations["financial_indices"].get(key))
        display = f"{round(max(0, ratio) * 100)}%"
        pair(
            gap_id,
            adequate_id,
            finance_known,
            ratio < 1,
            f"{label}为{display}，{resource}尚不能覆盖该情景。",
            f"{label}为{display}，{resource}可以覆盖该情景。",
            f"calculations.financial_indices.{key}",
        )
        for fact_id in (gap_id, adequate_id):
            facts[fact_id]["displayValue"] = display

    monthly_income = sum(number(value) for value in answers["Q28"].values()) / 12
    monthly_expense = sum(number(value) for value in answers["Q30"].values())
    cashflow_known = _numeric_fields_known(raw, "Q28", 6) and _numeric_fields_known(raw, "Q30", 10)
    if cashflow_known:
        gap = monthly_income - monthly_expense
        put("cashflow_gap", gap < 0, "当前家庭月支出高于月收入。", ("Q28", "Q30"))
        put("cashflow_surplus", gap > 0, "当前家庭月收入高于月支出。", ("Q28", "Q30"))
        put(
            "cashflow_balanced",
            gap == 0,
            "当前家庭月收入与月支出大致平衡。",
            ("Q28", "Q30"),
        )
        put(
            "basic_and_cashflow_adequate",
            gap > 0 and calculations["financial_indices"]["basic_living"] >= 1,
            "当前现金流和退休基本生活覆盖均达到测算标准。",
            ("Q28", "Q30"),
            source="calculation",
        )
    marital = answers.get("Q4")
    pair(
        "married",
        "unmarried",
        has_value(marital),
        marital == "married",
        "当前婚姻状态为已婚。",
        "当前婚姻状态不是已婚。",
        "Q4",
    )
    put(
        "marital_unknown",
        not has_value(marital),
        "婚姻状态尚不明确。",
        ("Q4",),
    )
    for code, label in (
        ("fulltime", "全职工作"),
        ("flexible", "灵活就业"),
        ("unemployed", "未就业"),
    ):
        put(
            "employment_" + code,
            answers.get("Q8") == code,
            f"当前就业状态为{label}。",
            ("Q8",),
        )
    put(
        "employment_unknown",
        not has_value(answers.get("Q8")),
        "当前就业状态尚不明确。",
        ("Q8",),
    )
    put(
        "income_volatility_high_or_medium",
        answers.get("Q8") == "flexible",
        "当前就业形态可能伴随收入波动。",
        ("Q8",),
    )
    _financial_facts(answers, raw, put, pair)
    _pension_facts(answers, raw, put, pair)
    _medical_and_health_facts(answers, put, pair)
    _planning_facts(answers, calculations, put, pair)
    _care_facts(answers, followups, put, pair)
    _followup_facts(followups, put, pair)
    debt_timing = debt_cleared_before_retirement(raw, followups)
    pair(
        "debt_before_retirement",
        "debt_after_retirement",
        debt_timing is not None,
        debt_timing is True,
        "主要债务计划在退休前还清。",
        "主要债务将延续到退休后。",
        "followups",
    )
    if debt_timing is not None:
        put("debt_schedule_unknown", False, "债务结清时点已有补充回答。", ("followups",))
    for fact_id, key in (
        ("basic_pension_coverage_known", "basic_living"),
        ("routine_pension_coverage_known", "basic_living_plus_routine_medical"),
    ):
        put(
            fact_id,
            calculations["financial_indices"].get(key) is not None,
            "养老金覆盖具备计算条件，不代表全部储备充足。",
            (f"calculations.financial_indices.{key}",),
            source="calculation",
        )
    for fact_id, field, code in (
        ("retirement_city_not_considered", "Q34", "not_considered"),
        ("retirement_city_direction_undecided", "Q34", "direction_undecided"),
        ("care_not_considered_due_age", "Q47", "not_considered_age"),
        ("care_assumed_family_without_discussion", "Q47", "not_discussed"),
    ):
        put(
            fact_id,
            answers.get(field) == code if has_value(answers.get(field)) else "unknown",
            "按当前问卷自报原因解释，不推断当前紧迫风险。",
            (field,),
        )
    knowledge = raw.get("LTC_KNOWLEDGE")
    for fact_id, field, allowed in (
        (
            "public_ltc_knowledge_response",
            "LTC_KNOWLEDGE",
            {"none", "heard", "some", "clear"},
        ),
        (
            "public_ltc_status_response",
            "LTC_STATUS",
            {"enrolled", "not_enrolled", "not_implemented"},
        ),
    ):
        state = "true" if raw.get(field) in allowed else "unknown"
        if field == "LTC_STATUS" and knowledge not in {"heard", "some", "clear"}:
            state = "hidden"
        put(
            fact_id,
            state,
            "公共长护险为自报信息；地区实施、待遇资格与金额须官方核验，不自动抵扣照护费用。",
            (field,),
            display=str(raw.get(field) or ""),
        )
    for fact_id in (
        "asset_use_unknown",
        "medical_support_need_unknown",
        "pension_contribution_self_paid_unknown",
    ):
        if facts[fact_id]["state"] == "unknown":
            put(fact_id, True, "本次未取得相应补充信息。", ("followups",))
    return facts


def _numeric_fields_known(raw: dict[str, Any], question: str, count: int | tuple[int, ...]) -> bool:
    return all(
        numeric_answer(raw.get(f"{question}__blank_{index}"))[0] in {"known", "missing"}
        for index in (range(1, count + 1) if isinstance(count, int) else count)
    )


def _financial_facts(answers, raw, put, pair):
    assets = answers["Q27"]
    liquid = sum(
        number(assets.get(key))
        for key in (
            "cash_current_wan",
            "time_deposit_wan",
            "stock_wan",
            "fund_wan",
            "bond_wan",
            "insurance_wan",
        )
    )
    accessible = liquid
    liquid = number(assets.get("cash_current_wan")) + number(assets.get("time_deposit_wan"))
    market = number(assets.get("stock_wan")) + number(assets.get("fund_wan"))
    for positive_id, none_id, unknown_id, value, label, fields in (
        (
            "liquid_assets_positive",
            "liquid_assets_none",
            "liquid_assets_unknown",
            liquid,
            "金融资产",
            (1, 2),
        ),
        (
            "accessible_financial_assets_positive",
            "accessible_financial_assets_none",
            "accessible_financial_assets_unknown",
            accessible,
            "可用金融资产",
            (1, 2, 3, 4, 5),
        ),
        (
            "market_assets_positive",
            "market_assets_none",
            "market_assets_unknown",
            market,
            "股票和基金",
            (3, 4),
        ),
        (
            "stock_assets_positive",
            "stock_assets_none",
            "stock_assets_unknown",
            number(assets.get("stock_wan")),
            "股票资产",
            (3,),
        ),
        (
            "time_deposit_positive",
            "time_deposit_none",
            "time_deposit_unknown",
            number(assets.get("time_deposit_wan")),
            "定期存款",
            (2,),
        ),
    ):
        known = _numeric_fields_known(raw, "Q27", fields)
        pair(
            positive_id,
            none_id,
            known,
            value > 0,
            f"家庭已有{label}。",
            f"家庭未填写正数{label}。",
            "Q27",
        )
        put(unknown_id, not known, f"{label}情况尚不明确。", ("Q27",))
    pair(
        "assets_known",
        "assets_unknown",
        True,
        _numeric_fields_known(raw, "Q27", (1, 2, 3, 4, 5, 7, 8, 9, 10)),
        "家庭资产信息完整。",
        "家庭资产信息不完整。",
        "Q27",
    )
    pair(
        "property_positive",
        "property_none",
        _numeric_fields_known(raw, "Q27", (7, 8)),
        number(assets.get("property_count")) > 0,
        "家庭持有房产。",
        "家庭未填写房产。",
        "Q27",
    )
    income_known = _numeric_fields_known(raw, "Q28", 6)
    income = sum(number(value) for value in answers["Q28"].values())
    pair(
        "income_positive",
        "income_zero",
        income_known,
        income > 0,
        "家庭年收入为正数。",
        "家庭各项年收入均为零。",
        "Q28",
    )
    put("income_unknown", not income_known, "家庭收入信息尚不完整。", ("Q28",))
    expenses_known = _numeric_fields_known(raw, "Q30", 10)
    put("expenses_known", expenses_known, "家庭月支出分项已填写。", ("Q30",))
    child_education = number(answers["Q30"].get("child_education"))
    pair(
        "child_education_positive",
        "child_education_none",
        expenses_known,
        child_education > 0,
        "当前存在子女教育支出。",
        "当前没有子女教育支出。",
        "Q30",
    )
    put(
        "child_education_unknown",
        not expenses_known,
        "子女教育支出尚不明确。",
        ("Q30",),
    )
    debt_known = has_value(answers.get("Q29"))
    debt_yes = answers.get("Q29") == "yes"
    pair(
        "debt_yes",
        "debt_no",
        debt_known,
        debt_yes,
        "家庭有贷款或借款。",
        "家庭明确没有贷款或借款。",
        "Q29",
    )
    put("debt_unknown", not debt_known, "家庭债务情况尚不明确。", ("Q29",))
    debt_values = answers["Q29_1"]
    positives = [number(value) > 0 for value in debt_values.values()]
    if debt_yes:
        put(
            "debt_categories_none",
            not any(positives),
            "各类债务余额均未填写正数。",
            ("Q29_1",),
        )
        put(
            "single_debt_category_positive",
            sum(positives) == 1,
            "当前只有一类债务余额为正。",
            ("Q29_1",),
        )
        put(
            "multiple_debt_categories_positive",
            sum(positives) > 1,
            "当前有多类债务余额为正。",
            ("Q29_1",),
        )
        put("mortgage_debt_positive", positives[0], "当前有房屋贷款余额。", ("Q29_1",))
        put("mortgage_debt_none", not positives[0], "当前没有房屋贷款余额。", ("Q29_1",))
    debt_payment = number(answers["Q30"].get("debt_repayment"))
    pair(
        "debt_payment_positive",
        "debt_payment_none",
        expenses_known,
        debt_payment > 0,
        "当前每月有贷款还款支出。",
        "当前每月没有贷款还款支出。",
        "Q30",
    )
    put("debt_payment_unknown", not expenses_known, "月还款支出尚不明确。", ("Q30",))
    put("debt_schedule_unknown", debt_yes, "债务是否能在退休前还清尚需确认。", ("Q29",))


def _pension_facts(answers, raw, put, pair):
    codes = answers.get("Q9") if isinstance(answers.get("Q9"), list) else []
    known = bool(codes)
    specific = bool({"employee_pension", "resident_pension"}.intersection(codes))
    none = "none" in codes
    uncertain = not known or "not_sure" in codes or (none and specific)
    put(
        "pension_participation_yes",
        "unknown" if uncertain else specific,
        "本人已参加基本养老保险。",
        ("Q9",),
    )
    put(
        "pension_participation_no",
        "unknown" if uncertain else none,
        "本人明确未参加基本养老保险。",
        ("Q9",),
    )
    put(
        "pension_not_insured",
        "unknown" if uncertain else none,
        "本人明确未参加基本养老保险。",
        ("Q9",),
    )
    put(
        "pension_participation_unknown",
        uncertain,
        "本人基本养老参保状态不明确。",
        ("Q9",),
    )
    put(
        "pension_type_unknown",
        uncertain,
        "本人基本养老保险类型不明确。",
        ("Q9",),
    )
    put(
        "employee_pension_yes",
        "unknown" if uncertain else "employee_pension" in codes,
        "本人参加职工基本养老保险。",
        ("Q9",),
    )
    put(
        "employee_pension_no",
        "unknown" if uncertain else "employee_pension" not in codes,
        "本人未参加职工基本养老保险。",
        ("Q9",),
    )
    put(
        "employee_pension_unknown",
        uncertain,
        "本人职工养老参保状态不明确。",
        ("Q9",),
    )
    put(
        "enterprise_annuity_only",
        known and not specific and "combined_annuity" in codes,
        "本人只填写了企业年金或职业年金。",
        ("Q9",),
    )
    # Full-time history does not collect locations, so one known city cannot prove a single-region history.
    known_regions = {
        str(row.get("city_code") or row.get("province_code"))
        for row in raw.get("PENSION_HISTORY") or []
        if row.get("province_code")
    }
    put(
        "multi_region_yes",
        specific and len(known_regions) > 1,
        "已填经历涉及多个明确参保地。",
        ("PENSION_HISTORY",),
    )
    put(
        "multi_region_no",
        "unknown",
        "现有经历不足以确认仅在一个地区参保。",
        ("PENSION_HISTORY",),
    )
    put(
        "multi_region_unknown",
        specific and len(known_regions) < 2,
        "是否多地参保尚不明确。",
        ("PENSION_HISTORY",),
    )
    benefit = answers.get("PENSION_BENEFIT_CITY")
    put(
        "benefit_city_known",
        specific and has_value(benefit) and benefit != "not_sure",
        "养老金待遇领取地已有明确方向。",
        ("PENSION_BENEFIT_CITY",),
    )
    put(
        "benefit_city_explicit_unknown",
        benefit == "not_sure",
        "本人明确表示待遇领取地尚未确定。",
        ("PENSION_BENEFIT_CITY",),
    )
    put(
        "benefit_city_answer_missing_or_conflict",
        specific and not has_value(benefit),
        "养老金待遇领取地答案缺失。",
        ("PENSION_BENEFIT_CITY",),
    )
    pair(
        "personal_pension_yes",
        "personal_pension_contribution_none",
        specific and has_value(answers.get("Q10")),
        answers.get("Q10") == "yes",
        "本人已参加个人养老金。",
        "本人未参加个人养老金。",
        "Q10",
    )
    contribution = str(answers.get("Q10_1") or "")
    put(
        "personal_pension_contribution_positive",
        answers.get("Q10") == "yes" and contribution not in {"", "none", "not_sure"},
        "个人养老金已有正数缴存档位。",
        ("Q10_1",),
    )
    put(
        "personal_pension_contribution_unknown",
        answers.get("Q10") == "yes" and contribution in {"", "not_sure"},
        "个人养老金缴存金额尚不明确。",
        ("Q10_1",),
    )
    commercial = answers.get("Q11")
    pair(
        "commercial_pension_yes",
        "commercial_pension_no",
        has_value(commercial),
        commercial in {"annuity", "savings", "both"},
        "本人已配置商业养老年金或储蓄型保险。",
        "本人未配置商业养老产品。",
        "Q11",
    )
    put(
        "commercial_pension_unknown",
        not has_value(commercial),
        "商业养老产品配置尚不明确。",
        ("Q11",),
    )
    put(
        "commercial_savings_only",
        commercial == "savings",
        "现有商业养老安排只有储蓄型保险。",
        ("Q11",),
    )
    q13 = answers.get("Q13") if isinstance(answers.get("Q13"), dict) else {}
    amount_known = (
        q13.get("knowledge") in {"clear", "very_clear"} and number(q13.get("amount_yuan")) > 0
    )
    pair(
        "pension_amount_known",
        "pension_amount_unknown",
        bool(q13),
        amount_known,
        "本人已填写明确的退休养老金月金额。",
        "本人尚不清楚退休养老金月金额。",
        "Q13",
    )
    put(
        "pension_income_estimation_low",
        q13.get("knowledge") in {"unknown", "unclear", "general"},
        "本人对退休养老金收入估计较弱。",
        ("Q13",),
    )
    put(
        "pension_income_estimation_unknown",
        not bool(q13),
        "养老金收入估计题尚未回答。",
        ("Q13",),
    )
    spouse_codes = raw.get("P9") if isinstance(raw.get("P9"), list) else []
    spouse_state = (
        "hidden" if answers.get("Q4") != "married" else "true" if spouse_codes else "unknown"
    )
    spouse_specific = bool({"employee_pension", "resident_pension"}.intersection(spouse_codes))
    spouse_none = "none" in spouse_codes
    spouse_uncertain = (
        spouse_state != "true" or "not_sure" in spouse_codes or (spouse_none and spouse_specific)
    )
    put(
        "spouse_basic_pension_yes",
        spouse_state if spouse_uncertain else spouse_specific,
        "配偶已参加基本养老保险。",
        ("P9",),
    )
    put(
        "spouse_basic_pension_no",
        spouse_state if spouse_uncertain else spouse_none,
        "配偶明确未参加基本养老保险。",
        ("P9",),
    )
    put(
        "spouse_basic_pension_unknown",
        spouse_state if spouse_state in {"hidden", "unknown"} else spouse_uncertain,
        "配偶基本养老参保状态不明确。",
        ("P9",),
    )


def _medical_and_health_facts(answers, put, pair):
    medical = answers.get("Q31")
    medical_known = has_value(medical)
    put(
        "basic_medical_yes",
        "unknown" if not medical_known else medical not in {"none", "not_sure"},
        "本人有基本医疗保障。",
        ("Q31",),
    )
    put(
        "basic_medical_no",
        "unknown" if not medical_known else medical == "none",
        "本人明确没有基本医疗保障。",
        ("Q31",),
    )
    put(
        "basic_medical_employee_or_resident",
        medical in {"employee_medical", "resident_medical"},
        "本人参加职工或居民基本医保。",
        ("Q31",),
    )
    put(
        "basic_medical_public",
        medical == "public_medical",
        "本人享有公费医疗。",
        ("Q31",),
    )
    put(
        "basic_medical_explicit_unknown",
        medical == "not_sure",
        "本人不清楚基本医保类型。",
        ("Q31",),
    )
    put(
        "basic_medical_unknown",
        not has_value(medical) or medical == "not_sure",
        "基本医保情况尚不明确。",
        ("Q31",),
    )
    put(
        "basic_medical_answer_missing_or_conflict",
        not medical_known,
        "基本医疗保险答案缺失或存在冲突。",
        ("Q31",),
    )
    spouse_medical = answers.get("P9_5")
    spouse_medical_state = (
        "hidden"
        if answers.get("Q4") != "married"
        else "true"
        if has_value(spouse_medical)
        else "unknown"
    )
    put(
        "spouse_basic_medical_yes",
        spouse_medical_state
        if spouse_medical_state != "true"
        else spouse_medical not in {"none", "not_sure"},
        "配偶已有明确的基本医疗保障。",
        ("P9_5",),
    )
    put(
        "spouse_basic_medical_no",
        spouse_medical_state if spouse_medical_state != "true" else spouse_medical == "none",
        "配偶明确未参加基本医疗保险。",
        ("P9_5",),
    )
    put(
        "spouse_basic_medical_unknown",
        spouse_medical_state if spouse_medical_state != "true" else spouse_medical == "not_sure",
        "配偶基本医疗保险状态不明确。",
        ("P9_5",),
    )
    health_insurance = answers.get("Q32") if isinstance(answers.get("Q32"), list) else []
    health_known = bool(health_insurance)
    commercial = "commercial_medical" in health_insurance
    critical = "critical_illness" in health_insurance
    ltc = "long_term_care" in health_insurance
    accident = "accident" in health_insurance
    pair(
        "commercial_health_yes",
        "commercial_health_no",
        health_known,
        bool(set(health_insurance) - {"none"}),
        "本人配置了商业健康险。",
        "本人未配置商业健康险。",
        "Q32",
    )
    put(
        "commercial_health_unknown",
        not health_known,
        "商业健康险配置尚不明确。",
        ("Q32",),
    )
    pair(
        "commercial_medical_yes",
        "commercial_medical_no",
        health_known,
        commercial,
        "本人配置了商业医疗险。",
        "本人未配置商业医疗险。",
        "Q32",
    )
    pair(
        "critical_illness_yes",
        "critical_illness_no",
        health_known,
        critical,
        "本人配置了重疾险。",
        "本人未配置重疾险。",
        "Q32",
    )
    pair(
        "commercial_ltc_yes",
        "commercial_ltc_no",
        health_known,
        ltc,
        "本人配置了长期护理险。",
        "本人未配置长期护理险。",
        "Q32",
    )
    put("accident_yes", health_known and accident, "本人持有意外伤害保障。", ("Q32",))
    put(
        "major_claim_cover_yes",
        health_known and (commercial or critical),
        "已有商业医疗险或重大疾病保险可按合同申请理赔。",
        ("Q32",),
    )
    payment_states = (
        answers.get("Q31") not in {None, "", "none", "not_sure"},
        health_known and bool(set(health_insurance) - {"none"}),
        number(answers["Q27"].get("cash_current_wan"))
        + number(answers["Q27"].get("time_deposit_wan"))
        + number(answers["Q27"].get("stock_wan"))
        + number(answers["Q27"].get("fund_wan"))
        + number(answers["Q27"].get("bond_wan"))
        + number(answers["Q27"].get("insurance_wan"))
        > 0,
    )
    payment_known = medical_known and health_known
    put(
        "major_payment_source_present",
        any(payment_states),
        "已确认至少一种大额医疗支付来源。",
        ("Q31", "Q32", "Q27"),
    )
    put(
        "major_payment_source_none",
        payment_known and not any(payment_states),
        "已确认的基本医保、商业健康险和金融资产均未形成大额医疗支付来源。",
        ("Q31", "Q32", "Q27"),
    )
    put(
        "major_payment_source_unknown",
        not payment_known and not any(payment_states),
        "大额医疗支付来源尚不完整。",
        ("Q31", "Q32", "Q27"),
    )
    issues = answers.get("Q33") if isinstance(answers.get("Q33"), list) else []
    issue_map = {
        "medical_procedure_problem": "procedure_unclear",
        "medical_eligibility_problem": "eligibility_unclear",
        "medical_cross_region_problem": "cross_region_problem",
        "medical_catalog_problem": "catalog_unclear",
        "medical_hassle_problem": "gave_up_due_hassle",
    }
    for fact_id, code in issue_map.items():
        put(fact_id, code in issues, "本人曾遇到医保使用或就医流程问题。", ("Q33",))
    has_problem = bool(set(issue_map.values()).intersection(issues))
    pair(
        "medical_path_problem",
        "medical_path_normal",
        bool(issues),
        has_problem,
        "本人曾遇到医保使用路径问题。",
        "本人未遇到医保使用路径问题。",
        "Q33",
    )
    put("medical_path_unknown", not bool(issues), "医保使用路径情况尚不明确。", ("Q33",))
    put("medical_process_problem", has_problem, "就医或报销流程存在实际障碍。", ("Q33",))
    chronic = answers.get("Q35") if isinstance(answers.get("Q35"), list) else []
    chronic_yes = bool(chronic) and "none" not in chronic
    pair(
        "chronic_yes",
        "chronic_no",
        bool(chronic),
        chronic_yes,
        "本人有医生明确诊断的慢性疾病。",
        "本人未填写慢性疾病。",
        "Q35",
    )
    put("chronic_unknown", not bool(chronic), "慢性疾病情况尚不明确。", ("Q35",))
    for fact_id, code in (
        ("chronic_hypertension", "hypertension"),
        ("chronic_diabetes", "diabetes"),
        ("chronic_hyperlipidemia", "hyperlipidemia"),
    ):
        put(fact_id, code in chronic, "本人已确诊相关慢性疾病。", ("Q35",))
    severe = bool({"coronary_heart", "cancer"}.intersection(chronic))
    pair(
        "chronic_coronary_or_cancer",
        "chronic_not_coronary_or_cancer",
        bool(chronic),
        severe,
        "本人有冠心病或癌症诊断。",
        "本人未填写冠心病或癌症诊断。",
        "Q35",
    )
    hd = bool({"hypertension", "diabetes"}.intersection(chronic))
    pair(
        "chronic_hypertension_or_diabetes",
        "chronic_not_hypertension_or_diabetes",
        bool(chronic),
        hd,
        "本人有高血压或糖尿病诊断。",
        "本人未填写高血压或糖尿病诊断。",
        "Q35",
    )
    hospital = answers.get("Q36")
    pair(
        "hospital_yes",
        "hospital_no",
        has_value(hospital),
        hospital in {"1", "gte_2"},
        "本人过去12个月有住院经历。",
        "本人过去12个月没有住院。",
        "Q36",
    )
    put(
        "hospital_repeat",
        hospital == "gte_2",
        "本人过去12个月住院两次及以上。",
        ("Q36",),
    )
    put("hospital_unknown", not has_value(hospital), "住院情况尚不明确。", ("Q36",))
    _lifestyle_facts(answers, put, pair)


def _lifestyle_facts(answers, put, pair):
    smoke = answers.get("Q37")
    pair(
        "smoking_current",
        "smoking_not_current",
        has_value(smoke),
        smoke in {"monthly_lt_10", "daily_lt_10", "daily_gte_10"},
        "本人目前仍在吸烟。",
        "本人目前不吸烟。",
        "Q37",
    )
    for fact_id, code in (
        ("smoking_never", "never"),
        ("smoking_quit", "quit"),
        ("smoking_monthly_lt_10", "monthly_lt_10"),
        ("smoking_daily_lt_10", "daily_lt_10"),
        ("smoking_daily_gte_10", "daily_gte_10"),
    ):
        put(fact_id, smoke == code, "吸烟频率已由问卷确认。", ("Q37",))
    put("smoking_unknown", not has_value(smoke), "吸烟状态尚不明确。", ("Q37",))
    alcohol = answers.get("Q38")
    pair(
        "alcohol_weekly_gte_4",
        "alcohol_not_frequent",
        has_value(alcohol),
        alcohol == "weekly_gte_4",
        "本人每周饮酒四次及以上。",
        "本人没有频繁饮酒。",
        "Q38",
    )
    put("alcohol_unknown", not has_value(alcohol), "饮酒频率尚不明确。", ("Q38",))
    exercise = answers.get("Q39")
    put(
        "exercise_none",
        exercise == "no",
        "本人近三个月没有每周中等强度活动。",
        ("Q39",),
    )
    put(
        "exercise_below_150",
        exercise == "yes_lt_150",
        "本人每周运动量低于150分钟。",
        ("Q39",),
    )
    put(
        "exercise_at_least_150",
        exercise == "yes_gte_150",
        "本人每周运动量达到150分钟。",
        ("Q39",),
    )
    put(
        "exercise_low",
        exercise in {"no", "yes_lt_150"},
        "本人运动量未达到建议水平。",
        ("Q39",),
    )
    put(
        "exercise_adequate",
        exercise == "yes_gte_150",
        "本人运动量达到问卷设定标准。",
        ("Q39",),
    )
    put("exercise_unknown", not has_value(exercise), "运动情况尚不明确。", ("Q39",))
    chronic = answers.get("Q35") if isinstance(answers.get("Q35"), list) else []
    guidance = exercise in {"no", "yes_lt_150"} and bool(set(chronic).difference({"none"}))
    put(
        "exercise_medical_guidance_needed",
        guidance,
        "改善运动前需要结合慢病情况获得专业指导。",
        ("Q35", "Q39"),
    )
    put(
        "exercise_medical_guidance_not_needed",
        has_value(exercise) and not guidance,
        "当前没有形成运动前医疗指导的强制信号。",
        ("Q35", "Q39"),
    )
    checkup = answers.get("Q40")
    put("checkup_never", checkup == "never", "本人从不进行全面体检。", ("Q40",))
    put(
        "checkup_rare",
        checkup in {"never", "rare_no_regular"},
        "本人很少体检且没有固定频率。",
        ("Q40",),
    )
    put(
        "checkup_every_2_3y",
        checkup == "every_2_3y",
        "本人每两到三年体检一次。",
        ("Q40",),
    )
    put(
        "checkup_yearly",
        checkup in {"yearly", "half_yearly"},
        "本人至少每年进行一次体检。",
        ("Q40",),
    )
    put(
        "checkup_regular",
        checkup in {"yearly", "half_yearly"},
        "本人有相对规律的体检安排。",
        ("Q40",),
    )
    put("checkup_unknown", not has_value(checkup), "体检频率尚不明确。", ("Q40",))
    monitor = answers.get("Q41")
    pair(
        "monitoring_regular",
        "monitoring_low",
        has_value(monitor),
        monitor in {"weekly_recorded", "monthly_or_remember"},
        "本人会相对规律地监测健康指标。",
        "本人健康指标监测频率较低。",
        "Q41",
    )
    put(
        "monitoring_unknown",
        not has_value(monitor),
        "健康指标监测情况尚不明确。",
        ("Q41",),
    )
    diet = answers.get("Q42")
    put("diet_none", diet == "0", "本人过去一周没有主动进行均衡饮食搭配。", ("Q42",))
    put("diet_low", diet in {"0", "1_2"}, "本人均衡饮食天数较少。", ("Q42",))
    put("diet_partial", diet == "3_4", "本人每周有三到四天注意均衡饮食。", ("Q42",))
    put("diet_regular", diet in {"5_6", "7"}, "本人多数日期注意均衡饮食。", ("Q42",))
    put("diet_adequate", diet == "7", "本人每天都注意均衡饮食。", ("Q42",))
    put("diet_unknown", not has_value(diet), "饮食情况尚不明确。", ("Q42",))


def _planning_facts(answers, calculations, put, pair):
    scores = calculations["scores"]
    for low_id, ready_id, key, label in (
        ("cognition_low", "cognition_ready", "financial_cognition", "财务认知"),
        ("health_low", "health_ready", "health_capital", "健康资本"),
        ("activity_low", "activity_planned", "activity_social", "活动社交"),
        ("care_plan_low", "care_plan_ready", "care_planning", "照护规划"),
    ):
        value = number(scores.get(key))
        pair(
            low_id,
            ready_id,
            True,
            value < 60,
            f"{label}得分为{round(value)}分，低于60分。",
            f"{label}得分为{round(value)}分。",
            f"calculations.scores.{key}",
        )
    retirement_city = answers.get("Q34")
    city_known = has_value(retirement_city)
    city_choice = (
        str(retirement_city.get("choice") or "")
        if isinstance(retirement_city, dict)
        else str(retirement_city or "")
    )
    put(
        "retirement_city_unplanned",
        "unknown" if not city_known else city_choice in {"not_considered", "direction_undecided"},
        "退休后的定居城市尚未确定。",
        ("Q34",),
    )
    put(
        "retirement_city_known",
        "unknown"
        if not city_known
        else city_choice in {"current_city", "hukou_or_hometown", "other_city"},
        "退休后的养老地点已有明确方向。",
        ("Q34",),
    )
    put(
        "retirement_city_unknown",
        "unknown" if not city_known else False,
        "退休后的养老地点尚不明确。",
        ("Q34",),
    )
    put(
        "retirement_city_answer_unknown",
        "unknown" if not city_known else False,
        "养老地点答案缺失或冲突。",
        ("Q34",),
    )
    pension_policy = answers.get("Q12") if isinstance(answers.get("Q12"), list) else []
    sufficient = (
        len(
            set(pension_policy).intersection(
                {
                    "min_years",
                    "account_query",
                    "interruption_effect",
                    "benefit_calculation",
                    "inheritance_or_withdraw",
                }
            )
        )
        >= 3
    )
    pair(
        "pension_policy_knowledge_sufficient",
        "pension_policy_knowledge_gap",
        bool(pension_policy),
        sufficient,
        "本人了解较多养老保险政策事项。",
        "本人养老保险政策知识仍有缺口。",
        "Q12",
    )
    put(
        "pension_policy_knowledge_unknown",
        not bool(pension_policy),
        "养老保险政策知识情况尚不明确。",
        ("Q12",),
    )
    q14 = answers.get("Q14") if isinstance(answers.get("Q14"), dict) else {}
    expense_known = (
        q14.get("estimation") in {"rough", "detailed"} and number(q14.get("amount_yuan")) > 0
    )
    pair(
        "retirement_expense_known",
        "retirement_expense_unknown",
        bool(q14),
        expense_known,
        "本人已估算退休后的月生活支出。",
        "本人尚未形成明确的退休月支出估算。",
        "Q14",
    )
    put(
        "retirement_expense_estimation_low",
        q14.get("estimation") in {"not_estimated", "rarely_considered", "occasionally"},
        "本人对退休支出的估算准备较弱。",
        ("Q14",),
    )
    put(
        "retirement_expense_estimation_unknown",
        not bool(q14),
        "退休支出估算题尚未回答。",
        ("Q14",),
    )
    reserve = answers.get("Q15")
    pair(
        "retirement_reserve_assessed",
        "retirement_reserve_not_assessed",
        has_value(reserve),
        reserve != "not_considered",
        "本人已评估退休储备充足程度。",
        "本人尚未评估退休储备。",
        "Q15",
    )
    put(
        "retirement_reserve_self_reported_insufficient",
        reserve in {"insufficient", "somewhat_insufficient"},
        "本人认为退休储备不足。",
        ("Q15",),
    )
    put(
        "retirement_reserve_not_insufficient",
        reserve in {"roughly_balanced", "rather_sufficient", "fully_sufficient"},
        "本人未将退休储备评价为不足。",
        ("Q15",),
    )
    put(
        "retirement_reserve_assessment_unknown",
        not has_value(reserve),
        "退休储备自评尚不明确。",
        ("Q15",),
    )
    saving = answers.get("Q16")
    put(
        "retirement_saving_willing",
        saving in {"probably_yes", "definitely_yes"},
        "本人愿意为退休持续预留收入。",
        ("Q16",),
    )
    put(
        "retirement_saving_unwilling",
        saving in {"definitely_no", "mostly_no"},
        "本人当前不愿专门预留退休资金。",
        ("Q16",),
    )
    put(
        "retirement_saving_uncertain",
        saving == "uncertain",
        "本人是否预留退休资金尚不确定。",
        ("Q16",),
    )
    put(
        "retirement_saving_not_unwilling",
        saving in {"uncertain", "probably_yes", "definitely_yes"},
        "本人没有明确拒绝为退休预留资金。",
        ("Q16",),
    )
    put(
        "retirement_saving_unknown",
        not has_value(saving),
        "退休储蓄意愿尚不明确。",
        ("Q16",),
    )
    personal_cost = answers.get("Q17")
    personal_cost_known = has_value(personal_cost)
    underestimated = personal_cost in {"no_personal", "very_little_personal"}
    put(
        "personal_cost_underestimate",
        "unknown" if not personal_cost_known else underestimated,
        "个人承担养老经济责任的预期偏低，可能带来准备不足风险。",
        ("Q17",),
    )
    put(
        "personal_cost_not_underestimate",
        "unknown" if not personal_cost_known else not underestimated,
        "本人已认识到个人需要承担相应养老经济责任。",
        ("Q17",),
    )
    put(
        "personal_cost_unknown",
        "unknown" if not personal_cost_known else False,
        "个人养老经济责任看法尚不明确。",
        ("Q17",),
    )
    priority = answers.get("Q18")
    pair(
        "retirement_plan_priority_high",
        "retirement_plan_priority_low",
        has_value(priority),
        priority in {"important", "very_important"},
        "本人重视退休财务规划。",
        "本人对退休财务规划的重视程度较低。",
        "Q18",
    )
    put(
        "retirement_plan_priority_unknown",
        not has_value(priority),
        "退休规划重要性评价尚不明确。",
        ("Q18",),
    )
    comprehension = answers.get("Q19")
    put(
        "finance_comprehension_low",
        comprehension in {"very_difficult", "difficult"},
        "本人理解退休财务问题存在困难。",
        ("Q19",),
    )
    put(
        "finance_comprehension_not_low",
        comprehension in {"neutral", "easy", "very_easy"},
        "本人未报告明显的退休财务理解困难。",
        ("Q19",),
    )
    put(
        "finance_comprehension_unclear",
        comprehension == "neutral",
        "本人对退休财务理解能力评价一般。",
        ("Q19",),
    )
    put(
        "finance_comprehension_unknown",
        not has_value(comprehension),
        "退休财务理解能力尚不明确。",
        ("Q19",),
    )
    completeness = answers.get("Q20")
    put(
        "retirement_plan_complete",
        completeness in {"complete", "very_complete"},
        "本人认为退休计划较完整。",
        ("Q20",),
    )
    put(
        "retirement_plan_incomplete",
        completeness in {"none", "not_complete", "average"},
        "本人退休计划尚不完整。",
        ("Q20",),
    )
    put(
        "retirement_plan_absent_or_incomplete",
        completeness in {"none", "not_complete", "average"},
        "本人退休计划缺失或不完整。",
        ("Q20",),
    )
    put(
        "retirement_plan_completeness_unknown",
        not has_value(completeness),
        "退休计划完整度尚不明确。",
        ("Q20",),
    )
    confidence = answers.get("Q21")
    pair(
        "retirement_plan_confidence_high",
        "retirement_plan_confidence_low",
        has_value(confidence),
        confidence in {"confident", "very_confident"},
        "本人对获得满意退休收入有信心。",
        "本人对退休收入的信心较低。",
        "Q21",
    )
    put(
        "retirement_plan_confidence_unknown",
        not has_value(confidence),
        "退休收入信心尚不明确。",
        ("Q21",),
    )
    put(
        "inflation_correct",
        answers.get("Q23") == "option_3",
        "已正确理解通胀会降低购买力。",
        ("Q23",),
    )
    put(
        "inflation_unknown",
        answers.get("Q23") in {None, "", "not_sure", "refuse"},
        "通胀知识尚不明确。",
        ("Q23",),
    )
    literacy = {
        "compound_interest_misconception": answers.get("Q22") in {"option_2", "option_3"},
        "inflation_misconception": answers.get("Q23") in {"option_1", "option_2"},
        "single_stock_misconception": answers.get("Q24") == "true",
        "mortgage_term_misconception": answers.get("Q25") == "false",
        "bond_price_misconception": answers.get("Q26") in {"down", "irrelevant"},
    }
    concepts = (
        "复利",
        "通胀与购买力",
        "单只股票与分散风险",
        "房贷期限与利息",
        "利率与债券价格",
    )
    for index, (fact_id, value) in enumerate(literacy.items(), 22):
        put(
            fact_id,
            value,
            f"金融素养题“{concepts[index - 22]}”存在明确错误判断。",
            (f"Q{index}",),
        )
    all_answered = all(has_value(answers.get(f"Q{i}")) for i in range(22, 27))
    put(
        "financial_literacy_unanswered_or_conflict",
        not all_answered,
        "金融素养题尚未完整回答。",
        ("Q22-Q26",),
    )
    uncertain = all_answered and any(
        answers.get(f"Q{i}") in {"not_sure", "refuse"} for i in range(22, 27)
    )
    no_error = all_answered and not uncertain and not any(literacy.values())
    put(
        "financial_literacy_all_correct",
        no_error,
        "五道金融知识题均回答正确。",
        ("Q22-Q26",),
    )
    put(
        "financial_literacy_no_misconception",
        no_error,
        "未发现问卷覆盖的金融知识误区。",
        ("Q22-Q26",),
    )
    put(
        "financial_literacy_uncertain",
        uncertain,
        "金融知识题中存在不确定回答。",
        ("Q22-Q26",),
    )
    put(
        "financial_literacy_unknown",
        not all_answered,
        "金融知识题未完整回答。",
        ("Q22-Q26",),
    )


def _care_facts(answers, followups, put, pair):
    activity = answers.get("Q43")
    put(
        "activity_unplanned",
        activity == "not_considered",
        "本人尚未考虑退休活动。",
        ("Q43",),
    )
    put("activity_vague", activity == "vague", "本人对退休活动只有模糊想法。", ("Q43",))
    put("activity_one", activity == "one_activity", "本人计划了一项退休活动。", ("Q43",))
    put(
        "activity_two_or_more",
        activity == "two_or_more",
        "本人计划了两项及以上退休活动。",
        ("Q43",),
    )
    put(
        "activity_planned",
        activity in {"one_activity", "two_or_more"},
        "本人已有明确退休活动计划。",
        ("Q43",),
    )
    put(
        "activity_answer_unknown",
        not has_value(activity),
        "退休活动计划尚不明确。",
        ("Q43",),
    )
    details = answers.get("Q43_1") if isinstance(answers.get("Q43_1"), list) else []
    known_count = 0
    for fact_id, missing_id, code, label in (
        ("activity_time_known", "activity_time_missing", "time", "时间"),
        ("activity_channel_known", "activity_channel_missing", "channel", "渠道"),
        ("activity_partner_known", "activity_partner_missing", "partner", "同伴"),
        ("activity_cost_known", "activity_cost_missing", "cost", "费用"),
    ):
        selected = code in details
        known_count += int(selected)
        pair(
            fact_id,
            missing_id,
            bool(details),
            selected,
            f"退休活动的{label}安排已有准备。",
            f"退休活动的{label}安排尚未准备。",
            "Q43_1",
        )
    put(
        "activity_details_complete",
        known_count == 4,
        "退休活动的时间、渠道、同伴和费用均有准备。",
        ("Q43_1",),
    )
    put(
        "activity_details_partial",
        0 < known_count < 4,
        "退休活动准备覆盖了部分要素。",
        ("Q43_1",),
    )
    put(
        "activity_details_missing",
        bool(details) and known_count == 0,
        "退休活动的执行要素尚未准备。",
        ("Q43_1",),
    )
    put(
        "activity_details_unknown",
        not bool(details),
        "退休活动准备细节尚不明确。",
        ("Q43_1",),
    )
    family = answers.get("KIN_SUPPORT")
    for fact_id in (
        "family_contact_regular",
        "family_contact_weak",
        "family_contact_unknown",
    ):
        put(fact_id, "hidden", "当前问卷未采集亲属联系频率。", ("KIN_SUPPORT",))
    for code, fact_id in (
        ("0", "kin_support_none"),
        ("1_2", "kin_support_one_two"),
        ("gte_3", "kin_support_strong"),
    ):
        put(
            fact_id,
            family == code if has_value(family) else "unknown",
            "亲属实际支持人数按本次自报解释，不代表已承诺照护。",
            ("KIN_SUPPORT",),
        )
    put(
        "kin_support_unknown",
        not has_value(family),
        "亲属实际支持人数尚不明确。",
        ("KIN_SUPPORT",),
    )
    nonkin = answers.get("Q45")
    put("nonkin_support_none", nonkin == "0", "本人没有可依靠的非亲属支持者。", ("Q45",))
    put(
        "nonkin_support_one_two",
        nonkin == "1_2",
        "本人有一到两位可依靠的非亲属支持者。",
        ("Q45",),
    )
    put(
        "nonkin_support_strong",
        nonkin == "gte_3",
        "本人有三位及以上可依靠的非亲属支持者。",
        ("Q45",),
    )
    put("nonkin_support_weak", nonkin in {"0", "1_2"}, "非亲属支持网络较弱。", ("Q45",))
    put(
        "nonkin_support_unknown",
        not has_value(nonkin),
        "非亲属支持网络尚不明确。",
        ("Q45",),
    )
    put(
        "social_support_strong",
        family == "gte_3" and nonkin == "gte_3",
        "家庭和非亲属支持网络较强。",
        ("KIN_SUPPORT", "Q45"),
    )
    put(
        "social_support_weak",
        family in {"0", "1_2"} or nonkin in {"0", "1_2"},
        "家庭或非亲属支持网络存在薄弱环节。",
        ("KIN_SUPPORT", "Q45"),
    )
    care = answers.get("Q46")
    mode_map = {
        "family_care": "care_mode_family",
        "home_hired_care": "care_mode_home",
        "institution_care": "care_mode_institution",
        "community_care": "care_mode_community",
        "not_considered": "care_mode_not_considered",
    }
    for code, fact_id in mode_map.items():
        put(fact_id, care == code, "本人已确认相应失能照护意向。", ("Q46",))
    put(
        "care_mode_answer_unknown",
        not has_value(care),
        "失能照护方式尚不明确。",
        ("Q46",),
    )
    resources = {
        "institution_care": ("Q46_1", ("beds", "price", "service"), "care_institution"),
        "home_hired_care": (
            "Q46_2",
            ("market", "price", "channel", "affordability"),
            "care_home",
        ),
        "community_care": (
            "Q46_3",
            ("availability", "service_content", "application"),
            "care_community",
        ),
        "family_care": (
            "Q46_4",
            ("daily_living", "medicine_management", "basic_nursing", "time_energy"),
            "care_family",
        ),
    }
    if care in resources:
        question_id, required, prefix = resources[care]
        selected = answers.get(question_id) if isinstance(answers.get(question_id), list) else []
        count = len(set(selected).intersection(required))
        put(
            f"{prefix}_resource_complete",
            count == len(required),
            "意向照护方式的关键资源已经全部核实。",
            (question_id,),
        )
        put(
            f"{prefix}_resource_incomplete",
            bool(selected) and count < len(required),
            "意向照护方式的关键资源尚未完整核实。",
            (question_id,),
        )
        put(
            f"{prefix}_resource_none",
            bool(selected) and count == 0,
            "尚未核实意向照护方式的关键资源。",
            (question_id,),
        )
        put(
            f"{prefix}_resource_unknown",
            not bool(selected),
            "意向照护方式的资源情况尚不明确。",
            (question_id,),
        )
        put(
            "care_resource_known",
            count == len(required),
            "已选照护方式的关键资源已完整核实。",
            (question_id,),
        )
        put(
            "care_resource_unknown",
            not bool(selected),
            "照护资源情况尚未核实。",
            (question_id,),
        )
        if care == "home_hired_care":
            price_ready = {"price", "affordability"}.issubset(selected)
            pair(
                "care_home_price_and_affordability_known",
                "care_home_price_or_affordability_gap",
                bool(selected),
                price_ready,
                "护工价格和家庭长期负担能力均已评估。",
                "护工价格或家庭长期负担能力尚未完整评估。",
                question_id,
            )
        elif care == "institution_care":
            pair(
                "care_institution_price_known",
                "care_institution_price_missing",
                bool(selected),
                "price" in selected,
                "意向养老机构价格已经核实。",
                "意向养老机构价格尚未核实。",
                question_id,
            )
            put(
                "care_institution_resource_has_details",
                count > 0,
                "已了解部分养老机构资源信息。",
                (question_id,),
            )
        elif care == "community_care":
            pair(
                "care_community_availability_known",
                "care_community_availability_missing",
                bool(selected),
                "availability" in selected,
                "社区照护服务是否可用已经核实。",
                "社区照护服务是否可用尚未核实。",
                question_id,
            )
        elif care == "family_care":
            capacity_ready = set(required).issubset(selected)
            pair(
                "care_family_capacity_complete",
                "care_family_capacity_gap",
                bool(selected),
                capacity_ready,
                "家庭基础照护能力和时间条件均已确认。",
                "家庭基础照护能力或时间条件尚未完整核实。",
                question_id,
            )
            pair(
                "care_family_time_complete",
                "care_family_time_gap",
                bool(selected),
                "time_energy" in selected,
                "家庭照护时间和精力条件已经确认。",
                "家庭照护时间和精力条件尚未确认。",
                question_id,
            )
            medication_nursing = {"medicine_management", "basic_nursing"}.issubset(selected)
            pair(
                "care_family_medication_and_nursing_complete",
                "care_family_medication_or_nursing_gap",
                bool(selected),
                medication_nursing,
                "家庭用药管理和基础护理能力均已确认。",
                "家庭用药管理或基础护理能力尚未确认。",
                question_id,
            )
    consensus = answers.get("Q47")
    put(
        "care_consensus_not_discussed",
        consensus in {"not_considered_age", "not_discussed"},
        "本人尚未与家人讨论照护安排。",
        ("Q47",),
    )
    put(
        "care_consensus_disputed",
        consensus == "mentioned_no_consensus",
        "本人已提及照护安排但尚未达成共识。",
        ("Q47",),
    )
    put(
        "care_consensus_strong",
        consensus in {"responsibility_clear", "written_or_legal"},
        "家庭照护责任已有较强共识。",
        ("Q47",),
    )
    put(
        "care_consensus_unknown",
        not has_value(consensus),
        "家庭照护共识尚不明确。",
        ("Q47",),
    )
    arrangement_issue = care == "not_considered" or consensus in {
        "not_considered_age",
        "not_discussed",
        "mentioned_no_consensus",
    }
    pair(
        "care_arrangement_issue",
        "care_arrangement_no_issue",
        has_value(care) and has_value(consensus),
        arrangement_issue,
        "照护方式或家庭责任安排存在缺口。",
        "照护方式和家庭责任已有基础安排。",
        "Q46",
        "Q47",
    )


def _followup_facts(followups, put, pair):
    for item in followups:
        if "pension_record_confidence" not in (item.get("target_framework_fields") or []):
            continue
        value = " ".join(str(item.get(key) or "") for key in ("answer_code", "answer_label"))
        known = bool(
            re.search(
                r"confirmed_all|checked_consistent|已核对.*一致|已查.*完整|明确.*完整",
                value,
            )
        )
        unknown = bool(re.search(r"not_sure|不清楚|不确定", value))
        pair(
            "pension_record_known",
            "pension_record_gap",
            not unknown,
            known,
            "已通过补充回答确认缴费记录核验情况。",
            "缴费记录仍有待核验。",
            "followups",
        )
    tokens = " ".join(
        str(value)
        for item in followups
        for value in [
            item.get("followup_id"),
            item.get("answer_code"),
            item.get("answer_label"),
            *(item.get("structured_tags") or []),
        ]
        if value
    )
    if "income_volatility" in tokens:
        put(
            "income_volatility_high_or_medium",
            any(
                code in tokens
                for code in ("high", "medium", "yes_over_50_drop", "yes_moderate_drop")
            ),
            "追问信息显示收入存在中高程度波动。",
            ("followups",),
        )
    if "retirement_asset_buffer" in tokens:
        allowed = any(code in tokens for code in ("medium", "high", "20_50", "gt_50"))
        pair(
            "asset_use_allowed",
            "asset_use_refused",
            True,
            allowed,
            "本人愿意在必要时动用部分金融资产。",
            "本人不愿动用金融资产。",
            "followups",
        )
        put(
            "asset_use_unknown",
            "unclear" in tokens or "not_sure" in tokens,
            "金融资产是否可用于养老尚不明确。",
            ("followups",),
        )
    budget_values = []
    for item in followups:
        if "care_budget_capacity" not in (item.get("target_framework_fields") or []):
            continue
        codes = item.get("answer_code")
        budget_values.extend(codes if isinstance(codes, list) else [codes])
        budget_values.extend([item.get("answer_label"), *(item.get("other_text") or {}).values()])
    budget_values = [str(value).strip() for value in budget_values if value is not None]
    if budget_values:
        unknown = any(
            value.lower() in UNKNOWN
            or re.match(r"(?:没有|还没有|尚无|未制定|尚未制定).*(?:预算|金额)", value)
            for value in budget_values
        )
        known = not unknown and any(
            value
            in {
                "known",
                "yes",
                "confirmed",
                "budget_known",
                "已确定",
                "已明确",
                "有明确预算",
            }
            or (
                numeric_answer(value)[0] in {"known", "range"}
                and any(bound is not None and bound >= 0 for bound in numeric_answer(value)[1:])
            )
            for value in budget_values
        )
        if known or unknown:
            pair(
                "care_budget_known",
                "care_budget_unknown",
                True,
                known,
                "有效回答说明已有照护预算或预算范围。",
                "有效回答说明照护预算尚不明确。",
                "followups",
            )
    if "support_agent" in tokens:
        none = "none" in tokens or "no_clear_agent" in tokens
        pair(
            "support_agent_known",
            "support_agent_none",
            True,
            not none,
            "突发事件协助人选已明确。",
            "突发事件协助人选尚未明确。",
            "followups",
        )
        put("support_agent_unknown", False, "突发事件协助人选尚不明确。", ("followups",))
    else:
        put("support_agent_unknown", True, "突发事件协助人选尚不明确。", ("followups",))


def _fact(
    fact_id: str,
    state: str,
    statement: str,
    *,
    source: str = "questionnaire",
    fields: tuple[str, ...] = (),
    display: str = "",
) -> dict[str, Any]:
    return {
        "factId": fact_id,
        "state": state,
        "statement": statement,
        "source": source,
        "sourceFieldIds": list(fields),
        **({"displayValue": display} if display else {}),
    }

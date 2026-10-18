"""Deterministic retirement assessment and report projections."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from retirement_readiness.core.calculation_inputs import (
    calculation_answers,
    estimate_financial_inputs,
    number,
    profile_complete,
    spouse_answers,
)
from retirement_readiness.core.calculator import (
    calculate_asset_capacity,
    round_number,
    validate_arithmetic,
)
from retirement_readiness.core.followup_lifecycle import debt_cleared_before_retirement
from retirement_readiness.core.household_cashflow import project_household
from retirement_readiness.core.parameters import (
    asset_return_rates,
    economic_assumptions,
    get_care_cost,
    get_medical_policy,
    resolve_calculation_regions,
)
from retirement_readiness.core.pension_calculation import household_pension
from retirement_readiness.core.score_standards import (
    ACTIVITY_PLAN_SCORES,
    ALCOHOL_SCORES,
    CARE_CONSENSUS_SCORES,
    CARE_MODE_SCORES,
    CHECKUP_SCORES,
    DIET_SCORES,
    EXERCISE_SCORES,
    FINANCIAL_LITERACY_SCORE_MATRIX,
    MONITORING_SCORES,
    NONKIN_SUPPORT_SCORES,
    PENSION_EXPECTATION_SCORES,
    PLAN_COMPLETENESS_SCORES,
    PLAN_COMPREHENSION_SCORES,
    PLAN_CONFIDENCE_SCORES,
    PLAN_IMPORTANCE_SCORES,
    RETIREMENT_EXPENSE_SCORES,
    RETIREMENT_RESERVE_SCORES,
    RETIREMENT_RESPONSIBILITY_SCORES,
    RETIREMENT_SAVING_SCORES,
    SMOKING_SCORES,
)

_ASSET_SOURCE_KEYS = {
    "cash_current": "cash_current_wan",
    "time_deposit": "time_deposit_wan",
    "fund": "fund_wan",
    "stock": "stock_wan",
    "bond": "bond_wan",
    "insurance_cash_value": "insurance_wan",
}
_EXPENSE_LABELS = {
    "food": "吃",
    "medical": "医疗（单列）",
    "housing": "住",
    "transport": "交通",
    "elder_support": "赡养老人",
    "utilities": "水电通信",
    "child_education": "子女教育",
    "debt_repayment": "月归还贷款",
    "leisure_entertainment": "休闲娱乐",
    "other": "其他",
}
_CHRONIC_MONTHLY_COST = {
    "hypertension": 300,
    "diabetes": 500,
    "hyperlipidemia": 260,
    "coronary_heart": 800,
    "chronic_lung": 400,
    "arthritis": 300,
    "cervical_lumbar": 300,
    "stomach_liver_kidney": 650,
    "cancer": 2000,
}
_HOSPITAL_ANNUAL_COST = {"0": 0, "1": 15_000, "gte_2": 35_000}
_MAJOR_ILLNESS_STAGES = (
    (300_000, 0.6),
    (150_000, 0.5),
    (120_000, 0.5),
    (100_000, 0.5),
    (50_000, 0.6),
)


def run_calculations(
    raw: dict[str, Any],
    *,
    followups: list[dict[str, Any]] | None = None,
    questionnaire_year: int | None = None,
    questionnaire_month: int | None = None,
) -> dict[str, Any]:
    """Calculate the deterministic assessment payload for one complete snapshot."""
    answers = calculation_answers(raw)
    assumptions = economic_assumptions()
    inflation = assumptions["inflation_rate"]
    life_expectancy = assumptions["life_expectancy"]
    year = questionnaire_year or datetime.now().year

    month = questionnaire_month or (7 if questionnaire_year is not None else datetime.now().month)
    current_age = number(answers.get("Q2"))
    has_spouse = answers.get("Q4") == "married"
    spouse_age = number(raw.get("SP_AGE")) if has_spouse else current_age
    pension = household_pension(raw, answers, questionnaire_year=year, questionnaire_month=month)
    members = [pension["respondent"]] + ([pension["spouse"]] if has_spouse else [])
    as_of = year * 12 + month - 1
    years_to_retirement = (
        max(
            0,
            min(person["projection"]["retirement_month"] for person in members) - as_of,
        )
        / 12
    )
    respondent_retirement_age = pension["respondent"]["projection"]["retirement_age"]
    spouse_retirement_age = (
        pension["spouse"]["projection"]["retirement_age"]
        if has_spouse
        else respondent_retirement_age
    )
    respondent_age_at_anchor = current_age + years_to_retirement
    spouse_age_at_anchor = spouse_age + years_to_retirement
    retirement_years = max(
        1, life_expectancy - min(respondent_retirement_age, spouse_retirement_age)
    )
    regions = resolve_calculation_regions(answers)
    input_assumptions = estimate_financial_inputs(
        raw,
        answers,
        year,
        {"high": 4500, "mid": 3600, "low": 3000}[_region_tier(regions["living"])],
    )
    debt_complete = answers.get("Q29") == "no" or profile_complete(raw, "Q29_1", 3)
    assets, income, expenses = (answers[key] for key in ("Q27", "Q28", "Q30"))

    current_assets = {
        key: number(assets.get(source)) * 10_000 for key, source in _ASSET_SOURCE_KEYS.items()
    }
    current_capacity = calculate_asset_capacity(current_assets, retirement_years)

    current_medical = expenses["medical"]
    spouse = spouse_answers(raw) if has_spouse else {}
    spouse_regions = resolve_calculation_regions(spouse) if has_spouse else regions
    respondent_current_medical = current_medical / 2 if has_spouse else current_medical
    spouse_current_medical = current_medical - respondent_current_medical if has_spouse else 0
    routine_medical = _round(
        _routine_medical(answers, respondent_current_medical, regions["medical"])
        + (
            _routine_medical(spouse, spouse_current_medical, spouse_regions["medical"])
            if has_spouse
            else 0
        )
    )
    care_policy = get_care_cost(regions["care"])
    monthly_care = _selected_care_cost(answers, care_policy)
    respondent_major_medical = _major_medical(answers, regions["medical"])
    spouse_major_medical = (
        _major_medical(spouse, spouse_regions["medical"])
        if has_spouse
        else respondent_major_medical
    )
    major_medical = max(
        (respondent_major_medical, spouse_major_medical),
        key=lambda item: item["gap"],
    )
    debt_continues = debt_cleared_before_retirement(raw, followups or []) is False or (
        expenses["debt_repayment"] > 0
        and pension["respondent"]["projection"]["statutory_month"] <= as_of
    )
    projection_args = (
        answers,
        pension,
        current_assets,
        routine_medical,
        major_medical["gap"],
        number(care_policy.get("annual_24h_care")),
    )
    model = project_household(*projection_args, debt_continues=debt_continues)
    longevity = project_household(*projection_args, life_age=90, debt_continues=debt_continues)
    model["longevity_90"] = {
        key: longevity[key] for key in ("retirement_months", "coverage", "display")
    }
    model["scenarios"] = []
    for label, overrides in (
        ("business_continues", {"business_continues": True}),
        ("debt_continues", {"debt_continues": True}),
        ("transfers_already_included", {"additional_transfers": False}),
    ):
        if (
            label == "business_continues"
            and not income["business_income"]
            or label == "debt_continues"
            and (not expenses["debt_repayment"] or debt_continues)
        ):
            continue
        alternative = project_household(
            *projection_args, **({"debt_continues": debt_continues} | overrides)
        )
        model["scenarios"].append(
            {
                "id": label,
                "coverage": alternative["coverage"],
                "display": alternative["display"],
            }
        )
    retirement_years = model["retirement_months"] / 12
    asset_capacity = model["capacity"]
    display = model["display"]
    monthly_pension = _round(display["pension"])
    monthly_asset_income = _round(display["asset_income"])
    monthly_rent_income = _round(display["rent"])
    monthly_asset_support = _round(
        display["asset_income"] + display["rent"] + display["principal_drawdown"]
    )
    monthly_total_income = _round(display["pension"] + display["asset_income"] + display["rent"])
    asset_income_items = {
        item["key"]: item["usable_yuan"] * ((1 + asset_return_rates()[item["key"]]) ** (1 / 12) - 1)
        for item in asset_capacity["items"]
    }
    routine_medical = display["routine_expense"]
    expense_model = _expense_summary(expenses, model, years_to_retirement, inflation)
    asset_projection = {
        "projected_assets": model["assets_at_retirement"],
        "projection": {
            "years_projected": years_to_retirement,
            "pre_retirement_funding_gap": model["pre_retirement_gap"],
            "cashflow_projection_basis": "monthly_household_cashflow",
            "yearly_trace": [],
        },
    }
    annual_basic = display["basic_expense"] * 12
    annual_pension = _round(display["pension"] * 12)
    annual_asset_recurring = _round((display["asset_income"] + display["rent"]) * 12)
    coverage = {key: item["ratio"] for key, item in model["coverage"].items()}
    coverage.update(
        {
            "scenario_resources": {
                key: item["resources"] for key, item in model["coverage"].items()
            },
            "denominators": {key: item["cost"] for key, item in model["coverage"].items()},
            "asset_principal_included_by_scenario": {
                key: key in {"basic_living_plus_major_medical", "basic_living_plus_care"}
                for key in model["coverage"]
            },
        }
    )
    financial_indices = {key: coverage[key] for key in model["coverage"]}
    financial_indices.update(
        {
            "scenario_resources": coverage["scenario_resources"],
            "index_denominators": coverage["denominators"],
            "asset_principal_included_by_scenario": coverage[
                "asset_principal_included_by_scenario"
            ],
            "annual_pension_income": annual_pension,
            "annual_asset_recurring_income": annual_asset_recurring,
            "risk_adjusted_liquid_assets": asset_capacity["risk_adjusted_liquid_assets"],
        }
    )
    scores = _scores(answers, financial_indices)
    monthly_expense = _round(display["basic_expense"] + routine_medical)
    monthly_gap = _round(
        display["pension"]
        + display["asset_income"]
        + display["rent"]
        - display["basic_expense"]
        - routine_medical
    )
    property_count = max(0, math.floor(number(assets.get("property_count"))))
    property_value = _round(number(assets.get("property_value_wan")) * 10_000)
    nonprimary_property = (
        _round(property_value / property_count * max(property_count - 1, 0))
        if property_count
        else 0
    )
    warnings: list[str] = (
        input_assumptions
        + list(model["assumptions"])
        + [note for person in members for note in person["assumptions"]]
    )
    low_confidence: list[str] = []
    if not debt_complete:
        warnings.append("债务明细不完整，债务压力不进入金额汇总。")
        low_confidence.append("Q29_1.debt")

    calculations = {
        "household_cashflow": model,
        "monthly_total_income": monthly_total_income,
        "monthly_total_expense": monthly_expense,
        "monthly_gap": monthly_gap,
        "critical_illness_gap": major_medical["gap"],
        "finance_profile_status": "available",
        "assumptions": {
            "calculation_scope": "household",
            "years_to_retirement": years_to_retirement,
            "respondent_current_age": current_age,
            **({"spouse_current_age": spouse_age} if has_spouse else {}),
            "respondent_retirement_age": respondent_retirement_age,
            "spouse_retirement_age": spouse_retirement_age,
            "respondent_age_at_household_retirement": respondent_age_at_anchor,
            "spouse_age_at_household_retirement": spouse_age_at_anchor,
            "retirement_years": retirement_years,
            "life_expectancy": life_expectancy,
            "inflation_rate": inflation,
            "questionnaire_year": year,
            "questionnaire_month": month,
            "retirement_projections": {
                name: pension[name]["projection"]
                for name in ("respondent", "spouse")
                if "projection" in pension[name]
            },
            "city_for_estimate": _region_label(regions["pension"]),
            "pension_region": _region_label(regions["pension"]),
            "living_region": _region_label(regions["living"]),
            "medical_region": _region_label(regions["medical"]),
            "care_region": _region_label(regions["care"]),
            "regions": regions,
        },
        "income_breakdown": {
            "asset_input_status": "complete",
            "income_input_status": "available",
            "monthly_basic_pension": pension["monthly_basic_pension"],
            "monthly_supplemental_pension": pension["monthly_supplemental_pension"],
            "monthly_pension_income": monthly_pension,
            "monthly_respondent_basic_pension": pension["respondent"]["monthly_basic"],
            "monthly_spouse_basic_pension": pension["spouse"]["monthly_basic"],
            "monthly_household_basic_pension": pension["monthly_basic_pension"],
            "monthly_respondent_supplemental_pension": pension["respondent"][
                "monthly_supplemental"
            ],
            "monthly_spouse_supplemental_pension": pension["spouse"]["monthly_supplemental"],
            "monthly_respondent_second_pillar_annuity": pension["respondent"][
                "monthly_second_pillar"
            ],
            "monthly_respondent_personal_pension": pension["respondent"][
                "monthly_personal_pension"
            ],
            "monthly_respondent_commercial_annuity": pension["respondent"][
                "monthly_commercial_annuity"
            ],
            "monthly_spouse_second_pillar_annuity": pension["spouse"]["monthly_second_pillar"],
            "monthly_spouse_personal_pension": pension["spouse"]["monthly_personal_pension"],
            "monthly_spouse_commercial_annuity": pension["spouse"]["monthly_commercial_annuity"],
            "monthly_asset_income": monthly_asset_income,
            "monthly_rent_income": monthly_rent_income,
            "monthly_asset_support": monthly_asset_support,
            "monthly_consumption_smoothing_support": monthly_pension + monthly_asset_support,
            "monthly_support_gap_with_assets": monthly_pension
            + monthly_asset_support
            - monthly_expense,
            "monthly_total_income": monthly_total_income,
            "annual_pension_income": annual_pension,
            "annual_asset_recurring_income": annual_asset_recurring,
            "asset_income_items": asset_income_items,
            "pension_estimation_method": (
                f"本人：{pension['respondent']['method']}；配偶：{pension['spouse']['method']}；"
                "两者分别估算后汇总为家庭确定性收入。"
                if has_spouse
                else pension["respondent"]["method"]
            )
            + " 企业年金、职业年金和个人养老金均按问卷填写年度起算至退休的账户终值估算，"
            "再分别除以本人或配偶第一支柱对应的计发月数。",
            "pension_details": pension["respondent"]["pension_details"],
            **(
                {"spouse_pension_details": pension["spouse"]["pension_details"]}
                if has_spouse
                else {}
            ),
            "supplemental_pension_details": {
                "respondent_second_pillar": pension["respondent"]["second_pillar_details"],
                "respondent_personal_pension": pension["respondent"]["personal_pension_details"],
                "spouse_second_pillar": pension["spouse"]["second_pillar_details"],
                "spouse_personal_pension": pension["spouse"]["personal_pension_details"],
            },
        },
        "expense_breakdown": {
            "monthly_retirement_basic_living_today": expense_model["retirement_monthly_today"],
            "monthly_retirement_basic_living": expense_model["retirement_monthly_at_retirement"],
            "monthly_routine_medical": routine_medical,
            "monthly_selected_care": monthly_care,
            "annual_basic_living_at_retirement": annual_basic,
            "annual_routine_medical": routine_medical * 12,
            "annual_selected_care": monthly_care * 12,
            "annual_24h_care_cost": _round(number(care_policy.get("annual_24h_care"))),
            "major_medical_oop_before_assets": major_medical["gap"],
        },
        "expense_model": expense_model,
        "asset_breakdown": {
            **asset_capacity,
            "input_status": "available",
            "debt_input_status": ("available" if debt_complete else "partial_or_missing"),
            "current_asset_capacity": current_capacity,
            "projection": asset_projection["projection"],
            "projected_assets": asset_projection["projected_assets"],
            "household_to_personal_factor": 1,
            "property_count": property_count,
            "household_property_assets": property_value,
            "nonprimary_property_estimate": nonprimary_property,
            "household_total_realisable_assets_estimate": _round(
                asset_capacity["gross_liquid_assets"] + nonprimary_property
            ),
        },
        "financial_indices": financial_indices,
        "scores": scores,
        "cashflow": {
            "monthly_total_income": monthly_total_income,
            "monthly_total_expense": monthly_expense,
            "monthly_gap": monthly_gap,
            "monthly_asset_support": monthly_asset_support,
            "monthly_support_gap_with_assets": monthly_pension
            + monthly_asset_support
            - monthly_expense,
        },
        "coverage": coverage,
        "warnings": warnings,
        "low_confidence_fields": sorted(set(low_confidence)),
        "default_parameters": _default_parameters(assumptions, regions, expense_model, care_policy),
    }
    calculations["warnings"].extend(validate_arithmetic(calculations))
    return calculations


def _region_label(region: dict[str, Any]) -> str:
    return str(region.get("city") or region.get("province") or region.get("raw") or "")


def _routine_medical(
    answers: dict[str, Any], current_monthly: float, region: dict[str, Any]
) -> int:
    chronic = (
        [item for item in answers.get("Q35", []) if item != "none"]
        if isinstance(answers.get("Q35"), list)
        else []
    )
    gross_annual = sum(_CHRONIC_MONTHLY_COST.get(str(code), 300) for code in chronic) * 12
    gross_annual += _HOSPITAL_ANNUAL_COST.get(str(answers.get("Q36") or "0"), 0)
    rate = _reimbursement_rate(answers.get("Q31"), region, major=False)
    return _round(max(gross_annual * (1 - rate) / 12, current_monthly * 0.8))


def _major_medical(answers: dict[str, Any], region: dict[str, Any]) -> dict[str, int]:
    rate = _reimbursement_rate(answers.get("Q31"), region, major=True)
    gross = sum(
        total * catalog * (1 - rate) + total * (1 - catalog)
        for total, catalog in _MAJOR_ILLNESS_STAGES
    )
    return {"gross_oop": _round(gross), "insurance_offset": 0, "gap": _round(gross)}


def _reimbursement_rate(code: Any, region: dict[str, Any], *, major: bool) -> float:
    policy = get_medical_policy(region)
    if code in {"employee_medical", "public_medical"}:
        return (
            number(policy.get("employee_inpatient_ratio" if major else "employee_outpatient_ratio"))
            or 0.75
        )
    if code == "resident_medical":
        return number(
            policy.get("resident_inpatient_ratio" if major else "resident_outpatient_ratio")
        ) or (0.6 if major else 0.55)
    return 0.0


def _selected_care_cost(answers: dict[str, Any], care: dict[str, Any]) -> int:
    code = answers.get("Q46")
    key = {
        "family_care": "monthly_mild",
        "home_hired_care": "monthly_moderate",
        "community_care": "monthly_moderate",
        "institution_care": "monthly_severe",
    }.get(str(code), "monthly_moderate")
    return _round(number(care.get(key)))


def _scores(answers: dict[str, Any], indices: dict[str, Any]) -> dict[str, int]:
    literacy_values = {
        "Q22": {
            "option_1": "more_than_1020",
            "option_2": "exactly_1020",
            "option_3": "less_than_1020",
        }.get(str(answers.get("Q22")), str(answers.get("Q22"))),
        "Q23": {
            "option_1": "buy_more",
            "option_2": "buy_same",
            "option_3": "buy_less",
        }.get(str(answers.get("Q23")), str(answers.get("Q23"))),
        "Q24": str(answers.get("Q24")),
        "Q25": str(answers.get("Q25")),
        "Q26": str(answers.get("Q26")),
    }
    literacy = (
        sum(
            FINANCIAL_LITERACY_SCORE_MATRIX[qid].get(literacy_values[qid], 50)
            for qid in FINANCIAL_LITERACY_SCORE_MATRIX
        )
        / 5
    )
    policy = _multiple_score(
        answers.get("Q12"),
        (
            "min_years",
            "account_query",
            "interruption_effect",
            "benefit_calculation",
            "inheritance_or_withdraw",
        ),
    )
    medical_selected = answers.get("Q33") if isinstance(answers.get("Q33"), list) else []
    if not medical_selected:
        medical_policy = 50
    elif medical_selected == ["never_happened"]:
        medical_policy = 100
    elif medical_selected == ["gave_up_due_hassle"]:
        medical_policy = 80
    else:
        problem_codes = {
            "procedure_unclear",
            "eligibility_unclear",
            "cross_region_problem",
            "catalog_unclear",
            "gave_up_due_hassle",
        }
        medical_policy = _clamp(
            max(1, 5 - len(problem_codes.intersection(medical_selected))) / 5 * 100
        )
    q13 = answers.get("Q13") if isinstance(answers.get("Q13"), dict) else {}
    q14 = answers.get("Q14") if isinstance(answers.get("Q14"), dict) else {}
    cognition_parts = (
        literacy,
        (policy + medical_policy) / 2,
        _score(q13.get("knowledge"), PENSION_EXPECTATION_SCORES),
        _score(q14.get("estimation"), RETIREMENT_EXPENSE_SCORES),
        _score(answers.get("Q16"), RETIREMENT_SAVING_SCORES),
        _score(answers.get("Q15"), RETIREMENT_RESERVE_SCORES),
        _score(answers.get("Q17"), RETIREMENT_RESPONSIBILITY_SCORES),
        sum(
            (
                _score(answers.get("Q18"), PLAN_IMPORTANCE_SCORES),
                _score(answers.get("Q19"), PLAN_COMPREHENSION_SCORES),
                _score(answers.get("Q20"), PLAN_COMPLETENESS_SCORES),
                _score(answers.get("Q21"), PLAN_CONFIDENCE_SCORES),
            )
        )
        / 4,
    )
    chronic = answers.get("Q35") if isinstance(answers.get("Q35"), list) else []
    chronic_score = (
        88
        if "none" in chronic
        else 45
        if {"cancer", "coronary_heart"}.intersection(chronic)
        else 62
    )
    lifestyle = (
        sum(
            (
                _score(answers.get("Q37"), SMOKING_SCORES),
                _score(answers.get("Q38"), ALCOHOL_SCORES),
                _score(answers.get("Q39"), EXERCISE_SCORES),
                _score(answers.get("Q42"), DIET_SCORES),
            )
        )
        / 4
    )
    monitoring = (
        _score(answers.get("Q40"), CHECKUP_SCORES) + _score(answers.get("Q41"), MONITORING_SCORES)
    ) / 2
    social = (
        _score(answers.get("KIN_SUPPORT"), NONKIN_SUPPORT_SCORES)
        + _score(answers.get("Q45"), NONKIN_SUPPORT_SCORES)
    ) / 2
    activity = (
        _score(answers.get("Q43"), ACTIVITY_PLAN_SCORES)
        + _multiple_score(answers.get("Q43_1"), ("time", "channel", "partner", "cost"), fallback=0)
        + social
    ) / 3
    care_resources = {
        "institution_care": ("Q46_1", ("beds", "price", "service")),
        "home_hired_care": ("Q46_2", ("market", "price", "channel", "affordability")),
        "community_care": ("Q46_3", ("availability", "service_content", "application")),
        "family_care": (
            "Q46_4",
            ("daily_living", "medicine_management", "basic_nursing", "time_energy"),
        ),
    }
    resource = care_resources.get(str(answers.get("Q46")))
    care_resource_score = (
        _multiple_score(answers.get(resource[0]), resource[1], fallback=0) if resource else 0
    )
    return {
        "financial_cognition": _clamp(sum(cognition_parts) / len(cognition_parts)),
        "financial_behavior": _clamp(
            sum(
                _coverage_score(indices[key])
                for key in (
                    "basic_living",
                    "basic_living_plus_routine_medical",
                    "basic_living_plus_major_medical",
                    "basic_living_plus_care",
                )
            )
            / 4
        ),
        "health_capital": _clamp((chronic_score + lifestyle + monitoring) / 3),
        "activity_social": _clamp(activity),
        "care_planning": _clamp(
            (
                _score(answers.get("Q46"), CARE_MODE_SCORES)
                + care_resource_score
                + _score(
                    "not_discussed"
                    if answers.get("Q47") == "not_considered_age"
                    else answers.get("Q47"),
                    CARE_CONSENSUS_SCORES,
                )
            )
            / 3
        ),
    }


def _region_tier(region: dict[str, Any]) -> str:
    label = region.get("city") or region.get("province")
    if label in {"北京", "上海", "深圳"}:
        return "high"
    if label in {"宁夏", "山西", "辽宁", "银川", "运城"} or region.get("province") in {
        "宁夏",
        "山西",
        "辽宁",
    }:
        return "low"
    return "mid"


def _score(value: Any, scores: dict[str, int], fallback: int = 50) -> int:
    return scores.get(str(value), fallback)


def _multiple_score(value: Any, positive: tuple[str, ...], fallback: int = 50) -> int:
    if not isinstance(value, list) or not value:
        return fallback
    if "none" in value or "not_sure" in value:
        return 25
    return _clamp(len(set(value).intersection(positive)) / max(1, len(positive)) * 100)


def _coverage_score(value: Any) -> float:
    return min(1.0, max(0.0, number(value))) * 100


def _default_parameters(
    assumptions: dict[str, float],
    regions: dict[str, dict[str, Any]],
    expense_model: dict[str, Any],
    care_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "key": "life_expectancy",
            "value": assumptions["life_expectancy"],
            "source": "system_default",
        },
        {
            "key": "inflation_rate",
            "value": assumptions["inflation_rate"],
            "source": "system_default",
        },
        {
            "key": "wage_growth_rate",
            "value": assumptions["wage_growth_rate"],
            "source": "system_default",
        },
        {
            "key": "retirement_expense_model",
            "value": expense_model["retirement_monthly_at_retirement"],
            "source": "derived_from_answers"
            if expense_model["source"] == "current_expense_projection"
            else "system_default",
        },
        {
            "key": "calculation_region_split",
            "value": {
                key: value.get("city") or value.get("province") for key, value in regions.items()
            },
            "source": "derived_from_answers",
        },
        {"key": "care_cost", "value": care_policy, "source": "model_estimate"},
    ]


def _clamp(value: float) -> int:
    return max(0, min(100, _round(value)))


def _round(value: float | int | None) -> int:
    return round_number(value)


def _expense_summary(
    expenses: dict[str, float], model: dict[str, Any], years: float, inflation: float
) -> dict[str, Any]:
    """Present the same monthly budget used by the cashflow engine."""
    factor = (1 + inflation) ** years
    categories = []
    for key, label in _EXPENSE_LABELS.items():
        current = expenses[key]
        today = model["basic_budget_today"].get(key, 0)
        categories.append(
            {
                "key": key,
                "label": label,
                "current_monthly": _round(current),
                "household_current_monthly": _round(current),
                "personal_scope_factor": 1,
                "retirement_monthly_today": _round(today),
                "retirement_monthly_at_retirement": _round(today * factor),
                "adjustment_factor": round(today / current, 2) if current else 0,
                "category_retention_factor": today / current if current else 0,
                "confidence": "medium",
            }
        )
    return {
        "source": "current_expense_projection",
        "calculation_scope": "household",
        "current_monthly_total": _round(sum(expenses.values())),
        "current_household_monthly_total": _round(sum(expenses.values())),
        "household_to_personal_factor": 1,
        "financial_household_to_personal_factor": 1,
        "retirement_monthly_today": _round(sum(model["basic_budget_today"].values())),
        "retirement_monthly_at_retirement": _round(model["display"]["basic_expense"]),
        "inflation_factor": round(factor, 2),
        "categories": categories,
        "low_confidence_fields": [],
    }

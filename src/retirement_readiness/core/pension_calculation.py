"""Pension projections from the current questionnaire, without historical aliases."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from retirement_readiness.core.calculation_inputs import number, spouse_answers
from retirement_readiness.core.calculator import round_number
from retirement_readiness.core.parameters import (
    region_from_composite,
    resolve_calculation_regions,
    resolve_region,
)
from retirement_readiness.core.pension_reference import (
    current_limits,
    historical_limits,
    resident_base,
    wage_boundaries,
    wage_reference,
)
from retirement_readiness.core.supplemental_pension import (
    annual_payments,
    contribution_value,
    estimate_personal_pension,
    estimate_second_pillar,
)

_PAYMENT_MONTHS = (
    233,
    230,
    226,
    223,
    220,
    216,
    212,
    208,
    204,
    199,
    195,
    190,
    185,
    180,
    175,
    170,
    164,
    158,
    152,
    145,
    139,
    132,
    125,
    117,
    109,
    101,
    93,
    84,
    75,
    65,
    56,
)


def payment_months(age: float) -> int:
    return _PAYMENT_MONTHS[max(40, min(70, round_number(age))) - 40]


def _minimum_months(year: int) -> int:
    return 180 + min(60, max(0, year - 2029) * 6)


def retirement_projection(
    age: float, sex: str, employee: bool, year: int, month: int
) -> dict[str, Any]:
    as_of = year * 12 + month - 1
    birth = as_of - round_number(age) * 12 - 6
    categories = [60] if not employee or sex == "male" else [50, 55]

    def retirement(base: int, born: int) -> int:
        delay = (
            max(
                0,
                min(
                    60 if base == 50 else 36,
                    math.ceil((born - (2025 - base) * 12 + 1) / (2 if base == 50 else 4)),
                ),
            )
            if employee
            else 0
        )
        return born + base * 12 + delay

    target = retirement(categories[0], birth)
    return {
        "as_of_month": as_of,
        "birth_month": birth,
        "retirement_month": target,
        "retirement_age": (target - birth) / 12,
        "earliest_month": min(retirement(base, birth - 5) for base in categories),
        "latest_month": max(retirement(base, birth + 6) for base in categories),
        "assumptions": ["周岁对应出生月份区间，采用中间月估算，非真实生日。"]
        + (
            [
                "女性原退休类别未采集，保留50岁和55岁两种情景；基准采用原50岁类别，不依据就业类型认定。"
            ]
            if len(categories) > 1
            else []
        ),
    }


def _known(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def _history(
    answers: dict[str, Any],
    region: dict[str, Any],
    moment: float,
    age: float,
    pension_type: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = answers.get("PENSION_HISTORY") or []
    result = []
    notes = []
    for index, row in enumerate(rows):
        employment = row.get("employment_type") or answers.get("Q8") or "flexible"
        kind = (
            "employee_pension"
            if employment == "fulltime"
            else row.get("pension_type", pension_type)
        )
        years, months = _known(row.get("paid_years")), _known(row.get("paid_months"))
        result.append(
            {
                "id": row.get("segment_id") or str(index),
                "employment": employment,
                "kind": kind,
                "months": years * 12 + months if years is not None and months is not None else None,
                "reported_annual_income": _known(row.get("annual_income_wan")),
                "annual_income": _known(row.get("annual_income_wan")),
                "annual_payment": _known(
                    row.get(
                        "resident_annual_payment_yuan"
                        if kind == "resident_pension"
                        else "employee_annual_payment_yuan"
                    )
                ),
                "grade": _known(row.get("grade_percent")),
                "region": resolve_region(region_from_composite(row), region)
                if row.get("province_code")
                else region,
            }
        )
        if result[-1]["annual_income"] is not None:
            result[-1]["annual_income"] *= 10000
    if pension_type == "resident_pension" and not any(
        row["kind"] == pension_type for row in result
    ):
        result = []
    presumed = round_number(
        max(
            0,
            min(
                age - 22,
                moment - 2014 if pension_type == "resident_pension" else math.inf,
            ),
        )
        * 12
    )
    if not result:
        count = answers.get("PENSION_SEGMENT_COUNT")
        empty_confirmed = count in (0, "0") and pension_type != "resident_pension"
        result = [
            {
                "id": "estimated-history",
                "employment": "fulltime" if answers.get("Q8") == "fulltime" else "flexible",
                "kind": pension_type,
                "months": 0 if empty_confirmed else presumed,
                "annual_income": None,
                "annual_payment": None,
                "grade": None,
                "region": region,
            }
        ]
        notes.append(
            "已填零段经历不新增历史年限。"
            if empty_confirmed
            else "未知历史年限按22岁起算，居民未知经历不早于2014年；不是实际缴费记录。"
        )
    unknown = [row for row in result if row["months"] is None]
    known = sum(row["months"] or 0 for row in result)
    for row in unknown:
        row["months"] = max(0, round_number((presumed - known) / len(unknown)))
    total = sum(row["months"] for row in result)
    maximum = round_number(max(0, age - 16) * 12)
    if total > maximum:
        remaining = maximum
        for index, row in enumerate(result):
            row["months"] = (
                remaining
                if index == len(result) - 1
                else math.floor(row["months"] * maximum / total)
            )
            remaining -= row["months"]
        notes.append("模型历史不早于16岁，冲突时长按比例分配；原答卷保留。")
    end = moment
    for row in reversed(result):
        row["end"], row["start"] = end, end - row["months"] / 12
        end = row["start"]
    originals = [dict(row) for row in result if (row["annual_income"] or 0) > 0]
    for row in result:
        midpoint = (row["start"] + row["end"]) / 2
        if row["kind"] == "none":
            continue
        if row["employment"] == "fulltime" and not row["annual_income"]:
            other = (
                min(originals, key=lambda r: abs((r["start"] + r["end"]) / 2 - midpoint))
                if originals
                else None
            )
            row["annual_income"] = (
                other["annual_income"] * 1.03 ** (midpoint - (other["start"] + other["end"]) / 2)
                if other
                else wage_reference(region, "contribution_reference", midpoint)["amount"] * 12
            )
            notes.append("未知历史工资按其他有效工资或当地同期工资估算，不改写原始未知。")
        elif row["kind"] == "resident_pension" and not row["annual_payment"]:
            row["annual_payment"] = 1000
            notes.append("居民未知缴费按每年1000元规划假设。")
        elif (
            row["employment"] == "flexible"
            and row["kind"] == "employee_pension"
            and not row["annual_payment"]
            and row["grade"] is None
        ):
            row["grade"] = 100
            notes.append("未知灵活职工缴费按当地100%工资档位估算。")
    notes.append("工作经历按所填顺序连续回推时间，不代表确认的参保起止日期。")
    return result, list(dict.fromkeys(notes))


def _historical_index(
    history: list[dict[str, Any]], region: dict[str, Any]
) -> list[dict[str, Any]]:
    segments = []
    for row in history:
        if row["kind"] != "employee_pension" or row["months"] == 0:
            continue
        boundaries = [2006, *wage_boundaries(region), *wage_boundaries(row["region"])]
        if region["province"] == "上海":
            boundaries += [1993, 2012, 2013, 2014, 2019, 2101]
        if region["province"] == "北京":
            boundaries += [1996, 2019, 2101]
        if region["province"] == "重庆":
            boundaries += [2019, 2101]
        points = sorted(
            {
                row["start"],
                row["end"],
                *(v for v in boundaries if row["start"] < v < row["end"]),
            }
        )
        for start, end in zip(points, points[1:]):
            midpoint = (start + end) / 2
            denominator = wage_reference(region, "index_denominator", midpoint)["amount"]
            if row["employment"] == "fulltime":
                lower, upper = historical_limits(row["region"], midpoint)
                base = max(lower, min(upper, row["annual_income"] / 12))
            elif row["annual_payment"] is not None:
                base = row["annual_payment"] / 12 / 0.2
            else:
                base = (
                    wage_reference(row["region"], "contribution_reference", midpoint)["amount"]
                    * row["grade"]
                    / 100
                )
            index = base / denominator
            if region["province"] == "上海" and start >= 1993 and end <= 2014:
                index = max(index, 1 if start < 2012 else 0.85 if start < 2013 else 0.75)
            segments.append(
                {
                    "identity": "unit_employee"
                    if row["employment"] == "fulltime"
                    else "flexible_employee",
                    "city": row["region"]["province"],
                    "start_year": start,
                    "end_year": end,
                    "months": (end - start) * 12,
                    "monthly_base_yuan": base,
                    "city_average_wage_yuan": denominator,
                    "index": index,
                }
            )
    return segments


def _person(answers: dict[str, Any], year: int, month: int) -> dict[str, Any]:
    moment = year + (month - 1) / 12
    employment = answers.get("Q8") or "unemployed"
    codes = list(answers.get("Q9") or [])
    assumptions = []
    if not codes or "not_sure" in codes:
        history_types = {
            "employee_pension"
            if row.get("employment_type") == "fulltime"
            else row.get("pension_type")
            for row in answers.get("PENSION_HISTORY") or []
        }
        codes = [
            "employee_pension"
            if "employee_pension" in history_types
            else "resident_pension"
            if "resident_pension" in history_types
            else "employee_pension"
            if employment in {"fulltime", "flexible"}
            else "resident_pension"
        ]
        assumptions.append("参保类别未知，按就业和已有经历选取规划方案，不认定实际资格。")
    employee = "employee_pension" in codes
    resident = "resident_pension" in codes
    region = resolve_calculation_regions(answers)["pension"]
    projection = retirement_projection(
        number(answers.get("Q2")), str(answers.get("Q1")), employee, year, month
    )
    as_of, birth = projection["as_of_month"], projection["birth_month"]
    age = (as_of - birth) / 12
    history, notes = _history(
        answers,
        region,
        moment,
        age,
        "employee_pension" if employee else "resident_pension",
    )
    assumptions += projection["assumptions"] + notes
    paid = sum(
        row["months"]
        for row in history
        if row["kind"] == ("employee_pension" if employee else "resident_pension")
    )
    working = employment in {"fulltime", "flexible"}
    target = max(
        projection["retirement_month"],
        as_of + (12 if working and projection["retirement_month"] <= as_of else 0),
    )
    if employee:
        for _ in range(12):
            next_target = max(target, as_of + max(0, _minimum_months(target // 12) - paid))
            if next_target == target:
                break
            target = next_target
    elif resident:
        target = max(target, as_of + max(0, 180 - paid))
    target = round_number(target)
    future_months = (
        max(0, target - as_of)
        if working or not employee
        else max(0, _minimum_months(target // 12) - paid)
    )
    projection.update(
        statutory_month=projection["retirement_month"],
        retirement_month=target,
        retirement_age=(target - birth) / 12,
    )
    years = max(0, (target - as_of) / 12)
    retire_age = (target - birth) / 12
    months = payment_months(retire_age)
    details = {}
    employee_amount = resident_amount = 0
    payments = []
    if employee:
        segments = _historical_index(history, region)
        history_months = sum(row["months"] for row in segments)
        historical = (
            sum(row["index"] * row["months"] for row in segments) / history_months
            if history_months
            else 1
        )
        limits = current_limits(region, moment)
        last = next(
            (
                row
                for row in reversed(history)
                if row["kind"] == "employee_pension" and row["months"] > 0
            ),
            {},
        )
        base = (
            last["annual_income"] / 12
            if last.get("employment") == "fulltime" and last.get("annual_income") is not None
            else last["annual_payment"] / 12 / 0.2
            if last.get("annual_payment") is not None
            else limits["wage"] * (last.get("grade") or 100) / 100
        )
        base = max(limits["lower"], min(limits["upper"], base))
        current_index = base / limits["wage"]
        total = history_months + future_months
        index = (
            (historical * history_months + current_index * future_months) / total
            if total
            else current_index
        )
        current_balance = sum(
            row["monthly_base_yuan"] * row["months"] * (0.11 if row["end_year"] <= 2006 else 0.08)
            for row in segments
        )
        payments = [
            {
                "month": as_of + i,
                "monthly_base": base * 1.03 ** (i / 12),
                "account_credit": base * 1.03 ** (i / 12) * 0.08,
                "personal_payment": base
                * 1.03 ** (i / 12)
                * (0.08 if employment == "fulltime" else 0.2),
            }
            for i in range(round_number(future_months))
        ]
        future_balance = sum(row["account_credit"] for row in payments)
        benefit = wage_reference(region, "benefit_base", target / 12)
        basic = benefit["amount"] * (1 + index) / 2 * (total / 12) * 0.01
        account = (current_balance + future_balance) / months
        employee_amount = round_number(basic + account)
        details = {
            "employee_actual_years": history_months / 12,
            "employee_deemed_years": 0,
            "employee_future_years": future_months / 12,
            "employee_total_years": total / 12,
            "employee_average_contribution_index": index,
            "employee_historical_average_index": historical,
            "employee_current_contribution_index": current_index,
            "employee_current_contribution": {
                **limits,
                "monthly_base": base,
                "index": current_index,
            },
            "employee_future_contribution_payments": payments,
            "employee_basic_pension": round_number(basic),
            "employee_personal_account_pension": round_number(account),
            "employee_current_account_balance": round_number(current_balance),
            "employee_estimated_future_account_balance": round_number(future_balance),
            "employee_index_segments": segments,
            "employee_index_confidence": "low",
            "employee_index_method": "历史指数按实际缴费月数加权，再与未来保持的当前指数加权；基数先匹配地区和期间边界。",
        }
    resident_details = {}
    if resident and not employee:
        rows = [row for row in history if row["kind"] == "resident_pension"]

        def subsidy(annual):
            return 60 if annual >= 500 else 30

        balance = sum(
            row["annual_payment"] * row["months"] / 12
            + subsidy(row["annual_payment"]) * max(0, row["end"] - max(2014, row["start"]))
            for row in rows
        )
        annual = rows[-1]["annual_payment"] if rows else 1000
        future_payments = annual_payments(annual, years, moment)
        balance = balance * 1.015**years + contribution_value(
            future_payments + annual_payments(subsidy(annual), years, moment),
            target,
            0.015,
        )
        resident_amount = round_number(resident_base(region, year) + balance / 139)
        resident_details = {
            "future_payments": future_payments,
            "account_at_retirement": balance,
            "base": resident_base(region, year),
        }
    second = estimate_second_pillar(
        participated="combined_annuity" in codes,
        history=history,
        employment=employment,
        moment=moment,
        age=age,
        retirement_age=retire_age,
        payment_months=months,
        wage=wage_reference(region, "contribution_reference", moment)["amount"],
    )
    regions = [
        region_from_composite(row)
        for row in answers.get("PENSION_HISTORY") or []
        if row.get("province_code")
    ]
    personal = estimate_personal_pension(
        participated=answers.get("Q10") == "yes",
        annual_contribution_code=answers.get("Q10_1"),
        first_pillar_enrollment_regions=regions,
        questionnaire_year=moment,
        current_age=age,
        retirement_age=retire_age,
        payment_months=months,
    )
    annuity = answers.get("Q11_1") if isinstance(answers.get("Q11_1"), dict) else {}
    commercial = (
        number(annuity.get("annual_amount_yuan")) / 12
        if answers.get("Q11") in {"annuity", "both"}
        else 0
    )
    if (
        answers.get("Q11") in {"annuity", "both"}
        and _known(annuity.get("periodic_amount_yuan")) is None
    ):
        commercial = 0
        assumptions.append("商业年金领取金额未知，不计未确认增量。")
    return {
        "monthly_basic": employee_amount + resident_amount,
        "monthly_employee": employee_amount,
        "monthly_resident": resident_amount,
        "monthly_second_pillar": round_number(second["monthly_amount"]),
        "monthly_personal_pension": round_number(personal["monthly_amount"]),
        "monthly_commercial_annuity": round_number(commercial),
        "monthly_supplemental": round_number(second["monthly_amount"])
        + round_number(personal["monthly_amount"])
        + round_number(commercial),
        "pension_details": details,
        "second_pillar_details": second,
        "personal_pension_details": personal,
        "resident_details": resident_details,
        "commercial_start_age": _known(annuity.get("start_age")) if annuity else retire_age,
        "projection": projection,
        "history": history,
        "employment": employment,
        "region": region,
        "method": "养老金为有说明的规划估算，不代替经办核定。",
        "assumptions": assumptions,
        "self_payments": payments if employee and not working else [],
    }


def household_pension(
    raw: dict[str, Any],
    answers: dict[str, Any],
    *,
    questionnaire_year: int | None = None,
    questionnaire_month: int | None = None,
) -> dict[str, Any]:
    year = questionnaire_year or datetime.now().year
    month = questionnaire_month or (7 if questionnaire_year is not None else datetime.now().month)
    respondent = _person(answers, year, month)
    spouse = _person(spouse_answers(raw), year, month) if raw.get("Q4") == "married" else None
    members = [respondent] + ([spouse] if spouse else [])
    return {
        "monthly_basic_pension": sum(person["monthly_basic"] for person in members),
        "monthly_supplemental_pension": sum(person["monthly_supplemental"] for person in members),
        "respondent": respondent,
        "spouse": spouse
        or {
            "monthly_basic": 0,
            "monthly_supplemental": 0,
            "monthly_second_pillar": 0,
            "monthly_personal_pension": 0,
            "monthly_commercial_annuity": 0,
            "pension_details": {},
            "second_pillar_details": {},
            "personal_pension_details": {},
        },
    }

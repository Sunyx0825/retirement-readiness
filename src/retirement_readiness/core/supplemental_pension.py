"""Second- and third-pillar pension accumulation models."""

from __future__ import annotations

from typing import Any

from retirement_readiness.core.parameters import resolve_region

_WAGE_GROWTH_RATE = 0.03
_PERSONAL_RETURN_RATE = 0.04
_PERSONAL_CONTRIBUTIONS = {
    "none": 0,
    "lt_1000": 500,
    "1000_3999": 2500,
    "4000_7999": 6000,
    "8000_11999": 10000,
    "12000": 12000,
}
_PERSONAL_PENSION_PILOT_CITIES = {
    "北京",
    "天津",
    "石家庄",
    "雄安新区",
    "晋城",
    "呼和浩特",
    "沈阳",
    "大连",
    "长春",
    "哈尔滨",
    "上海",
    "苏州",
    "杭州",
    "宁波",
    "合肥",
    "南昌",
    "青岛",
    "东营",
    "郑州",
    "武汉",
    "长沙",
    "广州",
    "深圳",
    "南宁",
    "海口",
    "重庆",
    "成都",
    "贵阳",
    "玉溪",
    "拉萨",
    "西安",
    "庆阳",
    "西宁",
    "银川",
    "乌鲁木齐",
}


def annual_payments(
    annual: float, years: float, moment: float, growth: float = 0
) -> list[dict[str, float]]:
    from retirement_readiness.core.calculator import round_number

    full = int(max(0, years))
    payments = [
        {
            "month": round_number((moment + i + 1) * 12) - 1,
            "amount": annual * (1 + growth) ** i,
        }
        for i in range(full)
    ]
    if years - full > 1e-8:
        payments.append(
            {
                "month": round_number((moment + years) * 12) - 1,
                "amount": annual * (1 + growth) ** full * (years - full),
            }
        )
    return [row for row in payments if row["amount"] > 0]


def contribution_value(payments: list[dict[str, float]], end_month: int, rate: float) -> float:
    return sum(
        row["amount"] * (1 + rate) ** ((end_month - row["month"] - 1) / 12)
        for row in payments
        if row["month"] < end_month
    )


def estimate_second_pillar(
    *,
    participated: bool,
    history: list[dict[str, Any]],
    employment: str,
    moment: float,
    age: float,
    retirement_age: float,
    payment_months: int,
    wage: float,
) -> dict[str, Any]:
    if not participated:
        return _empty("not_participating", "combined", payment_months)
    segments = [
        row for row in history if row["employment"] == "fulltime" and row["end"] > row["start"]
    ]
    if not segments:
        segments = [
            {
                "start": max(2015, moment - sum(row["months"] for row in history) / 12),
                "end": moment,
                "annual_income": wage * 12,
            }
        ]
    future = max(0, retirement_age - age)
    balance_year = min(moment, moment + retirement_age - age)
    balance = 0.0
    historical_years = 0.0
    for row in segments:
        start, end = max(2015, row["start"]), min(row["end"], balance_year)
        years = max(0, end - start)
        historical_years += years
        annual = (row["annual_income"] or wage * 12) * 0.08
        full = int(years)
        balance += (
            sum(annual * 1.03 ** (years - i - 1) for i in range(full)) + annual * (years - full)
        ) * 1.03 ** (balance_year - end)
    basis = next(
        (
            row["annual_income"]
            for row in reversed(segments)
            if row.get("annual_income") is not None
        ),
        wage * 12,
    )
    payments = (
        annual_payments(basis * 0.08, future, moment, 0.03) if employment == "fulltime" else []
    )
    terminal = balance * 1.03**future + contribution_value(
        payments, round((moment + future) * 12), 0.03
    )
    return {
        **_empty("estimated", "combined", payment_months),
        "terminal_value": terminal,
        "current_balance": balance,
        "monthly_amount": terminal / payment_months,
        "current_contribution_years": historical_years,
        "future_contribution_years": future if payments else 0,
        "total_contribution_years": historical_years + (future if payments else 0),
        "annual_contribution_base": basis,
        "contribution_rate": 0.08,
        "investment_return_rate": 0.03,
        "start_year": 2015,
        "future_payments": payments,
        "assumptions": [
            "年金按2015年历史窗口、8%缴费、3%积累收益和工资增长估算，领取期收益为0；均为模型假设。"
        ],
    }


def estimate_personal_pension(
    *,
    participated: bool,
    annual_contribution_code: Any,
    first_pillar_enrollment_regions: list[str],
    questionnaire_year: int,
    current_age: float,
    retirement_age: float,
    payment_months: int,
) -> dict[str, Any]:
    if not participated:
        return _empty("not_participating", "personal_pension", payment_months)
    code = str(annual_contribution_code or "")
    annual = _PERSONAL_CONTRIBUTIONS.get(code, 6000)
    pilot = any(
        (region := resolve_region(text))["province"] == "福建"
        or (region["city"] or region["province"]) in _PERSONAL_PENSION_PILOT_CITIES
        for text in first_pillar_enrollment_regions
    )
    start_year = 2023 if pilot else 2025
    current_years = max(0, questionnaire_year - start_year)
    future_years = max(0, retirement_age - current_age)
    total_years = current_years + future_years
    as_of = round(questionnaire_year * 12)
    end = round((questionnaire_year + retirement_age - current_age) * 12)
    payments = [
        {
            "month": month,
            "amount": min(12000, annual * 1.03 ** max(0, month // 12 - as_of // 12)) / 12,
        }
        for month in range(start_year * 12, end)
        if annual > 0
    ]
    terminal = contribution_value(payments, end, 0.04)
    return {
        "status": "estimated",
        "type": "personal_pension",
        "terminal_value": terminal,
        "monthly_amount": terminal / max(1, payment_months),
        "payment_months": payment_months,
        "current_contribution_years": _money(current_years),
        "future_contribution_years": _money(future_years),
        "total_contribution_years": _money(total_years),
        "annual_contribution_base": annual,
        "contribution_rate": 1,
        "investment_return_rate": _PERSONAL_RETURN_RATE,
        "wage_growth_rate": _WAGE_GROWTH_RATE,
        "start_year": start_year,
        "pilot_region_matched": pilot if first_pillar_enrollment_regions else None,
        "future_payments": [row for row in payments if row["month"] >= as_of],
        "assumptions": [
            "参保地区仅采用已填写的缴费地区；未采集时暂用2025年积累窗口，不认定非试点。2023/2025不代表实际开户年份。"
        ],
        "source_regions": first_pillar_enrollment_regions,
    }


def _empty(status: str, pillar_type: str, payment_months: int) -> dict[str, Any]:
    return {
        "status": status,
        "type": pillar_type,
        "terminal_value": 0,
        "monthly_amount": 0,
        "payment_months": payment_months,
        "current_contribution_years": 0,
        "future_contribution_years": 0,
        "total_contribution_years": 0,
        "annual_contribution_base": 0,
        "contribution_rate": 0,
        "investment_return_rate": 0,
        "wage_growth_rate": _WAGE_GROWTH_RATE,
    }


def _money(value: float) -> float:
    return round(value, 2)

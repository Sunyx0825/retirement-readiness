"""Pure arithmetic shared by retirement calculations and regression tests."""

from __future__ import annotations

import math
from typing import Any

ASSET_LIQUIDITY_FACTORS = {
    "cash_current": 1.0,
    "time_deposit": 0.95,
    "fund": 0.75,
    "stock": 0.65,
    "bond": 0.9,
    "insurance_cash_value": 0.7,
}
_ASSET_LABELS = {
    "cash_current": "现金及活期",
    "time_deposit": "定期存款",
    "fund": "基金",
    "stock": "股票",
    "bond": "债券",
    "insurance_cash_value": "保险现金价值",
}


def calculate_asset_capacity(assets: dict[str, float], retirement_years: float) -> dict[str, Any]:
    items = []
    for key, factor in ASSET_LIQUIDITY_FACTORS.items():
        gross = max(0.0, _finite(assets.get(key)))
        usable = gross * factor
        items.append(
            {
                "key": key,
                "label": _ASSET_LABELS[key],
                "gross_yuan": round_number(gross),
                "liquidity_factor": factor,
                "usable_yuan": round_number(usable),
                "haircut_yuan": round_number(gross - usable),
            }
        )
    gross = sum(item["gross_yuan"] for item in items)
    adjusted = sum(item["usable_yuan"] for item in items)
    annual_drawdown = adjusted / max(1.0, _finite(retirement_years))
    return {
        "gross_liquid_assets": round_number(gross),
        "risk_adjusted_liquid_assets": round_number(adjusted),
        "liquidity_haircut_amount": round_number(gross - adjusted),
        "annual_principal_drawdown": round_number(annual_drawdown),
        "monthly_principal_drawdown": round_number(annual_drawdown / 12),
        "items": items,
    }


def validate_arithmetic(calculations: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    cashflow = calculations["cashflow"]
    if (
        abs(
            cashflow["monthly_total_income"]
            - cashflow["monthly_total_expense"]
            - cashflow["monthly_gap"]
        )
        > 1
    ):
        warnings.append("monthly_gap_not_equal_income_minus_expense")
    coverage = calculations["coverage"]
    indices = calculations["financial_indices"]
    for coverage_key, index_key in (
        ("basic_living", "basic_living"),
        ("basic_living_plus_routine_medical", "basic_living_plus_routine_medical"),
        ("basic_living_plus_major_medical", "basic_living_plus_major_medical"),
        ("basic_living_plus_care", "basic_living_plus_care"),
    ):
        if abs(coverage[coverage_key] - indices[index_key]) > 0.01:
            warnings.append(f"{coverage_key}_index_mismatch")
    return warnings


def _finite(value: Any) -> float:
    try:
        number = float(value or 0)
        return number if number == number and abs(number) != float("inf") else 0.0
    except (TypeError, ValueError):
        return 0.0


def round_number(value: Any) -> int:
    """Match JavaScript Math.round, the reference calculation contract."""
    return math.floor(_finite(value) + 0.5)

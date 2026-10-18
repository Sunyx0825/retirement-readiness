"""Monthly household accumulation and aligned retirement resource scenarios."""

from __future__ import annotations

from typing import Any

from retirement_readiness.core.calculator import (
    ASSET_LIQUIDITY_FACTORS,
    calculate_asset_capacity,
    round_number,
)
from retirement_readiness.core.parameters import asset_return_rates

_KEYS = tuple(ASSET_LIQUIDITY_FACTORS)
DISPLAY_NOTE = "养老金展示为家庭成员各自初领金额之和；指标按各自领取期累计，费用按较长退休规划期累计。退休后相对增长为0，有限账户耗尽后停止领取。"


def _move(balances: dict[str, float], amount: float) -> float:
    if amount >= 0:
        balances["cash_current"] += amount
        return 0
    needed = -amount
    for key in _KEYS:
        used = min(balances[key], needed)
        balances[key] -= used
        needed -= used
    return max(0, needed)


def _earn(balances: dict[str, float], rates: dict[str, float]) -> float:
    total = 0.0
    for key in _KEYS:
        gain = balances[key] * ((1 + rates[key]) ** (1 / 12) - 1)
        balances[key] += gain
        total += gain
    return total


def project_household(
    answers: dict[str, Any],
    pension: dict[str, Any],
    initial_assets: dict[str, float],
    routine_medical: float,
    major_pressure: float,
    annual_care: float,
    *,
    life_age: int = 85,
    business_continues: bool = False,
    debt_continues: bool = False,
    additional_transfers: bool = True,
) -> dict[str, Any]:
    people = [pension["respondent"]] + (
        [pension["spouse"]] if answers.get("Q4") == "married" else []
    )
    as_of = people[0]["projection"]["as_of_month"]
    retirement = [person["projection"]["retirement_month"] for person in people]
    start = max(as_of, min(retirement))
    budget_month = max(as_of, *retirement)
    ends = [
        max(
            person["projection"]["birth_month"] + life_age * 12,
            as_of + (15 if life_age == 90 else 10) * 12,
            target + 12,
        )
        for person, target in zip(people, retirement)
    ]
    durations = [end - max(as_of, target) for end, target in zip(ends, retirement)]
    months = max(durations)

    def price(month):
        return 1.02 ** ((month - as_of) / 12)

    factor = price(start)
    rates = asset_return_rates()
    incomes, expenses = answers["Q28"], answers["Q30"]
    workers = [
        i for i, person in enumerate(people) if person["employment"] in {"fulltime", "flexible"}
    ]
    latest_incomes = [
        next(
            (
                row["reported_annual_income"]
                for row in reversed(person["history"])
                if (row.get("reported_annual_income") or 0) > 0
            ),
            0,
        )
        for person in people
    ]
    complete_weights = bool(workers) and all(latest_incomes[i] > 0 for i in workers)
    weights = [
        latest_incomes[i] / sum(latest_incomes[j] for j in workers)
        if i in workers and complete_weights
        else 1 / len(workers)
        if i in workers
        else 0
        for i in range(len(people))
    ]
    if not workers:
        weights[0] = 1
    work_stops = [
        max(as_of + 1, target) if i in workers else as_of for i, target in enumerate(retirement)
    ]
    early_stop = max(as_of + 1, min(work_stops))
    child_ages = answers["Q5"]["child_ages"]
    education_end = (
        as_of + round_number((22 - min(child_ages)) * 12)
        if len(child_ages) == answers["Q5"]["count"] and child_ages and min(child_ages) < 22
        else max(ends)
    )
    basic_today = {}
    for key in (
        "food",
        "housing",
        "transport",
        "elder_support",
        "utilities",
        "child_education",
        "leisure_entertainment",
        "other",
    ):
        retained = 1 - 0.2 * len(workers) / len(people) if key in {"food", "transport"} else 1
        finish = (
            retirement[0]
            if key == "elder_support"
            else education_end
            if key == "child_education"
            else max(ends)
        )
        basic_today[key] = expenses[key] * retained if budget_month < finish else 0
    basic_budget = sum(basic_today.values())
    debt_end = (
        max(ends)
        if debt_continues
        else max(as_of + (1 if expenses["debt_repayment"] > 0 else 0), retirement[0])
    )
    debt_budget = expenses["debt_repayment"] if budget_month < debt_end else 0
    transfers: dict[int, float] = {}
    pension_flows = []
    display_pension = 0.0
    for i, person in enumerate(people):
        target, birth = retirement[i], person["projection"]["birth_month"]
        components = [(person["monthly_basic"], target, ends[i], None)]
        for detail_key, amount_key in (
            ("second_pillar_details", "monthly_second_pillar"),
            ("personal_pension_details", "monthly_personal_pension"),
        ):
            detail = person[detail_key]
            amount = (
                detail["monthly_amount"]
                if detail_key == "second_pillar_details"
                else person[amount_key]
            )
            components.append(
                (
                    amount,
                    target,
                    min(ends[i], target + detail["payment_months"]),
                    detail["terminal_value"],
                )
            )
            if additional_transfers:
                for payment in detail.get("future_payments", []):
                    month = int(payment["month"])
                    if as_of <= month < target:
                        transfers[month] = transfers.get(month, 0) + payment["amount"]
        if additional_transfers:
            for payment in person["resident_details"].get("future_payments", []):
                month = int(payment["month"])
                transfers[month] = transfers.get(month, 0) + payment["amount"]
        for payment in person.get("self_payments", []):
            month = int(payment["month"])
            transfers[month] = transfers.get(month, 0) + payment["personal_payment"]
        commercial_age = person["commercial_start_age"]
        commercial_start = (
            birth + round_number(commercial_age * 12) if commercial_age is not None else target
        )
        components.append((person["monthly_commercial_annuity"], commercial_start, ends[i], None))
        display_pension += sum(component[0] for component in components)
        for amount, first, last, capital in components:
            pension_flows.append(
                {
                    "amount": amount,
                    "first": first,
                    "last": last,
                    "capital": capital,
                    "own_start": max(as_of, target),
                    "duration": durations[i],
                }
            )
    reserve = sum(
        amount * factor / price(month) for month, amount in transfers.items() if month >= start
    )
    balances = dict(initial_assets)
    pre_gap = 0.0
    for month in range(as_of, start):
        _earn(balances, rates)
        wage = sum(
            incomes["wage_income"] / 12 * weights[i] * 1.03 ** ((month - as_of) / 12)
            for i in range(len(people))
            if month < work_stops[i]
        )
        recurring = (incomes["rent_income"] * 0.88 + incomes["land_income"]) / 12
        if month < early_stop or business_continues:
            recurring += incomes["business_income"] / 12
        if month < early_stop:
            recurring += incomes["other_income"] / 12
        pension_income = sum(
            flow["amount"]
            if flow["capital"] is None
            else min(
                flow["amount"],
                max(0, flow["capital"] - flow["amount"] * (month - flow["first"])),
            )
            for flow in pension_flows
            if flow["first"] <= month < flow["last"]
        )
        spending = sum(
            expenses[key]
            for key in (
                "food",
                "housing",
                "transport",
                "utilities",
                "leisure_entertainment",
                "other",
                "medical",
            )
        )
        if month < retirement[0]:
            spending += expenses["elder_support"]
        if month < education_end:
            spending += expenses["child_education"]
        if month < debt_end:
            spending += expenses["debt_repayment"]
        pre_gap += _move(
            balances,
            wage + recurring + pension_income - spending * price(month) - transfers.get(month, 0),
        ) / price(month)
    assets_at_start = dict(balances)
    converted = []
    for flow in pension_flows:
        first = max(flow["own_start"], flow["first"])
        last = flow["last"]
        if last <= first:
            continue
        conversion = factor / price(flow["own_start"])
        capital = flow["capital"]
        if capital is not None:
            capital = max(0, capital - flow["amount"] * max(0, first - flow["first"])) * conversion
        converted.append(
            {
                "amount": flow["amount"] * conversion,
                "first": first - flow["own_start"],
                "last": last - flow["own_start"],
                "capital": capital,
            }
        )
    rent = incomes["rent_income"] * 0.88 / 12
    recurring = rent + incomes["land_income"] / 12
    if business_continues or early_stop > start:
        recurring += incomes["business_income"] / 12
    if early_stop > start:
        recurring += incomes["other_income"] / 12
    rows = []
    pension_sum = 0.0
    pension_reinvestment = 0.0
    other_resources = sum(balances.values())
    for offset in range(months):
        pension_income = sum(
            flow["amount"]
            if flow["capital"] is None
            else min(
                flow["amount"],
                max(0, flow["capital"] - flow["amount"] * (offset - flow["first"])),
            )
            for flow in converted
            if flow["first"] <= offset < flow["last"]
        )
        gain = _earn(balances, rates)
        transfer = reserve if offset == 0 else 0
        other_resources = max(0, other_resources + recurring + gain)
        covered = min(other_resources, transfer)
        other_resources -= covered
        pension_reinvestment += (transfer - covered) / factor
        gap = _move(
            balances,
            pension_income
            + recurring
            - (basic_budget + routine_medical + debt_budget) * factor
            - transfer,
        )
        pension_sum += pension_income / factor
        rows.append(
            {
                "month": start + offset,
                "pension": pension_income,
                "recurring_income": recurring,
                "basic_expense": basic_budget * factor,
                "routine_expense": routine_medical * factor,
                "restricted_transfer": transfer,
                "asset_return": gain,
                "end_assets": sum(balances.values()),
                "unfunded_gap": gap,
            }
        )
    basic_cost = basic_budget * months
    routine_cost = (basic_budget + routine_medical) * months
    net_pension = max(0, pension_sum - pension_reinvestment)
    coverage = {
        "basic_living": {"resources": net_pension, "cost": basic_cost},
        "basic_living_plus_routine_medical": {
            "resources": net_pension,
            "cost": routine_cost,
        },
    }
    for key, pressure in (
        ("basic_living_plus_major_medical", major_pressure),
        ("basic_living_plus_care", annual_care * months / 12),
    ):
        stress = {name: assets_at_start[name] * ASSET_LIQUIDITY_FACTORS[name] for name in _KEYS}
        principal = sum(stress.values()) / factor
        gains = gap_total = transferred = outside = 0.0
        first_gap = None
        for offset, row in enumerate(rows):
            outside += recurring - rent
            covered = min(outside, row["restricted_transfer"])
            outside -= covered
            transfer = row["restricted_transfer"] - covered
            transferred += transfer / factor
            gains += (_earn(stress, rates) + rent) / factor
            shock = (
                pressure / months
                if key == "basic_living_plus_care"
                else pressure
                if offset == 0
                else 0
            )
            gap = _move(
                stress,
                row["pension"] + rent - basic_budget * factor - shock * factor - transfer,
            )
            gap_total += gap / factor
            if gap > 0 and first_gap is None:
                first_gap = start + offset
        coverage[key] = {
            "resources": max(0, pension_sum + principal + gains - transferred),
            "cost": basic_cost + pressure,
            "initial_principal": principal,
            "asset_income": gains,
            "reallocated_resources": transferred,
            "unfunded_gap": gap_total,
            "first_unfunded_month": first_gap,
        }
    for item in coverage.values():
        item["ratio"] = float(f"{item['resources'] / item['cost']:.13g}") if item["cost"] else 1.0
    capacity = calculate_asset_capacity(assets_at_start, months / 12)
    asset_income = sum(
        item["usable_yuan"] * ((1 + rates[item["key"]]) ** (1 / 12) - 1)
        for item in capacity["items"]
    )
    return {
        "version": "household_cashflow_1.2.0",
        "as_of_month": as_of,
        "retirement_start_month": start,
        "retirement_months": months,
        "member_retirement_months": retirement,
        "member_planning_months": durations,
        "assets_at_retirement": assets_at_start,
        "capacity": capacity,
        "coverage": coverage,
        "period_pension": pension_sum,
        "pension_reinvestment": pension_reinvestment,
        "pre_retirement_gap": pre_gap,
        "basic_budget_today": basic_today,
        "rows": rows,
        "display": {
            "pension": display_pension,
            "basic_expense": basic_budget * factor,
            "routine_expense": routine_medical * factor,
            "asset_income": asset_income,
            "rent": rent,
            "principal_drawdown": capacity["risk_adjusted_liquid_assets"] / months,
        },
        "assumptions": [
            DISPLAY_NOTE,
            "退休前支出按2%增长，原首次退休节点作为资产和共同价格参照；父母赡养支出从本人退休起按0规划。",
            "未知保单现金价值不计入可用资产，累计保费不转为现金价值。",
            "新增受限养老账户缴款从自由资金扣除，并剔除养老金再投入的重复额。",
        ],
    }

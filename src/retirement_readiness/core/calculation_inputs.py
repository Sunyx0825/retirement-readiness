"""Project the current questionnaire shape into calculation-friendly records."""

from __future__ import annotations

import re
from typing import Any

from retirement_readiness.core.numeric_answers import model_value, numeric_answer

_ASSET_KEYS = (
    "cash_current_wan",
    "time_deposit_wan",
    "stock_wan",
    "fund_wan",
    "bond_wan",
    "insurance_total_premium_wan",
    "property_count",
    "property_value_wan",
    "vehicle_count",
    "vehicle_value_wan",
)
_INCOME_KEYS = (
    "wage_income",
    "business_income",
    "rent_income",
    "investment_income",
    "land_income",
    "other_income",
)
_DEBT_KEYS = ("mortgage_wan", "car_loan_wan", "other_debt_wan")
_EXPENSE_KEYS = (
    "food",
    "medical",
    "housing",
    "transport",
    "elder_support",
    "utilities",
    "child_education",
    "debt_repayment",
    "leisure_entertainment",
    "other",
)


def calculation_answers(raw: dict[str, Any]) -> dict[str, Any]:
    answers = dict(raw)
    answers["Q2"] = number(raw.get("Q2"))
    answers["Q5"] = {
        "count": number(raw.get("Q5__blank_1")),
        "child_ages": _number_list(raw.get("Q5__blank_2")),
    }
    answers["Q6"] = {
        "choice": raw.get("Q6"),
        "father_age": number(raw.get("Q6__father_alive_text__blank_1")),
        "mother_age": number(raw.get("Q6__mother_alive_text__blank_1")),
        "parent_ages": [
            number(raw.get("Q6__parents_alive_text__blank_1")),
            number(raw.get("Q6__parents_alive_text__blank_2")),
        ],
    }
    answers["Q13"] = {
        "knowledge": raw.get("Q13"),
        "amount_yuan": _selected_number(raw, "Q13", str(raw.get("Q13") or "")),
    }
    answers["Q14"] = {
        "estimation": raw.get("Q14"),
        "amount_yuan": _selected_number(raw, "Q14", str(raw.get("Q14") or "")),
    }
    answers["Q27"] = flat_record(raw, "Q27", _ASSET_KEYS)
    answers["Q28"] = {
        key: value * 10_000 for key, value in flat_record(raw, "Q28", _INCOME_KEYS).items()
    }
    answers["Q29_1"] = flat_record(raw, "Q29_1", _DEBT_KEYS)
    answers["Q30"] = flat_record(raw, "Q30", _EXPENSE_KEYS)
    answers["Q27"]["insurance_wan"] = 0
    answers["Q11_1"] = annuity_record(raw.get("Q11_1"))
    return answers


def annuity_record(value: Any) -> dict[str, Any]:
    record = dict(value) if isinstance(value, dict) else {}
    record["annual_amount_yuan"] = number(record.get("periodic_amount_yuan")) * (
        12 if record.get("period") == "monthly" else 1
    )
    return record


def spouse_answers(raw: dict[str, Any]) -> dict[str, Any]:
    history = raw.get("SP_PENSION_HISTORY") or []
    mapped = {key: raw[key] for key in ("Q7", "Q34") if key in raw}
    mapped.update(
        {
            "Q1": "female" if raw.get("Q1") == "male" else "male",
            "Q2": number(raw.get("SP_AGE")),
            "Q8": history[-1].get("employment_type", "fulltime") if history else "fulltime",
            "Q9": raw.get("P9"),
            "PENSION_HISTORY": history,
            "PENSION_SEGMENT_COUNT": raw.get("SP_PENSION_SEGMENT_COUNT"),
            "PENSION_BENEFIT_CITY": raw.get("SP_PENSION_BENEFIT_CITY"),
            "Q10": "no",
            "Q11": raw.get("P9_4"),
            "Q11_1": annuity_record(raw.get("P9_4_1")),
            "Q11_2": raw.get("P9_4_2"),
            "Q31": raw.get("P9_5"),
            "Q32": raw.get("P9_6"),
            "Q35": [],
            "Q36": "0",
        }
    )
    for key, value in raw.items():
        if key.startswith("SP_PENSION_BENEFIT_CITY__"):
            mapped[key.removeprefix("SP_")] = value
    return mapped


def flat_record(
    answers: dict[str, Any], question_id: str, keys: tuple[str, ...]
) -> dict[str, float]:
    return {
        key: number(answers.get(f"{question_id}__blank_{index}"))
        for index, key in enumerate(keys, start=1)
    }


def number(value: Any) -> float:
    return model_value(value)


def has_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, dict):
        return any(has_value(item) for item in value.values())
    if isinstance(value, list):
        return any(has_value(item) for item in value)
    return True


def profile_complete(raw: dict[str, Any], question_id: str, count: int) -> bool:
    if question_id in {"Q27", "Q28", "Q30"}:
        return True
    return all(
        raw.get(f"{question_id}__blank_{index}") is not None
        and str(raw.get(f"{question_id}__blank_{index}")).strip() != ""
        for index in range(1, count + 1)
    )


def _number_list(value: Any) -> list[float]:
    return [number(item) for item in re.split(r"[、,，\s]+", str(value or "")) if item]


def _selected_number(answers: dict[str, Any], question_id: str, code: str) -> float:
    prefix = f"{question_id}__{code}_text"
    return number(answers.get(prefix) or answers.get(f"{prefix}__blank_1"))


def estimate_financial_inputs(
    raw: dict[str, Any], answers: dict[str, Any], year: int, floor: float
) -> list[str]:
    """Apply only the documented unknown-value assumptions to the model view."""
    from retirement_readiness.core.parameters import resolve_calculation_regions
    from retirement_readiness.core.pension_reference import wage_reference

    people = [raw] + ([spouse_answers(raw)] if raw.get("Q4") == "married" else [])
    workers = [person for person in people if person.get("Q8") in {"fulltime", "flexible"}]
    historical_income = sum(
        next(
            (
                number(row.get("annual_income_wan")) * 10000
                for row in reversed(person.get("PENSION_HISTORY") or [])
                if number(row.get("annual_income_wan")) > 0
            ),
            0,
        )
        for person in workers
    )
    wage = wage_reference(
        resolve_calculation_regions(answers)["current"], "contribution_reference", year
    )["amount"]
    notes = []

    def fill(
        question: str,
        keys: tuple[str, ...],
        defaults: dict[str, float],
        multiplier: float = 1,
    ) -> None:
        for index, key in enumerate(keys, 1):
            value = raw.get(f"{question}__blank_{index}")
            status = numeric_answer(value)[0]
            answers[question][key] = (
                model_value(value, defaults.get(key, 0) / multiplier) * multiplier
            )
            if status != "known":
                notes.append(
                    f"{question}.{key}：{'空白按0' if status == 'missing' else '按原区间取模型值' if status == 'range' else '未知采用规划假设'}，本次取{answers[question][key]:g}；原回答保留。"
                )

    fill(
        "Q28",
        _INCOME_KEYS,
        {"wage_income": historical_income or wage * 12 * len(workers)},
        10000,
    )
    income = sum(answers["Q28"].values()) / 12
    fill(
        "Q30",
        _EXPENSE_KEYS,
        {
            "food": floor * 0.4,
            "medical": floor * 0.1,
            "housing": floor * 0.2,
            "transport": floor * 0.1,
            "utilities": floor * 0.1,
            "leisure_entertainment": floor * 0.1,
            "other": floor * 0.1,
            "debt_repayment": income * 0.1 if raw.get("Q29") == "yes" else 0,
        },
    )
    fill(
        "Q29_1",
        _DEBT_KEYS,
        {
            "other_debt_wan": answers["Q30"]["debt_repayment"] * 12 / 10000
            if raw.get("Q29") == "yes"
            else 0
        },
    )
    fill("Q27", _ASSET_KEYS, {})
    return notes

"""Normalize supported financial records to the questionnaire's field IDs."""

from copy import deepcopy

RECORD_FIELDS = {
    "Q27": (
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
    ),
    "Q28": (
        "wage_income",
        "business_income",
        "rent_income",
        "investment_income",
        "land_income",
        "other_income",
    ),
    "Q29_1": ("mortgage_wan", "car_loan_wan", "other_debt_wan"),
    "Q30": (
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
    ),
}


def normalize_answers(raw):
    """Accept named financial records or inline fields without losing amounts.

    Assets, debt, and annual income use ten-thousand yuan; monthly expenses use
    yuan. Conflicting duplicate representations are rejected, never overwritten.
    """
    if not isinstance(raw, dict):
        raise ValueError("Questionnaire answers must be a JSON object.")
    answers = deepcopy(raw)
    for question, fields in RECORD_FIELDS.items():
        record = answers.get(question)
        if not isinstance(record, dict):
            continue
        unknown = set(record) - set(fields)
        if unknown:
            raise ValueError(f"Unknown fields in {question}: {sorted(unknown)}")
        for number, field in enumerate(fields, 1):
            if field not in record:
                continue
            target = f"{question}__blank_{number}"
            if target in answers and str(answers[target]) != str(record[field]):
                raise ValueError(f"Conflicting representations of {target}.")
            answers[target] = record[field]
        del answers[question]
    return answers

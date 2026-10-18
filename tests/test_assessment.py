import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from retirement_readiness.core.calculation_inputs import calculation_answers
from retirement_readiness.core.calculator import ASSET_LIQUIDITY_FACTORS, calculate_asset_capacity
from retirement_readiness.core.household_cashflow import _move
from retirement_readiness.core.report_stages import _validated_id_list
from retirement_readiness.inputs import normalize_answers
from retirement_readiness.pipeline import assess
from retirement_readiness.runtime import ModelNotConfigured, model_session, openai_client
from retirement_readiness.tools import ask_followup, project_cashflow, validate_questionnaire

ROOT = Path(__file__).parents[1]


def household(name="beijing"):
    return json.loads((ROOT / f"examples/{name}.json").read_text(encoding="utf-8"))["answers"]


def test_named_records_preserve_financial_amounts():
    normalized = normalize_answers(household())
    model = calculation_answers(normalized)
    assert model["Q28"]["wage_income"] == 540000
    assert model["Q27"]["cash_current_wan"] == 15
    assert model["Q27"]["stock_wan"] == 10
    assert model["Q30"]["food"] == 4000


def test_conflicting_input_forms_are_rejected():
    with pytest.raises(ValueError, match="Conflicting"):
        normalize_answers({"Q28": {"wage_income": 54}, "Q28__blank_1": 10})


def test_negative_age_and_contradictory_choices_are_rejected():
    answers = household()
    answers["Q2"] = -10
    answers["Q35"] = ["none", "hypertension"]
    result = validate_questionnaire(answers)
    assert not result["complete"]
    assert any("Q2" in item for item in result["invalid_fields"])
    assert any("exclusive_option" in item for item in result["invalid_fields"])
    with pytest.raises(ValueError, match="correct"):
        project_cashflow(answers, as_of=date(2026, 9, 1))


def test_asset_drawdown_stops_at_zero_and_records_unfunded_gap():
    balances = dict.fromkeys(ASSET_LIQUIDITY_FACTORS, 0.0)
    balances.update(cash_current=100, time_deposit=50)
    gap = _move(balances, -180)
    assert gap == 30
    assert sum(balances.values()) == 0
    assert all(value >= 0 for value in balances.values())


def test_liquidity_haircut_does_not_count_principal_twice():
    capacity = calculate_asset_capacity({"cash_current": 1000, "stock": 1000}, 10)
    assert capacity["gross_liquid_assets"] == 2000
    assert capacity["risk_adjusted_liquid_assets"] == 1650
    assert capacity["annual_principal_drawdown"] == 165


@pytest.mark.parametrize("name", ["beijing", "kunming", "tangshan"])
def test_each_synthetic_case_reaches_a_consistent_report(name):
    answers = household(name)
    assert validate_questionnaire(answers)["complete"]
    original = json.dumps(answers, sort_keys=True)
    calculations, report = asyncio.run(assess(answers, as_of=date(2026, 9, 1)))
    indices = calculations["financial_indices"]
    for scenario, denominator in indices["index_denominators"].items():
        assert denominator > 0
        assert indices[scenario] == pytest.approx(
            indices["scenario_resources"][scenario] / denominator
        )
    assert report["headline_metrics"]["scores"] == calculations["scores"]
    assert report["mode"] == "offline_rules"
    assert "基本生活" in report["markdown"]
    assert json.dumps(answers, sort_keys=True) == original


def test_model_failure_preserves_the_same_calculation():
    class FailedModel:
        calls = []

        async def chat_completion(self, **kwargs):
            raise RuntimeError("simulated unavailable model")

    answers = household()
    expected, _ = asyncio.run(assess(answers, as_of=date(2026, 9, 1)))
    actual, report = asyncio.run(assess(answers, as_of=date(2026, 9, 1), model=FailedModel()))
    assert actual == expected
    assert report["mode"] == "offline_fallback"


def test_model_context_does_not_leak_between_requests():
    class TestModel:
        async def chat_completion(self, **kwargs):
            return "test response"

    with model_session(TestModel()):
        assert asyncio.run(openai_client.chat_completion()) == "test response"
    with pytest.raises(ModelNotConfigured):
        asyncio.run(openai_client.chat_completion())


def test_unknown_evidence_identifiers_are_rejected():
    assert _validated_id_list(["invented"], {"known"}, minimum=0, maximum=2) is None


def test_followup_works_without_network():
    bundle = asyncio.run(ask_followup(household(), module_id="care"))
    assert isinstance(bundle, dict)
    assert len(bundle.get("questions", [])) <= 2

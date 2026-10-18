"""The three public tool boundaries used by interview and report workflows."""

from datetime import date
from typing import Any

from .core.calculations import run_calculations
from .core.followup import generate_followup_bundle
from .core.questionnaire import sanitize_answers, validate_answers
from .inputs import normalize_answers


def validate_questionnaire(answers: dict[str, Any]) -> dict[str, Any]:
    """Identify missing or inconsistent fields without silently repairing facts."""
    if not isinstance(answers, dict):
        raise ValueError("Questionnaire answers must be a JSON object.")
    return validate_answers(normalize_answers(answers))


def project_cashflow(answers: dict[str, Any], *, as_of: date, followups=None):
    """Calculate pensions, asset paths, scenario coverage, and readiness scores."""
    answers = normalize_answers(answers)
    validation = validate_questionnaire(answers)
    if not validation["complete"]:
        fields = validation["missing_fields"] + validation["invalid_fields"]
        raise ValueError(f"Complete or correct these questionnaire fields first: {fields}")
    return run_calculations(
        sanitize_answers(answers),
        followups=followups or [],
        questionnaire_year=as_of.year,
        questionnaire_month=as_of.month,
    )


async def ask_followup(answers, *, module_id, followups=None, events=None):
    """Ask bounded, context-dependent questions; fall back to domain rules."""
    return await generate_followup_bundle(
        module_id=module_id,
        answers=normalize_answers(answers),
        followups=followups or [],
        events=events or [],
    )

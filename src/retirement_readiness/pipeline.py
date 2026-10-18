"""Coordinate validation, calculation, and evidence-grounded report roles."""

from copy import deepcopy
from datetime import date

from .core.constants import CALCULATION_ID, SURVEY_ID
from .core.report_generator import (
    _baseline_report,
    _report_visuals,
    _v2_report,
    generate_retirement_report,
    report_markdown,
)
from .core.rule_catalog import active_rule_catalog
from .core.rule_engine import select_report_rules
from .core.rule_facts import build_rule_facts
from .inputs import normalize_answers
from .runtime import model_session
from .tools import project_cashflow


def deterministic_report(answers, followups, calculations):
    """Render verified metrics and applicable rules without a network call."""
    catalog = active_rule_catalog()
    facts = build_rule_facts(answers, followups, calculations, catalog)
    selected = select_report_rules(catalog, facts)
    report = _baseline_report(calculations, selected, catalog.rule_pack_id, catalog.relationships)
    report["mode"] = "offline_rules"
    report["model_enhancement"]["stages"] = {
        stage: "not_requested" for stage in report["model_enhancement"]["stages"]
    }
    report["visuals"] = _report_visuals(calculations, report["sections"])
    report["v2_report"] = _v2_report(report, calculations)
    report["markdown"] = report_markdown(report)
    return report


async def assess(answers, *, as_of: date, followups=None, model=None):
    """Preserve a deterministic result even if model narration fails.

    Live report roles select recommendations, explain the overall assessment,
    personalize sections, caption scores, and identify further evidence-based
    observations. Amounts and scores always come from the calculation tool.
    """
    followups = followups or []
    answers = normalize_answers(answers)
    calculations = project_cashflow(answers, as_of=as_of, followups=followups)
    calculations.update(survey_id=SURVEY_ID, calculation_id=CALCULATION_ID)
    baseline = deterministic_report(answers, followups, calculations)
    if model is None:
        return calculations, baseline
    try:
        with model_session(model):
            report = await generate_retirement_report(
                answers=deepcopy(answers),
                followups=deepcopy(followups),
                calculations=deepcopy(calculations),
            )
        report["mode"] = "live_multi_agent"
        report["model_calls"] = list(model.calls)
        return calculations, report
    except (RuntimeError, ValueError, TypeError, KeyError) as error:
        baseline["mode"] = "offline_fallback"
        baseline["model_failure"] = type(error).__name__
        return calculations, baseline

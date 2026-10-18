"""Exercise the real orchestration with a scripted model, without network calls.

The fixture supplies contract-shaped text; it tests plumbing and references,
not the quality of a language model's advice.
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from retirement_readiness.pipeline import assess


class ContractModel:
    def __init__(self, baseline):
        self.calls = []
        self.selections = {
            section["code"]: [{"ruleId": item["rule_id"]} for item in section["recommendations"]]
            for section in baseline["sections"]
        }

    async def chat_completion(self, *, stage, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        self.calls.append({"stage": stage})
        references = {"contextIds": [], "calculationFactIds": [], "assumptionIds": []}
        if stage == "rule_selection":
            decisions = payload["requiredObjectDecisions"]
            for decision in decisions:
                decision["recommendationSelections"] = self.selections[decision["objectCode"]]
            result = {"objectDecisions": decisions, "v2_report": {"conclusion": ["合成案例测试。"]}}
        elif stage == "overall_assessment":
            result = {
                **references,
                "overallResult": "这是用于验证报告编排的合成文本，不作为用户建议。" * 5,
            }
        elif stage == "section_overviews":
            facts = payload["reportAssessmentContext"]["factCatalog"]["questionnaire"]
            context_id = next(x["contextId"] for x in facts if x.get("referenceable") is not False)
            result = {
                "items": [
                    {
                        **references,
                        "objectCode": item["objectCode"],
                        "overview": "本段仅用于测试报告格式及引用校验，所有金额由独立计算模块给出。"
                        * 2,
                        "contextIds": [context_id],
                        "calculationFactIds": [item["resultFactId"]]
                        if item["kind"] == "financial_coverage"
                        else [],
                    }
                    for item in payload["reportObjects"]
                ]
            }
        elif stage == "score_captions":
            result = {
                "items": [
                    {
                        **references,
                        "objectCode": item["objectCode"],
                        "caption": "此评分说明仅用于验证报告结构和计算隔离。",
                    }
                    for item in payload["scoreObjects"]
                ]
            }
        elif stage in ("problem_explanations", "recommendation_explanations"):
            text = "此说明仅用于验证各报告角色的数据流与输出格式，不能作为实际家庭财务建议。"
            result = {
                "items": [
                    {
                        **references,
                        "ruleId": rule_id,
                        "situationAnalysis": text,
                        "retirementImpact": text,
                        "fitAnalysis": text,
                        "personalizedNextStep": text,
                    }
                    for rule_id in payload["platformObjectTask"]["expectedRuleIdsInOrder"]
                ]
            }
        elif stage == "open_insights":
            result = {"insights": []}
        else:
            raise AssertionError(f"Unexpected report stage: {stage}")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))]
        )


def test_all_report_roles_run_and_preserve_calculated_amounts():
    case_path = Path(__file__).parents[1] / "examples/beijing.json"
    answers = json.loads(case_path.read_text(encoding="utf-8"))["answers"]
    original, baseline = asyncio.run(assess(answers, as_of=date(2026, 9, 1)))
    model = ContractModel(baseline)
    calculations, report = asyncio.run(assess(answers, as_of=date(2026, 9, 1), model=model))
    assert report["mode"] == "live_multi_agent"
    assert calculations == original
    assert report["headline_metrics"] == baseline["headline_metrics"]
    assert {item["stage"] for item in model.calls} == {
        "rule_selection",
        "overall_assessment",
        "section_overviews",
        "problem_explanations",
        "recommendation_explanations",
        "score_captions",
        "open_insights",
    }
    assert model.calls[0]["stage"] == "rule_selection"

"""Published business prompts; adapters and validation live outside these texts."""

import json
from pathlib import Path

_PROMPTS = json.loads(
    (Path(__file__).with_name("data") / "report-prompts.json").read_text(encoding="utf-8")
)
REPORT_CALL_A_PROMPT = _PROMPTS["REPORT_CALL_A_PROMPT"]
REPORT_OVERALL_ASSESSMENT_PROMPT = _PROMPTS["REPORT_OVERALL_ASSESSMENT_PROMPT"]
REPORT_SECTION2_OVERVIEW_PROMPT = _PROMPTS["REPORT_SECTION2_OVERVIEW_PROMPT"]
REPORT_SECTION2_FINANCIAL_OVERVIEW_PROMPT = _PROMPTS["REPORT_SECTION2_FINANCIAL_OVERVIEW_PROMPT"]
REPORT_SCORE_CAPTION_PROMPT = _PROMPTS["REPORT_SCORE_CAPTION_PROMPT"]
REPORT_OPEN_INSIGHT_PROMPT = _PROMPTS["REPORT_OPEN_INSIGHT_PROMPT"]


def section2_prompt(object_code: str, kind: str) -> str:
    shared = _PROMPTS[f"REPORT_SECTION2_{kind.upper()}_SHARED_PROMPT"]
    return f"{shared}\n# 本对象补充要求\n{_PROMPTS['SECTION2_OBJECT_PROMPTS'][object_code][kind]}"

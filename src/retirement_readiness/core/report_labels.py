"""Canonical user-facing labels shared by retirement report renderers."""

REPORT_OBJECT_TITLES = {
    "basic_living": "基本生活充足指数",
    "routine_medical": "慢病风险充足指数",
    "major_medical": "重疾风险充足指数",
    "long_term_care": "失能风险充足指数",
    "financial_cognition": "财务准备认知",
    "health_capital": "健康资本积累",
    "activity_social": "活动社交规划",
    "care_planning": "失能照护规划",
}

REPORT_RESULT_FACT_IDS = {
    "basic_living": "CALC-INDEX-BASIC-LIVING",
    "routine_medical": "CALC-INDEX-ROUTINE-MEDICAL",
    "major_medical": "CALC-INDEX-MAJOR-MEDICAL",
    "long_term_care": "CALC-INDEX-LONG-TERM-CARE",
    "financial_cognition": "CALC-SCORE-FINANCIAL-COGNITION",
    "health_capital": "CALC-SCORE-HEALTH-CAPITAL",
    "activity_social": "CALC-SCORE-ACTIVITY-SOCIAL",
    "care_planning": "CALC-SCORE-CARE-PLANNING",
}

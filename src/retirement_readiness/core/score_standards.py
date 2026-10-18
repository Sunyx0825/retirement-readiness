"""Retirement readiness scoring tables."""

FINANCIAL_LITERACY_SCORE_MATRIX = {
    "Q22": {
        "more_than_1020": 100,
        "exactly_1020": 35,
        "less_than_1020": 10,
        "not_sure": 20,
    },
    "Q23": {"buy_less": 100, "buy_same": 20, "buy_more": 10, "not_sure": 20},
    "Q24": {"false": 100, "true": 15, "not_sure": 20},
    "Q25": {"true": 100, "false": 15, "not_sure": 20, "refuse": 20},
    "Q26": {"up": 100, "down": 20, "irrelevant": 15, "not_sure": 20},
}
PENSION_EXPECTATION_SCORES = {
    "unknown": 20,
    "unclear": 35,
    "general": 58,
    "clear": 82,
    "very_clear": 100,
}
RETIREMENT_EXPENSE_SCORES = {
    "not_estimated": 20,
    "rarely_considered": 35,
    "occasionally": 55,
    "rough": 80,
    "detailed": 100,
}
RETIREMENT_RESERVE_SCORES = {
    "not_considered": 25,
    "insufficient": 20,
    "somewhat_insufficient": 40,
    "roughly_balanced": 60,
    "rather_sufficient": 82,
    "fully_sufficient": 100,
}
RETIREMENT_RESPONSIBILITY_SCORES = {
    "no_personal": 20,
    "very_little_personal": 35,
    "equal_shared": 68,
    "mostly_personal": 86,
    "all_personal": 75,
}
PLAN_IMPORTANCE_SCORES = {
    "not_important": 15,
    "less_important": 35,
    "neutral": 55,
    "important": 82,
    "very_important": 100,
}
PLAN_COMPREHENSION_SCORES = {
    "very_difficult": 20,
    "difficult": 35,
    "neutral": 55,
    "easy": 82,
    "very_easy": 100,
}
PLAN_COMPLETENESS_SCORES = {
    "none": 20,
    "not_complete": 40,
    "average": 60,
    "complete": 82,
    "very_complete": 100,
}
PLAN_CONFIDENCE_SCORES = {
    "no_confidence": 20,
    "low_confidence": 35,
    "neutral": 55,
    "confident": 82,
    "very_confident": 100,
}
RETIREMENT_SAVING_SCORES = {
    "definitely_no": 20,
    "mostly_no": 35,
    "uncertain": 50,
    "probably_yes": 78,
    "definitely_yes": 100,
}
SMOKING_SCORES = {
    "never": 100,
    "quit": 85,
    "monthly_lt_10": 65,
    "daily_lt_10": 40,
    "daily_gte_10": 20,
}
ALCOHOL_SCORES = {
    "never": 100,
    "monthly_lt_1": 85,
    "monthly_1_3": 60,
    "weekly_gte_4": 25,
}
EXERCISE_SCORES = {"no": 25, "yes_lt_150": 65, "yes_gte_150": 100}
DIET_SCORES = {"0": 20, "1_2": 40, "3_4": 60, "5_6": 82, "7": 100}
CHECKUP_SCORES = {
    "never": 20,
    "rare_no_regular": 38,
    "every_2_3y": 60,
    "yearly": 88,
    "half_yearly": 100,
}
MONITORING_SCORES = {
    "weekly_recorded": 100,
    "monthly_or_remember": 75,
    "quarter_or_half_year": 55,
    "only_uncomfortable": 35,
    "never": 20,
}
ACTIVITY_PLAN_SCORES = {
    "not_considered": 20,
    "vague": 45,
    "one_activity": 75,
    "two_or_more": 100,
}
NONKIN_SUPPORT_SCORES = {"0": 20, "1_2": 60, "gte_3": 100}
CARE_MODE_SCORES = {
    "not_considered": 20,
    "family_care": 62,
    "home_hired_care": 72,
    "community_care": 78,
    "institution_care": 75,
}
CARE_CONSENSUS_SCORES = {
    "not_discussed": 20,
    "mentioned_no_consensus": 42,
    "consensus_basic": 72,
    "responsibility_clear": 90,
    "written_or_legal": 100,
}
